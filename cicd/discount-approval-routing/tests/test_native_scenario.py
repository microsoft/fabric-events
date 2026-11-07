import ast
import base64
import copy
import inspect
import json
import math
import operator
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bundle
import card
import deploy
import native
from fabric_api import Reply


class FakeFabric:
    environment = "msit"
    control_resource = "https://analysis.windows.net/powerbi/api"

    def __init__(self, config):
        self.config = config
        self.catalog = native.items(config)
        self.inventory = []
        self.definitions = {}
        self.calls = []
        self.roles = []
        self.counter = 0
        self.tokens = Mock()
        self.tokens.identity.return_value = {
            "tenantId": "11111111-1111-4111-8111-111111111111",
            "objectId": "22222222-2222-4222-8222-222222222222", "objectType": "User",
        }
        self.tokens.current_user_address.return_value = "operator@example.invalid"
        self.check_url = Mock()
        self.sleep = Mock()

    def list_all(self, path):
        if path == "capacities":
            return [{"id": "test-capacity", "state": "Active", "sku": "F16"}]
        if path.endswith("/items"):
            return copy.deepcopy(self.inventory)
        raise AssertionError(path)

    def complete(self, reply):
        return reply.body

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body)))
        if path.endswith("/items/bulkImportDefinitions"):
            platforms = [
                (entry, json.loads(base64.b64decode(entry["payload"])))
                for entry in body["definitionParts"] if entry["path"].endswith("/.platform")
            ]
            receipts = []
            logical_to_actual = {}
            for entry, platform in platforms:
                self.counter += 1
                identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fake-import-{self.counter}"))
                logical_to_actual[platform["config"]["logicalId"]] = identifier
            for entry, platform in platforms:
                identifier = logical_to_actual[platform["config"]["logicalId"]]
                metadata = platform["metadata"]
                self.inventory.append({"id": identifier, **metadata})
                root = entry["path"].rsplit("/", 1)[0] + "/"
                parts = [
                    {**part, "path": part["path"][len(root):]}
                    for part in body["definitionParts"] if part["path"].startswith(root)
                ]
                for part in parts:
                    if part["path"] == "DatabaseProperties.json":
                        value = json.loads(base64.b64decode(part["payload"]))
                        value["parentEventhouseItemId"] = logical_to_actual[value["parentEventhouseItemId"]]
                        part["payload"] = native.part(part["path"], json.dumps(value))["payload"]
                self.definitions[identifier] = {"parts": parts}
                receipts.append({
                    "itemId": identifier, "itemLogicalId": platform["config"]["logicalId"],
                    "itemDisplayName": metadata["displayName"], "itemType": metadata["type"],
                    "operationStatus": "Succeeded",
                })
            return Reply(200, {}, {"importItemDefinitionsDetails": receipts})
        if path.endswith("/getDefinition"):
            return Reply(200, {}, {"definition": copy.deepcopy(self.definitions[path.split("/")[-2]])})
        if "/kqlDatabases/" in path:
            identifier = path.rsplit("/", 1)[-1]
            props = json.loads(native.decoded(self.definitions[identifier], "DatabaseProperties.json"))
            return Reply(200, {}, {
                "id": identifier,
                "properties": {**props, "queryServiceUri": "https://test.kusto.fabric.microsoft.com"},
            })
        if path.endswith("/security/enable"):
            return Reply(200, {}, {})
        if path.endswith("/dataAccessRoles"):
            if method == "PUT":
                self.roles = copy.deepcopy(body["value"])
            exported = copy.deepcopy(self.roles)
            for role in exported:
                role["id"] = str(uuid.uuid4())
            return Reply(200, {"ETag": "test-etag"}, {"value": exported})
        if path.endswith("/topology"):
            identifier = path.split("/")[-2]
            topology = json.loads(native.decoded(self.definitions[identifier], "eventstream.json"))
            for node in topology["sources"] + topology["destinations"]:
                node.update(id=str(uuid.uuid4()), status="Running")
                node.pop("inputSchemas", None)
            return Reply(200, {}, topology)
        if "/items/" in path and method == "GET":
            identifier = path.rsplit("/", 1)[-1]
            return Reply(200, {}, next(item for item in self.inventory if item["id"] == identifier))
        if path == f"workspaces/{self.config['workspace_id']}":
            return Reply(200, {}, {"displayName": "Synthetic test workspace", "capacityId": "test-capacity"})
        raise AssertionError((method, path, body))


