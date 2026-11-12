import datetime
import math

import fabric.functions as fn

EVENT_NAME = "${event_name}"
udf = fn.UserDataFunctions()


def required_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return normalized


@udf.connection(argName="businessEventsClient", alias="${schema_name}")
@udf.function()
def evaluate_order_risk(
    businessEventsClient: fn.FabricBusinessEventsClient,
    orderId: str,
    customerId: str,
    riskScore: float,
    decidedAt: str,
) -> dict:
    orderId = required_text(orderId, "orderId", 128)
    customerId = required_text(customerId, "customerId", 128)
    if isinstance(riskScore, bool) or not isinstance(riskScore, (int, float)):
        raise ValueError("riskScore must be a number")
    if not math.isfinite(riskScore) or not 0 <= riskScore <= 1:
        raise ValueError("riskScore must be finite and between 0 and 1")
    decidedAt = required_text(decidedAt, "decidedAt", 64)
    parsed_time = datetime.datetime.fromisoformat(decidedAt.replace("Z", "+00:00"))
    if parsed_time.tzinfo is None or parsed_time.utcoffset() != datetime.timedelta(0):
        raise ValueError("decidedAt must use timezone-aware UTC")
    if riskScore >= 0.85:
        decision = "hold"
    elif riskScore >= 0.55:
        decision = "manual_review"
    else:
        decision = "release"
    businessEventsClient.PublishEvent(
        type=EVENT_NAME,
        event_data={
            "order_id": orderId, "customer_id": customerId, "risk_score": float(riskScore),
            "decision": decision, "decided_at": parsed_time.isoformat().replace("+00:00", "Z"),
        },
        data_version="v1",
    )
    return {"decision": decision, "event_published": True}
