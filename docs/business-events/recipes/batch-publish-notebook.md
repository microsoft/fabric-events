# Batch Publish from a Notebook

Publish multiple Business Events in a single Notebook call — useful for processing a list of records and signaling each one as an individual business event.

## When to use this recipe

Use batch publishing when your Notebook processes a collection of items and each item should generate its own event. For example, after a daily inventory check, publish one `Retail.Inventory.LowStockThreshold` event per product that is below threshold.

## Batch publish with an array payload

Pass a list of dictionaries as `event_data` to publish multiple events in a single call:

```python
from datetime import datetime, timezone

low_stock_items = [
    {"product_id": "SKU-9821", "store_id": "STR-001", "current_qty": 4, "threshold_qty": 10},
    {"product_id": "SKU-4432", "store_id": "STR-003", "current_qty": 2, "threshold_qty": 15},
    {"product_id": "SKU-7751", "store_id": "STR-007", "current_qty": 1, "threshold_qty": 5},
]

event_data = [
    {**item, "occurred_at": datetime.now(timezone.utc).isoformat()}
    for item in low_stock_items
]

notebookutils.businessEvents.publish(
    "MyWorkspace",
    "RetailInventory",
    "Retail.Inventory.LowStockThreshold",
    event_data,
    dataVersion="v1"
)
```

## Considerations

**Publish only what changed.** Filter your dataset before building the array — include only items that crossed a threshold, not every item in the inventory.

**Each item in the array becomes an independent event.** The platform delivers each one individually, with its own retry window.

**Avoid very large arrays.** If you need to publish thousands of events per second, consider using Eventstream with a streaming source instead of a Notebook call.
