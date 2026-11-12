# ${viewer_helpers}

import fabric.functions as fn

udf = fn.UserDataFunctions()


@udf.connection(argName="businessEventsClient", alias="${schema_name}")
@udf.function()
def publish_viewer_behavior_event(
    businessEventsClient: fn.FabricBusinessEventsClient,
    viewerId: str,
    sessionId: str,
    contentId: str,
    action: str,
    occurredAt: str,
) -> dict:
    event = validate_event({
        "viewer_id": viewerId, "session_id": sessionId, "content_id": contentId,
        "action": action, "occurred_at": occurredAt,
    })
    businessEventsClient.PublishEvent(type="${event_name}", event_data=event, data_version="v1")
    return {"event_published": True}
