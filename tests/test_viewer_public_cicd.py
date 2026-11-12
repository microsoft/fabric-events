import copy
import datetime
import io
import inspect
import json
from pathlib import Path
import re
import sys
import tempfile
import textwrap
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import uuid

from test_public_cicd import ActionApi, ROOT, TENANT, WORKSPACE
import action_deploy
import public_api
import public_deploy
from public_consumers import definition_part, part
import viewer_deploy
import viewer_records

FOLDER = ROOT / "cicd" / "real-time-viewer-recommendations"


class ExistsError(Exception):
    status_code = 412


def load_udf(source):
    fabric, functions = ModuleType("fabric"), ModuleType("fabric.functions")
    fabric.functions = functions
    def function_decorator(function):
        if any("_" in name for name in inspect.signature(function).parameters):
            raise ValueError("Fabric UDF input parameter names cannot contain underscores.")
        return function
    functions.UserDataFunctions = lambda: SimpleNamespace(
        function=lambda: function_decorator, connection=lambda **kwargs: lambda function: function,
    )
    functions.FabricBusinessEventsClient = functions.FabricLakehouseClient = object
    core, exceptions = ModuleType("azure.core"), ModuleType("azure.core.exceptions")
    core.MatchConditions = SimpleNamespace(IfMissing="if_missing")
    exceptions.ResourceExistsError = exceptions.ResourceModifiedError = ExistsError
    namespace = {}
    with patch.dict(sys.modules, {"fabric": fabric, "fabric.functions": functions,
                                 "azure.core": core, "azure.core.exceptions": exceptions}):
        exec(compile(source, "function_app.py", "exec"), namespace)
    return namespace


