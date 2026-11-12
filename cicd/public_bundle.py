"""Package one public scenario with its shared implementation, never the archive."""

import argparse
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def bundle(folder, output):
    folder, output = Path(folder), Path(output)
    files = {name: ROOT / name for name in (
        "public_api.py", "public_deploy.py", "public_bundle.py", "public_contracts.json",
    )}
    files.update({name: folder / name for name in (
        "deploy.py", "deploy.bat", "bundle.py", "scenario.json",
        "schema.avsc", "config.card.example.json", "config.wizard.example.json", "config.schema.json",
    )})
    scenario = json.loads((folder / "scenario.json").read_text())
    if scenario.get("command_behavior", {}).get("test") != "manual_native_kql_inputs_only":
        files["test.bat"] = folder / "test.bat"
    assets = scenario.get("distribution_assets", [])
    shared = {"sales_deploy.py", "public_consumers.py", "payment_deploy.py", "inventory_deploy.py",
              "teams_ui.py", "teams_ui.ps1", "action_deploy.py", "business_records.py",
              "revenue_deploy.py", "revenue_source.py", "revenue_components.py", "revenue_records.py",
              "discount_deploy.py", "fraud_deploy.py", "cart_deploy.py", "public_udf.py",
              "viewer_deploy.py", "viewer_records.py", "failure_deploy.py", "failure_records.py",
              "loyalty_deploy.py", "loyalty_catalog.py"}
    allowed = shared | {"notebook-content.py", "DatabaseSchema.kql", "fixtures.json", "eventstream.json",
                        "function_app.py", "response-notebook.py", "response-function.py",
                        "source-query.kql", "sample-input.kql", "catalog.json", "setup.sql", "record-cart-task.sql",
                        "record-pipeline-failure.sql"}
    if not isinstance(assets, list) or any(name not in allowed for name in assets):
        raise ValueError("The public scenario requests an unreviewed distribution asset.")
    for name in assets:
        files[name] = ROOT / name if name in shared else folder / name
    if any(not path.is_file() for path in files.values()):
        raise ValueError("The public scenario distribution is incomplete.")
    if output.exists() and scenario.get("package_policy") != "single_current_package":
        raise ValueError("Choose an unused package output path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if scenario.get("package_policy") != "single_current_package":
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(files.items()):
                archive.write(path, "cicd/" + folder.name + "/" + name)
        return output
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(files.items()):
                archive.write(path, "cicd/" + folder.name + "/" + name)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def main(folder):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    print(bundle(folder, parser.parse_args().output))