class NativeScenarioTests(unittest.TestCase):
    def setUp(self):
        self.config = deploy.load_config(ROOT / "config.card.example.json")
        self.config.update(workspace_id=str(uuid.uuid4()), environment="msit")
        self.spec = native.manifest()
        self.temporary = tempfile.TemporaryDirectory(prefix="native scenario tests ")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config_path = self.directory / "config.json"
        self.config_path.write_text(json.dumps(self.config))

    def target(self):
        api = FakeFabric(self.config)
        return deploy.Deployment(self.config, self.directory / "attempt.json", api), api

    def test_plan_is_offline_even_without_workspace_or_credentials(self):
        config = deploy.load_config(ROOT / "config.wizard.example.json")
        with patch.object(deploy.FabricApi, "request", side_effect=AssertionError("network")):
            result = deploy.plan(config)
        self.assertFalse(result["cloud_writes"])
        self.assertFalse(result["publisher_executes_during_deploy"])
        self.assertEqual(result["manual_eventstream_subscription_required"], self.spec["manual_eventstream"])

    def test_all_names_event_and_dependencies_bind_together(self):
        config = copy.deepcopy(self.config)
        config["resource_prefix"] = "Renamed"
        config["event_name"] = "Synthetic.Renamed.Exception"
        config["resource_names"] = {role: "Renamed" + role.title() for role in self.spec["resource_roles"]}
        native.validate_config(config)
        actual = {role: str(uuid.uuid4()) for role in self.spec["items"]}
        payload = native.bulk_payload(self.spec["items"], config, actual, ["operator@example.invalid"])
        text = "\n".join(base64.b64decode(part["payload"]).decode() for part in payload["definitionParts"])
        self.assertNotIn("${", text)
        self.assertIn(config["event_name"], text)
        self.assertFalse(payload["options"]["allowPairingByName"])
        props = json.loads(native.decoded(native.definition("database", config, actual), "DatabaseProperties.json"))
        self.assertEqual(props["parentEventhouseItemId"], actual["eventhouse"])
        for role in self.spec["items"]:
            definition = native.definition(role, config, actual, ["operator@example.invalid"])
            platform = json.loads(native.decoded(definition, ".platform"))
            self.assertEqual(platform["metadata"]["displayName"], config["resource_names"][role])
            self.assertNotEqual(platform["config"]["logicalId"], actual[role])
        if self.spec["manual_eventstream"]:
            definition = native.definition("publisher", config, actual)
            metadata = json.loads(native.decoded(definition, "definition.json"))
            self.assertEqual(metadata["connectedDataSources"][0], {
                "alias": config["resource_names"]["schema"], "artifactId": actual["schema"],
                "artifactType": "EventDefinition", "workspaceId": config["workspace_id"],
            })
        else:
            graph = json.loads(native.decoded(native.definition("publisher", config, actual), "eventstream.json"))
            self.assertEqual(graph["destinations"][0]["properties"]["itemId"], actual["schema"])

    def test_invalid_or_incompatible_configuration_is_rejected(self):
        for mutate in (
            lambda c: c.update(scenario_id="different"),
            lambda c: c.update(source_backend="notebook"),
            lambda c: c.update(event_name="Bad-Event"),
            lambda c: c.update(teams_delivery="channel"),
            lambda c: c.update(teams_recipients=[]),
            lambda c: c["resource_names"].update(schema=c["resource_names"]["activator"].upper()),
            lambda c: c["resource_names"].update(schema="CON"),
            lambda c: c["resource_names"].update({self.spec["eventstream_roles"][0]: "Invalid Stream"}),
        ):
            config = copy.deepcopy(self.config)
            mutate(config)
            with self.assertRaises(ValueError):
                native.validate_config(config)

    def test_native_deploy_does_not_inject_or_invoke(self):
        target, api = self.target()
        with patch.object(deploy.connections, "ensure", return_value={"status": "native_association_verified"}):
            result = target.deploy()
        expected = "manual_eventstream_subscription_required" if self.spec["manual_eventstream"] else "configured_delivery_unverified"
        self.assertEqual(result["status"], expected)
        self.assertEqual(set(target.state["items"]), set(self.spec["items"]))
        self.assertFalse(any("/invoke" in path or "/connection" in path for _, path, _ in api.calls))
        self.assertNotIn("last_run", target.state)
        imported_types = {item["type"] for item in api.inventory}
        self.assertNotIn("Notebook", imported_types)
        if self.spec["manual_eventstream"]:
            self.assertNotIn("Eventstream", imported_types)
            with self.assertRaises(ValueError):
                target.run_test()

    def test_permission_readback_checks_policy_not_regenerated_server_role_id(self):
        target, api = self.target()
        target.state["items"]["schema"] = str(uuid.uuid4())
        target.grant_access()
        target.grant_access()
        self.assertEqual(len([call for call in api.calls if call[0] == "PUT"]), 2)

    def test_batch_launcher_reports_manual_step_without_running_test(self):
        target, api = self.target()
        with (
            patch.object(card, "ROOT", self.directory),
            patch.object(deploy, "FabricApi", return_value=api),
            patch.object(deploy.connections, "ensure", return_value={"status": "native_association_verified"}),
        ):
            code = card.main(["deploy", "--config", str(self.config_path)])
        self.assertEqual(code, 3 if self.spec["manual_eventstream"] else 0)
        active = next((self.directory / ".deployments").rglob("*.active.json"))
        state = json.loads(Path(json.loads(active.read_text())["state_file"]).read_text())
        self.assertNotIn("last_run", state)
        self.assertFalse(any("/invoke" in path or "/connection" in path for _, path, _ in api.calls))

    def test_native_binding_readback_rejects_changed_event_or_rule(self):
        expected = native.definition("activator", self.config, recipients=["operator@example.invalid"])
        actual = copy.deepcopy(expected)
        entities = json.loads(native.decoded(actual, "ReflexEntities.json"))
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        rule["payload"]["definition"]["settings"]["shouldRun"] = False
        actual["parts"] = [part for part in actual["parts"] if part["path"] != "ReflexEntities.json"]
        actual["parts"].append(native.part("ReflexEntities.json", json.dumps(entities)))
        with self.assertRaises(ValueError):
            native.assert_definition(expected, actual)

    def test_platform_managed_kql_export_is_not_an_empty_source_script(self):
        expected = native.definition("database", self.config)
        actual = copy.deepcopy(expected)
        for entry in actual["parts"]:
            if entry["path"] == "DatabaseSchema.kql":
                entry.update(native.part(entry["path"], ".create-merge table ManagedHistory (id:string)"))
        self.assertTrue(native.assert_definition(expected, actual))
        for entry in expected["parts"]:
            if entry["path"] == "DatabaseSchema.kql":
                entry.update(native.part(entry["path"], ".create table RequiredInput (id:string)"))
        with self.assertRaises(ValueError):
            native.assert_definition(expected, actual)

    def test_eventstream_node_order_is_not_topology_but_input_references_are(self):
        if self.spec["manual_eventstream"]:
            self.skipTest("Eventstream publisher only")
        expected = native.definition("publisher", self.config)
        graph = json.loads(native.decoded(expected, "eventstream.json"))
        graph["operators"].reverse()
        actual = copy.deepcopy(expected)
        actual["parts"] = [part for part in actual["parts"] if part["path"] != "eventstream.json"]
        actual["parts"].append(native.part("eventstream.json", json.dumps(graph)))
        native.assert_definition(expected, actual)
        graph["operators"][0]["inputNodes"] = [{"name": "Payments"}]
        actual["parts"] = [part for part in actual["parts"] if part["path"] != "eventstream.json"]
        actual["parts"].append(native.part("eventstream.json", json.dumps(graph)))
        with self.assertRaises(ValueError):
            native.assert_definition(expected, actual)

    def test_udf_omitted_generated_metadata_does_not_hide_changed_source(self):
        if not self.spec["manual_eventstream"]:
            self.skipTest("UDF publisher only")
        expected = native.definition("publisher", self.config)
        actual = copy.deepcopy(expected)
        actual["parts"] = [part for part in actual["parts"] if part["path"] != "resources/functions.json"]
        self.assertTrue(native.assert_definition(expected, actual))
        for entry in actual["parts"]:
            if entry["path"] == "function_app.py":
                entry.update(native.part(entry["path"], native.decoded(expected, entry["path"]).replace(
                    "requestedDiscountPercent > 15", "requestedDiscountPercent > 16",
                )))
        with self.assertRaises(ValueError):
            native.assert_definition(expected, actual)

    def test_native_import_saves_pending_location_before_wait(self):
        target, api = self.target()
        api.request = Mock(return_value=Reply(
            202, {"Location": "https://msitapi.fabric.microsoft.com/v1/operations/test"}, {},
        ))
        api.complete = Mock(side_effect=TimeoutError("still running"))
        with self.assertRaises(TimeoutError):
            target.import_items(("schema",))
        state = json.loads(target.state_path.read_text())
        self.assertEqual(state["pending_import"]["roles"], ["schema"])
        self.assertTrue(state["pending_import"]["location"].endswith("/operations/test"))
        self.assertEqual(len(api.request.call_args_list), 1)

    def test_collision_never_adopts_an_existing_item(self):
        target, api = self.target()
        api.inventory.append({
            "id": str(uuid.uuid4()), "displayName": self.config["resource_names"]["schema"],
            "type": "EventSchemaSet", "description": self.spec["owner"],
        })
        with self.assertRaisesRegex(ValueError, "collision"):
            target.preflight()
        self.assertEqual(target.state["items"], {})
        self.assertFalse(any(method != "GET" for method, _, _ in api.calls))

    def test_current_reference_never_falls_back_to_old_success(self):
        with patch.object(card, "ROOT", self.directory), patch.object(deploy, "main", side_effect=[0, 0, 0, 0, 0, 1]):
            args = ["deploy", "--config", str(self.config_path)]
            self.assertEqual(card.main(args), 0)
            active = next((self.directory / ".deployments").rglob("*.active.json"))
            first = json.loads(active.read_text())["state_file"]
            self.assertEqual(card.main(args), 1)
            second = json.loads(active.read_text())["state_file"]
        self.assertNotEqual(first, second)
        self.assertTrue(Path(first).is_file())
        self.assertTrue(Path(second).is_file())
        with patch.object(card, "ROOT", self.directory):
            with self.assertRaisesRegex(ValueError, "Complete deployment"):
                card.main(["test", "--config", str(self.config_path)])

    def test_unbound_public_example_cannot_start_a_deployment(self):
        with patch.object(card, "ROOT", self.directory), patch.object(deploy, "FabricApi") as api:
            with self.assertRaisesRegex(ValueError, "approved workspace_id"):
                card.main(["deploy", "--config", str(ROOT / "config.card.example.json")])
        api.assert_not_called()
        self.assertFalse((self.directory / ".deployments").exists())

    def test_verify_only_never_calls_publisher(self):
        target, _ = self.target()
        target.state["last_run"] = target.make_batch()
        target.state["last_run"]["status"] = "inputs_submitted_consumers_unverified"
        rows = [{**row, "Deliveries": 2} for row in target.state["last_run"]["expected"]]
        with patch.object(target, "history_rows", return_value=rows), patch.object(target, "run_test", side_effect=AssertionError):
            result, code = target.verify(wait_seconds=0)
            self.assertEqual(code, 2)
            self.assertEqual(result["teams"], "not_verified")
            result, code = target.verify(
                teams_confirmed=True, eventstream_confirmed=self.spec["manual_eventstream"], wait_seconds=0,
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["received_records"], 2 * len(rows))

    def test_history_rejects_wrong_payload_missing_or_negative_event(self):
        target, _ = self.target()
        batch = target.make_batch()
        rows = [{**row, "Deliveries": 1} for row in batch["expected"]]
        target.validate_rows(rows, batch)
        variants = [rows[:-1], [*rows, {**batch["inputs"][0], "Deliveries": 1}]]
        changed = copy.deepcopy(rows)
        changed[0][self.spec["fields"][1]] = "unexpected"
        variants.append(changed)
        for variant in variants:
            with self.assertRaises(ValueError):
                target.validate_rows(variant, batch)

    def test_ambiguous_test_requires_explicit_new_batch(self):
        target, _ = self.target()
        target.state.update(status="configured_delivery_unverified", last_run={"status": "publication_started"})
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            target.run_test()

    def test_shared_runtime_and_existing_helpers_remain_identical(self):
        sibling = ROOT.parent / ("high-value-payment" if self.spec["manual_eventstream"] else "discount-approval-routing")
        for filename in ("native.py", "deploy.py", "card.py", "bundle.py", "deploy.bat", "test.bat", "fabric_api.py", "connections.py"):
            self.assertEqual((ROOT / filename).read_bytes(), (sibling / filename).read_bytes(), filename)
        for filename in ("fabric_api.py", "connections.py"):
            self.assertEqual((ROOT / filename).read_bytes(), (ROOT.parent / "sales-volume-alert" / filename).read_bytes())

    def test_optional_zip_contains_only_scenario_sources(self):
        output = bundle.bundle(self.directory / "scenario.zip")
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            prefix = "cicd/" + ROOT.name + "/"
            self.assertTrue(all(name.startswith(prefix) for name in names))
            self.assertIn(prefix + "deploy.bat", names)
            self.assertIn(prefix + "test.bat", names)
            self.assertFalse(any(name.lower().endswith((".md", ".html")) for name in names))
            self.assertFalse(any(
                forbidden in name for name in names
                for forbidden in (".deployments", ".local.json", "__pycache__", "README.md", "/tests/")
            ))
            for entry in archive.infolist():
                self.assertEqual(archive.read(entry), (ROOT / entry.filename[len(prefix):]).read_bytes())

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_real_batch_entrypoint_from_extracted_path_with_spaces(self):
        archive = bundle.bundle(self.directory / "scenario.zip")
        extracted = self.directory / "extracted folder with spaces"
        with zipfile.ZipFile(archive) as source:
            source.extractall(extracted)
        package = extracted / "cicd" / ROOT.name
        (package / "card.py").write_text(
            "import json,sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(3 if sys.argv[1]=='deploy' else 2)\n",
            encoding="utf-8",
        )
        for phase, code in (("deploy", 3), ("test", 2)):
            command = (
                f'"{os.environ["COMSPEC"]}" /d /s /c '
                f'""{package / (phase + ".bat")}" --config "custom configuration.json""'
            )
            result = subprocess.run(
                command,
                cwd=self.directory, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), [phase, "--config", "custom configuration.json"])

    def test_payment_native_graph_filters_boundaries_without_currency_conversion(self):
        if self.spec["manual_eventstream"]:
            self.skipTest("Payment publisher only")
        graph = json.loads(native.decoded(native.definition("publisher", self.config), "eventstream.json"))
        cases = json.loads((ROOT / "data" / "test-cases.json").read_text())
        nodes = {node["name"]: node for group in ("sources", "streams", "operators", "destinations") for node in graph[group]}
        memo = {}

        def evaluate(name):
            if name in memo:
                return memo[name]
            node = nodes[name]
            if node["type"] == "CustomEndpoint":
                rows = [case["input"] for case in cases]
            else:
                rows = [row for parent in node["inputNodes"] for row in evaluate(parent["name"])]
                if node["type"] == "Filter":
                    for condition in node["properties"]["conditions"]:
                        comparison = {"GreaterThan": operator.gt, "GreaterThanOrEquals": operator.ge}[condition["operatorType"]]
                        rows = [row for row in rows if comparison(row[condition["column"]["columnName"]], float(condition["value"]["value"]))]
                elif node["type"] == "ManageFields":
                    rows = [{
                        column["alias"]: row[column["properties"]["column"]["columnName"]]
                        for column in node["properties"]["columns"]
                    } for row in rows]
            memo[name] = rows
            return rows

        actual = evaluate(graph["destinations"][0]["name"])
        self.assertEqual(actual, [case["input"] for case in cases if case["expected"] is not None])
        self.assertEqual({row["currency"] for row in actual}, {"USD", "EUR", "JPY"})
        self.assertTrue(all(set(row) == set(self.spec["fields"]) for row in actual))

    def test_payment_test_injects_all_inputs_without_python_filtering_or_persisting_keys(self):
        if self.spec["manual_eventstream"]:
            self.skipTest("Payment injection only")
        target, api = self.target()
        target.state["items"]["publisher"] = str(uuid.uuid4())
        batch = target.make_batch()
        target.state["last_run"] = batch
        target.save()
        api.request = Mock(side_effect=[
            Reply(200, {}, {"sources": [{"id": str(uuid.uuid4()), "type": "CustomEndpoint"}]}),
            Reply(200, {}, {
                "type": "CustomEndpoint", "eventHubName": "synthetic-input",
                "accessKeys": {"primaryConnectionString": "synthetic-endpoint"},
            }),
        ])
        sdk = types.ModuleType("azure.eventhub")
        sdk.EventData = lambda text: json.loads(text)
        sdk.EventHubProducerClient = Mock()
        context = MagicMock()
        sdk.EventHubProducerClient.from_connection_string.return_value = context
        producer = context.__enter__.return_value
        sent = []
        producer.create_batch.return_value.add.side_effect = sent.append
        errors = types.ModuleType("azure.core.exceptions")
        errors.AzureError = type("AzureError", (Exception,), {})
        with patch.dict(sys.modules, {
            "azure": types.ModuleType("azure"), "azure.core": types.ModuleType("azure.core"),
            "azure.eventhub": sdk, "azure.core.exceptions": errors,
        }):
            target.publish_payments(batch)
        self.assertEqual(sent, batch["inputs"])
        self.assertGreater(len(sent), len(batch["expected"]))
        sdk.EventHubProducerClient.from_connection_string.assert_called_once_with(
            "synthetic-endpoint", eventhub_name="synthetic-input", retry_total=0,
        )
        self.assertNotIn("synthetic-endpoint", target.state_path.read_text())
        self.assertEqual(batch["status"], "inputs_submitted_consumers_unverified")

    def udf(self):
        functions = types.ModuleType("fabric.functions")
        functions.FabricBusinessEventsClient = object
        registration = Mock()
        registration.connection.side_effect = lambda **kwargs: lambda function: function
        registration.function.side_effect = lambda: lambda function: function
        functions.UserDataFunctions = Mock(return_value=registration)
        package = types.ModuleType("fabric")
        package.functions = functions
        source = native.decoded(native.definition("publisher", self.config), "function_app.py")
        namespace = {}
        with patch.dict(sys.modules, {"fabric": package, "fabric.functions": functions}):
            exec(compile(source, "native_function_app.py", "exec"), namespace)
        return namespace["evaluate_discount_request"], registration

    def test_discount_native_policy_boundaries_and_precedence(self):
        if not self.spec["manual_eventstream"]:
            self.skipTest("UDF publisher only")
        function, registration = self.udf()
        registration.connection.assert_called_once_with(
            argName="businessEventsClient", alias=self.config["resource_names"]["schema"],
        )
        parameters = json.loads(native.decoded(
            native.definition("publisher", self.config), "resources/functions.json",
        ))["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"]
        self.assertEqual(set(inspect.signature(function).parameters) - {"businessEventsClient"}, {p["name"] for p in parameters})
        for case in json.loads((ROOT / "data" / "test-cases.json").read_text()):
            client = Mock()
            args = {target: case["input"][source] for source, target in self.spec["parameter_map"].items()}
            result = function(client, **args)
            if case["expected"] is None:
                self.assertEqual(result["approval_level"], "none")
                self.assertIs(result["event_published"], False)
                client.PublishEvent.assert_not_called()
            else:
                self.assertEqual(result["approval_level"], case["expected"]["approval_level"])
                client.PublishEvent.assert_called_once_with(
                    type=self.config["event_name"], event_data={**case["input"], **case["expected"]}, data_version="v1",
                )

    def test_discount_invalid_inputs_never_publish(self):
        if not self.spec["manual_eventstream"]:
            self.skipTest("UDF publisher only")
        function, _ = self.udf()
        base = {
            "dealId": "synthetic", "accountName": "Synthetic Account", "region": "NA",
            "dealValue": 1000.0, "requestedDiscountPercent": 12.0,
            "estimatedMarginPercent": 25.0, "eventTime": "2026-01-01T00:00:00Z",
        }
        for field, value in (
            ("dealValue", True), ("dealValue", 0), ("requestedDiscountPercent", math.nan),
            ("estimatedMarginPercent", math.inf), ("requestedDiscountPercent", 101),
            ("accountName", " "), ("eventTime", "2026-01-01T00:00:00"),
            ("eventTime", "2026-01-01T00:00:00+02:00"),
        ):
            client = Mock()
            with self.assertRaises(ValueError):
                function(client, **{**base, field: value})
            client.PublishEvent.assert_not_called()

    def test_discount_test_invokes_every_proposal_and_saves_intent_before_calls(self):
        if not self.spec["manual_eventstream"]:
            self.skipTest("UDF invocation only")
        target, api = self.target()
        target.state["items"]["publisher"] = str(uuid.uuid4())
        batch = target.make_batch()
        target.state["last_run"] = batch
        target.save()
        calls = []

        def invoke(method, path, body):
            saved = json.loads(target.state_path.read_text())["last_run"]
            self.assertEqual(saved["invocations"][-1]["status"], "invocation_started")
            self.assertEqual(method, "POST")
            self.assertTrue(path.endswith("/functions/evaluate_discount_request/invoke"))
            self.assertEqual(set(body), set(self.spec["parameter_map"].values()))
            calls.append(body)
            expected = next((row for row in batch["expected"] if row["deal_id"] == body["dealId"]), None)
            return Reply(200, {}, {"status": "Succeeded", "output": json.dumps({
                "approval_level": expected["approval_level"] if expected else "none",
                "event_published": expected is not None,
            })})

        api.request = invoke
        target.publish_discounts(batch)
        self.assertEqual(len(calls), len(batch["inputs"]))
        self.assertGreater(len(calls), len(batch["expected"]))
        self.assertTrue(all(attempt["status"] == "succeeded" for attempt in batch["invocations"]))
        self.assertEqual(batch["status"], "inputs_submitted_consumers_unverified")

    def test_discount_portal_attestation_is_explicit_and_never_api_verified(self):
        if not self.spec["manual_eventstream"]:
            self.skipTest("Manual consumer applies only to discount")
        target, api = self.target()
        target.state.update(status="manual_eventstream_subscription_required", items={"schema": str(uuid.uuid4())})
        identifier = str(uuid.uuid4())
        api.inventory.append({
            "id": identifier, "type": "Eventstream", "displayName": self.config["resource_names"]["eventstream"],
        })
        target.acknowledge_eventstream(identifier)
        self.assertEqual(target.state["items"]["eventstream"], identifier)
        self.assertEqual(target.state["manual_source_attestation"]["evidence"], "operator_attested_portal_configuration_not_api_verified")
        self.assertTrue(all(method == "GET" for method, _, _ in api.calls))


if __name__ == "__main__":
    unittest.main()