class ViewerApi(ActionApi):
    def __init__(self):
        super().__init__()
        self.tokens = SimpleNamespace(token=lambda resource: "unit-test-token")
        self.inputs, self.events = [], []
        self.failure = None
        self.failed_operations = {}

    def lakehouse(self, store):
        endpoint = public_api.ONELAKE + "/" + WORKSPACE + "/" + store + "/Files"
        def file_client(name):
            path = endpoint + "/" + name
            def create(match_condition):
                if match_condition != "if_missing":
                    raise AssertionError("Native writes must be conditional.")
                if path in self.records:
                    raise ExistsError()
                self.records[path] = None
            def append(data, offset, length):
                if (offset, length) != (0, len(data)):
                    raise AssertionError("The complete recommendation must be persisted.")
                self.records[path] = json.loads(data)
            return SimpleNamespace(
                create_file=Mock(side_effect=create), append_data=Mock(side_effect=append), flush_data=Mock(),
                download_file=lambda: SimpleNamespace(readall=lambda: json.dumps(self.records[path]).encode()),
                close=Mock(),
            )
        return SimpleNamespace(connectToFiles=lambda: SimpleNamespace(
            url=endpoint, get_file_client=file_client, close=Mock(),
        ))

    def deliver(self, type, event_data, data_version):
        self.events.append({"type": type, "data": copy.deepcopy(event_data), "version": data_version})
        reflex = next(key for key, item in self.items.items() if item["type"] == "Reflex")
        entities = json.loads(definition_part(self.definitions[reflex], "ReflexEntities.json"))
        source = next(row for row in entities if row["type"] == "realTimeHubSource-v1")
        if event_data["action"].lower() not in source["payload"]["filterSettings"]["filters"][0]["values"]:
            return
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        if not rule["payload"]["definition"]["settings"]["shouldRun"]:
            return
        arguments = {
            entry["name"]: entry
            for entry in json.loads(rule["payload"]["definition"]["instance"])["steps"][-1]["rows"][0]["arguments"]
        }
        target = arguments["itemId"]["value"]
        parameters = {}
        for parameter in arguments["parameters"]["values"]:
            parts = {entry["name"]: entry for entry in parameter["arguments"]}
            field = parts["parameterValue"]["values"][0]["arguments"][0]["value"]
            parameters[parts["parameterName"]["value"]] = event_data[field]
        definition = self.definitions[target]
        declaration = json.loads(definition_part(definition, "definition.json"))
        store = declaration["connectedDataSources"][0]["artifactId"]
        worker = load_udf(definition_part(definition, "function_app.py"))
        worker[arguments["subitemId"]["value"]](self.lakehouse(store), **parameters)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        prefix = "System.Action.FabricItem."
        activation = {
            "___id": str(uuid.uuid4()), "___time": now, "System.RuleId": rule["uniqueIdentifier"],
            "System.ActionType": "FabricItem", prefix + "WorkspaceId": WORKSPACE,
            prefix + "ItemId": target, prefix + "ItemType": "UserDataFunctions", prefix + "JobType": "Execute",
            prefix + "FabricItemConnectionDocumentId": arguments["fabricJobConnectionDocumentId"]["value"],
        }
        activation.update({prefix + "Parameters." + name + ".String": value for name, value in parameters.items()})
        self.activations.append(activation)
        self.jobs.append({"id": str(uuid.uuid4()), "itemId": target, "jobType": "Execute", "status": "Completed",
                          "startTimeUtc": now, "endTimeUtc": now, "failureReason": None})

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if method == "GET" and url in self.failed_operations:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            return public_api.Reply(200, {}, copy.deepcopy(self.failed_operations[url]))
        if path.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.definitions[path.split("/")[-2]] = copy.deepcopy(body["definition"])
            return public_api.Reply(200, {}, None)
        if path.endswith("/invoke"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            self.inputs.append(copy.deepcopy(body))
            definition = self.definitions[path.split("/")[-4]]
            publisher = load_udf(definition_part(definition, "function_app.py"))
            output = publisher[viewer_deploy.FUNCTION](SimpleNamespace(PublishEvent=self.deliver), **body)
            if self.failure:
                raise RuntimeError(self.failure)
            return public_api.Reply(200, {}, {
                "status": "Succeeded", "functionName": viewer_deploy.FUNCTION, "invocationId": str(uuid.uuid4()),
                "errors": [], "output": output,
            })
        return super()._request(method, url, resource, body, headers)


class ViewerPublicTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = public_deploy.configuration(FOLDER, FOLDER / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
        self.api = ViewerApi()
        self.target = viewer_deploy.ViewerDeployment(FOLDER, self.config, Path(directory.name) / "ledger.json", self.api)
        self.clock = [0]
        self.api.sleep = lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds)
        self.addCleanup(patch.stopall)
        patch.object(action_deploy.time, "monotonic", side_effect=lambda: self.clock[0]).start()

    def test_native_deploy_is_idle_and_exact_filter_and_five_fields_reach_real_response(self):
        self.target.deploy()
        self.assertEqual(self.api.inputs, [])
        self.assertEqual(self.api.records, {})
        self.assertEqual(len(self.api.items), 5)
        self.assertEqual(self.api.events, [])
        self.assertEqual(self.target.get_definition("publisher"), self.target.udf_definition())
        self.assertIn("/userDataFunctions/", self.api.calls[-1][1])
        entities = self.target.activator_entities()
        source = next(row for row in entities if row["type"] == "realTimeHubSource-v1")
        self.assertEqual(source["payload"]["filterSettings"], {"filters": [
            {"operatorType": "StringIn", "key": "data.action", "values": ["watch", "like"]},
        ]})
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        detector = json.loads(rule["payload"]["definition"]["instance"])["steps"][1]["rows"]
        self.assertEqual(detector, [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}])
        triggered = self.target.test()
        self.assertTrue(triggered["source_triggered"])
        self.assertFalse(triggered["e2e_verified"])
        self.assertEqual(triggered["publication_requests"], 6)
        self.assertFalse(any("/queryActivations" in row[1] for row in self.api.calls))
        self.assertEqual(len(self.api.events), 6)
        self.assertTrue(all(set(event["data"]) == set(viewer_records.FIELDS) for event in self.api.events))
        result = self.target.verify()
        self.assertTrue(result["e2e_verified"])
        self.assertEqual(result["operational_records_verified"], 3)
        self.assertEqual(result["observation_seconds"], 300)
        self.assertFalse(result["trained_model"])
        self.assertFalse(result["serving_application_updated"])
        for row in triggered["expected_interactions"]:
            event = row["event"]
            matches = [record for record in self.api.records.values()
                       if record["recommendation_id"] == viewer_records.recommendation_id(event)]
            self.assertEqual(len(matches), int(row["expected"] is not None))
            if matches:
                self.assertEqual([item["content_id"] for item in matches[0]["recommendations"]], row["expected"])
                self.assertEqual({field: matches[0][field] for field in viewer_records.FIELDS}, event)
        invokes = [call for call in self.api.calls if call[1].endswith("/invoke")]
        self.assertEqual(len(invokes), 6)
        self.assertTrue(all(call[1].endswith("/functions/" + viewer_deploy.FUNCTION + "/invoke")
                            and call[-1] == public_api.POWERBI for call in invokes))
        with self.assertRaisesRegex(ValueError, "already has a source attempt"):
            self.target.test()

    def test_changed_subscription_filter_is_rejected_without_rebinding_or_source_execution(self):
        self.target.deploy()
        identifier = self.target.state["items"]["activator"]
        definition = self.api.definitions[identifier]
        entities = json.loads(definition_part(definition, "ReflexEntities.json"))
        source = next(row for row in entities if row["type"] == "realTimeHubSource-v1")
        source["payload"]["filterSettings"]["filters"][0]["values"] = ["watch"]
        definition["parts"] = [part("ReflexEntities.json", entities)]
        calls = len(self.api.calls)
        with self.assertRaisesRegex(ValueError, "differs"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])
        self.assertFalse(any(row[1].endswith("/updateDefinition") for row in self.api.calls[calls:]))

    def test_ambiguous_source_invocation_is_persisted_before_submission_and_never_replayed(self):
        self.target.deploy()
        self.api.failure = "Ambiguous transport after publication"
        with self.assertRaisesRegex(RuntimeError, "Ambiguous transport"):
            self.target.test()
        saved = public_deploy.read(self.target.path)["test_run"]
        self.assertEqual(saved["pending_input"], "watch-science")
        self.assertEqual(len(saved["inputs"]), 6)
        self.assertEqual(len(self.api.inputs), 1)
        self.assertEqual(len(self.api.records), 1)
        with self.assertRaisesRegex(ValueError, "already has"):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "incomplete or ambiguous"):
            self.target.verify()
        self.assertEqual(len(self.api.inputs), 1)

    def test_records_alone_missing_or_failed_jobs_and_wrong_actual_parameters_never_certify_e2e(self):
        self.target.deploy()
        self.target.test()
        jobs, activations = copy.deepcopy(self.api.jobs), copy.deepcopy(self.api.activations)
        for failure in ("missing-activations", "missing-jobs", "failed-job", "changed-content"):
            self.api.jobs, self.api.activations = copy.deepcopy(jobs), copy.deepcopy(activations)
            if failure == "missing-activations":
                self.api.activations.clear()
            elif failure == "missing-jobs":
                self.api.jobs.clear()
            elif failure == "failed-job":
                self.api.jobs[0]["status"] = "Failed"
            else:
                self.api.activations[0]["System.Action.FabricItem.Parameters.contentId.String"] = "city-mystery"
            with self.subTest(failure=failure), self.assertRaises((ValueError, RuntimeError)):
                self.target.verify()
            self.assertFalse(self.target.state["verification"]["e2e_verified"])
        self.assertEqual(len(self.api.inputs), 6)

    def test_duplicate_delivery_and_out_of_order_interactions_preserve_immutable_snapshots(self):
        self.target.deploy()
        triggered = self.target.test()
        first, second = [row["event"] for row in triggered["expected_interactions"][:2]]
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertLess(viewer_records.utc_time(second["occurred_at"]), viewer_records.utc_time(first["occurred_at"]))
        saved = copy.deepcopy(self.api.records)
        self.api.deliver(self.config["event_name"], first, "v1")
        self.assertEqual(saved, self.api.records)
        catalog = copy.deepcopy(self.target.catalog)
        catalog["catalog_id"] = "changed-catalog"
        identifier = self.target.state["items"]["response_function"]
        namespace = load_udf(self.target.response_source())
        namespace["CATALOG"] = catalog
        arguments = {name: first[field] for name, (field, _) in viewer_deploy.RESPONSE_PARAMETERS.items()}
        with self.assertRaisesRegex(ValueError, "conflict"):
            namespace[viewer_deploy.RESPONSE](self.api.lakehouse(self.target.state["items"]["record_store"]), **arguments)
        self.assertEqual(saved, self.api.records)
        self.assertIn(identifier, self.api.definitions)

    def test_ranking_ties_exclusions_invalid_catalog_and_unknown_content_fail_explicitly(self):
        fixtures = public_deploy.read(FOLDER / "fixtures.json")
        event = viewer_deploy.fixture_inputs(fixtures, self.target.catalog, str(uuid.uuid4()),
                                           "2026-01-01T00:00:00Z")[1]["event"]
        record = self.target.make_outcome(event, {})
        self.assertEqual([item["content_id"] for item in record["recommendations"]],
                         ["case-notes", "hidden-case", "quiet-city"])
        for change in ({"action": "Watch"}, {"action": "click"}, {"content_id": "missing"},
                       {"occurred_at": "2026-01-01T00:00:00"}, {"viewer_id": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.target.make_outcome({**event, **change}, {})
        catalog = copy.deepcopy(self.target.catalog)
        catalog["items"][0]["popularity"] = True
        with self.assertRaises(ValueError):
            viewer_records.make_recommendations(event, catalog, self.target.state["activator_ids"]["rule"])
        corrupt = copy.deepcopy(record)
        corrupt["recommendations"][2]["shared_tag_count"] = 1.0
        with self.assertRaisesRegex(ValueError, "conflict"):
            viewer_records.check_recommendations(corrupt, record)
        self.assertNotEqual(viewer_records.recommendation_id(event),
                            viewer_records.recommendation_id({**event, "content_id": "case-notes"}))

    def test_public_custom_names_schema_and_actual_udf_parameters_are_not_hardcoded(self):
        config = copy.deepcopy(self.config)
        config["event_name"] = "Streaming.Viewer.Observed"
        config["resource_names"] = {role: "Custom" + name for role, name in config["resource_names"].items()}
        target = viewer_deploy.ViewerDeployment(FOLDER, config, self.target.path.with_name("custom.json"), self.api)
        target.deploy()
        target.test()
        self.assertEqual({row["displayName"] for row in self.api.items.values()}, set(config["resource_names"].values()))
        self.assertTrue(all(row["type"] == config["event_name"] for row in self.api.events))
        response = json.loads(definition_part(target.response_udf_definition(), "resources/functions.json"))
        parameters = response["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"]
        self.assertEqual(parameters, [{"name": name, "dataType": "str"} for name in viewer_deploy.RESPONSE_PARAMETERS])
        declaration = json.loads(definition_part(target.udf_definition(), "definition.json"))
        self.assertEqual(declaration["connectedDataSources"][0]["artifactId"], target.state["items"]["schema"])
        self.assertEqual(declaration["connectedDataSources"][0]["alias"], config["resource_names"]["schema"])

    def test_cli_dispatches_source_test_without_consumer_checks_or_teams_settings(self):
        config_path = self.target.path.with_name("config.json")
        public_deploy.save(config_path, self.config)
        self.target.save()
        with patch("viewer_deploy.ViewerDeployment") as constructor, patch("sys.stdout", new=io.StringIO()):
            constructor.return_value.test.return_value = {"source_triggered": True, "e2e_verified": False}
            result = public_deploy.main(FOLDER, [
                "test", "--config", str(config_path), "--state-file", str(self.target.path),
                "--confirm-workspace", WORKSPACE,
            ])
        self.assertEqual(result, 0)
        constructor.return_value.test.assert_called_once_with(None)
        constructor.return_value.verify.assert_not_called()

    def test_sdk_destination_storage_errors_and_incomplete_existing_snapshots_are_not_success(self):
        self.target.deploy()
        row = viewer_deploy.fixture_inputs(public_deploy.read(FOLDER / "fixtures.json"), self.target.catalog,
                                          str(uuid.uuid4()), "2026-01-01T00:00:00Z")[0]
        namespace = load_udf(self.target.response_source())
        function = namespace[viewer_deploy.RESPONSE]
        arguments = {name: row["event"][field] for name, (field, _) in viewer_deploy.RESPONSE_PARAMETERS.items()}
        store = self.target.state["items"]["record_store"]
        files = self.api.lakehouse(store).connectToFiles()
        endpoint = files.url
        files.url = endpoint.replace("onelake.dfs.fabric.microsoft.com", "private.example")
        lakehouse = SimpleNamespace(connectToFiles=lambda: files)
        with self.assertRaisesRegex(ValueError, "public OneLake"):
            function(lakehouse, **arguments)
        self.assertEqual(self.api.records, {})
        files.url = endpoint
        client = files.get_file_client("ViewerRecommendations/" + viewer_records.recommendation_id(row["event"]) + ".json")
        failure = ExistsError()
        failure.status_code = 500
        client.create_file.side_effect = failure
        files.get_file_client = lambda name: client
        with self.assertRaises(ExistsError):
            function(lakehouse, **arguments)
        client.append_data.assert_not_called()
        path = endpoint + "/ViewerRecommendations/" + viewer_records.recommendation_id(row["event"]) + ".json"
        self.api.records[path] = None
        with self.assertRaisesRegex(ValueError, "conflict"):
            function(self.api.lakehouse(store), **arguments)
        self.assertIsNone(self.api.records[path])

    def test_native_response_metadata_absence_is_explicit_but_unreviewed_parts_are_rejected(self):
        self.target.deploy()
        identifier = self.target.state["items"]["response_function"]
        original = copy.deepcopy(self.api.definitions[identifier])
        self.api.definitions[identifier]["parts"] = [
            row for row in original["parts"] if row["path"] != "resources/functions.json"
        ]
        result = self.target.verify_provisioning()
        self.assertEqual(result["response_metadata_readback"], "not_returned_by_public_definition")
        self.assertFalse(result["e2e_verified"])
        self.api.definitions[identifier] = copy.deepcopy(original)
        self.api.definitions[identifier]["parts"].append(part("json.py", "raise RuntimeError('unreviewed')"))
        with self.assertRaisesRegex(ValueError, "unreviewed parts"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_workbook_reference_code_runs_without_a_deployed_consumer_or_hidden_catalog(self):
        workbook = (ROOT / "docs" / "business-events" / "scenarios" /
                    "real-time-viewer-recommendations" / "deployment.md").read_text(encoding="utf-8")
        publisher_source, response_source = [
            textwrap.dedent(source).strip()
            for source in re.findall(r"(?m)^[ \t]*```python\n(.*?)^[ \t]*```", workbook, re.S)
        ]
        store = str(uuid.uuid4())
        response_source = response_source.replace("<workspace-id>", WORKSPACE).replace("<lakehouse-id>", store)
        response = load_udf((ROOT / "cicd" / "viewer_records.py").read_text(encoding="utf-8") + "\n" + response_source)
        publisher = load_udf(publisher_source)
        events = []
        rows = viewer_deploy.fixture_inputs(public_deploy.read(FOLDER / "fixtures.json"), self.target.catalog,
                                           str(uuid.uuid4()), "2026-01-01T00:00:00Z")
        for row in rows[:2]:
            self.assertEqual(publisher[viewer_deploy.FUNCTION](
                SimpleNamespace(PublishEvent=lambda **event: events.append(event)),
                **{name: row["event"][field] for name, (field, _) in viewer_deploy.PARAMETERS.items()},
            ), {"event_published": True})
            arguments = {name: row["event"][field] for name, (field, _) in viewer_deploy.RESPONSE_PARAMETERS.items()}
            actual = response[viewer_deploy.RESPONSE](self.api.lakehouse(store), **arguments)
            self.assertEqual([item["content_id"] for item in actual["recommendations"]], row["expected"])
            self.assertNotIn("rule_id", actual)
        self.assertEqual(self.api.items, {})
        self.assertEqual(len(events), 2)
        self.assertEqual(len(self.api.records), 2)

    def prepare_failed_publisher(self):
        self.target.preflight()
        self.target.create("schema")
        self.target.verify_schema()
        self.target.roles(create=True)
        self.target.create("record_store")
        self.target.prepare_record_store()
        self.target.create("response_function")
        address, rejected = self.target.creation_request("publisher")
        definition = rejected["definition"]
        source = definition_part(definition, "function_app.py")
        metadata = json.loads(definition_part(definition, "resources/functions.json"))
        for name, (field, _) in viewer_deploy.PARAMETERS.items():
            source = source.replace(name, field)
        for parameter in metadata["functionsMetadata"][0]["fabricProperties"]["fabricFunctionParameters"]:
            parameter["name"] = viewer_deploy.PARAMETERS[parameter["name"]][0]
        definition["parts"] = [definition["parts"][0], part("function_app.py", source),
                               part("resources/functions.json", metadata)]
        operation_id = str(uuid.uuid4())
        location = public_api.FABRIC + "/v1/operations/" + operation_id
        failure = {"status": "Failed", "error": {
            "errorCode": "MetadataValidationError", "message": "Input parameter viewer_id cannot contain an underscore.",
        }}
        self.api.failed_operations[location] = failure
        pending = {
            "role": "publisher", "phase": "request_started", "path": address, "request": rejected,
            "actual_status": 202, "operation_headers": {"x-ms-operation-id": operation_id},
            "location": location, "operation": copy.deepcopy(failure),
        }
        self.target.state["pending"] = pending
        self.target.save()
        return copy.deepcopy(pending)

    def test_explicit_resume_corrects_terminal_validation_failure_without_recreating_owned_items(self):
        rejected = self.prepare_failed_publisher()
        retained = dict(self.target.state["items"])
        self.target.deploy(resume=True)
        self.assertEqual(self.target.state["failed_creates"][0]["request"], rejected["request"])
        self.assertEqual(self.target.state["failed_creates"][0]["operation"]["error"]["errorCode"],
                         "MetadataValidationError")
        self.assertTrue(all(self.target.state["items"][role] == identifier for role, identifier in retained.items()))
        self.assertEqual(len(self.api.items), 5)
        self.assertEqual(self.api.inputs, [])
        self.assertNotIn("pending", self.target.state)
        self.target.test()
        self.assertEqual(set(self.api.inputs[0]), {"viewerId", "sessionId", "contentId", "action", "occurredAt"})
        self.assertEqual(set(self.api.events[0]["data"]),
                         {"viewer_id", "session_id", "content_id", "action", "occurred_at"})
        self.assertEqual(len(self.api.events), 6)

    def test_validation_recovery_never_retries_an_unchanged_definition_or_running_operation(self):
        rejected = self.prepare_failed_publisher()
        for problem in ("unchanged", "running", "wrong-operation", "existing-publisher"):
            self.target.state["pending"] = copy.deepcopy(rejected)
            self.api.failed_operations[rejected["location"]] = copy.deepcopy(rejected["operation"])
            if problem == "unchanged":
                self.target.state["pending"]["request"] = self.target.creation_request("publisher")[1]
            elif problem == "running":
                self.api.failed_operations[rejected["location"]] = {"status": "Running"}
            elif problem == "wrong-operation":
                self.target.state["pending"]["location"] = public_api.FABRIC + "/v1/operations/" + str(uuid.uuid4())
            else:
                identifier = str(uuid.uuid4())
                self.api.items[identifier] = {
                    "id": identifier, "type": "UserDataFunction", "displayName": "ViewerPublisher",
                    "workspaceId": WORKSPACE, "description": self.target.owner,
                }
            before = len(self.api.calls)
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                self.target.deploy(resume=True)
            self.assertFalse(any(row[0] in ("POST", "PUT", "DELETE") for row in self.api.calls[before:]))
            self.assertIn("pending", self.target.state)
            self.assertNotIn("failed_creates", self.target.state)

    def test_failed_public_operation_surfaces_native_validation_reason_and_retains_evidence(self):
        rejected = self.prepare_failed_publisher()
        with self.assertRaisesRegex(RuntimeError, "MetadataValidationError.*viewer_id"):
            self.target.wait(public_api.Reply(202, {"x-ms-operation-id":
                                                  rejected["operation_headers"]["x-ms-operation-id"],
                                                  "retry-after": "0"}, None),
                             self.target.state["pending"], need_result=True)
        self.assertEqual(public_deploy.read(self.target.path)["pending"]["operation"], rejected["operation"])


if __name__ == "__main__":
    unittest.main()
