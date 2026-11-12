"""Public native Revenue source preparation, separate from full scenario certification."""

import copy
import datetime
import json
import re
import uuid
from urllib.parse import urlsplit

from public_api import PublicApiError, Reply
from public_consumers import definition_part, matches_definition, part
from public_deploy import Deployment, canonical_scenario_id, read, source_tables

FIELDS = ("region", "period_start", "revenue", "target", "variance_pct", "detected_at")


def normalize_kql_duration(value):
    if not isinstance(value, str):
        raise ValueError("KQL duration parameters require a UTC timestamp string.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
            timestamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", value):
            timestamp = datetime.datetime.strptime(value, "%m/%d/%Y %H:%M:%S")
        else:
            raise ValueError("Unexpected duration timestamp format.")
    except ValueError as error:
        raise ValueError("KQL duration parameters require ISO UTC or MM/dd/yyyy HH:mm:ss.") from error
    # Activator's public export represents UTC duration windows at whole-second precision.
    return timestamp.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_definition(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "expression" and isinstance(child, (str, list)):
                text = "\n".join(child) if isinstance(child, list) else child
                tokens = re.findall(r'"(?:[^"]|"")*"|\S', text)
                result[key] = "".join(tokens)
            elif key == "instance" and isinstance(child, str):
                result[key] = normalize_definition(json.loads(child))
            else:
                result[key] = normalize_definition(child)
        if result.get("type") == "kqlSource-v1" and isinstance(result.get("payload"), dict):
            parameters = result["payload"].get("queryParameters")
            if isinstance(parameters, list):
                for parameter in parameters:
                    if isinstance(parameter, dict) and parameter.get("type") in ("DURATION_START", "DURATION_END"):
                        parameter["value"] = normalize_kql_duration(parameter.get("value"))
        return result
    return [normalize_definition(child) for child in value] if isinstance(value, list) else value


class RevenueSourceContract(Deployment):
    resource_types = {
        "eventhouse": "Eventhouse", "source": "KQLDatabase",
        "queryset": "KQLQueryset", "publisher": "Reflex",
    }

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if canonical_scenario_id(config["scenario_id"]) != "revenue-below-target":
            raise ValueError("This source contract belongs only to Revenue Below Target.")
        if (self.state["items"] or self.state.get("pending")) and self.state.get("revenue_public_source_version") != 3:
            raise ValueError("The source ledger is not a KQL deployment. Start a fresh deployment with unused names.")
        self.state["revenue_public_source_version"] = 3
        self.state.setdefault("source_window_start", "1970-01-01T00:00:00Z")
        self.state.setdefault("source_window_end", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        identifiers = (
            "container", "source", "event", "step", "object", "identity", "identity_tuple",
            "split_event", "identity_step", "identity_tuple_step", "split_step",
            "publication_rule", "publication_action", "publication_select", "publication_detect", "publication_act",
            "query_data_source", "query_observations", "query_current",
        ) + tuple(prefix + field for field in FIELDS for prefix in ("attribute_", "select_", "compute_"))
        source_ids = self.state.setdefault("source_ids", {})
        for key in identifiers:
            source_ids.setdefault(key, str(uuid.uuid4()))

    def database_definition(self):
        return {"parts": [
            part("DatabaseProperties.json", {
                "databaseType": "ReadWrite", "parentEventhouseItemId": self.state["items"]["eventhouse"],
            }),
            part("DatabaseSchema.kql", (self.folder / "DatabaseSchema.kql").read_text(encoding="utf-8")),
        ]}

    def source_query(self):
        return (self.folder / "source-query.kql").read_text(encoding="utf-8")

    def source_connection(self):
        database = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["source"]).body
        if (self.check_item("source", database) != self.state["items"]["source"]
                or database["properties"].get("parentEventhouseItemId") != self.state["items"]["eventhouse"]):
            raise ValueError("The source database is not the exact owned Eventhouse child.")
        endpoint = database["properties"]["queryServiceUri"].rstrip("/")
        address = urlsplit(endpoint)
        if (address.scheme != "https" or not (address.hostname or "").endswith(".kusto.fabric.microsoft.com")
                or address.port not in (None, 443) or address.username or address.password
                or address.path or address.query or address.fragment):
            raise ValueError("The source database has an unexpected public Kusto query endpoint.")
        return endpoint

    def queryset_definition(self):
        identifiers = self.state["source_ids"]
        data_source = identifiers["query_data_source"]
        return {"parts": [part("RealTimeQueryset.json", {"queryset": {
            "version": "1.0.0",
            "dataSources": [{
                "id": data_source, "clusterUri": self.source_connection(),
                "type": "AzureDataExplorer", "databaseName": self.state["items"]["source"],
            }],
            "tabs": [
                {"id": identifiers["query_observations"], "title": "Revenue observations",
                 "content": self.source_query(), "dataSourceId": data_source},
                {"id": identifiers["query_current"], "title": "Current revenue",
                 "content": self.source_query()
                 + "\n| summarize arg_max(observed_at, *) by region, period_start\n| project "
                 + ", ".join(FIELDS), "dataSourceId": data_source},
            ],
        }})]}

    def publisher_entities(self):
        identifiers = self.state["source_ids"]
        parent = {"targetUniqueIdentifier": identifiers["container"]}
        source = {
            "name": "Revenue KQL observations", "runSettings": {"executionIntervalInSeconds": 60},
            "query": {"queryString": self.source_query()},
            "eventhouseItem": {
                "itemId": self.state["items"]["source"], "workspaceId": self.config["workspace_id"],
                "itemType": "KustoDatabase",
            },
            "queryParameters": [
                {"name": name, "type": kind, "value": self.state[key]}
                for name, kind, key in (
                    ("startTime", "DURATION_START", "source_window_start"),
                    ("endTime", "DURATION_END", "source_window_end"),
                )
            ],
            "eventTimeSettings": {
                "timeFieldName": "observed_at", "ingestionDelayInSeconds": 30, "timeZone": "UTC",
            },
            "metadata": {
                "workspaceId": self.config["workspace_id"], "measureName": "",
                "querySetId": self.state["items"]["queryset"], "queryId": identifiers["query_observations"],
            },
            "parentContainer": parent,
        }
        event = {"templateId": "SourceEvent", "templateVersion": "1.3.1", "steps": [{
            "id": identifiers["step"], "name": "SourceEventStep", "rows": [{
                "kind": "SourceReference", "name": "SourceSelector",
                "arguments": [{"name": "entityId", "type": "string", "value": identifiers["source"]}],
            }],
        }]}
        entities = [
            {"uniqueIdentifier": identifiers["container"], "type": "container-v1",
             "payload": {"name": "Revenue native source contract", "type": "kqlQueries"}},
            {"uniqueIdentifier": identifiers["source"], "type": "kqlSource-v1", "payload": source},
            {"uniqueIdentifier": identifiers["event"], "type": "timeSeriesView-v1", "payload": {
                "name": "Revenue native source event", "parentContainer": parent,
                "definition": {"type": "Event", "instance": json.dumps(event)},
            }},
        ]
        parent_object = {"targetUniqueIdentifier": identifiers["object"]}

        def view(key, name, kind, instance=None, on_object=True):
            definition = {"type": kind}
            if instance is not None:
                definition["instance"] = json.dumps(instance)
            payload = {"name": name, "parentContainer": parent, "definition": definition}
            if on_object:
                payload["parentObject"] = parent_object
            return {"uniqueIdentifier": identifiers[key], "type": "timeSeriesView-v1", "payload": payload}

        def template(name, steps):
            return {"templateId": name, "templateVersion": "1.3.1", "steps": steps}

        def assertion(kind):
            return {"name": "TypeAssertion", "kind": "TypeAssertion", "arguments": [
                {"name": "op", "type": "string", "value": kind},
                {"name": "format", "type": "string", "value": ""},
            ]}

        entities.append(view("object", "Revenue reporting period", "Object", on_object=False))
        entities.append(view("identity", "Composite key", "Attribute", template("IdentityPartAttribute", [{
            "id": identifiers["identity_step"], "name": "IdPartStep", "rows": [assertion("Text")],
        }])))
        entities.append(view("identity_tuple", "Composite key", "Attribute", template("IdentityTupleAttribute", [{
            "id": identifiers["identity_tuple_step"], "name": "IdStructureStep", "rows": [{
                "name": "IdPart", "kind": "IdPart", "arguments": [{
                    "name": "idPart", "kind": "AttributeReference", "type": "complex",
                    "arguments": [{"name": "entityId", "type": "string", "value": identifiers["identity"]}],
                }],
            }],
        }])))
        entities.append(view("split_event", "Revenue period observations", "Event", template("SplitEvent", [{
            "id": identifiers["split_step"], "name": "SplitEventStep", "rows": [
                {"name": "EventSelector", "kind": "Event", "arguments": [{
                    "name": "event", "kind": "EventReference", "type": "complex",
                    "arguments": [{"name": "entityId", "type": "string", "value": identifiers["event"]}],
                }]},
                {"name": "FieldIdMapping", "kind": "FieldIdMapping", "arguments": [
                    {"name": "fieldName", "type": "string", "value": "Composite key"},
                    {"name": "idPart", "kind": "AttributeReference", "type": "complex",
                     "arguments": [{"name": "entityId", "type": "string", "value": identifiers["identity"]}]},
                ]},
                {"name": "SplitEventOptions", "kind": "EventOptions", "arguments": [
                    {"name": "isAuthoritative", "type": "boolean", "value": True},
                ]},
            ],
        }])))
        for field in FIELDS:
            kind = "Number" if field in ("revenue", "target", "variance_pct") else "Text"
            entities.append(view("attribute_" + field, field, "Attribute", template("BasicEventAttribute", [
                {"id": identifiers["select_" + field], "name": "EventSelectStep", "rows": [
                    {"name": "EventSelector", "kind": "Event", "arguments": [{
                        "name": "event", "kind": "EventReference", "type": "complex",
                        "arguments": [{"name": "entityId", "type": "string", "value": identifiers["split_event"]}],
                    }]},
                    {"name": "EventFieldSelector", "kind": "EventField", "arguments": [
                        {"name": "fieldName", "type": "string", "value": field},
                    ]},
                ]},
                {"id": identifiers["compute_" + field], "name": "EventComputeStep", "rows": [assertion(kind)]},
            ])))
        return entities

    def publication_entities(self, schema_id):
        schema_id = str(uuid.UUID(schema_id))
        identifiers = self.state["source_ids"]
        parent = {"targetUniqueIdentifier": identifiers["container"]}
        argument = lambda name, value: {"name": name, "type": "string", "value": value}
        fields = read(self.folder / "schema.avsc")["fields"]
        if tuple(field["name"] for field in fields) != FIELDS:
            raise ValueError("Publication requires the six original Revenue schema fields.")
        parameters = []
        for field in fields:
            expected_type = "double" if field["name"] in ("revenue", "target", "variance_pct") else "string"
            if field["type"] != expected_type:
                raise ValueError("Publication must preserve the original Revenue schema types.")
            parameters.append({"type": "complex", "kind": "FabricItemParameter", "arguments": [
                argument("parameterName", field["name"]),
                {"name": "parameterValue", "type": "complexArray", "values": [{
                    "kind": "AttributeReference", "type": "complexReference", "arguments": [
                        argument("entityId", identifiers["attribute_" + field["name"]]),
                    ],
                }]},
                argument("parameterType", expected_type),
            ]})
        instance = {"templateId": "AttributeTrigger", "templateVersion": "1.3.1", "steps": [
            {"id": identifiers["publication_select"], "name": "ScalarSelectStep", "rows": [{
                "name": "AttributeSelector", "kind": "Attribute", "arguments": [{
                    "name": "attribute", "kind": "AttributeReference", "type": "complex", "arguments": [
                        argument("entityId", identifiers["attribute_variance_pct"]),
                    ],
                }],
            }]},
            {"id": identifiers["publication_detect"], "name": "ScalarDetectStep", "rows": [
                {"name": "NumberBecomes", "kind": "NumberBecomes", "arguments": [
                    argument("op", "BecomesLessThan"), {"name": "value", "type": "number", "value": -15},
                ]},
                {"name": "OccurrenceOption", "kind": "EachTime", "arguments": []},
            ]},
            {"id": identifiers["publication_act"], "name": "ActStep", "rows": [{
                "name": "BusinessEventBinding", "kind": "BusinessEventPublication", "arguments": [
                    argument("schemaName", self.config["event_name"]), argument("schemaVersion", "v1"),
                    argument("businessEventDocumentId", identifiers["publication_action"]),
                    {"name": "data", "type": "array", "values": parameters},
                ],
            }]},
        ]}
        return [
            {"uniqueIdentifier": identifiers["publication_action"], "type": "businessEventAction-v1", "payload": {
                "name": "Publish revenue below target", "schemaName": self.config["event_name"], "schemaVersion": "v1",
                "item": {"itemId": schema_id, "workspaceId": self.config["workspace_id"]}, "parentContainer": parent,
            }},
            {"uniqueIdentifier": identifiers["publication_rule"], "type": "timeSeriesView-v1", "payload": {
                "name": "Revenue below target", "parentContainer": parent,
                "parentObject": {"targetUniqueIdentifier": identifiers["object"]},
                "definition": {"type": "Rule", "instance": json.dumps(instance),
                               "settings": {"shouldRun": False, "shouldApplyRuleOnUpdate": False}},
            }},
        ]

    def configure_publication(self, schema_id):
        schema_id = str(uuid.UUID(schema_id))
        record = self.state.get("publisher_publication")
        if record and record["schema_id"] != schema_id:
            raise ValueError("The publication destination cannot be rebound to a different schema set.")
        if record and record["phase"] == "verified":
            return self.verify_publisher(allow_manual_rules=True)
        if record and record.get("actual_status") == 202:
            self.wait(Reply(202, record["headers"], None), record, need_result=False)
        if not record:
            actual = self.verify_publisher()
            definition = copy.deepcopy(self.state["source_readbacks"]["publisher"]["definition"])
            definition["parts"] = [entry for entry in definition["parts"] if entry["path"] != ".platform"]
            for entry in definition["parts"]:
                if entry["path"] == "ReflexEntities.json":
                    entry.update(part("ReflexEntities.json", actual + self.publication_entities(schema_id)))
            record = {"schema_id": schema_id, "phase": "request_started"}
            self.state["publisher_publication"] = record
            self.save()
            reply = self.api.request(
                "POST", self.base + "items/" + self.state["items"]["publisher"] + "/updateDefinition",
                {"definition": definition},
            )
            record.update(actual_status=reply.status, headers=reply.headers)
            self.save()
            if reply.status not in (200, 202):
                raise RuntimeError("Publication definition update requires HTTP 200 or a successful HTTP 202 operation.")
            self.wait(reply, record, need_result=False)
        actual = self.verify_publisher()
        record["phase"] = "verified"
        self.save()
        return actual

    def creation_request(self, role):
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "eventhouse":
            return self.base + "eventhouses", body
        if role == "source":
            collection, definition = "kqlDatabases", self.database_definition()
        elif role == "queryset":
            collection, definition = "items", self.queryset_definition()
            body["type"] = "KQLQueryset"
        elif role == "publisher":
            collection, definition = "items", {"parts": [part("ReflexEntities.json", self.publisher_entities())]}
            body["type"] = "Reflex"
        else:
            raise ValueError("This contract creates only the KQL source, Queryset and Activator publisher.")
        return self.base + collection, {**body, "definition": definition}

    def get_definition(self, role):
        collection = "kqlDatabases" if role == "source" else "items"
        record = {"role": role, "phase": "request_started"}
        self.state.setdefault("source_readbacks", {})[role] = record
        self.save()
        try:
            reply = self.api.request(
                "POST", self.base + collection + "/" + self.state["items"][role] + "/getDefinition",
            )
        except PublicApiError as error:
            record.update(phase="failed", actual_status=error.status, error_code=error.code)
            self.save()
            raise
        record.update(actual_status=reply.status, headers=reply.headers)
        self.save()
        definition = self.wait(reply, record, need_result=True)["definition"]
        record.update(phase="returned", definition=definition)
        self.save()
        return definition

    def verify_source(self):
        self.source_connection()
        actual = self.get_definition("source")
        expected = (self.folder / "DatabaseSchema.kql").read_text(encoding="utf-8")
        if source_tables(definition_part(actual, "DatabaseSchema.kql")) != source_tables(expected):
            raise ValueError("The KQL source readback differs from the original revenue snapshot table.")
        return actual

    def verify_queryset(self):
        actual = self.get_definition("queryset")
        expected = json.loads(definition_part(self.queryset_definition(), "RealTimeQueryset.json"))
        if not matches_definition(expected, json.loads(definition_part(actual, "RealTimeQueryset.json"))):
            raise ValueError("The Queryset readback differs from its original source query or owned database binding.")
        return actual

    def verify_publisher(self, allow_manual_rules=False):
        actual = json.loads(definition_part(self.get_definition("publisher"), "ReflexEntities.json"))
        expected = self.publisher_entities()
        publication = self.state.get("publisher_publication")
        if publication:
            expected.extend(self.publication_entities(publication["schema_id"]))
        by_id = {entity["uniqueIdentifier"]: entity for entity in actual}
        expected_ids = {entity["uniqueIdentifier"] for entity in expected}
        if (len(by_id) != len(actual) or not expected_ids <= set(by_id)
                or not allow_manual_rules and set(by_id) != expected_ids):
            raise ValueError("The public source readback has missing, duplicated or unexpected entity identities.")
        if allow_manual_rules and any(
            entity["type"].endswith("Source-v1") and entity["uniqueIdentifier"] != self.state["source_ids"]["source"]
            for entity in actual
        ):
            raise ValueError("The native publisher contains a substituted or additional source.")
        for entity in expected:
            received = by_id[entity["uniqueIdentifier"]]
            if publication and allow_manual_rules and entity["uniqueIdentifier"] == self.state["source_ids"]["publication_rule"]:
                enabled = received["payload"]["definition"]["settings"].get("shouldRun")
                if type(enabled) is not bool:
                    raise ValueError("The publication rule must retain a boolean activation setting.")
                entity["payload"]["definition"]["settings"]["shouldRun"] = enabled
            if not matches_definition(normalize_definition(entity), normalize_definition(received)):
                raise ValueError("The public KQL source readback differs from its query, database, properties or mappings.")
        return actual

    def prepare(self):
        if self.state.get("pending"):
            raise ValueError("Resolve the exact pending operation before continuing source preparation.")
        self.preflight()
        previous = self.state.pop("source_contract_verification", None)
        if previous:
            self.state.setdefault("source_contract_history", []).append(previous)
        self.state["status"] = "revenue_public_source_preparation_started_no_inputs"
        self.save()
        self.create("eventhouse")
        house = self.api.request("GET", self.base + "eventhouses/" + self.state["items"]["eventhouse"]).body
        children = house["properties"]["databasesItemIds"]
        if not isinstance(children, list) or any(not isinstance(identifier, str) for identifier in children):
            raise ValueError("The source Eventhouse did not return its documented child database inventory.")
        self.state.setdefault("service_created_children", [str(uuid.UUID(identifier)) for identifier in children])
        self.save()
        self.create("source")
        self.verify_source()
        self.create("queryset")
        self.verify_queryset()
        self.create("publisher")
        self.verify_publisher()
        result = {
            "status": "revenue_public_native_source_contract_verified_no_inputs", "source_contract_verified": True,
            "e2e_verified": False, "business_event_publication_configured": False, "source_inputs_submitted": 0,
            "native_source_rows_verified": False, "source_cadence_verified": False, "crossing_detector_verified": False,
            "items": copy.deepcopy(self.state["items"]),
        }
        self.state.update(status=result["status"], source_contract_verification=result)
        self.save()
        return result
