"""Deploy native definitions, or explicitly inject and verify one external test batch."""

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
import uuid

import connections
import native
from fabric_api import FabricApi, KUSTO, STORAGE, Reply

ROOT = Path(__file__).resolve().parent


def load_config(path):
    return native.validate_config(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def fingerprint(config):
    return {key: copy.deepcopy(value) for key, value in config.items() if key != "$schema"}


def plan(config):
    spec = native.manifest()
    for role in spec["items"]:
        native.definition(role, config)
    return {
        "mode": "offline-plan", "cloud_writes": False, "scenario_id": spec["scenario_id"],
        "environment": config["environment"], "workspace_id": config["workspace_id"],
        "event_name": config["event_name"], "source_backend": config["source_backend"],
        "resources": config["resource_names"], "publisher_executes_during_deploy": False,
        "dummy_data": "External fixtures injected only by test.bat.",
        "manual_eventstream_subscription_required": spec["manual_eventstream"],
        "automation_limit": spec.get("automation_limit"),
    }


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
        actual = self.api.complete(self.api.request(
            "POST", self.base + f"items/{self.state['items'][role]}/getDefinition",
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

    def check_topology(self):
        if self.spec["manual_eventstream"]:
            return
        path = self.base + f"eventstreams/{self.state['items']['publisher']}/topology"
        expected = json.loads(native.decoded(
            native.definition("publisher", self.config, self.state["items"]), "eventstream.json",
        ))
        deadline = time.monotonic() + 180
        while True:
            topology = self.api.request("GET", path).body
            for key in ("sources", "destinations"):
                # Topology omits destination inputSchemas; getDefinition verifies them.
                runtime_nodes = [
                    {field: value for field, value in node.items() if field != "inputSchemas"}
                    for node in expected[key]
                ]
                native.assert_subset(runtime_nodes, topology[key])
            nodes = topology["sources"] + topology["destinations"]
            if any(node.get("error") or node.get("status") in ("Failed", "Warning", "Stopped") for node in nodes):
                raise RuntimeError("Eventstream is not healthy. Inspect topology; no test data was injected.")
            if nodes and all(node.get("status") == "Running" for node in nodes):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Eventstream readiness is unresolved. Resume this attempt; do not submit test data.")
            self.api.sleep(3)

    def deploy(self, resume=False):
        if self.state.get("pending_import"):
            if not resume:
                raise ValueError("Use resume for this pending operation.")
            self.resume_import()
        self.preflight()
        self.import_items(("schema", "eventhouse", "database"))
        self.readback("schema")
        self.grant_access()
        database = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["database"]).body
        if database["properties"]["parentEventhouseItemId"] != self.state["items"]["eventhouse"]:
            raise ValueError("Native database parent was not bound to this deployment's Eventhouse.")
        self.api.check_url(database["properties"]["queryServiceUri"], KUSTO)
        self.connect_history(create=True)
        self.import_items(("activator",))
        self.import_items(("publisher",))
        for role in self.catalog:
            self.readback(role)
        self.check_topology()
        self.preflight()
        self.state["status"] = (
            "manual_eventstream_subscription_required"
            if self.spec["manual_eventstream"] and not self.state.get("manual_source_attestation")
            else "configured_delivery_unverified"
        )
        self.save()
        return {
            "status": self.state["status"], "items": self.state["items"],
            "ledger": str(self.state_path), "events_published": 0, "dummy_rows_loaded": 0,
            "manual_step": self.spec.get("automation_limit"),
            "definition_readback_notes": self.state.get("definition_readback_notes", {}),
        }

    def acknowledge_eventstream(self, identifier):
        if not self.spec["manual_eventstream"] or self.state.get("status") != "manual_eventstream_subscription_required":
            raise ValueError("This deployment is not awaiting the manual Eventstream subscription.")
        identifier = str(uuid.UUID(identifier))
        item = self.api.request("GET", self.base + "items/" + identifier).body
        if item["type"] != "Eventstream" or item["displayName"] != self.config["resource_names"]["eventstream"]:
            raise ValueError("The explicitly selected Eventstream does not match this wizard's resource.")
        self.state["items"]["eventstream"] = identifier
        self.state["manual_source_attestation"] = {
            "eventstream_id": identifier, "event_name": self.config["event_name"],
            "schema_id": self.state["items"]["schema"],
            "evidence": "operator_attested_portal_configuration_not_api_verified",
        }
        self.state["status"] = "configured_delivery_unverified"
        self.save()
        return {"status": self.state["status"], "source_evidence": self.state["manual_source_attestation"]}

    def make_batch(self):
        cases = json.loads((ROOT / "data" / "test-cases.json").read_text(encoding="utf-8"))
        run_id = str(uuid.uuid4())
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        inputs, expected = [], []
        for case in cases:
            payload = copy.deepcopy(case["input"])
            payload[self.spec["identity_field"]] = f"cicd-{run_id}-{case['id']}"
            payload[self.spec["time_field"]] = stamp
            inputs.append(payload)
            if case["expected"] is not None:
                expected.append({**payload, **case["expected"]})
        return {
            "run_id": run_id, "inputs": inputs, "expected": expected,
            "status": "publication_started", "invocations": [],
        }

    def publish_payments(self, batch):
        try:
            from azure.eventhub import EventData, EventHubProducerClient
            from azure.core.exceptions import AzureError
        except ImportError:
            raise RuntimeError("Install the test-only dependency with: python -m pip install -r requirements.txt") from None
        topology = self.api.request(
            "GET", self.base + f"eventstreams/{self.state['items']['publisher']}/topology",
        ).body
        sources = [source for source in topology["sources"] if source["type"] == "CustomEndpoint"]
        if len(sources) != 1:
            raise ValueError("Expected the native payment custom endpoint, not a replacement publisher.")
        connection = self.api.request(
            "GET", self.base + f"eventstreams/{self.state['items']['publisher']}/sources/{sources[0]['id']}/connection",
        ).body
        if connection["type"] != "CustomEndpoint":
            raise ValueError("Unexpected Eventstream test endpoint.")
        secret = connection["accessKeys"]["primaryConnectionString"]
        try:
            with EventHubProducerClient.from_connection_string(
                secret, eventhub_name=connection["eventHubName"], retry_total=0,
            ) as producer:
                messages = producer.create_batch()
                for payload in batch["inputs"]:
                    messages.add(EventData(json.dumps(payload, allow_nan=False)))
                producer.send_batch(messages)
        except (AzureError, ValueError):
            raise RuntimeError(
                "Dummy-input delivery failed or is ambiguous. Credentials were not logged. "
                "Inspect the saved batch; do not blindly republish."
            ) from None
        batch["status"] = "inputs_submitted_consumers_unverified"

    def publish_discounts(self, batch):
        path = self.base + f"userDataFunctions/{self.state['items']['publisher']}/functions/evaluate_discount_request/invoke"
        for payload in batch["inputs"]:
            parameters = {target: payload[source] for source, target in self.spec["parameter_map"].items()}
            attempt = {"id": payload[self.spec["identity_field"]], "status": "invocation_started"}
            batch["invocations"].append(attempt)
            self.save()
            response = self.api.request("POST", path, parameters).body
            if response.get("status") != "Succeeded":
                raise RuntimeError("UDF invocation did not succeed. Inspect the saved attempt before another test.")
            output = response.get("output")
            if isinstance(output, str):
                output = json.loads(output)
            expected = next((
                item for item in batch["expected"]
                if item[self.spec["identity_field"]] == payload[self.spec["identity_field"]]
            ), None)
            level = expected["approval_level"] if expected else "none"
            if not isinstance(output, dict) or output.get("approval_level") != level or (
                output.get("event_published") is not (expected is not None)
            ):
                raise ValueError("UDF result does not match the fixture's policy expectation.")
            attempt.update(status="succeeded", approval_level=level, event_published=output["event_published"])
            self.save()
        batch["status"] = "inputs_submitted_consumers_unverified"

    def run_test(self, new_run=False):
        if self.state.get("pending_import") or self.state.get("pending_history_connection"):
            raise ValueError("Deployment has an unresolved operation.")
        if self.state.get("status") != "configured_delivery_unverified":
            raise ValueError("Complete deployment, including its explicit manual source step, before testing.")
        if self.state.get("last_run", {}).get("status") == "publication_started" and not new_run:
            raise ValueError("A previous test is ambiguous. Inspect it; --new-run explicitly starts a different batch.")
        self.preflight()
        self.connect_history(create=False)
        for role in ("activator", "publisher"):
            self.readback(role)
        self.check_topology()
        batch = self.make_batch()
        if self.state.get("last_run"):
            self.state.setdefault("test_history", []).append(self.state["last_run"])
        self.state["last_run"] = batch
        self.save()
        if self.spec["manual_eventstream"]:
            self.publish_discounts(batch)
        else:
            self.publish_payments(batch)
        self.save()
        return {"status": batch["status"], "run_id": batch["run_id"], "expected_events": len(batch["expected"])}

    def history_rows(self, batch):
        run_id = str(uuid.UUID(batch["run_id"]))
        database = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["database"]).body
        endpoint = database["properties"]["queryServiceUri"].rstrip("/") + "/v1/rest/query"
        self.api.check_url(endpoint, KUSTO)
        fields = ", ".join(self.spec["fields"])
        query = (
            f"['{self.config['event_name']}']\n"
            f'| where {self.spec["identity_field"]} startswith "cicd-{run_id}-"\n'
            f"| summarize Deliveries=count() by {fields}"
        )
        response = self.api.request("POST", endpoint, {"db": database["id"], "csl": query}, resource=KUSTO).body
        tables = [
            table for table in response["Tables"]
            if self.spec["identity_field"] in {column["ColumnName"] for column in table["Columns"]}
        ]
        if len(tables) != 1 or response.get("error") or response.get("Exceptions"):
            raise ValueError("Eventhouse did not return a complete test result.")
        table = tables[0]
        columns = [column["ColumnName"] for column in table["Columns"]]
        return [dict(zip(columns, row)) for row in table["Rows"]]

    def validate_rows(self, rows, batch):
        if len(rows) != len(batch["expected"]):
            raise ValueError("History contains missing events, unexpected events, or payload variants.")
        contract = json.loads(native.decoded(
            native.definition("schema", self.config), "EventSchemaSetDefinition.json",
        ))
        avro = json.loads(contract["schemas"][0]["versions"][0]["schema"])
        types = {field["name"]: field["type"] for field in avro["fields"]}
        for expected in batch["expected"]:
            matches = [row for row in rows if row[self.spec["identity_field"]] == expected[self.spec["identity_field"]]]
            if len(matches) != 1:
                raise ValueError("An expected event is missing or ambiguous.")
            actual = matches[0]
            if not isinstance(actual.get("Deliveries"), int) or actual["Deliveries"] < 1:
                raise ValueError("Invalid Eventhouse delivery count.")
            for field, value in expected.items():
                received = actual.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if isinstance(received, bool) or not isinstance(received, (int, float)) or not math.isclose(
                        value, received,
                        rel_tol=1e-7 if types[field] == "float" else 0,
                        abs_tol=0.0001 if types[field] == "float" else 1e-8,
                    ):
                        raise ValueError(f"Eventhouse payload mismatch for {field}.")
                elif received != value:
                    raise ValueError(f"Eventhouse payload mismatch for {field}.")

    def verify(self, teams_confirmed=False, eventstream_confirmed=False, wait_seconds=120):
        batch = self.state.get("last_run")
        if not batch:
            raise ValueError("No saved test batch. Verification never creates or publishes one.")
        if eventstream_confirmed and not self.spec["manual_eventstream"]:
            raise ValueError("This scenario has no Eventstream consumer to attest.")
        deadline = time.monotonic() + wait_seconds
        while True:
            rows = self.history_rows(batch)
            try:
                self.validate_rows(rows, batch)
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                self.api.sleep(5)
                continue
            break
        submitted = batch["status"] == "inputs_submitted_consumers_unverified"
        complete = submitted and teams_confirmed and (eventstream_confirmed or not self.spec["manual_eventstream"])
        evidence = {
            "status": "verified_with_operator_attestations" if complete else "history_verified_remaining_evidence_required",
            "run_id": batch["run_id"], "eventhouse": "payloads_verified",
            "publication": batch["status"], "distinct_events": len(rows),
            "received_records": sum(row["Deliveries"] for row in rows),
            "teams": "operator_attested" if teams_confirmed else "not_verified",
            "eventstream_consumer": (
                "operator_attested" if eventstream_confirmed else "not_verified"
            ) if self.spec["manual_eventstream"] else "not_applicable",
            "nonmatching_events": "not_observed_in_current_history_query",
        }
        batch["verification"] = evidence
        self.save()
        return evidence, 0 if complete else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "preflight", "deploy", "resume", "acknowledge-eventstream", "run", "verify"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.card.example.json")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--confirm-workspace")
    parser.add_argument("--eventstream-id")
    parser.add_argument("--teams-confirmed", action="store_true")
    parser.add_argument("--eventstream-confirmed", action="store_true")
    parser.add_argument("--new-run", action="store_true")
    args = parser.parse_args(argv)
    if (args.teams_confirmed or args.eventstream_confirmed) and args.command != "verify":
        raise ValueError("Delivery attestations belong only to verification, never publication or deployment.")
    if args.new_run and args.command != "run":
        raise ValueError("--new-run belongs only to an explicitly requested test publication.")
    if bool(args.eventstream_id) != (args.command == "acknowledge-eventstream"):
        raise ValueError("--eventstream-id is required only for the explicit portal configuration attestation.")
    config = load_config(args.config)
    if args.command == "plan":
        result = plan(config)
        code = 0
    else:
        if not args.state_file:
            raise ValueError("Select the exact deployment --state-file; no historical ledger is inferred.")
        if args.command not in ("preflight", "verify") and args.confirm_workspace != config["workspace_id"]:
            raise ValueError("Supply the approved --confirm-workspace UUID after reviewing the plan.")
        target = Deployment(config, args.state_file)
        code = 0
        if args.command == "preflight":
            result = target.preflight()
        elif args.command in ("deploy", "resume"):
            result = target.deploy(resume=args.command == "resume")
            code = 3 if result["status"] == "manual_eventstream_subscription_required" else 0
        elif args.command == "acknowledge-eventstream":
            if not args.eventstream_id:
                raise ValueError("Select the explicitly configured portal --eventstream-id.")
            result = target.acknowledge_eventstream(args.eventstream_id)
        elif args.command == "run":
            result = target.run_test(args.new_run)
        else:
            result, code = target.verify(args.teams_confirmed, args.eventstream_confirmed)
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
