"""Orchestrate native Fabric Bulk Import, runtime configuration and scenario verification."""

import argparse
import ast
import base64
import copy
import json
import math
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fabric_api import FABRIC, KUSTO, STORAGE, FabricApi, FabricOperationError, FabricRequestError, Reply, operation_error_codes
import connections
import native
from scenario import EVENT_NAME, expected_findings, validate_fixtures

ROOT = Path(__file__).resolve().parent
OWNER = native.OWNER


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    required = {"resource_prefix", "workspace_id", "teams_delivery", "teams_recipients", "teams_definition_file"}
    if "configuration_version" in config:
        required.add("environment")
    if required - set(config):
        raise ValueError("Missing configuration fields: " + ", ".join(sorted(required - set(config))))
    config.setdefault("environment", "fabric")
    if config["environment"] not in ("fabric", "daily", "msit"):
        raise ValueError("environment must be fabric, daily or msit.")
    if not isinstance(config["resource_prefix"], str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{2,39}", config["resource_prefix"],
    ):
        raise ValueError("resource_prefix must be 3-40 letters, digits or underscores, starting with a letter.")
    items = native.configured_items(config)
    if "configuration_version" not in config and any(
        not item["name"].startswith(config["resource_prefix"]) for item in items.values()
    ):
        raise ValueError("resource_prefix must match the names in the native .platform definitions.")
    if config["workspace_id"] is not None:
        if not isinstance(config["workspace_id"], str):
            raise ValueError("workspace_id must be a UUID string or null for offline review.")
        config["workspace_id"] = str(uuid.UUID(config["workspace_id"]))
    if config["teams_delivery"] not in ("channel", "individual"):
        raise ValueError("teams_delivery must be channel or individual.")
    recipients = config["teams_recipients"]
    if not isinstance(recipients, list) or any(
        not isinstance(value, str) or (
            value != "@me" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value)
        )
        for value in recipients
    ):
        raise ValueError("teams_recipients must be an array of email addresses or @me.")
    template = config["teams_definition_file"]
    if template is not None and not isinstance(template, str):
        raise ValueError("teams_definition_file must be a file path string or null.")
    if template:
        config["teams_definition_file"] = str((Path(path).resolve().parent / template).resolve())
    return config


def fixtures():
    rows = json.loads((ROOT / "data" / "store-windows.json").read_text(encoding="utf-8"))
    validate_fixtures(rows)
    return rows


def definition_part(path, value):
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return {
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def decode_part(definition, path):
    matches = [part for part in definition["parts"] if part["path"] == path]
    if len(matches) != 1 or matches[0]["payloadType"] != "InlineBase64":
        raise ValueError(f"Expected one InlineBase64 definition part named {path}.")
    return json.loads(base64.b64decode(matches[0]["payload"], validate=True))


def schema_definition(items=None):
    return native.definition("schema", items)


def validate_schema(definition, items=None, event_name=EVENT_NAME):
    expected = decode_part(schema_definition(items), "EventSchemaSetDefinition.json")
    contract = json.loads((ROOT / "schema.avsc").read_text(encoding="utf-8"))
    contract["name"] = event_name
    if (
        expected["eventTypes"][0]["id"] != event_name
        or expected["eventTypes"][0]["category"] != "BusinessEventType"
        or json.loads(expected["schemas"][0]["versions"][0]["schema"])
        != contract
    ):
        raise ValueError("The native Business Event definition differs from the canonical contract.")
    actual = decode_part(definition, "EventSchemaSetDefinition.json")
    if len(actual["eventTypes"]) != 1 or len(actual["schemas"]) != 1:
        raise ValueError("Expected exactly one Business Event type and one schema.")
    event = actual["eventTypes"][0]
    if any(event.get(key) != value for key, value in expected["eventTypes"][0].items()):
        raise ValueError("The deployed Business Event category or event contract differs.")
    schema = actual["schemas"][0]
    versions = schema["versions"]
    if (
        schema["id"] != event_name or schema["format"] != "Avro/1.12.0"
        or len(versions) != 1 or versions[0]["id"] != "v1"
        or versions[0]["format"] != "Avro/1.12.0"
    ):
        raise ValueError("The deployed schema identity or version differs.")
    if json.loads(versions[0]["schema"]) != json.loads(expected["schemas"][0]["versions"][0]["schema"]):
        raise ValueError("The deployed Avro schema differs from the canonical contract.")


def build_activator(config, schema_id):
    event_name = native.event_name(config)
    event_id = f"/workspaces/{config['workspace_id']}/eventschemasets/{schema_id}/eventtypes/{event_name}"
    definition = native.definition("activator", native.configured_items(config))
    native_source = next(
        entity for entity in decode_part(definition, "ReflexEntities.json")
        if entity["type"] == "realTimeHubSource-v1"
    )
    source_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, event_id + json.dumps(native_source["payload"]["filterSettings"], sort_keys=True),
    ))
    if config["teams_delivery"] == "channel":
        path = config["teams_definition_file"]
        if not path:
            raise ValueError(
                "A channel post requires an exported complete native Teams-channel Activator definition. "
                "No direct-message substitute will be created."
            )
        entities = json.loads(Path(path).read_text(encoding="utf-8"))
        sources = [entity for entity in entities if entity["type"] == "realTimeHubSource-v1"]
        rules = [
            entity for entity in entities if entity["type"] == "timeSeriesView-v1"
            and entity["payload"].get("definition", {}).get("type") == "Rule"
        ]
        if len(sources) != 1 or len(rules) != 1:
            raise ValueError("The native channel template must have exactly one RTH source and one rule.")
        source = sources[0]
        original_types = source["payload"].get("connection", {}).get("eventTypeFullyQualifiedIds", [])
        if len(original_types) != 1 or not original_types[0].endswith("/eventtypes/" + event_name):
            raise ValueError("Export the channel template from this Sales Volume Business Event, not another event.")
        old_source = source["uniqueIdentifier"]
        source["uniqueIdentifier"] = source_id
        for entity in entities:
            definition = entity["payload"].get("definition", {})
            if "instance" in definition:
                instance = json.loads(definition["instance"])
                replace_source_reference(instance, old_source, source_id)
                definition["instance"] = json.dumps(instance)
        configured_source = native.bind(native_source, {"${source_id}": source_id, "${event_id}": event_id})
        for key in ("connection", "filterSettings"):
            source["payload"][key] = configured_source["payload"][key]
        rules[0]["payload"]["definition"]["settings"].update(
            shouldRun=True, shouldApplyRuleOnUpdate=True,
        )
    else:
        if not config["teams_recipients"]:
            raise ValueError("Choose approved individual Teams recipients before enabling the rule.")
        if "@me" in config["teams_recipients"]:
            raise ValueError("Resolve @me from the current delegated user session before rendering the action.")
        entities = native.bind(decode_part(definition, "ReflexEntities.json"), {
            "${source_id}": source_id, "${event_id}": event_id,
            "${teams_recipients}": [
                {"type": "string", "value": value} for value in config["teams_recipients"]
            ],
        })
        source = next(entity for entity in entities if entity["type"] == "realTimeHubSource-v1")
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        rule["payload"]["definition"]["settings"].update(
            shouldRun=True, shouldApplyRuleOnUpdate=True,
        )
    validate_activator(entities, event_id, config["teams_delivery"])
    return {"parts": [definition_part("ReflexEntities.json", entities)]}


