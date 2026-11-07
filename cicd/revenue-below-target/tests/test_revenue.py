import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import native
import card
from powerbi import refresh_model


def reply(value=None, status=200):
    return SimpleNamespace(body={"value": value or []}, status=status, headers={})


class RevenueNativeTests(unittest.TestCase):
    def test_channel_is_explicitly_rejected_without_a_dm_fallback(self):
        config = json.loads((ROOT / "config.card.example.json").read_text())
        config.update(teams_delivery="channel", teams_recipients=[])
        with self.assertRaisesRegex(ValueError, "no DM fallback"):
            native.validate_config(config)

    def test_deployment_rejects_source_and_teams_test_options_before_cloud_work(self):
        with self.assertRaisesRegex(ValueError, "do not belong to deployment"):
            card.main(["deploy", "--verify-only"])
        with self.assertRaisesRegex(ValueError, "do not belong to deployment"):
            card.main(["deploy", "--teams-file", "private-evidence.json"])

    def test_refresh_persists_request_before_submission_and_reads_actual_completion(self):
        api = Mock(metadata_host="https://df-msit-scus-redirect.analysis.windows.net")
        api.request.side_effect = [reply([{"requestId": "old"}]), reply(status=202),
                                   reply([{"requestId": "actual", "status": "Completed"}])]
        record = {}
        saved = []
        refresh_model(api, "workspace", "model", record, lambda: saved.append(dict(record)))
        self.assertEqual(saved[0]["refresh_request_ids_before"], ["old"])
        self.assertIn("refresh_requested_at", saved[0])
        self.assertEqual(record["actual_refresh"]["requestId"], "actual")
        self.assertEqual([call.args[0] for call in api.request.call_args_list], ["GET", "POST", "GET"])

    def test_refresh_resume_reads_existing_operation_without_another_post(self):
        api = Mock(metadata_host="https://df-msit-scus-redirect.analysis.windows.net")
        api.request.return_value = reply([{"requestId": "actual", "status": "Completed"}])
        record = {"refresh_requested_at": "2026-01-01T00:00:00Z", "refresh_request_ids_before": ["old"]}
        refresh_model(api, "workspace", "model", record, Mock(), resume=True)
        self.assertEqual(record["actual_refresh"]["requestId"], "actual")
        api.request.assert_called_once()
        self.assertEqual(api.request.call_args.args[0], "GET")
        with self.assertRaisesRegex(ValueError, "must not be replayed"):
            refresh_model(api, "workspace", "model", record, Mock())

    def test_refresh_rejects_ambiguous_concurrent_results(self):
        api = Mock(metadata_host="https://df-msit-scus-redirect.analysis.windows.net")
        api.request.side_effect = [reply(), reply(status=202), reply([
            {"requestId": "one", "status": "Completed"}, {"requestId": "two", "status": "Completed"},
        ])]
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            refresh_model(api, "workspace", "model", {}, Mock())

    def test_genuine_bindings_preserve_two_field_identity_and_six_field_publication(self):
        config = json.loads((ROOT / "config.card.example.json").read_text())
        config["workspace_id"] = str(uuid.uuid4())
        config = native.validate_config(config)
        identifiers = {role: str(uuid.uuid4()) for role in native.items(config)}
        entities = json.loads(native.decoded(native.definition("publisher", config, identifiers), "ReflexEntities.json"))
        source = next(e for e in entities if e["type"] == "powerBiSource-v1")
        query = source["payload"]["query"]
        self.assertEqual(query["compositeKey"]["keys"], ["region", "period_start"])
        self.assertEqual(
            json.loads(query["queryString"])["groupBy"],
            [{"column": field} for field in ("region", "period_start", "revenue", "target", "detected_at")],
        )
        self.assertNotIn("${", json.dumps(entities))
        publications = [
            step for entity in entities
            if entity.get("payload", {}).get("definition", {}).get("type") == "Rule"
            for step in json.loads(entity["payload"]["definition"]["instance"])["steps"]
            if "BusinessEventPublication" in json.dumps(step)
        ]
        self.assertEqual(len(publications), 1)
        for field in ("region", "period_start", "revenue", "target", "variance_pct", "detected_at"):
            self.assertIn(field, json.dumps(publications[0]))


if __name__ == "__main__":
    unittest.main()
