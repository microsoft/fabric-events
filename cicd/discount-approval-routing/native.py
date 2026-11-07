"""Bind checked-in Fabric definitions. Business rules remain in native artifacts."""

import ast
import base64
import copy
import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVENT_PATTERN = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
RESOURCE_PATTERN = r"[A-Za-z][A-Za-z0-9 _-]{1,98}[A-Za-z0-9_-]"


def manifest():
    return json.loads((ROOT / "scenario.json").read_text(encoding="utf-8"))


def bind(value, replacements):
    if isinstance(value, dict):
        return {
            key: json.dumps(bind(json.loads(child), replacements))
            if key in ("schema", "instance") and isinstance(child, str)
            else bind(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [bind(child, replacements) for child in value]
    if isinstance(value, str):
        if value in replacements:
            return copy.deepcopy(replacements[value])
        if "${" in value:
            raise ValueError(f"Unresolved native binding: {value}")
    return value


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
    return replacements.get(value, value) if isinstance(value, str) else value


def validate_config(config):
    spec = manifest()
    required = {
        "configuration_version", "scenario_id", "environment", "workspace_id",
        "resource_prefix", "event_name", "source_backend", "resource_names",
        "teams_delivery", "teams_recipients", "teams_definition_file",
    }
    if not isinstance(config, dict) or required - config.keys():
        raise ValueError("Configuration must contain the complete versioned card contract.")
    if config.keys() - required - {"$schema"}:
        raise ValueError("Unknown configuration fields.")
    if config["configuration_version"] != "1.0" or config["scenario_id"] != spec["scenario_id"]:
        raise ValueError("Configuration version or scenario_id does not match this package.")
    if config["source_backend"] != spec["source_backend"]:
        raise ValueError("The publisher backend cannot be replaced with another workload.")
    if config["environment"] not in ("msit", "daily", "fabric"):
        raise ValueError("environment must be msit, daily or fabric.")
    if config["workspace_id"] is not None:
        if not isinstance(config["workspace_id"], str):
            raise ValueError("workspace_id must be a UUID string or null for offline planning.")
        config["workspace_id"] = str(uuid.UUID(config["workspace_id"]))
    prefix = config["resource_prefix"]
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,39}", prefix):
        raise ValueError("resource_prefix must contain 3-40 letters, digits or underscores.")
    event = config["event_name"]
    if not isinstance(event, str) or not 3 <= len(event) <= 80 or not re.fullmatch(EVENT_PATTERN, event):
        raise ValueError("event_name must be a 3-80 character Avro-qualified name.")
    names = config["resource_names"]
    if not isinstance(names, dict) or set(names) != set(spec["resource_roles"]):
        raise ValueError("resource_names must exactly match this scenario's wizard resource roles.")
    for role, name in names.items():
        if not isinstance(name, str) or not re.fullmatch(RESOURCE_PATTERN, name) or re.fullmatch(
            r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name, re.I,
        ):
            raise ValueError(f"Invalid portable resource name for {role}.")
        if role in spec["eventstream_roles"] and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,99}", name):
            raise ValueError("Eventstream names require 3-100 letters, digits or underscores.")
    if len({name.casefold() for name in names.values()}) != len(names):
        raise ValueError("Resource names must be distinct, ignoring case.")
    if config["teams_delivery"] != "individual" or config["teams_definition_file"] is not None:
        raise ValueError("These card packages support explicit individual Teams delivery, not channel substitution.")
    recipients = config["teams_recipients"]
    if not isinstance(recipients, list) or not recipients or any(
        not isinstance(value, str) or (
            value != "@me" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value)
        ) for value in recipients
    ):
        raise ValueError("Choose approved individual Teams recipients or @me.")
    return config


def items(config):
    spec = manifest()
    result = {}
    for role, folder in spec["items"].items():
        root = ROOT / "items" / folder
        platform = json.loads((root / ".platform").read_text(encoding="utf-8"))
        identifier = str(uuid.UUID(platform["config"]["logicalId"]))
        result[role] = {
            "role": role, "root": root, "type": platform["metadata"]["type"],
            "name": config["resource_names"][role],
            "template_id": identifier,
            "logical_id": str(uuid.uuid5(uuid.UUID(identifier), config["resource_prefix"])),
        }
    if len({item["logical_id"] for item in result.values()}) != len(result):
        raise ValueError("Native logical IDs must be unique.")
    return result


def part(path, content):
    if not isinstance(content, bytes):
        content = content.encode("utf-8")
    return {
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(content).decode("ascii"),
    }


