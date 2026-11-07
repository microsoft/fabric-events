import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "cicd" / "fraud-response-before-fulfillment"


def load_modules():
    names = ("deploy", "native", "foundation", "connections", "fabric_api", "flow_api", "azure_backend", "test_run")
    previous = {name: sys.modules.pop(name, None) for name in names}
    sys.path.insert(0, str(PACKAGE))
    try:
        import deploy
        import native
        import test_run
        return deploy, native, test_run
    finally:
        sys.path.pop(0)
        for name in names:
            sys.modules.pop(name, None)
            if previous[name] is not None:
                sys.modules[name] = previous[name]


deploy, native, test_run = load_modules()


class RiskConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((PACKAGE / "config.card.example.json").read_text())

    def test_reviewed_names_and_new_target_bind_every_native_artifact(self):
        self.config.update(
            resource_prefix="ReviewedRisk",
            workspace_id="10000000-0000-4000-8000-000000000001",
            event_name="Demo.Order.RiskDecision",
            teams_delivery="individual", teams_recipients=[],
        )
        self.config["resource_names"] = {
            role: "Reviewed" + name for role, name in self.config["resource_names"].items()
        }
        ids = {role: str(__import__("uuid").uuid4()) for role in self.config["resource_names"]}
        for role, item in native.items(self.config).items():
            definition = native.definition(role, self.config, ids)
            platform = json.loads(native.decoded(definition, ".platform"))
            self.assertEqual(platform["metadata"]["displayName"], self.config["resource_names"][role])
            self.assertNotIn("${", json.dumps(definition))
            if role == "publisher":
                source = next(part for part in definition["parts"] if part["path"].endswith(".py"))
                code = native.decoded(definition, source["path"])
                self.assertIn(self.config["resource_names"]["schema"], code)
                self.assertIn(self.config["event_name"], code)
        entities = json.loads(native.decoded(native.definition("activator", self.config, ids), "ReflexEntities.json"))
        source = next(node for node in entities if node["type"] == "realTimeHubSource-v1")
        self.assertIn(ids["schema"], source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0])

    def test_initial_import_cannot_activate_rules_or_create_unbound_actions(self):
        self.config.update(teams_delivery="individual", teams_recipients=[])
        payload = native.bulk_payload(["activator"], self.config)
        part = next(part for part in payload["definitionParts"] if part["path"].endswith("/ReflexEntities.json"))
        part = {**part, "path": "ReflexEntities.json"}
        entities = json.loads(native.decoded({"parts": [part]}, "ReflexEntities.json"))
        self.assertEqual(len(entities), 3)
        self.assertFalse(any(node["type"] == "powerAutomateFlowAction-v1" for node in entities))
        self.assertFalse(any(node["payload"].get("definition", {}).get("type") == "Rule" for node in entities))

    def test_unknown_roles_duplicate_names_and_invalid_azure_names_are_rejected(self):
        for mutate in (
            lambda value: value["resource_names"].update(extra="ExtraItem"),
            lambda value: value["flow_names"].update(release=value["flow_names"]["hold"]),
            lambda value: value["azure"].update(storage_account="Not-A-Storage-Account"),
            lambda value: value["resource_names"].update(schema="con"),
            lambda value: value["azure"].update(function_app=""),
        ):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                value = copy.deepcopy(self.config)
                mutate(value)
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    deploy.load_config(path)

    def test_each_flow_template_receives_its_own_native_connection_and_secure_action(self):
        self.config["data_activator_connection_name"] = "shared-dataactivator-authenticated"
        backend = SimpleNamespace(base_url="https://synthetic.azurewebsites.net/api/test", key='memory-only-"key')
        for decision in deploy.DECISIONS:
            body = deploy.flow_definition(decision, self.config, decision + "-native-connection", backend)
            definition = body["properties"]["definition"]
            trigger = definition["triggers"]["When_an_activator_rule_is_triggered"]
            self.assertEqual(trigger["inputs"]["parameters"]["connectionInfo"], decision + "-native-connection")
            action = next(iter(definition["actions"].values()))
            self.assertEqual(action["inputs"]["headers"]["x-functions-key"], backend.key)
            self.assertEqual(action["runtimeConfiguration"]["secureData"]["properties"], ["inputs", "outputs"])


