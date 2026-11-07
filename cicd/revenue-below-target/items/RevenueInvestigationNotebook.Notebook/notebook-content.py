# Fabric notebook source


# PARAMETERS CELL ********************

query_endpoint = ""
database_id = ""
pipeline_run_id = ""

# CELL ********************

import datetime
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import notebookutils

database_id = str(uuid.UUID(database_id))
pipeline_run_id = str(uuid.UUID(pipeline_run_id))
endpoint = query_endpoint.rstrip("/")
address = urllib.parse.urlsplit(endpoint)
if (
    address.scheme != "https" or not (address.hostname or "").endswith(".kusto.fabric.microsoft.com")
    or address.port not in (None, 443) or address.username or address.password
    or address.path or address.query or address.fragment
):
    raise ValueError("The investigation must use a verified Fabric KQL endpoint.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(statement, management=False):
    message = urllib.request.Request(
        endpoint + ("/v1/rest/mgmt" if management else "/v1/rest/query"),
        data=json.dumps({"db": database_id, "csl": statement}).encode(),
        headers={
            "Authorization": "Bearer " + notebookutils.credentials.getToken("kusto"),
            "Content-Type": "application/json",
        }, method="POST",
    )
    try:
        response = urllib.request.build_opener(NoRedirect()).open(message, timeout=120)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Investigation KQL request failed: HTTP {error.code}.") from None
    with response:
        result = json.load(response)
    if result.get("error") or result.get("Exceptions"):
        raise RuntimeError("The investigation KQL response failed or is incomplete.")
    return result


def rows(statement, column):
    result = request(statement)
    tables = [t for t in result["Tables"] if column in [c["ColumnName"] for c in t["Columns"]]]
    if len(tables) != 1:
        raise RuntimeError("The investigation did not receive a complete result table.")
    table = tables[0]
    columns = [c["ColumnName"] for c in table["Columns"]]
    return [dict(zip(columns, row)) for row in table["Rows"]]


event_name = "${event_name}"
source = "['" + event_name + "']"
deadline = time.monotonic() + 120
while True:
    events = rows(source, "region")
    if events:
        break
    if time.monotonic() >= deadline:
        raise TimeoutError("No actual Revenue event reached Eventhouse; no investigation was fabricated.")
    time.sleep(5)
identities = set()
for event in events:
    identity = (event["region"], event["period_start"], event["detected_at"])
    if identity in identities:
        raise RuntimeError("The actual Revenue event identity is duplicated.")
    identities.add(identity)
    instant = datetime.datetime.strptime(event["detected_at"], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    if (
        instant.utcoffset() != datetime.timedelta(0)
        or any(not isinstance(event[field], str) or not event[field].strip() for field in ("region", "period_start"))
        or any(type(event[field]) not in (int, float) for field in ("revenue", "target", "variance_pct"))
        or event["target"] <= 0 or event["variance_pct"] >= -15
        or not math.isclose((event["revenue"] - event["target"]) / event["target"] * 100, event["variance_pct"], abs_tol=1e-8)
    ):
        raise ValueError("An actual complete Revenue snapshot is invalid or inconsistent.")
statement = (
    ".set-or-append revenue_investigations <|\n" + source
    + f" | extend pipeline_run_id={json.dumps(pipeline_run_id)}, investigated_at=now(),"
    + " shortfall=target-revenue, revenue_lift_pct=iff(revenue > 0, (target-revenue)/revenue*100.0, real(null))"
    + " | project region, period_start, revenue, target, variance_pct, detected_at,"
    + " shortfall, revenue_lift_pct, pipeline_run_id, investigated_at"
)
request(statement, management=True)
deadline = time.monotonic() + 120
while True:
    output = rows(
        f"revenue_investigations | where pipeline_run_id == {json.dumps(pipeline_run_id)}", "pipeline_run_id",
    )
    if output:
        break
    if time.monotonic() >= deadline:
        raise TimeoutError("The investigation output has not been independently read back.")
    time.sleep(5)
fields = ("region", "period_start", "revenue", "target", "variance_pct", "detected_at")
if sorted(tuple(row[field] for field in fields) for row in output) != sorted(
    tuple(event[field] for field in fields) for event in events
):
    raise RuntimeError("The refreshed investigation dataset does not match actual Revenue history.")
notebookutils.notebook.exit(json.dumps({
    "status": "actual_revenue_investigation_dataset_refreshed_and_readback_verified",
    "pipeline_run_id": pipeline_run_id, "output_row_count": len(output),
}))
