"""Public Fraud decision publisher with a manual Power Automate and OMS handoff."""

import datetime
import json
import math
import re
import uuid

from public_consumers import subscription_entities
from public_deploy import read
from public_udf import UdfEventSourceDeployment, publisher_definition, verify_publisher_definition

FUNCTION = "evaluate_order_risk"
PARAMETERS = {
    "orderId": ("order_id", "str"), "customerId": ("customer_id", "str"),
    "riskScore": ("risk_score", "float"), "decidedAt": ("decided_at", "str"),
}
DECISIONS = ("hold", "manual_review", "release")


def fixture_payloads(fixtures, run_id, decided_at):
    run_id = str(uuid.UUID(run_id))
    instant = datetime.datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Fraud source timestamps must use timezone-aware UTC.")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide nonempty synthetic order fixtures.")
    inputs, identifiers = [], set()
    for row in fixtures:
        if not isinstance(row, dict) or set(row) != {"id", "input", "expected"}:
            raise ValueError("Fraud fixtures must contain the exact reviewed fields.")
        name, source, expected = row["id"], row["input"], row["expected"]
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", name)
                or name in identifiers):
            raise ValueError("Fraud fixture IDs must be distinct portable labels.")
        identifiers.add(name)
        if (not isinstance(source, dict) or set(source) != {"customer_id", "risk_score"}
                or not isinstance(source["customer_id"], str)
                or not source["customer_id"].startswith("test-") or len(source["customer_id"]) > 128):
            raise ValueError("Use exact synthetic customer and risk fields.")
        score = source["risk_score"]
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Fraud fixture risk scores must be finite numbers between 0 and 1.")
        if not isinstance(expected, dict) or set(expected) != {"decision"} or expected["decision"] not in DECISIONS:
            raise ValueError("Fraud fixture expectations must specify one reviewed decision.")
        event = {
            "order_id": f"cicd-{run_id}-{name}", "customer_id": source["customer_id"].strip(),
            "risk_score": float(score), "decision": expected["decision"],
            "decided_at": instant.isoformat().replace("+00:00", "Z"),
        }
        inputs.append({"parameters": {name: event[field] for name, (field, _) in PARAMETERS.items()},
                       "expected_event": event})
    return inputs


def invocation_output(reply, expected):
    envelope = reply.body
    if (reply.status != 200 or not isinstance(envelope, dict) or envelope.get("status") != "Succeeded"
            or envelope.get("errors") not in (None, [])
            or "functionName" in envelope and envelope["functionName"] != FUNCTION):
        raise RuntimeError("The Fraud UDF did not confirm successful execution. Retain the attempt.")
    output = envelope.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError("The Fraud UDF output is not valid JSON.") from error
    if (not isinstance(output, dict) or output.get("decision") != expected["decision"]
            or output.get("event_published") is not True):
        raise RuntimeError("The Fraud UDF result differs from the original decision policy.")
    if "invocationId" in envelope and (
        not isinstance(envelope["invocationId"], str) or not envelope["invocationId"].strip()
    ):
        raise RuntimeError("The Fraud UDF returned an invalid invocation identity.")
    return output


