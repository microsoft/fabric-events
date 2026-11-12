import contextlib
import io
import json
from pathlib import Path
import re
import runpy
import sys
from tempfile import TemporaryDirectory
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree

import markdown
from mkdocs.config import load_config
from mkdocs.structure.files import get_files
from mkdocs.structure.nav import get_navigation


ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    "sales-volume-alert": "sales-volume-alert",
    "real-time-stream-alert": "real-time-stream-alert",
    "discount-approval-routing": "discount-approval-routing",
    "low-stock-threshold": "low-stock-threshold",
    "revenue-below-target": "revenue-below-target",
    "fraud-response-before-fulfillment": "fraud-response-before-fulfillment",
    "real-time-viewer-recommendations": "real-time-viewer-recommendations",
    "abandoned-cart-recovery": "abandoned-cart-recovery",
    "data-pipeline-failure-follow-up": "data-pipeline-failure-follow-up",
    "customer-loyalty-milestone": "customer-loyalty-milestone",
}


def guide_path(package):
    return ROOT / "docs" / "business-events" / "scenarios" / GUIDES[package] / "deployment.md"


MANUAL_SCENARIOS = ("order-history-analysis",)
SCENARIOS = tuple(GUIDES) + MANUAL_SCENARIOS


def scenario_path(scenario):
    return ROOT / "docs" / "business-events" / "scenarios" / (scenario + ".md")


def workbook_path(scenario):
    return scenario_path(scenario).parent / scenario / "deployment.md"


def code_blocks(text):
    return [(language.strip(), textwrap.dedent(body).strip()) for language, body in re.findall(
        r"(?m)^[ \t]*```([^\n]*)\n(.*?)^[ \t]*```[ \t]*$", text, re.S)]


class DeploymentDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(config_file=str(ROOT / "mkdocs.yml"))

    def test_microsoft_learn_links_are_locale_neutral(self):
        forbidden = re.compile(
            rb"https?://learn\.microsoft\.com/en-us(?=/|[?#\s\x22\x27<>)]|$)", re.I,
        )
        paths = set(ROOT.glob("*.md"))
        paths.update((ROOT / "cicd").glob("*.json"))
        paths.update(ROOT / "cicd" / package / "scenario.json" for package in GUIDES)
        paths.update(
            Path(file.abs_src_path) for file in get_files(self.config)
            if not file.inclusion.is_excluded() and Path(file.abs_src_path).suffix.lower() in {
                ".md", ".html", ".json", ".yaml", ".yml", ".css", ".js", ".zip",
            }
        )
        for path in sorted(paths):
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        with self.subTest(path=path, member=member.filename):
                            self.assertIsNone(forbidden.search(archive.read(member)))
            else:
                with self.subTest(path=path):
                    self.assertIsNone(forbidden.search(path.read_bytes()))

    def test_deployment_navigation_is_nested_under_its_scenario(self):
        def groups(entries):
            for entry in entries:
                if isinstance(entry, str):
                    continue
                for name, children in entry.items():
                    if isinstance(children, list):
                        yield name, children
                        yield from groups(children)

        navigation = list(groups(self.config.nav))
        scenarios = next(children for name, children in navigation if name == "Scenarios")
        self.assertEqual(scenarios[0], "business-events/scenarios/index.md")
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = f"business-events/scenarios/{scenario}.md"
                deployment = {"Workbook": f"business-events/scenarios/{scenario}/deployment.md"}
                self.assertTrue(any(
                    isinstance(entry, dict) and [overview, deployment] in entry.values()
                    for entry in scenarios
                ))

    def test_scenario_indexes_preserve_urls_and_page_navigation(self):
        files = get_files(self.config)
        navigation = get_navigation(files, self.config)
        navigation = self.config.plugins.on_nav(navigation, config=self.config, files=files)
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                page = files.get_file_from_path(f"business-events/scenarios/{scenario}.md").page
                self.assertTrue(page.is_index)
                self.assertEqual(page.url, f"business-events/scenarios/{scenario}/")
                self.assertIs(page.parent.children[0], page)
                self.assertIn(page, navigation.pages)
        for page in navigation.pages:
            if page.previous_page:
                self.assertIs(page.previous_page.next_page, page)
            if page.next_page:
                self.assertIs(page.next_page.previous_page, page)

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

    def test_alerting_scenario_names_and_packages_match_the_overviews(self):
        for package in GUIDES:
            with self.subTest(package=package):
                overview = ROOT / "docs" / "business-events" / "scenarios" / (GUIDES[package] + ".md")
                folder = ROOT / "cicd" / package
                manifest = json.loads((folder / "scenario.json").read_text())
                self.assertEqual(manifest["scenario_id"], folder.name)
                self.assertEqual(manifest["scenario_name"], overview.read_text(encoding="utf-8").splitlines()[0].removeprefix("# "))
                self.assertEqual(folder.name, overview.stem)
                deployment = guide_path(package).read_text(encoding="utf-8")
                self.assertIn(f"cicd\\{folder.name}", deployment)
                index = (overview.parent / "index.md").read_text(encoding="utf-8")
                self.assertIn(f"[Workbook]({folder.name}/deployment.md)", index)

    def test_customer_guides_omit_internal_operational_context(self):
        forbidden = re.compile(
            r"\bMSIT\b|\bDaily\b|"
            r"Business Events Scenarios Playground|C:\\Workspace|\.deployments[\\/]msit",
        )
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                source = workbook_path(scenario)
                text = source.read_text(encoding="utf-8")
                self.assertIsNone(forbidden.search(text))
                self.assertNotIn("\u2014", text)

    def test_current_guides_use_source_test_and_learner_evaluation_not_author_automation(self):
        for scenario in GUIDES:
            with self.subTest(scenario=scenario):
                text = guide_path(scenario).read_text(encoding="utf-8")
                self.assertIn("### Evaluation criteria", text)
                self.assertNotIn("`verify`", text)
                self.assertNotRegex(text, r"deploy\.py\s+verify\b")
                prose = re.sub(r"(?m)^([ \t]*)```[^\n]*\n[\s\S]*?^\1```[ \t]*$", "", text)
                self.assertNotRegex(prose, r"(?i)\bexit\s+[*`]*\s*\d+\b|\bE2E\b|\breadback\b")
                for author_setting in ("teams_verifier", "teams_chat_id", "Chat.Read", "desktop unlocked"):
                    self.assertNotIn(author_setting, text)
                for author_result in ("expected_review_events", "expected_decision_events",
                                      "expected_interactions", "source_execution_verified"):
                    self.assertNotIn(author_result, prose)
                if scenario in ("revenue-below-target", "customer-loyalty-milestone"):
                    self.assertNotIn("test.bat", text)
                    self.assertNotRegex(text, r"deploy\.py\s+test\b")
                else:
                    command = next(line for line in text.splitlines() if line.startswith(".\\test.bat "))
                    self.assertNotIn("--source-only", command)
                    self.assertNotIn("--teams-chat-id", command)
                self.assertNotIn("e2e_verified", text)
        sales = guide_path("sales-volume-alert").read_text(encoding="utf-8")
        self.assertIn("check the new messages in Teams", sales)
        self.assertIn("same account as Azure CLI", sales)
        self.assertIn("**two alerts**", sales)
        self.assertIn("**-30% boundary**", sales)

    def test_environment_setup_uses_one_shared_sequence_across_all_current_guides(self):
        for scenario in GUIDES:
            with self.subTest(scenario=scenario):
                text = guide_path(scenario).read_text(encoding="utf-8")
                self.assertEqual(text.count("## 1. Configure your environment"), 1)
                setup = text.split("## 1. Configure your environment", 1)[1].split("\n## ", 1)[0]
                self.assertIn("Replace the three placeholders below:", setup)
                blocks = re.findall(r"```powershell\n(.*?)\n```", setup, re.S)
                self.assertEqual(blocks, [
                    f"Set-Location '<extracted-folder>\\cicd\\{scenario}'\n"
                    "$tenantId = '<your-tenant-uuid>'\n"
                    "$workspaceId = '<your-workspace-uuid>'\n\n"
                    "az login --tenant $tenantId\n"
                    "python .\\deploy.py configure --tenant-id $tenantId --workspace-id $workspaceId "
                    "--auth-provider azure_cli --output .\\config.local.json",
                ])
                self.assertIn("Review the tenant, workspace and unused resource names in `config.local.json`", setup)
                config = json.loads((ROOT / "cicd" / scenario / "config.card.example.json").read_text(encoding="utf-8"))
                default = str(config["accept_preview_apis"]).lower()
                self.assertIn(f"**`accept_preview_apis` defaults to `{default}`**", setup)
                self.assertNotRegex(setup, r"deploy\.py\s+(?:plan|verify|deploy|test|resume)\b|\.\\(?:deploy|test)\.bat\b")

    def test_packaged_workbooks_link_complete_public_downloads(self):
        for scenario in GUIDES:
            with self.subTest(scenario=scenario):
                workbook = guide_path(scenario)
                text = workbook.read_text(encoding="utf-8")
                links = re.findall(
                    r"\[Download package\]\((\.\./\.\./\.\./assets/downloads/[^)]+\.zip)\)",
                    text,
                )
                self.assertEqual(len(links), 1)
                package = (workbook.parent / links[0]).resolve()
                self.assertTrue(package.is_file(), package)
                self.assertIn(f"Download **`{package.name}`** or the [repository ZIP]", text)
                with zipfile.ZipFile(package) as archive:
                    self.assertIsNone(archive.testzip())
                    prefix = f"cicd/{scenario}/"
                    manifest = json.loads(archive.read(prefix + "scenario.json"))
                    self.assertEqual(manifest["scenario_id"], scenario)
                    for member in archive.namelist():
                        self.assertTrue(member.startswith(prefix), member)
                        self.assertFalse(any(marker in member for marker in (
                            ".local.", ".deployments", "cicd_old",
                        )), member)

    def test_failure_follow_up_describes_visible_responses_and_ignored_outcomes(self):
        text = guide_path("data-pipeline-failure-follow-up").read_text(encoding="utf-8")
        step = text.split("## 4. Run workloads and inspect follow-up cases", 1)[1].split("\n## ", 1)[0]
        commands = re.findall(r"```powershell\n(.*?)\n```", step, re.S)
        self.assertEqual(commands, [
            ".\\test.bat --config .\\config.local.json --confirm-workspace $workspaceId",
        ])
        self.assertIn("single Notebook run", step)
        self.assertIn("case names, not statuses", step)
        self.assertIn("ignored by the Activator source filter", step)
        self.assertIn("items.notebook", step)
        self.assertNotIn("source summary", step)
        self.assertNotIn("expected_events", step)
        self.assertNotIn("source_run_id", step)
        self.assertNotIn("source_job_id", step)
        self.assertNotIn("re-raises", step)
        self.assertNotIn("finally", step)
        self.assertNotIn("<failed-load-run-id>", step)
        self.assertNotIn("Open that Notebook execution in Fabric. Its printed summary", step)

    def test_guides_retain_full_operational_instructions(self):
        required = {
            "sales-volume-alert": (
                "## Resources and coverage",
                "## 1. Configure your environment",
                "## 2. Deploy to Fabric",
                "## 3. Run the sample and check Teams",
                "--confirm-workspace $workspaceId",
                "accept_preview_apis",
                "store IDs in its printed summary",
                "Not configured by this package",
            ),
            "real-time-stream-alert": (
                "## Resources and coverage",
                "## 1. Configure your environment",
                "## 2. Deploy to Fabric",
                "## 3. Send the sample and check cases",
                "source_run_id",
                "Not configured by this package",
            ),
            "discount-approval-routing": (
                "## Resources and coverage",
                "## 1. Configure your environment",
                "## 2. Deploy to Fabric",
                "## 3. Connect Eventstream before testing",
                "## 4. Evaluate proposals and inspect delivery",
                "--confirm-workspace $workspaceId",
                "source_run_id",
                "Not configured by this package",
                "two manager and four executive",
            ),
            "low-stock-threshold": (
                "## Resources and coverage",
                "## 1. Configure your environment",
                "## 2. Deploy to Fabric",
                "## 3. Send the findings and check requests",
                "source_run_id",
                "Not configured by this package",
            ),
            "revenue-below-target": (
                "## Resources and coverage",
                "## 1. Configure your environment",
                "## 2. Deploy to Fabric",
                "## 3. Complete manual setup before testing",
                "## 4. Test the flow from KQL",
                "--confirm-workspace $workspaceId",
                "Not configured by this package",
                "Becomes → Less than → `-15`",
                "KQL null variance",
                "Files → RevenueInvestigations",
            ),
            "fraud-response-before-fulfillment": (
                "## Resources and coverage",
                "## 1. Configure your environment",
                "## 2. Deploy to Fabric",
                "## 3. Connect responses and prepare orders",
                "## 4. Evaluate orders and inspect enforcement",
                "--confirm-workspace $workspaceId",
                "source_run_id",
                "source_orders",
                "Not configured by this package",
                "three operational responses",
                "review_completed = false",
            ),
        }
        for package, fragments in required.items():
            text = guide_path(package).read_text(encoding="utf-8")
            with self.subTest(package=package):
                self.assertNotRegex(text, r"(?im)^#{2,}\s+[^\n]*(?:interrupted|resume)\b")
                self.assertNotRegex(text, r"\bresume\s+--config\b")
                self.assertNotIn("continuing an interrupted exercise", text)
                self.assertNotIn("If you delete the Fabric artifacts yourself", text)
                self.assertNotIn("retain every incomplete deployment", text)
            for fragment in fragments:
                with self.subTest(package=package, required=fragment):
                    self.assertIn(fragment, text)

    def test_workbook_reference_renders_as_expandable_code(self):
        source = guide_path("sales-volume-alert").read_text(encoding="utf-8")
        rendered = markdown.markdown(source, extensions=[
            "pymdownx.superfences", "pymdownx.details", "tables", "attr_list",
        ])
        self.assertIn('<details class="info">', rendered)
        self.assertIn("<pre>", rendered)
        self.assertIn("Event contract", rendered)
        self.assertIn("Retail.Sales.VolumeAlert", rendered)

    def test_business_context_owns_the_story_and_workbook_owns_the_practice(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                workbook = workbook_path(scenario).read_text(encoding="utf-8")
                headings = re.findall(r"(?m)^## ([^\n]+)", overview)
                self.assertEqual(headings, [
                    "Business Context", "Current Problem",
                    "Improved Business Response {#business-events}",
                    "Event-driven architecture {#architecture}",
                    "Business outcome and boundaries", "Try the Workbook",
                ])
                rendered = markdown.markdown(overview, extensions=["toc", "attr_list", "pymdownx.superfences"])
                self.assertIn('<h2 id="business-events">Improved Business Response</h2>', rendered)
                self.assertIn(f"[Workbook]({scenario}/deployment.md){{#deploy-this-scenario}}", overview)
                self.assertEqual({language for language, _ in code_blocks(overview)}, {"mermaid"})
                self.assertIn("Where the exercise stops", overview)
                self.assertIn('<span id="why-business-events"></span>', overview)
                self.assertIn('<span id="business-value"></span>', overview)
                self.assertIn(f"../{scenario}.md", workbook)
                self.assertNotRegex(workbook, r"\]\(\.\./[^)]+\.md#")
                self.assertNotIn("manual Overview", workbook)
                self.assertNotIn("manual scenario", workbook)
                self.assertNotIn("quick deployment", workbook)
                self.assertEqual(workbook.splitlines()[0].split(" {#")[0],
                                 overview.splitlines()[0] + ": Workbook")

    def test_business_narrative_precedes_the_product_and_technical_roles(self):
        implementation = (
            r"\b(?:Fabric|Activator|Eventstream|Notebook|"
            r"User Data Function|Lakehouse|Eventhouse)\b|`"
        )
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                opening = overview.split("\n", 1)[1].lstrip()
                self.assertTrue(opening.startswith("## Business Context\n"))
                context = overview.split("## Business Context\n", 1)[1].split(
                    "## Current Problem", 1,
                )[0]
                context_prose = re.sub(r"```.*?```", "", context, flags=re.S)
                problem = overview.split("## Current Problem\n", 1)[1].split(
                    "## Improved Business Response", 1,
                )[0]
                self.assertNotRegex(context_prose + problem, rf"\bBusiness Events?\b|{implementation}")
                solution = overview.split("## Improved Business Response {#business-events}\n", 1)[1].split(
                    "## Event-driven architecture", 1,
                )[0].strip()
                self.assertIn("Business Event", solution)
                self.assertNotRegex(solution.split(". ", 1)[0], r"\bBusiness Events?\b")
                solution_content = re.sub(r"(?m)^```(?:mermaid)?[ \t]*\n?", "", solution)
                self.assertNotRegex(solution_content, implementation)
                architecture = overview.index("## Event-driven architecture")
                self.assertGreater(overview.index("**Publisher:**"), architecture)
                self.assertGreater(overview.index("**Business signal:**"), architecture)

    def test_event_driven_flow_is_explained_after_the_diagram(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                architecture = overview.split("## Event-driven architecture {#architecture}\n", 1)[1].split(
                    "## Business outcome and boundaries", 1,
                )[0]
                explanation = architecture.split("```", 2)[2]
                self.assertRegex(explanation, r"(?m)^1\. \S")

    def test_history_is_a_creation_time_extension_not_a_response_dependency(self):
        for scenario in SCENARIOS:
            if scenario == "customer-loyalty-milestone" or scenario in MANUAL_SCENARIOS:
                continue
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                workbook = workbook_path(scenario).read_text(encoding="utf-8")
                architecture = overview.split("## Event-driven architecture {#architecture}\n", 1)[1]
                diagram = next(body for language, body in code_blocks(architecture) if language == "mermaid")
                self.assertRegex(diagram, r'(?:BE|B) -\.->\|"Analyze in Eventhouse"\| (?:EH|H)')
                history = workbook.split("## Optional event history", 1)[1].split("\n## ", 1)[0]
                self.assertIn("during Business Event creation", history)
                self.assertIn("not completed actions", history)
                self.assertIn("Skip these queries for its deployed event", history)
                self.assertIn("does not configure this association", history)
                self.assertNotRegex(diagram, r'\b(?:EH|H)\s*(?:-->|-\.->)[^\n]*\b(?:ACT|PIPE|UDF|T)\b')

    def test_each_scenario_separates_current_business_response_and_technical_views(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                workbook = workbook_path(scenario).read_text(encoding="utf-8")
                diagrams = [body for language, body in code_blocks(overview) if language == "mermaid"]
                context = overview.split("## Business Context\n", 1)[1].split("## Current Problem", 1)[0]
                business = overview.split("## Improved Business Response {#business-events}\n", 1)[1].split(
                    "## Event-driven architecture", 1,
                )[0]
                architecture = overview.split("## Event-driven architecture {#architecture}\n", 1)[1].split(
                    "## Business outcome and boundaries", 1,
                )[0]
                context_diagrams = [body for language, body in code_blocks(context) if language == "mermaid"]
                business_diagrams = [body for language, body in code_blocks(business) if language == "mermaid"]
                solution_diagrams = [body for language, body in code_blocks(architecture) if language == "mermaid"]
                self.assertEqual(len(context_diagrams), 1)
                self.assertEqual(len(business_diagrams), 1)
                self.assertEqual(len(solution_diagrams), 1)
                self.assertEqual(diagrams, context_diagrams + business_diagrams + solution_diagrams)
                self.assertNotIn("Highlighted steps need a separate handoff or review", context)
                self.assertRegex(context_diagrams[0], r'\w+\[\("')
                self.assertRegex(context_diagrams[0], r'-->\|"[^"]+"\|')
                self.assertEqual(re.findall(r"(?m)^\s*classDef (\w+)\b", context_diagrams[0]), ["businessGap"])
                self.assertEqual(re.findall(r"(?m)^\s*style (\w+)\b", context_diagrams[0]), ["GAP"])
                self.assertNotRegex(
                    context_diagrams[0], r"\b(?:Business Events?|Fabric|Activator|Eventstream|Notebook|Lakehouse|Eventhouse)\b",
                )
                for diagram in diagrams:
                    self.assertIn("flowchart TB", diagram)
                    self.assertNotIn("%%{init:", diagram)
                self.assertNotIn("mermaid", [language for language, _ in code_blocks(workbook)])
                self.assertNotIn("## Deployment architecture", workbook)
                rendered = markdown.markdown(workbook, extensions=["toc", "attr_list", "pymdownx.superfences"])
                anchors = re.findall(r'\bid="([^"]+)"', rendered)
                self.assertEqual(len(anchors), len(set(anchors)))

    def test_site_diagram_hook_preserves_graphs_and_disables_scaling(self):
        hooks = runpy.run_path(str(ROOT / "overrides" / "scenario_diagrams.py"))
        for file in get_files(self.config):
            if file.inclusion.is_excluded() or not file.src_uri.endswith(".md"):
                continue
            path = Path(file.abs_src_path)
            page = SimpleNamespace(file=SimpleNamespace(
                src_uri=path.relative_to(ROOT / "docs").as_posix(),
            ))
            original = path.read_text(encoding="utf-8")
            rendered = hooks["on_page_markdown"](original, page)
            before = code_blocks(original)
            after = code_blocks(rendered)
            self.assertEqual(len(before), len(after))
            for (_, graph), (_, configured) in zip(before, after):
                if not graph.startswith("flowchart "):
                    self.assertEqual(configured, graph)
                    continue
                with self.subTest(page=page.file.src_uri, graph=graph.splitlines()[0]):
                    self.assertTrue(graph.startswith("flowchart TB\n"))
                    directive, preserved = configured.split("\n", 1)
                    self.assertEqual(preserved, graph)
                    settings = json.loads(directive.removeprefix("%%{init: ").removesuffix("}%%"))
                    self.assertFalse(settings["flowchart"]["useMaxWidth"])
                    self.assertIn("transparent", settings["themeCSS"])

    def test_site_diagram_hook_isolates_layout_without_affecting_other_content(self):
        hooks = runpy.run_path(str(ROOT / "overrides" / "scenario_diagrams.py"))
        content = '<p>Keep this prose.</p><pre class="mermaid"><code>flowchart TB\nA --> B</code></pre>'
        for uri in ("business-events/scenarios/sample.md", "business-events/design-guides/sample.md",
                    "business-events/introduction/sample.md", "fabric-events/sample.md", "azure-events/sample.md"):
            with self.subTest(page=uri):
                page = SimpleNamespace(file=SimpleNamespace(src_uri=uri))
                wrapped = hooks["on_page_content"](content, page)
                self.assertIn('role="region" aria-label="Diagram"', wrapped)
                self.assertNotIn("tabindex", wrapped)
                self.assertIn('pre class="scenario-mermaid"', wrapped)
                self.assertIn(content.split("<pre", 1)[0], wrapped)
                self.assertEqual(hooks["on_page_content"]("<p>No diagram.</p>", page), "<p>No diagram.</p>")
                markdown = "Keep this prose.\n\n```mermaid\nflowchart TB\nA --> B\n```"
                configured = hooks["on_page_markdown"](markdown, page)
                self.assertTrue(configured.startswith("Keep this prose.\n\n```mermaid\n%%{init: "))

    def test_shared_diagram_configuration_is_not_indexed_as_customer_content(self):
        hooks = runpy.run_path(str(ROOT / "overrides" / "scenario_diagrams.py"))
        page = SimpleNamespace(file=SimpleNamespace(src_uri="business-events/introduction/sample.md"))
        content = (
            '<pre class="mermaid"><code>%%{init: {"themeCSS": "shared styles"}'
            '}%%\nflowchart TB\nA["Source"]\nB["Consumer"]\nA --> B\n</code></pre>'
        )
        wrapped = hooks["on_page_content"](content, page)
        original_text = "".join(ElementTree.fromstring(content).itertext())
        element = ElementTree.fromstring(wrapped)
        self.assertEqual("".join(element.itertext()), original_text)
        excluded = element.find('.//span[@data-search-exclude="true"]')
        self.assertIsNotNone(excluded)
        self.assertTrue(excluded.text.startswith("%%{init: "))
        self.assertNotIn("flowchart", excluded.text)

    def test_current_state_marks_the_gap_without_adding_a_business_component(self):
        explanations = set()
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                current, business, solution = [body for language, body in code_blocks(overview) if language == "mermaid"]
                frames = re.findall(r'(?ms)^\s*subgraph GAP\["([^"]+)"\]\n(.*?)^\s*end\s*$', current)
                self.assertEqual(len(frames), 1)
                explanation, steps = frames[0]
                self.assertIn("gap:", explanation.lower())
                self.assertNotRegex(explanation.lower(), r"problem here|gap here|highlighted steps")
                explanations.add(explanation)
                marked = set(re.findall(r"(?m)^\s*(\w+)[^\n]+:::businessGap\s*$", steps))
                all_marked = set(re.findall(r"(?m)^\s*(\w+)[^\n]+:::businessGap\s*$", current))
                nodes = set(re.findall(r"(?m)^\s*(\w+)[\[({]", current))
                self.assertTrue(marked)
                self.assertEqual(marked, all_marked)
                self.assertLess(len(marked), len(nodes))
                self.assertNotRegex(current, r"(?m)^\s*GAP\s*(?:-->|-\.->)")
                self.assertNotRegex(current, r"(?:-->|-\.->)\s*GAP\b")
                self.assertNotIn("businessGap", solution)
                self.assertNotIn("subgraph GAP", solution)
                self.assertNotIn("businessGap", business)
                self.assertNotIn("subgraph GAP", business)
        self.assertEqual(len(explanations), len(SCENARIOS))

    def test_business_response_explains_the_improvement_without_fabric_components(self):
        explanations = set()
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                current, business, technical = [
                    body for language, body in code_blocks(overview) if language == "mermaid"
                ]
                self.assertIn('(["<b>Business Event</b><br/>', business)
                self.assertNotRegex(
                    business,
                    r"\b(?:Fabric|Activator|Eventstream|Notebook|User Data Function|UDF|"
                    r"Lakehouse|Eventhouse|KQL|SQL|Power Automate|Teams)\b",
                )
                frames = re.findall(r'(?ms)^\s*subgraph VALUE\["([^"]+)"\]\n(.*?)^\s*end\s*$', business)
                self.assertEqual(len(frames), 1)
                explanation, steps = frames[0]
                self.assertTrue(explanation.startswith("Improvement: "))
                self.assertNotRegex(explanation.lower(), r"instantly|guaranteed|faster by|\d+% faster")
                explanations.add(explanation)
                marked = set(re.findall(r"(?m)^\s*(\w+)[^\n]+:::businessValue\s*$", steps))
                all_marked = set(re.findall(r"(?m)^\s*(\w+)[^\n]+:::businessValue\s*$", business))
                nodes = set(re.findall(r"(?m)^\s*(\w+)[\[({]", business))
                self.assertTrue(marked)
                self.assertEqual(marked, all_marked)
                self.assertLess(len(marked), len(nodes))
                self.assertNotRegex(business, r"(?m)^\s*VALUE\s*(?:-->|-\.->)")
                self.assertNotRegex(business, r"(?:-->|-\.->)\s*VALUE\b")
                self.assertNotIn("businessValue", current + technical)
                self.assertNotIn("subgraph VALUE", current + technical)
        self.assertEqual(len(explanations), len(SCENARIOS))

    def test_all_diagrams_distinguish_node_identities_from_descriptions(self):
        for scenario in SCENARIOS:
            overview = scenario_path(scenario).read_text(encoding="utf-8")
            diagrams = [body for language, body in code_blocks(overview) if language == "mermaid"]
            for index, diagram in enumerate(diagrams):
                nodes = re.findall(r'(?m)^\s*(\w+)(\[\(|\(\[|\[|\{)"([^"\n]+)"', diagram)
                self.assertTrue(nodes)
                for node, shape, label in nodes:
                    with self.subTest(scenario=scenario, diagram=index, node=node):
                        if shape == "{":
                            question = re.sub(r"<[^>]+>", " ", label).strip()
                            self.assertRegex(question, r"^(?:Does|Did|Is|Has|What)\b.+\?$")
                        else:
                            parts = re.fullmatch(r"<b>(.+?)</b><br/>(.+)", label)
                            self.assertIsNotNone(parts, label)
                            identity, description = parts.groups()
                            self.assertTrue(re.sub(r"<br/>", "", identity).strip())
                            self.assertNotRegex(identity, r"</?b>")
                            self.assertNotRegex(description, r"</?b>")

    def test_payment_workbook_uses_the_six_payment_sample_and_preserved_contract(self):
        overview = scenario_path("real-time-stream-alert").read_text(encoding="utf-8")
        workbook = guide_path("real-time-stream-alert").read_text(encoding="utf-8")
        main = workbook.split("## Optional event history", 1)[0]
        for value in ("six payments", "**three cases**", "5000.01 USD", "6000 EUR", "9000 JPY",
                      "4999.99 USD", "no Business Event, UDF activation or case",
                      "**Eventstream, not the test script", "check the saved cases"):
            self.assertIn(value, main)
        self.assertIn("local currency", overview)
        schema = next(json.loads(body) for language, body in code_blocks(workbook) if language == "json")
        self.assertEqual([field["name"] for field in schema["fields"]],
                         ["transaction_id", "payment_method", "amount", "currency", "store_id", "processed_at"])
        self.assertEqual(sum(body.startswith(".\\test.bat") for language, body in code_blocks(main)
                             if language == "powershell"), 1)

    def test_required_source_eventhouses_are_not_confused_with_history(self):
        paths = {
            "sales-volume-alert": (("S", "N"), ("N", "B")),
            "revenue-below-target": (("SRC", "Q"), ("Q", "PUB"), ("PUB", "BE")),
            "customer-loyalty-milestone": (("SRC", "PUB"), ("PUB", "BE")),
        }
        for scenario, edges in paths.items():
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                architecture = overview.split("## Event-driven architecture {#architecture}\n", 1)[1]
                diagram = next(body for language, body in code_blocks(architecture) if language == "mermaid")
                self.assertIn("Source Eventhouse", diagram)
                for source, target in edges:
                    self.assertRegex(diagram, rf'{source} -->\s*(?:\|"[^"]+"\|\s*)?{target}')
        revenue = guide_path("revenue-below-target").read_text(encoding="utf-8")
        for fragment in ("@pipeline().RunId", "uses the event's revenue figures directly",
                         "without needing Business Event history", "revenue_lift_pct",
                         "shortfall `20000` and required lift `25%`", "ready_for_review"):
            self.assertIn(fragment, revenue)
        diagram = next(body for language, body in code_blocks(scenario_path("revenue-below-target").read_text())
                       if language == "mermaid")
        self.assertNotIn("Teams", diagram)

    def test_revenue_workbook_retains_configured_names_and_rule(self):
        folder = ROOT / "cicd" / "revenue-below-target"
        config = json.loads((folder / "config.card.example.json").read_text())
        manifest = json.loads((folder / "scenario.json").read_text())
        workbook = guide_path(folder.name).read_text(encoding="utf-8")
        self.assertEqual(workbook.splitlines()[0], "# " + manifest["scenario_name"] + ": Workbook")
        for role, name in config["resource_names"].items():
            with self.subTest(role=role):
                self.assertIn(name, workbook)
        self.assertIn(manifest["business_response"]["rule_name"], workbook)
        self.assertNotIn("SalesPerformance", workbook)

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
                if package not in ("sales-volume-alert", "real-time-stream-alert", "low-stock-threshold",
                                   "revenue-below-target", "discount-approval-routing", "fraud-response-before-fulfillment",
                                   "real-time-viewer-recommendations", "abandoned-cart-recovery", "data-pipeline-failure-follow-up",
                                   "customer-loyalty-milestone"):
                    self.assertIn(f"https://github.com/microsoft/fabric-events/tree/main/cicd/{package}", page)
                    self.assertEqual(page.count("## Deploy this scenario"), 1)
                manifest_path = folder / "scenario.json"
                manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
                manual_source = manifest.get("command_behavior", {}).get("test") == "manual_native_kql_inputs_only"
                source_assets = ((folder / "sample-input.kql").is_file() and (folder / "source-query.kql").is_file()
                                 if manual_source else (folder / "test.bat").is_file())
                executable = (folder / "deploy.bat").is_file() and source_assets
                if manual_source:
                    self.assertFalse((folder / "test.bat").exists())
                if manifest.get("implemented_scope") in ("public_foundations", "public_full_deployment", "public_hybrid_deployment"):
                    self.assertTrue(executable)
                    if package in ("sales-volume-alert", "real-time-stream-alert", "low-stock-threshold",
                                   "revenue-below-target", "discount-approval-routing", "fraud-response-before-fulfillment",
                                   "real-time-viewer-recommendations", "abandoned-cart-recovery"):
                        self.assertIn("## Resources and coverage", customer_guide)
                        self.assertIn("Not configured by this package", customer_guide)
                    self.assertNotIn("e2e_verified", customer_guide)
                    self.assertNotIn("**Status:", page)
                    for example in ("card", "wizard"):
                        config = json.loads((folder / f"config.{example}.example.json").read_text())
                        if manifest.get("package_policy") == "single_current_package":
                            self.assertNotIn("configuration_version", config)
                        else:
                            self.assertEqual(config["configuration_version"], manifest.get("configuration_version", "2.0"))
                        self.assertIsNone(config["tenant_id"])
                        self.assertIsNone(config["workspace_id"])
                elif manifest.get("deployable", executable):
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

    def test_cart_workbook_contains_both_real_responses_and_boundary_exclusions(self):
        overview = scenario_path("abandoned-cart-recovery").read_text(encoding="utf-8")
        workbook = guide_path("abandoned-cart-recovery").read_text(encoding="utf-8")
        for value in ("**Is greater than**", "**On each value**", "Activation ID", "float32",
                      "test_recipient", "## 3. Complete manual setup before testing",
                      "In the approved mailbox, find three reminders", "dbo.CartRecoveryTasks",
                      "dbo.RecordCartRecoveryTask", "setup.sql", "record-cart-task.sql",
                      "Open flow builder", "SQL Server → Execute stored procedure",
                      "same-named SQL parameters", "six pending tasks", "29.99"):
            self.assertIn(value, workbook)
        self.assertNotIn("On each event", workbook)
        self.assertIn("current cart state, contact preferences", overview)
        self.assertIn("three reminders", overview)
        self.assertIn("six SQL follow-up tasks", overview)
        self.assertNotIn("recovers revenue automatically", overview)

    def test_viewer_workbook_preserves_the_five_string_contract_and_real_outputs(self):
        overview = scenario_path("real-time-viewer-recommendations").read_text(encoding="utf-8")
        workbook = guide_path("real-time-viewer-recommendations").read_text(encoding="utf-8")
        for value in ("On each value", "Files", "ready_for_serving", "watch", "like",
                      "without a trained model", "six viewer interactions", "no response execution or file"):
            self.assertIn(value, workbook)
        self.assertIn("does not prove that an application displayed", overview)
        schema = next(json.loads(body) for language, body in code_blocks(workbook) if language == "json")
        self.assertEqual(schema["fields"], [{"name": name, "type": "string"} for name in (
            "viewer_id", "session_id", "content_id", "action", "occurred_at")])
        self.assertEqual(len([body for language, body in code_blocks(workbook) if language == "python"]), 2)
        for text in (overview, workbook):
            self.assertNotIn("Power Automate", text)

    def test_fraud_workbook_preserves_decisions_and_external_response_enforcement(self):
        overview = scenario_path("fraud-response-before-fulfillment").read_text(encoding="utf-8")
        workbook = guide_path("fraud-response-before-fulfillment").read_text(encoding="utf-8")
        for value in ("the premium Data Activator connector", "register all six `source_orders`",
                      "0.5499", "0.55", "0.8499", "0.85", "review_requested", "review_completed",
                      "**Is equal to**", "**Becomes equal to**", "**Activation ID**", "x-activation-id",
                      "float32", "no Azure backend", "HTTP `409`",
                      "The three operational responses still need your connections",
                      "### Test order system", "reject competing decisions"):
            self.assertIn(value, workbook)
        self.assertIn("supplied score", overview)
        for forbidden in ("cicd_old", "--verify-only", "--new-run", "payment_method",
                          "## Archived native reference"):
            self.assertNotIn(forbidden, workbook)

    def test_discount_workbook_uses_one_sample_with_optional_application_reference(self):
        workbook = guide_path("discount-approval-routing").read_text(encoding="utf-8")
        main = workbook.split("## Optional application integration", 1)[0]
        for case in ("Within policy", "Seller boundary", "Manager lower boundary",
                     "Manager upper boundary", "Executive margin", "Executive precedence"):
            self.assertIn(case, main)
        for value in ("prepares three Fabric items", "Connect Eventstream in Step 3",
                      "six proposals ready for review in both Teams and Eventstream",
                      "no notification for the two proposals within policy"):
            self.assertIn(value, main)
        self.assertNotIn("python server.py", main)
        self.assertIn("python server.py", workbook)
        self.assertIn("{#parse-the-fabric-response}", workbook)
        self.assertIn("publication_requested", workbook)
        self.assertIn("optional browser application", scenario_path("discount-approval-routing").read_text())
        schema = next(json.loads(body) for language, body in code_blocks(workbook) if language == "json"
                      and '"fields"' in body)
        self.assertEqual(len(schema["fields"]), 9)

    def test_revenue_workbook_retains_native_setup_observation_gates_and_exact_source(self):
        workbook = guide_path("revenue-below-target").read_text(encoding="utf-8")
        setup = workbook.split("## 3. Complete manual setup before testing", 1)[1].split("## 4.", 1)[0]
        for value in ("### 3.1. KQL data and Real-Time Dashboard",
                      "### 3.2. Check the prepared event-driven response",
                      "### 3.3. Publisher rule", "### 3.4. Activation",
                      "**KQL Queryset**", "**Pin to dashboard**", "**Current revenue**",
                      "**Revenue observations**", "The response is connected for you",
                      "**Revenue native source contract → Revenue reporting period**",
                      "stopped **Revenue below target** rule", "existing **RevenuePublisher** item",
                      "Selecting a source event shows **Live feed**, not the rule editor",
                      "**Definition → Action**", "Select **View action** to open **Edit the action**",
                      "six preconfigured dynamic mappings", "Confirm that the rule is stopped",
                      "**Start** is enabled and **Stop** is disabled", "**Condition: Decreases below**",
                      "Close the action editor without changing the mappings",
                      "Do not use fixed sample values or parse the combined **Composite key**",
                      "**Keep the reporting period consistent.**",
                      "changing revenue must not create a new monitored period"):
            self.assertIn(value, setup)
        for field in ("region", "period_start", "revenue", "target", "variance_pct", "detected_at"):
            self.assertIn(f"| `{field}` | `{field}` |", setup)
        publisher = setup.split("### 3.3.", 1)[1].split("### 3.4.", 1)[0]
        self.assertNotIn("Select **New rule**", publisher)
        sample = workbook.split("## 4. Test the flow from KQL", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn("```powershell", sample)
        for value in ("Add each snapshot separately",
                      "**Wait until Activator shows that state before moving to the next row.**",
                      "Leave the pipeline to start from the event rather than running it manually",
                      "two observed active source polls", "ready_for_review"):
            self.assertIn(value, sample)
        sequence = re.findall(
            r"^\| (?:Baseline|Exact boundary|First crossing below|Remaining below|Recovery above|Second crossing below)"
            r" \| `(\d+)` \| `(-\d+)` \| (None|One) \|$", sample, re.M)
        self.assertEqual(sequence, [("90000", "-10", "None"), ("85000", "-15", "None"),
                                   ("80000", "-20", "One"), ("78000", "-22", "None"),
                                   ("90000", "-10", "None"), ("80000", "-20", "One")])
        data_steps = setup.split("### 3.1.", 1)[1].split("### 3.2.", 1)[0]
        rendered = markdown.markdown(data_steps, extensions=["pymdownx.superfences"])
        self.assertEqual(rendered.count("<li>"), 8)
        self.assertIn('<div class="highlight">', rendered.split("<li>")[4].split("</li>", 1)[0])
        package = ROOT / "cicd" / "revenue-below-target"
        commands = [body for language, body in code_blocks(workbook) if language == "kusto"]
        self.assertIn((package / "sample-input.kql").read_text().strip(), commands)
        self.assertIn((package / "source-query.kql").read_text().strip(), commands)
        self.assertNotRegex(workbook, r"Power BI|Power Query|Semantic model|```dax")

    def test_inventory_uses_the_warehouse_finding_without_another_detector(self):
        overview = scenario_path("low-stock-threshold").read_text(encoding="utf-8")
        workbook = guide_path("low-stock-threshold").read_text(encoding="utf-8")
        for value in ("warehouse", "second stock test", "not a recommended purchase quantity"):
            self.assertIn(value, overview)
        main = workbook.split("## Optional event history", 1)[0]
        for value in ("Production WMS connection", "synthetic findings", "**three requests**",
                      "pending_approval", "`4`, `0` and `-2`", "`10`, `5` and `8`",
                      "`threshold` is not an order quantity", "existing request unchanged"):
            self.assertIn(value, main)

    def test_business_pages_identify_the_real_operational_action(self):
        for scenario, action in (
            ("sales-volume-alert", "Teams"),
            ("low-stock-threshold", "Data Pipeline"),
            ("real-time-stream-alert", "User Data Function"),
        ):
            with self.subTest(scenario=scenario):
                overview = scenario_path(scenario).read_text(encoding="utf-8")
                workbook = guide_path(scenario).read_text(encoding="utf-8")
                self.assertIn(f"**Consumer(s):** Activator ({action})", overview)
                self.assertIn(f"Activator ({action})", workbook)
                self.assertNotIn("\u2014", overview)

    def test_workbook_samples_require_persisted_outcomes_from_the_actual_source(self):
        for scenario, folder, status in (
            ("low-stock-threshold", "ReplenishmentRequests", "pending_approval"),
            ("real-time-stream-alert", "PaymentReviewCases", "pending_review"),
        ):
            with self.subTest(scenario=scenario):
                workbook = guide_path(scenario).read_text(encoding="utf-8")
                main = workbook.split("## Optional event history", 1)[0]
                self.assertIn(f"Files/{folder}", main)
                self.assertIn(status, main)
                self.assertIn("saved", main)
                self.assertIn(".\\test.bat --config .\\config.local.json --confirm-workspace $workspaceId", main)
                self.assertIn("repeated", main.lower())
                self.assertIn("source_run_id", main)

    def test_catalog_distinguishes_optional_history_from_the_analysis_scenario(self):
        catalog = scenario_path("index").read_text(encoding="utf-8")
        for action in ("Teams", "Data Pipeline", "User Data Function"):
            self.assertIn(f"| Activator ({action}), optional Eventhouse |", catalog)
        row = next(line for line in catalog.splitlines() if "(order-history-analysis.md)" in line and line.startswith("|"))
        self.assertIn("| Eventhouse | Beginner |", row)
        overview = scenario_path("order-history-analysis").read_text(encoding="utf-8")
        architecture = overview.split("## Event-driven architecture", 1)[1]
        self.assertIn('BE -->|"Analyze in Eventhouse"| EH', architecture)
        self.assertNotIn("-.->", architecture)

    def test_order_history_sample_publishes_the_synthetic_orders_used_in_the_analysis(self):
        workbook = workbook_path("order-history-analysis").read_text(encoding="utf-8")
        cell = next(body for language, body in code_blocks(workbook) if language == "python")
        calls = []
        notebookutils = SimpleNamespace(businessEvents=SimpleNamespace(
            publish=lambda **kwargs: calls.append(kwargs) or True,
        ))
        with patch.dict(sys.modules, {"notebookutils": notebookutils}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(cell, "order-history-workbook", "exec"), {})
        self.assertEqual(len(calls), 1)
        rows = calls[0]["eventData"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["order_id"] for row in rows}), 6)
        self.assertEqual(len({row["sample_run_id"] for row in rows}), 1)
        self.assertEqual({
            store: (sum(row["store_id"] == store for row in rows),
                    sum(row["order_total_usd"] for row in rows if row["store_id"] == store))
            for store in {row["store_id"] for row in rows}
        }, {"north": (3, 350.0), "south": (3, 550.0)})
        schema = next(json.loads(body) for language, body in code_blocks(workbook) if language == "json")
        self.assertTrue(all(set(row) == {field["name"] for field in schema["fields"]} for row in rows))
        query = next(body for language, body in code_blocks(workbook) if language == "kql")
        self.assertIn("where sample_run_id == SampleRun", query)
        self.assertIn("arg_max(placed_at, store_id, order_total_usd) by order_id", query)
        self.assertNotIn("test.bat", workbook)
        self.assertNotIn(".zip", workbook)

    def test_order_history_sample_does_not_report_success_when_publication_fails(self):
        cell = next(body for language, body in code_blocks(
            workbook_path("order-history-analysis").read_text(encoding="utf-8")
        ) if language == "python")
        notebookutils = SimpleNamespace(businessEvents=SimpleNamespace(publish=lambda **kwargs: False))
        with patch.dict(sys.modules, {"notebookutils": notebookutils}), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                exec(compile(cell, "order-history-workbook", "exec"), {})

    def test_local_artwork_is_excluded_even_if_restored_to_the_docs_folder(self):
        with TemporaryDirectory() as directory:
            for name in ("index.md", "assets/home/public.png", "assets/home2/v001/private.png"):
                path = Path(directory) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"local artwork exclusion fixture")
            config = load_config(config_file=str(ROOT / "mkdocs.yml"), docs_dir=directory)
            files = {file.src_uri: file for file in get_files(config)}
            self.assertFalse(files["index.md"].inclusion.is_excluded())
            self.assertFalse(files["assets/home/public.png"].inclusion.is_excluded())
            self.assertTrue(files["assets/home2/v001/private.png"].inclusion.is_excluded())
        home = (ROOT / "docs" / "home" / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("assets/home2/v001/", home)

    def test_home_illustrations_resolve_to_published_home_assets(self):
        home = (ROOT / "docs" / "home" / "index.md").read_text(encoding="utf-8")
        images = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', home)
        self.assertEqual(len(images), 4)
        files = {file.src_uri: file for file in get_files(self.config)}
        for reference in images:
            with self.subTest(reference=reference):
                image = (ROOT / "docs" / "home" / reference).resolve()
                name = image.relative_to(ROOT / "docs").as_posix()
                self.assertTrue(name.startswith("assets/home/"))
                self.assertIn(name, files)
                self.assertFalse(files[name].inclusion.is_excluded())
                self.assertTrue(image.is_file())

    def test_unpublished_local_scenarios_are_excluded_from_the_site(self):
        files = get_files(self.config)
        excluded = {
            "business-events/scenarios/shipment-delay-auto-mitigation.md",
            "business-events/scenarios/shipment-delay-escalation.md",
            "business-events/scenarios/shipment-delay-escalation/deployment.md",
            "assets/downloads/shipment-delay-escalation.zip",
            "assets/home2/v001/shipment-response.png",
        }
        for file in files:
            if file.src_uri in excluded:
                self.assertTrue(file.inclusion.is_excluded(), file.src_uri)
        published = [file for file in files if not file.inclusion.is_excluded()]
        self.assertFalse(any("shipment-delay" in file.src_uri for file in published))
        for file in published:
            if file.src_uri.endswith(".md"):
                self.assertNotIn("shipment-delay-", Path(file.abs_src_path).read_text(encoding="utf-8"))

    def test_catalog_tables_link_all_published_workbooks(self):
        catalog = scenario_path("index").read_text(encoding="utf-8")
        headers = [line for line in catalog.splitlines() if line.startswith("| Scenario |")]
        self.assertTrue(headers)
        for header in headers:
            self.assertEqual(header.strip("|").split("|")[-1].strip(), "Workbook")
        rows = [line for line in catalog.splitlines() if line.startswith("| [")]
        self.assertEqual(len(rows), len(SCENARIOS))
        for row in rows:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            self.assertEqual(len(cells), 6)
            link = re.fullmatch(r"\[[^\]]+\]\(([^)]+\.md)\)", cells[0])
            self.assertIsNotNone(link)
            scenario = Path(link[1]).stem
            self.assertIn(scenario, SCENARIOS)
            self.assertEqual(cells[-1], f"[Workbook]({scenario}/deployment.md)")
            self.assertTrue(workbook_path(scenario).is_file())

    def test_scenario_catalog_and_navigation_put_advanced_exercises_last(self):
        catalog = scenario_path("index").read_text(encoding="utf-8")
        level_order = {"Beginner": 0, "Intermediate": 1, "Advanced": 2}
        catalog_order = []
        for section in re.split(r"\n#{2,3} ", catalog):
            ranks = []
            for row in section.splitlines():
                if not row.startswith("| ["):
                    continue
                cells = [cell.strip() for cell in row.strip("|").split("|")]
                scenario = re.fullmatch(r"\[[^\]]+\]\(([^)]+)\.md\)", cells[0]).group(1)
                rank = level_order[cells[4]]
                ranks.append(rank)
                catalog_order.append((scenario, rank))
            with self.subTest(section=section.splitlines()[0]):
                self.assertEqual(ranks, sorted(ranks))
        navigation = get_navigation(get_files(self.config), self.config)
        actual = [
            Path(page.file.src_path).stem for page in navigation.pages
            if Path(page.file.src_path).parent == Path("business-events") / "scenarios"
            and Path(page.file.src_path).stem in SCENARIOS
        ]
        self.assertEqual(actual, [scenario for scenario, _ in sorted(
            catalog_order, key=lambda entry: entry[1],
        )])

    def test_four_demo_guides_and_configuration_templates_do_not_expose_release_versions(self):
        for scenario in ("sales-volume-alert", "low-stock-threshold", "real-time-stream-alert", "revenue-below-target"):
            with self.subTest(scenario=scenario):
                guide = guide_path(scenario).read_text(encoding="utf-8")
                self.assertNotRegex(guide, r"(?i)\bv\d+\.\d+\b|\bversioned\b|\blatest\b")
                folder = ROOT / "cicd" / scenario
                manifest = json.loads((folder / "scenario.json").read_text())
                self.assertEqual(manifest["package_policy"], "single_current_package")
                self.assertNotIn("configuration_version", manifest)
                schema = json.loads((folder / "config.schema.json").read_text())
                self.assertNotIn("configuration_version", schema["required"])
                for mode in ("card", "wizard"):
                    config = json.loads((folder / f"config.{mode}.example.json").read_text())
                    self.assertNotIn("configuration_version", config)

    def test_native_response_guides_use_operational_files_and_source_driven_e2e(self):
        for scenario, action, folder in (
            ("low-stock-threshold", "Data Pipeline", "ReplenishmentRequests"),
            ("real-time-stream-alert", "User Data Function", "PaymentReviewCases"),
        ):
            with self.subTest(scenario=scenario):
                text = guide_path(scenario).read_text(encoding="utf-8")
                self.assertIn(f"Activator ({action})", text)
                self.assertIn(f"Files/{folder}", text)
                self.assertIn("not a Delta or SQL table", text)
                self.assertRegex(text, r"rather than (?:sending )?Teams messages")
                self.assertIn("### Evaluation criteria", text)
                self.assertIn("repeated", text.lower())
                command = next(line for line in text.splitlines() if line.startswith(".\\test.bat "))
                self.assertNotIn("--source-only", command)
                self.assertNotIn("--teams-chat-id", command)
                self.assertNotIn("e2e_verified", text)

    def test_customer_scenarios_hide_internal_certification_and_development_status(self):
        forbidden = re.compile(
            r"\*\*Status:|\bcertif\w*\b|\bhybrid\b|"
            r"\b(?:live|full) E2E (?:verified|pending)\b|"
            r"e2e_verified|full_scenario_blocked|manual_setup_required|manual_source_trigger_required",
            re.I,
        )
        for scenario, overview_name in GUIDES.items():
            for path in (guide_path(scenario), ROOT / "docs" / "business-events" / "scenarios" / (overview_name + ".md")):
                with self.subTest(path=path):
                    self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))
        catalog = ROOT / "docs" / "business-events" / "scenarios" / "index.md"
        self.assertIsNone(forbidden.search(catalog.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