def definition(role, config, actual_ids=None, recipients=None):
    catalog = items(config)
    item = catalog[role]
    identifiers = {key: value["logical_id"] for key, value in catalog.items()}
    identifiers.update(actual_ids or {})
    workspace = config["workspace_id"] or "00000000-0000-0000-0000-000000000000"
    event_id = f"/workspaces/{workspace}/eventschemasets/{identifiers['schema']}/eventtypes/{config['event_name']}"
    replacements = {
        "${event_name}": config["event_name"], "${schema_url}": "#/schemas/" + config["event_name"],
        "${workspace_id}": workspace, "${schema_id}": identifiers["schema"],
        "${schema_name}": config["resource_names"]["schema"],
        "${source_id}": str(uuid.uuid5(uuid.NAMESPACE_URL, event_id)),
        "${event_id}": event_id, "${rule_enabled}": True,
        "${teams_recipients}": [
            {"type": "string", "value": value} for value in (recipients or config["teams_recipients"])
        ],
        **{
            value["template_id"]: (actual_ids or {}).get(key, value["logical_id"])
            for key, value in catalog.items()
        },
    }
    files = []
    for path in sorted(item["root"].rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(item["root"]).as_posix()
        content = path.read_text(encoding="utf-8")
        if relative == ".platform" or path.suffix == ".json":
            value = bind(json.loads(content), replacements)
            if relative == ".platform":
                value["metadata"].update(displayName=item["name"], description=manifest()["owner"])
                value["config"]["logicalId"] = item["logical_id"]
            content = json.dumps(value, indent=2)
        elif path.suffix == ".py":
            for marker, replacement in (
                ('"${event_name}"', json.dumps(config["event_name"])),
                ('"${schema_name}"', json.dumps(config["resource_names"]["schema"])),
            ):
                if content.count(marker) != 1:
                    raise ValueError("The UDF must declare exactly one event and one connection-alias binding.")
                content = content.replace(marker, replacement, 1)
            ast.parse(content)
        if "${" in content:
            raise ValueError(f"Unresolved native binding in {role}/{relative}.")
        files.append(part(relative, content))
    return {"parts": files}


def bulk_payload(roles, config, actual_ids=None, recipients=None):
    catalog = items(config)
    parts = []
    for role in roles:
        item = catalog[role]
        for entry in definition(role, config, actual_ids, recipients)["parts"]:
            parts.append({**entry, "path": f"/{item['name']}.{item['type']}/{entry['path']}"})
    return {"definitionParts": parts, "options": {"allowPairingByName": False}}


def decoded(definition, path):
    parts = [entry for entry in definition["parts"] if entry["path"] == path]
    if len(parts) != 1 or parts[0]["payloadType"] != "InlineBase64":
        raise ValueError(f"Missing or ambiguous native definition part: {path}")
    return base64.b64decode(parts[0]["payload"], validate=True).decode("utf-8-sig")


def assert_subset(expected, actual, path="$"):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or not expected.keys() <= actual.keys():
            raise ValueError(f"Native definition readback is missing expected properties at {path}.")
        for key, value in expected.items():
            if key in ("schema", "instance") and isinstance(value, str):
                assert_subset(json.loads(value), json.loads(actual[key]), f"{path}.{key}")
            else:
                assert_subset(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise ValueError(f"Native definition readback contains different nodes or fields at {path}.")
        if expected and all(isinstance(item, dict) and "uniqueIdentifier" in item for item in expected):
            if not all(isinstance(item, dict) and "uniqueIdentifier" in item for item in actual):
                raise ValueError("Native entity identifiers are missing.")
            if len({item["uniqueIdentifier"] for item in actual}) != len(actual):
                raise ValueError("Native entity identifiers are duplicated.")
            expected = sorted(expected, key=lambda item: item["uniqueIdentifier"])
            actual = sorted(actual, key=lambda item: item["uniqueIdentifier"])
        for index, (left, right) in enumerate(zip(expected, actual)):
            assert_subset(left, right, f"{path}[{index}]")
    elif expected != actual:
        raise ValueError(f"Native definition readback differs from the intended binding at {path}.")


def assert_definition(expected, actual):
    notes = []
    for entry in expected["parts"]:
        path = entry["path"]
        if path == ".platform":
            continue
        if path == "resources/functions.json" and not any(
            part["path"] == path for part in actual["parts"]
        ):
            notes.append("Generated UDF metadata is not exported; source/connection readback does not establish runtime execution.")
            continue
        left, right = decoded(expected, path), decoded(actual, path)
        if path == "DatabaseSchema.kql" and all(
            not line.strip() or line.lstrip().startswith("//") for line in left.splitlines()
        ):
            notes.append("KQL schema is platform-managed; association and actual payload delivery are checked independently.")
            continue
        if path.endswith(".json"):
            left_value, right_value = json.loads(left), json.loads(right)
            if path == "eventstream.json":
                for graph in (left_value, right_value):
                    for group in ("sources", "streams", "operators", "destinations"):
                        nodes = graph.get(group, [])
                        if len({node["name"] for node in nodes}) != len(nodes):
                            raise ValueError(f"Duplicated Eventstream node names in {group}.")
                        if group in graph:
                            graph[group] = sorted(nodes, key=lambda node: node["name"])
            assert_subset(left_value, right_value)
        elif path.endswith(".py"):
            if ast.dump(ast.parse(left)) != ast.dump(ast.parse(right)):
                raise ValueError("Deployed UDF code differs from the native source.")
        elif left.strip() != right.strip():
            raise ValueError(f"Deployed native source differs: {path}.")
    return notes
