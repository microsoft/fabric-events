import copy
import contextlib
import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import uuid
import zipfile

from test_public_cicd import FakePublicApi, ROOT, TENANT, WORKSPACE
import failure_deploy
from failure_records import FIELDS, validate_event
import public_api
import public_bundle
import public_deploy
from public_consumers import definition_part

FOLDER = ROOT / "cicd" / "data-pipeline-failure-follow-up"


class FailureApi(FakePublicApi):
    def __init__(self):
        super().__init__()
        self.source_requests = []
        self.job_status = "InProgress"
        self.summary = None
        self.ambiguous = False

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.definitions[path.split("/")[-2]] = copy.deepcopy(body["definition"])
            return public_api.Reply(200, {}, None)
        if method == "POST" and "/jobs/execute/instances" in path:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.source_requests.append(copy.deepcopy(body))
            if self.ambiguous:
                raise RuntimeError("Ambiguous Notebook submission.")
            self.notebook = path.split("/")[-4]
            self.job_id = str(uuid.uuid4())
            return public_api.Reply(202, {"location": public_api.FABRIC + "/v1/workspaces/" + WORKSPACE
                                         + "/items/" + self.notebook + "/jobs/instances/" + self.job_id}, None)
        if method == "GET" and "/jobs/" in path:
            result = {"id": self.job_id, "itemId": self.notebook, "status": self.job_status}
            if self.summary is not None:
                result["exitValue"] = json.dumps(self.summary)
            return public_api.Reply(200, {}, result)
        reply = super()._request(method, url, resource, body, headers)
        if method == "GET" and "/sqlDatabases/" in path:
            reply.body["properties"] = {
                "serverFqdn": "test.database.fabric.microsoft.com,1433",
                "databaseName": "DataOpsCases-" + reply.body["id"],
            }
        return reply


def load_worker(source):
    fabric, functions = ModuleType("fabric"), ModuleType("fabric.functions")
    fabric.functions = functions
    functions.UserDataFunctions = lambda: SimpleNamespace(
        function=lambda: lambda function: function, connection=lambda **kwargs: lambda function: function,
    )
    functions.FabricSqlConnection = object
    namespace = {}
    with patch.dict(sys.modules, {"fabric": fabric, "fabric.functions": functions}):
        exec(compile(source, "response-function.py", "exec"), namespace)
    return namespace[failure_deploy.FUNCTION]


class FailurePublicTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = public_deploy.configuration(FOLDER, FOLDER / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
        self.api = FailureApi()
        self.target = failure_deploy.FailureDeployment(
            FOLDER, self.config, Path(temporary.name) / "ledger.json", self.api,
        )

    def publisher(self):
        namespace = {"__name__": "offline_source"}
        exec(compile(self.target.source("notebook-content.py"), "notebook-content.py", "exec"), namespace)
        return namespace

    def test_workload_failure_publishes_actual_partial_progress_and_reraises(self):
        source = self.publisher()
        events = []
        def fail(progress):
            progress["rows_processed"] = 7
            raise RuntimeError("Original workload failure")
        with self.assertLogs(level="ERROR"), self.assertRaisesRegex(RuntimeError, "Original workload failure"):
            source["execute_workload"](fail, events.append, "notebook-1", "Daily load", "run-1", "schedule")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "failed")
        self.assertEqual(events[0]["rows_processed"], 7)
        self.assertEqual(validate_event(events[0]), events[0])

    def test_zero_rows_is_not_a_failure_and_skip_is_explicit(self):
        source = self.publisher()
        events = []
        source["execute_workload"](lambda progress: None, events.append, "nb", "Load", "empty", "manual")
        def skip(progress):
            raise source["SkippedWorkload"]()
        source["execute_workload"](skip, events.append, "nb", "Load", "skipped", "manual")
        self.assertEqual([row["status"] for row in events], ["completed", "skipped"])
        self.assertEqual([row["rows_processed"] for row in events], [0, 0])
        with self.assertRaisesRegex(RuntimeError, "Publication failed"):
            source["execute_workload"](
                lambda progress: None, Mock(side_effect=RuntimeError("Publication failed")),
                "nb", "Load", "run", "manual",
            )

    def test_native_deploy_is_idle_and_binds_failed_filter_all_fields_and_exact_sql_database(self):
        result = self.target.deploy()
        self.assertEqual(len(self.api.items), 5)
        self.assertEqual(self.api.source_requests, [])
        self.assertFalse(result["e2e_verified"])
        self.assertEqual(result["manual_setup_required"], ["run_sql_setup_scripts"])
        entities = self.target.activator_entities()
        source = next(row for row in entities if row["type"] == "realTimeHubSource-v1")
        self.assertEqual(source["payload"]["filterSettings"]["filters"], [
            {"operatorType": "StringIn", "key": "data.status", "values": ["failed"]},
        ])
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        instance = json.loads(rule["payload"]["definition"]["instance"])
        self.assertEqual(instance["steps"][1]["rows"][0]["kind"], "OnEveryValue")
        parameters = next(row["values"] for row in instance["steps"][-1]["rows"][0]["arguments"]
                          if row["name"] == "parameters")
        self.assertEqual(len(parameters), 8)
        declaration = json.loads(definition_part(self.target.response_definition(), "definition.json"))
        self.assertEqual(declaration["connectedDataSources"], [{
            "alias": "FailureCases", "artifactId": self.target.state["items"]["case_store"],
            "artifactType": "SqlDbNative", "workspaceId": WORKSPACE,
        }])
        self.assertFalse(any("/invoke" in call[1] for call in self.api.calls))

    def test_source_job_is_accepted_not_e2e_and_ambiguous_submissions_are_never_replayed(self):
        self.target.deploy()
        self.api.ambiguous = True
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "already has a source attempt"):
            self.target.test()
        self.assertEqual(len(self.api.source_requests), 1)

    def test_cli_dispatches_the_renamed_scenario_to_its_original_source(self):
        self.target.deploy()
        config = self.target.path.parent / "config.local.json"
        public_deploy.save(config, self.config)
        with patch.object(failure_deploy, "FailureDeployment", return_value=self.target) as deployment:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = public_deploy.main(FOLDER, [
                    "test", "--config", str(config), "--state-file", str(self.target.path),
                    "--confirm-workspace", WORKSPACE,
                ])
        self.assertEqual(code, 0)
        deployment.assert_called_once_with(FOLDER, self.config, self.target.path)
        self.assertEqual(len(self.api.source_requests), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["scenario_name"], "Data Pipeline Failure Follow-up")
        self.assertTrue(result["source_triggered"])
        self.assertFalse(result["e2e_verified"])

    def test_real_notebook_source_publishes_six_outcomes_and_summary_readback_requires_exact_facts(self):
        self.target.deploy()
        triggered = self.target.test()
        self.assertFalse(triggered["source_execution_verified"])
        self.assertFalse(triggered["e2e_verified"])
        source = self.publisher()
        events, summaries = [], []
        notebookutils = ModuleType("notebookutils")
        notebookutils.businessEvents = SimpleNamespace(publish=lambda **kwargs: events.append(kwargs) or True)
        notebookutils.notebook = SimpleNamespace(exit=lambda value: summaries.append(json.loads(value)))
        attempt = self.target.state["test_run"]
        with patch.dict(sys.modules, {"notebookutils": notebookutils}), self.assertLogs(level="ERROR"):
            source["run_scenario"](attempt["parameters"], attempt["fixtures"])
        self.assertEqual(len(events), 6)
        self.assertEqual([row["eventData"]["status"] for row in events],
                         ["failed", "failed", "completed", "completed", "completed", "skipped"])
        self.assertEqual(summaries[0]["expected_cases"], 2)
        self.api.summary, self.api.job_status = summaries[0], "Completed"
        verified = self.target.verify()
        self.assertTrue(verified["source_execution_verified"])
        self.assertFalse(verified["e2e_verified"])
        self.api.summary["payloads"][0]["rows_processed"] += 1
        with self.assertRaisesRegex(ValueError, "actual workload facts"):
            self.target.verify()

    def test_native_worker_binds_values_and_preserves_returned_case_lifecycle_without_hiding_sql_errors(self):
        self.target.deploy()
        worker = load_worker(self.target.source("response-function.py"))
        event = {
            "pipeline_id": "nb'; DROP TABLE ignored", "pipeline_name": "Daily load", "run_id": "run-1",
            "status": "failed", "rows_processed": 0, "duration_seconds": 0,
            "triggered_by": "schedule", "completed_at": "2026-01-01T10:00:00Z",
        }
        stored = {**event, "case_id": str(uuid.uuid4()), "case_status": "in_progress",
                  "created_at": datetime.datetime(2026, 1, 1, 10, 0, 1)}
        cursor = Mock()
        cursor.description = [(key,) for key in stored]
        cursor.fetchone.return_value = list(stored.values())
        connection = Mock()
        connection.cursor.return_value = cursor
        database = SimpleNamespace(connect=Mock(return_value=connection))
        arguments = {name: event[field] for name, (field, _) in failure_deploy.PARAMETERS.items()}
        result = worker(database, **arguments)
        self.assertEqual(result["case_status"], "in_progress")
        query, *bound = cursor.execute.call_args.args
        self.assertNotIn(event["pipeline_id"], query)
        self.assertEqual(bound, [event[name] for name in FIELDS])
        self.assertTrue(connection.autocommit)
        connection.close.assert_called_once()
        cursor.execute.side_effect = RuntimeError("SQL rejected conflicting facts")
        with self.assertRaisesRegex(RuntimeError, "SQL rejected"):
            worker(database, **arguments)
        database.connect.reset_mock()
        for status in ("completed", "skipped"):
            with self.assertRaisesRegex(ValueError, "Only failed"):
                worker(database, **dict(arguments, status=status))
        database.connect.assert_not_called()

    def test_validation_rejects_non_utc_boolean_and_truncating_inputs(self):
        event = {
            "pipeline_id": "nb", "pipeline_name": "Load", "run_id": "run", "status": "failed",
            "rows_processed": 1, "duration_seconds": 0, "triggered_by": "manual",
            "completed_at": "2026-01-01T10:00:00Z",
        }
        for updates in (
            {"rows_processed": True}, {"duration_seconds": -1},
            {"completed_at": "2026-01-01T10:00:00"}, {"completed_at": "2026-01-01T10:00:00+01:00"},
            {"pipeline_id": "a" * 129}, {"pipeline_id": "\U0001f600" * 65},
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_event(dict(event, **updates))

    def test_extracted_package_plans_without_checkout_imports_or_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = public_bundle.bundle(FOLDER, Path(directory) / "scenario.zip")
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertFalse(any(".local." in name or ".deployments" in name for name in archive.namelist()))
                archive.extractall(Path(directory) / "extracted")
            extracted = Path(directory) / "extracted" / "cicd" / FOLDER.name
            result = subprocess.run(
                [sys.executable, str(extracted / "deploy.py"), "plan"], cwd=directory,
                capture_output=True, text=True, check=True,
            )
            plan = json.loads(result.stdout)
            self.assertEqual(set(plan["implemented_resources"]), set(self.config["resource_names"]))
            self.assertFalse(plan["automated_e2e_test_available"])
            self.assertFalse(plan["cloud_writes"])


if __name__ == "__main__":
    unittest.main()
