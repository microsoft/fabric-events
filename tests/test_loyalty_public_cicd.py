import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cicd"))
from loyalty_catalog import validate_catalog
from loyalty_deploy import LoyaltyDeployment
from public_bundle import bundle
from public_consumers import definition_part, part
import public_deploy
from test_public_cicd import FakePublicApi, TENANT, WORKSPACE

FOLDER = ROOT / "cicd" / "customer-loyalty-milestone"
OVERVIEW = ROOT / "docs" / "business-events" / "scenarios" / (FOLDER.name + ".md")


def configured():
    config = public_deploy.configuration(FOLDER, FOLDER / "config.card.example.json")
    config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
    return config


class LoyaltyPublicDeploymentTests(unittest.TestCase):
    def test_preparation_creates_only_six_declared_items_without_running_workloads(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi()
            target = LoyaltyDeployment(FOLDER, configured(), Path(directory) / "ledger.json", api)
            result = target.deploy()
            self.assertEqual(len(api.items), 6)
            self.assertEqual(result["status"], "manual_setup_required")
            self.assertFalse(result["e2e_verified"])
            self.assertFalse(result["catalog_initialization_verified"])
            self.assertEqual(set(result["manual_resources"]), {"publisher", "eventstream"})
            created = [call for call in api.calls if call[0] == "POST"
                       and call[1].rsplit("/", 1)[-1] in ("eventSchemaSets", "eventhouses", "kqlDatabases",
                                                       "items", "lakehouses", "notebooks")]
            self.assertEqual(len(created), 6)
            self.assertEqual({row["type"] for row in api.items.values()},
                             {"EventSchemaSet", "Eventhouse", "KQLDatabase", "KQLQueryset", "Lakehouse", "Notebook"})
            self.assertFalse(any("/jobs/" in call[1] or "/eventstreams" in call[1]
                                 or call[4] == "https://api.kusto.windows.net" for call in api.calls))
            lakehouse_request = next(call[2] for call in created if call[1].endswith("/lakehouses"))
            self.assertNotIn("creationPayload", lakehouse_request)
            source_request = next(call[2] for call in created if call[1].endswith("/kqlDatabases"))
            source = definition_part(source_request["definition"], "DatabaseSchema.kql")
            self.assertNotIn(".set-or-append", source)
            self.assertNotIn("4900", source)
            self.assertEqual(public_deploy.read(target.path)["status"], "manual_setup_required")

    def test_custom_resource_names_and_native_catalog_destination_remain_bound(self):
        config = configured()
        config["resource_names"] = {role: "Custom_" + role for role in config["resource_names"]}
        with tempfile.TemporaryDirectory() as directory:
            target = LoyaltyDeployment(FOLDER, config, Path(directory) / "ledger.json", FakePublicApi())
            result = target.deploy()
            self.assertEqual({row["displayName"] for row in target.api.items.values()},
                             {name for role, name in config["resource_names"].items()
                              if role not in ("publisher", "eventstream")})
            self.assertEqual(result["manual_resources"],
                             {role: config["resource_names"][role] for role in ("publisher", "eventstream")})
            notebook = target.notebook_source()
            ast.parse(notebook)
            self.assertNotIn("${", notebook)
            self.assertIn(WORKSPACE + "@onelake.dfs.fabric.microsoft.com/", notebook)
            self.assertIn(target.state["items"]["record_store"] + "/Tables/LoyaltyCampaignCatalog", notebook)
            self.assertIn('.mode("errorifexists")', notebook)
            self.assertIn('.option("delta.columnMapping.mode", "none")', notebook)
            self.assertIn('.option("delta.enableDeletionVectors", "false")', notebook)
            self.assertNotIn(".PublishEvent", notebook)
            self.assertNotIn("LoyaltyCampaignCandidates", notebook)

    def test_interrupted_public_creation_resumes_without_duplicate_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            api = FakePublicApi(asynchronous=True)
            api.interrupt_poll = True
            path = Path(directory) / "ledger.json"
            target = LoyaltyDeployment(FOLDER, configured(), path, api)
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                target.deploy()
            ids = copy.deepcopy(target.state["loyalty_query_ids"])
            resumed = LoyaltyDeployment(FOLDER, configured(), path, api)
            resumed.deploy(resume=True)
            self.assertEqual(len(api.items), 6)
            self.assertEqual(resumed.state["loyalty_query_ids"], ids)
            self.assertNotIn("pending", public_deploy.read(path))

    def test_definition_inspection_allows_sample_tabs_but_rejects_source_or_notebook_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            target = LoyaltyDeployment(FOLDER, configured(), Path(directory) / "ledger.json", FakePublicApi())
            target.deploy()
            query_id = target.state["items"]["queryset"]
            query = json.loads(definition_part(target.api.definitions[query_id], "RealTimeQueryset.json"))
            query["queryset"]["tabs"].insert(0, {
                "id": str(uuid.uuid4()), "title": "My sample", "content": ".show tables",
                "dataSourceId": target.state["loyalty_query_ids"]["source"],
            })
            target.api.definitions[query_id] = {"parts": [part("RealTimeQueryset.json", query)]}
            target.verify()
            original = copy.deepcopy(target.api.definitions[query_id])
            query["queryset"]["tabs"][1]["content"] += "\n| where total_spend > 5000"
            target.api.definitions[query_id] = {"parts": [part("RealTimeQueryset.json", query)]}
            with self.assertRaisesRegex(ValueError, "Queryset"):
                target.verify()
            target.api.definitions[query_id] = original
            notebook_id = target.state["items"]["notebook"]
            target.api.definitions[notebook_id] = {"parts": [part("notebook-content.py", "raise RuntimeError('changed')")]}
            with self.assertRaisesRegex(ValueError, "Notebook"):
                target.verify()

    def test_unexpected_schema_enabled_lakehouse_stops_before_notebook_creation(self):
        class SchemaEnabledApi(FakePublicApi):
            def _request(self, method, url, resource, body, headers):
                reply = super()._request(method, url, resource, body, headers)
                if method == "GET" and "/lakehouses/" in url:
                    reply.body.setdefault("properties", {})["defaultSchema"] = "dbo"
                return reply

        with tempfile.TemporaryDirectory() as directory:
            api = SchemaEnabledApi()
            target = LoyaltyDeployment(FOLDER, configured(), Path(directory) / "ledger.json", api)
            with self.assertRaisesRegex(ValueError, "without schemas"):
                target.deploy()
            self.assertNotIn("Notebook", {row["type"] for row in api.items.values()})

    def test_plan_and_unsupported_source_test_do_not_authenticate_or_claim_subscription(self):
        config = configured()
        current = public_deploy.plan(FOLDER, config)
        self.assertEqual(set(current["implemented_resources"]), set(LoyaltyDeployment.resource_types))
        self.assertEqual(set(current["remaining_resources"]), {"publisher", "eventstream"})
        self.assertFalse(current["automated_source_trigger_available"])
        self.assertFalse(current["automated_e2e_test_available"])
        self.assertIn("eventstream_business_events_source", {gap["id"] for gap in current["public_api_gaps"]})
        self.assertNotIn("managed_business_event_eventhouse_association",
                         {gap["id"] for gap in current["public_api_gaps"]})
        with patch.object(public_deploy, "PublicApi") as api, contextlib.redirect_stderr(io.StringIO()) as errors:
            result = public_deploy.main(FOLDER, ["test"])
        self.assertEqual(result, 1)
        self.assertIn("no automated source trigger", errors.getvalue())
        api.assert_not_called()

    def test_manual_source_bundle_is_standalone_and_contains_no_fake_test_or_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            output = bundle(FOLDER, directory / "loyalty.zip")
            manifest = public_deploy.read(FOLDER / "scenario.json")
            expected = {
                "public_api.py", "public_deploy.py", "public_bundle.py", "public_contracts.json",
                "deploy.py", "deploy.bat", "bundle.py", "scenario.json", "schema.avsc",
                "config.card.example.json", "config.wizard.example.json", "config.schema.json",
                *manifest["distribution_assets"],
            }
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual({Path(name).name for name in archive.namelist()}, expected)
                self.assertFalse(any("test.bat" in name or ".deployments" in name or ".local." in name
                                     or name.endswith((".md", ".html")) for name in archive.namelist()))
                archive.extractall(directory / "extracted")
            isolated = directory / "extracted" / "cicd" / FOLDER.name
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            command = subprocess.run([sys.executable, str(isolated / "deploy.py"), "plan"],
                                     cwd=directory, env=environment, capture_output=True, text=True)
            self.assertEqual(command.returncode, 0, command.stderr)
            current = json.loads(command.stdout)
            self.assertEqual(set(current["remaining_resources"]), {"publisher", "eventstream"})
            self.assertFalse(current["cloud_writes"])


class LoyaltyCatalogTests(unittest.TestCase):
    def test_duplicate_reference_keys_fail_before_they_can_multiply_candidates(self):
        rows = public_deploy.read(FOLDER / "catalog.json")
        for active in (False, True):
            duplicate = {**rows[0], "campaign_id": "another-campaign", "is_active": active}
            with self.subTest(active=active), self.assertRaisesRegex(ValueError, "exactly one"):
                validate_catalog(rows + [duplicate])

    def test_reference_catalog_rejects_missing_keys_null_identifiers_and_non_boolean_activity(self):
        rows = public_deploy.read(FOLDER / "catalog.json")
        variants = [
            [], None, [{**rows[0], "campaign_id": None}], [{**rows[0], "catalog_store_id": ""}],
            [{**rows[0], "benefit_code": " "}], [{**rows[0], "catalog_tier": " gold"}],
            [{**rows[0], "is_active": 1}], [{**rows[0], "is_active": "true"}],
            [{key: value for key, value in rows[0].items() if key != "campaign_id"}],
        ]
        for invalid in variants:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_catalog(invalid)

    def test_reference_catalog_accepts_distinct_stores_and_never_uses_customer_data(self):
        rows = public_deploy.read(FOLDER / "catalog.json")
        self.assertEqual(validate_catalog(rows), rows)
        self.assertEqual(validate_catalog(rows + [{**rows[0], "catalog_store_id": "another-store"}])[-1]["campaign_id"],
                         rows[0]["campaign_id"])
        with self.assertRaises(ValueError):
            validate_catalog([{**rows[0], "customer_name": "Not reference data"}])


class LoyaltyDocumentationContractTests(unittest.TestCase):
    def test_workbook_and_package_use_the_same_source_schema_query_and_sample_commands(self):
        workbook = (OVERVIEW.parent / FOLDER.name / "deployment.md").read_text(encoding="utf-8")
        blocks = re.findall(r"(?m)^[ \t]*```kusto\n(.*?)^[ \t]*```", workbook, re.S)
        normalized = [textwrap.dedent(block).strip() for block in blocks]
        self.assertIn((FOLDER / "DatabaseSchema.kql").read_text().strip(), normalized)
        self.assertIn((FOLDER / "source-query.kql").read_text().strip(), normalized)
        sample_blocks = [block for block in normalized if block.startswith(".set-or-append")]
        source = (FOLDER / "sample-input.kql").read_text()
        source = "\n".join(line for line in source.splitlines() if not line.startswith("//"))
        executable = [block.strip() for block in re.split(r"\n\s*\n", source) if block.strip()]
        self.assertEqual(sample_blocks, executable)
        schema = json.loads(re.search(r"```json\n(.*?)\n[ \t]*```", workbook, re.S).group(1))
        self.assertEqual(schema, public_deploy.read(FOLDER / "schema.avsc"))

    def test_both_guides_preserve_campaign_preparation_boundaries_and_negative_cases(self):
        manifest = public_deploy.read(FOLDER / "scenario.json")
        guide = OVERVIEW.parent / FOLDER.name / "deployment.md"
        for path in (OVERVIEW, guide):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                for table in (manifest["campaign_preparation"]["candidate_table"],
                              manifest["campaign_preparation"]["unmatched_table"]):
                    self.assertIn(table, text)
                for context in ("5100", "5000", "4999", "5200", "contact permission", "benefit eligibility"):
                    self.assertIn(context, text)
                self.assertIn("not an exactly-once", text)
                self.assertIn("campaign", text)
                self.assertNotIn("`verify`", text)
                if path == guide:
                    self.assertNotIn("flowchart TB", text)
        self.assertIn("4900", guide.read_text(encoding="utf-8"))
        self.assertNotIn("sum(amount) by customer_id, customer_name, store_id", OVERVIEW.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
