import contextlib
import copy
import inspect
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import textwrap
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from test_public_cicd import FakePublicApi, ROOT, TENANT, WORKSPACE
import cart_deploy
import public_api
import public_deploy
from public_consumers import definition_part, part


def load_publisher(source):
    functions = ModuleType("fabric.functions")

    def decorate(function):
        if any("_" in name for name in inspect.signature(function).parameters):
            raise ValueError("Fabric UDF parameter names cannot contain underscores.")
        return function

    functions.UserDataFunctions = lambda: SimpleNamespace(
        function=lambda: decorate, connection=lambda **kwargs: lambda function: function,
    )
    functions.FabricBusinessEventsClient = object
    fabric = ModuleType("fabric")
    fabric.functions = functions
    namespace = {}
    with patch.dict(sys.modules, {"fabric": fabric, "fabric.functions": functions}):
        exec(compile(source, "function_app.py", "exec"), namespace)
    return namespace[cart_deploy.FUNCTION]


class CartApi(FakePublicApi):
    def __init__(self):
        super().__init__()
        self.tokens = SimpleNamespace(token=lambda resource: "offline-test-token")
        self.inputs, self.events = [], []
        self.fail_after_input = None
        self.override = None
        self.omit_metadata = False

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/invoke"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.inputs.append(copy.deepcopy(body))
            source = definition_part(self.definitions[path.split("/")[-4]], "function_app.py")
            output = load_publisher(source)(
                SimpleNamespace(PublishEvent=lambda **event: self.events.append(copy.deepcopy(event))), **body,
            )
            if self.fail_after_input == len(self.inputs):
                raise RuntimeError("Ambiguous cart publication transport.")
            response = {
                "functionName": cart_deploy.FUNCTION, "status": "Succeeded", "errors": [],
                "output": json.dumps(output), "invocationId": str(uuid.uuid4()),
            }
            if self.override:
                response.update(self.override)
            return public_api.Reply(200, {}, response)
        reply = super()._request(method, url, resource, body, headers)
        if method == "GET" and "/sqlDatabases/" in path:
            reply.body["properties"] = {
                "serverFqdn": "test.database.fabric.microsoft.com,1433",
                "databaseName": "CartFollowUp-" + reply.body["id"],
            }
        if self.omit_metadata and path.endswith("/getDefinition") and "/userDataFunctions/" in path:
            reply.body["definition"]["parts"] = [p for p in reply.body["definition"]["parts"]
                                                if p["path"] != "resources/functions.json"]
        return reply


class CartPublicTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = ROOT / "cicd" / "abandoned-cart-recovery"
        self.config = public_deploy.configuration(self.folder, self.folder / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE, test_recipient="approved@example.invalid")
        self.api = CartApi()
        self.target = cart_deploy.CartDeployment(
            self.folder, self.config, Path(temporary.name) / "ledger.json", self.api,
        )

    def test_deploy_prepares_sql_destination_without_invoking_source_or_consumers(self):
        result = self.target.deploy()
        self.assertEqual(result["status"], "manual_setup_required")
        self.assertEqual(set(result["items"]), {"schema", "publisher", "activator", "task_store"})
        self.assertEqual(self.api.inputs, [])
        self.assertFalse(result["e2e_verified"])
        self.assertFalse(result["email_delivery_verified"])
        self.assertFalse(result["sql_tasks_verified"])
        self.assertFalse(result["sql_schema_verified"])
        self.assertEqual(result["sql_destination"]["server"], "test.database.fabric.microsoft.com,1433")
        entities = self.target.activator_entities()
        self.assertEqual(len(entities), 3)
        self.assertFalse(any(row["payload"].get("definition", {}).get("type") == "Rule" for row in entities))
        source = next(row for row in entities if row["type"] == "realTimeHubSource-v1")
        self.assertEqual(source["payload"]["filterSettings"], {"filters": []})
        self.assertTrue(source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0].endswith(
            "/eventschemasets/" + result["items"]["schema"] + "/eventtypes/Ecommerce.Cart.Abandoned",
        ))
        self.target.deploy(resume=True)
        self.assertEqual(len(self.api.items), 4)

    def test_test_invokes_original_source_six_times_without_executing_or_certifying_responses(self):
        self.target.deploy()
        result = self.target.test()
        self.assertEqual(result["publication_requests"], 6)
        self.assertEqual(result["expected_emails"], 3)
        self.assertEqual(result["expected_sql_tasks"], 6)
        self.assertIsNone(result["events_published"])
        self.assertFalse(result["email_delivery_verified"])
        self.assertFalse(result["sql_tasks_verified"])
        events = [entry["event_data"] for entry in self.api.events]
        self.assertEqual(events, [row["expected_event"] for row in result["expected_carts"]])
        self.assertEqual([row["cart_value"] for row in events], [145.5, 30.01, 250.0, 30.0, 29.99, 0.0])
        self.assertEqual(len({row["cart_id"] for row in events}), 6)
        self.assertTrue(all(result["source_run_id"] in row["cart_id"] for row in events))
        self.assertTrue(all(row["customer_email"] == self.config["test_recipient"] for row in events))
        for entry in self.api.events:
            self.assertEqual(entry["type"], self.config["event_name"])
            self.assertEqual(entry["data_version"], "v1")
            self.assertEqual(set(entry["event_data"]), {field for field, _ in cart_deploy.PARAMETERS.values()})
            self.assertIs(type(entry["event_data"]["cart_value"]), float)
            self.assertIs(type(entry["event_data"]["item_count"]), int)
        for call in self.api.calls:
            self.assertNotEqual(call[-1], public_api.GRAPH)
            if call[1].endswith("/invoke"):
                self.assertEqual(call[-1], public_api.POWERBI)
                self.assertEqual(set(call[2]), set(cart_deploy.PARAMETERS))
        self.assertFalse(self.target.verify()["e2e_verified"])
        self.assertEqual(len(self.api.inputs), 6)
        with self.assertRaisesRegex(ValueError, "already has a source attempt"):
            self.target.test()

    def test_approved_mailbox_can_be_configured_after_deployment_without_rebinding_items(self):
        self.config["test_recipient"] = None
        self.target.deploy()
        before = len(self.api.calls)
        with self.assertRaisesRegex(ValueError, "approved"):
            self.target.test()
        self.assertEqual(len(self.api.calls), before)
        self.assertNotIn("test_run", self.target.state)
        config = {**self.config, "test_recipient": "approved@example.invalid"}
        resumed = cart_deploy.CartDeployment(self.folder, config, self.target.path, self.api)
        self.assertEqual(resumed.test()["publication_requests"], 6)

    def test_manual_actions_survive_readback_without_exporting_connections_or_claiming_delivery(self):
        self.target.deploy()
        identifier = self.target.state["items"]["activator"]
        entities = json.loads(definition_part(self.api.definitions[identifier], "ReflexEntities.json"))
        entities.append({"uniqueIdentifier": str(uuid.uuid4()), "type": "manual-custom-action",
                         "payload": {"connection": "PRIVATE-CONNECTION-DO-NOT-EXPORT"}})
        self.api.definitions[identifier] = {"parts": [part("ReflexEntities.json", entities)]}
        result = self.target.verify()
        self.assertEqual(result["manual_consumer_entities_observed"], 1)
        self.assertFalse(result["manual_consumer_actions_verified"])
        self.assertNotIn("PRIVATE-CONNECTION", self.target.path.read_text())
        self.assertEqual(len(json.loads(definition_part(self.api.definitions[identifier], "ReflexEntities.json"))), 4)

    def test_changed_source_binding_or_duplicate_entities_block_source_execution(self):
        self.target.deploy()
        identifier = self.target.state["items"]["activator"]
        original = copy.deepcopy(self.api.definitions[identifier])
        for mode in ("binding", "duplicate"):
            entities = json.loads(definition_part(original, "ReflexEntities.json"))
            if mode == "binding":
                entities[1]["payload"]["connection"]["eventTypeFullyQualifiedIds"] = ["different-source"]
            else:
                entities.append(entities[0])
            self.api.definitions[identifier] = {"parts": [part("ReflexEntities.json", entities)]}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("test_run", self.target.state)

    def test_ambiguous_source_invocation_is_retained_and_never_replayed(self):
        self.target.deploy()
        self.api.fail_after_input = 3
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.target.test()
        saved = public_deploy.read(self.target.path)["test_run"]
        self.assertEqual(saved["pending_input"], "large-cart")
        self.assertEqual(len(saved["invocations"]), 2)
        with self.assertRaises(ValueError):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "incomplete or ambiguous"):
            self.target.verify()
        self.assertEqual(len(self.api.inputs), 3)

    def test_success_shaped_publication_or_repeated_invocation_cannot_complete_the_run(self):
        self.target.deploy()
        self.api.override = {"invocationId": "same-invocation"}
        with self.assertRaisesRegex(RuntimeError, "repeated invocation"):
            self.target.test()
        self.assertIn("pending_input", self.target.state["test_run"])
        for override in ({"output": {"event_published": False}}, {"status": "Failed"},
                         {"invocationId": ""}, {"output": {"event_published": "true"}}):
            with self.subTest(override=override), self.assertRaises(RuntimeError):
                cart_deploy.invocation_output(public_api.Reply(200, {}, {
                    "status": "Succeeded", "errors": [], "output": {"event_published": True},
                    "invocationId": "valid-identity", **override,
                }))

    def test_invalid_facts_publish_nothing_and_timestamp_is_not_rewritten(self):
        self.target.state["items"]["schema"] = str(uuid.uuid4())
        function = load_publisher(definition_part(self.target.udf_definition(), "function_app.py"))
        row = cart_deploy.fixture_payloads(
            public_deploy.read(self.folder / "fixtures.json"), str(uuid.uuid4()),
            "2026-11-09T15:00:00Z", self.config["test_recipient"],
        )[0]["parameters"]
        for key, value in (("customerId", ""), ("cartId", " padded "), ("cartValue", True),
                           ("cartValue", float("nan")), ("cartValue", float("inf")), ("cartValue", -1),
                           ("itemCount", True), ("itemCount", 0), ("itemCount", 3.5),
                           ("itemCount", 2_147_483_648), ("customerEmail", "a;b@example.invalid"),
                           ("customerEmail", "a@example.invalid,b@example.invalid"),
                           ("lastActivityAt", "2026-11-09T15:00:00"),
                           ("lastActivityAt", "2026-11-09T15:00:00+02:00")):
            events = []
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                function(SimpleNamespace(PublishEvent=lambda **event: events.append(event)), **{**row, key: value})
            self.assertEqual(events, [])
        for recipient in ("first@example.invalid", "second+cart@example.invalid"):
            events = []
            function(SimpleNamespace(PublishEvent=lambda **event: events.append(event)),
                     **{**row, "customerEmail": recipient})
            self.assertEqual(events[0]["event_data"]["last_activity_at"], row["lastActivityAt"])
            self.assertEqual(events[0]["event_data"]["customer_email"], recipient)

    def test_workbook_reference_and_schema_match_the_packaged_original_six_field_publisher(self):
        self.target.state["items"]["schema"] = str(uuid.uuid4())
        workbook = (ROOT / "docs" / "business-events" / "scenarios" / self.folder.name / "deployment.md").read_text(
            encoding="utf-8",
        )
        manual = textwrap.dedent(re.search(
            r"(?m)^[ \t]*```python\n(.*?)^[ \t]*```", workbook, re.S,
        ).group(1)).strip()
        packaged = definition_part(self.target.udf_definition(), "function_app.py").replace(
            self.config["resource_names"]["schema"], "EcommerceCart",
        ).strip()
        self.assertEqual(manual, packaged)
        load_publisher(manual)
        schema = json.loads(re.search(r"(?m)^[ \t]*```json\n(.*?)^[ \t]*```", workbook, re.S).group(1))
        self.assertEqual(schema, public_deploy.read(self.folder / "schema.avsc"))

    def test_cli_dispatch_requires_workspace_confirmation_and_reports_manual_boundaries(self):
        config = self.target.path.parent / "config.local.json"
        public_deploy.save(config, self.config)
        plan = public_deploy.plan(self.folder, self.config)
        self.assertEqual(plan["full_deployment_status"], "ready_for_manual_handoff")
        self.assertTrue(plan["automated_source_trigger_available"])
        self.assertFalse(plan["automated_e2e_test_available"])
        self.assertEqual([gap["id"] for gap in plan["public_api_gaps"]], ["activator_power_automate_custom_actions"])
        arguments = ["--config", str(config), "--state-file", str(self.target.path)]
        with patch.object(cart_deploy, "CartDeployment", return_value=self.target), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(public_deploy.main(self.folder, ["deploy", *arguments, "--confirm-workspace", WORKSPACE]), 2)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(public_deploy.main(self.folder, ["test", *arguments]), 1)
            self.assertEqual(self.api.inputs, [])
            self.assertEqual(public_deploy.main(self.folder, ["test", *arguments, "--confirm-workspace", WORKSPACE]), 0)

    def test_multi_recipient_configuration_fails_before_authentication(self):
        path = self.target.path.parent / "invalid.local.json"
        for recipient in ("a;b@example.invalid", "a@example.invalid,b@example.invalid",
                          "Name <a@example.invalid>", "a@example.invalid\r\nBcc: other@example.invalid"):
            public_deploy.save(path, {**self.config, "test_recipient": recipient})
            with self.subTest(recipient=recipient), patch.object(public_deploy, "PublicApi") as api:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(public_deploy.main(self.folder, ["plan", "--config", str(path)]), 1)
                api.assert_not_called()

    def test_overridden_names_event_and_absent_metadata_are_honestly_read_back(self):
        self.config["resource_names"] = {
            "schema": "NewCartSchema", "publisher": "NewCartUdf", "activator": "NewCartActions",
            "task_store": "NewCartSql",
        }
        self.config["event_name"] = "Ecommerce.Cart.TestAbandoned"
        self.api.omit_metadata = True
        result = self.target.deploy()
        self.assertEqual(result["udf_metadata_readback"], "not_returned_by_public_definition")
        self.target.test()
        self.assertTrue(all(row["type"] == self.config["event_name"] for row in self.api.events))
        connection = json.loads(definition_part(self.target.udf_definition(), "definition.json"))["connectedDataSources"][0]
        self.assertEqual(connection["alias"], "NewCartSchema")
        self.assertEqual(connection["artifactId"], self.target.state["items"]["schema"])

    def test_sql_readback_rejects_another_database_or_nonpublic_destination_before_source_execution(self):
        self.target.deploy()
        original = self.api._request
        for bad in ({"id": str(uuid.uuid4())},
                    {"properties": None},
                    {"properties": {"serverFqdn": "private.example", "databaseName": "other"}},
                    {"properties": {"serverFqdn": "test.database.fabric.microsoft.com.attacker.invalid",
                                    "databaseName": "other"}}):
            def request(*arguments):
                reply = original(*arguments)
                if arguments[0] == "GET" and "/sqlDatabases/" in arguments[1]:
                    reply.body.update(bad)
                return reply
            with self.subTest(bad=bad), patch.object(self.api, "_request", side_effect=request):
                with self.assertRaises(ValueError):
                    self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("test_run", self.target.state)

    def test_sql_readback_uses_the_environment_specific_public_service_destination(self):
        self.target.deploy()
        original = self.api._request
        server = "test.test-database.fabric.microsoft.com,1433"
        def request(*arguments):
            reply = original(*arguments)
            if arguments[0] == "GET" and "/sqlDatabases/" in arguments[1]:
                reply.body["properties"]["serverFqdn"] = server
            return reply
        with patch.object(self.api, "_request", side_effect=request):
            self.assertEqual(self.target.verify_provisioning()["sql_destination"]["server"], server)


if __name__ == "__main__":
    unittest.main()
