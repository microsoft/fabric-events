import base64
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cicd"))
import public_api
import public_bundle
import public_deploy

SCENARIOS = (
    "sales-volume-alert", "real-time-stream-alert", "discount-approval-routing",
    "low-stock-threshold", "revenue-below-target", "fraud-response-before-fulfillment",
    "real-time-viewer-recommendations", "abandoned-cart-recovery", "data-pipeline-failure-follow-up",
)
TENANT, WORKSPACE, CAPACITY, USER = [str(uuid.uuid4()) for _ in range(4)]


class FakePublicApi(public_api.PublicApi):
    def __init__(self, asynchronous=False):
        super().__init__(TENANT)
        self.sleep = lambda seconds: None
        self.calls, self.items, self.definitions, self.operations = [], {}, {}, {}
        self.roles = [{
            "name": name, "members": {"fabricItemMembers": []}, "decisionRules": [{
                "effect": "Permit", "permission": [
                    {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
                    {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]},
                ],
            }],
        } for name in ("UnrelatedReader", "OtherPageReader")]
        self.asynchronous = asynchronous
        self.interrupt_poll = False

    def identity(self):
        return {"tenantId": TENANT, "objectId": USER, "objectType": "User"}

    def principal_name(self):
        return "tester@example.com"

    def _request(self, method, url, resource, body, headers):
        self.calls.append((method, url, copy.deepcopy(body), dict(headers), resource))
        path = urlsplit(url).path
        reply = lambda value=None, status=200, extra=None: public_api.Reply(status, extra or {}, copy.deepcopy(value))
        if path.endswith("/security/enable"):
            return reply(status=204)
        if path.endswith("/dataAccessRoles"):
            if method == "PUT":
                if headers != {"If-Match": '"roles-version"'}:
                    raise AssertionError("The complete role replacement must use its exact ETag.")
                self.roles = copy.deepcopy(body["value"])
                return reply()
            extra = {"etag": "roles-version"}
            if not urlsplit(url).query:
                return reply({"value": self.roles[:1], "continuationUri": url + "?continuationToken=page2"}, extra=extra)
            return reply({"value": self.roles[1:]}, extra=extra)
        if "/operations/" in path:
            operation = path.split("/operations/")[1].split("/")[0]
            if self.interrupt_poll:
                self.interrupt_poll = False
                raise RuntimeError("Simulated ambiguous transport interruption.")
            return reply(self.operations[operation] if path.endswith("/result") else {"status": "Succeeded"})
        if path.endswith("/getDefinition"):
            identifier = path.split("/")[-2]
            return reply({"definition": self.definitions[identifier]})
        if method == "POST":
            kind = (body["type"] if path.endswith("/items") else {
                "eventSchemaSets": "EventSchemaSet", "eventhouses": "Eventhouse",
                "kqlDatabases": "KQLDatabase", "notebooks": "Notebook", "eventstreams": "Eventstream",
                "userDataFunctions": "UserDataFunction",
                "lakehouses": "Lakehouse", "dataPipelines": "DataPipeline",
                "semanticModels": "SemanticModel", "reports": "Report",
            }[path.split("/")[-1]])
            if kind == "KQLDatabase" and "definition" in body:
                for entry in body["definition"]["parts"]:
                    if entry["path"] == "DatabaseSchema.kql":
                        script = base64.b64decode(entry["payload"]).decode("utf-8")
                        if re.search(r"(?im)^\s*\.create\s+table\s+(?:\['[^']+'\]|[A-Za-z0-9_]+)\s*\(", script):
                            raise RuntimeError("ScriptContainsUnsupportedCommand: table definitions require .create-merge table.")
            identifier = str(uuid.uuid4())
            item = {"id": identifier, "type": kind, "workspaceId": WORKSPACE,
                    "displayName": body["displayName"], "description": body["description"]}
            self.items[identifier] = item
            if "definition" in body:
                self.definitions[identifier] = copy.deepcopy(body["definition"])
            if kind == "KQLDatabase":
                properties = (body["creationPayload"] if "creationPayload" in body else json.loads(
                    base64.b64decode(next(part["payload"] for part in body["definition"]["parts"]
                                         if part["path"] == "DatabaseProperties.json"))))
                item["properties"] = {"parentEventhouseItemId": properties["parentEventhouseItemId"],
                                      "queryServiceUri": "https://test.kusto.fabric.microsoft.com"}
            if kind == "Lakehouse":
                item["properties"] = {"oneLakeFilesPath": public_api.ONELAKE + "/" + WORKSPACE + "/" + identifier + "/Files"}
            if self.asynchronous:
                operation = str(uuid.uuid4())
                self.operations[operation] = item
                return reply(status=202, extra={"location": public_api.FABRIC + "/v1/operations/" + operation,
                                               "retry-after": "0"})
            return reply(item, status=201)
        if path == "/v1/capacities":
            return reply({"value": [{"id": CAPACITY, "sku": "F16", "state": "Active"}]})
        if path == "/v1/workspaces/" + WORKSPACE:
            return reply({"id": WORKSPACE, "displayName": "Approved public test", "capacityId": CAPACITY})
        if path.endswith("/items"):
            return reply({"value": list(self.items.values())})
        identifier = path.split("/")[-1]
        item = copy.deepcopy(self.items[identifier])
        if "/eventhouses/" in path:
            item["properties"] = {"databasesItemIds": [key for key, value in self.items.items()
                                                       if value.get("properties", {}).get("parentEventhouseItemId") == identifier]}
        return reply(item)


