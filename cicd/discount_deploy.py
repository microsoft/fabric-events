"""Discount policy evaluation and Teams notification through public Fabric APIs."""

import datetime
import json
import math
import re
import uuid

from public_api import POWERBI
from public_consumers import ActivatorDeployment, part
from public_deploy import read
from public_udf import publisher_definition, verify_publisher_definition

FUNCTION = "evaluate_discount_request"
PARAMETERS = {
    "dealId": ("deal_id", "str"), "accountName": ("account_name", "str"), "region": ("region", "str"),
    "dealValue": ("deal_value", "float"), "requestedDiscountPercent": ("requested_discount_percent", "float"),
    "estimatedMarginPercent": ("estimated_margin_percent", "float"), "eventTime": ("event_time", "datetime"),
}


def fixture_payloads(fixtures, run_id, event_time):
    run_id = str(uuid.UUID(run_id))
    instant = datetime.datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Discount fixture timestamps must use timezone-aware UTC.")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide a nonempty list of synthetic discount proposals.")
    results, identifiers = [], set()
    for row in fixtures:
        if not isinstance(row, dict) or set(row) != {"id", "input", "expected"}:
            raise ValueError("Discount fixtures must contain the exact reviewed fields.")
        identifier, proposal, expected = row["id"], row["input"], row["expected"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", identifier)
                or identifier in identifiers):
            raise ValueError("Discount fixture IDs must be distinct portable lowercase labels.")
        identifiers.add(identifier)
        if not isinstance(proposal, dict) or set(proposal) != {field for field, _ in PARAMETERS.values()}:
            raise ValueError("Discount proposals must contain the exact seven source fields.")
        for field, maximum in (("deal_id", 128), ("account_name", 256), ("region", 128)):
            if not isinstance(proposal[field], str) or not proposal[field].strip() or len(proposal[field].strip()) > maximum:
                raise ValueError(f"Invalid discount fixture {field}.")
        for field, minimum, maximum in (
            ("deal_value", 0.01, 1_000_000_000_000), ("requested_discount_percent", 0, 100),
            ("estimated_margin_percent", -100, 100),
        ):
            value = proposal[field]
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"Invalid discount fixture {field}.")
        if expected is not None and (
            not isinstance(expected, dict) or set(expected) != {"approval_level", "approval_reason"}
            or expected["approval_level"] not in ("manager", "executive")
            or not isinstance(expected["approval_reason"], str) or not expected["approval_reason"].strip()
        ):
            raise ValueError("Discount fixture expectations must describe an approval-required event or none.")
        proposal = {
            **{field: value.strip() if isinstance(value, str) else value for field, value in proposal.items()},
            "deal_id": f"cicd-{run_id}-{identifier}",
            "event_time": instant.isoformat().replace("+00:00", "Z"),
        }
        results.append({
            "parameters": {name: proposal[field].strip() if isinstance(proposal[field], str) else proposal[field]
                           for name, (field, _) in PARAMETERS.items()},
            "expected_event": {**proposal, **expected} if expected is not None else None,
        })
    return results


def invocation_output(reply, expected_event):
    envelope = reply.body
    if (reply.status != 200 or not isinstance(envelope, dict) or envelope.get("status") != "Succeeded"
            or envelope.get("errors") not in (None, [])
            or "functionName" in envelope and envelope["functionName"] != FUNCTION):
        raise RuntimeError("The native discount UDF did not confirm successful execution. Retain this attempt.")
    output = envelope.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError("The discount UDF output string is not valid JSON.") from error
    level = expected_event["approval_level"] if expected_event else "none"
    if (not isinstance(output, dict) or not {"approval_level", "event_published", "message"} <= output.keys()
            or output["approval_level"] != level or type(output["event_published"]) is not bool
            or output["event_published"] != (expected_event is not None)
            or not isinstance(output["message"], str) or not output["message"].strip()):
        raise RuntimeError("The discount UDF result differs from the reviewed policy expectation.")
    if "invocationId" in envelope and (
        not isinstance(envelope["invocationId"], str) or not envelope["invocationId"].strip()
    ):
        raise RuntimeError("The discount UDF returned an invalid invocation identity.")
    return output


