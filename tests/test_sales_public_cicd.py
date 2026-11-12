import copy
import contextlib
import datetime
import io
import json
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from test_public_cicd import FakePublicApi, ROOT, TENANT, WORKSPACE
import public_api
import public_deploy
import sales_deploy


class SalesApi(FakePublicApi):
    def __init__(self):
        super().__init__()
        self.job_id = str(uuid.uuid4())
        self.chat_id = "19:approved@thread.v2"
        self.messages = []
        self.job = None
        self.job_status = "Completed"
        self.missing_location = False
        self.notify_boundary = False
        self.internal_job_location = False
        self.ignore_rule_update = False
        self.fail_rule_start = False

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            entities = json.loads(sales_deploy.definition_part(body["definition"], "ReflexEntities.json"))
            rule = next(entry for entry in entities if entry["payload"].get("definition", {}).get("type") == "Rule")
            if self.fail_rule_start and rule["payload"]["definition"]["settings"]["shouldRun"]:
                raise RuntimeError("Rule start interrupted.")
            if not self.ignore_rule_update:
                self.definitions[path.split("/")[-2]] = copy.deepcopy(body["definition"])
            return public_api.Reply(200, {}, None)
        if resource == public_api.GRAPH:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            result = {"value": self.messages} if "/messages" in path else {
                "id": self.chat_id, "chatType": "oneOnOne",
            }
            return public_api.Reply(200, {}, copy.deepcopy(result))
        if "/jobs/execute/instances" in path or "/jobs/instances/" in path:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            if method == "POST":
                if body["executionData"] != {"compute": "Spark"} or urlsplit(url).query != "beta=false":
                    raise AssertionError("The PySpark publisher requires the released beta=false request contract.")
                parameters = {value["name"]: json.loads(value["value"]) for value in body["parameters"]}
                fixtures = parameters["fixtures_json"]
                run = str(uuid.uuid4())
                window = "2026-11-07T20:00:00Z"
                payloads = self.logic["expected_findings"](fixtures, run, window)
                summary = {"operation": "seed-and-publish", "run_id": run, "window_start": window,
                           "payloads": sorted(payloads, key=lambda row: row["store_id"]),
                           "source_store_count": len(payloads), "published_count": len(payloads),
                           "source_transaction_count": sum(row["actual_transactions"] for row in payloads),
                           "expected_teams_matches": sum(row["deviation_pct"] < -30 for row in payloads)}
                self.job = {"id": self.job_id, "itemId": path.split("/notebooks/")[1].split("/")[0],
                            "status": self.job_status, "properties": {"exitValue": json.dumps(summary)}}
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                self.messages = [{
                    "id": str(index), "createdDateTime": now, "from": {"application": {"id": "test-bot"}},
                    "body": {"content": json.dumps(row)}, "attachments": [],
                } for index, row in enumerate(payloads)
                    if row["deviation_pct"] < -30 or self.notify_boundary and row["deviation_pct"] == -30]
                self.messages.append({"id": "system", "from": None, "body": {"content": "System notice"}})
                location = f"{public_api.FABRIC}/v1/workspaces/{WORKSPACE}/items/{self.job['itemId']}/jobs/instances/{self.job_id}"
                if self.internal_job_location:
                    location = location.replace(public_api.FABRIC, "https://internal.example.test")
                return public_api.Reply(202, {} if self.missing_location else {"location": location, "retry-after": "0"}, None)
            return public_api.Reply(200, {}, copy.deepcopy(self.job))
        return super()._request(method, url, resource, body, headers)


class SalesPublicTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = ROOT / "cicd" / "sales-volume-alert"
        self.config = public_deploy.configuration(self.folder, self.folder / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE, accept_preview_apis=True, teams_verifier="graph")
        self.api = SalesApi()
        self.target = sales_deploy.SalesDeployment(
            self.folder, self.config, Path(self.directory.name) / "ledger.json", self.api,
        )
        self.api.logic = self.target.logic
        self.clock = [0]
        self.api.sleep = lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds)

    def test_provisioning_binds_real_source_definition_without_running_test_inputs(self):
        result = self.target.deploy()
        self.assertTrue(result["source_binding_verified"])
        self.assertFalse(result["e2e_verified"])
        self.assertEqual(len(self.api.items), 5)
        self.assertFalse(any("/jobs/" in call[1] for call in self.api.calls))
        definition = self.api.definitions[self.target.state["items"]["database"]]
        self.assertEqual(set(sales_deploy.source_tables(sales_deploy.definition_part(
            definition, "DatabaseSchema.kql",
        ))), {"cicd_store_baselines", "cicd_pos_transactions"})
        source = sales_deploy.definition_part(self.api.definitions[self.target.state["items"]["notebook"]], "notebook-content.py")
        parameters = {}
        exec(source.split("# CELL ********************", 1)[0], parameters)
        binding = json.loads(parameters["config_json"])
        self.assertEqual(binding["database_id"], self.target.state["items"]["database"])
        self.assertEqual(binding["schema_set_id"], self.target.state["items"]["schema"])
        entities = json.loads(sales_deploy.definition_part(
            self.api.definitions[self.target.state["items"]["activator"]], "ReflexEntities.json",
        ))
        event_source = next(entity for entity in entities if entity["type"] == "realTimeHubSource-v1")
        self.assertEqual(event_source["payload"]["filterSettings"]["filters"], [])
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        self.assertTrue(rule["payload"]["definition"]["settings"]["shouldRun"])
        instance = json.loads(rule["payload"]["definition"]["instance"])
        detector = next(step for step in instance["steps"] if step["name"] == "EventDetectStep")
        self.assertEqual(detector["rows"], [
            {"name": "EventFieldSelector", "kind": "EventField", "arguments": [
                {"name": "fieldName", "type": "string", "value": "deviation_pct"},
            ]},
            {"name": "NumberValueCondition", "kind": "NumberValueCondition", "arguments": [
                {"name": "op", "type": "string", "value": "IsLessThan"},
                {"name": "threshold", "type": "number", "value": -30},
            ]},
        ])

    def test_windows_ui_verifies_native_notebook_delivery_without_graph(self):
        self.target.deploy()
        self.target.teams_verifier = "windows_ui"
        def visible_cards(recipient):
            return [{
                "id": message["id"], "createdDateTime": message["createdDateTime"],
                "verifier": "public_windows_ui_automation", "recipient": recipient,
                "recipient_verified_from_visible_teams_profile": True,
                "actual_card_text": "This is a card message. " + " ".join(
                    f"{key}: {value}" for key, value in json.loads(message["body"]["content"]).items()
                )
                                    + " View rule in Activator Tester via Fabric Activator",
            } for message in self.api.messages if message.get("from")]
        with patch("teams_ui.messages", side_effect=visible_cards), patch.object(
            sales_deploy.time, "monotonic", side_effect=lambda: self.clock[0],
        ):
            triggered = self.target.test()
            self.assertTrue(triggered["source_triggered"])
            self.assertFalse(triggered["e2e_verified"])
            result = self.target.verify()
        self.assertTrue(result["e2e_verified"])
        self.assertEqual(result["teams_verifier"], "public_windows_ui_automation")
        self.assertEqual(len(result["teams_message_ids"]), 2)
        self.assertFalse(any(call[-1] == public_api.GRAPH for call in self.api.calls))

    def test_legacy_configuration_and_owned_ledger_keep_their_original_identity(self):
        legacy = {**self.config, "scenario_id": "sales-volume"}
        config_path = self.target.path.with_name("legacy.config.json")
        public_deploy.save(config_path, legacy)
        loaded = public_deploy.configuration(self.folder, config_path)
        self.assertEqual(loaded, legacy)
        ledger = self.target.path.with_name("legacy.ledger.json")
        target = sales_deploy.SalesDeployment(self.folder, loaded, ledger, self.api)
        target.deploy()
        original = ledger.read_bytes()
        resumed = sales_deploy.SalesDeployment(self.folder, loaded, ledger, self.api)
        self.assertEqual(ledger.read_bytes(), original)
        self.assertEqual(resumed.owner, "Managed by fabric-events/cicd public-only/sales-volume.")
        self.assertTrue(resumed.verify()["source_binding_verified"])
        stream = ROOT / "cicd" / "real-time-stream-alert"
        with self.assertRaisesRegex(ValueError, "different scenario"):
            public_deploy.configuration(stream, config_path)

    def test_changed_source_table_types_fail_readback(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["database"]]
        script = sales_deploy.definition_part(definition, "DatabaseSchema.kql")
        definition["parts"] = [entry if entry["path"] != "DatabaseSchema.kql" else sales_deploy.part(
            "DatabaseSchema.kql", script.replace("expected_transactions:int", "expected_transactions:string"),
        ) for entry in definition["parts"]]
        with self.assertRaisesRegex(ValueError, "column types"):
            self.target.verify()
        self.assertFalse(self.target.state["verification"]["e2e_verified"])

    def test_rule_start_is_explicit_verified_and_not_repeated_on_resume(self):
        self.target.deploy()
        updates = [call for call in self.api.calls if call[1].endswith("/updateDefinition")]
        self.assertEqual(len(updates), 2)
        flags = []
        for call in updates:
            entities = json.loads(sales_deploy.definition_part(call[2]["definition"], "ReflexEntities.json"))
            rule = next(entry for entry in entities if entry["payload"].get("definition", {}).get("type") == "Rule")
            flags.append(rule["payload"]["definition"]["settings"])
        self.assertEqual(flags, [
            {"shouldRun": False, "shouldApplyRuleOnUpdate": False},
            {"shouldRun": True, "shouldApplyRuleOnUpdate": False},
        ])
        self.target.deploy(resume=True)
        self.assertEqual(len([call for call in self.api.calls if call[1].endswith("/updateDefinition")]), 2)
        self.assertEqual(self.target.state["activator_start"]["phase"], "verified")
        self.assertFalse(any("/jobs/" in call[1] for call in self.api.calls))

    def test_unpersisted_rule_start_fails_before_test_publication(self):
        self.api.ignore_rule_update = True
        with self.assertRaisesRegex(ValueError, "run settings"):
            self.target.deploy()
        self.assertEqual(len(self.api.items), 5)
        self.assertNotEqual(self.target.state["activator_start"]["phase"], "verified")
        self.assertFalse(any("/jobs/" in call[1] for call in self.api.calls))

    def test_interrupted_start_resumes_the_same_stopped_rule_without_recreation(self):
        self.api.fail_rule_start = True
        with self.assertRaisesRegex(RuntimeError, "start interrupted"):
            self.target.deploy()
        owned = dict(self.target.state["items"])
        self.api.fail_rule_start = False
        self.target.deploy(resume=True)
        self.assertEqual(self.target.state["items"], owned)
        self.assertEqual(len(self.api.items), 5)
        self.assertEqual(self.target.state["activator_start"]["phase"], "verified")

    def test_publisher_collision_fails_before_any_create(self):
        self.api.items["unmanaged"] = {"id": "unmanaged", "displayName": self.config["resource_names"]["notebook"]}
        with self.assertRaisesRegex(ValueError, "collision"):
            self.target.deploy()
        self.assertFalse(any(call[0] != "GET" for call in self.api.calls))

    def test_eventhouse_default_database_name_does_not_collide_with_its_parent_type(self):
        self.target.deploy()
        identifier = str(uuid.uuid4())
        self.api.items[identifier] = {
            "id": identifier, "type": "KQLDatabase", "workspaceId": WORKSPACE,
            "displayName": self.config["resource_names"]["eventhouse"], "description": "",
            "properties": {"parentEventhouseItemId": self.target.state["items"]["eventhouse"]},
        }
        result = self.target.verify()
        self.assertFalse(result["e2e_verified"])
        self.assertNotIn(identifier, result["items"].values())

    def test_e2e_requires_source_summary_and_actual_application_messages(self):
        self.target.deploy()
        with patch.object(sales_deploy.time, "monotonic", side_effect=lambda: self.clock[0]):
            self.target.test(self.api.chat_id)
            result = self.target.verify()
        self.assertTrue(result["e2e_verified"])
        self.assertEqual(result["events_published"], 6)
        self.assertEqual(len(result["teams_message_ids"]), 2)
        self.assertFalse(result["eventhouse_subscription_configured"])
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test(self.api.chat_id)
        self.assertEqual(len([call for call in self.api.calls if call[0] == "POST" and "/jobs/" in call[1]]), 1)

    def test_ambiguous_execution_never_replays_publication(self):
        self.target.deploy()
        self.api.missing_location = True
        with self.assertRaisesRegex(RuntimeError, "Location"):
            self.target.test(self.api.chat_id)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.target.verify()
        self.assertEqual(len([call for call in self.api.calls if call[0] == "POST" and "/jobs/" in call[1]]), 1)
        self.assertFalse(self.target.state["verification"]["e2e_verified"])

    def test_source_only_stays_incomplete_and_later_verifies_the_same_job(self):
        self.target.deploy()
        with patch.object(sales_deploy.time, "monotonic", side_effect=lambda: self.clock[0]):
            result = self.target.test()
            self.assertFalse(result["e2e_verified"])
            self.assertIsNone(result["events_published"])
            self.assertEqual(result["source_job_id"], self.api.job_id)
            self.assertFalse(any(call[-1] == public_api.GRAPH for call in self.api.calls))
            result = self.target.verify(self.api.chat_id)
        self.assertTrue(result["e2e_verified"])
        self.assertEqual(len([call for call in self.api.calls if call[0] == "POST" and "/jobs/" in call[1]]), 1)

    def test_notebook_job_identity_uses_only_documented_public_readback_routes(self):
        self.target.deploy()
        self.api.internal_job_location = True
        with patch.object(sales_deploy.time, "monotonic", side_effect=lambda: self.clock[0]):
            result = self.target.test()
        self.assertFalse(result["e2e_verified"])
        self.assertTrue(self.target.state["test_run"]["location"].startswith(public_api.FABRIC))
        self.assertFalse(any("internal.example.test" in call[1] for call in self.api.calls))

    def test_failed_notebook_retains_job_evidence_without_claiming_delivery(self):
        self.target.deploy()
        self.api.job_status = "Failed"
        with patch.object(sales_deploy.time, "monotonic", side_effect=lambda: self.clock[0]):
            self.target.test(self.api.chat_id)
            with self.assertRaisesRegex(RuntimeError, "failed"):
                self.target.verify()
        self.assertEqual(self.target.state["test_run"]["job"]["status"], "Failed")
        self.assertEqual(len(self.api.items), 5)
        self.assertFalse(self.target.state["verification"]["e2e_verified"])

    def test_boundary_notification_fails_the_e2e(self):
        self.target.deploy()
        self.api.notify_boundary = True
        with patch.object(sales_deploy.time, "monotonic", side_effect=lambda: self.clock[0]):
            self.target.test(self.api.chat_id)
            with self.assertRaisesRegex(ValueError, "boundary"):
                self.target.verify()
        self.assertFalse(self.target.state["verification"]["e2e_verified"])

    def test_teams_does_not_silently_switch_auth_providers(self):
        self.target.deploy()
        self.config["auth_provider"] = "fabric_cli"
        self.target.test(self.api.chat_id)
        with self.assertRaisesRegex(ValueError, "azure_cli"):
            self.target.verify()
        self.assertIn("test_run", self.target.state)

    def test_trigger_returns_the_accepted_job_without_ui_graph_or_job_polling(self):
        self.target.deploy()
        self.target.teams_verifier = "windows_ui"
        self.api.job_status = "Running"
        before = len(self.api.calls)
        with patch("teams_ui.messages", side_effect=AssertionError("Test must not read Teams")), patch.object(
            self.target, "verify", side_effect=AssertionError("Test must not certify"),
        ):
            result = self.target.test()
        self.assertTrue(result["source_triggered"])
        self.assertFalse(result["source_execution_verified"])
        self.assertFalse(result["e2e_verified"])
        self.assertEqual(result["source_job_id"], self.api.job_id)
        calls = self.api.calls[before:]
        self.assertFalse(any(call[-1] == public_api.GRAPH for call in calls))
        self.assertFalse(any(call[0] == "GET" and "/jobs/" in call[1] for call in calls))

    def test_notebook_reads_source_before_publish_and_rejects_non_true_acceptance(self):
        namespace = self.target.logic
        fixtures = public_deploy.read(self.folder / "fixtures.json")
        original = namespace["expected_findings"]
        globals_ = namespace["run_scenario"].__globals__
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                source, published, exits, tables = [], [], [], []
                def expected(*arguments):
                    rows = original(*arguments)
                    source[:] = rows
                    return rows
                def publish(**arguments):
                    self.assertEqual(arguments["eventData"], source)
                    published.append(arguments)
                    return accepted
                def ingest(config, table, rows):
                    tables.append((table, len(list(rows))))
                module = types.SimpleNamespace(
                    businessEvents=types.SimpleNamespace(publish=publish),
                    notebook=types.SimpleNamespace(exit=exits.append),
                )
                parameters = {"operation": "seed-and-publish", "event_name": self.config["event_name"],
                              "database_id": WORKSPACE, "workspace_id": WORKSPACE, "schema_set_id": WORKSPACE}
                with patch.dict(sys.modules, notebookutils=module), patch.object(sys, "version_info", (3, 12)), patch.dict(
                    globals_, expected_findings=expected, ingest_source_rows=ingest,
                    query_rows=lambda config, query: list(source),
                ), contextlib.redirect_stdout(io.StringIO()):
                    if accepted:
                        namespace["run_scenario"](parameters, fixtures)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "publish did not return True"):
                            namespace["run_scenario"](parameters, fixtures)
                self.assertEqual(tables, [("cicd_pos_transactions", 1078), ("cicd_store_baselines", 6)])
                self.assertEqual(len(published), 1)
                self.assertEqual(len(exits), 1 if accepted else 0)


if __name__ == "__main__":
    unittest.main()
