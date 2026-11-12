# Fabric notebook source

# METADATA ********************
# META {"kernel_info":{"name":"synapse_pyspark"},"dependencies":{}}

# PARAMETERS CELL ********************

product_id = ""
product_name = ""
store_id = ""
current_stock = 0
threshold = 0
supplier_id = ""
detected_at = ""
pipeline_run_id = ""

# CELL ********************

# ${record_helpers}

# CELL ********************

import notebookutils

def prepare_request():
    payload = {name: globals()[name] for name in INVENTORY_FIELDS}
    record = make_record("inventory", payload, "${rule_id}", pipeline_run_id)
    path = (
        "abfss://${workspace_id}@onelake.dfs.fabric.microsoft.com/"
        "${lakehouse_id}/Files/ReplenishmentRequests/" + record["request_id"] + ".json"
    )
    if notebookutils.fs.exists(path):
        return check_record("inventory", json.loads(notebookutils.fs.head(path, 65536)), record)
    if notebookutils.fs.put(path, json.dumps(record, allow_nan=False), False) is not True:
        raise RuntimeError("The pipeline failed to persist its replenishment request.")
    return check_record("inventory", json.loads(notebookutils.fs.head(path, 65536)), record)


notebookutils.notebook.exit(json.dumps(prepare_request(), allow_nan=False))
