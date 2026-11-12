"""Shared public Activator subscription and individual Teams readback."""

import base64
import copy
import json
import math
import re
from urllib.parse import quote, urlsplit
import uuid

from public_api import GRAPH, Reply, public_url
from public_deploy import Deployment, canonical_scenario_id, validate_teams_chat_id


def part(name, content):
    if not isinstance(content, str):
        content = json.dumps(content, allow_nan=False)
    return {"path": name, "payloadType": "InlineBase64",
            "payload": base64.b64encode(content.encode("utf-8")).decode("ascii")}


def definition_part(definition, name):
    parts = [entry for entry in definition["parts"] if entry["path"] == name]
    if len(parts) != 1 or parts[0]["payloadType"] != "InlineBase64":
        raise ValueError(f"The public definition must contain exactly one {name} InlineBase64 part.")
    return base64.b64decode(parts[0]["payload"], validate=True).decode("utf-8-sig")


def matches_definition(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches_definition(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            matches_definition(left, right) for left, right in zip(expected, actual)
        )
    return expected == actual


def teams_content(message):
    if message.get("verifier") == "public_windows_ui_automation":
        return message["actual_card_text"]
    return json.dumps({"body": message.get("body"), "attachments": message.get("attachments")}, ensure_ascii=True)


def is_teams_receipt(message):
    if message.get("verifier") == "public_windows_ui_automation":
        return message.get("recipient_verified_from_visible_teams_profile") is True
    sender = message.get("from")
    return (not message.get("deletedDateTime") and isinstance(sender, dict)
            and isinstance(sender.get("application"), dict) and bool(sender["application"].get("id")))


def teams_evidence(message):
    return {key: message[key] for key in (
        "id", "createdDateTime", "body", "attachments", "actual_card_text", "verifier",
        "recipient", "recipient_verified_from_visible_teams_profile",
    ) if key in message}


def ui_fields_match(message, fields):
    if message.get("verifier") != "public_windows_ui_automation":
        return True
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", message["actual_card_text"])
    for field, value in fields.items():
        prefix = rf"(?<!\w){re.escape(field)}:\s*"
        if type(value) in (int, float):
            values = re.findall(prefix + r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?=\s|$)", text)
            if not any(
                float(actual) == value if type(value) is int else
                math.isclose(float(actual), value, rel_tol=1e-6, abs_tol=1e-4)
                for actual in values
            ):
                return False
        elif not re.search(prefix + re.escape(str(value)) + r"(?=\s|$)", text):
            return False
    return True


def subscription_entities(config, schema_id, identifiers, container_name, event_name):
    parent = {"targetUniqueIdentifier": identifiers["container"]}
    instance = {
        "templateId": "SourceEvent", "templateVersion": "1.2.4", "steps": [{
            "name": "SourceEventStep", "id": identifiers["source_step"],
            "rows": [{"name": "SourceSelector", "kind": "SourceReference", "arguments": [
                {"name": "entityId", "type": "string", "value": identifiers["source"]},
            ]}],
        }],
    }
    return [
        {"uniqueIdentifier": identifiers["container"], "type": "container-v1",
         "payload": {"name": container_name, "type": "rthSubscriptions"}},
        {"uniqueIdentifier": identifiers["source"], "type": "realTimeHubSource-v1", "payload": {
            "name": config["event_name"], "parentContainer": parent,
            "connection": {"scope": "BusinessEvents", "eventTypeFullyQualifiedIds": [
                f"/workspaces/{config['workspace_id']}/eventschemasets/{schema_id}/eventtypes/{config['event_name']}",
            ]}, "filterSettings": {"filters": []},
        }},
        {"uniqueIdentifier": identifiers["event"], "type": "timeSeriesView-v1", "payload": {
            "name": event_name, "parentContainer": parent,
            "definition": {"type": "Event", "instance": json.dumps(instance)},
        }},
    ]


def activator_entities(config, schema_id, identifiers, recipients, should_run=True, action=None):
    payment = canonical_scenario_id(config["scenario_id"]) == "real-time-stream-alert"
    inventory = canonical_scenario_id(config["scenario_id"]) == "low-stock-threshold"
    revenue = canonical_scenario_id(config["scenario_id"]) == "revenue-below-target"
    discount = canonical_scenario_id(config["scenario_id"]) == "discount-approval-routing"
    viewer = canonical_scenario_id(config["scenario_id"]) == "real-time-viewer-recommendations"
    failure = canonical_scenario_id(config["scenario_id"]) == "data-pipeline-failure-follow-up"
    failure_parameters = [(name, source, "Number" if kind == "int" else "String") for name, source, kind in (
        ("pipelineId", "pipeline_id", "str"), ("pipelineName", "pipeline_name", "str"),
        ("runId", "run_id", "str"), ("status", "status", "str"),
        ("rowsProcessed", "rows_processed", "int"), ("durationSeconds", "duration_seconds", "int"),
        ("triggeredBy", "triggered_by", "str"), ("completedAt", "completed_at", "str"),
    )]
    if failure and (recipients or action is None or action["item_type"] != "UserDataFunctions"
                    or action["job_type"] != "Execute" or action["parameters"] != failure_parameters
                    or action.get("function") != "register_pipeline_failure"):
        raise ValueError("Failed runs require their native SQL response and all eight original parameters.")
    viewer_parameters = [(name, source, "String") for name, source in (
        ("viewerId", "viewer_id"), ("sessionId", "session_id"), ("contentId", "content_id"),
        ("action", "action"), ("occurredAt", "occurred_at"),
    )]
    if viewer and (recipients or action is None or action["item_type"] != "UserDataFunctions"
                   or action["job_type"] != "Execute" or action["parameters"] != viewer_parameters
                   or action.get("function") != "refresh_session_recommendations"):
        raise ValueError("Viewer requires its native recommendation UDF and all five event parameters.")
    if discount and (not recipients or action is not None):
        raise ValueError("Discount requires its native Teams notification, not a substitute Fabric action.")
    revenue_parameters = [(name, name, "Number" if name in ("revenue", "target", "variance_pct") else "String")
                          for name in ("region", "period_start", "revenue", "target", "variance_pct", "detected_at")]
    if revenue and (recipients or action is None or action["item_type"] != "Pipeline"
                    or action["job_type"] != "Pipeline" or action["parameters"] != revenue_parameters
                    or action.get("function")):
        raise ValueError("Revenue requires a Data Pipeline action with all six native event parameters, not Teams.")
    fields = (("product_id", "product_name", "store_id", "current_stock", "threshold", "supplier_id", "detected_at")
              if inventory else
              ("transaction_id", "payment_method", "amount", "currency", "store_id", "processed_at")
              if payment else
              ("region", "period_start", "revenue", "target", "variance_pct", "detected_at")
              if revenue else
              ("deal_id", "approval_level", "approval_reason", "region")
              if discount else
              ("viewer_id", "session_id", "content_id", "action", "occurred_at")
              if viewer else
              ("pipeline_id", "pipeline_name", "run_id", "status", "rows_processed", "duration_seconds", "triggered_by", "completed_at")
              if failure else
              ("store_id", "expected_transactions", "actual_transactions", "deviation_pct", "window_start", "severity"))
    parent = {"targetUniqueIdentifier": identifiers["container"]}
    text = lambda value: {"type": "string", "value": value}
    argument = lambda name, value: {"name": name, "type": "string", "value": value}
    field = lambda name: {
        "type": "complex", "kind": "EventFieldReference", "arguments": [argument("fieldName", name)],
    }
    message = []
    for name in fields:
        message.extend([text(name + ": "), field(name), text(" ")])
    detector = [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}] if payment or inventory or revenue or discount or viewer or failure else [
        {"name": "EventFieldSelector", "kind": "EventField", "arguments": [argument("fieldName", "deviation_pct")]},
        {"name": "NumberValueCondition", "kind": "NumberValueCondition", "arguments": [
            argument("op", "IsLessThan"), {"name": "threshold", "type": "number", "value": -30},
        ]},
    ]
    rule_instance = {
        "templateId": "EventTrigger", "templateVersion": "1.2.4", "steps": [{
            "name": "FieldsDefaultsStep", "id": identifiers["fields_step"], "rows": [{
                "name": "EventSelector", "kind": "Event", "arguments": [{
                    "name": "event", "type": "complex", "kind": "EventReference",
                    "arguments": [argument("entityId", identifiers["event"])],
                }],
            }],
        }, {
            "name": "EventDetectStep", "id": identifiers["detect_step"], "rows": detector,
        }, {
            "name": "ActStep", "id": identifiers["act_step"], "rows": [{
                "name": "TeamsBinding", "kind": "TeamsMessage", "arguments": [
                    argument("messageLocale", "en-us"),
                    {"name": "recipients", "type": "array", "values": [text(value) for value in recipients]},
                    {"name": "headline", "type": "array", "values": [text(
                        "Review a low-stock finding" if inventory else
                        "Review a high-value payment" if payment else
                        "Discount review required" if discount else "Investigate a store transaction-volume drop",
                    )]},
                    {"name": "optionalMessage", "type": "array", "values": message},
                    {"name": "additionalInformation", "type": "array", "values": []},
                ],
            }],
        }],
    }
    if action is not None:
        parameters = [{
            "kind": "FabricItemParameter", "type": "complex", "arguments": [
                argument("parameterName", name), argument("parameterType", kind),
                {"name": "parameterValue", "type": "complexArray", "values": [{
                    "kind": "EventFieldReference", "type": "complexReference",
                    "arguments": [argument("fieldName", source)],
                }]},
            ],
        } for name, source, kind in action["parameters"]]
        arguments = [
            argument("workspaceId", config["workspace_id"]), argument("itemId", action["item_id"]),
            argument("itemType", action["item_type"]), argument("jobType", action["job_type"]),
            argument("fabricJobConnectionDocumentId", identifiers["fabric_action"]),
            {"name": "additionalInformation", "type": "array", "values": []},
            {"name": "parameters", "type": "array", "values": parameters},
        ]
        if action.get("function"):
            arguments.append(argument("subitemId", action["function"]))
        rule_instance["steps"][-1]["rows"] = [{
            "name": "FabricItemBinding", "kind": "FabricItemInvocation", "arguments": arguments,
        }]
    entities = subscription_entities(
        config, schema_id, identifiers,
        "Low stock threshold" if inventory else "High-value payment" if payment else
        "Revenue Below Target" if revenue else "Discount approval" if discount else
        "Viewer recommendations" if viewer else "Pipeline failure follow-up" if failure else "Sales volume alert",
        "Inventory findings" if inventory else "Payment findings" if payment else
        "Revenue shortfalls" if revenue else "Discount review requirements" if discount else
        "Viewer interactions" if viewer else "Failed pipeline executions" if failure else "Sales findings",
    )
    if viewer:
        entities[1]["payload"]["filterSettings"]["filters"] = [{
            "operatorType": "StringIn", "key": "data.action", "values": ["watch", "like"],
        }]
    if failure:
        entities[1]["payload"]["filterSettings"]["filters"] = [{
            "operatorType": "StringIn", "key": "data.status", "values": ["failed"],
        }]
    entities.append(
        {"uniqueIdentifier": identifiers["rule"], "type": "timeSeriesView-v1", "payload": {
            "name": "Prepare replenishment request" if inventory and action else
                    "Register payment review case" if payment and action else
                    "Prepare revenue investigation" if revenue else
                    "Refresh session recommendations" if viewer else
                    "Register failed run" if failure else
                    "Review each low-stock finding" if inventory else
                    "Review each high-value payment" if payment else
                    "Notify discount review" if discount else "Sales volume drop below -30%",
            "parentContainer": parent, "definition": {
                "type": "Rule", "instance": json.dumps(rule_instance),
                "settings": {"shouldRun": should_run, "shouldApplyRuleOnUpdate": False},
            },
        }},
    )
    if action is not None:
        entities.append({
            "uniqueIdentifier": identifiers["fabric_action"], "type": "fabricItemAction-v1",
            "payload": {
                "name": "Prepare replenishment request" if inventory else
                        "Prepare revenue investigation" if revenue else
                        "Refresh session recommendations" if viewer else
                        "Register failed run" if failure else "Register payment review case",
                "fabricItem": {"workspaceId": config["workspace_id"], "itemId": action["item_id"],
                               "itemType": action["item_type"]},
                "jobType": action["job_type"], "parentContainer": parent,
            },
        })
    return entities


