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


def required_number(value: float, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if number < minimum or number > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


@udf.connection(argName="businessEventsClient", alias="${schema_name}")
@udf.function()
def evaluate_discount_request(
    businessEventsClient: fn.FabricBusinessEventsClient,
    dealId: str,
    accountName: str,
    region: str,
    dealValue: float,
    requestedDiscountPercent: float,
    estimatedMarginPercent: float,
    eventTime: datetime.datetime,
) -> dict:
    dealId = required_text(dealId, "dealId", 128)
    accountName = required_text(accountName, "accountName", 256)
    region = required_text(region, "region", 128)
    dealValue = required_number(dealValue, "dealValue", 0.01, 1_000_000_000_000)
    requestedDiscountPercent = required_number(requestedDiscountPercent, "requestedDiscountPercent", 0, 100)
    estimatedMarginPercent = required_number(estimatedMarginPercent, "estimatedMarginPercent", -100, 100)
    if not isinstance(eventTime, datetime.datetime) or eventTime.tzinfo is None:
        raise ValueError("eventTime must be a timezone-aware datetime")
    if eventTime.utcoffset() != datetime.timedelta(0):
        raise ValueError("eventTime must use UTC")
    normalized_event_time = eventTime.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    if requestedDiscountPercent <= 10 and estimatedMarginPercent >= 25:
        return {
            "approval_level": "none", "event_published": False,
            "message": f"Deal {dealId} is within commercial policy. No event published.",
        }

    if requestedDiscountPercent > 15 or estimatedMarginPercent < 25:
        approval_level = "executive"
        reasons = []
        if requestedDiscountPercent > 15:
            reasons.append("discount exceeds 15%")
        if estimatedMarginPercent < 25:
            reasons.append("margin is below 25%")
        approval_reason = " and ".join(reasons)
    else:
        approval_level = "manager"
        approval_reason = "discount exceeds the 10% seller authority"

    businessEventsClient.PublishEvent(
        type=EVENT_NAME,
        event_data={
            "deal_id": dealId, "account_name": accountName, "region": region, "deal_value": dealValue,
            "requested_discount_percent": requestedDiscountPercent,
            "estimated_margin_percent": estimatedMarginPercent,
            "approval_level": approval_level, "approval_reason": approval_reason,
            "event_time": normalized_event_time,
        },
        data_version="v1",
    )
    return {
        "approval_level": approval_level, "event_published": True,
        "message": f"Deal {dealId} requires {approval_level} approval. Business Event published.",
    }
