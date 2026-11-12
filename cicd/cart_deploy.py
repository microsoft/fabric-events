"""Original cart publisher with independent email and SQL follow-up responses."""

import datetime
import json
import math
import re
import uuid

from public_consumers import subscription_entities
from public_deploy import read, sql_database_destination
from public_udf import UdfEventSourceDeployment, publisher_definition, verify_publisher_definition

FUNCTION = "publish_cart_abandoned_event"
PARAMETERS = {
    "customerId": ("customer_id", "str"), "customerEmail": ("customer_email", "str"),
    "cartId": ("cart_id", "str"), "cartValue": ("cart_value", "float"),
    "itemCount": ("item_count", "int"), "lastActivityAt": ("last_activity_at", "str"),
}


def fixture_payloads(fixtures, run_id, last_activity_at, recipient):
    run_id = str(uuid.UUID(run_id))
    instant = datetime.datetime.fromisoformat(last_activity_at.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Cart fixture timestamps must use timezone-aware UTC.")
    if (not isinstance(recipient, str) or len(recipient) > 254
            or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", recipient)):
        raise ValueError("Set test_recipient to a mailbox approved for these test emails before testing.")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide nonempty synthetic cart fixtures.")
    inputs, identifiers = [], set()
    for row in fixtures:
        if not isinstance(row, dict) or set(row) != {"id", "customer_id", "cart_value", "item_count", "expected_email"}:
            raise ValueError("Cart fixtures require the exact reviewed fields.")
        label = row["id"]
        if not isinstance(label, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", label) or label in identifiers:
            raise ValueError("Cart fixture IDs must be distinct portable labels.")
        identifiers.add(label)
        if not isinstance(row["customer_id"], str) or not re.fullmatch(r"test-[a-z0-9-]{1,60}", row["customer_id"]):
            raise ValueError("Use synthetic test customer identifiers.")
        value = row["cart_value"]
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1_000_000_000_000
                or type(row["item_count"]) is not int or not 1 <= row["item_count"] <= 2_147_483_647):
            raise ValueError("Cart fixtures require finite nonnegative values and positive Avro int item counts.")
        if type(row["expected_email"]) is not bool or row["expected_email"] != (value > 30):
            raise ValueError("Cart email expectations must match the strict cart_value > 30 rule.")
        event = {
            "customer_id": row["customer_id"], "customer_email": recipient,
            "cart_id": f"cicd-{run_id}-{label}", "cart_value": float(value),
            "item_count": row["item_count"],
            "last_activity_at": instant.isoformat().replace("+00:00", "Z"),
        }
        inputs.append({"fixture_id": label, "parameters": {
            name: event[field] for name, (field, _) in PARAMETERS.items()
        }, "expected_event": event, "expected_email": row["expected_email"], "expected_sql_task": True})
    return inputs


def invocation_output(reply):
    envelope = reply.body
    if (reply.status != 200 or not isinstance(envelope, dict) or envelope.get("status") != "Succeeded"
            or envelope.get("errors") not in (None, [])
            or envelope.get("functionName", FUNCTION) != FUNCTION):
        raise RuntimeError("The cart publisher did not confirm successful execution. Retain the attempt.")
    output = envelope.get("output")
    if isinstance(output, str):
        output = json.loads(output)
    if (not isinstance(output, dict) or set(output) != {"event_published"} or output["event_published"] is not True
            or not isinstance(envelope.get("invocationId"), str) or not envelope["invocationId"].strip()):
        raise RuntimeError("The cart publisher did not confirm its publication request and invocation identity.")
    return output


class CartDeployment(UdfEventSourceDeployment):
    resource_types = {**UdfEventSourceDeployment.resource_types, "task_store": "SQLDatabase"}

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("cart_public_version") != 2:
            raise ValueError("The ledger does not match the Cart implementation. No adoption is permitted.")
        self.state["cart_public_version"] = 2

    def creation_request(self, role):
        if role == "task_store":
            return self.base + "items", {
                "displayName": self.config["resource_names"][role], "description": self.owner,
                "type": "SQLDatabase",
            }
        return super().creation_request(role)

    def udf_definition(self):
        return publisher_definition(
            self.folder, self.config, self.state["items"]["schema"], FUNCTION,
            "Publish the six original cart abandonment facts supplied by the storefront.", PARAMETERS,
        )

    def activator_entities(self):
        return subscription_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"],
            "Abandoned carts", "Cart abandonment events",
        )

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy the Cart schema, native publisher, event source and SQL database before testing.")
        self.verify_schema()
        self.roles()
        verify_publisher_definition(self)
        manual_entities = self.verify_event_source()
        self.state["sql_destination"] = sql_database_destination(self, "task_store")
        result = {
            "status": "manual_setup_required", "items": self.state["items"], "source_binding_verified": True,
            "udf_metadata_readback": self.state["udf_metadata_readback"],
            "manual_consumer_entities_observed": manual_entities, "manual_consumer_actions_verified": False,
            "email_delivery_verified": False, "sql_tasks_verified": False,
            "sql_destination": self.state["sql_destination"], "sql_schema_verified": False,
            "manual_setup_required": ["run_sql_setup_scripts", "two_power_automate_actions_and_rules",
                                      "approved_test_mailbox_and_sql_connection"],
            "eventhouse_subscription_configured": False, "e2e_verified": False,
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Cart prepares its native publisher and subscription, not unused history containers.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public create; never repeat it.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("task_store")
        self.create("publisher")
        self.create("activator")
        result = self.verify_provisioning()
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Cart uses email and SQL follow-up responses, not a Teams chat.")
        if self.state.get("test_run"):
            raise ValueError("This Cart deployment already has a source attempt. Do not replay its carts.")
        run_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc)
        inputs = fixture_payloads(
            read(self.folder / "fixtures.json"), run_id,
            (now - datetime.timedelta(minutes=45)).isoformat(), self.config["test_recipient"],
        )
        result = self.verify_provisioning()
        attempt = {"run_id": run_id, "started_at": now.isoformat().replace("+00:00", "Z"),
                   "inputs": inputs, "phase": "invoking_native_publisher", "invocations": []}
        self.state["test_run"] = attempt
        self.save()
        for row in inputs:
            attempt["pending_input"] = row["fixture_id"]
            self.save()
            reply = self.api.request(
                "POST", self.base + f"userDataFunctions/{self.state['items']['publisher']}/functions/{FUNCTION}/invoke",
                row["parameters"],
            )
            attempt["last_response"] = {"http_status": reply.status, "body": reply.body}
            self.save()
            output = invocation_output(reply)
            if any(saved["response"]["invocationId"] == reply.body["invocationId"] for saved in attempt["invocations"]):
                raise RuntimeError("The cart publisher returned a repeated invocation identity.")
            attempt["invocations"].append({"fixture_id": row["fixture_id"], "response": reply.body, "output": output})
            attempt.pop("pending_input")
            self.save()
        attempt["phase"] = "native_publication_requests_succeeded_delivery_pending"
        result.update(
            status="source_triggered_verification_pending", source_triggered=True, source_run_id=run_id,
            source_execution_verified=True, publication_requests=len(inputs), events_published=None,
            expected_carts=inputs, expected_emails=sum(row["expected_email"] for row in inputs),
            expected_sql_tasks=len(inputs), outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Cart requires actual email and SQL task evidence, not a Teams chat.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if attempt:
            if attempt.get("pending_input") or len(attempt["invocations"]) != len(attempt["inputs"]):
                raise ValueError("Cart source execution is incomplete or ambiguous. Retain it without replay.")
            for row, invocation in zip(attempt["inputs"], attempt["invocations"]):
                if row["fixture_id"] != invocation["fixture_id"]:
                    raise ValueError("Saved cart source evidence does not match the original inputs.")
            result.update(source_run_id=attempt["run_id"], source_execution_verified=True,
                          publication_requests=len(attempt["invocations"]), events_published=None)
        self.state["verification"] = result
        self.save()
        return result
