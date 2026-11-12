# Fabric notebook source

# METADATA ********************
# META {"kernel_info":{"name":"synapse_pyspark"},"dependencies":{}}

# PARAMETERS CELL ********************

config_json = "{}"
fixtures_json = "[]"

# CELL ********************

import datetime
import json
import logging
import re
import time
import uuid

# ${failure_helpers}


class SkippedWorkload(Exception):
    pass


class SyntheticLoadFailure(Exception):
    pass


def execute_workload(work, publish, pipeline_id, pipeline_name, run_id, triggered_by):
    progress = {"rows_processed": 0}
    started = time.monotonic()
    status = "completed"
    try:
        work(progress)
    except SkippedWorkload:
        status = "skipped"
        logging.info("Workload %s was explicitly skipped.", run_id)
    except Exception:
        status = "failed"
        logging.exception("Workload %s failed; publishing its original outcome before re-raising.", run_id)
        raise
    finally:
        event = validate_event({
            "pipeline_id": pipeline_id, "pipeline_name": pipeline_name, "run_id": run_id,
            "status": status, "rows_processed": progress["rows_processed"],
            "duration_seconds": int(time.monotonic() - started), "triggered_by": triggered_by,
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        publish(event)


def validate_fixtures(fixtures):
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide nonempty synthetic workload fixtures.")
    seen = set()
    for row in fixtures:
        if (not isinstance(row, dict) or set(row) != {"id", "operation", "rows"}
                or not isinstance(row["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", row["id"])
                or row["id"] in seen or row["operation"] not in ("complete", "fail", "skip")
                or type(row["rows"]) is not int or not 0 <= row["rows"] <= 1000
                or row["operation"] == "skip" and row["rows"] != 0):
            raise ValueError("Workload fixtures require distinct labels, reviewed operations and bounded row counts.")
        seen.add(row["id"])


def run_scenario(config, fixtures):
    import notebookutils

    validate_fixtures(fixtures)
    run_id = str(uuid.UUID(config["source_run_id"]))
    payloads = []

    def publish(event):
        result = notebookutils.businessEvents.publish(
            eventSchemaSetWorkspace=config["workspace_id"], eventSchemaSet=config["schema_set_id"],
            eventTypeName=config["event_name"], eventData=event, dataVersion="v1",
        )
        if result is not True:
            raise RuntimeError("The original workload outcome was not accepted for publication.")
        payloads.append(event)

    for fixture in fixtures:
        def work(progress):
            if fixture["operation"] == "skip":
                raise SkippedWorkload("Synthetic workload was not scheduled to run.")
            for _ in range(fixture["rows"]):
                progress["rows_processed"] += 1
            if fixture["operation"] == "fail":
                raise SyntheticLoadFailure("Controlled synthetic load failure.")

        try:
            execute_workload(
                work, publish, config["notebook_id"], config["notebook_name"],
                str(uuid.uuid5(uuid.UUID(run_id), fixture["id"])), "manual",
            )
        except SyntheticLoadFailure:
            if fixture["operation"] != "fail":
                raise
            logging.info("Continuing the demonstration after the expected synthetic failure.")

    summary = {
        "source_run_id": run_id, "source_item_id": config["notebook_id"], "payloads": payloads,
        "publication_requests": len(payloads),
        "expected_cases": sum(event["status"] == "failed" for event in payloads),
    }
    print(json.dumps(summary, indent=2))
    notebookutils.notebook.exit(json.dumps(summary))


# CELL ********************

if __name__ == "__main__":
    run_scenario(json.loads(config_json), json.loads(fixtures_json))
