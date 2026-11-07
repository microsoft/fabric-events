# Fabric notebook source


# PARAMETERS CELL ********************

config_json = "{}"
fixtures_json = "[]"


# CELL ********************

import datetime
import json
import re
import uuid

EVENT_NAME = "Retail.Sales.VolumeAlert"


def finding(store_id, expected, actual, window_start):
    if not isinstance(store_id, str) or not store_id.strip():
        raise ValueError("store_id must be non-empty.")
    if type(expected) is not int or not 0 < expected <= 2**31 - 1:
        raise ValueError("expected_transactions must be a positive Avro int.")
    if type(actual) is not int or not 0 <= actual <= 2**31 - 1:
        raise ValueError("actual_transactions must be a non-negative Avro int.")
    instant = datetime.datetime.fromisoformat(window_start.replace("Z", "+00:00"))
    if instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("window_start must use UTC.")
    deviation = round((actual - expected) / expected * 100, 2)
    return {
        "store_id": store_id,
        "expected_transactions": expected,
        "actual_transactions": actual,
        "deviation_pct": deviation,
        "window_start": instant.isoformat().replace("+00:00", "Z"),
        "severity": "high" if deviation < -30 else "medium" if deviation < -15 else "low",
    }


def validate_fixtures(fixtures):
    if not fixtures:
        raise ValueError("At least one store window is required.")
    names = set()
    for row in fixtures:
        if not re.fullmatch(r"[a-z0-9-]+", row["store_id"]):
            raise ValueError("Fixture store IDs must contain lowercase letters, digits or hyphens.")
        if row["store_id"] in names:
            raise ValueError("Fixture store IDs must be unique.")
        names.add(row["store_id"])
        finding(
            row["store_id"], row["expected_transactions"],
            row["actual_transactions"], "2026-01-01T00:00:00Z",
        )


def expected_findings(fixtures, run_id, window_start):
    run_id = str(uuid.UUID(run_id))
    validate_fixtures(fixtures)
    return [
        finding(
            f"cicd-{run_id}-{row['store_id']}",
            row["expected_transactions"], row["actual_transactions"], window_start,
        )
        for row in fixtures
    ]


# CELL ********************

def kusto_request(config, statement, management=False):
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    import notebookutils

    endpoint = config["query_endpoint"].rstrip("/")
    address = urlsplit(endpoint)
    if (
        address.scheme != "https" or not (address.hostname or "").endswith(".kusto.fabric.microsoft.com")
        or address.port not in (None, 443) or address.username or address.password
        or address.path or address.query or address.fragment
    ):
        raise ValueError("Refusing to send credentials to an unexpected Kusto endpoint.")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(
        endpoint + ("/v1/rest/mgmt" if management else "/v1/rest/query"),
        data=json.dumps({"db": str(uuid.UUID(config["database_id"])), "csl": statement}).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + notebookutils.credentials.getToken("kusto"),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=120)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Kusto request failed with HTTP {error.code}.") from None
    with response:
        result = json.load(response)
    if result.get("error") or result.get("Exceptions"):
        raise RuntimeError("Kusto returned an incomplete or failed result.")
    return result


def query_rows(config, statement, key_column="store_id"):
    result = kusto_request(config, statement)
    tables = [
        table for table in result["Tables"]
        if key_column in [column["ColumnName"] for column in table["Columns"]]
    ]
    if len(tables) != 1:
        raise RuntimeError("Kusto did not return exactly one source result.")
    table = tables[0]
    columns = [column["ColumnName"] for column in table["Columns"]]
    return [dict(zip(columns, row)) for row in table["Rows"]]


def ingest_source_rows(config, table, rows):
    import csv
    import io
    import itertools

    if table not in ("cicd_store_baselines", "cicd_pos_transactions"):
        raise ValueError("Dummy initialization can write only the two source tables, not event history.")
    iterator = iter(rows)
    while batch := list(itertools.islice(iterator, 500)):
        text = io.StringIO(newline="")
        csv.writer(text, lineterminator="\n").writerows(batch)
        kusto_request(config, f".ingest inline into table {table} <|\n" + text.getvalue(), management=True)


