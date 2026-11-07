import json
from pathlib import Path
import re
import unittest

import markdown
from mkdocs.config import load_config
from mkdocs.structure.files import get_files


ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    "sales-volume-alert": "sales-volume-alert",
    "high-value-payment": "real-time-stream-alert",
    "discount-approval-routing": "discount-approval-routing",
    "low-stock-threshold": "low-stock-threshold",
    "revenue-below-target": "revenue-below-target",
    "fraud-response-before-fulfillment": "fraud-response-before-fulfillment",
}


def guide_path(package):
    return ROOT / "docs" / "business-events" / "scenarios" / GUIDES[package] / "deployment.md"


class DeploymentDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(config_file=str(ROOT / "mkdocs.yml"))

    def test_deployment_navigation_is_nested_under_its_scenario(self):
        def groups(entries):
            for entry in entries:
                for children in entry.values():
                    if isinstance(children, list):
                        yield children
                        yield from groups(children)

        navigation = list(groups(self.config.nav))
        for scenario in GUIDES.values():
            with self.subTest(scenario=scenario):
                overview = {"Overview": f"business-events/scenarios/{scenario}.md"}
                deployment = {"Deployment": f"business-events/scenarios/{scenario}/deployment.md"}
                self.assertTrue(any(
                    overview in children and deployment in children
                    for children in navigation
                ))

    def test_only_nested_documentation_sources_are_discovered(self):
        files = get_files(self.config)
        for package, scenario in GUIDES.items():
            with self.subTest(package=package):
                self.assertIsNotNone(files.get_file_from_path(
                    f"business-events/scenarios/{scenario}/deployment.md",
                ))
                self.assertIsNone(files.get_file_from_path(
                    f"business-events/deployments/{package}.md",
                ))
        self.assertFalse(any("cicd" in Path(file.abs_src_path).relative_to(
            self.config.docs_dir,
        ).parts for file in files))

    def test_executable_scenarios_do_not_contain_documentation_copies(self):
        for package in GUIDES:
            with self.subTest(package=package):
                folder = ROOT / "cicd" / package
                self.assertEqual(list(folder.glob("README*")), [])
                self.assertEqual(list(folder.glob("*.html")), [])

    def test_customer_guides_omit_internal_operational_context(self):
        forbidden = re.compile(
            r"\bMSIT\b|\bDaily\b|"
            r"Business Events Scenarios Playground|C:\\Workspace|\.deployments[\\/]msit",
        )
        for package in GUIDES:
            with self.subTest(package=package):
                source = guide_path(package)
                text = source.read_text(encoding="utf-8")
                self.assertIsNone(forbidden.search(text))
                self.assertNotIn("\u2014", text)

    def test_guides_retain_full_operational_instructions(self):
        required = {
            "sales-volume-alert": (
                "## Clean deployment quick start",
                "## Configuration and parameter injection",
                "### Wizard field to deployment binding",
                "## Manual execution from the Fabric portal",
                "## 3. Complete the native integration gates",
                "## API references",
                "--with-deployment-plan",
                "reconcile-import",
                "teams-definition.local.json",
                "fixtures_json",
                "3,868",
            ),
            "high-value-payment": (
                "## Inject names and target parameters",
                "## Fresh deployments and local records",
                "## Engineering handoff and native ownership",
                "python -m pip install -r",
                "--verify-only --teams-confirmed",
                'resume --config $configFile --state-file "$ledger"',
                "Eventstream names use 3-100",
                "six-field ManageFields mapping",
            ),
            "discount-approval-routing": (
                "## Required native Eventstream portal step",
                "## Inject wizard names and deployment parameters",
                "## Fresh attempts, local records and resume",
                "## Engineering handoff and optional package",
                "acknowledge-eventstream --config $configFile",
                "--teams-confirmed --eventstream-confirmed",
                'resume --config $configFile --state-file "$ledger"',
                "artifactType:",
                "two manager and four executive",
            ),
            "low-stock-threshold": (
                "## Implemented source",
                "## Connection and remaining native bindings",
                "## Offline publisher tests",
                "${schema_id}",
                "signed 32-bit Avro int",
                "detectedAt",
                "python -m unittest discover",
            ),
            "revenue-below-target": (
                "## Implemented source",
                "## Bindings required before deployment",
                "## External test sequence and evidence",
                "variance_pct.dax",
                "report/page/visual binding",
                "Becomes less than -15",
                "actual parameter mapping",
            ),
            "fraud-response-before-fulfillment": (
                "## Implemented source",
                "## Native bindings and connection ownership",
                "## Offline publisher tests",
                "## Repeatable deployment and testing",
                "--verify-only",
                "--new-run",
                'resume --config $configFile --state-file "$ledger"',
                "${schema_id}",
                "single-precision limits",
                "Ecommerce.Order.HighRiskDetected",
                "OMS enforcement needs separate evidence",
                "python -m unittest discover",
            ),
        }
        for package, fragments in required.items():
            text = guide_path(package).read_text(encoding="utf-8")
            for fragment in fragments:
                with self.subTest(package=package, required=fragment):
                    self.assertIn(fragment, text)

    def test_sales_portal_steps_keep_commands_inside_the_numbered_sequence(self):
        source = guide_path("sales-volume-alert").read_text(encoding="utf-8")
        steps = source.split("1. Build the resource archive locally:", 1)[1].split(
            "For example, begin with this parameter-cell configuration:", 1,
        )[0]
        rendered = markdown.markdown(
            "1. Build the resource archive locally:" + steps,
            extensions=["pymdownx.superfences"],
        )
        self.assertEqual(rendered.count("<li>"), 5)
        first_step = rendered.split("</li>", 1)[0]
        self.assertIn("<pre>", first_step)
        self.assertIn("bundle.py", re.sub(r"<[^>]+>", "", first_step))

    def test_six_scenario_connections_distinguish_references_from_seeds(self):
        index = (ROOT / "docs" / "business-events" / "scenarios" / "index.md").read_text(encoding="utf-8")
        for package, guide in GUIDES.items():
            with self.subTest(package=package):
                folder = ROOT / "cicd" / package
                page = (ROOT / "docs" / "business-events" / "scenarios" / (guide + ".md")).read_text(encoding="utf-8")
                uri = f"{guide}/deployment.md"
                self.assertIn(uri, page)
                self.assertIn(uri, index)
                customer_guide = guide_path(package).read_text(encoding="utf-8")
                self.assertIn(f"../{guide}.md", customer_guide)
                self.assertIn(f"https://github.com/microsoft/fabric-events/tree/main/cicd/{package}", page)
                self.assertEqual(page.count("## Deploy this scenario"), 1)
                manifest_path = folder / "scenario.json"
                manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
                executable = (folder / "deploy.bat").is_file() and (folder / "test.bat").is_file()
                if manifest.get("deployable", executable):
                    self.assertTrue(executable)
                    self.assertIn("**Status: Reference implementation available", page)
                    self.assertIn("not a generally available customer provisioning flow", customer_guide)
                    wizard = json.loads((folder / "config.wizard.example.json").read_text())
                    self.assertIsNone(wizard["workspace_id"])
                    card = json.loads((folder / "config.card.example.json").read_text())
                    self.assertIsNone(card["workspace_id"])
                else:
                    self.assertIn("**Status: In preparation.", page)
                    self.assertIs(manifest["deployable"], False)


if __name__ == "__main__":
    unittest.main()