class ActivatorDeployment(Deployment):
    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        self.teams_verifier = config.get("teams_verifier", "graph")
        if "activator_ids" not in self.state:
            self.state["activator_ids"] = {key: str(uuid.uuid4()) for key in (
                "container", "source", "event", "rule", "source_step", "fields_step", "detect_step", "act_step",
            )}

    def preflight(self, resuming=False):
        result = super().preflight(resuming)
        recipients = [self.api.principal_name() if value == "@me" else value
                      for value in self.config["teams_recipients"]]
        if len({value.casefold() for value in recipients}) != len(recipients):
            raise ValueError("The resolved Teams recipients must be distinct.")
        if "recipients" in self.state and self.state["recipients"] != recipients:
            raise ValueError("The approved Teams destination changed. This exact deployment will not be rebound.")
        self.state["recipients"] = recipients
        return result

    def get_definition(self, role):
        collection = {"schema": "eventSchemaSets", "database": "kqlDatabases",
                      "notebook": "notebooks", "publisher": "eventstreams", "activator": "items",
                      "response_notebook": "notebooks", "response_pipeline": "dataPipelines",
                      "response_function": "userDataFunctions"}[role]
        reply = self.api.request("POST", self.base + collection + f"/{self.state['items'][role]}/getDefinition")
        return self.wait(reply, {}, need_result=True)["definition"]

    def activator_entities(self, should_run=True):
        return activator_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"], self.state["recipients"],
            should_run=should_run,
        )

    def check_activator(self, definition, should_run=True):
        actual = json.loads(definition_part(definition, "ReflexEntities.json"))
        expected = self.activator_entities(should_run)
        if not isinstance(actual, list):
            raise ValueError("The Activator definition must be an entity array.")
        by_id = {entry["uniqueIdentifier"]: copy.deepcopy(entry) for entry in actual}
        if len(by_id) != len(actual) or set(by_id) != {entry["uniqueIdentifier"] for entry in expected}:
            raise ValueError("The Activator source, event and rule identities differ.")
        for entry in expected:
            received = by_id[entry["uniqueIdentifier"]]
            if (entry["type"] == "fabricItemAction-v1"
                    and entry["payload"]["fabricItem"]["itemType"] == "UserDataFunctions"
                    and received["payload"]["fabricItem"]["itemType"] == "FunctionSet"):
                received["payload"]["fabricItem"]["itemType"] = "UserDataFunctions"
            if entry["type"] == "timeSeriesView-v1":
                entry["payload"]["definition"]["instance"] = json.loads(entry["payload"]["definition"]["instance"])
                received["payload"]["definition"]["instance"] = json.loads(received["payload"]["definition"]["instance"])
            if not matches_definition(entry, received):
                raise ValueError("The persisted Activator subscription, condition, run settings or action differs.")

    def start_activator(self):
        start = self.state.setdefault("activator_start", {})
        if start.get("actual_status") == 202:
            self.wait(Reply(202, start["operation_headers"], None), start, need_result=False)
            start.pop("actual_status")
            self.save()
        definition = self.get_definition("activator")
        entities = json.loads(definition_part(definition, "ReflexEntities.json"))
        rules = [entry for entry in entities if entry["uniqueIdentifier"] == self.state["activator_ids"]["rule"]]
        if len(rules) != 1:
            raise ValueError("Cannot start an Activator whose exact owned rule is missing.")
        settings = rules[0]["payload"]["definition"]["settings"]
        if type(settings.get("shouldRun")) is not bool:
            raise ValueError("The persisted rule has no explicit boolean run setting.")
        self.check_activator(definition, settings["shouldRun"])
        if start.get("phase") == "verified":
            self.check_activator(definition)
            return
        for enabled in (False, True):
            settings["shouldRun"] = enabled
            settings["shouldApplyRuleOnUpdate"] = False
            start.update(phase="starting" if enabled else "stopping")
            self.save()
            response = self.api.request(
                "POST", self.base + f"reflexes/{self.state['items']['activator']}/updateDefinition",
                {"definition": {"parts": [part("ReflexEntities.json", entities)]}},
            )
            start.update(actual_status=response.status, operation_headers=response.headers)
            self.save()
            self.wait(response, start, need_result=False)
            self.check_activator(self.get_definition("activator"), should_run=enabled)
            start.pop("actual_status")
            self.save()
        start["phase"] = "verified"
        self.save()

    def chat_address(self, chat_id):
        validate_teams_chat_id(chat_id)
        if self.config["auth_provider"] != "azure_cli":
            raise ValueError("Automated Teams readback requires azure_cli with delegated Graph Chat.Read.")
        if self.state["recipients"] != [self.api.principal_name()]:
            raise ValueError("Automated E2E currently verifies a single notification to the selected delegated user.")
        address = GRAPH + "/v1.0/me/chats/" + quote(chat_id, safe="")
        chat = self.api.request("GET", address).body
        if chat["id"] != chat_id or chat["chatType"] != "oneOnOne":
            raise ValueError("The readback is not the exact approved individual Teams chat.")
        return address

    def teams_address(self, chat_id):
        if self.teams_verifier == "source_only":
            return None
        if self.teams_verifier == "windows_ui":
            if self.state["recipients"] != [self.api.principal_name()]:
                raise ValueError("Windows Teams verification supports only the selected user's individual Activator chat.")
            return "windows_ui"
        return self.chat_address(chat_id) if chat_id is not None else None

    def chat_messages(self, address):
        if address == "windows_ui":
            from teams_ui import messages
            return messages(self.state["recipients"][0])
        current = address + "/messages?$top=50"
        selected = urlsplit(current).path
        seen, result = set(), []
        while current:
            if current in seen or len(seen) >= 100 or urlsplit(public_url("GET", current)[0]).path != selected:
                raise ValueError("Teams pagination changed the approved chat or exceeded its bounded limit.")
            seen.add(current)
            response = self.api.request("GET", current).body
            if not isinstance(response.get("value"), list):
                raise ValueError("Teams did not return the documented chat message collection.")
            result.extend(response["value"])
            current = response.get("@odata.nextLink")
        return result
