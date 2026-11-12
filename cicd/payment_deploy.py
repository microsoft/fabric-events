"""Native Eventstream payment filtering through documented public APIs."""

import base64
import copy
import datetime
import hashlib
import hmac
import json
import math
import re
import struct
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener
import uuid

from public_api import NoRedirect
from action_deploy import ActionDeployment
from public_consumers import definition_part, matches_definition, part
from public_deploy import read

FIELDS = ("transaction_id", "payment_method", "amount", "currency", "store_id", "processed_at")


def fixture_payloads(fixtures, run_id, processed_at):
    run_id = str(uuid.UUID(run_id))
    instant = datetime.datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
    if instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Fixture timestamps must use UTC.")
    identifiers = set()
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide a nonempty list of payment fixtures.")
    result = []
    for row in fixtures:
        if not isinstance(row, dict) or set(row) != {"id", "payment_method", "amount", "currency", "store_id"}:
            raise ValueError("Payment fixtures must contain the exact reviewed fields.")
        identifier = row["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", identifier):
            raise ValueError("Fixture IDs must be portable lowercase labels.")
        if identifier in identifiers:
            raise ValueError("Payment fixture IDs must be distinct.")
        identifiers.add(identifier)
        amount = row["amount"]
        if (type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0
                or amount > 3.4028234663852886e38):
            raise ValueError("Payment amounts must be finite, nonnegative Avro float values.")
        for key in ("payment_method", "currency", "store_id"):
            if not isinstance(row[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row[key]):
                raise ValueError(f"Invalid payment fixture {key}.")
        result.append({"transaction_id": f"cicd-{run_id}-{identifier}", "processed_at": processed_at,
                       **{key: row[key] for key in ("payment_method", "amount", "currency", "store_id")}})
    return result


def source_connection(connection):
    if not isinstance(connection, dict) or connection.get("type") != "CustomEndpoint":
        raise ValueError("The owned source must return a public CustomEndpoint connection.")
    namespace, hub = connection["fullyQualifiedNamespace"], connection["eventHubName"]
    if (not isinstance(namespace, str) or not re.fullmatch(r"[a-z0-9-]+\.servicebus\.windows\.net", namespace)
            or not isinstance(hub, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", hub)):
        raise ValueError("The public source returned an unexpected Event Hubs destination.")
    values = {}
    for entry in connection["accessKeys"]["primaryConnectionString"].split(";"):
        if not entry:
            continue
        key, value = entry.split("=", 1)
        if key in values:
            raise ValueError("The Event Hubs connection repeats a credential field.")
        values[key] = value
    if (values.get("Endpoint") != f"sb://{namespace}/" or values.get("EntityPath") != hub
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", values.get("SharedAccessKeyName", ""))
            or not values.get("SharedAccessKey")
            or values["SharedAccessKey"] != connection["accessKeys"]["primaryKey"]):
        raise ValueError("The source connection string does not identify the exact returned endpoint and key.")
    return f"https://{namespace}/{quote(hub, safe='')}", values["SharedAccessKeyName"], values["SharedAccessKey"]


def send_event(connection, payload):
    endpoint, key_name, key = source_connection(connection)
    resource, expires = quote(endpoint, safe=""), str(int(time.time()) + 600)
    signature = base64.b64encode(hmac.new(
        key.encode("utf-8"), (resource + "\n" + expires).encode("utf-8"), hashlib.sha256,
    ).digest()).decode("ascii")
    token = f"SharedAccessSignature sr={resource}&sig={quote(signature, safe='')}&se={expires}&skn={quote(key_name, safe='')}"
    request = Request(endpoint + "/messages?timeout=60&api-version=2014-01",
                      data=json.dumps(payload, allow_nan=False).encode("utf-8"), method="POST",
                      headers={"Authorization": token, "Content-Type": "application/atom+xml;type=entry;charset=utf-8"})
    try:
        response = build_opener(NoRedirect()).open(request, timeout=90)
    except HTTPError as error:
        raise RuntimeError(f"Public Event Hubs input failed with HTTP {error.code}; retain this run, do not replay.") from None
    except (URLError, TimeoutError) as error:
        raise RuntimeError("Event Hubs input delivery is ambiguous. Retain the run; do not replay.") from None
    with response:
        if response.status != 201:
            raise RuntimeError("Event Hubs did not return its documented 201 input acceptance.")
        return response.status


def card_matches(content, payload):
    content = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", content)
    for key in FIELDS:
        if key == "amount":
            values = [float(value) for value in re.findall(
                r'amount[^A-Za-z0-9+-]*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)', content,
            )]
            # Avro float can round the source's JSON number to IEEE-754 binary32.
            allowed = (payload[key], struct.unpack("!f", struct.pack("!f", payload[key]))[0])
            if not any(value in allowed for value in values):
                return False
        elif key not in content or str(payload[key]) not in content:
            return False
    return True


def check_native_topology(expected, actual):
    if not isinstance(actual, dict) or actual.get("compatibilityLevel") != expected["compatibilityLevel"]:
        raise ValueError("The native Eventstream compatibility level differs.")
    for collection in ("sources", "streams", "operators", "destinations"):
        rows = actual.get(collection)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
                                             for row in rows):
            raise ValueError("The native Eventstream has an invalid node collection.")
        by_name = {row["name"]: row for row in rows}
        required = {row["name"]: row for row in expected[collection]}
        if len(by_name) != len(rows) or set(by_name) != set(required):
            raise ValueError("The native Eventstream graph has missing, extra or duplicate nodes.")
        if any(not matches_definition(row, by_name[name]) for name, row in required.items()):
            raise ValueError("The persisted native filter, six-field mapping or Business Event destination differs.")


class PaymentDeployment(ActionDeployment):
    resource_types = {
        "schema": "EventSchemaSet", "publisher": "Eventstream", "activator": "Reflex",
        "record_store": "Lakehouse", "response_function": "UserDataFunction",
    }

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("payment_public_version") != 2:
            raise ValueError("The ledger does not match this exercise's resources. No resource adoption is permitted.")
        self.state["payment_public_version"] = 2

    def stream_definition(self):
        definition = copy.deepcopy(read(self.folder / "eventstream.json"))
        definition["destinations"][0]["properties"].update(
            workspaceId=self.config["workspace_id"], itemId=self.state["items"]["schema"],
            businessEventTypeId=self.config["event_name"],
        )
        if "${" in json.dumps(definition):
            raise ValueError("The native Eventstream definition contains an unresolved binding.")
        return definition

    def creation_request(self, role):
        if role not in ("publisher", "activator"):
            return super().creation_request(role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "publisher":
            body["definition"] = {"parts": [
                part("eventstream.json", self.stream_definition()),
                part("eventstreamProperties.json", {"retentionTimeInDays": 1, "eventThroughputLevel": "Low"}),
            ]}
            return self.base + "eventstreams", body
        body.update(type="Reflex", definition={"parts": [part("ReflexEntities.json", self.activator_entities())]})
        return self.base + "items", body

    def verify_stream(self, allow_resume=False):
        definition = self.get_definition("publisher")
        expected = self.stream_definition()
        actual = json.loads(definition_part(definition, "eventstream.json"))
        check_native_topology(expected, actual)
        runtime_expected = copy.deepcopy(expected)
        # Topology omits destination inputSchemas; getDefinition verifies them above.
        for destination in runtime_expected["destinations"]:
            del destination["inputSchemas"]
        deadline = time.monotonic() + 600
        while True:
            topology = self.api.request("GET", self.base + f"eventstreams/{self.state['items']['publisher']}/topology").body
            self.state["stream_topology"] = topology
            self.save()
            check_native_topology(runtime_expected, topology)
            components = topology["sources"] + topology["destinations"]
            if any(row.get("error") or row["status"] in ("Failed", "Warning") for row in components):
                raise RuntimeError("The native Eventstream source or Business Events destination failed; inspect the saved topology.")
            if all(row["status"] == "Running" for row in components):
                self.state.setdefault("definition_backup", {})["publisher"] = definition
                return topology
            if any(row["status"] == "Paused" for row in components):
                if not allow_resume:
                    raise ValueError("The owned Eventstream is paused. Resume deployment before testing.")
                start = self.state.setdefault("stream_start", {})
                if not start:
                    start["phase"] = "request_started"
                    self.save()
                    reply = self.api.request(
                        "POST", self.base + f"eventstreams/{self.state['items']['publisher']}/resume",
                        {"startType": "WhenLastStopped"},
                    )
                    start.update(phase="accepted", actual_status=reply.status)
                    self.save()
            if any(row["status"] not in ("Running", "Creating", "Created", "Updating", "Paused", "Resuming")
                   for row in components):
                raise RuntimeError("The Eventstream has an unexpected status; no input will be submitted.")
            if time.monotonic() >= deadline:
                raise TimeoutError("The Eventstream is not running. Resume this exact deployment; no inputs were submitted.")
            self.api.sleep(5)

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy the complete schema, Eventstream publisher and Activator before testing.")
        self.verify_schema()
        self.roles()
        self.check_activator(self.get_definition("activator"))
        self.verify_stream()
        self.verify_response()
        result = {"status": "payment_public_provisioning_verified_e2e_pending", "items": self.state["items"],
                  "source_binding_verified": True, "e2e_verified": False,
                  "eventhouse_subscription_configured": False}
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Use the shared runner for explicit schema-only foundations.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public create, never start it again.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("record_store")
        self.prepare_record_store()
        self.create("response_function")
        self.create("activator")
        self.start_activator()
        self.create("publisher")
        self.verify_stream(allow_resume=True)
        result = self.verify_provisioning()
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("This scenario uses Activator (User Data Function), not a Teams chat.")
        if self.state.get("test_run"):
            raise ValueError("This deployment already has a test attempt. Use verify; do not replay its inputs.")
        result = self.verify_provisioning()
        if self.list_records():
            raise ValueError("A fresh source test requires an empty owned operational record folder.")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        payloads = fixture_payloads(read(self.folder / "fixtures.json"), run_id, now)
        source_id = str(uuid.UUID(self.state["stream_topology"]["sources"][0]["id"]))
        connection = self.api.request(
            "GET", self.base + f"eventstreams/{self.state['items']['publisher']}/sources/{source_id}/connection",
        ).body
        source_connection(connection)
        attempt = {"run_id": run_id, "started_at": now, "payloads": payloads,
                   "chat_id": chat_id, "phase": "submitting_inputs", "accepted_inputs": []}
        self.state["test_run"] = attempt
        self.save()
        for payload in payloads:
            attempt["pending_input"] = payload["transaction_id"]
            self.save()
            status = send_event(connection, payload)
            attempt["accepted_inputs"].append({"transaction_id": payload["transaction_id"], "status": status})
            attempt.pop("pending_input")
            self.save()
        attempt["phase"] = "inputs_accepted_delivery_pending"
        self.save()
        result.update(
            status="source_triggered_verification_pending", source_triggered=True,
            source_run_id=run_id, source_inputs_accepted=len(attempt["accepted_inputs"]),
            source_item_id=self.state["items"]["publisher"],
            transaction_ids=[payload["transaction_id"] for payload in payloads],
            events_published=None, e2e_verified=False, outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("This scenario uses Activator (User Data Function), not a Teams chat.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if not attempt:
            return result
        if attempt.get("pending_input") or len(attempt["accepted_inputs"]) != len(attempt["payloads"]):
            raise ValueError("Input acceptance is incomplete or ambiguous. Retain this run; no inputs will be replayed.")
        result.update(source_run_id=attempt["run_id"], source_inputs_accepted=len(attempt["accepted_inputs"]),
                      events_published=None, status="payment_public_inputs_accepted_delivery_pending")
        expected = [
            {**row, "amount": float(row["amount"])}
            for row in attempt["payloads"] if row["amount"] > 5000
        ]
        excluded = [row for row in attempt["payloads"] if row["amount"] <= 5000]
        return self.verify_business_outcomes(attempt, result, expected, excluded)