class DiscountDeployment(ActivatorDeployment):
    resource_types = {"schema": "EventSchemaSet", "publisher": "UserDataFunction", "activator": "Reflex"}

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("discount_public_version") != 1:
            raise ValueError("The ledger does not match the Discount publisher and consumer. No adoption is permitted.")
        self.state["discount_public_version"] = 1

    def preflight(self, resuming=False):
        if self.config["auth_provider"] != "azure_cli":
            raise ValueError("Discount UDF invocation requires azure_cli with the documented Power BI audience.")
        result = super().preflight(resuming)
        self.api.tokens.token(POWERBI)
        return result

    def udf_definition(self):
        return publisher_definition(
            self.folder, self.config, self.state["items"]["schema"], FUNCTION,
            "Evaluate discount and margin policy and publish only manager or executive review requirements.",
            PARAMETERS,
        )

    def creation_request(self, role):
        if role == "schema":
            return super().creation_request(role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "publisher":
            body["definition"] = self.udf_definition()
            return self.base + "userDataFunctions", body
        if role != "activator":
            raise ValueError("Discount creates only its schema, native UDF and Activator.")
        body.update(type="Reflex", definition={"parts": [part("ReflexEntities.json", self.activator_entities())]})
        return self.base + "items", body

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy the complete Discount schema, publisher and notification before testing.")
        self.verify_schema()
        self.roles()
        definition = self.get_definition("activator")
        self.check_activator(definition)
        verify_publisher_definition(self)
        self.state["definition_backup"]["activator"] = definition
        result = {
            "status": "manual_setup_required", "items": self.state["items"],
            "source_binding_verified": True, "udf_metadata_readback": self.state["udf_metadata_readback"],
            "manual_setup_required": ["business_event_eventstream_subscription"],
            "manual_eventstream_name": self.config["resource_names"]["eventstream"],
            "eventstream_subscription_verified": False, "eventhouse_subscription_configured": False,
            "e2e_verified": False,
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Discount deployment prepares its native publisher and notification, not unused containers.")
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
        self.start_activator()
        self.create("publisher")
        result = self.verify_provisioning()
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Inspect Discount notifications and Eventstream delivery personally; no chat ID is required.")
        if self.state.get("test_run"):
            raise ValueError("This deployment already has a test attempt. Do not replay its proposal evaluations.")
        result = self.verify_provisioning()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        run_id = str(uuid.uuid4())
        inputs = fixture_payloads(read(self.folder / "fixtures.json"), run_id, now)
        attempt = {"run_id": run_id, "started_at": now, "inputs": inputs,
                   "phase": "invoking_native_udf", "invocations": []}
        self.state["test_run"] = attempt
        self.save()
        for row in inputs:
            payload = row["parameters"]
            attempt["pending_input"] = payload["dealId"]
            self.save()
            reply = self.api.request(
                "POST", self.base + f"userDataFunctions/{self.state['items']['publisher']}/functions/{FUNCTION}/invoke",
                payload,
            )
            attempt["last_response"] = {"http_status": reply.status, "body": reply.body}
            self.save()
            output = invocation_output(reply, row["expected_event"])
            invocation_id = reply.body.get("invocationId")
            if invocation_id is not None and any(
                entry["response"].get("invocationId") == invocation_id for entry in attempt["invocations"]
            ):
                raise RuntimeError("The discount UDF returned a repeated invocation identity.")
            attempt["invocations"].append({"deal_id": payload["dealId"], "response": reply.body, "output": output})
            attempt.pop("pending_input")
            self.save()
        attempt["phase"] = "native_evaluations_succeeded_delivery_pending"
        self.save()
        result.update(
            status="source_triggered_verification_pending", source_triggered=True, source_execution_verified=True,
            source_run_id=run_id, source_item_id=self.state["items"]["publisher"],
            native_udf_evaluations=len(attempt["invocations"]),
            deal_ids=[row["parameters"]["dealId"] for row in inputs],
            expected_review_events=[row["expected_event"] for row in inputs if row["expected_event"] is not None],
            publication_requests=sum(entry["output"]["event_published"] for entry in attempt["invocations"]),
            events_published=None, outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Discount E2E requires personal inspection of Teams and Eventstream, not a chat ID.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if not attempt:
            return result
        if attempt.get("pending_input") or len(attempt["invocations"]) != len(attempt["inputs"]):
            raise ValueError("Discount execution is incomplete or ambiguous. Retain this run; never replay its inputs.")
        result.update(
            status="discount_evaluations_succeeded_delivery_pending", source_run_id=attempt["run_id"],
            source_execution_verified=True, native_udf_evaluations=len(attempt["invocations"]),
            publication_requests=sum(entry["output"]["event_published"] for entry in attempt["invocations"]),
            events_published=None, e2e_verified=False,
        )
        self.state["verification"] = result
        self.save()
        return result
