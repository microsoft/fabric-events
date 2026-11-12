import copy
import datetime
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import uuid

from test_public_cicd import ActionApi, ROOT, TENANT, WORKSPACE
import inventory_deploy
import public_api
import public_deploy
from public_consumers import definition_part, part


class InventoryApi(ActionApi):
    def __init__(self):
        super().__init__()
        self.tokens = SimpleNamespace(token=lambda resource: "unit-test-token")
        self.inputs, self.messages = [], []
        self.chat_id = "19:inventory@thread.v2"
        self.failure = None
        self.output = {"event_published": True}
        self.duplicate_receipt = False

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/getDefinition") and "/userDataFunctions/" in path:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            definition = copy.deepcopy(self.definitions[path.split("/")[-2]])
            definition["parts"] = [entry for entry in definition["parts"] if entry["path"] != "resources/functions.json"]
            content = json.loads(definition_part(definition, "definition.json"))
            if not content["libraries"]["public"]:
                content["libraries"]["public"] = [{"name": "fabric-user-data-functions", "type": "PYPI", "version": "1.0"}]
            definition["parts"][0] = part("definition.json", content)
            return public_api.Reply(200, {}, {"definition": definition})
        if path.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.definitions[path.split("/")[-2]] = copy.deepcopy(body["definition"])
            return public_api.Reply(200, {}, None)
        if path.endswith("/invoke"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.inputs.append(copy.deepcopy(body))
            if self.failure:
                raise RuntimeError(self.failure)
            self.emit_record("inventory", {field: body[name] for name, (field, _) in inventory_deploy.PARAMETERS.items()})
            message = {
                "id": str(len(self.messages)), "from": {"application": {"id": "unit-test-bot"}},
                "createdDateTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "body": {"content": " ".join(f"{field}: {body[name]}"
                                          for name, (field, _) in inventory_deploy.PARAMETERS.items())},
                "attachments": [],
            }
            self.messages.append(message)
            if self.duplicate_receipt:
                self.messages.append({**message, "id": message["id"] + "-duplicate"})
            return public_api.Reply(200, {}, {
                "functionName": inventory_deploy.FUNCTION, "invocationId": str(uuid.uuid4()),
                "status": "Succeeded", "output": copy.deepcopy(self.output), "errors": [],
            })
        if resource == public_api.GRAPH:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            return public_api.Reply(200, {}, copy.deepcopy(
                {"value": self.messages} if "/messages" in path else {"id": self.chat_id, "chatType": "oneOnOne"},
            ))
        return super()._request(method, url, resource, body, headers)


class InventoryPublicTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = ROOT / "cicd" / "low-stock-threshold"
        self.config = public_deploy.configuration(self.folder, self.folder / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE, accept_preview_apis=True)
        self.api = InventoryApi()
        self.target = inventory_deploy.InventoryDeployment(
            self.folder, self.config, Path(directory.name) / "ledger.json", self.api,
        )
        self.clock = [0]
        self.api.sleep = lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds)
        self.addCleanup(patch.stopall)
        patch.object(inventory_deploy.time, "monotonic", side_effect=lambda: self.clock[0]).start()

    def test_definition_binds_public_schema_id_without_format_or_internal_calls(self):
        self.target.deploy()
        self.assertEqual(self.target.state["verification"]["udf_metadata_readback"],
                         "not_returned_by_public_definition")
        self.assertEqual({item["type"] for item in self.api.items.values()},
                         {"EventSchemaSet", "UserDataFunction", "Reflex", "Lakehouse", "Notebook", "DataPipeline"})
        self.assertEqual(self.api.inputs, [])
        address, request = self.target.creation_request("publisher")
        self.assertTrue(address.endswith("/userDataFunctions"))
        self.assertNotIn("format", request["definition"])
        definition = json.loads(definition_part(request["definition"], "definition.json"))
        self.assertEqual(definition["connectedDataSources"], [{
            "alias": self.config["resource_names"]["schema"], "artifactId": self.target.state["items"]["schema"],
            "artifactType": "EventDefinition", "workspaceId": WORKSPACE,
        }])
        self.assertEqual({entry["path"] for entry in request["definition"]["parts"]},
                         {"definition.json", "function_app.py", "resources/functions.json"})
        metadata = json.loads(definition_part(request["definition"], "resources/functions.json"))
        parameters = metadata["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"]
        self.assertEqual([entry["name"] for entry in parameters], list(inventory_deploy.PARAMETERS))
        entities = json.loads(definition_part(self.target.get_definition("activator"), "ReflexEntities.json"))
        rule = next(entry for entry in entities if entry["payload"].get("definition", {}).get("type") == "Rule")
        steps = json.loads(rule["payload"]["definition"]["instance"])["steps"]
        detector = next(step for step in steps if step["name"] == "EventDetectStep")
        self.assertEqual(detector["rows"], [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}])
        for field, _ in inventory_deploy.PARAMETERS.values():
            self.assertIn(field, rule["payload"]["definition"]["instance"])

    def test_pipeline_persists_three_requests_without_teams_or_direct_consumer_invocation(self):
        self.target.deploy()
        triggered = self.target.test()
        self.assertFalse(triggered["e2e_verified"])
        self.assertEqual(len(triggered["native_udf_invocation_ids"]), 3)
        self.assertEqual(len(triggered["product_ids"]), 3)
        self.assertFalse(any("/queryActivations" in call[1] for call in self.api.calls))
        result = self.target.verify()
        self.assertTrue(result["e2e_verified"])
        self.assertEqual(result["persisted_outcome_status"], "pending_approval")
        self.assertEqual(result["operational_records_verified"], 3)
        self.assertEqual(len(self.api.inputs), 3)
        self.assertFalse(any(call[-1] == public_api.GRAPH for call in self.api.calls))
        self.assertFalse(any(call[0] == "POST" and "/jobs/" in call[1] for call in self.api.calls))
        self.assertEqual(len(self.target.state["test_run"]["pipeline_jobs"]), 3)

    def test_changed_pipeline_parameter_blocks_source_inputs(self):
        self.target.deploy()
        identifier = self.target.state["items"]["response_pipeline"]
        definition = self.api.definitions[identifier]
        content = json.loads(definition_part(definition, "pipeline-content.json"))
        content["properties"]["activities"][0]["typeProperties"]["parameters"]["current_stock"]["value"]["value"] = "10"
        definition["parts"][0] = part("pipeline-content.json", content)
        with self.assertRaisesRegex(ValueError, "pipeline's seven parameters"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("test_run", self.target.state)

    def test_source_execution_is_not_teams_delivery_and_ambiguous_invocations_never_replay(self):
        self.target.deploy()
        self.target.source_only = True
        result = self.target.test()
        self.assertFalse(result["e2e_verified"])
        self.assertTrue(result["source_execution_verified"])
        self.assertIsNone(result["events_published"])
        self.assertEqual(result["native_udf_invocations"], 3)
        invokes = [call for call in self.api.calls if call[1].endswith("/invoke")]
        self.assertTrue(all(call[-1] == public_api.POWERBI for call in invokes))
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test()
        self.target.verify()
        self.assertEqual(len(self.api.inputs), 3)

    def test_source_acceptance_and_activations_without_persisted_requests_are_not_e2e(self):
        self.target.deploy()
        self.api.omit_records = True
        self.assertTrue(self.target.test()["source_triggered"])
        with self.assertRaisesRegex(RuntimeError, "remain incomplete"):
            self.target.verify()
        self.assertFalse(self.target.state["verification"]["e2e_verified"])
        self.assertEqual(len(self.api.inputs), 3)
        self.assertEqual(len(self.api.activations), 3)

    def test_request_files_do_not_certify_a_failed_native_pipeline(self):
        self.target.deploy()
        self.target.source_only = True
        self.target.test()
        self.api.jobs[0]["status"] = "Failed"
        self.api.jobs[0]["failureReason"] = {"message": "Notebook failed after its write"}
        self.target.source_only = False
        with self.assertRaisesRegex(RuntimeError, "native replenishment pipeline failed"):
            self.target.verify()
        self.assertFalse(self.target.state["verification"]["e2e_verified"])
        self.assertEqual(len(self.target.state["test_run"]["operational_records"]), 3)

    def test_dynamic_action_references_exact_target_and_original_fields(self):
        self.target.deploy()
        entities = self.target.activator_entities()
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        step = json.loads(rule["payload"]["definition"]["instance"])["steps"][-1]
        self.assertEqual(len(step["rows"]), 1)
        binding = step["rows"][0]
        self.assertEqual(binding["kind"], "FabricItemInvocation")
        arguments = {row["name"]: row for row in binding["arguments"]}
        self.assertEqual(arguments["itemId"]["value"], self.target.state["items"]["response_pipeline"])
        self.assertEqual(arguments["fabricJobConnectionDocumentId"]["value"], self.target.state["activator_ids"]["fabric_action"])
        for parameter, field in zip(arguments["parameters"]["values"], inventory_deploy.PARAMETERS.values()):
            parts = {row["name"]: row for row in parameter["arguments"]}
            self.assertEqual(parts["parameterName"]["value"], field[0])
            reference = parts["parameterValue"]["values"][0]
            self.assertEqual(reference["type"], "complexReference")
            self.assertEqual(reference["arguments"][0]["value"], field[0])

    def test_ambiguous_udf_execution_is_saved_and_blocks_test_and_verify(self):
        self.target.deploy()
        self.api.failure = "Ambiguous runtime response"
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.target.test()
        self.assertIn("pending_input", public_deploy.read(self.target.path)["test_run"])
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "incomplete or ambiguous"):
            self.target.verify()
        self.assertEqual(len(self.api.inputs), 1)

    def test_changed_udf_connection_is_rejected_before_invocation(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        content = json.loads(definition_part(definition, "definition.json"))
        content["connectedDataSources"][0]["artifactId"] = str(uuid.uuid4())
        definition["parts"][0] = part("definition.json", content)
        with self.assertRaisesRegex(ValueError, "UDF connection"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_success_shaped_invalid_output_does_not_confirm_execution_or_replay(self):
        self.target.deploy()
        self.api.output = {"event_published": 1}
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            self.target.test()
        attempt = public_deploy.read(self.target.path)["test_run"]
        self.assertIn("pending_input", attempt)
        self.assertEqual(attempt["last_response"]["body"]["output"], self.api.output)
        self.assertEqual(attempt["invocations"], [])

    def test_unreviewed_udf_library_changes_fail_before_any_invocation(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        content = json.loads(definition_part(definition, "definition.json"))
        content["libraries"]["public"] = [{"name": "unreviewed-package", "type": "PYPI", "version": "1.0"}]
        definition["parts"][0] = part("definition.json", content)
        with self.assertRaisesRegex(ValueError, "UDF connection"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_inventory_alias_preserves_raw_configuration_and_ownership(self):
        legacy = {**self.config, "scenario_id": "inventory"}
        path = self.target.path.with_name("legacy.config.json")
        public_deploy.save(path, legacy)
        loaded = public_deploy.configuration(self.folder, path)
        self.assertEqual(loaded, legacy)
        target = inventory_deploy.InventoryDeployment(self.folder, loaded, path.with_name("legacy.ledger.json"), self.api)
        target.deploy()
        original = target.path.read_bytes()
        resumed = inventory_deploy.InventoryDeployment(self.folder, loaded, target.path, self.api)
        self.assertEqual(target.path.read_bytes(), original)
        self.assertEqual(resumed.owner, "Managed by fabric-events/cicd public-only/inventory.")
        self.assertTrue(resumed.verify_provisioning()["source_binding_verified"])

    def test_native_sdk_normalizes_without_redetecting_stock_and_propagates_publish_errors(self):
        functions = ModuleType("fabric.functions")
        functions.UserDataFunctions = lambda: SimpleNamespace(
            connection=lambda **kwargs: lambda function: function, function=lambda: lambda function: function,
        )
        functions.FabricBusinessEventsClient = object
        fabric = ModuleType("fabric")
        fabric.functions = functions
        namespace = {}
        with patch.dict(sys.modules, {"fabric": fabric, "fabric.functions": functions}):
            exec(compile((self.folder / "function_app.py").read_text(), "function_app.py", "exec"), namespace)
        client = SimpleNamespace(PublishEvent=Mock())
        values = {
            "productId": " product ", "productName": " Keyboard ", "storeId": " store ",
            "currentStock": 20, "threshold": 10, "supplierId": " supplier ", "detectedAt": "2026-01-01T00:00:00+00:00",
        }
        self.assertEqual(namespace[inventory_deploy.FUNCTION](client, **values), {"event_published": True})
        payload = client.PublishEvent.call_args.kwargs["event_data"]
        self.assertEqual(payload["product_id"], "product")
        self.assertEqual(payload["current_stock"], 20)
        self.assertEqual(payload["detected_at"], "2026-01-01T00:00:00Z")
        for key, invalid in (("currentStock", True), ("threshold", 2 ** 31),
                             ("detectedAt", "2026-01-01T00:00:00"), ("productId", " ")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                namespace[inventory_deploy.FUNCTION](client, **{**values, key: invalid})
        self.assertEqual(client.PublishEvent.call_count, 1)
        client.PublishEvent.side_effect = RuntimeError("Native publication failed")
        with self.assertRaisesRegex(RuntimeError, "Native publication failed"):
            namespace[inventory_deploy.FUNCTION](client, **values)


if __name__ == "__main__":
    unittest.main()
