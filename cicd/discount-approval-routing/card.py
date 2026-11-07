"""Scenario-local fresh deployments and an independently invoked end-to-end test."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import deploy

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("deploy", "test"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.card.example.json")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--teams-confirmed", action="store_true")
    parser.add_argument("--eventstream-confirmed", action="store_true")
    parser.add_argument("--new-run", action="store_true")
    args = parser.parse_args(argv)
    if args.phase == "deploy" and any((
        args.verify_only, args.teams_confirmed, args.eventstream_confirmed, args.new_run,
    )):
        raise ValueError("Test flags do not belong to deploy.bat.")
    if (args.teams_confirmed or args.eventstream_confirmed) and not args.verify_only:
        raise ValueError("Consumer attestations require --verify-only, after checking actual notifications and messages.")
    if args.verify_only and args.new_run:
        raise ValueError("--verify-only cannot create a new batch.")
    config_path = args.config.resolve()
    config = deploy.load_config(config_path)
    if not config["workspace_id"]:
        raise ValueError("Set the approved workspace_id in the downloaded wizard configuration.")
    directory = ROOT / ".deployments" / config["environment"] / config["workspace_id"]
    current = directory / (config["resource_prefix"] + ".active.json")
    if args.phase == "deploy":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        state = args.state_file.resolve() if args.state_file else (
            directory / f"{config['resource_prefix']}.deploy-{stamp}-{uuid.uuid4().hex}.json"
        )
        if state.exists():
            raise ValueError("Deployment always requires a fresh ledger. Existing files are never overwritten.")
    elif args.state_file:
        state = args.state_file.resolve()
    else:
        if not current.is_file():
            raise ValueError("No active deployment. Run deploy.bat or select its exact --state-file.")
        state = Path(json.loads(current.read_text(encoding="utf-8"))["state_file"])
        if not state.is_absolute():
            raise ValueError("The active reference must contain an absolute ledger path.")
    if args.phase == "test" and not state.is_file():
        raise ValueError("The selected deployment ledger does not exist. No older deployment will be substituted.")
    shared = ["--config", str(config_path), "--state-file", str(state)]
    confirmation = ["--confirm-workspace", config["workspace_id"]]
    print(f"Configuration: {config_path}\nLedger: {state}")
    if args.phase == "deploy":
        target = deploy.Deployment(config, state)
        target.save()
        deploy.atomic_json(current, {"state_file": str(state)})
        for command in ("plan", "preflight", "deploy"):
            result = deploy.main([command, *shared, *(confirmation if command == "deploy" else [])])
            if result:
                return result
        print("Environment configured. No dummy data loaded and no test events published.")
        return 0
    if not args.verify_only:
        print("TEST: inject one fresh external dummy batch. Real Teams notifications can result.")
        result = deploy.main(["run", *shared, *confirmation, *(["--new-run"] if args.new_run else [])])
        if result:
            return result
    return deploy.main([
        "verify", *shared, *(["--teams-confirmed"] if args.teams_confirmed else []),
        *(["--eventstream-confirmed"] if args.eventstream_confirmed else []),
    ])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
