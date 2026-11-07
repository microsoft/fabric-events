"""Build a portal resource archive from the same sources used by deploy.py."""

import argparse
import json
import zipfile
from pathlib import Path

import native

ROOT = Path(__file__).resolve().parent
PREFIX = "cicd/sales-volume-alert/"
MODULES = ("deploy.py", "fabric_api.py", "native.py", "scenario.py", "connections.py", "bundle.py", "portal-deploy.py", "card.py")


def build(output):
    items = native.local_items()
    native.validate_plan(items)
    files = {
        PREFIX + name: (ROOT / name).read_bytes()
        for name in (
            *MODULES, "config.example.json", "config.wizard.example.json", "config.card.example.json", "config.schema.json",
            "schema.avsc", "data/store-windows.json",
        )
    }
    for name in ("deploy.bat", "test.bat"):
        files[PREFIX + name] = (ROOT / name).read_bytes()
    for item in items.values():
        for name, content in item["files"].items():
            files[PREFIX + "items/" + item["folder"] + "/" + name] = content
    for name in ("analysis.kql", "verify.kql"):
        files[PREFIX + "queries/" + name] = (ROOT / "queries" / name).read_bytes()
    for name in ("business-event.json", "kusto-token-request.json", "workspace-assignment.json"):
        files[PREFIX + "connections/" + name] = (ROOT / "connections" / name).read_bytes()
    output = Path(output).resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("The resource archive output must have a .zip extension.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    temporary.replace(output)
    return {"archive": str(output), "file_count": len(files), "cloud_writes": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2))
