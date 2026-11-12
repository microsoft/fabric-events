"""Original run-completion facts shared by the publisher and SQL response."""

import datetime

FIELDS = (
    "pipeline_id", "pipeline_name", "run_id", "status", "rows_processed",
    "duration_seconds", "triggered_by", "completed_at",
)


def validate_event(event):
    if not isinstance(event, dict) or set(event) != set(FIELDS):
        raise ValueError("Provide exactly the eight original run-completion fields.")
    for name, limit in (
        ("pipeline_id", 128), ("pipeline_name", 256), ("run_id", 128),
        ("status", 16), ("triggered_by", 32), ("completed_at", 64),
    ):
        value = event[name]
        if (not isinstance(value, str) or not value.strip() or "\x00" in value
                or len(value.encode("utf-16-le")) > limit * 2):
            raise ValueError(f"{name} must be a nonempty string within the SQL column limit.")
    if event["status"] not in ("completed", "failed", "skipped"):
        raise ValueError("Run status must be completed, failed or skipped.")
    if event["triggered_by"] not in ("schedule", "manual", "upstream"):
        raise ValueError("Use the original schedule, manual or upstream trigger.")
    for name in ("rows_processed", "duration_seconds"):
        if type(event[name]) is not int or not 0 <= event[name] <= 2_147_483_647:
            raise ValueError(f"{name} must be a nonnegative Avro int.")
    instant = datetime.datetime.fromisoformat(event["completed_at"].replace("Z", "+00:00"))
    if instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("completed_at must identify a timezone-aware UTC instant.")
    return dict(event)
