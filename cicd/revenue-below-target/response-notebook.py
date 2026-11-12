# Fabric notebook source

# METADATA ********************
# META {"kernel_info":{"name":"synapse_pyspark"},"dependencies":{}}

# PARAMETERS CELL ********************

region = ""
period_start = ""
revenue = 0.0
target = 0.0
variance_pct = 0.0
detected_at = ""
pipeline_run_id = ""

# CELL ********************

# ${record_helpers}

# CELL ********************

import notebookutils


def prepare_investigation():
    payload = {name: globals()[name] for name in REVENUE_FIELDS}
    record = make_revenue_investigation(payload, "${rule_id}", pipeline_run_id)
    path = (
        "abfss://${workspace_id}@onelake.dfs.fabric.microsoft.com/"
        "${lakehouse_id}/Files/RevenueInvestigations/" + record["investigation_id"] + ".json"
    )
    if notebookutils.fs.exists(path):
        return check_revenue_investigation(json.loads(notebookutils.fs.head(path, 65536)), record)
    if notebookutils.fs.put(path, json.dumps(record, allow_nan=False), False) is not True:
        raise RuntimeError("The pipeline failed to persist its Revenue investigation.")
    return check_revenue_investigation(json.loads(notebookutils.fs.head(path, 65536)), record)


notebookutils.notebook.exit(json.dumps(prepare_investigation(), allow_nan=False))