class ActionApi(FakePublicApi):
    def __init__(self):
        super().__init__()
        self.records, self.activations, self.jobs = {}, [], []
        self.omit_records = False

    def emit_record(self, kind, payload):
        import business_records
        from public_consumers import definition_part
        import struct
        reflex_id = [identifier for identifier, item in self.items.items() if item["type"] == "Reflex"][-1]
        entities = json.loads(definition_part(self.definitions[reflex_id], "ReflexEntities.json"))
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        target = next(row for row in entities if row["type"] == "fabricItemAction-v1")["payload"]["fabricItem"]["itemId"]
        if kind == "payment":
            payload = {**payload, "amount": struct.unpack("!f", struct.pack("!f", payload["amount"]))[0]}
        run_id = str(uuid.uuid4())
        record = business_records.make_record(kind, payload, rule["uniqueIdentifier"], run_id if kind == "inventory" else None)
        store = [identifier for identifier, item in self.items.items() if item["type"] == "Lakehouse"][-1]
        name = business_records.record_contract(kind)[3]
        identifier = business_records.record_id(kind, payload)
        path = public_api.ONELAKE + "/" + WORKSPACE + "/" + store + "/Files/" + name + "/" + identifier + ".json"
        if not self.omit_records:
            if path in self.records:
                business_records.check_record(kind, self.records[path], record)
            else:
                self.records[path] = record
        activation = {
            "___id": str(uuid.uuid4()), "___time": record["created_at"], "System.RuleId": rule["uniqueIdentifier"],
            "System.ActionType": "FabricItem", "System.Action.FabricItem.WorkspaceId": WORKSPACE,
            "System.Action.FabricItem.ItemId": target,
            "System.Action.FabricItem.ItemType": "Pipeline" if kind == "inventory" else "UserDataFunctions",
            "System.Action.FabricItem.JobType": "Pipeline" if kind == "inventory" else "Execute",
            "System.Action.FabricItem.FabricItemConnectionDocumentId": next(
                row["uniqueIdentifier"] for row in entities if row["type"] == "fabricItemAction-v1"
            ),
        }
        from action_deploy import PAYMENT_PARAMETERS
        mapping = (
            [(field, field) for field in business_records.INVENTORY_FIELDS] if kind == "inventory" else
            [(name, field) for name, (field, _) in PAYMENT_PARAMETERS.items()]
        )
        for name, field in mapping:
            data_type = "Number" if field in ("amount", "current_stock", "threshold") else "String"
            activation["System.Action.FabricItem.Parameters." + name + "." + data_type] = str(payload[field])
        self.activations.append(activation)
        self.jobs.append({
            "id": run_id, "itemId": target, "jobType": "Pipeline" if kind == "inventory" else "Execute",
            "status": "Completed", "startTimeUtc": record["created_at"],
            "endTimeUtc": record["created_at"], "failureReason": None,
        })

    def _request(self, method, url, resource, body, headers):
        from urllib.parse import parse_qs
        parts = urlsplit(url)
        if parts.netloc == "onelake.dfs.fabric.microsoft.com" and not parts.path.endswith("/security/enable"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            if method == "PUT":
                return public_api.Reply(201, {}, None)
            if parts.query:
                directory = parse_qs(parts.query)["directory"][0]
                prefix = public_api.ONELAKE + "/" + WORKSPACE + "/" + directory + "/"
                return public_api.Reply(200, {}, {"paths": [
                    {"name": directory + "/" + path.removeprefix(prefix), "isDirectory": False}
                    for path in self.records if path.startswith(prefix)
                ]})
            return public_api.Reply(200, {"etag": '"record-etag"'}, copy.deepcopy(self.records[url]))
        if parts.path.endswith("/queryActivations"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            if body["query"] == ".show tables":
                rows = [{"TableName": "ruleactivations_" + uuid.UUID(row["System.RuleId"]).hex}
                        for row in self.activations[:1]]
                columns = ["TableName"]
            else:
                rows = self.activations
                columns = list(rows[0]) if rows else ["___id", "___time", "System.RuleId", "System.ActionType"]
            return public_api.Reply(200, {}, {"tables": [{
                "columns": [{"columnName": column} for column in columns],
                "rows": [[row[column] for column in columns] for row in rows],
            }]})
        if parts.path.endswith("/jobs/instances"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            return public_api.Reply(200, {}, {"value": copy.deepcopy(self.jobs)})
        return super()._request(method, url, resource, body, headers)


class PublicDeploymentTests(unittest.TestCase):
    def test_configure_uses_default_preview_consent_and_preserves_explicit_decline(self):
        for scenario in ("sales-volume-alert", "real-time-stream-alert",
                         "low-stock-threshold", "revenue-below-target", "discount-approval-routing",
                         "fraud-response-before-fulfillment", "real-time-viewer-recommendations",
                         "abandoned-cart-recovery"):
            for decline in (False, True):
                with self.subTest(scenario=scenario, decline=decline), tempfile.TemporaryDirectory() as directory:
                    folder = ROOT / "cicd" / scenario
                    directory = Path(directory)
                    arguments = [
                        "configure", "--tenant-id", TENANT, "--workspace-id", WORKSPACE,
                        "--output", str(directory / "generated.json"),
                    ]
                    if decline:
                        config = public_deploy.configuration(folder, folder / "config.card.example.json")
                        config["accept_preview_apis"] = False
                        public_deploy.save(directory / "declined.json", config)
                        arguments += ["--config", str(directory / "declined.json")]
                    with patch.object(public_deploy, "PublicApi") as api, contextlib.redirect_stdout(io.StringIO()):
                        code = public_deploy.main(folder, arguments)
                    self.assertEqual(code, 0)
                    api.assert_not_called()
                    generated = public_deploy.configuration(folder, directory / "generated.json")
                    self.assertIs(generated["accept_preview_apis"], not decline)

    def test_public_powerbi_workspace_discovery_is_read_only_scoped_and_uses_its_own_audience(self):
        address = public_api.POWERBI_API + "/v1.0/myorg/groups/" + WORKSPACE + "/datasets"
        self.assertEqual(public_api.public_url("GET", address), (address, public_api.POWERBI))
        for method, url in (
            ("POST", address), ("GET", address + "?environment=internal"),
            ("GET", address + "/" + str(uuid.uuid4())), ("GET", address.replace("/groups/" + WORKSPACE, "")),
            ("GET", address.replace("api.powerbi.com", "private.example")),
        ):
            with self.subTest(method=method, url=url), self.assertRaises(ValueError):
                public_api.public_url(method, url)

    def configured(self, scenario):
        folder = ROOT / "cicd" / scenario
        config = public_deploy.configuration(folder, folder / "config.card.example.json")
        config.update(tenant_id=TENANT, workspace_id=WORKSPACE, accept_preview_apis=True)
        if "teams_verifier" in config:
            config["teams_verifier"] = "graph"
        return folder, config

    def test_full_deployment_blocks_all_six_before_authentication_or_writes(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario), patch.object(public_deploy, "PublicApi") as api:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = public_deploy.main(ROOT / "cicd" / scenario, ["deploy"])
                self.assertEqual(code, 3)
                api.assert_not_called()
                self.assertFalse(json.loads(output.getvalue())["e2e_verified"])

    def test_sales_requires_configuration_and_preview_consent_before_authentication(self):
        folder, config = self.configured("sales-volume-alert")
        config["accept_preview_apis"] = False
        plan = public_deploy.plan(folder, config)
        review = plan["public_contract_review"]
        self.assertEqual(review["excluded_operation"]["contract_status"], "no_verified_public_contract")
        self.assertEqual(review["components"]["notebook_publisher"]["contract_status"], "documented")
        self.assertTrue(review["live_deployment_verified"])
        self.assertFalse(plan["live_deployment_verified"])
        with patch.object(public_deploy, "PublicApi") as api, patch.object(
            public_deploy, "configuration", return_value=config,
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            code = public_deploy.main(folder, ["deploy", "--confirm-workspace", WORKSPACE])
        self.assertEqual(code, 3)
        api.assert_not_called()
        actual = json.loads(output.getvalue())
        self.assertEqual(actual["public_api_gaps"], [])
        self.assertEqual(actual["full_deployment_status"], "configuration_required")
        self.assertFalse(actual["e2e_verified"])
        self.assertFalse(actual["cloud_writes"])

    def test_sales_keeps_source_containers_without_provisioning_event_history(self):
        folder, config = self.configured("sales-volume-alert")
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi()
            target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
            result = target.deploy("foundations")
            current = public_deploy.plan(folder, config)
            self.assertEqual(current["event_analysis"]["scope"], "excluded")
            self.assertFalse(current["eventhouse_usage"]["source_binding_verified"])
            self.assertFalse(result["e2e_verified"])
            self.assertEqual(set(result["items"]), {"schema", "eventhouse", "database"})
            requests = [call for call in api.calls if call[0] != "GET"]
            base = f"{public_api.FABRIC}/v1/workspaces/{WORKSPACE}/"
            expected_paths = {
                base + "eventSchemaSets",
                base + "eventhouses",
                base + "kqlDatabases",
                base + f"eventSchemaSets/{result['items']['schema']}/getDefinition",
                f"{public_api.ONELAKE}/v1.0/workspaces/{WORKSPACE}/artifacts/{result['items']['schema']}/security/enable",
                base + f"items/{result['items']['schema']}/dataAccessRoles",
            }
            self.assertEqual({call[1] for call in requests}, expected_paths)
            houses = [call for call in requests if call[1].endswith("/eventhouses")]
            self.assertEqual(len(houses), 1)
            self.assertEqual(houses[0][2]["displayName"], config["resource_names"]["eventhouse"])
            self.assertFalse(result["eventhouse_subscription_configured"])
            self.assertFalse(result["e2e_verified"])

    def test_analysis_exclusion_does_not_remove_other_scenario_gaps(self):
        for scenario in SCENARIOS:
            if scenario in ("sales-volume-alert", "real-time-stream-alert", "low-stock-threshold",
                            "real-time-viewer-recommendations"):
                continue
            with self.subTest(scenario=scenario):
                folder, config = self.configured(scenario)
                current = public_deploy.plan(folder, config)
                identifiers = {gap["id"] for gap in current["public_api_gaps"]}
                self.assertNotIn("managed_business_event_eventhouse_association", identifiers)
                self.assertEqual(current["full_deployment_status"], "ready_for_manual_handoff")
                if scenario == "revenue-below-target":
                    self.assertNotIn("manual_managed_event_history", current["unimplemented_steps"])
                    self.assertIn("manual_publisher_activation", current["unimplemented_steps"])
                    self.assertNotIn("manual_stable_publisher_identity", current["unimplemented_steps"])
                    self.assertFalse(current["automated_e2e_test_available"])
                if scenario == "discount-approval-routing":
                    self.assertIn("eventstream_business_events_source", identifiers)
                if scenario == "fraud-response-before-fulfillment":
                    self.assertEqual(identifiers, {"activator_power_automate_custom_actions"})
                    self.assertIn("manual_power_automate_actions_and_decision_rules", current["unimplemented_steps"])

    def test_foundations_for_all_six_preserve_roles_and_do_not_claim_e2e(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                folder, config = self.configured(scenario)
                api = FakePublicApi()
                previous = copy.deepcopy(api.roles)
                target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
                result = target.deploy("foundations")
                self.assertFalse(result["e2e_verified"])
                self.assertFalse(result["eventhouse_subscription_configured"])
                self.assertEqual(api.roles[:2], previous)
                creates = [call for call in api.calls if call[0] == "POST" and call[1].endswith(
                    ("eventSchemaSets", "eventhouses", "kqlDatabases"))]
                self.assertEqual(len(creates), len(set(config["resource_names"]) & public_deploy.FOUNDATIONS.keys()))
                security = next(call for call in api.calls if call[1].endswith("security/enable"))
                self.assertEqual(security[-1], public_api.STORAGE)
                self.assertNotIn("pending", public_deploy.read(target.path))

    def test_preview_consent_and_wrong_tenant_fail_before_api_requests(self):
        folder, config = self.configured(SCENARIOS[0])
        with tempfile.TemporaryDirectory() as directory:
            for preview, tenant in ((False, TENANT), (True, str(uuid.uuid4()))):
                with self.subTest(preview=preview):
                    candidate = {**config, "accept_preview_apis": preview}
                    api = FakePublicApi()
                    api.identity = lambda: {"tenantId": tenant, "objectId": USER, "objectType": "User"}
                    target = public_deploy.Deployment(folder, candidate, Path(directory) / "ledger.json", api)
                    with self.assertRaises(ValueError):
                        target.deploy("foundations")
                    self.assertEqual(api.calls, [])

    def test_async_create_resumes_its_operation_without_duplicate_creation(self):
        folder, config = self.configured(SCENARIOS[0])
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi(asynchronous=True)
            api.interrupt_poll = True
            path = Path(directory) / "ledger.json"
            target = public_deploy.Deployment(folder, config, path, api)
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                target.deploy("foundations")
            self.assertIn("location", public_deploy.read(path)["pending"])
            resumed = public_deploy.Deployment(folder, config, path, api)
            resumed.deploy("foundations", resume=True)
            creates = [call for call in api.calls if call[0] == "POST" and call[1].endswith("eventSchemaSets")]
            self.assertEqual(len(creates), 1)
            self.assertEqual(len(api.items), 3)

    def test_ambiguous_create_and_cleaned_ledgers_never_replay(self):
        folder, config = self.configured(SCENARIOS[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            api = FakePublicApi()
            target = public_deploy.Deployment(folder, config, path, api)
            target.state["pending"] = {"role": "schema", "phase": "request_started"}
            target.save()
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                target.deploy("foundations", resume=True)
            self.assertFalse(any(call[0] != "GET" for call in api.calls))
            target.state["deployment_lifecycle"] = "test_resources_deleted"
            target.save()
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "resources were deleted"):
                public_deploy.Deployment(folder, config, path, api)
            self.assertEqual(path.read_bytes(), before)

    def test_operation_id_polls_only_public_host_when_service_returns_internal_location(self):
        folder, config = self.configured(SCENARIOS[0])
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi()
            target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
            operation = str(uuid.uuid4())
            api.operations[operation] = {"verified": True}
            pending = {}
            result = target.wait(public_api.Reply(202, {
                "location": "https://internal.example.test/v1/operations/" + operation,
                "x-ms-operation-id": operation, "retry-after": "0",
            }, None), pending, need_result=True)
            self.assertEqual(result, {"verified": True})
            self.assertEqual(pending["location"], public_api.FABRIC + "/v1/operations/" + operation)
            self.assertTrue(all(urlsplit(call[1]).netloc == "api.fabric.microsoft.com" for call in api.calls))

    def test_unapproved_location_without_operation_id_fails_before_polling(self):
        folder, config = self.configured(SCENARIOS[0])
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi()
            target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
            with self.assertRaisesRegex(ValueError, "approved public API"):
                target.wait(public_api.Reply(202, {
                    "location": "https://internal.example.test/v1/operations/" + str(uuid.uuid4()),
                }, None), {}, need_result=True)
            self.assertEqual(api.calls, [])

    def test_schema_readback_accepts_documented_part_and_json_normalization_but_not_changed_fields(self):
        folder, config = self.configured(SCENARIOS[0])
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi()
            target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
            target.deploy("foundations")
            part = api.definitions[target.state["items"]["schema"]]["parts"][0]
            definition = json.loads(base64.b64decode(part["payload"]))
            version = definition["schemas"][0]["versions"][0]
            avro = json.loads(version["schema"])
            version["schema"] = json.dumps(avro, sort_keys=True, separators=(",", ":"))
            definition["description"] = "Server-owned non-semantic metadata"
            part["path"] = "EventSchemaSetV1.json"
            part["payload"] = base64.b64encode(json.dumps(definition).encode()).decode()
            target.verify_schema()
            avro["fields"][0]["type"] = "int"
            version["schema"] = json.dumps(avro)
            part["payload"] = base64.b64encode(json.dumps(definition).encode()).decode()
            with self.assertRaisesRegex(ValueError, "differs"):
                target.verify_schema()

    def test_configuration_rejects_wrong_types_and_udf_aliases(self):
        for scenario in SCENARIOS:
            folder, config = self.configured(scenario)
            for key, invalid in (("tenant_id", 123), ("resource_names", None),
                                 ("resource_prefix", []), ("configuration_version", None),
                                 ("event_name", "Bad event"), ("resource_prefix", "NUL")):
                with self.subTest(scenario=scenario, key=key), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "config.json"
                    public_deploy.save(path, {**config, key: invalid})
                    with self.assertRaises(ValueError):
                        public_deploy.configuration(folder, path)
            if public_deploy.read(folder / "scenario.json")["source_backend"] == "native_udf":
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "config.json"
                    names = {**config["resource_names"], "schema": "S" * 20}
                    public_deploy.save(path, {**config, "resource_names": names})
                    public_deploy.configuration(folder, path)
                    names["schema"] += "S"
                    public_deploy.save(path, {**config, "resource_names": names})
                    with self.assertRaisesRegex(ValueError, "20 characters"):
                        public_deploy.configuration(folder, path)

    def test_configured_chat_is_optional_for_deployment_but_validated_when_present(self):
        for scenario in ("sales-volume-alert",):
            folder, config = self.configured(scenario)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                for chat in (None, "19:approved@thread.v2"):
                    candidate = {**config, "teams_chat_id": chat}
                    public_deploy.save(path, candidate)
                    self.assertEqual(public_deploy.configuration(folder, path), candidate)
                legacy = {key: value for key, value in config.items() if key != "teams_chat_id"}
                public_deploy.save(path, legacy)
                self.assertEqual(public_deploy.configuration(folder, path), legacy)
                for invalid in ("", " ", "https://teams.example/chat", "x" * 257, True, 123):
                    with self.subTest(scenario=scenario, invalid=invalid):
                        public_deploy.save(path, {**config, "teams_chat_id": invalid})
                        with self.assertRaisesRegex(ValueError, "Teams chat ID"):
                            public_deploy.configuration(folder, path)

    def test_cli_uses_configured_chat_override_and_explicit_source_only_for_all_alerts(self):
        for scenario, implementation in (
            ("sales-volume-alert", "sales_deploy.SalesDeployment"),
        ):
            folder, config = self.configured(scenario)
            with tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                config_path, ledger = directory / "config.json", directory / "ledger.json"
                public_deploy.save(config_path, {**config, "teams_chat_id": "19:configured@thread.v2"})
                public_deploy.save(ledger, {})
                for command, flags, chat in (
                    ("test", [], "19:configured@thread.v2"),
                    ("test", ["--teams-chat-id", "19:override@thread.v2"], "19:override@thread.v2"),
                    ("test", ["--source-only"], None),
                    ("verify", [], "19:configured@thread.v2"),
                ):
                    with self.subTest(scenario=scenario, command=command, flags=flags), patch(
                        implementation,
                    ) as constructor, contextlib.redirect_stdout(io.StringIO()):
                        target = constructor.return_value
                        getattr(target, command).return_value = {
                            "e2e_verified": command == "verify" and chat is not None,
                            "source_triggered": command == "test",
                        }
                        code = public_deploy.main(folder, [
                            command, "--config", str(config_path), "--state-file", str(ledger),
                            "--confirm-workspace", WORKSPACE, *flags,
                        ])
                        self.assertEqual(code, 2 if command == "verify" and chat is None else 0)
                        getattr(target, command).assert_called_once_with(chat)

    def test_trigger_needs_no_chat_but_conflicting_legacy_source_flags_still_fail(self):
        folder, config = self.configured("sales-volume-alert")
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            ledger = Path(directory) / "ledger.json"
            public_deploy.save(config_path, {**config, "teams_chat_id": None})
            public_deploy.save(ledger, {})
            with patch("sales_deploy.SalesDeployment") as constructor, contextlib.redirect_stdout(io.StringIO()):
                constructor.return_value.test.return_value = {"source_triggered": True, "e2e_verified": False}
                code = public_deploy.main(folder, [
                    "test", "--config", str(config_path), "--state-file", str(ledger),
                    "--confirm-workspace", WORKSPACE,
                ])
                self.assertEqual(code, 0)
                constructor.return_value.test.assert_called_once_with(None)
            with patch("sales_deploy.SalesDeployment") as constructor, contextlib.redirect_stderr(io.StringIO()) as errors:
                code = public_deploy.main(folder, [
                    "test", "--config", str(config_path), "--confirm-workspace", WORKSPACE,
                    "--source-only", "--teams-chat-id", "19:approved@thread.v2",
                ])
                self.assertEqual(code, 1)
                constructor.assert_not_called()
                self.assertIn("--source-only", errors.getvalue())

    def test_cli_windows_ui_requires_no_chat_and_keeps_explicit_source_only_and_graph_override(self):
        folder, config = self.configured("sales-volume-alert")
        with tempfile.TemporaryDirectory() as directory:
            config_path, ledger = Path(directory) / "config.json", Path(directory) / "ledger.json"
            public_deploy.save(config_path, {**config, "teams_verifier": "windows_ui", "teams_chat_id": None})
            public_deploy.save(ledger, {})
            for flags, verifier, chat in (
                ([], "windows_ui", None),
                (["--source-only"], "source_only", None),
                (["--teams-chat-id", "19:approved@thread.v2"], "graph", "19:approved@thread.v2"),
            ):
                with self.subTest(flags=flags), patch(
                    "sales_deploy.SalesDeployment",
                ) as constructor, contextlib.redirect_stdout(io.StringIO()):
                    constructor.return_value.test.return_value = {"source_triggered": True, "e2e_verified": False}
                    code = public_deploy.main(folder, [
                        "test", "--config", str(config_path), "--state-file", str(ledger),
                        "--confirm-workspace", WORKSPACE, *flags,
                    ])
                    self.assertEqual(code, 0)
                    self.assertEqual(constructor.return_value.teams_verifier, verifier)
                    constructor.return_value.test.assert_called_once_with(chat)

    def test_chat_readback_configuration_does_not_rebind_existing_deployment(self):
        folder, config = self.configured("sales-volume-alert")
        original = {key: value for key, value in config.items() if key != "teams_chat_id"}
        with tempfile.TemporaryDirectory() as directory:
            ledger, api = Path(directory) / "ledger.json", FakePublicApi()
            public_deploy.Deployment(folder, original, ledger, api).save()
            for chat in (None, "19:approved@thread.v2"):
                changed = {**original, "teams_chat_id": chat, "teams_verifier": "windows_ui"}
                resumed = public_deploy.Deployment(folder, changed, ledger, api)
                self.assertEqual(resumed.state["configuration"], original)
                self.assertEqual(resumed.config, changed)
            with self.assertRaisesRegex(ValueError, "exact public-only configuration"):
                public_deploy.Deployment(folder, {
                    **changed, "teams_recipients": ["different@example.com"],
                }, ledger, api)
            self.assertEqual(api.calls, [])

    def test_configure_can_save_the_approved_chat_in_a_private_configuration(self):
        folder, _ = self.configured("sales-volume-alert")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "config.json"
            code = public_deploy.main(folder, [
                "configure", "--output", str(output), "--tenant-id", TENANT, "--workspace-id", WORKSPACE,
                "--teams-chat-id", "19:approved@thread.v2",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(public_deploy.configuration(folder, output)["teams_chat_id"], "19:approved@thread.v2")
            self.assertEqual(public_deploy.configuration(folder, output)["teams_verifier"], "graph")

    def test_missing_or_hostile_lro_identity_does_not_poll_or_replay(self):
        folder, config = self.configured(SCENARIOS[0])
        for headers in ({}, {"location": "https://example.invalid/v1/operations/" + USER}):
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as directory:
                api = FakePublicApi()
                target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
                api._request = lambda *args: public_api.Reply(202, headers, None)
                pending = {"role": "schema", "phase": "request_started"}
                target.state["pending"] = pending
                with self.assertRaises((RuntimeError, ValueError)):
                    target.wait(public_api.Reply(202, headers, None), pending, need_result=True)
                self.assertEqual(api.calls, [])

    def test_failed_and_timed_out_lro_remain_persisted_without_fetching_a_result(self):
        folder, config = self.configured(SCENARIOS[0])
        for status in ("Failed", "Running"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                api = FakePublicApi()
                target = public_deploy.Deployment(folder, config, Path(directory) / "ledger.json", api)
                pending = {"role": "schema", "phase": "request_started"}
                target.state["pending"] = pending
                calls = []
                def request(method, url, resource, body, headers):
                    calls.append(url)
                    value = {"status": status}
                    if status == "Failed":
                        value["error"] = {"errorCode": "InvalidDefinition"}
                    return public_api.Reply(200, {"retry-after": "0"}, value)
                api._request = request
                with patch.object(public_deploy.time, "monotonic", side_effect=[0, 601]):
                    with self.assertRaises((RuntimeError, TimeoutError)):
                        target.wait(public_api.Reply(202, {
                            "location": public_api.FABRIC + "/v1/operations/" + USER, "retry-after": "0",
                        }, None), pending, need_result=True)
                self.assertEqual(public_deploy.read(target.path)["pending"]["operation"]["status"], status)
                self.assertFalse(any(url.endswith("/result") for url in calls))

    def test_schema_fields_match_the_published_workbook_contracts(self):
        for scenario in SCENARIOS:
            if scenario in ("real-time-viewer-recommendations", "abandoned-cart-recovery", "data-pipeline-failure-follow-up"):
                continue
            with self.subTest(scenario=scenario):
                workbook = (ROOT / "docs" / "business-events" / "scenarios" /
                            scenario / "deployment.md").read_text(encoding="utf-8")
                schemas = [
                    json.loads(body) for body in re.findall(
                        r"(?m)^[ \t]*```json\n(.*?)^[ \t]*```[ \t]*$", workbook, re.S,
                    ) if '"fields"' in body
                ]
                if schemas:
                    self.assertEqual(len(schemas), 1)
                    expected = [
                        {key: value for key, value in field.items() if key != "doc"}
                        for field in schemas[0]["fields"]
                    ]
                else:
                    fields = re.findall(
                        r"(?m)^\s*\|\s*`(\w+)`\s*\|\s*`(string|float|double|int|long|boolean)`\s*\|",
                        workbook,
                    )
                    self.assertTrue(fields)
                    expected = [{"name": name, "type": kind} for name, kind in fields]
                actual = public_deploy.read(ROOT / "cicd" / scenario / "schema.avsc")
                self.assertEqual(actual["fields"], expected)

    def test_bundles_are_standalone_exact_and_exclude_credentials_and_archive(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(dir=ROOT) as directory:
                directory = Path(directory)
                folder = ROOT / "cicd" / scenario
                archive_path = public_bundle.bundle(folder, directory / "public-v1.zip")
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertIsNone(archive.testzip())
                    assets = public_deploy.read(folder / "scenario.json").get("distribution_assets", [])
                    self.assertEqual(len(archive.namelist()), 13 + len(assets))
                    for name in archive.namelist():
                        self.assertNotIn("cicd_old", name)
                        self.assertNotIn(".local.", name)
                        self.assertNotIn(".deployments", name)
                        source = folder / Path(name).name
                        if not source.is_file():
                            source = ROOT / "cicd" / Path(name).name
                        self.assertEqual(archive.read(name), source.read_bytes())
                    archive.extractall(directory / "extracted")
                    if scenario in ("sales-volume-alert", "real-time-stream-alert",
                                    "low-stock-threshold", "revenue-below-target", "discount-approval-routing",
                                    "fraud-response-before-fulfillment", "real-time-viewer-recommendations",
                                    "abandoned-cart-recovery"):
                        launcher = archive.read(f"cicd/{scenario}/test.bat").decode("utf-8")
                        self.assertIn('python "%~dp0deploy.py" test %*', launcher)
                script = directory / "extracted" / "cicd" / scenario / "deploy.py"
                process = subprocess.run([sys.executable, str(script), "plan"], cwd=directory,
                                         capture_output=True, text=True, check=False)
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertFalse(result["e2e_verified"])
                if scenario in ("sales-volume-alert", "real-time-stream-alert", "low-stock-threshold"):
                    self.assertEqual(result["scenario_name"], public_deploy.read(folder / "scenario.json")["scenario_name"])
                    self.assertEqual(result["scenario_id"], scenario)
                if public_deploy.read(folder / "scenario.json").get("package_policy") == "single_current_package":
                    public_bundle.bundle(folder, archive_path)
                    with zipfile.ZipFile(archive_path) as archive:
                        self.assertIsNone(archive.testzip())
                else:
                    with self.assertRaises(ValueError):
                        public_bundle.bundle(folder, archive_path)

    def test_demo_configure_omits_versions_and_existing_ledger_metadata_does_not_rebind_resources(self):
        scenarios = ("sales-volume-alert", "low-stock-threshold", "real-time-stream-alert", "revenue-below-target")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                folder, config = self.configured(scenario)
                self.assertNotIn("configuration_version", config)
                output = Path(directory) / "config.json"
                with contextlib.redirect_stdout(io.StringIO()), patch.object(public_deploy, "PublicApi") as api:
                    self.assertEqual(public_deploy.main(folder, [
                        "configure", "--tenant-id", TENANT, "--workspace-id", WORKSPACE, "--output", str(output),
                    ]), 0)
                api.assert_not_called()
                saved = public_deploy.read(output)
                self.assertNotIn("configuration_version", saved)
                for label in ("2.0", "2.1", "2.2"):
                    public_deploy.save(output, {**saved, "configuration_version": label})
                    self.assertEqual(public_deploy.configuration(folder, output), saved)
                ledger = Path(directory) / "ledger.json"
                target = public_deploy.Deployment(folder, saved, ledger, FakePublicApi())
                target.state["configuration"] = {**saved, "configuration_version": "2.1"}
                target.save()
                public_deploy.Deployment(folder, saved, ledger, target.api)
                changed = {**saved, "workspace_id": str(uuid.uuid4())}
                with self.assertRaisesRegex(ValueError, "exact public-only configuration"):
                    public_deploy.Deployment(folder, changed, ledger, target.api)

    def test_failed_demo_package_refresh_preserves_the_previous_complete_archive(self):
        for scenario in ("sales-volume-alert", "low-stock-threshold", "real-time-stream-alert", "revenue-below-target"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                folder = ROOT / "cicd" / scenario
                path = public_bundle.bundle(folder, directory / (scenario + ".zip"))
                previous = path.read_bytes()
                with patch.object(zipfile.ZipFile, "write", side_effect=OSError("Interrupted packaging")):
                    with self.assertRaisesRegex(OSError, "Interrupted packaging"):
                        public_bundle.bundle(folder, path)
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(list(directory.glob("*.tmp")), [])


class PublicTransportTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows launcher contract")
    def test_scenario_test_launchers_dispatch_test_and_preserve_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="scenario launchers ") as directory:
            directory = Path(directory)
            script = directory / "capture arguments.py"
            script.write_text(
                "import json,sys; print(json.dumps(sys.argv[1:])); raise SystemExit(23)",
                encoding="utf-8",
            )
            (directory / "python.cmd").write_text(
                f'@echo off\n"{sys.executable}" "{script}" %*\nexit /b %errorlevel%\n',
                encoding="utf-8",
            )
            environment = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ["PATH"])
            config = directory / "config.local.json"
            for scenario in ("sales-volume-alert", "real-time-stream-alert",
                             "low-stock-threshold", "revenue-below-target", "discount-approval-routing",
                             "fraud-response-before-fulfillment", "real-time-viewer-recommendations",
                             "abandoned-cart-recovery"):
                with self.subTest(scenario=scenario):
                    folder = ROOT / "cicd" / scenario
                    arguments = [
                        str(folder / "test.bat"), "--config", str(config),
                        "--confirm-workspace", WORKSPACE,
                    ]
                    command = (
                        '"' + os.environ["COMSPEC"] + '" /d /s /c "'
                        + subprocess.list2cmdline(arguments) + '"'
                    )
                    result = subprocess.run(
                        command,
                        cwd=directory, env=environment, capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout), [
                        str(folder / "deploy.py"), "test", "--config", str(config),
                        "--confirm-workspace", WORKSPACE,
                    ])

    @unittest.skipUnless(os.name == "nt", "Windows launcher contract")
    def test_windows_cli_launcher_preserves_file_paths_and_quoted_etags(self):
        with tempfile.TemporaryDirectory(prefix="public cli ") as directory:
            directory = Path(directory)
            script = directory / "echo arguments.py"
            script.write_text("import json,sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
            launcher = directory / "fake-fab.cmd"
            launcher.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
            arguments = ["fab", "api", "workspaces", "-H", 'If-Match="roles-version"',
                         "-i", str(directory / "request body.json"), "--output_format", "json"]
            with patch.object(public_api.shutil, "which", return_value=str(launcher)):
                result = public_api.cli_json(arguments)
            self.assertEqual(result, arguments[1:])

    def test_fabric_cli_uses_public_audiences_and_documented_json_files(self):
        status = {"logged_in": True, "tenant_id": TENANT, "principal_id": USER, "account": "tester@example.invalid"}
        requests = []
        def cli(arguments):
            if arguments[1:3] == ["auth", "status"]:
                return status
            requests.append(arguments)
            if "-i" in arguments:
                input_file = Path(arguments[arguments.index("-i") + 1])
                self.assertEqual(public_deploy.read(input_file), {"enableOneSecurity": True})
            return {"status_code": 204, "headers": {}, "text": "(Empty)"}
        with patch.object(public_api, "cli_json", side_effect=cli):
            api = public_api.PublicApi(TENANT, "fabric_cli")
            api.request("POST", public_api.ONELAKE + f"/v1.0/workspaces/{WORKSPACE}/artifacts/{USER}/security/enable",
                        {"enableOneSecurity": True})
            self.assertEqual(requests[0][requests[0].index("-A") + 1], "storage")
            self.assertFalse(Path(requests[0][requests[0].index("-i") + 1]).exists())
            api.request("GET", "workspaces")
            self.assertEqual(requests[1][requests[1].index("-A") + 1], "fabric")

    def test_unapproved_routes_and_hostile_polling_urls_are_rejected_before_auth(self):
        api = public_api.PublicApi(TENANT)
        api._request = unittest.mock.Mock()
        for method, route in (
            ("POST", "https://example.invalid/v1/workspaces"),
            ("DELETE", f"workspaces/{WORKSPACE}"),
            ("DELETE", f"workspaces/{WORKSPACE}/items"),
            ("DELETE", f"workspaces/{WORKSPACE}/items/{USER}?hardDelete=true"),
            ("POST", f"workspaces/{WORKSPACE}/artifacts"),
            ("GET", f"workspaces/{WORKSPACE}/items?environment=internal"),
            ("GET", public_api.FABRIC + ":443/v1/workspaces"),
            ("GET", public_api.FABRIC + "/v1/workspaces#fragment"),
        ):
            with self.subTest(route=route), self.assertRaises(ValueError):
                api.request(method, route)
        api._request.assert_not_called()

    def test_documented_cleanup_deletes_only_the_exact_public_item_route(self):
        api = public_api.PublicApi(TENANT)
        api._request = unittest.mock.Mock(return_value=public_api.Reply(200, {}, None))
        route = f"workspaces/{WORKSPACE}/items/{USER}"
        reply = api.request("DELETE", route)
        self.assertEqual(reply.status, 200)
        api._request.assert_called_once_with(
            "DELETE", public_api.FABRIC + "/v1/" + route, public_api.FABRIC, None, {},
        )

    def test_cli_failure_never_falls_back_and_error_envelopes_remain_errors(self):
        api = public_api.PublicApi(TENANT, "fabric_cli")
        with patch.object(public_api, "cli_json", side_effect=RuntimeError("CLI unavailable")):
            with self.assertRaisesRegex(RuntimeError, "CLI unavailable"):
                api.request("GET", "workspaces")
        api = public_api.PublicApi(TENANT)
        api._request = lambda *args: public_api.Reply(400, {}, {"error": "Unexpected text"})
        with self.assertRaises(public_api.PublicApiError):
            api.request("GET", "workspaces")
        api._request = lambda *args: public_api.Reply(200, {}, {"error": {"code": "Rejected"}})
        with self.assertRaises(RuntimeError):
            api.request("GET", "workspaces")

    def test_changed_pagination_collection_and_etag_are_rejected(self):
        api = public_api.PublicApi(TENANT)
        api._request = lambda *args: public_api.Reply(200, {}, {
            "value": [], "continuationUri": public_api.FABRIC + "/v1/capacities",
        })
        with self.assertRaisesRegex(ValueError, "changed"):
            api.list_all("workspaces")
        first = public_api.Reply(200, {"etag": "first"}, {
            "value": [], "continuationUri": public_api.FABRIC + "/v1/workspaces?continuationToken=next",
        })
        api._request = lambda *args: public_api.Reply(200, {"etag": "different"}, {"value": []})
        with self.assertRaisesRegex(ValueError, "changed"):
            api.list_all("workspaces", first_reply=first)

    def test_delegated_azure_identity_rejects_wrong_tenant_expiry_and_app_tokens(self):
        def token(claims):
            encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
            return {"accessToken": "header." + encoded + ".signature"}
        good = {"tid": TENANT, "oid": USER, "exp": time.time() + 3600, "scp": "Item.ReadWrite.All"}
        with patch.object(public_api, "cli_json", return_value=token(good)):
            self.assertEqual(public_api.AzureCli(TENANT).identity()["objectId"], USER)
        for claims in ({**good, "tid": str(uuid.uuid4())}, {**good, "exp": 1}, {**good, "idtyp": "app"}):
            with patch.object(public_api, "cli_json", return_value=token(claims)), self.assertRaises(ValueError):
                public_api.AzureCli(TENANT).identity()
        with patch.object(public_api, "cli_json", side_effect=[token(good), token({**good, "oid": str(uuid.uuid4())})]):
            provider = public_api.AzureCli(TENANT)
            provider.identity()
            with self.assertRaisesRegex(ValueError, "same selected delegated user"):
                provider.token(public_api.STORAGE)


if __name__ == "__main__":
    unittest.main()
