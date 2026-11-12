import datetime
import math
import re

import fabric.functions as fn

udf = fn.UserDataFunctions()


@udf.connection(argName="businessEventsClient", alias="${schema_name}")
@udf.function()
def publish_cart_abandoned_event(
    businessEventsClient: fn.FabricBusinessEventsClient,
    customerId: str,
    customerEmail: str,
    cartId: str,
    cartValue: float,
    itemCount: int,
    lastActivityAt: str,
) -> dict:
    for value in (customerId, cartId):
        if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 128:
            raise ValueError("Customer and cart identifiers must be nonempty strings of at most 128 characters.")
    if (not isinstance(customerEmail, str) or len(customerEmail) > 254
            or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", customerEmail)):
        raise ValueError("Provide one valid customer email address without whitespace.")
    if type(cartValue) not in (int, float) or not math.isfinite(cartValue) or not 0 <= cartValue <= 1_000_000_000_000:
        raise ValueError("Cart value must be finite and nonnegative.")
    if type(itemCount) is not int or not 1 <= itemCount <= 2_147_483_647:
        raise ValueError("Item count must be a positive Avro int.")
    if not isinstance(lastActivityAt, str):
        raise ValueError("Last activity must be a timezone-aware UTC timestamp.")
    instant = datetime.datetime.fromisoformat(lastActivityAt.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Last activity must be a timezone-aware UTC timestamp.")
    businessEventsClient.PublishEvent(type="${event_name}", event_data={
        "customer_id": customerId, "customer_email": customerEmail, "cart_id": cartId,
        "cart_value": float(cartValue), "item_count": itemCount, "last_activity_at": lastActivityAt,
    }, data_version="v1")
    return {"event_published": True}