def replace_source_reference(value, old_id, new_id):
    if isinstance(value, dict):
        if value.get("kind") == "SourceReference":
            for item in value.get("arguments", []):
                if item.get("name") == "entityId" and item.get("value") == old_id:
                    item["value"] = new_id
        for child in value.values():
            replace_source_reference(child, old_id, new_id)
    elif isinstance(value, list):
        for child in value:
            replace_source_reference(child, old_id, new_id)


def entity_references(value, kind):
    if isinstance(value, dict):
        if value.get("kind") == kind:
            yield from (
                item["value"] for item in value.get("arguments", []) if item.get("name") == "entityId"
            )
        for child in value.values():
            yield from entity_references(child, kind)
    elif isinstance(value, list):
        for child in value:
            yield from entity_references(child, kind)


def validate_activator(entities, event_id, delivery=None):
    ids = [entity["uniqueIdentifier"] for entity in entities]
    if len(ids) != len(set(ids)):
        raise ValueError("Activator entity IDs must be unique.")
    sources = [entity for entity in entities if entity["type"] == "realTimeHubSource-v1"]
    if len(sources) != 1:
        raise ValueError("Expected exactly one Business Events source.")
    source = sources[0]
    if source["payload"]["connection"] != {"scope": "BusinessEvents", "eventTypeFullyQualifiedIds": [event_id]}:
        raise ValueError("Activator points to a different Business Event.")
    if source["payload"]["filterSettings"]["filters"]:
        raise ValueError("Activator must evaluate the native rule without an upstream event filter.")
    references, event_references, views, rules = [], [], [], []
    for entity in entities:
        parent = entity["payload"].get("parentContainer")
        if parent and parent["targetUniqueIdentifier"] not in ids:
            raise ValueError("Activator contains a dangling parent reference.")
        definition = entity["payload"].get("definition", {})
        if "instance" not in definition:
            continue
        instance = json.loads(definition["instance"])
        references.extend(entity_references(instance, "SourceReference"))
        event_references.extend(entity_references(instance, "EventReference"))
        if definition["type"] == "Event":
            views.append(entity["uniqueIdentifier"])
        if definition["type"] == "Rule":
            checks = [step["rows"] for step in instance["steps"] if step["name"] == "EventDetectStep"]
            if instance["templateId"] != "EventTrigger" or checks != [[
                {
                    "name": "EventFieldSelector", "kind": "EventField",
                    "arguments": [{"name": "fieldName", "type": "string", "value": "deviation_pct"}],
                },
                {
                    "name": "NumberValueCondition", "kind": "NumberValueCondition",
                    "arguments": [
                        {"name": "op", "type": "string", "value": "IsLessThan"},
                        {"name": "threshold", "type": "number", "value": -30},
                    ],
                },
            ]]:
                raise ValueError("The native per-event rule must use the strict deviation_pct < -30 condition.")
            actions = [step["rows"] for step in instance["steps"] if step["name"] == "ActStep"]
            if len(actions) != 1 or len(actions[0]) != 1 or "Teams" not in actions[0][0]["kind"]:
                raise ValueError("The scenario requires exactly one native Teams action.")
            action = actions[0][0]
            if delivery == "channel" and action["kind"] == "TeamsMessage" and any(
                argument.get("name") == "recipients" and argument.get("values")
                for argument in action.get("arguments", [])
            ):
                raise ValueError(
                    "The channel template contains the individual-recipient TeamsMessage binding. "
                    "Export an actual channel-post rule; direct messages are not a channel substitute."
                )
            if definition.get("settings", {}).get("shouldRun") is not True:
                raise ValueError("The Teams rule is not enabled.")
            if definition["settings"].get("shouldApplyRuleOnUpdate") is not True:
                raise ValueError("The Teams rule must apply its deployed definition to the active rule.")
            rules.append(entity)
    if (
        len(rules) != 1 or references != [source["uniqueIdentifier"]]
        or len(views) != 1 or event_references != views
    ):
        raise ValueError("Expected one enabled rule and one connected SourceEvent view.")


