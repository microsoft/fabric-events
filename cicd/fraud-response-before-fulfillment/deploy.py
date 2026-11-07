"""Repeatable Risk deployment; this module never registers or publishes test orders."""

import copy
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from azure_backend import Backend
from fabric_api import FabricRequestError
from flow_api import Flows, ReflexActions
import foundation
import native

ROOT = Path(__file__).resolve().parent
DECISIONS = ("hold", "manual_review", "release")


def load_config(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    example = json.loads((ROOT / "config.card.example.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) - {"$schema"} != set(example):
        raise ValueError("Use the complete versioned Risk configuration; unknown fields are rejected.")
    if value["configuration_version"] != "1.0" or value["scenario_id"] != "fraud":
        raise ValueError("This configuration does not belong to the Risk package.")
    if value["environment"] not in ("fabric", "daily", "msit"):
        raise ValueError("Unsupported Fabric control target.")
    for key in ("workspace_id", "flow_environment_id"):
        if value[key] is not None:
            value[key] = str(uuid.UUID(value[key]))
    if not isinstance(value["resource_prefix"], str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{2,39}", value["resource_prefix"],
    ):
        raise ValueError("Use a portable 3-40 character resource_prefix.")
    if not isinstance(value["event_name"], str) or not re.fullmatch(native.EVENT_PATTERN, value["event_name"]):
        raise ValueError("event_name must be an Avro-qualified name.")
    if not 3 <= len(value["event_name"]) <= 80:
        raise ValueError("event_name must contain 3-80 characters.")
    for key, roles in (("resource_names", set(example["resource_names"])), ("flow_names", set(DECISIONS))):
        names = value[key]
        if not isinstance(names, dict) or set(names) != roles:
            raise ValueError(f"{key} must contain exactly the reviewed resource roles.")
        if any(
            not isinstance(name, str) or not re.fullmatch(native.RESOURCE_PATTERN, name)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name, re.I)
            for name in names.values()
        ):
            raise ValueError(f"{key} contains an invalid portable name.")
        if len({name.casefold() for name in names.values()}) != len(names):
            raise ValueError(f"{key} must contain distinct names.")
    azure = value["azure"]
    if not isinstance(azure, dict) or set(azure) != set(example["azure"]):
        raise ValueError("Supply the complete Azure backend contract.")
    if azure["mode"] not in ("provision", "existing"):
        raise ValueError("Choose provision or explicitly select an existing synthetic backend.")
    if azure["subscription_id"] is not None:
        azure["subscription_id"] = str(uuid.UUID(azure["subscription_id"]))
    patterns = {
        "resource_group": r"[A-Za-z0-9_().-]{1,90}",
        "location": r"[a-z0-9]{2,40}",
        "storage_account": r"[a-z0-9]{3,24}",
        "function_app": r"[a-z][a-z0-9-]{2,58}[a-z0-9]",
        "identity_name": r"[A-Za-z][A-Za-z0-9_-]{2,99}",
        "table_name": r"[A-Za-z][A-Za-z0-9]{2,62}",
    }
    for key, pattern in patterns.items():
        if (
            not isinstance(azure[key], str) or (key != "resource_group" and not azure[key])
            or (azure[key] and not re.fullmatch(pattern, azure[key]))
        ):
            raise ValueError(f"Invalid Azure {key}.")
    connection = value["data_activator_connection_name"]
    if connection is not None and (
        not isinstance(connection, str) or not re.fullmatch(r"shared-dataactivator-[a-zA-Z0-9-]+", connection)
    ):
        raise ValueError("Choose an actual Data Activator connector connection name.")
    return value


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def flow_definition(decision, config, connection_info, backend):
    text = (ROOT / "flows" / (decision + ".json")).read_text(encoding="utf-8")
    for marker, value in {
        "${flow_name}": config["flow_names"][decision],
        "${native_connection_info}": connection_info,
        "${order_api_base_url}": backend.base_url,
        "${order_api_key}": backend.key,
        "${data_activator_connection_name}": config["data_activator_connection_name"],
    }.items():
        text = text.replace(marker, json.dumps(value)[1:-1])
    if "${" in text:
        raise ValueError("The native flow template contains unresolved bindings.")
    return json.loads(text)


class Deployment(foundation.Deployment):
    def __init__(self, config, state_path, api=None):
        if not all((
            config["workspace_id"], config["flow_environment_id"], config["data_activator_connection_name"],
            config["azure"]["subscription_id"], config["azure"]["resource_group"],
        )):
            raise ValueError("Set the actual approved Fabric, Power Automate and Azure targets before deploying.")
        internal = {
            **copy.deepcopy(config), "source_backend": "native_udf",
            "teams_recipients": [], "teams_delivery": "individual", "teams_definition_file": None,
        }
        super().__init__(internal, state_path, api)
        self.flows = Flows(self.api, config["flow_environment_id"])
        self.backend = Backend(config["azure"], self.state, self.save, self.state_path.parent)
        self.state.setdefault("native_connections", {})
        self.state.setdefault("native_actions", {})
        self.state.setdefault("flows", {})

    def check_flows(self):
        self.flows.verify_connection(self.config["data_activator_connection_name"])
        inventory = self.flows.list()
        for decision, name in self.config["flow_names"].items():
            identifier = self.state["flows"].get(decision)
            matches = [
                flow for flow in inventory
                if flow["properties"]["displayName"].casefold() == name.casefold()
            ]
            if any(flow["name"] != identifier for flow in matches):
                raise ValueError(f"Unmanaged flow name collision for {decision}; nothing will be adopted.")
            if identifier and (len(matches) != 1 or matches[0]["name"] != identifier):
                raise ValueError("An exact tracked flow was renamed or removed.")

    def entities(self):
        return json.loads(native.decoded(
            native.definition("activator", self.config, self.state["items"]), "ReflexEntities.json",
        ))

    def configure_actions(self):
        bridge = ReflexActions(self.api, self.config["workspace_id"], self.state["items"]["activator"])
        nodes = self.entities()
        for action in self.spec["actions"]:
            decision = action["decision"]
            expected = next(
                node for node in nodes
                if node["type"] == "powerAutomateFlowAction-v1" and node["payload"]["name"] == action["caption"]
            )
            if decision not in self.state["native_connections"]:
                if self.state.get("pending_native_connection"):
                    raise ValueError("A native connection request is ambiguous; it will not be replayed.")
                self.state["pending_native_connection"] = decision
                self.save()
                result = bridge.create_connection()
                self.state["native_connections"][decision] = {
                    **result, "connection_info_sha256": digest(result["connectionString"]),
                }
                self.state.pop("pending_native_connection")
                self.save()
            connection = self.state["native_connections"][decision]
            if decision not in self.state["native_actions"]:
                current = self.api.complete(self.api.request(
                    "POST", self.base + "items/" + self.state["items"]["activator"] + "/getDefinition",
                ))["definition"]
                actual = json.loads(native.decoded(current, "ReflexEntities.json"))
                found = [node for node in actual if node["uniqueIdentifier"] == expected["uniqueIdentifier"]]
                if found and self.state.get("pending_native_action") != decision:
                    raise ValueError("An untracked native action will not be adopted.")
                if not found:
                    if self.state.get("pending_native_action"):
                        raise ValueError("A native custom-action request is unresolved; it will not be replayed.")
                    self.state["pending_native_action"] = decision
                    self.save()
                    bridge.create_action(
                        expected["uniqueIdentifier"], action["caption"], action["parameters"],
                        connection["flowConnectionId"],
                    )
                    current = self.api.complete(self.api.request(
                        "POST", self.base + "items/" + self.state["items"]["activator"] + "/getDefinition",
                    ))["definition"]
                    actual = json.loads(native.decoded(current, "ReflexEntities.json"))
                    found = [node for node in actual if node["uniqueIdentifier"] == expected["uniqueIdentifier"]]
                if len(found) != 1:
                    raise ValueError("The native action has no exact readback.")
                native.assert_subset(expected, found[0])
                self.state["native_actions"][decision] = expected["uniqueIdentifier"]
                self.state.pop("pending_native_action", None)
                self.save()
            if decision not in self.state["flows"]:
                if self.state.get("pending_flow"):
                    raise ValueError("An accepted or ambiguous flow request is unresolved; it will not be replayed.")
                body = flow_definition(decision, self.config, connection["connectionString"], self.backend)
                self.state["pending_flow"] = decision
                self.save()
                try:
                    result = self.flows.request("POST", "flows", body)
                except FabricRequestError as error:
                    if error.status in (400, 401, 403, 404, 405, 409, 413, 415, 422, 429):
                        self.state["last_flow_rejection"] = {"decision": decision, "status": error.status, "codes": error.error_codes}
                        self.state.pop("pending_flow")
                        self.save()
                    raise
                self.state["flows"][decision] = str(uuid.UUID(result["name"]))
                self.state.pop("pending_flow")
                self.save()
            actual = self.flows.request("GET", "flows/" + self.state["flows"][decision])
            properties = actual["properties"]
            info = properties["definition"]["triggers"]["When_an_activator_rule_is_triggered"]["inputs"]["parameters"]["connectionInfo"]
            if digest(info) != connection["connection_info_sha256"]:
                raise ValueError("The actual flow has a different native action connection.")
            expected_flow = flow_definition(decision, self.config, info, self.backend)["properties"]
            if properties["displayName"] != expected_flow["displayName"] or properties["state"] != "Started":
                raise ValueError("The exact deployed flow is not started with its reviewed name.")
            native.assert_subset(expected_flow["definition"], properties["definition"])
            actual_reference = properties["connectionReferences"]["shared_dataactivator"]
            if actual_reference["connectionName"] != self.config["data_activator_connection_name"]:
                raise ValueError("The flow selected a different connector connection.")
            connection.pop("connectionString", None)
            self.save()

    def apply_rules(self, enabled):
        expected = {"parts": [native.part("ReflexEntities.json", json.dumps(self.entities()))]}
        nodes = json.loads(native.decoded(expected, "ReflexEntities.json"))
        for node in nodes:
            if node["payload"].get("definition", {}).get("type") == "Rule":
                node["payload"]["definition"]["settings"].update(
                    shouldRun=enabled, shouldApplyRuleOnUpdate=False,
                )
        expected = {"parts": [native.part("ReflexEntities.json", json.dumps(nodes))]}
        route = self.base + "items/" + self.state["items"]["activator"]
        pending = self.state.get("pending_rules")
        if pending and pending["enabled"] != enabled:
            raise ValueError("A different rule update is unresolved.")
        if not pending:
            current = self.api.complete(self.api.request("POST", route + "/getDefinition"))["definition"]
            existing = json.loads(native.decoded(current, "ReflexEntities.json"))
            intended = {node["uniqueIdentifier"]: node for node in nodes}
            identifiers = {node["uniqueIdentifier"] for node in existing}
            base_ids = {
                node["uniqueIdentifier"] for node in nodes
                if node["payload"].get("definition", {}).get("type") != "Rule"
            }
            if len(identifiers) != len(existing) or identifiers not in (base_ids, set(intended)):
                raise ValueError("The owned Activator contains unexpected or missing entities.")
            settings = []
            for node in existing:
                match = copy.deepcopy(intended[node["uniqueIdentifier"]])
                definition = node["payload"].get("definition", {})
                if definition.get("type") == "Rule":
                    actual_settings = definition.get("settings", {})
                    if (
                        type(actual_settings.get("shouldRun")) is not bool
                        or actual_settings.get("shouldApplyRuleOnUpdate") is not False
                    ):
                        raise ValueError("A native rule has unexpected activation or replay settings.")
                    match["payload"]["definition"]["settings"]["shouldRun"] = actual_settings["shouldRun"]
                    settings.append(actual_settings["shouldRun"])
                native.assert_subset(match, node)
            if identifiers == set(intended) and settings == [enabled] * 3:
                self.state["rules_enabled"] = enabled
                self.save()
                return
            self.state["pending_rules"] = {"enabled": enabled, "request_started": True}
            self.save()
            reply = self.api.request("POST", route + "/updateDefinition", {"definition": expected})
            if reply.status == 202:
                location = reply.headers.get("Location")
                if not location:
                    raise RuntimeError("The accepted rule update has no tracked operation location.")
                self.api.check_url(location, self.api.control_resource)
                self.state["pending_rules"]["location"] = location
                self.save()
        pending = self.state["pending_rules"]
        if pending.get("location"):
            deadline = time.monotonic() + 600
            while True:
                operation = self.api.request("GET", pending["location"]).body
                if operation["status"] == "Succeeded":
                    break
                if operation["status"] in ("Failed", "Cancelled") or time.monotonic() >= deadline:
                    raise RuntimeError("The tracked native rule update did not succeed.")
                self.api.sleep(3)
        actual = self.api.complete(self.api.request("POST", route + "/getDefinition"))["definition"]
        native.assert_definition(expected, actual)
        self.state.pop("pending_rules")
        self.state["rules_enabled"] = enabled
        self.save()

    def deploy(self, resume=False):
        if self.state.get("pending_import"):
            if not resume:
                raise ValueError("Resume the exact pending import instead of deploying again.")
            self.resume_import()
        self.preflight()
        self.check_flows()
        self.backend.provision()
        self.import_items(("schema", "eventhouse", "database"))
        self.readback("schema")
        self.grant_access()
        self.connect_history(create=True)
        self.import_items(("publisher",))
        self.import_items(("activator",))
        self.configure_actions()
        self.apply_rules(True)
        for role in self.catalog:
            self.readback(role)
        self.preflight()
        self.check_flows()
        self.state["status"] = "deployed_native_flows_ready_no_test_inputs"
        self.save()
        return {"status": self.state["status"], "items": self.state["items"], "flows": self.state["flows"]}
