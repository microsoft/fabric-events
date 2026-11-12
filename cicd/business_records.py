"""Exact operational records, separate from managed Business Event history."""

import datetime
import hashlib
import json
import math
import uuid

INVENTORY_FIELDS = (
    "product_id", "product_name", "store_id", "current_stock", "threshold", "supplier_id", "detected_at",
)
PAYMENT_FIELDS = ("transaction_id", "payment_method", "amount", "currency", "store_id", "processed_at")


def utc_time(value):
    if not isinstance(value, str):
        raise ValueError("Record timestamps must be ISO 8601 strings.")
    instant = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Record timestamps must use UTC.")
    return instant


def record_contract(kind):
    if kind == "inventory":
        return INVENTORY_FIELDS, "request_id", "pending_approval", "ReplenishmentRequests"
    if kind == "payment":
        return PAYMENT_FIELDS, "case_id", "pending_review", "PaymentReviewCases"
    raise ValueError("Unknown operational record kind.")


def record_id(kind, payload):
    fields, _, _, _ = record_contract(kind)
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise ValueError("The operational record requires the exact original event fields.")
    key = [payload[name] for name in (
        ("product_id", "store_id", "detected_at") if kind == "inventory" else ("transaction_id",)
    )]
    return hashlib.sha256(json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def make_record(kind, payload, rule_id, pipeline_run_id=None):
    fields, identity, status, _ = record_contract(kind)
    identifier = record_id(kind, payload)
    for field in fields:
        value = payload[field]
        if kind == "inventory" and field in ("current_stock", "threshold"):
            if type(value) is not int or not -(2 ** 31) <= value < 2 ** 31:
                raise ValueError("Stock fields must retain their signed Avro integer values.")
        elif kind == "payment" and field == "amount":
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 3.4028234663852886e38:
                raise ValueError("Payment amounts must retain a finite nonnegative Avro float value.")
        elif not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"Invalid original event field {field}.")
    utc_time(payload["detected_at" if kind == "inventory" else "processed_at"])
    result = {
        **payload, identity: identifier, "status": status, "rule_id": str(uuid.UUID(rule_id)),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if kind == "inventory":
        result["pipeline_run_id"] = str(uuid.UUID(pipeline_run_id))
    elif pipeline_run_id is not None:
        raise ValueError("Payment cases must not claim a pipeline execution.")
    return result


def check_record(kind, actual, expected):
    fields, identity, status, _ = record_contract(kind)
    extra = {"pipeline_run_id"} if kind == "inventory" else set()
    if (not isinstance(actual, dict) or set(actual) != set(fields) | {identity, "status", "rule_id", "created_at"} | extra
            or any(type(actual[name]) is not type(expected[name]) or actual[name] != expected[name]
                   for name in (*fields, identity, "status", "rule_id"))
            or actual["status"] != status):
        raise ValueError("A committed record contains conflicting or incomplete business context.")
    utc_time(actual["created_at"])
    if kind == "inventory":
        uuid.UUID(actual["pipeline_run_id"])
    return actual
