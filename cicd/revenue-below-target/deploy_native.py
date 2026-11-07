import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import deploy
from fabric_api import KUSTO
import native
from powerbi import refresh_model

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deploy and read back the nine genuine Revenue resources without test inputs.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    config = deploy.load_config(args.config)
    if not config["workspace_id"]:
        raise ValueError("Choose the actual approved workspace before native deployment.")
    if config["teams_delivery"] != "individual":
        raise ValueError("This private candidate has not validated a native two-rule channel definition.")
    path = args.state_file or ROOT / ".deployments" / (
        config["resource_prefix"] + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex + ".json"
    )
    if args.resume and (not args.state_file or not path.is_file()):
        raise ValueError("Resume requires the exact existing recorded candidate ledger.")
    if path.exists() and not args.resume:
        raise ValueError("Candidate deployment requires a fresh ledger; unresolved creation must never be replayed.")
    target = deploy.Deployment(config, path)
    target.save()
    if target.state.get("pending_import"):
        if not args.resume:
            raise ValueError("An unresolved import requires explicit resume.")
        target.resume_import()
    preflight = target.preflight()
    target.state["items"]["workspace_name"] = preflight["workspace"]
    target.state["status"] = "genuine_native_powerbi_candidate_deployment_started_no_inputs"
    target.save()
    target.import_items(("schema", "eventhouse", "database"))
    target.readback("schema")
    target.grant_access()
    database = target.api.request("GET", target.base + "kqlDatabases/" + target.state["items"]["database"]).body
    if database["properties"]["parentEventhouseItemId"] != target.state["items"]["eventhouse"]:
        raise ValueError("The native child database was not bound to this candidate's actual Eventhouse.")
    endpoint = database["properties"]["queryServiceUri"].rstrip("/")
    target.api.check_url(endpoint, KUSTO)
    target.state["items"]["query_endpoint"] = endpoint
    target.save()
    target.connect_history(create=True)
    for group in (("source",), ("report",), ("notebook",), ("pipeline",), ("activator",), ("publisher",)):
        target.import_items(group)
        for role in group:
            target.readback(role)
        if group == ("source",):
            source_definition = native.definition("source", config, target.state["items"], target.state["resolved_recipients"])
            source_model = json.loads(native.decoded(source_definition, "model.bim"))
            expression = source_model["model"]["tables"][0]["partitions"][0]["source"]["expression"]
            if not any(line.endswith(", {})") for line in expression):
                raise RuntimeError("Only the actually empty deployment fixture may be prepared.")
            if not target.state.get("source_preparation"):
                record = {"status": "empty_model_native_refresh_pending_no_inputs"}
                target.state["source_preparation"] = record
                target.save()
                refresh_model(target.api, config["workspace_id"], target.state["items"]["source"], record, target.save)
                record["status"] = "actual_empty_model_refresh_completed_no_inputs"
                target.save()
            elif target.state["source_preparation"]["status"] != "actual_empty_model_refresh_completed_no_inputs":
                record = target.state["source_preparation"]
                refresh_model(target.api, config["workspace_id"], target.state["items"]["source"], record, target.save, resume=True)
                record["status"] = "actual_empty_model_refresh_completed_no_inputs"
                target.save()
            result = target.api.request("POST", target.api.metadata_host + (
                f"/v1.0/myorg/groups/{config['workspace_id']}/datasets/{target.state['items']['source']}/executeQueries"
            ), {"queries": [{"query": "EVALUATE RevenuePerformance"}], "serializerSettings": {"includeNulls": True}}).body
            if result.get("error") or result["results"][0]["tables"][0]["rows"]:
                raise RuntimeError("The actually prepared deployment model is not an empty source fixture.")
            target.state["source_preparation"]["actual_empty_query_verified"] = True
            target.save()
    for role in ("eventhouse", "database"):
        target.readback(role)
    source = json.loads(native.decoded(
        native.definition("source", config, target.state["items"], target.state["resolved_recipients"]), "model.bim",
    ))
    expression = source["model"]["tables"][0]["partitions"][0]["source"]["expression"]
    if not any(line.endswith(", {})") for line in expression):
        raise RuntimeError("Deployment must not populate the external source fixture.")
    target.state["status"] = "fresh_native_candidate_deployment_readback_verified_no_inputs_e2e_unverified"
    target.save()
    print(json.dumps({
        "status": target.state["status"], "ledger": str(path),
        "resource_count": len(native.manifest()["items"]), "test_events_submitted": 0, "end_to_end_verified": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
