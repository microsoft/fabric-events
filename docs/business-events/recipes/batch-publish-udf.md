# Batch Publish from a User Data Function

Publish multiple Business Events in a single User Data Function call by passing an array as the event data.

## When to use this recipe

Use batch publishing from a UDF when your function processes a collection of items and each item should generate its own event — for example, processing an HTTP request that contains multiple inventory updates at once.

## Batch publish with an array payload

The key difference from a single-event publish is that `event_data` is a list of dictionaries instead of a single dictionary:

```python
import azure.functions as func
from fabric.functions import *

udf = UserDataFunctions()

@udf.connection(argName="businessEventsClient", alias="RetailInventory")
@udf.route(trigger=HttpTrigger())
def batch_publish(req: HttpRequest, businessEventsClient: FabricBusinessEventsClient):
    from datetime import datetime, timezone

    items = req.get_json()  # expects a list of inventory items

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

    return HttpResponse(f"Published {len(event_data)} events", status_code=200)
```

## Example request payload

```json
[
  { "store_id": "STR-001", "product_id": "SKU-9821", "current_qty": 4, "threshold_qty": 10 },
  { "store_id": "STR-003", "product_id": "SKU-4432", "current_qty": 2, "threshold_qty": 15 },
  { "store_id": "STR-007", "product_id": "SKU-7751", "current_qty": 1, "threshold_qty": 5 }
]
```

## Add error handling

```python
@udf.connection(argName="businessEventsClient", alias="RetailInventory")
@udf.route(trigger=HttpTrigger())
def batch_publish(req: HttpRequest, businessEventsClient: FabricBusinessEventsClient):
    from datetime import datetime, timezone

    try:
        items = req.get_json()
    except ValueError:
        return HttpResponse("Invalid JSON body", status_code=400)

    if not isinstance(items, list) or len(items) == 0:
        return HttpResponse("Request body must be a non-empty array", status_code=400)

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

    return HttpResponse(f"Published {len(event_data)} events", status_code=200)
```

## Considerations

**Each item in the array becomes an independent event.** The platform delivers each one individually, with its own retry window.

**Validate array size before publishing.** Very large arrays may hit request size limits. Filter the input to only include items that actually need an event.