class RiskFulfillmentResumeTests(unittest.TestCase):
    def test_resume_after_release_fulfillment_never_reposts_the_action(self):
        order_id = "cicd-10000000-0000-4000-8000-000000000001-minimum-release"
        case = {
            "order": {"order_id": order_id, "customer_id": "test-customer", "risk_score": 0},
            "decision": "release", "parameters": {"decidedAt": "2026-01-01T00:00:00Z"},
        }
        order = {
            **case["order"], "status": "released", "decision": "release",
            "action_count": 1, "activation_id": "native-event-id",
        }
        row = {**case["order"], "decision": "release", "Deliveries": 1, "decided_at": case["parameters"]["decidedAt"]}
        backend = Mock()

        def request(method, path, body=None, expected=200):
            if method == "POST":
                order["status"] = "fulfilled"
            return copy.deepcopy(order)

        backend.request.side_effect = request
        deployment = SimpleNamespace(
            api=Mock(), backend=backend, flows=Mock(), config={},
            spec={"actions": [{"decision": "release", "parameters": ["order_id"]}]},
        )
        run = test_run.TestRun(deployment)
        run.save = Mock()
        observed = [{
            "decision": "release", "run_id": "actual-native-run",
            "trigger": {
                "customProperties": {"order_id": order_id}, "inputFields": {"order_id": order_id},
                "eventId": "native-event-id", "ruleId": "native-release-rule",
            },
        }]
        for _ in range(2):
            run.verify_case(case, {order_id: copy.deepcopy(order)}, {order_id: row}, observed, {"release": "native-release-rule"})
        posts = [call for call in backend.request.call_args_list if call.args[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(case["persisted_after_fulfillment"]["status"], "fulfilled")
        self.assertEqual(case["persisted_after_native_action"]["status"], "released")

    def test_a_fulfilled_order_without_this_test_intent_is_not_accepted(self):
        case = {
            "order": {"order_id": "synthetic-order", "customer_id": "test-customer", "risk_score": 0},
            "decision": "release", "parameters": {"decidedAt": "2026-01-01T00:00:00Z"},
        }
        deployment = SimpleNamespace(
            api=Mock(), backend=Mock(), flows=Mock(), config={},
            spec={"actions": [{"decision": "release", "parameters": ["order_id"]}]},
        )
        run = test_run.TestRun(deployment)
        observed = [{
            "decision": "release", "trigger": {
                "customProperties": {"order_id": "synthetic-order"}, "inputFields": {"order_id": "synthetic-order"},
                "eventId": "native-event-id", "ruleId": "native-release-rule",
            },
        }]
        order = {
            "status": "fulfilled", "decision": "release", "action_count": 1, "activation_id": "native-event-id",
        }
        row = {
            "decision": "release", "Deliveries": 1, "customer_id": "test-customer", "risk_score": 0,
            "decided_at": case["parameters"]["decidedAt"],
        }
        with self.assertRaises(RuntimeError):
            run.verify_case(case, {"synthetic-order": order}, {"synthetic-order": row}, observed, {"release": "native-release-rule"})
        deployment.backend.request.assert_not_called()


class RiskBackendAndBundleTests(unittest.TestCase):
    def test_ambiguous_azure_creation_is_not_replayed(self):
        config = json.loads((PACKAGE / "config.card.example.json").read_text())["azure"]
        state = {}
        backend = deploy.Backend(config, state, Mock(), Path("."))
        backend.resources = Mock(return_value=[])
        backend.command = Mock(side_effect=RuntimeError("Synthetic lost creation response"))
        with self.assertRaises(RuntimeError):
            backend.provision()
        self.assertEqual(state["azure_backend"]["pending_step"], "storage-account")
        with self.assertRaises(ValueError):
            backend.provision()
        backend.command.assert_called_once()

    def test_unmanaged_azure_resource_is_never_adopted(self):
        config = json.loads((PACKAGE / "config.card.example.json").read_text())["azure"]
        backend = deploy.Backend(config, {}, Mock(), Path("."))
        backend.resources = Mock(return_value=[{
            "name": config["storage_account"], "tags": {"fabric-events-attempt": "unrelated"},
        }])
        backend.command = Mock()
        with self.assertRaises(ValueError):
            backend.provision()
        backend.command.assert_not_called()

    def test_distribution_excludes_private_state_documentation_and_caches(self):
        import zipfile
        spec = importlib.util.spec_from_file_location("risk_bundle", PACKAGE / "bundle.py")
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        with tempfile.TemporaryDirectory() as directory:
            output = bundle.bundle(Path(directory) / "risk.zip")
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertTrue(any(name.endswith("/deploy.bat") for name in names))
                self.assertTrue(any(name.endswith("/test-order-api/function_app.py") for name in names))
                self.assertFalse(any(
                    ".deployments" in name or "__pycache__" in name
                    or name.endswith((".md", ".html", ".pyc", ".har", ".local.json"))
                    for name in names
                ))


if __name__ == "__main__":
    unittest.main()
