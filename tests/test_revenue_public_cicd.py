import copy
import contextlib
import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cicd"))
from public_api import FABRIC, ONELAKE, POWERBI, POWERBI_API, PublicApiError, Reply, public_url
from public_consumers import activator_entities, definition_part, part
import public_deploy
from public_deploy import configuration, read, save
from revenue_source import RevenueSourceContract, normalize_definition
from revenue_components import RevenueComponents
from revenue_deploy import RevenueDeployment
from revenue_records import REVENUE_FIELDS
from test_public_cicd import ActionApi, TENANT, WORKSPACE


class RevenueApi(ActionApi):
    def __init__(self):
        super().__init__()
        self.asynchronous = True
        self.refreshes = []
        self.export_disabled = False
        self.normalize_source_export = False
        self.create_default_database = False
        self.interrupt_activation = False

    def _request(self, method, url, resource, body, headers):
        if method == "POST" and url.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            identifier = urlsplit(url).path.split("/")[-2]
            self.definitions[identifier] = copy.deepcopy(body["definition"])
            if self.interrupt_activation:
                self.interrupt_activation = False
                raise RuntimeError("Activation response interrupted.")
            return Reply(200, {}, None)
        if urlsplit(url).netloc == "api.powerbi.com":
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            if method == "POST":
                identifier = str(uuid.uuid4())
                self.refreshes.append({"requestId": identifier, "status": "Completed"})
                return Reply(202, {"x-ms-request-id": identifier}, None)
            return Reply(200, {}, {"value": copy.deepcopy(self.refreshes)})
        if (self.export_disabled and url.endswith("/getDefinition")
                and self.items[url.split("/")[-2]]["type"] == "Reflex"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            return Reply(400, {}, {"errorCode": "Activator_Alm_ExportNotSupported"})
        reply = super()._request(method, url, resource, body, headers)
        if self.create_default_database and method == "POST" and url.endswith("/eventhouses"):
            house = next(item for item in reversed(list(self.items.values())) if item["type"] == "Eventhouse")
            identifier = str(uuid.uuid4())
            self.items[identifier] = {
                "id": identifier, "type": "KQLDatabase", "workspaceId": WORKSPACE,
                "displayName": body["displayName"], "description": "",
                "properties": {"parentEventhouseItemId": house["id"]},
            }
        if self.normalize_source_export and "/items/" in url and url.endswith("/getDefinition"):
            if self.items[url.split("/")[-2]]["type"] == "Reflex":
                entities = json.loads(definition_part(reply.body["definition"], "ReflexEntities.json"))
                for entity in entities:
                    if entity["type"] == "kqlSource-v1":
                        for parameter in entity["payload"]["queryParameters"]:
                            parameter["value"] = datetime.datetime.fromisoformat(
                                parameter["value"].replace("Z", "+00:00"),
                            ).strftime("%m/%d/%Y %H:%M:%S")
                reply.body["definition"]["parts"] = [part("ReflexEntities.json", list(reversed(entities)))]
        return reply


class RevenueSourceTests(unittest.TestCase):
    def target(self, directory, api=None):
        folder = ROOT / "cicd" / "revenue-below-target"
        config = configuration(folder, folder / "config.card.example.json")
        config.update(tenant_id=TENANT, workspace_id=WORKSPACE, accept_preview_apis=True)
        return RevenueSourceContract(folder, config, Path(directory) / "ledger.json", api or RevenueApi())

    def test_source_routes_require_explicit_formats_and_scoped_refresh_uses_powerbi_audience(self):
        base = FABRIC + "/v1/workspaces/" + WORKSPACE + "/"
        identifier = str(uuid.uuid4())
        for collection, format_name in (("semanticModels", "TMSL"), ("reports", "PBIR")):
            address = base + collection + "/" + identifier + "/getDefinition"
            self.assertEqual(public_url("POST", address + "?format=" + format_name)[1], FABRIC)
            self.assertEqual(public_url("POST", base + collection)[1], FABRIC)
            for suffix in ("", "?format=unknown", "?format=" + format_name.lower(),
                           "?format=" + format_name + "&format=" + format_name,
                           "?format=" + format_name + "&private=true"):
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    public_url("POST", address + suffix)
        address = POWERBI_API + "/v1.0/myorg/groups/" + WORKSPACE + "/datasets/" + identifier + "/refreshes"
        for method in ("GET", "POST"):
            self.assertEqual(public_url(method, address)[1], POWERBI)
        for method, url in (("DELETE", address), ("POST", address + "?internal=true"),
                            ("GET", address + "/" + identifier),
                            ("POST", address.replace("/groups/" + WORKSPACE, ""))):
            with self.subTest(method=method, url=url), self.assertRaises(ValueError):
                public_url(method, url)

    def test_prepares_kql_source_and_shared_queries_without_claiming_publication_or_e2e(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            proof = target.prepare()
            self.assertTrue(proof["source_contract_verified"])
            self.assertFalse(proof["e2e_verified"])
            self.assertFalse(proof["business_event_publication_configured"])
            self.assertEqual(proof["source_inputs_submitted"], 0)
            self.assertEqual(set(proof["items"]), {"eventhouse", "source", "queryset", "publisher"})
            source = target.publisher_entities()[1]["payload"]
            self.assertEqual(source["eventhouseItem"]["itemId"], proof["items"]["source"])
            self.assertNotEqual(source["eventhouseItem"]["itemId"], proof["items"]["eventhouse"])
            self.assertEqual(source["eventhouseItem"]["itemType"], "KustoDatabase")
            self.assertEqual(source["eventTimeSettings"]["timeFieldName"], "observed_at")
            self.assertEqual([(row["name"], row["type"]) for row in source["queryParameters"]],
                             [("startTime", "DURATION_START"), ("endTime", "DURATION_END")])
            query = source["query"]["queryString"]
            self.assertIn("iff(target > 0", query)
            self.assertIn("real(null)", query)
            self.assertIn("pack_array(region, period_start)", query)
            self.assertNotIn("-15", query)
            queryset = json.loads(definition_part(target.get_definition("queryset"), "RealTimeQueryset.json"))["queryset"]
            self.assertEqual(queryset["tabs"][0]["content"], query)
            self.assertEqual(queryset["dataSources"][0]["databaseName"], proof["items"]["source"])
            self.assertLess(source["queryParameters"][0]["value"], source["queryParameters"][1]["value"])
            self.assertTrue(queryset["tabs"][1]["content"].startswith(query))
            self.assertIn("arg_max(observed_at", queryset["tabs"][1]["content"])
            self.assertEqual(source["metadata"]["querySetId"], proof["items"]["queryset"])
            self.assertFalse(any(item["type"] in ("SemanticModel", "Report", "KQLDashboard")
                                 for item in target.api.items.values()))
            self.assertFalse(any("api.powerbi.com" in call[1] or "/refreshes" in call[1] for call in target.api.calls))

    def test_publisher_prepares_six_typed_properties_from_one_stably_identified_source_event(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.state["items"] = {"source": str(uuid.uuid4()), "queryset": str(uuid.uuid4())}
            entities = target.publisher_entities()
            self.assertEqual(entities, target.publisher_entities())
            by_id = {entity["uniqueIdentifier"]: entity for entity in entities}
            self.assertEqual(len(by_id), len(entities))
            self.assertEqual(sum(entity["type"] == "kqlSource-v1" for entity in entities), 1)
            self.assertFalse(any(entity["payload"].get("definition", {}).get("type") == "Rule"
                                 for entity in entities))
            ids = target.state["source_ids"]
            source = by_id[ids["source"]]["payload"]
            self.assertIn("pack_array(region, period_start)", source["query"]["queryString"])
            split = json.loads(by_id[ids["split_event"]]["payload"]["definition"]["instance"])
            rows = split["steps"][0]["rows"]
            self.assertEqual(rows[0]["arguments"][0]["arguments"][0]["value"], ids["event"])
            self.assertEqual(rows[1]["arguments"][0]["value"], "Composite key")
            self.assertEqual(rows[1]["arguments"][1]["arguments"][0]["value"], ids["identity"])
            self.assertTrue(rows[2]["arguments"][0]["value"])
            step_ids = []
            for field in REVENUE_FIELDS:
                payload = by_id[ids["attribute_" + field]]["payload"]
                self.assertEqual(payload["name"], field)
                self.assertEqual(payload["parentObject"]["targetUniqueIdentifier"], ids["object"])
                instance = json.loads(payload["definition"]["instance"])
                self.assertEqual(instance["templateId"], "BasicEventAttribute")
                selection, conversion = instance["steps"]
                self.assertEqual(selection["rows"][0]["arguments"][0]["arguments"][0]["value"],
                                 ids["split_event"])
                self.assertEqual(selection["rows"][1]["arguments"],
                                 [{"name": "fieldName", "type": "string", "value": field}])
                kind = "Number" if field in ("revenue", "target", "variance_pct") else "Text"
                self.assertEqual(conversion["rows"][0]["arguments"][0]["value"], kind)

            def check_references(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in ("entityId", "targetUniqueIdentifier"):
                            self.assertIn(child, by_id)
                        elif key == "name" and child == "entityId":
                            self.assertIn(value["value"], by_id)
                        check_references(child)
                elif isinstance(value, list):
                    for child in value:
                        check_references(child)

            check_references(entities)
            for entity in entities:
                definition = entity["payload"].get("definition", {})
                if "instance" in definition:
                    instance = json.loads(definition["instance"])
                    check_references(instance)
                    step_ids.extend(step["id"] for step in instance["steps"])
            self.assertEqual(len(step_ids), len(set(step_ids)))
            for identifier in (*by_id, *step_ids):
                self.assertEqual(str(uuid.UUID(identifier)), identifier)

    def test_manual_publisher_setup_cannot_rebind_properties_or_add_another_powerbi_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            original = target.publisher_entities()
            changed = copy.deepcopy(original)
            attribute = next(entity for entity in changed
                             if entity["uniqueIdentifier"] == target.state["source_ids"]["attribute_detected_at"])
            instance = json.loads(attribute["payload"]["definition"]["instance"])
            instance["steps"][0]["rows"][1]["arguments"][0]["value"] = "System.IngestionTime"
            attribute["payload"]["definition"]["instance"] = json.dumps(instance)
            target.api.definitions[target.state["items"]["publisher"]] = {
                "parts": [part("ReflexEntities.json", changed)],
            }
            with self.assertRaisesRegex(ValueError, "source readback differs"):
                target.verify_publisher(allow_manual_rules=True)
            changed = copy.deepcopy(original)
            additional_source = copy.deepcopy(changed[1])
            additional_source["uniqueIdentifier"] = str(uuid.uuid4())
            changed.append(additional_source)
            target.api.definitions[target.state["items"]["publisher"]] = {
                "parts": [part("ReflexEntities.json", changed)],
            }
            with self.assertRaisesRegex(ValueError, "additional source"):
                target.verify_publisher(allow_manual_rules=True)

    def test_publication_rule_is_stopped_and_maps_all_six_typed_attributes_without_changing_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            original = target.publisher_entities()
            publisher_id = target.state["items"]["publisher"]
            metadata = copy.deepcopy(target.api.items[publisher_id])
            target.api.definitions[publisher_id]["parts"].append(part(".platform", {
                "metadata": {"displayName": target.config["resource_names"]["publisher"], "type": "Reflex"},
            }))
            schema_id = str(uuid.uuid4())
            actual = target.configure_publication(schema_id)
            self.assertEqual(actual[:len(original)], original)
            self.assertEqual(len(actual), 15)
            action, rule = actual[-2:]
            self.assertEqual(action["type"], "businessEventAction-v1")
            self.assertEqual(action["payload"]["item"], {"itemId": schema_id, "workspaceId": WORKSPACE})
            self.assertEqual(action["payload"]["schemaName"], "Sales.Revenue.BelowTarget")
            self.assertEqual(rule["payload"]["definition"]["settings"],
                             {"shouldRun": False, "shouldApplyRuleOnUpdate": False})
            self.assertEqual(rule["payload"]["parentObject"]["targetUniqueIdentifier"],
                             target.state["source_ids"]["object"])
            instance = json.loads(rule["payload"]["definition"]["instance"])
            self.assertEqual(instance["templateId"], "AttributeTrigger")
            select, detect, act = instance["steps"]
            self.assertEqual(select["rows"][0]["arguments"][0]["arguments"][0]["value"],
                             target.state["source_ids"]["attribute_variance_pct"])
            self.assertEqual(detect["rows"][0], {
                "name": "NumberBecomes", "kind": "NumberBecomes", "arguments": [
                    {"name": "op", "type": "string", "value": "BecomesLessThan"},
                    {"name": "value", "type": "number", "value": -15},
                ],
            })
            binding = act["rows"][0]
            self.assertEqual(binding["kind"], "BusinessEventPublication")
            arguments = {entry["name"]: entry for entry in binding["arguments"]}
            self.assertEqual(arguments["businessEventDocumentId"]["value"], action["uniqueIdentifier"])
            self.assertEqual(arguments["schemaName"]["value"], "Sales.Revenue.BelowTarget")
            self.assertEqual(arguments["schemaVersion"]["value"], "v1")
            mappings = arguments["data"]["values"]
            self.assertEqual(len(mappings), 6)
            for name, mapping in zip(REVENUE_FIELDS, mappings):
                values = {entry["name"]: entry for entry in mapping["arguments"]}
                self.assertEqual(values["parameterName"]["value"], name)
                self.assertEqual(values["parameterType"]["value"],
                                 "double" if name in ("revenue", "target", "variance_pct") else "string")
                self.assertEqual(values["parameterValue"]["values"], [{
                    "kind": "AttributeReference", "type": "complexReference", "arguments": [{
                        "name": "entityId", "type": "string",
                        "value": target.state["source_ids"]["attribute_" + name],
                    }],
                }])
            updates = [call for call in target.api.calls if call[1].endswith("/updateDefinition")]
            self.assertEqual(len(updates), 1)
            self.assertEqual([entry["path"] for entry in updates[0][2]["definition"]["parts"]], ["ReflexEntities.json"])
            self.assertEqual(target.api.items[publisher_id], metadata)
            target.configure_publication(schema_id)
            self.assertEqual(len([call for call in target.api.calls if call[1].endswith("/updateDefinition")]), 1)

    def test_publication_waits_for_accepted_operation_before_verifying_the_complete_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            operation_id = str(uuid.uuid4())
            operation_url = FABRIC + "/v1/operations/" + operation_id
            request = target.api._request

            def accepted_update(method, url, resource, body, headers):
                if method == "GET" and url == operation_url:
                    target.api.calls.append((method, url, body, headers, resource))
                    return Reply(200, {}, {"status": "Succeeded"})
                reply = request(method, url, resource, body, headers)
                if method == "POST" and url.endswith("/updateDefinition"):
                    return Reply(202, {"x-ms-operation-id": operation_id, "retry-after": "0"}, None)
                return reply

            target.api._request = accepted_update
            self.assertEqual(len(target.configure_publication(str(uuid.uuid4()))), 15)
            publication = target.state["publisher_publication"]
            self.assertEqual(publication["actual_status"], 202)
            self.assertEqual(publication["operation"]["status"], "Succeeded")
            self.assertEqual(publication["phase"], "verified")
            self.assertEqual(len([call for call in target.api.calls if call[1].endswith("/updateDefinition")]), 1)

    def test_publication_accepts_manual_activation_but_rejects_mapping_changes_and_destination_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            schema_id = str(uuid.uuid4())
            entities = target.configure_publication(schema_id)
            rule = entities[-1]
            rule["payload"]["definition"]["settings"]["shouldRun"] = True
            target.api.definitions[target.state["items"]["publisher"]] = {
                "parts": [part("ReflexEntities.json", entities)],
            }
            self.assertTrue(target.verify_publisher(allow_manual_rules=True)[-1]["payload"]["definition"]["settings"]["shouldRun"])
            before = len(target.api.calls)
            with self.assertRaisesRegex(ValueError, "cannot be rebound"):
                target.configure_publication(str(uuid.uuid4()))
            self.assertEqual(len(target.api.calls), before)
            instance = json.loads(rule["payload"]["definition"]["instance"])
            instance["steps"][-1]["rows"][0]["arguments"][-1]["values"][0]["arguments"][1]["values"] = [
                {"type": "string", "value": "West"},
            ]
            rule["payload"]["definition"]["instance"] = json.dumps(instance)
            target.api.definitions[target.state["items"]["publisher"]] = {
                "parts": [part("ReflexEntities.json", entities)],
            }
            with self.assertRaisesRegex(ValueError, "source readback differs"):
                target.verify_publisher(allow_manual_rules=True)

    def test_interrupted_publication_is_verified_without_replay_and_missing_results_remain_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            schema_id = str(uuid.uuid4())
            target.api.interrupt_activation = True
            with self.assertRaisesRegex(RuntimeError, "response interrupted"):
                target.configure_publication(schema_id)
            recovered = self.target(directory, target.api)
            recovered.configure_publication(schema_id)
            self.assertEqual(len([call for call in target.api.calls if call[1].endswith("/updateDefinition")]), 1)
            recovered.state["publisher_publication"]["phase"] = "request_started"
            recovered.api.definitions[recovered.state["items"]["publisher"]] = {
                "parts": [part("ReflexEntities.json", recovered.publisher_entities())],
            }
            with self.assertRaisesRegex(ValueError, "missing, duplicated or unexpected"):
                recovered.configure_publication(schema_id)
            self.assertEqual(len([call for call in target.api.calls if call[1].endswith("/updateDefinition")]), 1)

    def test_old_source_ledger_is_preserved_and_requires_fresh_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            target.state["revenue_public_source_version"] = 1
            target.save()
            before = target.path.read_bytes()
            calls = len(target.api.calls)
            with self.assertRaisesRegex(ValueError, "fresh deployment"):
                self.target(directory, target.api)
            self.assertEqual(target.path.read_bytes(), before)
            self.assertEqual(len(target.api.calls), calls)

    def test_preflight_only_source_ledger_can_prepare_the_missing_object_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            original_ids = {key: target.state["source_ids"][key] for key in ("container", "source", "event", "step")}
            target.state["source_ids"] = copy.deepcopy(original_ids)
            target.state["revenue_public_source_version"] = 1
            target.save()
            target = self.target(directory, target.api)
            self.assertTrue(target.prepare()["source_contract_verified"])
            self.assertEqual({key: target.state["source_ids"][key] for key in original_ids}, original_ids)
            self.assertEqual(len(target.publisher_entities()), 13)

    def test_failed_public_export_retains_import_and_refresh_evidence_without_false_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RevenueApi()
            api.export_disabled = True
            target = self.target(directory, api)
            with self.assertRaises(PublicApiError):
                target.prepare()
            recorded = read(target.path)
            self.assertEqual(len(recorded["items"]), 4)
            self.assertEqual(recorded["source_readbacks"]["publisher"]["actual_status"], 400)
            self.assertNotIn("source_contract_verification", recorded)
            self.assertFalse(any(call[0] == "DELETE" for call in api.calls))
            creates = len([call for call in api.calls if call[0] == "POST" and call[1].endswith("/items")])
            with self.assertRaises(PublicApiError):
                self.target(directory, api).prepare()
            self.assertEqual(creates, len([call for call in api.calls if call[0] == "POST" and call[1].endswith("/items")]))

    def test_changed_query_and_database_binding_are_rejected_without_replacing_source_data(self):
        self.assertEqual(normalize_definition({"expression": ["RETURN", ' "two words"']}),
                         normalize_definition({"expression": 'RETURN "two words"'}))
        self.assertNotEqual(normalize_definition({"expression": '"two words"'}),
                            normalize_definition({"expression": '"twowords"'}))
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            queryset = json.loads(definition_part(target.get_definition("queryset"), "RealTimeQueryset.json"))
            queryset["queryset"]["tabs"][0]["content"] += "\n| where variance_pct < -15"
            target.api.definitions[target.state["items"]["queryset"]] = {"parts": [part("RealTimeQueryset.json", queryset)]}
            with self.assertRaisesRegex(ValueError, "original source query"):
                target.verify_queryset()
            database = target.api.items[target.state["items"]["source"]]
            database["properties"]["parentEventhouseItemId"] = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, "exact owned Eventhouse child"):
                target.verify_source()

    def test_source_schema_changes_are_rejected_without_overwriting_the_table(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            definition = target.api.definitions[target.state["items"]["source"]]
            schema = definition_part(definition, "DatabaseSchema.kql").replace("target:real", "target:string")
            definition["parts"] = [part("DatabaseSchema.kql", schema)]
            before = len(target.api.calls)
            with self.assertRaisesRegex(ValueError, "revenue snapshot table"):
                target.verify_source()
            self.assertTrue(all(call[0] in ("GET", "POST") and not call[1].endswith("/updateDefinition")
                                for call in target.api.calls[before:]))

    def test_service_created_source_child_is_tracked_and_preserved_without_becoming_event_history(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RevenueApi()
            api.create_default_database = True
            target = self.target(directory, api)
            target.prepare()
            children = target.state["service_created_children"]
            self.assertEqual(len(children), 1)
            self.assertNotIn(target.state["items"]["source"], children)
            original = copy.deepcopy(api.items[children[0]])
            target.prepare()
            self.assertEqual(api.items[children[0]], original)
            self.assertFalse(any(call[0] == "DELETE" for call in api.calls))
            self.assertEqual(target.publisher_entities()[1]["payload"]["eventhouseItem"]["itemId"],
                             target.state["items"]["source"])

    def test_source_endpoint_rejects_private_hosts_credentials_and_redirect_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.prepare()
            database = target.api.items[target.state["items"]["source"]]
            for endpoint in ("http://test.kusto.fabric.microsoft.com", "https://private.pbidedicated.windows.net",
                             "https://user:secret@test.kusto.fabric.microsoft.com",
                             "https://test.kusto.fabric.microsoft.com/other", "https://test.kusto.fabric.microsoft.com?key=x"):
                database["properties"]["queryServiceUri"] = endpoint
                with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, "unexpected public Kusto"):
                    target.source_connection()

    def test_canonical_source_metadata_and_entity_reordering_preserve_identity_but_rebinding_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RevenueApi()
            api.normalize_source_export = True
            target = self.target(directory, api)
            self.assertTrue(target.prepare()["source_contract_verified"])
            api.normalize_source_export = False
            entities = target.publisher_entities()
            entities[1]["payload"]["eventhouseItem"]["itemId"] = str(uuid.uuid4())
            api.definitions[target.state["items"]["publisher"]] = {"parts": [part("ReflexEntities.json", entities)]}
            with self.assertRaisesRegex(ValueError, "query, database, properties or mappings"):
                target.verify_publisher()

    def test_kql_export_dates_preserve_existing_fractional_window_but_reject_changed_or_invalid_times(self):
        with tempfile.TemporaryDirectory() as directory:
            api = RevenueApi()
            api.normalize_source_export = True
            target = self.target(directory, api)
            target.state["source_window_end"] = "2026-11-09T05:18:05.191512+00:00"
            self.assertTrue(target.prepare()["source_contract_verified"])
            self.assertEqual(target.state["source_window_end"], "2026-11-09T05:18:05.191512+00:00")
            actual = json.loads(definition_part(
                target.state["source_readbacks"]["publisher"]["definition"], "ReflexEntities.json",
            ))
            source = next(entity for entity in actual if entity["type"] == "kqlSource-v1")
            self.assertEqual(source["payload"]["queryParameters"][1]["value"], "11/09/2026 05:18:05")
            api.normalize_source_export = False
            for changed in ("11/09/2026 05:18:06", "09/11/2026 05:18:05"):
                source["payload"]["queryParameters"][1]["value"] = changed
                api.definitions[target.state["items"]["publisher"]] = {"parts": [part("ReflexEntities.json", actual)]}
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "source readback differs"):
                    target.verify_publisher()
            for invalid in ("13/09/2026 05:18:05", "2026-11-09T05:18:05+01:00", None):
                source["payload"]["queryParameters"][1]["value"] = invalid
                api.definitions[target.state["items"]["publisher"]] = {"parts": [part("ReflexEntities.json", actual)]}
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "KQL duration parameters"):
                    target.verify_publisher()

    def test_revenue_response_is_one_native_pipeline_rule_without_teams_or_a_second_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            identifiers = {name: str(uuid.uuid4()) for name in (
                "container", "source", "event", "rule", "source_step", "fields_step",
                "detect_step", "act_step", "fabric_action",
            )}
            schema_id, pipeline_id = str(uuid.uuid4()), str(uuid.uuid4())
            parameters = [(name, name, "Number" if name in ("revenue", "target", "variance_pct") else "String")
                          for name in REVENUE_FIELDS]
            action = {"item_id": pipeline_id, "item_type": "Pipeline", "job_type": "Pipeline", "parameters": parameters}
            entities = activator_entities(target.config, schema_id, identifiers, [], should_run=False, action=action)
            rule = next(entity["payload"] for entity in entities if entity["uniqueIdentifier"] == identifiers["rule"])
            self.assertEqual(rule["name"], read(target.folder / "scenario.json")["business_response"]["rule_name"])
            source = next(entity["payload"] for entity in entities if entity["type"] == "realTimeHubSource-v1")
            self.assertEqual(source["connection"]["eventTypeFullyQualifiedIds"], [
                f"/workspaces/{WORKSPACE}/eventschemasets/{schema_id}/eventtypes/Sales.Revenue.BelowTarget",
            ])
            rules = [entity["payload"]["definition"] for entity in entities
                     if entity["payload"].get("definition", {}).get("type") == "Rule"]
            self.assertEqual(len(rules), 1)
            self.assertFalse(rules[0]["settings"]["shouldRun"])
            instance = json.loads(rules[0]["instance"])
            detector = next(step for step in instance["steps"] if step["name"] == "EventDetectStep")
            self.assertEqual([row["kind"] for row in detector["rows"]], ["OnEveryValue"])
            act = next(step for step in instance["steps"] if step["name"] == "ActStep")
            self.assertEqual([row["kind"] for row in act["rows"]], ["FabricItemInvocation"])
            arguments = {argument["name"]: argument for argument in act["rows"][0]["arguments"]}
            self.assertEqual(arguments["itemId"]["value"], pipeline_id)
            self.assertEqual(arguments["workspaceId"]["value"], WORKSPACE)
            self.assertEqual(arguments["fabricJobConnectionDocumentId"]["value"], identifiers["fabric_action"])
            values = arguments["parameters"]["values"]
            self.assertEqual(len(values), 6)
            for value, (name, field, kind) in zip(values, parameters):
                bindings = {row["name"]: row for row in value["arguments"]}
                self.assertEqual(bindings["parameterName"]["value"], name)
                self.assertEqual(bindings["parameterType"]["value"], kind)
                self.assertEqual(bindings["parameterValue"]["values"][0]["arguments"][0]["value"], field)
            self.assertNotIn("TeamsMessage", json.dumps(entities))
            self.assertNotIn("recipients", json.dumps(entities))
            for wrong_action, recipients in (
                (None, []), ({**action, "item_type": "UserDataFunctions"}, []),
                ({**action, "parameters": [("region", "region", "String")]}, []),
                (action, ["someone@example.com"]),
            ):
                with self.subTest(action=wrong_action, recipients=recipients), self.assertRaises(ValueError):
                    activator_entities(target.config, schema_id, identifiers, recipients, action=wrong_action)

    def test_offline_hybrid_plan_binds_pipeline_and_missing_confirmation_blocks_before_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            target.config["resource_names"]["pipeline"] = "CustomRevenueAnalysis"
            path = Path(directory) / "config.json"
            save(path, target.config)
            output = io.StringIO()
            with patch.object(public_deploy, "PublicApi") as api, contextlib.redirect_stdout(output):
                self.assertEqual(public_deploy.main(target.folder, ["plan", "--config", str(path)]), 0)
            planned = json.loads(output.getvalue())
            self.assertEqual(planned["planned_business_response"]["target_item_name"], "CustomRevenueAnalysis")
            self.assertFalse(planned["planned_business_response"]["requires_teams"])
            self.assertEqual(planned["publisher_backend"], "native_activator_kql")
            self.assertEqual(planned["remaining_resources"], {"dashboard": "RevenueMonitor"})
            self.assertFalse(planned["e2e_verified"])
            self.assertEqual(planned["full_deployment_status"], "ready_for_manual_handoff")
            self.assertEqual(planned["implemented_scope"], "hybrid_deployment")
            self.assertTrue(planned["manual_setup_required"])
            api.assert_not_called()
            for command in ("deploy", "test"):
                with self.subTest(command=command), patch.object(public_deploy, "PublicApi") as api:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(public_deploy.main(target.folder, [command, "--config", str(path)]), 1)
                    api.assert_not_called()
            for extra in (["--scope", "foundations"], ["--source-only"], ["--teams-chat-id", "19:unused"]):
                with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    public_deploy.main(target.folder, ["plan", "--config", str(path), *extra])

    def test_cli_deploy_configures_nine_items_and_reports_manual_dashboard_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            config_path = Path(directory) / "config.json"
            ledger = Path(directory) / "hybrid.json"
            save(config_path, source.config)
            api = source.api
            with patch.object(public_deploy, "PublicApi", return_value=api):
                for command in ("deploy", "resume", "verify", "test"):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        code = public_deploy.main(source.folder, [
                            command, "--config", str(config_path), "--state-file", str(ledger),
                            "--confirm-workspace", WORKSPACE,
                        ])
                    self.assertEqual(code, 2)
                    result = json.loads(output.getvalue())
                    if command != "test":
                        self.assertEqual(len(result["items"]), 9)
                        self.assertTrue(result["consumer_rule_enabled"])
                    else:
                        self.assertFalse(result["source_triggered"])
                        self.assertTrue(result["manual_trigger_steps"])
                    self.assertFalse(result["e2e_verified"])
                    self.assertEqual(result["source_inputs_submitted"], 0)
                    self.assertEqual(result["status"], "manual_source_trigger_required" if command == "test"
                                     else "manual_setup_required")
            self.assertEqual(len(api.items), 9)
            self.assertEqual({item["type"] for item in api.items.values()} & {"Eventhouse", "KQLDatabase"},
                             {"Eventhouse", "KQLDatabase"})
            self.assertEqual(len(api.refreshes), 0)
            updates = [call for call in api.calls if call[1].endswith("/updateDefinition")]
            self.assertEqual(len(updates), 2)
            self.assertEqual(updates[0][1].split("/")[-2], read(ledger)["items"]["publisher"])
            self.assertEqual(updates[1][1].split("/")[-2], read(ledger)["items"]["activator"])
            self.assertFalse(any("/jobs/" in call[1] or call[0] == "DELETE" for call in api.calls))
            for method, url, *_ in api.calls:
                public_url(method, url)

    def test_resume_preserves_publisher_rules_but_rebinding_and_changed_kql_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            target = RevenueDeployment(source.folder, source.config, Path(directory) / "hybrid.json", source.api)
            target.deploy()
            api = source.api
            query_id, publisher_id = target.state["items"]["queryset"], target.state["items"]["publisher"]
            entities = json.loads(definition_part(api.definitions[publisher_id], "ReflexEntities.json"))
            entities.append({"uniqueIdentifier": str(uuid.uuid4()), "type": "timeSeriesView-v1",
                             "payload": {"name": "Manual publisher rule"}})
            api.definitions[publisher_id] = {"parts": [part("ReflexEntities.json", entities)]}
            before = copy.deepcopy(api.definitions)
            call_count = len(api.calls)
            resumed = RevenueDeployment(source.folder, source.config, target.path, api)
            self.assertFalse(resumed.deploy(resume=True)["e2e_verified"])
            self.assertEqual(api.definitions, before)
            self.assertFalse(any(call[0] in ("PUT", "DELETE") or call[1].endswith(("/refreshes", "/updateDefinition"))
                                 for call in api.calls[call_count:] if call[0] != "GET"))
            with self.assertRaisesRegex(ValueError, "different source/component ledgers"):
                RevenueDeployment(source.folder, source.config, target.path, api,
                                  source_state_path=Path(directory) / "different.json")
            queryset = json.loads(definition_part(api.definitions[query_id], "RealTimeQueryset.json"))
            queryset["queryset"]["tabs"][0]["content"] += "\n| where variance_pct < -15"
            api.definitions[query_id]["parts"][0] = part("RealTimeQueryset.json", queryset)
            with self.assertRaisesRegex(ValueError, "original source query"):
                resumed.verify()
            api.definitions[query_id] = before[query_id]
            entities[1]["payload"]["eventhouseItem"]["itemId"] = str(uuid.uuid4())
            api.definitions[publisher_id] = {"parts": [part("ReflexEntities.json", entities)]}
            with self.assertRaisesRegex(ValueError, "query, database, properties or mappings"):
                resumed.verify()

    def test_revenue_test_returns_manual_origin_instructions_without_automatic_verification_or_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            target = RevenueDeployment(source.folder, source.config, Path(directory) / "hybrid.json", source.api)
            target.deploy()
            before = len(source.api.calls)
            with patch.object(target, "verify", side_effect=AssertionError("Test must not verify")):
                result = target.test()
            self.assertFalse(result["source_triggered"])
            self.assertFalse(result["direct_consumer_invocation_performed"])
            self.assertEqual(result["source_inputs_submitted"], 0)
            self.assertTrue(any("80000" in step for step in result["manual_trigger_steps"]))
            self.assertTrue(all(call[0] == "GET" for call in source.api.calls[before:]))

    def test_hybrid_resume_resolves_exact_accepted_source_create_and_never_repeats_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            target = RevenueDeployment(source.folder, source.config, Path(directory) / "hybrid.json", source.api)
            source.api.interrupt_poll = True
            with self.assertRaisesRegex(RuntimeError, "transport interruption"):
                target.deploy()
            self.assertEqual(len(source.api.items), 1)
            self.assertEqual(read(target.path)["pending"]["role"], "eventhouse")
            source.api.interrupt_activation = True
            target = RevenueDeployment(source.folder, source.config, target.path, source.api)
            with self.assertRaisesRegex(RuntimeError, "Activation response interrupted"):
                target.deploy(resume=True)
            self.assertEqual(len(source.api.items), 9)
            target = RevenueDeployment(source.folder, source.config, target.path, source.api)
            self.assertTrue(target.deploy(resume=True)["consumer_rule_enabled"])
            self.assertEqual(len([call for call in source.api.calls if call[1].endswith("/updateDefinition")]), 2)

    def test_canonical_revenue_names_match_configurations_and_legacy_ledger_is_not_rebound(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            manifest = read(target.folder / "scenario.json")
            self.assertEqual(manifest["scenario_id"], target.folder.name)
            self.assertEqual(manifest["scenario_name"], "Revenue Below Target")
            for kind in ("card", "wizard"):
                config = configuration(target.folder, target.folder / f"config.{kind}.example.json")
                self.assertEqual(config["scenario_id"], target.folder.name)
            legacy = copy.deepcopy(target.config)
            legacy["scenario_id"] = "revenue"
            legacy_path = Path(directory) / "legacy-config.json"
            save(legacy_path, legacy)
            loaded = configuration(target.folder, legacy_path)
            previous = RevenueSourceContract(target.folder, loaded, target.path, target.api)
            previous.save()
            self.assertEqual(public_deploy.canonical_scenario_id(loaded["scenario_id"]), manifest["scenario_id"])
            self.assertTrue(previous.owner.endswith("/revenue."))
            with self.assertRaisesRegex(ValueError, "exact public-only configuration"):
                RevenueSourceContract(target.folder, target.config, target.path, target.api)
            self.assertEqual(target.api.calls, [])

    def test_components_prepare_five_native_roles_and_exact_event_inputs_without_replacing_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            source.prepare()
            source_items = copy.deepcopy(source.state["items"])
            target = RevenueComponents(
                source.folder, source.config, Path(directory) / "components.json", source.path, source.api,
            )
            result = target.prepare()
            self.assertTrue(result["component_definitions_verified"])
            self.assertFalse(result["e2e_verified"])
            self.assertFalse(result["eventhouse_subscription_configured"])
            self.assertFalse(result["consumer_rule_enabled"])
            self.assertEqual(set(result["items"]), set(target.resource_types))
            self.assertEqual(len(source.api.items), 9)
            self.assertEqual(read(source.path)["items"], source_items)
            pipeline = json.loads(definition_part(target.get_definition("pipeline"), "pipeline-content.json"))
            activity = pipeline["properties"]["activities"][0]["typeProperties"]
            self.assertEqual(activity["notebookId"], result["items"]["notebook"])
            numeric = {"revenue", "target", "variance_pct"}
            self.assertEqual(set(pipeline["properties"]["parameters"]), set(REVENUE_FIELDS))
            self.assertEqual(set(activity["parameters"]), set(REVENUE_FIELDS) | {"pipeline_run_id"})
            for field in REVENUE_FIELDS:
                self.assertEqual(pipeline["properties"]["parameters"][field]["type"],
                                 "Float" if field in numeric else "String")
                self.assertEqual(activity["parameters"][field], {
                    "type": "float" if field in numeric else "string",
                    "value": {"type": "Expression", "value": "@pipeline().parameters." + field},
                })
            self.assertNotIn("kusto", target.notebook_source())
            self.assertIn(result["items"]["record_store"], target.notebook_source())
            self.assertEqual(activity["parameters"]["pipeline_run_id"]["value"]["value"], "@pipeline().RunId")
            self.assertEqual(len(source.api.refreshes), 0)
            self.assertFalse(any("/jobs/" in call[1] or call[0] == "DELETE" for call in source.api.calls))
            self.assertEqual({role["name"] for role in source.api.roles},
                             {"UnrelatedReader", "OtherPageReader", "PublicScenarioPublisherConsumer"})
            creates = len(source.api.items)
            target.prepare()
            self.assertEqual(len(source.api.items), creates)

    def test_component_readback_rejects_rebound_pipeline_and_unexpectedly_enabled_consumer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            source.prepare()
            target = RevenueComponents(
                source.folder, source.config, Path(directory) / "components.json", source.path, source.api,
            )
            target.prepare()
            identifier = target.state["items"]["pipeline"]
            original = copy.deepcopy(source.api.definitions[identifier])
            content = json.loads(definition_part(original, "pipeline-content.json"))
            content["properties"]["activities"][0]["typeProperties"]["parameters"]["region"]["value"]["value"] = "East"
            source.api.definitions[identifier] = {"parts": [part("pipeline-content.json", content)]}
            with self.assertRaisesRegex(ValueError, "six event parameters, notebook or native run binding"):
                target.verify_components()
            source.api.definitions[identifier] = original
            entities = target.consumer_entities()
            rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
            rule["payload"]["definition"]["settings"]["shouldRun"] = True
            source.api.definitions[target.state["items"]["activator"]] = {"parts": [part("ReflexEntities.json", entities)]}
            with self.assertRaisesRegex(ValueError, "actual native Pipeline consumer"):
                target.verify_components()
            self.assertFalse(read(target.path)["verification"]["e2e_verified"])

    def test_components_resume_known_create_without_adopting_another_source_or_replaying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            source.prepare()
            path = Path(directory) / "components.json"
            target = RevenueComponents(source.folder, source.config, path, source.path, source.api)
            source.api.interrupt_poll = True
            with self.assertRaisesRegex(RuntimeError, "ambiguous transport"):
                target.prepare()
            self.assertEqual(read(path)["pending"]["role"], "schema")
            recovered = RevenueComponents(source.folder, source.config, path, source.path, source.api)
            self.assertTrue(recovered.prepare(resume=True)["component_definitions_verified"])
            self.assertEqual(len(source.api.items), 9)
            other_path = Path(directory) / "other-source.json"
            save(other_path, read(source.path))
            with self.assertRaisesRegex(ValueError, "rebound to another native source"):
                RevenueComponents(source.folder, source.config, path, other_path, source.api)

    def test_components_never_create_or_query_eventhouse_even_when_optional_history_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            source.prepare()
            source.api.create_default_database = True
            target = RevenueComponents(
                source.folder, source.config, Path(directory) / "components.json", source.path, source.api,
            )
            result = target.prepare()
            self.assertFalse(result["eventhouse_required"])
            self.assertEqual(len(source.api.items), 9)
            self.assertTrue(target.verify_components()["component_definitions_verified"])
            self.assertFalse(any("kusto" in call[1] for call in source.api.calls))
            self.assertNotIn("kusto", target.notebook_source())

    def test_existing_optional_history_is_untouched_and_never_becomes_an_investigation_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.target(directory)
            house_id, database_id = str(uuid.uuid4()), str(uuid.uuid4())
            for identifier, kind in ((house_id, "Eventhouse"), (database_id, "KQLDatabase")):
                source.api.items[identifier] = {
                    "id": identifier, "type": kind, "workspaceId": WORKSPACE,
                    "displayName": "Optional " + kind, "description": "Unrelated history",
                }
            original = copy.deepcopy(source.api.items)
            target = RevenueDeployment(source.folder, source.config, Path(directory) / "hybrid.json", source.api)
            result = target.deploy()
            self.assertTrue(result["source_eventhouse_required"])
            self.assertFalse(result["managed_event_history_required"])
            for identifier, item in original.items():
                self.assertEqual(source.api.items[identifier], item)
            self.assertEqual(len(target.state["items"]), 9)
            self.assertFalse(any(house_id in call[1] or database_id in call[1] for call in source.api.calls))

    def test_revenue_onelake_routes_allow_only_scoped_listing_directory_creation_and_json_readback(self):
        identifier = str(uuid.uuid4())
        folder = ONELAKE + "/" + WORKSPACE + "/" + identifier + "/Files/RevenueInvestigations"
        for method, address in (
            ("PUT", folder + "?resource=directory"), ("GET", folder + "/" + "a" * 64 + ".json"),
            ("GET", ONELAKE + "/" + WORKSPACE + "?resource=filesystem&directory="
             + identifier + "%2FFiles%2FRevenueInvestigations&recursive=false&maxResults=500"),
        ):
            public_url(method, address)
        for method, address in (
            ("DELETE", folder), ("PUT", folder + "/" + "a" * 64 + ".json"),
            ("GET", folder + "/unexpected.json"), ("GET", folder.replace("RevenueInvestigations", "OtherFiles")),
        ):
            with self.subTest(method=method, address=address), self.assertRaises(ValueError):
                public_url(method, address)

    def run_investigation(self, payload, run, records, writes, transform=None, put_result=True, drop_write=False):
        exits = []
        def put(path, content, overwrite):
            self.assertFalse(overwrite)
            writes.append(path)
            if put_result and not drop_write:
                records[path] = json.loads(content)
            return put_result
        def head(path, limit):
            record = copy.deepcopy(records[path])
            return json.dumps(transform(record) if transform else record)
        utilities = SimpleNamespace(
            fs=SimpleNamespace(exists=lambda path: path in records, put=put, head=head),
            notebook=SimpleNamespace(exit=lambda result: exits.append(json.loads(result))),
        )
        source = (ROOT / "cicd" / "revenue-below-target" / "response-notebook.py").read_text()
        source = source.replace("# ${record_helpers}", (ROOT / "cicd" / "revenue_records.py").read_text())
        for name, value in {
            "workspace_id": WORKSPACE, "lakehouse_id": "00000000-0000-0000-0000-000000000002",
            "rule_id": "00000000-0000-0000-0000-000000000001",
        }.items():
            source = source.replace("${" + name + "}", value)
        source = source.split("# CELL ********************", 1)[1]
        with patch.dict(sys.modules, {"notebookutils": utilities}):
            exec(compile(source, "response-notebook.py", "exec"), {**payload, "pipeline_run_id": run})
        return exits[0]

    def revenue_event(self):
        return {"region": "West", "period_start": "2026-11-01", "revenue": 80000.0, "target": 100000.0,
                "variance_pct": -20.0, "detected_at": "20261108T120000Z"}

    def test_investigation_consumes_one_event_and_persists_readback_with_real_execution_and_calculations(self):
        event, run, records, writes = self.revenue_event(), str(uuid.uuid4()), {}, []
        record = self.run_investigation(event, run, records, writes)
        self.assertEqual({field: record[field] for field in REVENUE_FIELDS}, event)
        self.assertEqual(record["shortfall"], 20000.0)
        self.assertEqual(record["revenue_lift_pct"], 25.0)
        self.assertEqual(record["pipeline_run_id"], run)
        self.assertEqual(record["status"], "ready_for_review")
        self.assertTrue(record["investigated_at"].endswith("Z"))
        self.assertRegex(writes[0], r"/Files/RevenueInvestigations/[a-f0-9]{64}\.json$")
        self.assertEqual(self.run_investigation(event, run, records, writes), record)
        self.assertEqual(len(writes), 1)
        second_run = str(uuid.uuid4())
        second = self.run_investigation(event, second_run, records, writes)
        self.assertEqual(second["pipeline_run_id"], second_run)
        self.assertNotEqual(second["investigation_id"], record["investigation_id"])
        self.assertEqual(len(records), 2)
        zero = {**event, "revenue": 0.0, "variance_pct": -100.0}
        self.assertIsNone(self.run_investigation(zero, str(uuid.uuid4()), records, writes)["revenue_lift_pct"])
        boundary = {**event, "revenue": 85000.0, "variance_pct": -15.0}
        self.assertEqual(self.run_investigation(boundary, str(uuid.uuid4()), records, writes)["shortfall"], 15000.0)

    def test_investigation_rejects_invalid_event_or_missing_native_run_before_writing(self):
        event = self.revenue_event()
        invalid_events = [
            {**event, "target": 0}, {**event, "target": -1}, {**event, "revenue": float("nan")},
            {**event, "target": float("inf")}, {**event, "variance_pct": -15.0},
            {**event, "variance_pct": -22.0}, {**event, "revenue": True}, {**event, "region": ""},
            {**event, "detected_at": "2026-11-08T12:00:00Z"},
            {**event, "detected_at": "20261108T120000+0100"},
        ]
        for invalid in invalid_events:
            writes = []
            with self.subTest(event=invalid), self.assertRaises(ValueError):
                self.run_investigation(invalid, str(uuid.uuid4()), {}, writes)
            self.assertEqual(writes, [])
        for run in ("", "invented-run", None):
            writes = []
            with self.subTest(run=run), self.assertRaises(ValueError):
                self.run_investigation(event, run, {}, writes)
            self.assertEqual(writes, [])

    def test_investigation_rejects_conflicting_readback_and_failed_or_missing_persistence(self):
        event, run = self.revenue_event(), str(uuid.uuid4())
        for field, value in (
            ("revenue_lift_pct", -20.0), ("region", "East"), ("pipeline_run_id", str(uuid.uuid4())),
            ("investigated_at", "2026-11-08T12:00:00+01:00"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_investigation(event, run, {}, [], transform=lambda row: {**row, field: value})
        records, writes = {}, []
        self.run_investigation(event, run, records, writes)
        record = next(iter(records.values()))
        record["target"] = 200000.0
        with self.assertRaises(ValueError):
            self.run_investigation(event, run, records, writes)
        self.assertEqual(len(writes), 1)
        with self.assertRaisesRegex(RuntimeError, "failed to persist"):
            self.run_investigation(event, run, {}, [], put_result=False)
        with self.assertRaises(KeyError):
            self.run_investigation(event, run, {}, [], drop_write=True)

    def test_history_bound_configuration_is_rejected_without_upgrading_or_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.target(directory)
            legacy = copy.deepcopy(target.config)
            legacy["configuration_version"] = "2.0"
            legacy["resource_names"].pop("record_store")
            legacy["resource_names"].update(eventhouse="RevenueHistory", database="RevenueAnalytics")
            path = Path(directory) / "legacy.json"
            save(path, legacy)
            with self.assertRaises(ValueError):
                configuration(target.folder, path)
            self.assertEqual(read(path), legacy)
            self.assertEqual(target.api.calls, [])


if __name__ == "__main__":
    unittest.main()
