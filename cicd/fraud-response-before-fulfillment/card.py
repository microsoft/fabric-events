"""Separate fresh deployment, explicit resume and synthetic end-to-end testing."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import deploy
import foundation
import native

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("plan", "deploy", "resume", "test", "pause"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.card.example.json")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--new-run", action="store_true")
    args = parser.parse_args(argv)
    if args.phase != "test" and (args.verify_only or args.new_run):
        raise ValueError("Test flags do not belong to deployment operations.")
    if args.verify_only and args.new_run:
        raise ValueError("Verification cannot create a new test run.")
    config = deploy.load_config(args.config.resolve())
    if args.phase == "plan":
        internal = {**config, "teams_delivery": "individual", "teams_recipients": []}
        for role in native.manifest()["items"]:
            native.definition(role, internal)
        print(json.dumps({
            "mode": "offline_plan", "cloud_writes": False, "resources": config["resource_names"],
            "flows": config["flow_names"], "azure": config["azure"],
            "test_orders_created": 0, "events_published": 0,
        }, indent=2))
        return 0
    directory = ROOT / ".deployments" / str(config["workspace_id"]) / config["resource_prefix"]
    pointer = directory / "active.json"
    if args.phase == "deploy":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        state = args.state_file.resolve() if args.state_file else (
            directory / (stamp + "-" + uuid.uuid4().hex) / "deployment.json"
        )
        if state.exists():
            raise ValueError("Deployment requires a fresh ledger. Use resume for an existing attempt.")
    elif args.state_file:
        state = args.state_file.resolve()
    else:
        if not pointer.is_file():
            raise ValueError("Select the exact deployment ledger; there is no active deployment.")
        state = Path(json.loads(pointer.read_text(encoding="utf-8"))["state_file"])
        if not state.is_absolute():
            raise ValueError("The active pointer must identify an absolute ledger path.")
    if args.phase != "deploy" and not state.is_file():
        raise ValueError("The selected deployment does not exist; an older attempt will not be substituted.")
    target = deploy.Deployment(config, state)
    print(f"Ledger: {state}", flush=True)
    if args.phase == "deploy":
        target.save()
        foundation.atomic_json(pointer, {"state_file": str(state)})
    if args.phase in ("deploy", "resume"):
        print(json.dumps(target.deploy(resume=args.phase == "resume"), indent=2))
    elif args.phase == "pause":
        target.preflight()
        target.apply_rules(False)
        print("The exact three owned native rules are paused.")
    else:
        from test_run import TestRun
        print(json.dumps(TestRun(target).run(new_run=args.new_run, verify_only=args.verify_only), indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
