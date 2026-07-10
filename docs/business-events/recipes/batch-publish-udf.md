# Batch Publish from a User Data Function

Publish multiple Business Events in a single User Data Function call by passing an array as the event data.

## When to use this recipe

Use batch publishing from a UDF when your function processes a collection of items and each item should generate its own event — for example, when multiple inventory updates arrive together and each one should produce an independent event.

## Batch publish with an array payload

The key difference from a single-event publish is that `event_data` is a list of dictionaries instead of a single dictionary:

```python
import fabric.functions as fn
import json
from datetime import datetime, timezone

udf = fn.UserDataFunctions()

@udf.connection(argName="businessEventsClient", alias="RetailInventory")
@udf.function()
def batch_publish(
    businessEventsClient: fn.FabricBusinessEventsClient,
    items_json: str
) -> str:
    items = json.loads(items_json)

    event_data = [
        {
            "store_id": item["store_id"],
            "product_id": item["product_id"],
            "current_qty": item["current_qty"],
            "threshold_qty": item["threshold_qty"],
            "occurred_at": datetime.now(timezone.utc).isoformat()
        }
        for item in items
    ]

    businessEventsClient.PublishEvent(
        type="Retail.Inventory.LowStockThreshold",
        event_data=event_data,
        data_version="v1"
    )

    return f"Published {len(event_data)} events"
```

## Example input

Pass a JSON string as the `items_json` parameter when calling the function:

```json
[
  { "store_id": "STR-001", "product_id": "SKU-9821", "current_qty": 4, "threshold_qty": 10 },
  { "store_id": "STR-003", "product_id": "SKU-4432", "current_qty": 2, "threshold_qty": 15 },
  { "store_id": "STR-007", "product_id": "SKU-7751", "current_qty": 1, "threshold_qty": 5 }
]
```

## Add error handling

```python
import fabric.functions as fn
import json
from datetime import datetime, timezone

udf = fn.UserDataFunctions()

@udf.connection(argName="businessEventsClient", alias="RetailInventory")
@udf.function()
def batch_publish(
    businessEventsClient: fn.FabricBusinessEventsClient,
    items_json: str
) -> str:
    try:
        items = json.loads(items_json)
    except (ValueError, TypeError):
        return "Invalid JSON input"

    if not isinstance(items, list) or len(items) == 0:
        return "Input must be a non-empty array"

    event_data = [
        {
            "store_id": item["store_id"],
            "product_id": item["product_id"],
            "current_qty": item["current_qty"],
            "threshold_qty": item["threshold_qty"],
            "occurred_at": datetime.now(timezone.utc).isoformat()
        }
        for item in items
    ]

    businessEventsClient.PublishEvent(
        type="Retail.Inventory.LowStockThreshold",
        event_data=event_data,
        data_version="v1"
    )

    return f"Published {len(event_data)} events"
```

## Considerations

**Each item in the array becomes an independent event.** The platform delivers each one individually, with its own retry window.

**Validate array size before publishing.** Very large arrays may hit request size limits. Filter the input to only include items that actually need an event.
