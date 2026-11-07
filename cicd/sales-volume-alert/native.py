"""Package local native definitions for Fabric Bulk Import. No repository integration."""

import base64
import copy
import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OWNER = "Managed by fabric-events/cicd/sales-volume-alert."
PARTS = {
    "EventSchemaSet": ("schema", ("EventSchemaSetDefinition.json",)),
    "Eventhouse": ("eventhouse", ("EventhouseProperties.json",)),
    "KQLDatabase": ("database", ("DatabaseProperties.json", "DatabaseSchema.kql")),
    "Notebook": ("notebook", ("notebook-content.py",)),
    "Reflex": ("activator", ("ReflexEntities.json",)),
    "DeploymentPlan": ("deployment_plan", ("plan.yml",)),
}
FOUNDATIONS = ("schema", "eventhouse", "database", "notebook")
EVENT_NAME = "Retail.Sales.VolumeAlert"
RESOURCE_ROLES = ("schema", "notebook", "activator", "eventhouse", "database")
EVENT_PATTERN = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
RESOURCE_PATTERN = r"[A-Za-z][A-Za-z0-9 _-]{1,98}[A-Za-z0-9_-]"


def event_name(config):
    return config.get("event_name", EVENT_NAME)


def bind_identities(value, replacements):
    if isinstance(value, dict):
        return {
            key: json.dumps(bind_identities(json.loads(child), replacements))
            if key in ("schema", "instance") and isinstance(child, str)
            else bind_identities(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [bind_identities(child, replacements) for child in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def configured_items(config):
    items = local_items()
    if "configuration_version" not in config:
        if any(key in config for key in ("scenario_id", "event_name", "resource_names", "source_backend")):
            raise ValueError("Wizard settings require configuration_version.")
        return items
    allowed = {
        "$schema", "configuration_version", "scenario_id", "event_name", "resource_names",
        "source_backend", "environment", "workspace_id", "resource_prefix",
        "teams_delivery", "teams_recipients", "teams_definition_file",
    }
    if set(config) - allowed:
        raise ValueError("Unknown wizard configuration fields: " + ", ".join(sorted(set(config) - allowed)))
    if config["configuration_version"] != "1.0" or config.get("scenario_id") != "sales-volume":
        raise ValueError("Expected configuration_version 1.0 and scenario_id sales-volume.")
    if config.get("source_backend") != "native_kql_tables":
        raise ValueError("The sales-volume package requires source_backend native_kql_tables.")
    name = config.get("event_name")
    if not isinstance(name, str) or not 3 <= len(name) <= 80 or not re.fullmatch(EVENT_PATTERN, name):
        raise ValueError("event_name must be a 3-80 character Avro-qualified name without hyphens.")
    names = config.get("resource_names")
    if not isinstance(names, dict) or set(names) != set(RESOURCE_ROLES):
        raise ValueError("resource_names must specify schema, notebook, activator, eventhouse and database.")
    names = {**names, "deployment_plan": config["resource_prefix"] + "Deployment"}
    for role, value in names.items():
        if (
            not isinstance(value, str) or value != value.strip()
            or not re.fullmatch(RESOURCE_PATTERN, value)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value, re.I)
        ):
            raise ValueError(f"Invalid portable resource name for {role}; use 3-100 letters, digits, spaces, underscores or hyphens.")
    if len({value.casefold() for value in names.values()}) != len(names):
        raise ValueError("Resource names must be distinct, ignoring case.")
    replacements = {
        EVENT_NAME: name, "#/schemas/" + EVENT_NAME: "#/schemas/" + name,
        **{
            item["logical_id"]: str(uuid.uuid5(uuid.UUID(item["logical_id"]), config["resource_prefix"]))
            for item in items.values()
        },
    }
    for role, item in items.items():
        item["name"] = names[role]
        item["folder"] = names[role] + "." + item["type"]
        item["logical_id"] = replacements[item["logical_id"]]
        for path, content in item["files"].items():
            if path == "notebook-content.py":
                marker = "EVENT_NAME = " + json.dumps(EVENT_NAME)
                source = content.decode("utf-8")
                if source.count(marker) != 1:
                    raise ValueError("The native Notebook must declare one canonical event-name binding.")
                item["files"][path] = source.replace(marker, "EVENT_NAME = " + json.dumps(name), 1).encode("utf-8")
            elif path.endswith(".json") or path in (".platform", "plan.yml"):
                value = bind_identities(json.loads(content), replacements)
                if path == ".platform":
                    value["metadata"]["displayName"] = names[role]
                item["files"][path] = json.dumps(value, indent=2).encode("utf-8")
    return items


def part(path, content):
    if not isinstance(content, bytes):
        content = content.encode("utf-8")
    return {
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(content).decode("ascii"),
    }


def local_items(root=ROOT / "items"):
    items = {}
    identifiers = set()
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name == "__pycache__":
            continue
        platform = json.loads((folder / ".platform").read_text(encoding="utf-8"))
        metadata = platform["metadata"]
        role, paths = PARTS[metadata["type"]]
        logical_id = str(uuid.UUID(platform["config"]["logicalId"]))
        if role in items or logical_id in identifiers:
            raise ValueError("Native item roles and logical IDs must be unique.")
        if metadata["description"] != OWNER:
            raise ValueError("Native item ownership description differs.")
        if folder.name != metadata["displayName"] + "." + metadata["type"]:
            raise ValueError("Native item folder must match its display name and type.")
        files = {path: (folder / path).read_bytes() for path in (".platform", *paths)}
        identifiers.add(logical_id)
        items[role] = {
            "folder": folder.name, "name": metadata["displayName"],
            "type": metadata["type"], "logical_id": logical_id, "files": files,
        }
    if set(items) != {entry[0] for entry in PARTS.values()}:
        raise ValueError("The native scenario must contain every required item type.")
    return items


def definition(role, items=None):
    item = (items if items is not None else local_items())[role]
    return {"parts": [part(path, content) for path, content in item["files"].items()]}


def validate_plan(items):
    plan = json.loads(items["deployment_plan"]["files"]["plan.yml"])
    if plan["version"] != "1.0.0":
        raise ValueError("Unsupported native deployment plan version.")
    groups = plan["groups"]
    names = [group["name"].casefold() for group in groups]
    ids = [group["logicalId"] for group in groups]
    if len(set(names)) != len(names) or len(set(ids)) != len(ids):
        raise ValueError("Native deployment plan groups must be unique.")
    if set(ids) != {items[role]["logical_id"] for role in FOUNDATIONS}:
        raise ValueError("Native plan must select exactly the foundation items.")
    by_name = {group["name"].casefold(): group for group in groups}
    visited, visiting = set(), set()

    def visit(name):
        if name in visiting:
            raise ValueError("Native deployment plan contains a dependency cycle.")
        if name not in by_name:
            raise ValueError("Native deployment plan references an unknown group.")
        if name in visited:
            return
        visiting.add(name)
        group = by_name[name]
        if group.get("preActions") or group.get("postActions"):
            raise ValueError("Do not execute the publisher before consumer configuration and verification.")
        for dependency in group.get("dependsOn", []):
            visit(dependency["groupName"].casefold())
        visiting.remove(name)
        visited.add(name)

    for name in names:
        visit(name)
    return plan


def bulk_payload(roles, items=None, overrides=None, with_plan=False):
    items = items if items is not None else local_items()
    roles = tuple(roles)
    if len(set(roles)) != len(roles):
        raise ValueError("A native import cannot contain duplicate items.")
    parts = []
    for role in roles:
        item = items[role]
        content = definition(role, items)
        if overrides and role in overrides:
            replacement = {entry["path"]: entry for entry in overrides[role]["parts"]}
            if set(replacement) != set(item["files"]) - {".platform"}:
                raise ValueError("A binding must preserve the native item's definition parts.")
            content["parts"] = [part(".platform", item["files"][".platform"]), *replacement.values()]
        for entry in content["parts"]:
            if "${" in base64.b64decode(entry["payload"]).decode("utf-8"):
                raise ValueError(f"Unresolved environment binding in {role}.")
            parts.append({**entry, "path": "/" + item["folder"] + "/" + entry["path"]})
    options = {"allowPairingByName": False}
    if with_plan:
        validate_plan(items)
        if set(roles) != {*FOUNDATIONS, "deployment_plan"}:
            raise ValueError("Attach the native plan only to its complete foundation import.")
        options["deploymentPlan"] = {
            "referenceType": "ByLogicalId", "logicalId": items["deployment_plan"]["logical_id"],
        }
    return {"definitionParts": parts, "options": options}


def bind(value, bindings):
    if isinstance(value, dict):
        return {
            key: json.dumps(bind(json.loads(child), bindings)) if key == "instance"
            and isinstance(child, str) else bind(child, bindings)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [bind(child, bindings) for child in value]
    if isinstance(value, str):
        if value in bindings:
            return copy.deepcopy(bindings[value])
        if "${" in value:
            raise ValueError("Unknown environment binding in the native definition.")
    return value


def notebook_configuration(parameters, items=None):
    items = items if items is not None else local_items()
    source = items["notebook"]["files"]["notebook-content.py"].decode("utf-8")
    marker = 'config_json = "{}"'
    if source.count(marker) != 1:
        raise ValueError("The native notebook must declare one unbound config_json parameter.")
    source = source.replace(marker, "config_json = " + json.dumps(json.dumps(parameters)), 1)
    return {"parts": [part("notebook-content.py", source)]}
