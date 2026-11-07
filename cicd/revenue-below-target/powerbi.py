from datetime import datetime, timezone
import time


def refresh_model(api, workspace_id, model_id, record, save, *, resume=False):
    if not api.metadata_host:
        raise ValueError("This verified Power BI refresh route requires an explicitly configured Daily or MSIT environment.")
    if record.get("refresh_requested_at") and not resume:
        raise ValueError("The recorded model refresh must not be replayed.")
    route = api.metadata_host + f"/v1.0/myorg/groups/{workspace_id}/datasets/{model_id}/refreshes"
    if resume:
        if not record.get("refresh_requested_at") or "refresh_request_ids_before" not in record:
            raise ValueError("Explicit refresh resume requires the recorded request and pre-request identities.")
        before = set(record["refresh_request_ids_before"])
    else:
        before = {r["requestId"] for r in api.request("GET", route).body["value"]}
        record["refresh_request_ids_before"] = sorted(before)
        record["refresh_requested_at"] = datetime.now(timezone.utc).isoformat()
        save()
        reply = api.request("POST", route, {"notifyOption": "NoNotification"})
        if reply.status != 202:
            raise RuntimeError("The supported Power BI refresh did not return Accepted.")
        record["refresh_location"] = reply.headers.get("Location")
        save()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        fresh = [r for r in api.request("GET", route).body["value"] if r["requestId"] not in before]
        if len(fresh) > 1:
            raise RuntimeError("Concurrent real refreshes make this operation ambiguous.")
        if fresh:
            record["actual_refresh"] = fresh[0]
            save()
            if fresh[0]["status"] == "Completed":
                return fresh[0]
            if fresh[0]["status"] not in ("Unknown", "InProgress"):
                raise RuntimeError(f"The actual model refresh failed: {fresh[0]['status']}.")
        time.sleep(3)
    raise TimeoutError("The recorded actual model refresh remains pending; do not replay it.")