class FraudDeployment(UdfEventSourceDeployment):
    resource_types = {"schema": "EventSchemaSet", "publisher": "UserDataFunction", "activator": "Reflex"}

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("fraud_public_version") != 1:
            raise ValueError("The ledger does not match the Fraud public publisher. No resource adoption is permitted.")
        self.state["fraud_public_version"] = 1

    def udf_definition(self):
        return publisher_definition(
            self.folder, self.config, self.state["items"]["schema"], FUNCTION,
            "Apply the supplied order risk score to the hold, manual review and release policy.", PARAMETERS,
        )

    def activator_entities(self):
        return subscription_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"],
            "Fraud decisions", "Order risk decisions",
        )

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy the Fraud schema, publisher and event source before testing.")
        self.verify_schema()
        self.roles()
        verify_publisher_definition(self)
        manual_entities = self.verify_event_source()
        result = {
            "status": "manual_setup_required", "items": self.state["items"],
            "source_binding_verified": True, "manual_consumer_entities_observed": manual_entities,
            "udf_metadata_readback": self.state["udf_metadata_readback"],
            "manual_consumer_actions_verified": False, "oms_enforcement_verified": False,
            "manual_setup_required": ["three_power_automate_actions", "approved_test_order_system"],
            "eventhouse_subscription_configured": False, "e2e_verified": False,
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Fraud deploys its native publisher and event source, not unused history containers.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public create; never repeat it.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("activator")
        self.create("publisher")
        result = self.verify_provisioning()
        if "source_plan" not in self.state:
            run_id = str(uuid.uuid4())
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            inputs = fixture_payloads(read(self.folder / "fixtures.json"), run_id, now)
            self.state["source_plan"] = {"run_id": run_id, "orders": [
                {key: row["expected_event"][key] for key in ("order_id", "customer_id", "risk_score")}
                for row in inputs
            ]}
        result.update(source_run_id=self.state["source_plan"]["run_id"],
                      source_orders=self.state["source_plan"]["orders"])
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Fraud evaluates original orders; Teams chat verification is not applicable.")
        if self.state.get("test_run"):
            raise ValueError("This Fraud deployment already has a test attempt. Do not replay its orders.")
        result = self.verify_provisioning()
        if "source_plan" not in self.state:
            raise ValueError("Deploy first and prepare the returned synthetic orders in the approved test OMS.")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        planned = self.state["source_plan"]
        inputs = fixture_payloads(read(self.folder / "fixtures.json"), planned["run_id"], now)
        orders = [{key: row["expected_event"][key] for key in ("order_id", "customer_id", "risk_score")}
                  for row in inputs]
        if orders != planned["orders"]:
            raise ValueError("The source fixtures changed after order preparation. Do not submit a different order set.")
        attempt = {"run_id": planned["run_id"], "started_at": now, "inputs": inputs,
                   "phase": "invoking_native_udf", "invocations": []}
        self.state["test_run"] = attempt
        self.save()
        for row in inputs:
            payload = row["parameters"]
            attempt["pending_input"] = payload["orderId"]
            self.save()
            reply = self.api.request(
                "POST", self.base + f"userDataFunctions/{self.state['items']['publisher']}/functions/{FUNCTION}/invoke",
                payload,
            )
            attempt["last_response"] = {"http_status": reply.status, "body": reply.body}
            self.save()
            output = invocation_output(reply, row["expected_event"])
            identity = reply.body.get("invocationId")
            if identity is not None and any(entry["response"].get("invocationId") == identity
                                            for entry in attempt["invocations"]):
                raise RuntimeError("The Fraud UDF returned a repeated invocation identity.")
            attempt["invocations"].append({"order_id": payload["orderId"], "response": reply.body, "output": output})
            attempt.pop("pending_input")
            self.save()
        attempt["phase"] = "native_evaluations_succeeded_delivery_pending"
        result.update(
            status="source_triggered_verification_pending", source_triggered=True, source_execution_verified=True,
            source_run_id=planned["run_id"], native_udf_evaluations=len(attempt["invocations"]),
            expected_decision_events=[row["expected_event"] for row in inputs],
            publication_requests=len(attempt["invocations"]), events_published=None,
            outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Fraud requires actual flow and OMS evidence, not a Teams chat ID.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if attempt:
            if attempt.get("pending_input") or len(attempt["invocations"]) != len(attempt["inputs"]):
                raise ValueError("Fraud source execution is incomplete or ambiguous. Retain it without replay.")
            result.update(source_run_id=attempt["run_id"], source_execution_verified=True,
                          native_udf_evaluations=len(attempt["invocations"]),
                          publication_requests=len(attempt["invocations"]), events_published=None)
        self.state["verification"] = result
        self.save()
        return result
