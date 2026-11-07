"""Create an optional, scenario-contained distribution without local runtime state."""

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = (
    "deploy.bat", "test.bat", "card.py", "deploy.py", "native.py",
    "fabric_api.py", "connections.py", "bundle.py", "scenario.json",
    "config.card.example.json", "config.wizard.example.json", "config.schema.json",
    "schema.avsc",
)


def bundle(output):
    selected = [ROOT / name for name in FILES]
    if (ROOT / "requirements.txt").is_file():
        selected.append(ROOT / "requirements.txt")
    for folder in ("items", "connections", "data"):
        selected.extend(path for path in (ROOT / folder).rglob("*") if path.is_file())
    if any(not path.is_file() for path in selected):
        raise ValueError("The distribution is missing a required source file.")
    if any("__pycache__" in path.parts or path.suffix == ".pyc" for path in selected):
        raise ValueError("Compiled caches do not belong in native artifact folders.")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(selected):
            archive.write(path, "cicd/" + ROOT.name + "/" + path.relative_to(ROOT).as_posix())
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(bundle(parser.parse_args().output))
