import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from test_public_cicd import FakePublicApi, ROOT, TENANT, WORKSPACE
import fraud_deploy
import public_api
import public_deploy
from public_consumers import definition_part, part


def publisher_function(source):
    functions = ModuleType("fabric.functions")
    functions.UserDataFunctions = lambda: SimpleNamespace(
        connection=lambda **kwargs: lambda function: function,
        function=lambda: lambda function: function,
    )
    functions.FabricBusinessEventsClient = object
    fabric = ModuleType("fabric")
    fabric.functions = functions
    namespace = {}
    with patch.dict(sys.modules, {"fabric": fabric, "fabric.functions": functions}):
        exec(compile(source, "function_app.py", "exec"), namespace)
    return namespace[fraud_deploy.FUNCTION]


class FraudApi(FakePublicApi):
    def __init__(self):
        super().__init__()
        self.tokens = SimpleNamespace(token=lambda resource: "unit-test-token")
        self.inputs, self.events = [], []
        self.fail_after_input = None
        self.output_override = None
        self.string_output = False
        self.omit_metadata = False

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/invoke"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.inputs.append(copy.deepcopy(body))
            source = definition_part(self.definitions[path.split("/")[-4]], "function_app.py")
            client = SimpleNamespace(PublishEvent=lambda **event: self.events.append(copy.deepcopy(event)))
            output = publisher_function(source)(client, **body)
            if self.fail_after_input == len(self.inputs):
                raise RuntimeError("Ambiguous order evaluation transport.")
            if self.output_override is not None:
                output = copy.deepcopy(self.output_override)
            return public_api.Reply(200, {}, {
                "functionName": fraud_deploy.FUNCTION, "status": "Succeeded",
                "output": json.dumps(output) if self.string_output else output,
                "errors": [], "invocationId": str(uuid.uuid4()),
            })
        reply = super()._request(method, url, resource, body, headers)
        if self.omit_metadata and path.endswith("/getDefinition") and "/userDataFunctions/" in path:
            reply.body["definition"]["parts"] = [row for row in reply.body["definition"]["parts"]
                                                if row["path"] != "resources/functions.json"]
        return reply


class FraudPublicTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = ROOT / "cicd" / "fraud-response-before-fulfillment"
        self.config = public_deploy.configuration(self.folder, self.folder / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
        self.api = FraudApi()
        self.target = fraud_deploy.FraudDeployment(
            self.folder, self.config, Path(directory.name) / "ledger.json", self.api,
        )

    def test_deploy_prepares_native_source_and_stable_order_plan_without_rules_or_execution(self):
        result = self.target.deploy()
        self.assertEqual(result["status"], "manual_setup_required")
        self.assertEqual(set(result["items"]), {"schema", "publisher", "activator"})
        self.assertEqual(self.api.inputs, [])
        self.assertEqual(len(result["source_orders"]), 6)
        again = self.target.deploy(resume=True)
        self.assertEqual(again["source_orders"], result["source_orders"])
        self.assertEqual(len(self.api.items), 3)
        self.assertFalse(result["e2e_verified"])
        self.assertFalse(result["manual_consumer_actions_verified"])
        self.assertFalse(result["oms_enforcement_verified"])
        entities = self.target.activator_entities()
        self.assertEqual(len(entities), 3)
        self.assertFalse(any(row["payload"].get("definition", {}).get("type") == "Rule" for row in entities))
        source = next(row for row in entities if row["type"] == "realTimeHubSource-v1")
        self.assertIn(result["items"]["schema"], source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0])
        definition = self.target.udf_definition()
        connection = json.loads(definition_part(definition, "definition.json"))["connectedDataSources"][0]
        self.assertEqual(connection["artifactId"], result["items"]["schema"])
        self.assertEqual(connection["artifactType"], "EventDefinition")
        metadata = json.loads(definition_part(definition, "resources/functions.json"))
        parameters = metadata["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"]
        self.assertEqual(parameters, [{"name": name, "dataType": kind}
                                     for name, (_, kind) in fraud_deploy.PARAMETERS.items()])

    def test_six_native_evaluations_preserve_prepared_orders_five_fields_and_original_boundaries(self):
        prepared = self.target.deploy()["source_orders"]
        self.api.string_output = True
        result = self.target.test()
        self.assertTrue(result["source_execution_verified"])
        self.assertEqual(result["native_udf_evaluations"], 6)
        self.assertEqual(result["publication_requests"], 6)
        self.assertIsNone(result["events_published"])
        self.assertFalse(result["e2e_verified"])
        self.assertFalse(result["oms_enforcement_verified"])
        self.assertEqual([row["order_id"] for row in prepared], [row["orderId"] for row in self.api.inputs])
        events = [row["event_data"] for row in self.api.events]
        self.assertEqual(events, result["expected_decision_events"])
        self.assertEqual([row["decision"] for row in events],
                         ["release", "release", "manual_review", "manual_review", "hold", "hold"])
        for event in self.api.events:
            self.assertEqual(event["type"], self.config["event_name"])
            self.assertEqual(event["data_version"], "v1")
            self.assertEqual(set(event["event_data"]),
                             {"order_id", "customer_id", "risk_score", "decision", "decided_at"})
        for call in self.api.calls:
            self.assertNotEqual(call[-1], public_api.GRAPH)
            self.assertNotIn("/updateDefinition", call[1])
            if call[1].endswith("/invoke"):
                self.assertEqual(call[-1], public_api.POWERBI)
                self.assertEqual(set(call[2]), set(fraud_deploy.PARAMETERS))
        self.assertFalse(self.target.verify()["e2e_verified"])
        self.assertEqual(len(self.api.inputs), 6)

    def test_added_manual_actions_are_not_overwritten_verified_or_exported_with_credentials(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["activator"]]
        entities = json.loads(definition_part(definition, "ReflexEntities.json"))
        entities.append({"uniqueIdentifier": str(uuid.uuid4()), "type": "manual-custom-action",
                         "payload": {"connectionInfo": "DO-NOT-EXPORT-THIS-CONNECTION"}})
        definition["parts"] = [part("ReflexEntities.json", entities)]
        result = self.target.verify()
        self.assertEqual(result["manual_consumer_entities_observed"], 1)
        self.assertFalse(result["manual_consumer_actions_verified"])
        self.assertNotIn("DO-NOT-EXPORT", self.target.path.read_text())
        self.assertEqual(len(json.loads(definition_part(definition, "ReflexEntities.json"))), 4)

    def test_changed_owned_source_or_udf_connection_prevents_any_order_evaluation(self):
        self.target.deploy()
        identifier = self.target.state["items"]["activator"]
        original = copy.deepcopy(self.api.definitions[identifier])
        entities = json.loads(definition_part(original, "ReflexEntities.json"))
        entities[1]["payload"]["connection"]["eventTypeFullyQualifiedIds"] = ["different-source"]
        self.api.definitions[identifier] = {"parts": [part("ReflexEntities.json", entities)]}
        with self.assertRaisesRegex(ValueError, "source or binding differs"):
            self.target.test()
        self.api.definitions[identifier] = original
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        content = json.loads(definition_part(definition, "definition.json"))
        content["connectedDataSources"][0]["artifactId"] = str(uuid.uuid4())
        definition["parts"][0] = part("definition.json", content)
        with self.assertRaisesRegex(ValueError, "UDF connection"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("test_run", self.target.state)

    def test_ambiguous_evaluation_is_persisted_and_never_replayed(self):
        self.target.deploy()
        self.api.fail_after_input = 3
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.target.test()
        self.assertEqual(len(self.api.inputs), 3)
        self.assertIn("pending_input", public_deploy.read(self.target.path)["test_run"])
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "incomplete or ambiguous"):
            self.target.verify()
        self.assertEqual(len(self.api.inputs), 3)

    def test_success_shaped_wrong_decision_is_retained_as_unresolved(self):
        self.target.deploy()
        self.api.output_override = {"decision": "hold", "event_published": True}
        with self.assertRaisesRegex(RuntimeError, "original decision policy"):
            self.target.test()
        self.assertIn("pending_input", self.target.state["test_run"])
        self.assertEqual(self.target.state["test_run"]["invocations"], [])

    def test_missing_service_metadata_is_reported_without_claiming_parameter_readback(self):
        self.api.omit_metadata = True
        result = self.target.deploy()
        self.assertEqual(result["udf_metadata_readback"], "not_returned_by_public_definition")
        self.assertFalse(result["e2e_verified"])

    def test_duplicate_invocation_identity_leaves_the_second_order_unresolved(self):
        self.target.deploy()
        original, identity = self.api._request, str(uuid.uuid4())

        def request(*arguments):
            response = original(*arguments)
            if arguments[1].endswith("/invoke"):
                response.body["invocationId"] = identity
            return response

        with patch.object(self.api, "_request", side_effect=request):
            with self.assertRaisesRegex(RuntimeError, "repeated invocation identity"):
                self.target.test()
        self.assertIn("pending_input", self.target.state["test_run"])
        self.assertEqual(len(self.target.state["test_run"]["invocations"]), 1)

    def test_invalid_source_inputs_publish_nothing_and_float_boundaries_are_not_rounded_early(self):
        self.target.state["items"]["schema"] = str(uuid.uuid4())
        function = publisher_function(definition_part(self.target.udf_definition(), "function_app.py"))
        inputs = fraud_deploy.fixture_payloads(
            public_deploy.read(self.folder / "fixtures.json"), str(uuid.uuid4()), "2026-11-09T15:00:00Z",
        )
        parameters = inputs[0]["parameters"]
        for key, value in (("orderId", ""), ("customerId", "x" * 129), ("riskScore", True),
                           ("riskScore", float("nan")), ("riskScore", float("inf")), ("riskScore", -0.01),
                           ("riskScore", 1.01), ("decidedAt", "2026-11-09T15:00:00"),
                           ("decidedAt", "2026-11-09T15:00:00+02:00")):
            with self.subTest(key=key, value=value):
                events = []
                client = SimpleNamespace(PublishEvent=lambda **event: events.append(event))
                with self.assertRaises(ValueError):
                    function(client, **{**parameters, key: value})
                self.assertEqual(events, [])
        for score, expected in ((0.549999999999, "release"), (0.849999999999, "manual_review")):
            with self.subTest(score=score):
                client = SimpleNamespace(PublishEvent=lambda **event: None)
                self.assertEqual(function(client, **{**parameters, "riskScore": score})["decision"], expected)

    def test_changed_fixtures_do_not_submit_orders_different_from_the_prepared_oms_batch(self):
        self.target.deploy()
        fixtures = public_deploy.read(self.folder / "fixtures.json")
        fixtures[0]["input"]["customer_id"] = "test-different-customer"
        with patch.object(fraud_deploy, "read", return_value=fixtures):
            with self.assertRaisesRegex(ValueError, "changed after order preparation"):
                self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("test_run", self.target.state)

    def test_cli_and_alias_dispatch_the_native_source_without_claiming_manual_actions(self):
        path = self.target.path.parent / "config.local.json"
        public_deploy.save(path, {**self.config, "scenario_id": "fraud"})
        aliased = public_deploy.configuration(self.folder, path)
        result = public_deploy.plan(self.folder, aliased)
        self.assertEqual(result["full_deployment_status"], "ready_for_manual_handoff")
        self.assertEqual([gap["id"] for gap in result["public_api_gaps"]], ["activator_power_automate_custom_actions"])
        self.assertFalse(result["automated_e2e_test_available"])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copyfile(self.folder / "scenario.json", folder / "scenario.json")
            arguments = ["--config", str(path), "--confirm-workspace", WORKSPACE,
                         "--state-file", str(self.target.path)]
            with patch.object(fraud_deploy, "FraudDeployment", return_value=self.target), patch("sys.stdout"):
                self.assertEqual(public_deploy.main(folder, ["deploy", *arguments]), 2)
                self.assertEqual(public_deploy.main(folder, ["test", *arguments]), 0)
            self.assertFalse(self.target.state["trigger"]["e2e_verified"])

    def test_reviewed_custom_names_and_event_propagate_to_native_bindings(self):
        config = copy.deepcopy(self.config)
        config["event_name"] = "Ecommerce.Order.ReviewDecision"
        config["resource_names"] = {
            "schema": "CustomRiskSchema", "publisher": "CustomRiskPublisher", "activator": "CustomRiskActions",
        }
        target = fraud_deploy.FraudDeployment(
            self.folder, config, self.target.path.parent / "custom-ledger.json", self.api,
        )
        result = target.deploy()
        target.test()
        self.assertEqual({row["displayName"] for row in self.api.items.values()}, set(config["resource_names"].values()))
        connection = json.loads(definition_part(target.udf_definition(), "definition.json"))["connectedDataSources"][0]
        self.assertEqual(connection["alias"], "CustomRiskSchema")
        self.assertEqual(connection["artifactId"], result["items"]["schema"])
        self.assertTrue(all(row["type"] == config["event_name"] for row in self.api.events))
        source = next(row for row in target.activator_entities() if row["type"] == "realTimeHubSource-v1")
        self.assertTrue(source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0].endswith(config["event_name"]))


if __name__ == "__main__":
    unittest.main()
