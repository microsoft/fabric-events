import datetime

import fabric.functions as fn

EVENT_NAME = "${event_name}"
udf = fn.UserDataFunctions()


def required_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return normalized


def required_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not -(2 ** 31) <= value < 2 ** 31:
        raise ValueError(f"{name} must fit an Avro int")
    return value


@udf.connection(argName="businessEventsClient", alias="${schema_name}")
@udf.function()
def publish_low_stock_event(
    businessEventsClient: fn.FabricBusinessEventsClient,
    productId: str,
    productName: str,
    storeId: str,
    currentStock: int,
    threshold: int,
    supplierId: str,
    detectedAt: str,
) -> dict:
    productId = required_text(productId, "productId", 128)
    productName = required_text(productName, "productName", 256)
    storeId = required_text(storeId, "storeId", 128)
    currentStock = required_int(currentStock, "currentStock")
    threshold = required_int(threshold, "threshold")
    supplierId = required_text(supplierId, "supplierId", 128)
    detectedAt = required_text(detectedAt, "detectedAt", 64)
    parsed_time = datetime.datetime.fromisoformat(detectedAt.replace("Z", "+00:00"))
    if parsed_time.tzinfo is None or parsed_time.utcoffset() != datetime.timedelta(0):
        raise ValueError("detectedAt must use timezone-aware UTC")
    businessEventsClient.PublishEvent(
        type=EVENT_NAME,
        event_data={
            "product_id": productId,
            "product_name": productName,
            "store_id": storeId,
            "current_stock": currentStock,
            "threshold": threshold,
            "supplier_id": supplierId,
            "detected_at": parsed_time.isoformat().replace("+00:00", "Z"),
        },
        data_version="v1",
    )
    return {"event_published": True}
