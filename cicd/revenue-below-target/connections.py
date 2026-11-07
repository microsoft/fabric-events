"""Bind the native workload association without creating Fabric items."""

import json
import time
import uuid
from pathlib import Path

import native
from fabric_api import FabricRequestError

ROOT = Path(__file__).resolve().parent
EVENT_NAME = "Retail.Sales.VolumeAlert"


def session(api, workspace_id, items, event_name=EVENT_NAME):
    if api.environment not in ("daily", "msit") or not api.metadata_host:
        raise ValueError("Automatic Business Event association currently targets Daily and MSIT.")
    artifacts = api.request(
        "GET", api.metadata_host + f"/metadata/workspaces/{workspace_id}/artifacts",
    ).body
    records = {}
    for role, expected_type in (("schema", "EventDefinition"), ("database", "KustoDatabase")):
        matches = [
            item for item in artifacts
            if item["objectId"] == items[role] and item["artifactType"] == expected_type
        ]
        if len(matches) != 1:
            raise ValueError(f"The native {role} identity could not be verified.")
        records[role] = matches[0]
    capacity_id = str(uuid.UUID(records["database"]["capacityObjectId"]))
    if (
        records["database"].get("parentArtifactObjectId") != items["eventhouse"]
        or capacity_id != str(uuid.UUID(records["schema"]["capacityObjectId"]))
    ):
        raise ValueError("The native database parent or capacity does not match the scenario.")
    bindings = {
        "${database_id}": records["database"]["objectId"],
        "${schema_id}": records["schema"]["objectId"], "${workspace_id}": workspace_id,
        "${event_id}": f"/workspaces/{workspace_id}/eventschemasets/{records['schema']['objectId']}/eventtypes/{event_name}",
        "${async_id}": str(uuid.uuid4()), "${iframe_id}": str(uuid.uuid4()),
    }
    request = native.bind(json.loads(
        (ROOT / "connections" / "kusto-token-request.json").read_text(encoding="utf-8"),
    ), bindings)
    result = api.request(
        "POST", api.metadata_host + "/metadata/v201606/generatemwctokenv2", request,
    ).body
    token = result["Token"]
    if not isinstance(token, str) or not token:
        raise RuntimeError("The native Kusto workload did not return authorization.")
    if str(uuid.UUID(result["CapacityObjectId"])) != capacity_id:
        raise ValueError("The routed workload capacity differs from the verified native artifacts.")
    definition = native.bind(native.bind_identities(json.loads(
        (ROOT / "connections" / "business-event.json").read_text(encoding="utf-8"),
    ), {EVENT_NAME: event_name}), bindings)
    return capacity_id, result["TargetUriHost"], token, definition


def ensure(api, workspace_id, items, create=False, pending=False, on_pending=None, known=None, event_name=EVENT_NAME):
    capacity_id, target_host, token, definition = session(api, workspace_id, items, event_name)
    deadline = time.monotonic() + 120
    while True:
        response = api.kusto_connection_request(
            "GET", capacity_id, items["database"], token, target_host=target_host,
        ).body
        records = response if isinstance(response, list) else response["value"]
        records = [{key.casefold(): value for key, value in item.items()} for item in records]
        matches = [item for item in records if item.get("businesseventname") == event_name]
        if len(matches) > 1:
            raise ValueError("The native Business Event association is ambiguous.")
        if matches:
            actual = matches[0]
            missing = [
                key for key in ("BusinessEventTypes", "EventSchemaSetArtifactId", "EventSchemaSetWorkspaceId", "DataFormat", "AdvancedFilters")
                if key.casefold() not in actual
            ]
            if missing:
                previous = known or {}
                if not pending and not (
                    previous.get("data_connection_name") == actual.get("dataconnectionname")
                    and previous.get("database_id") == items["database"]
                    and previous.get("schema_id") == items["schema"]
                    and previous.get("event_id") == definition["BusinessEventTypes"][0]
                ):
                    raise ValueError("A summary-only association cannot establish ownership without a managed creation request.")
                if not actual.get("dataconnectionname") or actual.get("healthstatus") != 1:
                    raise ValueError("The managed native association is not healthy or has no connection identity.")
            if "dataformat" not in actual:
                raise ValueError("The native association has an unverifiable DataFormat.")
            for key in ("BusinessEventTypes", "EventSchemaSetArtifactId", "EventSchemaSetWorkspaceId", "DataFormat"):
                if key.casefold() not in actual:
                    continue
                value = actual[key.casefold()]
                if key == "DataFormat" and isinstance(value, str):
                    value = value.upper()
                if value != definition[key]:
                    raise ValueError(f"The native association has a different or unverifiable {key}.")
            if actual.get("advancedfilters"):
                raise ValueError("Eventhouse history must receive all findings without an alert filter.")
            return {
                "name": event_name, "database_id": items["database"],
                "schema_id": items["schema"], "event_id": definition["BusinessEventTypes"][0],
                "data_connection_name": actual.get("dataconnectionname"),
                "binding_readback": "managed_request_and_healthy_summary" if missing else "complete_definition",
                "status": "native_association_verified_delivery_unverified",
            }
        if not pending and create:
            pending = True
            if on_pending is not None:
                on_pending(True)
            try:
                api.kusto_connection_request(
                    "POST", capacity_id, items["database"], token, definition, target_host=target_host,
                )
            except FabricRequestError as error:
                if error.status in (400, 401, 403, 404, 405, 409, 413, 415, 422, 429) and on_pending is not None:
                    on_pending(False)
                raise
        elif not pending:
            raise ValueError("The native Eventhouse association does not exist. Run connect-eventhouse or deploy.")
        if time.monotonic() >= deadline:
            raise TimeoutError("The native association request is unresolved; resume verification, not creation.")
        api.sleep(3)
