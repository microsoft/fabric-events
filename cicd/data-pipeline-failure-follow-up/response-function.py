import datetime
import uuid
import fabric.functions as fn

# ${failure_helpers}

udf = fn.UserDataFunctions()


@udf.connection(argName="caseDatabase", alias="FailureCases")
@udf.function()
def register_pipeline_failure(
    caseDatabase: fn.FabricSqlConnection, pipelineId: str, pipelineName: str, runId: str,
    status: str, rowsProcessed: int, durationSeconds: int, triggeredBy: str, completedAt: str,
) -> dict:
    event = validate_event({
        "pipeline_id": pipelineId, "pipeline_name": pipelineName, "run_id": runId,
        "status": status, "rows_processed": rowsProcessed, "duration_seconds": durationSeconds,
        "triggered_by": triggeredBy, "completed_at": completedAt,
    })
    if status != "failed":
        raise ValueError("Only failed executions may reach case registration.")
    connection = caseDatabase.connect()
    try:
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute(
            "EXEC dbo.RecordPipelineFailure @pipeline_id=?, @pipeline_name=?, @run_id=?, @status=?, "
            "@rows_processed=?, @duration_seconds=?, @triggered_by=?, @completed_at=?",
            *[event[name] for name in FIELDS],
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Case registration returned no persisted row.")
        result = dict(zip([column[0] for column in cursor.description], row))
        if (any(type(result.get(name)) is not type(value) or result[name] != value for name, value in event.items())
                or result.get("case_status") not in ("pending_triage", "in_progress", "resolved")):
            raise RuntimeError("The persisted case does not match the failed execution and its response rule.")
        result["case_id"] = str(uuid.UUID(str(result["case_id"])))
        created = result["created_at"]
        if not isinstance(created, datetime.datetime) or created.tzinfo is not None:
            raise RuntimeError("SQL did not return the expected UTC datetime2 creation time.")
        result["created_at"] = created.replace(tzinfo=datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return result
    finally:
        connection.close()
