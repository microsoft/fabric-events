# Order History Analysis: Workbook

[Business context and architecture](../order-history-analysis.md)

Publish six synthetic orders, retain the received events in Eventhouse and compare order volume and value across two stores. This Beginner exercise uses guided native setup and one KQL result check. It has no downloadable deployment package.

## Resources and coverage

| Resource | Name used in this exercise | Preparation |
|---|---|---|
| Event Schema Set | `OrderHistorySchema` | Create in the native Business Events interface |
| Business Event | `Retail.Orders.OrderPlaced` | Define five payload fields and enable **Analyze in Eventhouse** during creation |
| Eventhouse and dedicated KQL table | `OrderHistory`, or an existing Eventhouse you can query | Fabric creates the event-history table and manages ingestion |
| Notebook | `OrderHistoryPublisher` | Create a Notebook and paste the source cell below |

Use an isolated learning workspace and unused item names. If these names already exist, choose new names and change `eventSchemaSet` in the source cell accordingly. Do not replace an existing event or its history configuration.

## Before you start

Use a Fabric workspace on an active supported capacity with permission to create Business Events, an Eventhouse and a Notebook. The Notebook needs a supported Fabric runtime with **Python 3.12+ and Business Events support**.

Publish permission on the Event Schema Set and permission to query the history database are required. If a separate owner manages the schema set, have that owner grant the appropriate data access role before publishing. See [Publish Business Events](https://learn.microsoft.com/fabric/real-time-hub/publish-business-events).

All records are generated in the Notebook. No tenant IDs, saved credentials, production endpoints or real order data are supplied by this Workbook.

## 1. Create the event with managed history

1. In Fabric, open **Real-Time hub → Business events → New business event → Create new schema**.
2. Select your learning workspace and create the Event Schema Set `OrderHistorySchema`. Name the event `Retail.Orders.OrderPlaced` and use schema version `v1`.
3. Define these payload fields. The JSON below is the corresponding Avro schema reference, not a second setup route.

| Field | Type | Meaning |
|---|---|---|
| `sample_run_id` | string | Correlates one execution of this exercise |
| `order_id` | string | Identifies one synthetic order |
| `store_id` | string | `north` or `south` |
| `order_total_usd` | double | Placed-order value in USD |
| `placed_at` | string | UTC placement time in ISO 8601 format |

```json
{
  "type": "record",
  "name": "OrderPlaced",
  "namespace": "Retail.Orders",
  "fields": [
    {"name": "sample_run_id", "type": "string"},
    {"name": "order_id", "type": "string"},
    {"name": "store_id", "type": "string"},
    {"name": "order_total_usd", "type": "double"},
    {"name": "placed_at", "type": "string"}
  ]
}
```

4. Keep **Analyze in Eventhouse** enabled during Business Event creation. Select a new Eventhouse named `OrderHistory`, or an existing Eventhouse in the workspace that you are allowed to use. Review and create the event.
5. Open the selected Eventhouse and find the dedicated event-history table created for this event. Note its actual database and table names; use them in the query later.

The [managed history option](https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-eventhouse) creates the table and ingests received events without a separate ingestion pipeline. Choose it **during creation**, before publishing. Creating an empty Eventhouse or writing rows directly into a table does not establish this subscription.

## 2. Publish one sample

Create `OrderHistoryPublisher` in the same workspace. Replace `<workspace-name-or-id>` below with **your** workspace name or ID, then run this Python cell.

```python
from datetime import datetime, timezone
from uuid import uuid4

import notebookutils

sample_run_id = str(uuid4())
placed_at = datetime.now(timezone.utc).isoformat()
orders = [
    ("north", 120.0),
    ("north", 80.0),
    ("north", 150.0),
    ("south", 200.0),
    ("south", 50.0),
    ("south", 300.0),
]
events = [
    {
        "sample_run_id": sample_run_id,
        "order_id": f"{sample_run_id}-{index:02d}",
        "store_id": store_id,
        "order_total_usd": amount,
        "placed_at": placed_at,
    }
    for index, (store_id, amount) in enumerate(orders, start=1)
]

published = notebookutils.businessEvents.publish(
    eventSchemaSetWorkspace="<workspace-name-or-id>",
    eventSchemaSet="OrderHistorySchema",
    eventTypeName="Retail.Orders.OrderPlaced",
    eventData=events,
    dataVersion="v1",
)
if published is not True:
    raise RuntimeError("Publication was not accepted. Inspect the Notebook error before retrying.")

print(f"Sample run: {sample_run_id}")
print("Publication accepted. Verify receipt and totals in Eventhouse next.")
```

Copy the printed **Sample run** value. Each execution creates a new run identifier; use the identifier from the execution you want to inspect. Publication success does **not** prove delivery to Eventhouse.

## 3. Compare stores in KQL

In the event-history database, open a KQL query. Replace `<event-history-table>` with the table actually created in step 1 and `<sample-run-id>` with the printed identifier.

The query accepts a table with payload fields exposed as columns or a CloudEvents `data` column. It filters the run **before** deduplicating by order identifier, so prior exercises and repeated delivery do not inflate this comparison.

```kql
let SampleRun = "<sample-run-id>";
['<event-history-table>']
| extend payload = parse_json(tostring(column_ifexists("data", pack_all())))
| extend sample_run_id = tostring(payload.sample_run_id),
         order_id = tostring(payload.order_id),
         store_id = tostring(payload.store_id),
         order_total_usd = todouble(payload.order_total_usd),
         placed_at = todatetime(payload.placed_at)
| where sample_run_id == SampleRun
| summarize arg_max(placed_at, store_id, order_total_usd) by order_id
| summarize Orders = count(), OrderValueUSD = sum(order_total_usd) by store_id
| order by store_id asc
```

### Expected result

| store_id | Orders | OrderValueUSD |
|---|---|---|
| north | 3 | 350 |
| south | 3 | 550 |

The two rows represent **six distinct orders and USD 900 in placed-order value**. Completion means these results are visible for the same run that the Notebook printed, not just that the publication call returned successfully.

If no rows appear, allow time for ingestion and rerun **the query**, not the publisher. Check the actual database, table, sample identifier, schema version and creation-time history choice. Inspect a received row to confirm its payload shape if fields are missing; do not treat an empty result or missing field as zero business activity.

## What this exercise demonstrates

A Business Event can feed an independent, queryable history without coupling the publisher to an analytics table. Here, managed Eventhouse history is the **required consumer**. In the operational scenarios it is an optional additional consumer instead.

The sample query deduplicates only this exercise's order identifiers. It does not reconcile changing orders, payments, cancellations or refunds. Keep production retention, access controls and reconciliation rules separate from this synthetic exercise.
