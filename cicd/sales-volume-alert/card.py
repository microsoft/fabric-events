"""Separate environment deployment from an explicitly requested end-to-end test."""

import argparse
from datetime import datetime, timezone
import json
import sys
import uuid
from pathlib import Path

import deploy
import native

ROOT = Path(__file__).resolve().parent


def state_directory(config):
    if not config["workspace_id"]:
        raise ValueError("Set workspace_id in the selected configuration before deploying or testing.")
    return (
        ROOT / ".deployments"
        / config["environment"] / config["workspace_id"]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("deploy", "test"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.card.example.json")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--verify-only", action="store_true", help="Check the saved test run without publishing.")
    parser.add_argument("--teams-confirmed", action="store_true", help="Explicit operator attestation, with --verify-only.")
    args = parser.parse_args(argv)
    if args.phase == "deploy" and (args.verify_only or args.teams_confirmed):
        raise ValueError("Verification flags belong to test.bat, not deploy.bat.")
    if args.teams_confirmed and not args.verify_only:
        raise ValueError("Use --teams-confirmed only with --verify-only, after checking the actual Teams cards.")
    config_path = args.config.resolve()
    config = deploy.load_config(config_path)
    directory = state_directory(config)
    current = directory / (config["resource_prefix"] + ".active.json")
    if args.phase == "deploy":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        state_path = args.state_file.resolve() if args.state_file else (
            directory / f"{config['resource_prefix']}.deploy-{stamp}-{uuid.uuid4().hex}.json"
        )
        if state_path.exists():
            raise ValueError("Each deployment needs an unused --state-file. Omit it to create a new ledger automatically.")
    elif args.state_file:
        state_path = args.state_file.resolve()
    else:
        if not current.is_file():
            raise ValueError("No current deployment reference. Run deploy.bat or select its printed --state-file explicitly.")
        state_path = Path(json.loads(current.read_text(encoding="utf-8"))["state_file"])
        if not state_path.is_absolute():
            raise ValueError("The current deployment reference must contain an absolute ledger path.")
    target = deploy.Deployment(config, state_path=state_path)
    shared = ["--config", str(config_path), "--state-file", str(target.state_path)]
    confirmation = ["--confirm-workspace", config["workspace_id"]]
    print(f"Configuration: {config_path}")
    print(f"Ledger: {target.state_path}")

    if args.phase == "deploy":
        target.save()
        directory.mkdir(parents=True, exist_ok=True)
        temporary = current.with_name(current.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps({"state_file": str(target.state_path)}, indent=2), encoding="utf-8")
        temporary.replace(current)
        for command in ("plan", "preflight", "deploy"):
            result = deploy.main([command, *shared, *(confirmation if command == "deploy" else [])])
            if result:
                return result
        print("Environment configured. No dummy data loaded and no events published by this deployment.")
        print("Inspect the Fabric items before explicitly running test.bat.")
        return 0

    if target.state.get("pending_job") or target.state.get("pending_import"):
        raise ValueError("Resume the saved job or native import before testing or verifying a previous publication.")
    required = {*native.FOUNDATIONS, "activator"}
    if not target.state_path.exists() or not required.issubset(target.state["items"]):
        raise ValueError("No complete deployment ledger. Run deploy.bat first, or select its existing --config and --state-file.")
    if args.verify_only:
        if not target.state.get("last_run"):
            raise ValueError("There is no saved publication to verify. Run test.bat after inspecting the deployment.")
    else:
        print("TEST: creating a fresh dummy batch and publishing it once. Real Teams notifications can result.")
        result = deploy.main(["run", *shared, *confirmation, "--seed-dummy-data"])
        if result:
            return result
    result = deploy.main(["verify", *shared, *(["--teams-confirmed"] if args.teams_confirmed else [])])
    if result == 2:
        print("Eventhouse verified; Teams delivery is not independently verified.")
        print("Check the two matching Teams cards. Repeat test.bat --verify-only without publishing again.")
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
