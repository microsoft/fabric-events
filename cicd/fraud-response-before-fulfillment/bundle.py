"""Build an executable-only distribution using an explicit source allowlist."""

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = (
    "deploy.bat", "test.bat", "card.py", "deploy.py", "foundation.py", "native.py",
    "fabric_api.py", "flow_api.py", "azure_backend.py", "test_run.py",
    "connections.py", "bundle.py", "scenario.json", "schema.avsc",
    "config.card.example.json", "config.wizard.example.json", "config.schema.json",
)


def bundle(output):
    selected = [ROOT / name for name in FILES]
    for folder in ("items", "connections", "data", "tests", "test-order-api"):
        selected.extend(
            path for path in (ROOT / folder).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    if any(not path.is_file() for path in selected):
        raise ValueError("A required executable distribution asset is missing.")
    if any(path.suffix.lower() in (".html", ".md", ".har") or ".deployments" in path.parts for path in selected):
        raise ValueError("Documentation, browser captures and local state do not belong in the distribution.")
    output = Path(output).resolve()
    if output in {path.resolve() for path in selected}:
        raise ValueError("The output must not overwrite an executable source asset.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(selected):
            archive.write(path, "cicd/" + ROOT.name + "/" + path.relative_to(ROOT).as_posix())
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(bundle(parser.parse_args().output))
