import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_public_cicd import ROOT
import public_consumers
import teams_ui


class TeamsUiTests(unittest.TestCase):
    def snapshot(self):
        return {
            "origin": "actual_rendered_teams_cards", "recipient": "tester@example.com",
            "recipient_verified_from_visible_teams_profile": True,
            "cards": [{
                "id": "message-body-1794118000000",
                "actual_card_text": (
                    " This is a card message. product_id: current-run product_name: Keyboard "
                    "current_stock: -2 threshold: 8 View rule in Activator "
                    "Tester via Fabric Activator Today at 10:00 PM."
                ),
            }],
        }

    def read_snapshot(self, snapshot, code=0):
        response = SimpleNamespace(returncode=code, stdout=json.dumps(snapshot), stderr="Chat is inaccessible")
        with patch.object(teams_ui.sys, "platform", "win32"), patch.object(
            teams_ui.shutil, "which", return_value="powershell.exe",
        ), patch.object(teams_ui.subprocess, "run", return_value=response):
            return teams_ui.messages("tester@example.com")

    def test_real_ui_provenance_does_not_fabricate_graph_sender_or_payload(self):
        row = self.read_snapshot(self.snapshot())[0]
        self.assertEqual(row["verifier"], "public_windows_ui_automation")
        self.assertNotIn("from", row)
        self.assertNotIn("body", row)
        self.assertTrue(public_consumers.is_teams_receipt(row))
        self.assertIn("Keyboard", public_consumers.teams_content(row))
        evidence = public_consumers.teams_evidence(row)
        self.assertEqual(evidence["actual_card_text"], row["actual_card_text"])
        self.assertTrue(public_consumers.ui_fields_match(row, {
            "product_id": "current-run", "product_name": "Keyboard", "current_stock": -2, "threshold": 8,
        }))
        self.assertFalse(public_consumers.ui_fields_match(row, {"product_name": "current-run"}))
        self.assertFalse(public_consumers.ui_fields_match(row, {"current_stock": 8}))

    def test_wrong_account_unverified_profile_and_non_native_cards_fail_closed(self):
        for key, value in (
            ("recipient", "someoneelse@example.com"),
            ("recipient_verified_from_visible_teams_profile", False),
            ("origin", "fixture_replay"),
        ):
            with self.subTest(key=key):
                snapshot = {**self.snapshot(), key: value}
                with self.assertRaisesRegex(ValueError, "independently verify"):
                    self.read_snapshot(snapshot)
        snapshot = self.snapshot()
        snapshot["cards"][0]["actual_card_text"] = "A user typed a finding via Fabric Activator"
        with self.assertRaisesRegex(ValueError, "actual Fabric Activator card"):
            self.read_snapshot(snapshot)
        with self.assertRaisesRegex(RuntimeError, "No receipt is verified"):
            self.read_snapshot(self.snapshot(), code=1)

    def test_conflicting_rendered_message_identities_are_not_silently_deduplicated(self):
        snapshot = self.snapshot()
        duplicate = copy.deepcopy(snapshot["cards"][0])
        duplicate["actual_card_text"] = duplicate["actual_card_text"].replace("Keyboard", "Mouse")
        snapshot["cards"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "conflicting card content"):
            self.read_snapshot(snapshot)

    def test_windows_ui_never_falls_back_to_graph_on_other_platforms(self):
        with patch.object(teams_ui.sys, "platform", "linux"), patch.object(teams_ui.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "local Windows desktop"):
                teams_ui.messages("tester@example.com")
            run.assert_not_called()
