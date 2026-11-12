import copy
import datetime
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
import discount_deploy
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
    return namespace[discount_deploy.FUNCTION]


class DiscountApi(FakePublicApi):
    def __init__(self):
        super().__init__()
        self.tokens = SimpleNamespace(token=lambda resource: "unit-test-token")
        self.inputs, self.events = [], []
        self.string_output = False
        self.omit_invocation_id = False
        self.output_override = None
        self.fail_after_input = None
        self.include_metadata = False

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.definitions[path.split("/")[-2]] = copy.deepcopy(body["definition"])
            return public_api.Reply(200, {}, None)
        if path.endswith("/getDefinition") and "/userDataFunctions/" in path:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            definition = copy.deepcopy(self.definitions[path.split("/")[-2]])
            if not self.include_metadata:
                definition["parts"] = [entry for entry in definition["parts"]
                                       if entry["path"] != "resources/functions.json"]
            content = json.loads(definition_part(definition, "definition.json"))
            if not content["libraries"]["public"]:
                content["libraries"]["public"] = [
                    {"name": "fabric-user-data-functions", "type": "PYPI", "version": "1.0"},
                ]
            definition["parts"][0] = part("definition.json", content)
            return public_api.Reply(200, {}, {"definition": definition})
        if path.endswith("/invoke"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.inputs.append(copy.deepcopy(body))
            source = definition_part(self.definitions[path.split("/")[-4]], "function_app.py")
            function = publisher_function(source)
            client = SimpleNamespace(PublishEvent=lambda **event: self.events.append(copy.deepcopy(event)))
            parameters = {**body, "eventTime": datetime.datetime.fromisoformat(body["eventTime"].replace("Z", "+00:00"))}
            output = function(client, **parameters)
            if self.fail_after_input == len(self.inputs):
                raise RuntimeError("Ambiguous discount invocation transport.")
            if self.output_override is not None:
                output = copy.deepcopy(self.output_override)
            response = {
                "functionName": discount_deploy.FUNCTION, "status": "Succeeded",
                "output": json.dumps(output) if self.string_output else output, "errors": [],
            }
            if not self.omit_invocation_id:
                response["invocationId"] = str(uuid.uuid4())
            return public_api.Reply(200, {}, response)
        return super()._request(method, url, resource, body, headers)


class DiscountPublicTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = ROOT / "cicd" / "discount-approval-routing"
        self.config = public_deploy.configuration(self.folder, self.folder / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
        self.api = DiscountApi()
        self.target = discount_deploy.DiscountDeployment(
            self.folder, self.config, Path(directory.name) / "ledger.json", self.api,
        )

    def test_deployment_binds_native_policy_and_notification_without_unused_containers(self):
        result = self.target.deploy()
        self.assertEqual(result["status"], "manual_setup_required")
        self.assertEqual(set(result["items"]), {"schema", "publisher", "activator"})
        self.assertEqual(self.api.inputs, [])
        self.assertFalse(result["eventhouse_subscription_configured"])
        self.assertFalse(result["eventstream_subscription_verified"])
        self.assertFalse(result["e2e_verified"])
        definition = self.target.udf_definition()
        metadata = json.loads(definition_part(definition, "resources/functions.json"))
        parameters = metadata["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"]
        self.assertEqual(parameters[-1], {"name": "eventTime", "dataType": "datetime"})
        connection = json.loads(definition_part(definition, "definition.json"))["connectedDataSources"][0]
        self.assertEqual(connection["artifactId"], result["items"]["schema"])
        self.assertEqual(connection["artifactType"], "EventDefinition")
        rule = next(row for row in self.target.activator_entities()
                    if row["payload"].get("definition", {}).get("type") == "Rule")
        steps = json.loads(rule["payload"]["definition"]["instance"])["steps"]
        detector = next(step for step in steps if step["name"] == "EventDetectStep")
        self.assertEqual(detector["rows"], [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}])
        self.assertEqual(steps[-1]["rows"][0]["kind"], "TeamsMessage")
        self.assertNotIn("account_name", steps[-1]["rows"][0]["arguments"][-2])
        self.assertTrue(rule["payload"]["definition"]["settings"]["shouldRun"])

    def test_source_evaluates_eight_proposals_and_only_requests_six_publications(self):
        self.target.deploy()
        result = self.target.test()
        self.assertTrue(result["source_triggered"])
        self.assertTrue(result["source_execution_verified"])
        self.assertEqual(result["native_udf_evaluations"], 8)
        self.assertEqual(result["publication_requests"], 6)
        self.assertIsNone(result["events_published"])
        self.assertFalse(result["e2e_verified"])
        self.assertEqual(len(self.api.events), 6)
        events = [event["event_data"] for event in self.api.events]
        self.assertEqual(events, result["expected_review_events"])
        self.assertEqual([event["approval_level"] for event in events], ["manager"] * 2 + ["executive"] * 4)
        for event in self.api.events:
            self.assertEqual(event["type"], self.config["event_name"])
            self.assertEqual(event["data_version"], "v1")
            self.assertEqual(len(event["event_data"]), 9)
            self.assertNotIn("evaluation_id", event["event_data"])
        for payload in self.api.inputs:
            self.assertEqual(set(payload), set(discount_deploy.PARAMETERS))
            self.assertIn(result["source_run_id"], payload["dealId"])
        self.assertFalse(any(call[-1] == public_api.GRAPH or "/queryActivations" in call[1]
                             for call in self.api.calls))
        self.assertTrue(all(call[-1] == public_api.POWERBI for call in self.api.calls if call[1].endswith("/invoke")))
        self.assertFalse(self.target.verify()["e2e_verified"])
        self.assertEqual(len(self.api.inputs), 8)

    def test_packaged_policy_preserves_the_companion_sample_contract(self):
        self.target.state["items"]["schema"] = str(uuid.uuid4())
        packaged = publisher_function(definition_part(self.target.udf_definition(), "function_app.py"))
        original = publisher_function(
            (ROOT / "samples" / "user-data-functions" / "discount-approval-routing" /
             "evaluate_discount_request.py").read_text(encoding="utf-8"),
        )
        inputs = discount_deploy.fixture_payloads(
            public_deploy.read(self.folder / "fixtures.json"), str(uuid.uuid4()), "2026-11-09T06:30:00Z",
        )
        for row in inputs:
            with self.subTest(deal=row["parameters"]["dealId"]):
                calls = [[], []]
                outputs = []
                for index, function in enumerate((packaged, original)):
                    client = SimpleNamespace(PublishEvent=lambda **event: calls[index].append(event))
                    parameters = {**row["parameters"], "eventTime": datetime.datetime.fromisoformat(
                        row["parameters"]["eventTime"].replace("Z", "+00:00"),
                    )}
                    outputs.append(function(client, **parameters))
                self.assertEqual(outputs[0], outputs[1])
                self.assertEqual(calls[0], calls[1])

    def test_changed_connection_blocks_source_inputs(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        content = json.loads(definition_part(definition, "definition.json"))
        content["connectedDataSources"][0]["artifactId"] = str(uuid.uuid4())
        definition["parts"][0] = part("definition.json", content)
        with self.assertRaisesRegex(ValueError, "UDF connection"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("test_run", self.target.state)

    def test_changed_native_source_or_parameter_metadata_blocks_source_inputs(self):
        self.target.deploy()
        self.api.include_metadata = True
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        original = copy.deepcopy(definition["parts"])
        definition["parts"][1] = part("function_app.py", "# Unreviewed replacement.")
        with self.assertRaisesRegex(ValueError, "publisher code differs"):
            self.target.test()
        definition["parts"] = original
        metadata = json.loads(definition_part(definition, "resources/functions.json"))
        metadata["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"][-1]["dataType"] = "str"
        definition["parts"][-1] = part("resources/functions.json", metadata)
        with self.assertRaisesRegex(ValueError, "parameter mappings differ"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_cli_prepares_with_exit_two_and_source_test_exits_zero_without_delivery_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copyfile(self.folder / "scenario.json", folder / "scenario.json")
            config = folder / "config.local.json"
            public_deploy.save(config, self.config)
            arguments = ["--config", str(config), "--confirm-workspace", WORKSPACE,
                         "--state-file", str(self.target.path)]
            with patch.object(discount_deploy, "DiscountDeployment", return_value=self.target), patch(
                "sys.stdout",
            ):
                self.assertEqual(public_deploy.main(folder, ["deploy", *arguments]), 2)
                self.assertEqual(public_deploy.main(folder, ["test", *arguments]), 0)
            self.assertFalse(self.target.state["trigger"]["e2e_verified"])
            self.assertEqual(len(self.api.inputs), 8)

    def test_duplicate_invocation_identity_retains_the_unresolved_proposal(self):
        self.target.deploy()
        original = self.api._request
        identity = str(uuid.uuid4())

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
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test()

    def test_ambiguous_source_evaluation_never_replays_an_input(self):
        self.target.deploy()
        self.api.fail_after_input = 3
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.target.test()
        self.assertEqual(len(self.api.events), 1)
        self.assertEqual(len(self.api.inputs), 3)
        self.assertIn("pending_input", public_deploy.read(self.target.path)["test_run"])
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "incomplete or ambiguous"):
            self.target.verify()
        self.assertEqual(len(self.api.inputs), 3)

    def test_string_output_and_optional_diagnostics_preserve_supported_response_behavior(self):
        self.target.deploy()
        self.api.string_output = True
        self.api.omit_invocation_id = True
        result = self.target.test()
        self.assertEqual(result["publication_requests"], 6)
        self.assertFalse(result["e2e_verified"])

    def test_success_shaped_wrong_policy_result_is_not_source_success(self):
        self.target.deploy()
        self.api.output_override = {"approval_level": "none", "event_published": 1, "message": "Not a boolean."}
        with self.assertRaisesRegex(RuntimeError, "reviewed policy"):
            self.target.test()
        self.assertIn("pending_input", self.target.state["test_run"])
        self.assertEqual(self.target.state["test_run"]["invocations"], [])

    def test_invalid_fixtures_fail_before_any_source_submission(self):
        fixtures = public_deploy.read(self.folder / "fixtures.json")
        for field, value in (("deal_value", True), ("requested_discount_percent", float("nan")),
                             ("estimated_margin_percent", 101)):
            with self.subTest(field=field):
                invalid = copy.deepcopy(fixtures)
                invalid[-1]["input"][field] = value
                with self.assertRaisesRegex(ValueError, "Invalid discount fixture"):
                    discount_deploy.fixture_payloads(invalid, str(uuid.uuid4()), "2026-11-09T06:30:00Z")
        with self.assertRaisesRegex(ValueError, "timezone-aware UTC"):
            discount_deploy.fixture_payloads(fixtures, str(uuid.uuid4()), "2026-11-09T06:30:00")

    def test_configured_plan_retains_the_manual_source_gap_and_does_not_claim_live_support(self):
        result = public_deploy.plan(self.folder, self.config)
        self.assertEqual(result["full_deployment_status"], "ready_for_manual_handoff")
        self.assertEqual(set(result["implemented_resources"]), {"schema", "publisher", "activator"})
        self.assertEqual(result["remaining_resources"], {"eventstream": "DiscountRouting"})
        self.assertEqual([gap["id"] for gap in result["public_api_gaps"]], ["eventstream_business_events_source"])
        self.assertTrue(result["automated_source_trigger_available"])
        self.assertFalse(result["automated_e2e_test_available"])
        self.assertFalse(result["public_contract_review"]["live_deployment_verified"])

    def test_legacy_alias_and_custom_names_propagate_without_adopting_old_resources(self):
        config = copy.deepcopy(self.config)
        config.update(scenario_id="discount", event_name="Sales.Deals.ReviewRequired")
        config["resource_names"] = {
            "schema": "CustomDiscountSchema", "publisher": "CustomDiscountPublisher",
            "activator": "CustomDiscountActions", "eventstream": "CustomDiscountRouting",
        }
        path = self.target.path.parent / "custom.json"
        public_deploy.save(path, config)
        config = public_deploy.configuration(self.folder, path)
        target = discount_deploy.DiscountDeployment(
            self.folder, config, self.target.path.parent / "custom-ledger.json", self.api,
        )
        result = target.deploy()
        self.assertEqual(set(result["items"]), {"schema", "publisher", "activator"})
        self.assertIn('alias="CustomDiscountSchema"', definition_part(target.udf_definition(), "function_app.py"))
        target.test()
        self.assertTrue(all(event["type"] == "Sales.Deals.ReviewRequired" for event in self.api.events))
        ledger = public_deploy.read(target.path)
        ledger.pop("discount_public_version")
        public_deploy.save(target.path, ledger)
        with self.assertRaisesRegex(ValueError, "No adoption"):
            discount_deploy.DiscountDeployment(self.folder, config, target.path, self.api)

    def test_disabled_notification_blocks_source_evaluations(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["activator"]]
        entities = json.loads(definition_part(definition, "ReflexEntities.json"))
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        rule["payload"]["definition"]["settings"]["shouldRun"] = False
        definition["parts"][0] = part("ReflexEntities.json", entities)
        with self.assertRaises(ValueError):
            self.target.test()
        self.assertEqual(self.api.inputs, [])
