"""Expose the native notebook's pure business logic for offline checks."""

import runpy
from pathlib import Path

_source = runpy.run_path(str(
    Path(__file__).resolve().parent
    / "items" / "SalesVolumeCicdV1Publisher.Notebook" / "notebook-content.py"
))
EVENT_NAME = _source["EVENT_NAME"]
finding = _source["finding"]
validate_fixtures = _source["validate_fixtures"]
expected_findings = _source["expected_findings"]
