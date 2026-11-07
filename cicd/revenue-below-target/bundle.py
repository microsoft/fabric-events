import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = (
    "deploy.bat", "test.bat", "card.py", "deploy_native.py", "deploy.py", "native.py",
    "fabric_api.py", "connections.py", "powerbi.py", "native_test.py",
    "test_native.ps1", "capture_native_source.ps1", "capture_native_teams.ps1",
    "bundle.py", "scenario.json", "schema.avsc", "config.card.example.json",
    "config.wizard.example.json", "config.schema.json",
)


def bundle(output):
    selected = [ROOT / name for name in FILES]
    for folder in ("items", "connections", "data"):
        selected.extend(path for path in (ROOT / folder).rglob("*") if path.is_file())
    if any(not path.is_file() for path in selected):
        raise ValueError("The distribution is missing a required source file.")
    if any(
        "__pycache__" in path.parts or path.suffix.lower() in (".pyc", ".html", ".md")
        or ".deployments" in path.relative_to(ROOT).parts
        for path in selected
    ):
        raise ValueError("Documentation, compiled caches and private evidence do not belong in the distribution.")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(selected):
            archive.write(path, "cicd/revenue-below-target/" + path.relative_to(ROOT).as_posix())
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    print(bundle(parser.parse_args().output))
