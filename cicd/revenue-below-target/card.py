import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

import deploy
import deploy_native
import native_test

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare an empty native Revenue deployment or separately run its actual source and response test.")
    parser.add_argument("phase", choices=("deploy", "test"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.card.example.json")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--browser-handle", type=int)
    parser.add_argument("--teams-handle", type=int)
    parser.add_argument("--teams-file", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "deploy" and any((args.verify_only, args.browser_handle, args.teams_handle, args.teams_file)):
        raise ValueError("Native source and Teams test options do not belong to deployment.")
    if args.verify_only and args.resume:
        raise ValueError("Verification does not resume or change source inputs.")
    if args.teams_file and not args.verify_only:
        raise ValueError("Import already captured Teams evidence only with --verify-only.")
    config_path = args.config.resolve()
    config = deploy.load_config(config_path)
    if not config["workspace_id"]:
        raise ValueError("Set the approved workspace_id in the reviewed configuration.")
    directory = ROOT / ".deployments" / config["environment"] / config["workspace_id"]
    current = directory / (config["resource_prefix"] + ".active.json")
    if args.phase == "deploy":
        if args.resume and not args.state_file:
            raise ValueError("Deployment resume requires its exact recorded --state-file.")
        state = args.state_file.resolve() if args.state_file else directory / (
            config["resource_prefix"] + ".deploy-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + uuid.uuid4().hex + ".json"
        )
        if state.exists() and not args.resume:
            raise ValueError("Deployment requires a fresh ledger; no recorded attempt is overwritten.")
        if args.resume and not state.is_file():
            raise ValueError("The explicit deployment attempt does not exist.")
        target = deploy.Deployment(config, state)
        if target.state.get("test"):
            raise ValueError("Do not redeploy over a source that has entered its separate test phase.")
        target.save()
        deploy.atomic_json(current, {"state_file": str(state)})
        return deploy_native.main(["--config", str(config_path), "--state-file", str(state), *(["--resume"] if args.resume else [])])
    if args.state_file:
        state = args.state_file.resolve()
    else:
        if not current.is_file():
            raise ValueError("No current deployment; supply its exact --state-file or deploy first.")
        state = Path(json.loads(current.read_text())["state_file"])
        if not state.is_absolute():
            raise ValueError("The current reference must contain an absolute ledger path.")
    if not state.is_file():
        raise ValueError("The selected deployment is missing. No older attempt is substituted.")
    deploy.Deployment(config, state)
    if args.verify_only:
        target = native_test.TestRun(state)
        if args.teams_file:
            target.teams(args.teams_file)
        return target.verify()
    if sys.platform != "win32":
        raise ValueError("Actual native source and Teams observation requires an authenticated interactive Windows session.")
    command = ["pwsh", "-NoProfile", "-File", str(ROOT / "test_native.ps1"), "-StateFile", str(state)]
    if args.resume:
        command.append("-Resume")
    if args.browser_handle:
        command.extend(["-BrowserHandle", str(args.browser_handle)])
    if args.teams_handle:
        command.extend(["-TeamsHandle", str(args.teams_handle)])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
