"""Execution-scoped investigations from the original Revenue Business Event."""

import datetime
import hashlib
import json
import math
import uuid

REVENUE_FIELDS = ("region", "period_start", "revenue", "target", "variance_pct", "detected_at")


def make_revenue_investigation(payload, rule_id, pipeline_run_id):
    if not isinstance(payload, dict) or set(payload) != set(REVENUE_FIELDS):
        raise ValueError("Revenue investigation requires the six original event fields.")
    for field in ("region", "period_start", "detected_at"):
        if not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field]) > 256:
            raise ValueError(f"Invalid Revenue event field {field}.")
    snapshot = datetime.datetime.strptime(payload["detected_at"], "%Y%m%dT%H%M%SZ")
    if snapshot.strftime("%Y%m%dT%H%M%SZ") != payload["detected_at"]:
        raise ValueError("Revenue detected_at must retain its basic ISO 8601 UTC snapshot.")
    for field in ("revenue", "target", "variance_pct"):
        if type(payload[field]) not in (int, float) or not math.isfinite(payload[field]):
            raise ValueError(f"Revenue event field {field} must be finite.")
    revenue, target, variance = (payload[field] for field in ("revenue", "target", "variance_pct"))
    if target <= 0 or not math.isclose(
        100 * (revenue - target) / target, variance, rel_tol=1e-8, abs_tol=1e-8,
    ):
        raise ValueError("The Revenue event contains an invalid target or inconsistent shortfall.")
    if not isinstance(rule_id, str) or not isinstance(pipeline_run_id, str):
        raise ValueError("Revenue analysis requires its real rule and native pipeline execution IDs.")
    rule_id, pipeline_run_id = str(uuid.UUID(rule_id)), str(uuid.UUID(pipeline_run_id))
    shortfall = target - revenue
    lift = 100 * shortfall / revenue if revenue > 0 else None
    if not math.isfinite(shortfall) or (lift is not None and not math.isfinite(lift)):
        raise ValueError("The Revenue investigation calculations must be finite.")
    identifier = hashlib.sha256((rule_id + ":" + pipeline_run_id).encode()).hexdigest()
    return {
        **payload, "investigation_id": identifier, "status": "ready_for_review", "rule_id": rule_id,
        "shortfall": shortfall, "revenue_lift_pct": lift, "pipeline_run_id": pipeline_run_id,
        "investigated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def check_revenue_investigation(actual, expected):
    if (not isinstance(actual, dict) or set(actual) != set(expected)
            or any(type(actual[field]) is not type(expected[field]) or actual[field] != expected[field]
                   for field in expected if field != "investigated_at")):
        raise ValueError("The stored Revenue investigation conflicts with its event or pipeline execution.")
    timestamp = actual["investigated_at"]
    if (not isinstance(timestamp, str) or not timestamp.endswith("Z")
            or datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).utcoffset()
            != datetime.timedelta(0)):
        raise ValueError("The Revenue investigation timestamp must use UTC.")
    return actual
