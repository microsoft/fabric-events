# Fabric notebook source


# MARKDOWN ********************

# # Sales Volume Alert: manual deployment from Fabric
# Upload sales-volume-alert.zip to this notebook's built-in resources.
# Review the plan before selecting a write command and confirming its workspace.
# This entry point runs the same native definitions and orchestrator as the local CLI.
# Use deploy for configured resources; optional dummy initialization never publishes.

# PARAMETERS CELL ********************

environment = "msit"
workspace_id = ""
command = "plan"
confirm_workspace_id = ""
teams_delivery = "individual"
teams_recipients_json = '["@me"]'
teams_definition_file = ""
eventhouse_connected = False
with_deployment_plan = False
seed_dummy_data = False
native_items_json = "[]"
capacity_id = ""
config_file = ""


# CELL ********************

import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def run_from_portal(credentials, resources=None):
    resources = Path("builtin") if resources is None else Path(resources)
    archive_path = resources / "sales-volume-alert.zip"
    state_path = (resources / "sales-volume-alert-state.json").resolve()
    module_names = ("fabric_api", "native", "scenario", "connections", "deploy")
    with tempfile.TemporaryDirectory(prefix="fabric-events-deploy-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            names = [entry.filename.casefold() for entry in entries]
            if len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024:
                raise ValueError("The resource archive has duplicate entries or exceeds 32 MiB.")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute() or ".." in path.parts
                    or "\\" in entry.filename or ":" in entry.filename
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("The resource archive contains an unsafe path or symbolic link.")
            archive.extractall(root)
        source = root / "cicd" / "sales-volume-alert"
        previous = {name: sys.modules.pop(name) for name in module_names if name in sys.modules}
        sys.path.insert(0, str(source))
        try:
            import deploy
            from fabric_api import FabricApi, NotebookTokens

            if config_file:
                if Path(config_file).name != config_file or not config_file.endswith(".json"):
                    raise ValueError("config_file must be a JSON file name uploaded to built-in resources.")
                config = json.loads((resources / config_file).read_text(encoding="utf-8-sig"))
                if not isinstance(config, dict):
                    raise ValueError("The uploaded configuration must be a JSON object.")
                if config.get("teams_definition_file"):
                    config["teams_definition_file"] = str((resources / config["teams_definition_file"]).resolve())
            else:
                config = json.loads((source / "config.wizard.example.json").read_text(encoding="utf-8"))
                config.update(
                    environment=environment, workspace_id=workspace_id or None,
                    teams_delivery=teams_delivery, teams_recipients=json.loads(teams_recipients_json),
                    teams_definition_file=str((resources / teams_definition_file).resolve())
                    if teams_definition_file else None,
                )
            config_path = source / "config.portal.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            arguments = [
                command, "--config", str(config_path),
                "--state-file", str(state_path),
            ]
            if confirm_workspace_id:
                arguments += ["--confirm-workspace", confirm_workspace_id]
            if eventhouse_connected:
                arguments.append("--eventhouse-connected")
            if with_deployment_plan:
                arguments.append("--with-deployment-plan")
            if seed_dummy_data:
                arguments.append("--seed-dummy-data")
            if command == "import-native":
                arguments += ["--items", *json.loads(native_items_json)]
            if command == "assign-capacity":
                arguments += ["--capacity-id", capacity_id]
            validated = deploy.load_config(config_path)
            api = FabricApi(tokens=NotebookTokens(credentials), environment=validated["environment"])
            exit_code = deploy.main(arguments, api=api)
            if exit_code:
                raise RuntimeError(f"Deployment command returned exit code {exit_code}; inspect its evidence.")
        finally:
            sys.path.remove(str(source))
            for name in module_names:
                sys.modules.pop(name, None)
            sys.modules.update(previous)
            if state_path.exists():
                print("Save this non-secret ledger before ending the session or switching deployment clients:")
                print(state_path.read_text(encoding="utf-8"))


# CELL ********************

if __name__ == "__main__":
    import notebookutils

    run_from_portal(notebookutils.credentials)