def run_scenario(config, fixtures):
    import sys
    import time

    import notebookutils

    operation = config.get("operation", "publish")
    if config.get("event_name", EVENT_NAME) != EVENT_NAME:
        raise ValueError("The runtime event name differs from this configured native Notebook.")
    if operation not in ("inspect", "seed", "publish", "seed-and-publish"):
        raise ValueError("operation must be inspect, seed, publish or seed-and-publish.")
    if sys.version_info < (3, 12):
        raise RuntimeError("Select a Fabric runtime with Python 3.12 and Business Events support.")
    if operation != "seed" and not callable(
        getattr(getattr(notebookutils, "businessEvents", None), "publish", None)
    ):
        raise RuntimeError("This Fabric runtime does not expose notebookutils.businessEvents.publish.")
    if operation == "inspect":
        notebookutils.notebook.exit(json.dumps({
            "operation": operation, "python_version": list(sys.version_info[:3]),
            "business_events_available": True, "published_count": 0,
        }))
        return

    expected = None
    if operation in ("seed", "seed-and-publish"):
        validate_fixtures(fixtures)
        run_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc)
        window = now.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=1)
        window_start = window.isoformat().replace("+00:00", "Z")
        expected = expected_findings(fixtures, run_id, window_start)
        ingest_source_rows(config, "cicd_pos_transactions", (
            (
                run_id, row["store_id"], f"{row['store_id']}-{index:06d}",
                (window + datetime.timedelta(seconds=index % 3600)).isoformat(),
                1099 + index % 27 * 100, "USD",
            )
            for row in expected for index in range(row["actual_transactions"])
        ))
        ingest_source_rows(config, "cicd_store_baselines", (
            (run_id, row["store_id"], row["expected_transactions"], window_start, now.isoformat())
            for row in expected
        ))
    elif config.get("source_run_id"):
        run_id = str(uuid.UUID(config["source_run_id"]))
    else:
        latest = query_rows(
            config, "cicd_store_baselines | top 1 by seeded_at desc | project run_id", key_column="run_id",
        )
        if not latest:
            raise RuntimeError("No source data exists. Run optional dummy initialization or load POS sources first.")
        run_id = str(uuid.UUID(latest[0]["run_id"]))

    query = f"""
let baselines = cicd_store_baselines
| where run_id == guid("{run_id}")
| summarize expected_transactions=max(expected_transactions) by store_id, window_start;
let actuals = cicd_pos_transactions
| where run_id == guid("{run_id}")
| summarize actual_transactions=count_distinct(transaction_id) by store_id, window_start=bin(occurred_at, 1h);
baselines
| join kind=leftouter actuals on store_id, window_start
| project store_id, expected_transactions, actual_transactions=toint(coalesce(actual_transactions, 0)), window_start
| order by store_id asc
"""
    deadline = time.monotonic() + 90
    while True:
        payloads = [
            finding(row["store_id"], row["expected_transactions"], row["actual_transactions"], row["window_start"])
            for row in query_rows(config, query)
        ]
        if expected is None or sorted(payloads, key=lambda row: row["store_id"]) == sorted(
            expected, key=lambda row: row["store_id"],
        ):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Persisted POS counts do not match the seed contract; no events were published.")
        time.sleep(3)
    if not payloads:
        raise RuntimeError("The selected source batch has no store baselines; no events were published.")
    summary = {
        "operation": operation, "run_id": run_id, "window_start": payloads[0]["window_start"],
        "source_fixture": f"kusto://{config['database_id']}/cicd_store_baselines?run_id={run_id}",
        "source_store_count": len(payloads),
        "source_transaction_count": sum(row["actual_transactions"] for row in payloads),
        "published_count": 0,
        "expected_teams_matches": sum(row["deviation_pct"] < -30 for row in payloads),
    }
    if operation != "seed":
        published = notebookutils.businessEvents.publish(
            eventSchemaSetWorkspace=config["workspace_id"], eventSchemaSet=config["schema_set_id"],
            eventTypeName=EVENT_NAME, eventData=payloads, dataVersion="v1",
        )
        if published is not True:
            raise RuntimeError("Business Events publish did not return True.")
        summary["published_count"] = len(payloads)
    print(json.dumps(summary, indent=2))
    notebookutils.notebook.exit(json.dumps(summary))


# CELL ********************

if __name__ == "__main__":
    run_scenario(json.loads(config_json), json.loads(fixtures_json))
