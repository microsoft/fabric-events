import copy
from datetime import datetime, timezone
import math
import re
import uuid


class OrderError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def identity(order_id):
    if not isinstance(order_id, str) or len(order_id) > 128:
        raise OrderError(400, "A synthetic order ID is required.")
    match = re.fullmatch(r"cicd-([0-9a-f-]{36})-([a-z0-9-]+)", order_id)
    if not match:
        raise OrderError(400, "Only run-scoped synthetic orders are accepted.")
    try:
        run_id = str(uuid.UUID(match[1]))
    except ValueError as error:
        raise OrderError(400, "The synthetic run ID is invalid.") from error
    if match[1] != run_id:
        raise OrderError(400, "The synthetic run ID must be canonical.")
    return run_id, order_id


def score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrderError(400, "risk_score must be numeric.")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise OrderError(400, "risk_score must be finite and within 0..1.")
    return float(value)


def new_order(body):
    if not isinstance(body, dict) or set(body) != {"order_id", "customer_id", "risk_score"}:
        raise OrderError(400, "Supply exactly order_id, customer_id and risk_score.")
    run_id, order_id = identity(body["order_id"])
    customer = body["customer_id"]
    if not isinstance(customer, str) or not re.fullmatch(r"test-[A-Za-z0-9-]{1,120}", customer):
        raise OrderError(400, "Only synthetic customer IDs are accepted.")
    return {
        "PartitionKey": run_id, "RowKey": order_id,
        "order_id": order_id, "customer_id": customer,
        "risk_score": score(body["risk_score"]),
        "status": "pending", "fulfillment_allowed": False,
        "decision": "", "activation_id": "", "action_count": 0,
        "review_request_id": "", "review_completed": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def transition(current, action, body, activation_id):
    expected = {
        "hold": {"order_id", "risk_score"},
        "manual_review": {"order_id", "customer_id", "risk_score"},
        "release": {"order_id"},
    }
    if action not in expected:
        raise OrderError(404, "Unknown order operation.")
    if not isinstance(body, dict) or set(body) != expected[action]:
        raise OrderError(400, "The action parameter contract does not match.")
    identity(body["order_id"])
    if body["order_id"] != current["order_id"]:
        raise OrderError(400, "The action refers to a different order.")
    if not isinstance(activation_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", activation_id):
        raise OrderError(400, "An actual activation ID is required.")
    if "risk_score" in body and not math.isclose(
        score(body["risk_score"]), current["risk_score"], rel_tol=0, abs_tol=1e-6,
    ):
        raise OrderError(409, "The action risk score differs from the registered order.")
    if "customer_id" in body and body["customer_id"] != current["customer_id"]:
        raise OrderError(409, "The action customer differs from the registered order.")
    if current["activation_id"] == activation_id and current["decision"] == action:
        return copy.deepcopy(current)
    if current["status"] != "pending":
        raise OrderError(409, "Another operation already changed this order.")
    result = copy.deepcopy(current)
    result.update(
        decision=action, activation_id=activation_id, action_count=1,
        status={"hold": "held", "manual_review": "review_requested", "release": "released"}[action],
        fulfillment_allowed=action == "release",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    if action == "manual_review":
        result["review_request_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, activation_id))
    return result


def fulfill(current):
    if current["status"] != "released" or current["fulfillment_allowed"] is not True:
        raise OrderError(409, "Fulfillment is blocked by the persisted order state.")
    result = copy.deepcopy(current)
    result.update(status="fulfilled", updated_at=datetime.now(timezone.utc).isoformat())
    return result