def merge_roles(existing, member):
    roles = copy.deepcopy(existing)
    name = "SalesVolumeCicdPublisherConsumer"
    role = next((role for role in roles if role["name"] == name), None)
    if role is None:
        role = {"name": name, "members": {"microsoftEntraMembers": []}}
        roles.append(role)
    role["decisionRules"] = [{
        "effect": "Permit", "permission": [
            {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
            {"attributeName": "Action", "attributeValueIncludedIn": ["Publish", "Consume"]},
        ],
    }]
    members = role.setdefault("members", {}).setdefault("microsoftEntraMembers", [])
    if not any(
        existing.get("tenantId") == member["tenantId"] and existing.get("objectId") == member["objectId"]
        for existing in members
    ):
        members.append(member)
    return roles


def role_policy(role):
    policy = {key: copy.deepcopy(role.get(key)) for key in ("name", "decisionRules", "members")}
    for member in (policy["members"] or {}).get("microsoftEntraMembers", []):
        member.pop("objectType", None)
    return policy


def plan(config, with_deployment_plan=False):
    items = native.configured_items(config)
    native.validate_plan(items)
    validate_schema(schema_definition(items), items, native.event_name(config))
    sample = expected_findings(fixtures(), "00000000-0000-0000-0000-000000000001", "2026-01-01T00:00:00Z")
    return {
        "mode": "offline-dry-run", "environment": config["environment"],
        "workspace_id": config["workspace_id"], "cloud_writes": False,
        "deployment_engine": "Fabric Bulk Import Item Definitions",
        "repository_integration": False,
        "native_plan": "Optional preview attachment. No automatic publisher execution.",
        "deployment_plan_attached": with_deployment_plan,
        "foundation_import_items": [
            items[role]["name"] for role in (
                (*native.FOUNDATIONS, "deployment_plan") if with_deployment_plan else native.FOUNDATIONS
            )
        ],
        "resources": [
            {"key": key, "type": item["type"], "name": item["name"],
             "logical_id": item["logical_id"], "optional": key == "deployment_plan"}
            for key, item in items.items()
        ],
        "configuration_version": config.get("configuration_version", "legacy"),
        "scenario_id": config.get("scenario_id", "sales-volume"),
        "event_name": native.event_name(config),
        "history_table": native.event_name(config),
        "schema_fields": json.loads((ROOT / "schema.avsc").read_text(encoding="utf-8"))["fields"],
        "source": "POS transactions and baselines persisted in native KQL source tables, separate from event history.",
        "dummy_data": "Optional separate seed command. Provisioning never loads data or publishes events.",
        "findings_per_run": len(sample),
        "expected_teams_matches": sum(row["deviation_pct"] < -30 for row in sample),
        "teams_delivery": config["teams_delivery"],
        "required_gates": [
            "User confirms the workspace and portal before cloud writes.",
            "The native Business Event -> Eventhouse association is created and verified by the deployment.",
            "Channel delivery requires an exported native channel rule; individual delivery is an explicit opt-in.",
            "Notebook runtime must support Python 3.12 and notebookutils.businessEvents.",
            "Eventhouse ingestion and Teams delivery must be verified separately.",
        ],
    }


class Deployment:
    def __init__(self, config, api=None, state_path=None):
        if not config["workspace_id"]:
            raise ValueError("Confirm a workspace and set workspace_id in your local config.")
        self.config = config
        self.native_items = native.configured_items(config)
        self.api = api or FabricApi(environment=config["environment"])
        self.base = f"workspaces/{config['workspace_id']}/"
        self.state_path = state_path or (
            Path.home() / ".copilot" / "state" / "fabric-events-cicd"
            / config["environment"] / config["workspace_id"]
            / (config["resource_prefix"] + ".json")
        )
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {
            "environment": config["environment"], "workspace_id": config["workspace_id"],
            "resource_prefix": config["resource_prefix"], "items": {},
        }
        if self.state.get("environment", "fabric") != config["environment"]:
            raise ValueError("Deployment state belongs to another Fabric environment.")
        if any(self.state[key] != config[key] for key in ("workspace_id", "resource_prefix")):
            raise ValueError("Deployment state belongs to another workspace or resource prefix.")
        identity = {
            "event_name": native.event_name(config),
            "resources": {
                role: {key: item[key] for key in ("name", "logical_id")}
                for role, item in self.native_items.items()
            },
        }
        saved = self.state.get("scenario_configuration")
        if saved is not None and saved != identity:
            raise ValueError("Scenario names or event identity changed. Use a separate deployment, not this ledger.")
        if saved is None and self.state_path.exists() and "configuration_version" in config:
            raise ValueError("A wizard configuration cannot reuse an unversioned deployment ledger.")
        self.state["scenario_configuration"] = identity

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def status(self):
        workspace = self.api.request("GET", self.base.rstrip("/")).body
        items = self.api.list_all(self.base + "items")
        result = {
            "environment": self.config["environment"], "workspace": workspace["displayName"],
            "workspace_id": self.config["workspace_id"], "cloud_writes": False,
            "workspace_item_count": len(items),
            "scenario_items": [
                {"id": item["id"], "name": item["displayName"], "type": item["type"],
                 "tracked": item["id"] in self.state["items"].values()}
                for item in items if item["displayName"] in {
                    definition["name"] for definition in self.native_items.values()
                } or (
                    "configuration_version" not in self.config
                    and item["displayName"].startswith(self.config["resource_prefix"])
                )
            ],
        }
        pending = self.state.get("pending_import")
        if pending:
            if "terminal_status" in pending:
                status, codes = pending["terminal_status"], pending["error_codes"]
            else:
                operation = self.api.request("GET", pending["location"]).body
                status, codes = operation["status"], operation_error_codes(operation)
                if status == "Failed":
                    pending.update(terminal_status=status, error_codes=codes)
                    self.save()
            result["native_operation"] = {
                "id": pending["location"].rstrip("/").split("/")[-1],
                "status": status, "error_codes": codes,
            }
        elif self.state.get("last_failed_import"):
            failed = self.state["last_failed_import"]
            result["last_failed_native_operation"] = {
                "id": failed["location"].rstrip("/").split("/")[-1],
                "error_codes": failed["error_codes"], "recovered_items": failed["recovered_items"],
            }
        if self.state.get("last_failed_job"):
            failed = self.state["last_failed_job"]
            result["last_failed_notebook_job"] = {
                "id": failed["location"].rstrip("/").split("/")[-1],
                "operation": failed["operation"], "status": failed["status"],
                "error_codes": failed.get("error_codes", []),
            }
        if self.state.get("pending_capacity_assignment"):
            result["pending_capacity_assignment"] = self.state["pending_capacity_assignment"]
        return result

    def assign_capacity(self, capacity_id):
        capacity_id = str(uuid.UUID(capacity_id))
        if self.state.get("pending_import") or self.state.get("pending_job"):
            raise ValueError("Resolve the pending import or notebook job before changing capacity.")
        pending = self.state.get("pending_capacity_assignment")
        if pending and pending["to_capacity_id"] != capacity_id:
            actual = self.api.request("GET", self.base.rstrip("/")).body
            if (
                str(uuid.UUID(actual["capacityId"])) != capacity_id
                or actual.get("capacityAssignmentProgress") != "Completed"
            ):
                raise ValueError("Resume the saved capacity assignment before selecting another capacity.")
            pending = {
                **pending, "to_capacity_id": capacity_id,
                "superseded_capacity_id": pending["to_capacity_id"], "assignment_origin": "external",
            }
        if not pending:
            target = self.preflight()
            capacities = self.api.list_all("capacities")
            matches = [item for item in capacities if item["id"] == capacity_id]
            if len(matches) != 1:
                raise ValueError("The selected capacity is not accessible to the current deployment user.")
            selected = matches[0]
            if selected["state"] != "Active" or not re.fullmatch(r"F(?:T(?:L)?)?[1-9]\d*", selected["sku"]):
                raise ValueError("Select an existing active Fabric or Fabric trial capacity.")
            if not selected.get("region") or selected["region"] != target["capacity_region"]:
                raise ValueError("Existing Fabric items cannot be migrated to another region.")
            pending = {"from_capacity_id": target["capacity_id"], "to_capacity_id": capacity_id}
            if target["capacity_id"] != capacity_id:
                self.state["pending_capacity_assignment"] = pending
                self.save()
                body = native.bind(json.loads(
                    (ROOT / "connections" / "workspace-assignment.json").read_text(encoding="utf-8"),
                ), {"${capacity_id}": capacity_id})
                try:
                    reply = self.api.request("POST", self.base.rstrip("/") + "/assignToCapacity", body)
                except FabricRequestError as error:
                    if error.status in (400, 401, 403, 404, 409, 422, 429):
                        self.state["last_failed_capacity_assignment"] = {
                            **pending, "error_codes": error.error_codes,
                        }
                        self.state.pop("pending_capacity_assignment")
                        self.save()
                    raise
                if reply.status not in (200, 202, 204):
                    raise RuntimeError("Capacity assignment returned an unexpected status; inspect before retrying.")
        deadline = time.monotonic() + 180
        while True:
            workspace = self.api.request("GET", self.base.rstrip("/")).body
            ready = (
                bool(workspace.get("capacityId"))
                and str(uuid.UUID(workspace["capacityId"])) == capacity_id
                and workspace.get("capacityAssignmentProgress") in (None, "Completed")
            )
            if ready and self.config["environment"] in ("daily", "msit"):
                artifacts = self.api.request(
                    "GET", self.api.metadata_host
                    + f"/metadata/workspaces/{self.config['workspace_id']}/artifacts",
                ).body
                routed = {
                    str(uuid.UUID(item["objectId"])) for item in artifacts
                    if item.get("capacityObjectId") and str(uuid.UUID(item["capacityObjectId"])) == capacity_id
                }
                ready = set(self.state["items"].values()).issubset(routed)
            if ready:
                self.state["last_capacity_assignment"] = pending
                self.state.pop("pending_capacity_assignment", None)
                self.save()
                return {"status": "workspace_capacity_verified_compute_unverified", **pending}
            if time.monotonic() >= deadline:
                raise TimeoutError("Capacity assignment is unresolved; resume assign-capacity, not another request.")
            self.api.sleep(3)

    def preflight(self):
        if self.state.get("pending_capacity_assignment"):
            raise ValueError("Resume assign-capacity before starting another deployment stage.")
        workspace = self.api.request("GET", self.base.rstrip("/")).body
        if not workspace.get("capacityId"):
            raise ValueError("The target workspace has no Fabric capacity.")
        items = self.api.list_all(self.base + "items")
        for key, definition in self.native_items.items():
            matches = [item for item in items if item["displayName"] == definition["name"]]
            if any(item["id"] != self.state["items"].get(key) for item in matches):
                raise ValueError(f"Unmanaged name collision for {key}; existing items will not be adopted.")
            if key in self.state["items"]:
                owned = [item for item in items if item["id"] == self.state["items"][key]]
                if len(owned) != 1 or any((
                    owned[0]["displayName"] != definition["name"],
                    owned[0]["type"] != definition["type"],
                    owned[0].get("description") != OWNER,
                )):
                    raise ValueError(f"Ownership or identity of {key} has changed; refusing to overwrite it.")
        capacities = [
            capacity for capacity in self.api.list_all("capacities") if capacity["id"] == workspace["capacityId"]
        ]
        if len(capacities) != 1:
            raise ValueError("The assigned capacity SKU cannot be verified with the current user's capacity access.")
        capacity = capacities[0]
        sku = capacity["sku"]
        if not re.fullmatch(r"(?:F(?:T)?[1-9]\d*|P[1-9]\d*)", sku):
            if sku.startswith("PP") or self.config["environment"] != "daily":
                raise ValueError(
                    f"The workspace capacity SKU is {sku}, not a supported Fabric capacity. "
                    "Premium Per User (PP) does not support this Notebook/Eventhouse deployment. "
                    "Assign an active Fabric F/FT or Premium P capacity in the selected environment."
                )
            print(
                f"WARNING: Daily capacity uses preview SKU {sku}; actual item creation must verify workload support.",
                file=sys.stderr,
            )
        if capacity["state"] != "Active":
            raise ValueError("The assigned Fabric capacity is not active.")
        return {
            "environment": self.config["environment"],
            "workspace": workspace["displayName"], "capacity_id": workspace["capacityId"],
            "capacity_sku": sku, "capacity_region": capacity.get("region"),
        }

    def import_native(self, roles, overrides=None, with_plan=False):
        if self.state.get("pending_import"):
            raise ValueError("A native import is unresolved. Use resume-import, not another deployment.")
        expected = {
            role: {key: self.native_items[role][key] for key in ("name", "type", "logical_id")}
            for role in roles
        }
        payload = native.bulk_payload(roles, self.native_items, overrides, with_plan)
        path = self.base + "items/bulkImportDefinitions" + ("?beta=true" if with_plan else "")
        reply = self.api.request("POST", path, payload)
        if reply.status == 202:
            location = reply.headers.get("Location")
            if not location:
                raise RuntimeError("Accepted native import has no Location. Inspect it before resubmitting.")
            self.api.check_url(location, self.api.control_resource)
            self.state["pending_import"] = {"location": location, "expected": expected}
            self.save()
        return self.complete_import(reply, expected)

    def complete_import(self, reply, expected):
        try:
            result = self.api.complete(reply)
        except FabricOperationError as error:
            self.state["pending_import"].update(
                terminal_status=error.status, error_codes=error.error_codes,
            )
            self.save()
            raise
        return self.record_import(result, expected)

    def record_import(self, result, expected):
        details = result["importItemDefinitionsDetails"]
        self.state["last_import"] = details
        failures = []
        seen = set()
        for entry in details:
            roles = [
                role for role, item in expected.items()
                if item["logical_id"] == entry["itemLogicalId"]
            ]
            if len(roles) != 1 or roles[0] in seen:
                failures.append("Unrecognized or duplicate native import receipt.")
                continue
            role = roles[0]
            seen.add(role)
            item = expected[role]
            if entry["itemType"] != item["type"] or entry["itemDisplayName"] != item["name"]:
                failures.append(f"Native import identity mismatch for {role}.")
                continue
            if entry["operationStatus"] in ("Succeeded", "SucceededDespiteFailures"):
                self.state["items"][role] = str(uuid.UUID(entry["itemId"]))
            if entry["operationStatus"] == "Succeeded":
                self.state.setdefault("native_receipts", {})[role] = copy.deepcopy(entry)
            if entry["operationStatus"] != "Succeeded":
                failures.append(f"{role}: {entry['operationStatus']}.")
        if seen != set(expected):
            failures.append("Native import did not return every requested item.")
        self.state.pop("pending_import", None)
        self.save()
        if failures:
            raise RuntimeError("Native deployment is incomplete. " + " ".join(failures))
        return details

    def resume_import(self):
        pending = self.state.get("pending_import")
        if not pending:
            raise ValueError("There is no saved native import to resume.")
        location = pending["location"]
        if pending.get("terminal_status") == "Failed":
            raise FabricOperationError("Failed", pending["error_codes"])
        details = self.complete_import(
            Reply(202, {"Location": location, "Retry-After": "0"}, {}), pending["expected"],
        )
        return {
            "status": "native_import_completed_configuration_pending",
            "items": details,
            "next": "Run provision again to finish configuration and definition readback.",
        }

    def reconcile_import(self):
        pending = self.state.get("pending_import")
        if not pending:
            raise ValueError("There is no saved native import to reconcile.")
        if "terminal_status" in pending:
            status, codes = pending["terminal_status"], pending["error_codes"]
        else:
            operation = self.api.request("GET", pending["location"]).body
            status, codes = operation["status"], operation_error_codes(operation)
            if status == "Failed":
                pending.update(terminal_status=status, error_codes=codes)
                self.save()
        if status != "Failed":
            raise ValueError("Only a terminal Failed import can be reconciled. Use resume-import otherwise.")
        inventory = self.api.list_all(self.base + "items")
        candidates = {}
        recovered = {}
        proven_receipts = {}
        receipts = [*self.state.get("native_receipts", {}).values(), *self.state.get("last_import", [])]
        for role, expected in pending["expected"].items():
            matches = [
                item for item in inventory
                if item["displayName"] == expected["name"] and item["type"] == expected["type"]
            ]
            if len(matches) > 1:
                raise ValueError("Ambiguous native import side effects; no local ownership will be changed.")
            if matches:
                if matches[0].get("description") != OWNER:
                    raise ValueError("A failed import candidate has foreign ownership; refusing to adopt it.")
                candidate = matches[0]
                proof = next((entry for entry in receipts if (
                    entry.get("operationStatus") == "Succeeded"
                    and entry.get("itemId") == candidate["id"] == self.state["items"].get(role)
                    and entry.get("itemLogicalId") == expected["logical_id"]
                    and entry.get("itemType") == expected["type"]
                    and entry.get("itemDisplayName") == expected["name"]
                )), None)
                if proof is not None:
                    recovered[role] = str(uuid.UUID(candidate["id"]))
                    proven_receipts[role] = copy.deepcopy(proof)
                else:
                    candidates[role] = candidate
        if candidates:
            selected = {item["id"] for item in candidates.values()}
            # All is equivalent only when every workspace item is a verified candidate.
            export_request = {"mode": "All"} if selected == {item["id"] for item in inventory} else {
                "mode": "Selective", "items": [{"id": item["id"]} for item in candidates.values()],
            }
            exported = self.api.complete(self.api.request(
                "POST", self.base + "items/bulkExportDefinitions",
                export_request,
            ))
            index = exported["itemDefinitionsIndex"]
            if (
                len(index) != len(candidates)
                or {item["id"] for item in index} != {item["id"] for item in candidates.values()}
            ):
                raise ValueError("Native export did not identify every failed-import candidate.")
            for role, candidate in candidates.items():
                root = next(item["rootPath"] for item in index if item["id"] == candidate["id"]).rstrip("/") + "/"
                definition = {"parts": [
                    {**part, "path": part["path"][len(root):]}
                    for part in exported["definitionParts"] if part["path"].startswith(root)
                ]}
                platform = decode_part(definition, ".platform")
                expected = pending["expected"][role]
                if (
                    str(uuid.UUID(platform["config"]["logicalId"])) != expected["logical_id"]
                    or platform["metadata"]["type"] != expected["type"]
                    or platform["metadata"]["displayName"] != expected["name"]
                ):
                    raise ValueError("Native export identity differs; names alone cannot establish ownership.")
                if role == "schema":
                    validate_schema(definition, self.native_items, native.event_name(self.config))
                recovered[role] = str(uuid.UUID(candidate["id"]))
        self.state["items"].update(recovered)
        self.state.setdefault("native_receipts", {}).update(proven_receipts)
        self.state["last_failed_import"] = {
            "location": pending["location"], "error_codes": codes,
            "recovered_items": recovered,
        }
        self.state.pop("pending_import")
        self.save()
        return {
            "status": "failed_native_import_reconciled_not_deployed", "cloud_writes": False,
            "recovered_items": recovered, "error_codes": codes,
            "next": "Resolve the reported service failure before explicitly retrying provision.",
        }

    def get_definition(self, key, format_name=None):
        item_id = self.state["items"][key]
        path = self.base + f"items/{item_id}/getDefinition"
        if format_name:
            path += "?format=" + format_name
        return self.api.complete(self.api.request("POST", path))["definition"]

    def provision(self, with_deployment_plan=False):
        if self.state.get("pending_job"):
            raise ValueError("Resolve the saved running job before redeploying its notebook.")
        target = self.preflight()
        member = self.api.tokens.identity(self.api.control_resource)
        roles = (*native.FOUNDATIONS, "deployment_plan") if with_deployment_plan else native.FOUNDATIONS
        self.import_native(roles, with_plan=with_deployment_plan)
        self.preflight()
        schema_id = self.state["items"]["schema"]
        validate_schema(self.get_definition("schema"), self.native_items, native.event_name(self.config))
        role_path = self.base + f"items/{schema_id}/dataAccessRoles"
        storage_endpoint = {
            "fabric": "https://onelake.dfs.fabric.microsoft.com",
            "daily": "https://daily-onelake.pbidedicated.windows.net",
            "msit": "https://msit-onelake.pbidedicated.windows.net",
        }[self.config["environment"]]
        security = self.api.request(
            "POST", f"{storage_endpoint}/v1.0/workspaces/{self.config['workspace_id']}"
            f"/artifacts/{schema_id}/security/enable",
            {"enableOneSecurity": True}, resource=STORAGE,
        )
        if security.status not in (200, 204):
            raise RuntimeError("Security enablement has not completed; do not grant roles or continue deployment.")
        reply = self.api.request("GET", role_path)
        roles = merge_roles(reply.body["value"], member)
        condition = {"If-Match": reply.headers["ETag"]} if reply.headers.get("ETag") else {}
        self.api.request("PUT", role_path, {"value": roles}, extra_headers=condition)
        actual = self.api.request("GET", role_path).body["value"]
        managed = next(role for role in roles if role["name"] == "SalesVolumeCicdPublisherConsumer")
        if not any(role_policy(role) == role_policy(managed) for role in actual):
            raise ValueError("Publish/Consume role readback did not match the intended permissions.")
        database = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["database"]).body
        if database["properties"]["parentEventhouseItemId"] != self.state["items"]["eventhouse"]:
            raise ValueError("Native KQL database dependency was not resolved to the deployed Eventhouse.")
        endpoint = database["properties"]["queryServiceUri"].rstrip("/")
        self.api.check_url(endpoint, KUSTO)
        self.state["query_endpoint"] = endpoint
        self.state["source_backend"] = "native_kql_tables"
        self.save()
        default_parameters = self.notebook_parameters()
        default_parameters.pop("source_run_id", None)
        configured = native.notebook_configuration(default_parameters, self.native_items)
        self.import_native(("notebook",), overrides={"notebook": configured})
        notebook = self.get_definition("notebook", "FabricGitSource")
        parts = [
            part for part in notebook["parts"] if part["path"] == "notebook-content.py"
            and part["payloadType"] == "InlineBase64"
        ]
        if len(parts) != 1:
            raise ValueError("Native notebook source readback is missing or ambiguous.")
        source = base64.b64decode(parts[0]["payload"], validate=True).decode("utf-8-sig")
        expected_source = base64.b64decode(configured["parts"][0]["payload"]).decode("utf-8")
        if ast.dump(ast.parse(source)) != ast.dump(ast.parse(expected_source)):
            raise ValueError("Notebook code readback differs from the deployed source.")
        return {
            "status": "native_foundations_deployed_not_end_to_end", **target,
            "deployment_engine": "Fabric Bulk Import Item Definitions",
            "deployment_plan_attached": with_deployment_plan,
            "items": self.state["items"],
            "next": "Run connect-eventhouse and enable-activator, or use deploy to configure both automatically.",
        }

    def notebook_parameters(self, operation="publish"):
        parameters = {
            "workspace_id": self.config["workspace_id"], "schema_set_id": self.state["items"]["schema"],
            "database_id": self.state["items"]["database"], "query_endpoint": self.state["query_endpoint"],
            "operation": operation,
        }
        if "configuration_version" in self.config:
            parameters["event_name"] = native.event_name(self.config)
        if operation == "publish" and self.state.get("last_seed"):
            parameters["source_run_id"] = self.state["last_seed"]["run_id"]
        return parameters

    def connect_eventhouse(self, create=True):
        if self.state.get("pending_import") or self.state.get("pending_job"):
            raise ValueError("Resolve the pending import or notebook job before changing consumer connections.")
        self.preflight()

        def mark_pending(value):
            if value:
                self.state["pending_eventhouse_connection"] = True
            else:
                self.state.pop("pending_eventhouse_connection", None)
            self.save()

        result = connections.ensure(
            self.api, self.config["workspace_id"], self.state["items"], create=create,
            pending=self.state.get("pending_eventhouse_connection", False), on_pending=mark_pending,
            known=self.state.get("eventhouse_connection"),
            event_name=native.event_name(self.config),
        )
        self.state["eventhouse_connection"] = result
        self.state.pop("pending_eventhouse_connection", None)
        self.save()
        return result

    def deploy(self, with_deployment_plan=False, seed_dummy_data=False):
        if self.config["environment"] not in ("daily", "msit"):
            raise ValueError("The complete deployment currently uses the Daily/MSIT workload association bridge.")
        result = {
            "status": "native_scenario_configured_delivery_unverified",
            "foundations": self.provision(with_deployment_plan),
            "eventhouse": self.connect_eventhouse(),
            "activator": self.enable_activator(),
        }
        if seed_dummy_data:
            result["seed"] = self.run(operation="seed")
        return result

    def enable_activator(self):
        if self.state.get("pending_job") or self.state.get("pending_import"):
            raise ValueError("Resolve the saved job or native import before changing its consumer.")
        self.preflight()
        schema_id = self.state["items"]["schema"]
        resolved = copy.deepcopy(self.config)
        if "@me" in resolved["teams_recipients"]:
            address = self.api.tokens.current_user_address(self.api.control_resource)
            resolved["teams_recipients"] = list(dict.fromkeys(
                address if recipient == "@me" else recipient for recipient in resolved["teams_recipients"]
            ))
        definition = build_activator(resolved, schema_id)
        self.import_native(("activator",), overrides={"activator": definition})
        entities = decode_part(self.get_definition("activator"), "ReflexEntities.json")
        event_id = f"/workspaces/{self.config['workspace_id']}/eventschemasets/{schema_id}/eventtypes/{native.event_name(self.config)}"
        validate_activator(entities, event_id, self.config["teams_delivery"])
        return {"status": "activator_configured_delivery_unverified", "id": self.state["items"]["activator"]}

    def run(self, eventhouse_connected=False, operation="publish"):
        if operation not in ("inspect", "seed", "publish", "seed-and-publish"):
            raise ValueError("operation must be inspect, seed, publish or seed-and-publish.")
        if self.state.get("pending_job"):
            raise ValueError("A saved job is unresolved. Use resume instead of starting another publication.")
        if self.state.get("pending_import"):
            raise ValueError("Resolve the native import before starting the publisher.")
        self.preflight()
        if operation in ("publish", "seed-and-publish"):
            schema_id = self.state["items"]["schema"]
            self.connect_eventhouse(create=False)
            entities = decode_part(self.get_definition("activator"), "ReflexEntities.json")
            event_id = f"/workspaces/{self.config['workspace_id']}/eventschemasets/{schema_id}/eventtypes/{native.event_name(self.config)}"
            validate_activator(entities, event_id, self.config["teams_delivery"])
        notebook_id = self.state["items"]["notebook"]
        parameters = {"operation": "inspect"} if operation == "inspect" else self.notebook_parameters(operation)
        reply = self.api.request(
            "POST", self.base + f"notebooks/{notebook_id}/jobs/execute/instances?beta=false",
            {
                "parameters": [
                    {"name": "config_json", "value": json.dumps(parameters), "type": "Text"},
                    {
                        "name": "fixtures_json",
                        "value": json.dumps(fixtures() if operation in ("seed", "seed-and-publish") else []),
                        "type": "Text",
                    },
                ],
                "executionData": {"compute": "Spark"},
            },
        )
        location = reply.headers.get("Location")
        if reply.status != 202 or not location:
            raise RuntimeError("Notebook execution was not accepted with a job Location.")
        self.api.check_url(location, self.api.control_resource)
        self.state["pending_job"] = location
        self.state["pending_job_operation"] = operation
        self.save()
        return self.wait_for_run(location, reply)

    def resume(self):
        location = self.state.get("pending_job")
        if not location:
            raise ValueError("There is no saved pending job to resume.")
        return self.wait_for_run(location, self.api.request("GET", location))

    def wait_for_run(self, location, reply):
        notebook_id = self.state["items"]["notebook"]
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            self.api.sleep(max(2, int(reply.headers.get("Retry-After", "5"))))
            reply = self.api.request("GET", location)
            status = reply.body["status"]
            if status == "Completed":
                job_id = str(uuid.UUID(location.rstrip("/").split("/")[-1]))
                result = self.api.request(
                    "GET", self.base + f"notebooks/{notebook_id}/jobs/execute/instances/{job_id}?beta=true",
                ).body
                exit_value = result.get("exitValue") or result.get("properties", {}).get("exitValue")
                if not exit_value:
                    raise RuntimeError(
                        "Notebook completed, but its exit summary is unavailable. "
                        "Inspect the existing job; do not blindly rerun it."
                    )
                summary = json.loads(exit_value)
                operation = self.state.get("pending_job_operation", "seed-and-publish")
                if operation == "inspect":
                    version = summary.get("python_version", [])
                    if (
                        summary.get("operation") != "inspect" or len(version) != 3
                        or tuple(version) < (3, 12, 0) or summary.get("business_events_available") is not True
                        or summary.get("published_count") != 0
                    ):
                        raise ValueError("The notebook runtime does not meet the publisher requirements.")
                    self.state["runtime_check"] = summary
                    self.state.pop("pending_job")
                    self.state.pop("pending_job_operation", None)
                    self.save()
                    return {"status": "native_runtime_verified_no_events_published", **summary}
                str(uuid.UUID(summary["run_id"]))
                expected = expected_findings(fixtures(), summary["run_id"], summary["window_start"])
                if (
                    summary.get("operation", "seed-and-publish") != operation
                    or summary["published_count"] != (0 if operation == "seed" else len(expected))
                    or summary["expected_teams_matches"] != sum(row["deviation_pct"] < -30 for row in expected)
                ):
                    raise ValueError("Notebook exit evidence does not match the scenario; inspect the saved job.")
                if operation == "seed" and (
                    summary.get("source_store_count") != len(expected)
                    or summary.get("source_transaction_count") != sum(row["actual_transactions"] for row in expected)
                ):
                    raise ValueError("Dummy-data exit evidence does not match the source contract.")
                self.state["last_seed" if operation == "seed" else "last_run"] = summary
                self.state.pop("pending_job")
                self.state.pop("pending_job_operation", None)
                self.save()
                if operation == "seed":
                    return {"status": "dummy_data_initialized_no_events_published", **summary}
                return {"status": "publisher_completed_consumers_unverified", **summary}
            if status not in ("NotStarted", "InProgress", "Running"):
                codes = operation_error_codes(reply.body.get("failureReason"))
                operation = self.state.get("pending_job_operation")
                if operation in ("inspect", "seed") and status in ("Failed", "Cancelled", "Deduped"):
                    self.state["last_failed_job"] = {
                        "location": location, "operation": operation, "status": status, "error_codes": codes,
                    }
                    self.state.pop("pending_job")
                    self.state.pop("pending_job_operation")
                    self.save()
                detail = " Error codes: " + ", ".join(codes) + "." if codes else ""
                raise RuntimeError(f"Notebook job terminated with status {status!r}.{detail} Inspect the existing job.")
        raise TimeoutError("Notebook execution is still pending; inspect the saved job instead of rerunning.")

    def verify(self, teams_confirmed=False):
        summary = self.state["last_run"]
        run_id = str(uuid.UUID(summary["run_id"]))
        database = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["database"]).body
        endpoint = database["properties"]["queryServiceUri"].rstrip("/") + "/v1/rest/query"
        self.api.check_url(endpoint, KUSTO)
        query = (ROOT / "queries" / "verify.kql").read_text(encoding="utf-8").replace("{{RUN_ID}}", run_id)
        query = query.replace("['" + EVENT_NAME + "']", "['" + native.event_name(self.config) + "']")
        response = self.api.request(
            "POST", endpoint, {"db": database["id"], "csl": query}, resource=KUSTO,
        ).body
        tables = [
            table for table in response["Tables"]
            if "store_id" in [column["ColumnName"] for column in table["Columns"]]
        ]
        if len(tables) != 1 or response.get("error") or response.get("Exceptions"):
            raise ValueError("Kusto did not return a complete findings result.")
        table = tables[0]
        rows = [dict(zip([column["ColumnName"] for column in table["Columns"]], row)) for row in table["Rows"]]
        expected = expected_findings(fixtures(), run_id, summary["window_start"])
        if len(rows) != len(expected):
            raise ValueError("Eventhouse is missing findings or contains unexpected payload variants. Retry verification.")
        for payload in expected:
            matches = [row for row in rows if row["store_id"] == payload["store_id"]]
            if len(matches) != 1:
                raise ValueError("An expected store finding is missing or ambiguous.")
            row = matches[0]
            for key in ("expected_transactions", "actual_transactions", "window_start", "severity"):
                if row[key] != payload[key]:
                    raise ValueError(f"Eventhouse payload mismatch for {key}.")
            if (
                not math.isfinite(row["deviation_pct"])
                or abs(row["deviation_pct"] - payload["deviation_pct"]) > 0.0001
                or row["Deliveries"] < 1
            ):
                raise ValueError("Eventhouse deviation or delivery count did not match.")
        return {
            "status": "end_to_end_verified_with_operator_attestation" if teams_confirmed else "eventhouse_verified_teams_unverified",
            "run_id": run_id, "distinct_findings": len(rows),
            "received_records": sum(row["Deliveries"] for row in rows),
            "teams_evidence": "operator_attested" if teams_confirmed else "not_verified",
            "expected_teams_matches": summary["expected_teams_matches"],
        }


