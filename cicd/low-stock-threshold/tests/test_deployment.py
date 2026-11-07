import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import deploy
import native


def configuration():
    config = json.loads((ROOT / "config.card.example.json").read_text())
    config.update(
        workspace_id=str(uuid.uuid4()), environment="msit",
        teams_delivery="individual", teams_recipients=["@me"],
    )
    return native.validate_config(config)


class InventoryDeploymentTests(unittest.TestCase):
    def test_channel_does_not_fall_back_to_individual_messages(self):
        config = configuration()
        config.update(teams_delivery="channel", teams_recipients=[])
        native.validate_config(config)
        with self.assertRaisesRegex(ValueError, "No individual-message substitute"):
            native.definition("activator", config)
        config["teams_recipients"] = ["@me"]
        with self.assertRaisesRegex(ValueError, "must not contain individual"):
            native.validate_config(config)

    def test_native_channel_export_rejects_an_individual_rule(self):
        config = configuration()
        entities = json.loads(native.decoded(native.definition("activator", config), "ReflexEntities.json"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ReflexEntities.json"
            path.write_text(json.dumps(entities))
            config.update(teams_delivery="channel", teams_recipients=[], teams_definition_file=str(path))
            with self.assertRaisesRegex(ValueError, "not a channel-post"):
                native.definition("activator", config)

    def test_native_bindings_preserve_schema_connection_and_each_event_condition(self):
        config = configuration()
        identifiers = {role: str(uuid.uuid4()) for role in native.items(config)}
        definitions = {role: native.definition(role, config, identifiers) for role in native.items(config)}
        schema = json.loads(native.decoded(definitions["schema"], "EventSchemaSetDefinition.json"))
        avro = json.loads(schema["schemas"][0]["versions"][0]["schema"])
        self.assertEqual(avro, json.loads((ROOT / "schema.avsc").read_text()))
        publisher = json.loads(native.decoded(definitions["publisher"], "definition.json"))
        self.assertEqual(publisher["connectedDataSources"], [{
            "alias": config["resource_names"]["schema"], "artifactId": identifiers["schema"],
            "artifactType": "EventDefinition", "workspaceId": config["workspace_id"],
        }])
        entities = json.loads(native.decoded(definitions["activator"], "ReflexEntities.json"))
        rule = next(entity["payload"]["definition"] for entity in entities
                    if entity["payload"].get("definition", {}).get("type") == "Rule")
        instance = json.loads(rule["instance"])
        detect = next(step for step in instance["steps"] if step["name"] == "EventDetectStep")
        self.assertEqual(detect["rows"], [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}])

    def test_reported_findings_invoke_only_the_native_inventory_publisher(self):
        config = configuration()
        api = Mock(environment="msit")
        api.request.return_value.body = {"status": "Succeeded", "output": {"event_published": True}}
        with tempfile.TemporaryDirectory() as directory:
            target = deploy.Deployment(config, Path(directory) / "ledger.json", api)
            target.state.update(
                status="configured_delivery_unverified",
                items={role: str(uuid.uuid4()) for role in native.items(config)},
            )
            with patch.object(target, "preflight"), patch.object(target, "connect_history"), patch.object(target, "readback"):
                result = target.run_test()
            batch = target.state["last_run"]
            self.assertEqual(result["expected_events"], 3)
            self.assertEqual(api.request.call_count, 3)
            for call, payload in zip(api.request.call_args_list, batch["inputs"]):
                self.assertEqual(call.args[0], "POST")
                self.assertTrue(call.args[1].endswith("/functions/publish_low_stock_event/invoke"))
                self.assertEqual(call.args[2], {
                    destination: payload[source]
                    for source, destination in native.manifest()["parameter_map"].items()
                })
            target.validate_rows([{**payload, "Deliveries": 1} for payload in batch["expected"]], batch)
            damaged = copy.deepcopy(batch["expected"])
            damaged[0]["store_id"] = "wrong-location"
            with self.assertRaisesRegex(ValueError, "store_id"):
                target.validate_rows([{**payload, "Deliveries": 1} for payload in damaged], batch)

    def test_publication_response_is_not_a_delivery_verification(self):
        config = configuration()
        api = Mock(environment="msit")
        api.request.return_value.body = {"status": "Succeeded", "output": {"event_published": True}}
        with tempfile.TemporaryDirectory() as directory:
            target = deploy.Deployment(config, Path(directory) / "ledger.json", api)
            batch = target.make_batch()
            target.state["items"]["publisher"] = str(uuid.uuid4())
            target.state["last_run"] = batch
            target.publish_findings(batch)
            self.assertEqual(batch["status"], "inputs_submitted_consumers_unverified")
            self.assertNotIn("verification", batch)
            api.request.return_value.body = {"status": "Succeeded", "output": {"event_published": False}}
            with self.assertRaisesRegex(ValueError, "did not confirm"):
                target.publish_findings(target.make_batch())

    def test_deployment_never_invokes_a_publisher_or_loads_test_inputs(self):
        config = configuration()
        api = Mock(environment="msit")
        with tempfile.TemporaryDirectory() as directory:
            target = deploy.Deployment(config, Path(directory) / "ledger.json", api)
            target.state["items"] = {role: str(uuid.uuid4()) for role in native.items(config)}
            api.request.return_value.body = {
                "properties": {
                    "parentEventhouseItemId": target.state["items"]["eventhouse"],
                    "queryServiceUri": "https://example.kusto.windows.net",
                }
            }
            with patch.object(target, "preflight"), patch.object(target, "import_items"), \
                    patch.object(target, "readback"), patch.object(target, "grant_access"), \
                    patch.object(target, "connect_history"):
                result = target.deploy()
            self.assertEqual(result["events_published"], 0)
            self.assertEqual(result["dummy_rows_loaded"], 0)
            self.assertNotIn("last_run", target.state)
            self.assertEqual(api.request.call_count, 1)
            self.assertEqual(api.request.call_args.args[0], "GET")


if __name__ == "__main__":
    unittest.main()
