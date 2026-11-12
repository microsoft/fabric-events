import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from public_deploy import main

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent))