def main(argv=None, api=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(
        "plan", "workspaces", "capacities", "status", "preflight", "deploy", "provision", "import-native",
        "resume-import", "reconcile-import", "connect-eventhouse", "enable-activator",
        "runtime-check", "seed", "run", "resume", "verify", "assign-capacity",
    ))
    parser.add_argument("--config", type=Path, default=ROOT / "config.wizard.example.json")
    parser.add_argument("--state-file", type=Path, help="Explicit deployment ledger path, shared with portal runs.")
    parser.add_argument("--items", nargs="+", choices=native.FOUNDATIONS,
                        help="Foundation definitions to submit together with import-native.")
    parser.add_argument("--capacity-id", help="Existing same-region Fabric capacity UUID for assign-capacity.")
    parser.add_argument("--confirm-workspace", help="Exact workspace UUID, confirming the reviewed write plan.")
    parser.add_argument("--eventhouse-connected", action="store_true",
                        help="Legacy flag. Publication always verifies the actual native connection.")
    parser.add_argument("--with-deployment-plan", action="store_true",
                        help="Attach the optional native preview plan to the Bulk Import operation.")
    parser.add_argument("--seed-dummy-data", action="store_true",
                        help="After successful provision, initialize source data without publishing events.")
    parser.add_argument("--teams-confirmed", action="store_true",
                        help="Operator attests matching Teams notifications and absence of nonmatching notifications.")
    args = parser.parse_args(argv)
    if bool(args.capacity_id) != (args.command == "assign-capacity"):
        raise ValueError("--capacity-id is required only with assign-capacity.")
    if args.seed_dummy_data and args.command not in ("deploy", "provision", "run"):
        raise ValueError("--seed-dummy-data is supported with deploy, provision or run. Use seed for separate initialization.")
    if args.command == "import-native" and not args.items:
        raise ValueError("import-native requires an explicit --items selection.")
    if args.items and args.command != "import-native":
        raise ValueError("--items is supported only with import-native.")
    config = load_config(args.config)
    if args.command == "plan":
        result = plan(config, args.with_deployment_plan)
    elif args.command in ("workspaces", "capacities"):
        api = api or FabricApi(environment=config["environment"])
        entries = api.list_all(args.command)
        result = {
            "environment": config["environment"], "cloud_writes": False,
            args.command: [
                {"id": entry["id"], "display_name": entry["displayName"], **(
                    {key: entry.get(key) for key in ("sku", "state", "region")}
                    if args.command == "capacities" else {}
                )}
                for entry in entries
            ],
        }
    else:
        if args.command in (
            "deploy", "provision", "import-native", "connect-eventhouse", "enable-activator", "runtime-check", "seed", "run",
            "assign-capacity",
        ):
            if not config["workspace_id"] or args.confirm_workspace != config["workspace_id"]:
                raise ValueError("Review plan first, then supply the exact --confirm-workspace UUID.")
        if api is not None and api.environment != config["environment"]:
            raise ValueError("The API client and configuration target different Fabric environments.")
        deployment = Deployment(config, api=api, state_path=args.state_file)
        if args.command == "preflight":
            result = deployment.preflight()
        elif args.command == "assign-capacity":
            result = deployment.assign_capacity(args.capacity_id)
        elif args.command == "status":
            result = deployment.status()
        elif args.command == "deploy":
            result = deployment.deploy(args.with_deployment_plan, args.seed_dummy_data)
        elif args.command == "provision":
            result = deployment.provision(args.with_deployment_plan)
            if args.seed_dummy_data:
                result = {
                    "status": "native_foundations_and_dummy_data_ready_consumers_pending",
                    "provision": result, "seed": deployment.run(operation="seed"),
                }
        elif args.command == "import-native":
            deployment.preflight()
            result = {
                "status": "native_items_imported_configuration_pending",
                "items": deployment.import_native(args.items),
            }
        elif args.command == "resume-import":
            result = deployment.resume_import()
        elif args.command == "reconcile-import":
            result = deployment.reconcile_import()
        elif args.command == "enable-activator":
            result = deployment.enable_activator()
        elif args.command == "connect-eventhouse":
            result = deployment.connect_eventhouse()
        elif args.command == "runtime-check":
            result = deployment.run(operation="inspect")
        elif args.command == "seed":
            result = deployment.run(operation="seed")
        elif args.command == "run":
            result = deployment.run(
                args.eventhouse_connected, operation="seed-and-publish" if args.seed_dummy_data else "publish",
            )
        elif args.command == "resume":
            result = deployment.resume()
        else:
            result = deployment.verify(args.teams_confirmed)
    print(json.dumps(result, indent=2))
    if result.get("status") == "eventhouse_verified_teams_unverified":
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
