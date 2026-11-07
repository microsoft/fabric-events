"""Shared ownership, import, permission, association and readback helpers for Revenue."""

import copy
import json
from pathlib import Path
import re
import uuid

import connections
import native
from fabric_api import FabricApi, STORAGE, Reply

ROOT = Path(__file__).resolve().parent


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(config, dict) and isinstance(config.get("teams_definition_file"), str):
        config["teams_definition_file"] = str(
            (Path(path).resolve().parent / config["teams_definition_file"]).resolve()
        )
    return native.validate_config(config)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def fingerprint(config):
    return {key: copy.deepcopy(value) for key, value in config.items() if key != "$schema"}


class Deployment:
    def __init__(self, config, state_path, api=None):
        if not config["workspace_id"]:
            raise ValueError("Set the actual approved workspace_id before deployment or testing.")
        self.config = config
        self.spec = native.manifest()
        self.catalog = native.items(config)
        self.state_path = Path(state_path).resolve()
        self.api = api or FabricApi(environment=config["environment"])
        if self.api.environment != config["environment"]:
            raise ValueError("API client and configuration target different environments.")
        self.base = f"workspaces/{config['workspace_id']}/"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {
            "configuration": fingerprint(config), "items": {}, "status": "new",
        }
        if self.state.get("configuration") != fingerprint(config):
            raise ValueError("This ledger belongs to a different configuration. Do not change its names or target.")

    def save(self):
        atomic_json(self.state_path, self.state)

    def preflight(self):
        if self.config["environment"] not in ("daily", "msit"):
            raise ValueError("Automatic Eventhouse association currently requires Daily or MSIT.")
        workspace = self.api.request("GET", self.base.rstrip("/")).body
        capacities = [
            item for item in self.api.list_all("capacities") if item["id"] == workspace.get("capacityId")
        ]
        if len(capacities) != 1 or capacities[0]["state"] != "Active" or not re.fullmatch(
            r"(?:F(?:T)?[1-9]\d*|P[1-9]\d*)", capacities[0]["sku"],
        ):
            raise ValueError("The approved workspace must have a verifiable active Fabric capacity.")
        inventory = self.api.list_all(self.base + "items")
        for role, name in self.config["resource_names"].items():
            matches = [item for item in inventory if item["displayName"].casefold() == name.casefold()]
            identifier = self.state["items"].get(role)
            if any(item["id"] != identifier for item in matches):
                raise ValueError(f"Unmanaged name collision for {role}. Nothing will be adopted or overwritten.")
            if identifier:
                found = [item for item in inventory if item["id"] == identifier]
                expected_type = self.catalog[role]["type"] if role in self.catalog else "Eventstream"
                if len(found) != 1 or found[0]["displayName"] != name or found[0]["type"] != expected_type:
                    raise ValueError(f"Tracked {role} identity changed or was deleted.")
                if role in self.catalog and found[0].get("description") != self.spec["owner"]:
                    raise ValueError(f"Tracked {role} ownership changed.")
        return {"workspace": workspace["displayName"], "capacity_sku": capacities[0]["sku"]}

    def record_import(self, result, roles):
        receipts = result["importItemDefinitionsDetails"]
        seen, failures = set(), []
        for receipt in receipts:
            matches = [role for role in roles if self.catalog[role]["logical_id"] == receipt["itemLogicalId"]]
            if len(matches) != 1 or matches[0] in seen:
                raise ValueError("Native import returned an unknown or duplicate receipt.")
            role = matches[0]
            seen.add(role)
            item = self.catalog[role]
            if receipt["itemDisplayName"] != item["name"] or receipt["itemType"] != item["type"]:
                raise ValueError("Native import receipt identity differs.")
            if receipt["operationStatus"] == "Succeeded":
                self.state["items"][role] = str(uuid.UUID(receipt["itemId"]))
            else:
                failures.append(role)
        self.state["last_import_receipts"] = receipts
        self.save()
        if seen != set(roles) or failures:
            raise RuntimeError("Native import is incomplete. Inspect the saved receipts; do not resubmit blindly.")
        self.state.pop("pending_import", None)
        self.save()

    def import_items(self, roles):
        roles = [role for role in roles if role not in self.state["items"]]
        if not roles:
            return
        if self.state.get("pending_import"):
            raise ValueError("An import is unresolved. Use the explicit resume command with this ledger.")
        recipients = [
            self.api.tokens.current_user_address(self.api.control_resource) if value == "@me" else value
            for value in self.config["teams_recipients"]
        ]
        payload = native.bulk_payload(roles, self.config, self.state["items"], recipients)
        self.state["resolved_recipients"] = recipients
        self.state["pending_import"] = {"roles": roles, "request_started": True}
        self.save()
        reply = self.api.request("POST", self.base + "items/bulkImportDefinitions", payload)
        if reply.status == 202:
            location = reply.headers.get("Location")
            if not location:
                raise RuntimeError("Import accepted without a polling Location. Inspect the workspace before retrying.")
            self.api.check_url(location, self.api.control_resource)
            self.state["pending_import"]["location"] = location
            self.save()
        self.record_import(self.api.complete(reply), roles)

    def resume_import(self):
        pending = self.state.get("pending_import")
        if not pending or not pending.get("location"):
            raise ValueError("No resumable operation Location. Inspect the incomplete attempt, not an older deployment.")
        self.record_import(self.api.complete(Reply(
            202, {"Location": pending["location"], "Retry-After": "0"}, {},
        )), pending["roles"])

    def readback(self, role):
        route = self.base + f"items/{self.state['items'][role]}/getDefinition"
        if self.catalog[role]["type"] == "SemanticModel":
            route = self.base + f"semanticModels/{self.state['items'][role]}/getDefinition?format=TMSL"
        actual = self.api.complete(self.api.request(
            "POST", route,
        ))["definition"]
        expected = native.definition(
            role, self.config, self.state["items"], self.state.get("resolved_recipients"),
        )
        notes = native.assert_definition(expected, actual)
        if notes:
            self.state.setdefault("definition_readback_notes", {})[role] = notes
            self.save()

    def grant_access(self):
        schema_id = self.state["items"]["schema"]
        host = {
            "daily": "https://daily-onelake.pbidedicated.windows.net",
            "msit": "https://msit-onelake.pbidedicated.windows.net",
        }[self.config["environment"]]
        reply = self.api.request(
            "POST", f"{host}/v1.0/workspaces/{self.config['workspace_id']}/artifacts/{schema_id}/security/enable",
            {"enableOneSecurity": True}, resource=STORAGE,
        )
        if reply.status not in (200, 204):
            raise RuntimeError("Data access security is not yet enabled.")
        path = self.base + f"items/{schema_id}/dataAccessRoles"
        previous = self.api.request("GET", path)
        roles = copy.deepcopy(previous.body["value"])
        name = "BusinessEventsScenarioPublisherConsumer"
        member = self.api.tokens.identity(self.api.control_resource)
        managed = next((role for role in roles if role["name"] == name), None)
        if managed is None:
            managed = {"name": name, "members": {"microsoftEntraMembers": []}}
            roles.append(managed)
        managed["decisionRules"] = [{
            "effect": "Permit", "permission": [
                {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
                {"attributeName": "Action", "attributeValueIncludedIn": ["Publish", "Consume"]},
            ],
        }]
        members = managed["members"]["microsoftEntraMembers"]
        if not any(all(existing.get(key) == member[key] for key in ("tenantId", "objectId")) for existing in members):
            members.append(member)
        self.api.request("PUT", path, {"value": roles}, extra_headers=(
            {"If-Match": previous.headers["ETag"]} if previous.headers.get("ETag") else {}
        ))
        actual = self.api.request("GET", path).body["value"]
        matches = [role for role in actual if role["name"] == name]
        if len(matches) != 1:
            raise ValueError("Publish/Consume permission readback is missing or ambiguous.")
        policies = [{key: role[key] for key in ("name", "decisionRules", "members")} for role in (managed, matches[0])]
        for role in policies:
            for identity in role["members"]["microsoftEntraMembers"]:
                identity.pop("objectType", None)
        native.assert_subset(*policies)

    def connect_history(self, create):
        def mark_pending(pending):
            self.state["pending_history_connection"] = pending
            self.save()

        result = connections.ensure(
            self.api, self.config["workspace_id"], self.state["items"], create=create,
            pending=self.state.get("pending_history_connection", False),
            on_pending=mark_pending, known=self.state.get("history_connection"),
            event_name=self.config["event_name"],
        )
        self.state["history_connection"] = result
        self.state.pop("pending_history_connection", None)
        self.save()
