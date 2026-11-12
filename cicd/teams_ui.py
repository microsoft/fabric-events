"""Read actual Activator cards through public Windows accessibility, without Graph."""

import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

VERIFIER = "public_windows_ui_automation"


def messages(recipient):
    if sys.platform != "win32":
        raise ValueError("Windows Teams UI verification requires a local Windows desktop.")
    shell = shutil.which("powershell.exe")
    if shell is None:
        raise ValueError("Windows PowerShell is required for Teams UI verification.")
    try:
        response = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_name("teams_ui.ps1")),
             "-Recipient", recipient],
            capture_output=True, encoding="utf-8-sig", timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Teams UI did not respond within 30 seconds. Retain this run and verify it again.") from error
    if response.returncode != 0:
        raise RuntimeError("Teams UI readback failed. No receipt is verified. " + response.stderr.strip())
    snapshot = json.loads(response.stdout)
    if (snapshot.get("origin") != "actual_rendered_teams_cards"
            or not isinstance(snapshot.get("recipient"), str)
            or snapshot["recipient"].casefold() != recipient.casefold()
            or snapshot.get("recipient_verified_from_visible_teams_profile") is not True
            or not isinstance(snapshot.get("cards"), list)):
        raise ValueError("Teams UI did not independently verify the approved account and actual cards.")
    result, seen = [], {}
    for card in snapshot["cards"]:
        identifier, text = card.get("id"), card.get("actual_card_text")
        if not isinstance(identifier, str) or not re.fullmatch(r"message-body-\d+", identifier):
            raise ValueError("The actual Teams card has no rendered message identity.")
        if (not isinstance(text, str) or not text.strip().startswith("This is a card message.")
                or "via Fabric Activator" not in text or "View rule in Activator" not in text):
            raise ValueError("The rendered message is not an actual Fabric Activator card.")
        if identifier in seen:
            if seen[identifier] != text:
                raise ValueError("A rendered receipt identity has conflicting card content.")
            continue
        seen[identifier] = text
        created = datetime.datetime.fromtimestamp(int(identifier.removeprefix("message-body-")) / 1000,
                                                 datetime.timezone.utc)
        result.append({
            "id": identifier, "createdDateTime": created.isoformat(), "actual_card_text": text,
            "verifier": VERIFIER, "recipient": snapshot["recipient"],
            "recipient_verified_from_visible_teams_profile": True,
        })
    return result
