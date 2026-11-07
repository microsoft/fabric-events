import copy
import ast
import base64
from collections import Counter
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import uuid
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy
import bundle
import card
import connections
import native
from fabric_api import DAILY, DAILY_FABRIC, MSIT, MSIT_FABRIC, FABRIC, KUSTO, POWERBI, STORAGE, FabricApi, FabricOperationError, FabricRequestError, NotebookTokens, Reply
from scenario import expected_findings, finding

WORKSPACE = "11111111-1111-4111-8111-111111111111"
SCHEMA = "22222222-2222-4222-8222-222222222222"
RUN = "33333333-3333-4333-8333-333333333333"
WINDOW = "2026-11-05T13:00:00Z"


def config():
    result = deploy.load_config(deploy.ROOT / "config.example.json")
    result.update(workspace_id=WORKSPACE, environment="fabric", teams_delivery="channel", teams_recipients=[])
    return result


def individual_config():
    result = config()
    result.update(teams_delivery="individual", teams_recipients=["operations@example.com"])
    return result


def import_receipts(roles=native.FOUNDATIONS):
    items = native.local_items()
    return [{
        "itemLogicalId": items[role]["logical_id"],
        "itemId": str(uuid.uuid5(uuid.NAMESPACE_URL, role)),
        "itemDisplayName": items[role]["name"], "itemType": items[role]["type"],
        "operationStatus": "Succeeded", "operationType": "Create",
    } for role in roles]


class NativeTests(unittest.TestCase):
    def test_notebook_defaults_bind_only_the_declared_configuration_parameter(self):
        parameters = {"operation": "publish", "workspace_id": WORKSPACE, "value": 'quote" and newline\n'}
        definition = native.notebook_configuration(parameters)
        source = base64.b64decode(definition["parts"][0]["payload"]).decode()
        assignments = {
            node.targets[0].id: node.value for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        }
        self.assertEqual(parameters, json.loads(ast.literal_eval(assignments["config_json"])))
        self.assertEqual("[]", ast.literal_eval(assignments["fixtures_json"]))
        items = native.local_items()
        items["notebook"]["files"]["notebook-content.py"] += b'\nconfig_json = "{}"\n'
        with self.assertRaisesRegex(ValueError, "one unbound"):
            native.notebook_configuration(parameters, items)

    def test_native_plan_references_resolve_and_preview_is_explicit(self):
        items = native.local_items()
        plan = native.validate_plan(items)
        database = json.loads(items["database"]["files"]["DatabaseProperties.json"])
        self.assertEqual(items["eventhouse"]["logical_id"], database["parentEventhouseItemId"])
        self.assertTrue(all(not group.get("postActions") for group in plan["groups"]))
        payload = native.bulk_payload((*native.FOUNDATIONS, "deployment_plan"), items, with_plan=True)
        self.assertEqual({
            "referenceType": "ByLogicalId", "logicalId": items["deployment_plan"]["logical_id"],
        }, payload["options"]["deploymentPlan"])
        self.assertFalse(payload["options"]["allowPairingByName"])

    def test_native_plan_rejects_cycles_unknown_dependencies_and_publisher_actions(self):
        for fault in ("cycle", "unknown", "publish"):
            with self.subTest(fault=fault):
                items = native.local_items()
                plan = native.validate_plan(items)
                if fault == "cycle":
                    plan["groups"][0]["dependsOn"] = [{"groupName": "Publisher"}]
                elif fault == "unknown":
                    plan["groups"][0]["dependsOn"] = [{"groupName": "Unknown"}]
                else:
                    plan["groups"][0]["postActions"] = [{"name": "Publish", "job": {"type": "Execute"}}]
                items["deployment_plan"]["files"]["plan.yml"] = json.dumps(plan).encode()
                with self.assertRaises(ValueError):
                    native.validate_plan(items)

    def test_unbound_paused_consumer_cannot_be_imported(self):
        entities = deploy.decode_part(native.definition("activator"), "ReflexEntities.json")
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        self.assertFalse(rule["payload"]["definition"]["settings"]["shouldRun"])
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            native.bulk_payload(("activator",))
        with self.assertRaisesRegex(ValueError, "Unknown"):
            native.bind({"value": "${not_declared}"}, {})

    def test_consumer_binding_preserves_native_action_and_encodes_recipients_safely(self):
        target = individual_config()
        target["teams_recipients"] = ['quoted"recipient@example.com', "second@example.com"]
        bound = deploy.build_activator(target, SCHEMA)
        payload = native.bulk_payload(("activator",), overrides={"activator": bound})
        entities = deploy.decode_part(bound, "ReflexEntities.json")
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        instance = json.loads(rule["payload"]["definition"]["instance"])
        action = next(step for step in instance["steps"] if step["name"] == "ActStep")["rows"][0]
        recipients = next(argument for argument in action["arguments"] if argument["name"] == "recipients")
        self.assertEqual(target["teams_recipients"], [value["value"] for value in recipients["values"]])
        self.assertNotIn("${", json.dumps(payload))
        for entry in payload["definitionParts"]:
            self.assertNotIn("${", base64.b64decode(entry["payload"]).decode())


class PortalPackageTests(unittest.TestCase):
    def test_uploaded_wizard_configuration_drives_packaged_portal_plan_without_authentication(self):
        notebook = runpy.run_path(str(deploy.ROOT / "portal-deploy.py"))
        with tempfile.TemporaryDirectory() as directory:
            target = deploy.load_config(deploy.ROOT / "config.wizard.example.json")
            target.update(event_name="Retail.Stores.CustomVolume", environment="daily", workspace_id=WORKSPACE)
            target["resource_names"]["notebook"] = "Custom Publisher"
            Path(directory, "reviewed.json").write_text(json.dumps(target), encoding="utf-8-sig")
            notebook["run_from_portal"].__globals__["config_file"] = "reviewed.json"
            bundle.build(Path(directory) / "sales-volume-alert.zip")
            credentials = Mock()
            credentials.getToken.side_effect = AssertionError("Must remain offline")
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                notebook["run_from_portal"](credentials, directory)
            result = json.loads(output.getvalue())
            self.assertEqual("daily", result["environment"])
            self.assertEqual(WORKSPACE, result["workspace_id"])
            self.assertEqual(target["event_name"], result["history_table"])
            self.assertIn("Custom Publisher", result["foundation_import_items"])
            credentials.getToken.assert_not_called()

    def test_archive_contains_exact_native_sources_but_no_local_configuration_or_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sales-volume-alert.zip"
            result = bundle.build(path)
            self.assertFalse(result["cloud_writes"])
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(result["file_count"], len(archive.namelist()))
                self.assertTrue(all(name.startswith(bundle.PREFIX) for name in archive.namelist()))
                self.assertFalse(any(".local." in name or "state.json" in name for name in archive.namelist()))
                self.assertFalse(any("/.deployments/" in name for name in archive.namelist()))
                for item in native.local_items().values():
                    for name, content in item["files"].items():
                        self.assertEqual(
                            content, archive.read(bundle.PREFIX + "items/" + item["folder"] + "/" + name),
                        )
                self.assertEqual(
                    (deploy.ROOT / "data" / "store-windows.json").read_bytes(),
                    archive.read(bundle.PREFIX + "data/store-windows.json"),
                )
                self.assertNotIn(bundle.PREFIX + "README.md", archive.namelist())
                self.assertFalse(any(name.lower().endswith((".md", ".html")) for name in archive.namelist()))
                for name in ("deploy.bat", "test.bat"):
                    self.assertEqual(
                        (deploy.ROOT / name).read_bytes(), archive.read(bundle.PREFIX + name),
                    )
                for name in ("card.py", "config.card.example.json"):
                    self.assertEqual(
                        (deploy.ROOT / name).read_bytes(), archive.read(bundle.PREFIX + name),
                    )
            with self.assertRaisesRegex(ValueError, ".zip"):
                bundle.build(Path(directory) / "config.local.json")

    def test_portal_plan_executes_packaged_sources_without_authentication_and_restores_modules(self):
        notebook = runpy.run_path(str(deploy.ROOT / "portal-deploy.py"))
        with tempfile.TemporaryDirectory() as directory:
            bundle.build(Path(directory) / "sales-volume-alert.zip")
            credentials = Mock()
            credentials.getToken.side_effect = AssertionError("Offline plan must not acquire tokens")
            before = {name: sys.modules[name] for name in ("deploy", "native", "scenario", "fabric_api")}
            with (
                patch("subprocess.run", side_effect=AssertionError("Must not invoke Azure CLI")),
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Must remain offline")),
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                notebook["run_from_portal"](credentials, directory)
            result = json.loads(output.getvalue())
            self.assertFalse(result["cloud_writes"])
            self.assertEqual("msit", result["environment"])
            credentials.getToken.assert_not_called()
            for name, module in before.items():
                self.assertIs(module, sys.modules[name])

    def test_portal_rejects_unsafe_archive_before_extraction(self):
        notebook = runpy.run_path(str(deploy.ROOT / "portal-deploy.py"))
        for paths in (["../escape.py"], ["C:/escape.py"], ["folder\\..\\escape.py"], ["one.py", "ONE.py"]):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as directory:
                with zipfile.ZipFile(Path(directory) / "sales-volume-alert.zip", "w") as archive:
                    for name in paths:
                        archive.writestr(name, "untrusted")
                with self.assertRaises(ValueError):
                    notebook["run_from_portal"](Mock(), directory)

    def test_notebook_credentials_map_audiences_cache_tokens_and_preserve_user_identity(self):
        claims = {
            "exp": int(time.time()) + 3600, "scp": "Item.ReadWrite.All", "idtyp": "user",
            "tid": WORKSPACE, "oid": RUN, "preferred_username": "operations@example.com",
        }
        token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
        credentials = Mock()
        credentials.getToken.return_value = token
        provider = NotebookTokens(credentials)
        for resource, audience in ((FABRIC, "pbi"), (POWERBI, "pbi"), (STORAGE, "storage"), (KUSTO, "kusto")):
            self.assertEqual(token, provider.get(resource))
            credentials.getToken.assert_called_with(audience)
        count = credentials.getToken.call_count
        self.assertEqual(RUN, provider.identity()["objectId"])
        self.assertEqual("operations@example.com", provider.current_user_address())
        self.assertEqual(count, credentials.getToken.call_count)
        with self.assertRaisesRegex(ValueError, "audience"):
            provider.get("https://unapproved.example")
        claims["exp"] = 1
        credentials.getToken.return_value = (
            "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
        )
        with self.assertRaisesRegex(RuntimeError, "expired"):
            NotebookTokens(credentials).get(FABRIC)


class CardLauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config.card.example.json"
        settings = deploy.load_config(deploy.ROOT / "config.card.example.json")
        settings["workspace_id"] = WORKSPACE
        self.config_path.write_text(json.dumps(settings))
        self.state_path = self.root / "shared state.json"
        self.target = deploy.Deployment(deploy.load_config(self.config_path), state_path=self.state_path)
        self.target.state["items"] = {
            role: str(uuid.uuid5(uuid.NAMESPACE_URL, role)) for role in (*native.FOUNDATIONS, "activator")
        }
        self.target.state["last_run"] = {"run_id": RUN}
        self.target.save()
        self.shared = ["--config", str(self.config_path), "--state-file", str(self.state_path)]
        directory = patch.object(card, "state_directory", return_value=self.root)
        directory.start()
        self.addCleanup(directory.stop)
        root = patch.object(card, "ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)
        self.current = self.root / "SalesDrop.active.json"

    def test_each_deployment_starts_fresh_without_reading_or_archiving_previous_state(self):
        old = self.root / "SalesDrop.json"
        old.write_text("invalid historical JSON must not be read")
        self.current.write_text("invalid previous reference must not be read")
        selected = []
        with patch.object(deploy, "main", return_value=0) as cli, patch(
            "sys.stdout", new_callable=io.StringIO,
        ):
            for _ in range(2):
                self.assertEqual(card.main(["deploy"]), 0)
                state = Path(json.loads(self.current.read_text())["state_file"])
                selected.append(state)
                saved = json.loads(state.read_text())
                self.assertEqual(saved["items"], {})
                self.assertNotIn("last_run", saved)
            self.assertEqual(cli.call_count, 6)
            for index, state in enumerate(selected):
                for call in cli.call_args_list[index * 3:index * 3 + 3]:
                    argv = call.args[0]
                    self.assertEqual(argv[argv.index("--state-file") + 1], str(state))
        self.assertNotEqual(*selected)
        self.assertTrue(all(path.exists() for path in selected))
        self.assertEqual(old.read_text(), "invalid historical JSON must not be read")
        self.assertEqual(list(self.root.glob("*.history-*.json")), [])

    def test_explicit_new_deployment_cannot_overwrite_an_existing_ledger(self):
        original = self.state_path.read_bytes()
        with patch.object(deploy, "main") as cli:
            with self.assertRaisesRegex(ValueError, "unused --state-file"):
                card.main(["deploy", *self.shared])
        self.assertEqual(self.state_path.read_bytes(), original)
        cli.assert_not_called()

    def test_default_test_uses_only_the_current_reference(self):
        self.current.write_text(json.dumps({"state_file": str(self.state_path)}))
        (self.root / "SalesDrop.deploy-other.json").write_text("not a selected deployment")
        with patch.object(deploy, "main", return_value=2) as cli, patch(
            "sys.stdout", new_callable=io.StringIO,
        ):
            self.assertEqual(card.main(["test", "--verify-only"]), 2)
        self.assertEqual(cli.call_args.args[0], ["verify", *self.shared])
        self.current.unlink()
        with patch.object(deploy, "main") as cli:
            with self.assertRaisesRegex(ValueError, "No current deployment reference"):
                card.main(["test"])
        cli.assert_not_called()

    def test_a_failed_new_attempt_does_not_select_an_older_success_for_testing(self):
        self.current.write_text(json.dumps({"state_file": str(self.state_path)}))
        original = self.state_path.read_bytes()
        with patch.object(deploy, "main", side_effect=[0, 7]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(card.main(["deploy"]), 7)
        selected = Path(json.loads(self.current.read_text())["state_file"])
        self.assertNotEqual(selected, self.state_path)
        with patch.object(deploy, "main") as cli, patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(ValueError, "No complete deployment ledger"):
                card.main(["test"])
        cli.assert_not_called()
        self.assertEqual(self.state_path.read_bytes(), original)

    def test_phases_share_identity_but_never_mix_responsibilities(self):
        for phase, flags, commands, results in (
            ("deploy", [], ["plan", "preflight", "deploy"], [0, 0, 0]),
            ("test", [], ["run", "verify"], [0, 2]),
            ("test", ["--verify-only"], ["verify"], [2]),
            ("test", ["--verify-only", "--teams-confirmed"], ["verify"], [0]),
        ):
            shared = self.shared if phase == "test" else [
                "--config", str(self.config_path), "--state-file", str(self.root / "new state.json"),
            ]
            with self.subTest(phase=phase, flags=flags), patch.object(
                deploy, "main", side_effect=results,
            ) as cli, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(card.main([phase, *shared, *flags]), results[-1])
                self.assertEqual([call.args[0][0] for call in cli.call_args_list], commands)
                for call in cli.call_args_list:
                    argv = call.args[0]
                    self.assertEqual(argv[1:5], shared)
                    self.assertEqual("--seed-dummy-data" in argv, argv[0] == "run")
                    self.assertEqual("--teams-confirmed" in argv, "--teams-confirmed" in flags)
                    self.assertEqual("--confirm-workspace" in argv, argv[0] in ("deploy", "run"))

    def test_stage_failure_stops_without_running_later_stages(self):
        for phase, results in (("deploy", [7]), ("deploy", [0, 7]), ("deploy", [0, 0, 7]), ("test", [7])):
            shared = self.shared if phase == "test" else [
                "--config", str(self.config_path), "--state-file", str(self.root / (uuid.uuid4().hex + ".json")),
            ]
            with self.subTest(phase=phase, results=results), patch.object(
                deploy, "main", side_effect=results,
            ) as cli, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(card.main([phase, *shared]), 7)
                self.assertEqual(cli.call_count, len(results))

    def test_test_requires_saved_deployment_and_verification_requires_a_publication(self):
        for mutation, flags, message in (
            ("missing", [], "deployment ledger"),
            ("partial", [], "deployment ledger"),
            ("unpublished", ["--verify-only"], "saved publication"),
            ("pending_job", ["--verify-only"], "Resume the saved"),
            ("pending_import", ["--verify-only"], "Resume the saved"),
        ):
            self.target.save()
            if mutation == "missing":
                self.state_path.unlink()
            elif mutation == "partial":
                saved = json.loads(self.state_path.read_text())
                saved["items"].pop("activator")
                self.state_path.write_text(json.dumps(saved))
            elif mutation == "unpublished":
                saved = json.loads(self.state_path.read_text())
                saved.pop("last_run")
                self.state_path.write_text(json.dumps(saved))
            else:
                saved = json.loads(self.state_path.read_text())
                saved[mutation] = "unresolved-operation"
                self.state_path.write_text(json.dumps(saved))
            with self.subTest(mutation=mutation), patch.object(deploy, "main") as cli, patch(
                "sys.stdout", new_callable=io.StringIO,
            ):
                with self.assertRaisesRegex(ValueError, message):
                    card.main(["test", *self.shared, *flags])
                cli.assert_not_called()

    def test_attestation_is_explicit_and_never_part_of_deployment_or_publication(self):
        for argv in (["deploy", "--verify-only"], ["deploy", "--teams-confirmed"], ["test", "--teams-confirmed"]):
            with self.subTest(argv=argv), patch.object(deploy, "main") as cli:
                with self.assertRaises(ValueError):
                    card.main([*argv, *self.shared])
                cli.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows batch entry points")
    def test_actual_batch_files_work_from_another_directory_and_propagate_exits(self):
        package = self.root / "package with spaces" / "cicd"
        scenario = package / "sales-volume-alert"
        archive_path = self.root / "scenario.zip"
        bundle.build(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(package.parent)
        (scenario / "native.py").write_text('FOUNDATIONS = ("schema", "eventhouse", "database", "notebook")\n')
        (scenario / "deploy.py").write_text(
            """import json, os
from pathlib import Path
def load_config(path):
    return json.loads(path.read_text())
class Deployment:
    def __init__(self, config, state_path=None):
        self.state_path = state_path
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {"items": {}}
    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state))
def main(argv):
    log = Path(os.environ["CARD_TEST_LOG"])
    with log.open("a") as stream:
        stream.write(json.dumps(argv) + "\\n")
    command = argv[0]
    if command == os.environ.get("CARD_FAIL_STAGE"):
        return 7
    state = Path(argv[argv.index("--state-file") + 1])
    if command == "deploy":
        state.write_text(json.dumps({"items": dict.fromkeys(
            ("schema", "eventhouse", "database", "notebook", "activator"), "item-id")}))
    if command == "run":
        saved = json.loads(state.read_text())
        saved["last_run"] = {"run_id": "test-run"}
        state.write_text(json.dumps(saved))
    return 2 if command == "verify" and "--teams-confirmed" not in argv else 0
""", encoding="utf-8",
        )
        cfg = scenario / "config.card.example.json"
        cfg.write_bytes(self.config_path.read_bytes())
        log = self.root / "calls.jsonl"
        env = {**os.environ, "CARD_TEST_LOG": str(log)}
        env["USERPROFILE"] = str(self.root / "profile with spaces")
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        settings = json.loads(cfg.read_text())
        directory = scenario / ".deployments" / settings["environment"] / settings["workspace_id"]
        current = directory / (settings["resource_prefix"] + ".active.json")

        def invoke(name, flags="", failure=""):
            env["CARD_FAIL_STAGE"] = failure
            log.write_text("")
            command = (
                f'"{os.environ["COMSPEC"]}" /d /s /c ""{scenario / name}" '
                f'{flags}"'
            )
            result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

        selected = []
        for name, flags, failure, expected_exit, expected_commands in (
            ("deploy.bat", "", "", 0, ["plan", "preflight", "deploy"]),
            ("test.bat", "", "", 2, ["run", "verify"]),
            ("test.bat", "--verify-only", "", 2, ["verify"]),
            ("test.bat", "--verify-only --teams-confirmed", "", 0, ["verify"]),
            ("test.bat", "", "run", 7, ["run"]),
            ("deploy.bat", "", "", 0, ["plan", "preflight", "deploy"]),
            ("test.bat", "--verify-only", "", 1, []),
            ("test.bat", "", "", 2, ["run", "verify"]),
            ("deploy.bat", "", "preflight", 7, ["plan", "preflight"]),
            ("test.bat", "", "", 1, []),
        ):
            with self.subTest(name=name, flags=flags, failure=failure):
                result, calls = invoke(name, flags, failure)
                self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
                self.assertEqual([call[0] for call in calls], expected_commands)
                state = Path(json.loads(current.read_text())["state_file"])
                if name == "deploy.bat":
                    selected.append(state)
                for call in calls:
                    self.assertEqual(call[call.index("--config") + 1], str(cfg))
                    self.assertEqual(call[call.index("--state-file") + 1], str(state))
                    self.assertEqual("--seed-dummy-data" in call, call[0] == "run")
        self.assertEqual(len(set(selected)), 3)
        self.assertTrue(all(path.exists() for path in selected))
        self.assertEqual(list(directory.glob("*.history-*.json")), [])
        self.assertFalse((Path(env["USERPROFILE"]) / ".copilot").exists())


class WizardConfigurationTests(unittest.TestCase):
    def target(self):
        target = deploy.load_config(deploy.ROOT / "config.wizard.example.json")
        target.update(workspace_id=WORKSPACE, event_name="Retail.Stores.CustomVolume")
        target["resource_names"] = {
            role: "Custom " + role.capitalize() for role in native.RESOURCE_ROLES
        }
        target["teams_recipients"] = ["operations@example.com"]
        return target

    def test_configured_native_identities_references_and_event_contract_are_consistent(self):
        original = native.local_items()
        target = self.target()
        items = native.configured_items(target)
        self.assertEqual(original, native.local_items())
        self.assertEqual(items, native.configured_items(target))
        self.assertNotEqual(original["schema"]["logical_id"], items["schema"]["logical_id"])
        for role, name in target["resource_names"].items():
            self.assertEqual(name, items[role]["name"])
            metadata = json.loads(items[role]["files"][".platform"])
            self.assertEqual(name, metadata["metadata"]["displayName"])
            self.assertEqual(items[role]["logical_id"], metadata["config"]["logicalId"])
        parent = json.loads(items["database"]["files"]["DatabaseProperties.json"])["parentEventhouseItemId"]
        self.assertEqual(items["eventhouse"]["logical_id"], parent)
        native.validate_plan(items)
        deploy.validate_schema(deploy.schema_definition(items), items, target["event_name"])
        definition = deploy.build_activator(target, SCHEMA)
        event_id = f"/workspaces/{WORKSPACE}/eventschemasets/{SCHEMA}/eventtypes/{target['event_name']}"
        deploy.validate_activator(deploy.decode_part(definition, "ReflexEntities.json"), event_id, "individual")
        payload = native.bulk_payload(
            (*native.FOUNDATIONS, "activator"), items, {"activator": definition},
        )
        self.assertNotIn("${", json.dumps(payload))
        self.assertIn("/Custom Notebook.Notebook/", str([part["path"] for part in payload["definitionParts"]]))
        other = native.configured_items({**target, "resource_prefix": "AnotherDeployment"})
        self.assertNotEqual(items["schema"]["logical_id"], other["schema"]["logical_id"])

    def test_invalid_or_unversioned_wizard_inputs_fail_before_deployment(self):
        faults = [
            {"configuration_version": "2.0"}, {"scenario_id": "revenue"},
            {"source_backend": "lakehouse"}, {"event_name": "Retail.Bad-Name"},
            {"event_name": "Retail..Bad"}, {"event_name": "Bad'] | take 1"},
            {"resource_names": {"schema": "OnlySchema"}}, {"unknown_binding": True},
            {"resource_names": {**self.target()["resource_names"], "schema": "../escape"}},
            {"resource_names": {**self.target()["resource_names"], "schema": "CON"}},
            {"resource_names": {**self.target()["resource_names"], "schema": "Custom Database"}},
            {"resource_names": {**self.target()["resource_names"], "schema": "Trailing "}},
        ]
        for fault in faults:
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                native.configured_items({**self.target(), **fault})
        target = self.target()
        target.pop("configuration_version")
        with self.assertRaisesRegex(ValueError, "require configuration_version"):
            native.configured_items(target)

    def test_ledger_rejects_changed_names_event_or_legacy_identity(self):
        target = self.target()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(target, Mock(), path)
            deployment.save()
            deploy.Deployment(target, Mock(), path)
            for changed in (
                {**target, "event_name": "Retail.Other.Event"},
                {**target, "resource_names": {**target["resource_names"], "schema": "Another Schema"}},
            ):
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    deploy.Deployment(changed, Mock(), path)
            deployment.state.pop("scenario_configuration")
            for items in ({}, {"schema": SCHEMA}):
                deployment.state["items"] = items
                deployment.state["pending_import"] = {"location": "saved-legacy-operation"}
                deployment.save()
                with self.assertRaisesRegex(ValueError, "unversioned deployment ledger"):
                    deploy.Deployment(target, Mock(), path)

    def test_configured_notebook_publishes_to_the_reviewed_event_and_rejects_parameter_drift(self):
        target = self.target()
        source = native.configured_items(target)["notebook"]["files"]["notebook-content.py"]
        namespace = {"__name__": "configured_notebook"}
        exec(compile(source, "configured-notebook", "exec"), namespace)
        utilities = types.ModuleType("notebookutils")
        utilities.notebook = Mock()
        utilities.businessEvents = Mock()
        utilities.businessEvents.publish.return_value = True
        function = namespace["run_scenario"]
        parameters = {
            "operation": "publish", "event_name": target["event_name"], "source_run_id": RUN,
            "database_id": SCHEMA, "workspace_id": WORKSPACE, "schema_set_id": SCHEMA,
        }
        with (
            patch.dict(sys.modules, {"notebookutils": utilities}),
            patch.dict(function.__globals__, {
                "query_rows": Mock(return_value=expected_findings(deploy.fixtures(), RUN, WINDOW)),
                "ingest_source_rows": Mock(side_effect=AssertionError("Must not seed")),
            }),
            patch("sys.version_info", (3, 12, 0)),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            function(parameters, [])
            self.assertEqual(target["event_name"], utilities.businessEvents.publish.call_args.kwargs["eventTypeName"])
            with self.assertRaisesRegex(ValueError, "event name differs"):
                function({**parameters, "event_name": native.EVENT_NAME}, [])

    def test_connection_and_verification_use_the_custom_event_identity(self):
        target = self.target()
        items = {"schema": SCHEMA, "database": RUN, "eventhouse": WORKSPACE}
        api = Mock(environment="msit", metadata_host=MSIT)
        api.request.side_effect = [
            Reply(200, {}, [
                {"objectId": SCHEMA, "artifactType": "EventDefinition", "capacityObjectId": WORKSPACE},
                {"objectId": RUN, "artifactType": "KustoDatabase", "capacityObjectId": WORKSPACE,
                 "parentArtifactObjectId": WORKSPACE},
            ]),
            Reply(200, {}, {"Token": "unit-test-workload", "TargetUriHost": "unit.pbidedicated.windows.net",
                           "CapacityObjectId": WORKSPACE}),
        ]
        definition = connections.session(api, WORKSPACE, items, target["event_name"])[3]
        self.assertEqual(target["event_name"], definition["BusinessEventName"])
        self.assertTrue(definition["BusinessEventTypes"][0].endswith("/" + target["event_name"]))
        with tempfile.TemporaryDirectory() as directory:
            api = Mock()
            api.request.side_effect = [
                Reply(200, {}, {"id": RUN, "properties": {"queryServiceUri": "https://unit.kusto.fabric.microsoft.com"}}),
                Reply(200, {}, {"Tables": [{"Columns": [{"ColumnName": "store_id"}], "Rows": []}]}),
            ]
            deployment = deploy.Deployment(target, api, Path(directory) / "state.json")
            deployment.state.update(items=items, last_run={"run_id": RUN, "window_start": WINDOW})
            with self.assertRaisesRegex(ValueError, "missing findings"):
                deployment.verify()
            query = api.request.call_args.args[2]["csl"]
            self.assertIn("['" + target["event_name"] + "']", query)
            self.assertNotIn("['" + native.EVENT_NAME + "']", query)


class ScenarioTests(unittest.TestCase):
    def test_failed_transaction_seed_cannot_publish_an_incomplete_baseline_batch(self):
        source = runpy.run_path(str(
            deploy.ROOT / "items" / "SalesVolumeCicdV1Publisher.Notebook" / "notebook-content.py"
        ))
        utilities = types.ModuleType("notebookutils")
        utilities.businessEvents = Mock()
        function = source["run_scenario"]
        ingest = Mock(side_effect=RuntimeError("Source ingestion rejected"))
        with (
            patch.dict(sys.modules, {"notebookutils": utilities}),
            patch.dict(function.__globals__, {"ingest_source_rows": ingest}),
            patch("sys.version_info", (3, 12, 0)),
            self.assertRaisesRegex(RuntimeError, "Source ingestion rejected"),
        ):
            function({"operation": "seed", "database_id": SCHEMA}, deploy.fixtures())
        self.assertEqual(["cicd_pos_transactions"], [call.args[1] for call in ingest.call_args_list])
        utilities.businessEvents.publish.assert_not_called()

    def test_existing_source_publication_does_not_require_or_create_dummy_data(self):
        source = runpy.run_path(str(
            deploy.ROOT / "items" / "SalesVolumeCicdV1Publisher.Notebook" / "notebook-content.py"
        ))
        expected = expected_findings(deploy.fixtures(), RUN, WINDOW)
        utilities = types.ModuleType("notebookutils")
        utilities.notebook = Mock()
        utilities.businessEvents = Mock()
        utilities.businessEvents.publish.return_value = True
        function = source["run_scenario"]
        with (
            patch.dict(sys.modules, {"notebookutils": utilities}),
            patch.dict(function.__globals__, {
                "ingest_source_rows": Mock(side_effect=AssertionError("Publication must not seed")),
                "query_rows": Mock(return_value=expected),
            }),
            patch("sys.version_info", (3, 12, 0)),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            function({
                "operation": "publish", "source_run_id": RUN, "database_id": SCHEMA,
                "workspace_id": WORKSPACE, "schema_set_id": SCHEMA,
            }, [])
        self.assertEqual(expected, utilities.businessEvents.publish.call_args.kwargs["eventData"])
        self.assertEqual(5, json.loads(utilities.notebook.exit.call_args.args[0])["published_count"])

    def test_dummy_ingestion_cannot_write_event_history_and_batches_source_rows(self):
        source = runpy.run_path(str(
            deploy.ROOT / "items" / "SalesVolumeCicdV1Publisher.Notebook" / "notebook-content.py"
        ))
        function = source["ingest_source_rows"]
        request = Mock()
        with patch.dict(function.__globals__, {"kusto_request": request}):
            with self.assertRaisesRegex(ValueError, "not event history"):
                function({}, "Retail.Sales.VolumeAlert", [])
            request.assert_not_called()
            function({}, "cicd_store_baselines", ([index] for index in range(501)))
        self.assertEqual(2, request.call_count)
        self.assertTrue(all(call.kwargs == {"management": True} for call in request.call_args_list))

    def test_seed_only_native_notebook_returns_before_business_event_publication(self):
        source = runpy.run_path(str(
            deploy.ROOT / "items" / "SalesVolumeCicdV1Publisher.Notebook" / "notebook-content.py"
        ))
        stored = {}

        def ingest(configuration, table, rows):
            stored[table] = list(rows)

        def query(configuration, statement):
            counts = Counter(row[1] for row in stored["cicd_pos_transactions"])
            return [{
                "store_id": row[1], "expected_transactions": row[2],
                "actual_transactions": counts[row[1]], "window_start": row[3],
            } for row in stored["cicd_store_baselines"]]

        utilities = types.ModuleType("notebookutils")
        utilities.notebook = Mock()
        utilities.businessEvents = Mock()
        utilities.businessEvents.publish.side_effect = AssertionError("A seed must not publish")
        function = source["run_scenario"]
        with (
            patch.dict(sys.modules, {"notebookutils": utilities}),
            patch.dict(function.__globals__, {"ingest_source_rows": ingest, "query_rows": query}),
            patch("sys.version_info", (3, 12, 0)),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            function({"operation": "seed", "database_id": SCHEMA}, deploy.fixtures())
        utilities.businessEvents.publish.assert_not_called()
        summary = json.loads(utilities.notebook.exit.call_args.args[0])
        self.assertEqual(0, summary["published_count"])
        self.assertEqual(5, summary["source_store_count"])
        self.assertEqual(3868, summary["source_transaction_count"])
        self.assertEqual(5, len(stored["cicd_store_baselines"]))
        self.assertEqual(3868, len(stored["cicd_pos_transactions"]))

    def test_strict_thresholds_and_zero_sales(self):
        for actual, deviation, severity in [
            (743, -40.56, "high"), (1000, -20.0, "medium"),
            (875, -30.0, "medium"), (1063, -14.96, "low"),
            (1250, 0.0, "low"), (0, -100.0, "high"),
        ]:
            with self.subTest(actual=actual):
                result = finding("store", 1250, actual, WINDOW)
                self.assertEqual((deviation, severity), (result["deviation_pct"], result["severity"]))

    def test_invalid_baselines_counts_and_time_fail(self):
        for expected, actual in [(0, 1), (-1, 0), (True, 1), (10, -1), (10, 1.5), (10, 2**31)]:
            with self.subTest(expected=expected, actual=actual), self.assertRaises(ValueError):
                finding("store", expected, actual, WINDOW)
        with self.assertRaisesRegex(ValueError, "UTC"):
            finding("store", 10, 5, "2026-11-05T13:00:00-08:00")

    def test_all_findings_published_contract_includes_nonmatching_cases(self):
        payloads = expected_findings(deploy.fixtures(), RUN, WINDOW)
        self.assertEqual(5, len(payloads))
        self.assertEqual(2, sum(row["deviation_pct"] < -30 for row in payloads))
        self.assertTrue(any(row["deviation_pct"] == -30 for row in payloads))
        self.assertTrue(any(row["deviation_pct"] == 0 for row in payloads))
        self.assertTrue(all(row["store_id"].startswith(f"cicd-{RUN}-") for row in payloads))
        with self.assertRaises(ValueError):
            expected_findings(deploy.fixtures() * 2, RUN, WINDOW)

    def test_definition_retains_business_event_category_and_canonical_contract(self):
        deploy.validate_schema(deploy.schema_definition())
        definition = deploy.decode_part(deploy.schema_definition(), "EventSchemaSetDefinition.json")
        event = definition["eventTypes"][0]
        self.assertEqual("BusinessEventType", event["category"])
        self.assertEqual("Retail.Sales.VolumeAlert", event["id"])
        schema = json.loads(definition["schemas"][0]["versions"][0]["schema"])
        payload = finding("store", 1250, 743, WINDOW)
        self.assertEqual(set(payload), {field["name"] for field in schema["fields"]})
        canonical = (
            deploy.ROOT.parents[1] / "docs" / "business-events" / "scenarios" / "sales-volume-alert.md"
        ).read_text(encoding="utf-8")
        canonical_schema = json.loads(canonical.split("```json", 1)[1].split("```", 1)[0])
        self.assertEqual(
            {field["name"]: field["type"] for field in canonical_schema["fields"]},
            {field["name"]: field["type"] for field in schema["fields"]},
        )

    def test_schema_readback_allows_serialization_changes_but_not_category_changes(self):
        body = deploy.decode_part(deploy.schema_definition(), "EventSchemaSetDefinition.json")
        version = body["schemas"][0]["versions"][0]
        version["schema"] = json.dumps(json.loads(version["schema"]), indent=4, sort_keys=True)
        version["description"] = ""
        deploy.validate_schema({"parts": [deploy.definition_part("EventSchemaSetDefinition.json", body)]})
        body["eventTypes"][0]["category"] = "GenericEventType"
        with self.assertRaisesRegex(ValueError, "category"):
            deploy.validate_schema({"parts": [deploy.definition_part("EventSchemaSetDefinition.json", body)]})

    def test_native_notebook_owns_business_logic_and_accepts_runtime_parameters(self):
        source = native.local_items()["notebook"]["files"]["notebook-content.py"].decode()
        compile(source, "native-notebook", "exec")
        self.assertIn("# PARAMETERS CELL ********************", source)
        self.assertIn('config_json = "{}"', source)
        self.assertIn('fixtures_json = "[]"', source)
        self.assertIn("def finding(", source)
        self.assertIn("count_distinct(transaction_id)", source)
        self.assertNotIn("pyspark.sql", source)
        self.assertIn("notebookutils.businessEvents.publish(", source)
        self.assertNotIn(WORKSPACE, source)
        self.assertNotIn("accessToken", source)

    def test_offline_plan_does_not_acquire_credentials(self):
        with patch("fabric_api.CliTokens.get", side_effect=AssertionError("Must remain offline")):
            result = deploy.plan(deploy.load_config(deploy.ROOT / "config.example.json"))
        self.assertIsNone(result["workspace_id"])
        self.assertFalse(result["cloud_writes"])
        self.assertEqual("Fabric Bulk Import Item Definitions", result["deployment_engine"])
        self.assertFalse(result["repository_integration"])


class ActivatorTests(unittest.TestCase):
    def test_native_filter_is_validated_instead_of_silently_rebuilt_by_python(self):
        for fault in ("threshold", "inclusive", "field", "unconditional", "upstream"):
            with self.subTest(fault=fault):
                entities = deploy.decode_part(native.definition("activator"), "ReflexEntities.json")
                rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
                instance = json.loads(rule["payload"]["definition"]["instance"])
                detection = next(step for step in instance["steps"] if step["name"] == "EventDetectStep")
                if fault == "threshold":
                    detection["rows"][1]["arguments"][1]["value"] = -29
                elif fault == "inclusive":
                    detection["rows"][1]["arguments"][0]["value"] = "IsLessThanOrEqualTo"
                elif fault == "field":
                    detection["rows"][0]["arguments"][0]["value"] = "actual_transactions"
                elif fault == "unconditional":
                    detection["rows"] = [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}]
                else:
                    source = next(entity for entity in entities if entity["type"] == "realTimeHubSource-v1")
                    source["payload"]["filterSettings"]["filters"] = [
                        {"operatorType": "NumberLessThan", "key": "data.deviation_pct", "values": ["-30"]}
                    ]
                rule["payload"]["definition"]["instance"] = json.dumps(instance)
                definition = {"parts": [deploy.definition_part("ReflexEntities.json", entities)]}
                with (
                    patch("native.definition", return_value=definition),
                    self.assertRaisesRegex(ValueError, "upstream" if fault == "upstream" else "strict"),
                ):
                    deploy.build_activator(individual_config(), SCHEMA)

    def test_default_channel_requires_native_template_without_dm_substitution(self):
        with self.assertRaisesRegex(ValueError, "channel post"):
            deploy.build_activator(config(), SCHEMA)

    def test_explicit_individual_action_is_connected_and_strictly_filtered(self):
        definition = deploy.build_activator(individual_config(), SCHEMA)
        entities = deploy.decode_part(definition, "ReflexEntities.json")
        source = next(entity for entity in entities if entity["type"] == "realTimeHubSource-v1")
        event_id = source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0]
        self.assertIn(WORKSPACE, event_id)
        self.assertIn(SCHEMA, event_id)
        deploy.validate_activator(entities, event_id)
        rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
        instance = json.loads(rule["payload"]["definition"]["instance"])
        action = next(step for step in instance["steps"] if step["name"] == "ActStep")["rows"][0]
        self.assertEqual("TeamsMessage", action["kind"])

    def test_disabled_unapplied_rule_or_dangling_event_reference_is_rejected(self):
        original = deploy.decode_part(
            deploy.build_activator(individual_config(), SCHEMA), "ReflexEntities.json",
        )
        source = next(entity for entity in original if entity["type"] == "realTimeHubSource-v1")
        event_id = source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0]
        for fault in ("disabled", "unapplied", "dangling"):
            with self.subTest(fault=fault):
                entities = copy.deepcopy(original)
                rule = next(entity for entity in entities if entity["payload"].get("definition", {}).get("type") == "Rule")
                definition = rule["payload"]["definition"]
                if fault == "disabled":
                    definition["settings"]["shouldRun"] = False
                elif fault == "unapplied":
                    definition["settings"]["shouldApplyRuleOnUpdate"] = False
                else:
                    instance = json.loads(definition["instance"])
                    instance["steps"][0]["rows"][0]["arguments"][0]["arguments"][0]["value"] = RUN
                    definition["instance"] = json.dumps(instance)
                with self.assertRaises(ValueError):
                    deploy.validate_activator(entities, event_id)

    def test_channel_template_preserves_action_and_rebinds_source(self):
        entities = deploy.decode_part(
            deploy.build_activator(individual_config(), SCHEMA), "ReflexEntities.json",
        )
        # This opaque test fixture is not a proposed Fabric channel-binding API contract.
        instance = json.loads(entities[-1]["payload"]["definition"]["instance"])
        next(step for step in instance["steps"] if step["name"] == "ActStep")["rows"] = [{
            "name": "OpaqueChannelFixture", "kind": "OpaqueTeamsTestAction",
            "arguments": [{"name": "testDestination", "type": "string", "value": "test-channel-only"}],
        }]
        entities[-1]["payload"]["definition"]["instance"] = json.dumps(instance)
        original_rule = copy.deepcopy(entities[-1])
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "ReflexEntities.json"
            template.write_text(json.dumps(entities), encoding="utf-8")
            target = config()
            target["teams_definition_file"] = str(template)
            new_schema = "44444444-4444-4444-8444-444444444444"
            rebound = deploy.decode_part(deploy.build_activator(target, new_schema), "ReflexEntities.json")
        self.assertEqual(original_rule, rebound[-1])
        source = next(entity for entity in rebound if entity["type"] == "realTimeHubSource-v1")
        self.assertIn(new_schema, source["payload"]["connection"]["eventTypeFullyQualifiedIds"][0])

    def test_individual_message_export_cannot_be_used_as_channel_template(self):
        entities = deploy.decode_part(
            deploy.build_activator(individual_config(), SCHEMA), "ReflexEntities.json",
        )
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "ReflexEntities.json"
            template.write_text(json.dumps(entities), encoding="utf-8")
            target = config()
            target["teams_definition_file"] = str(template)
            with self.assertRaisesRegex(ValueError, "individual-recipient"):
                deploy.build_activator(target, SCHEMA)


class RoleTests(unittest.TestCase):
    def test_role_merge_preserves_unrelated_roles_and_existing_members(self):
        member = {"tenantId": WORKSPACE, "objectId": RUN, "objectType": "User"}
        existing = [{"name": "Unrelated", "members": {"microsoftEntraMembers": []}, "decisionRules": []}]
        before = copy.deepcopy(existing)
        first = deploy.merge_roles(existing, member)
        self.assertEqual(before, existing)
        self.assertEqual(before[0], first[0])
        second = deploy.merge_roles(first, member)
        self.assertEqual(first, second)
        self.assertEqual(2, len(second))
        first[-1]["members"]["microsoftEntraMembers"][0].pop("objectType")
        merged = deploy.merge_roles(first, member)
        self.assertEqual(1, len(merged[-1]["members"]["microsoftEntraMembers"]))

    def test_role_readback_compares_permissions_and_identity_not_server_metadata(self):
        member = {"tenantId": WORKSPACE, "objectId": RUN, "objectType": "User"}
        expected = deploy.merge_roles([], member)[0]
        actual = copy.deepcopy(expected)
        actual.update({"id": SCHEMA, "etag": "new-version", "kind": "Policy"})
        actual["members"]["microsoftEntraMembers"][0].pop("objectType")
        self.assertEqual(deploy.role_policy(expected), deploy.role_policy(actual))
        actual["decisionRules"][0]["permission"][1]["attributeValueIncludedIn"] = ["Consume"]
        self.assertNotEqual(deploy.role_policy(expected), deploy.role_policy(actual))
        actual = copy.deepcopy(expected)
        actual["members"]["microsoftEntraMembers"] = []
        self.assertNotEqual(deploy.role_policy(expected), deploy.role_policy(actual))


class Response(io.BytesIO):
    def __init__(self, status, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.status = status
        self.headers = headers or {}


class Tokens:
    def get(self, resource):
        return "unit-test-placeholder"


class Opener:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class ClientTests(unittest.TestCase):
    def test_workload_route_uses_native_target_host_and_scoped_authorization_not_bearer(self):
        tokens = Mock()
        opener = Opener(Response(200, []))
        client = FabricApi(tokens, opener, environment="daily")
        host = "pbipdailyeus2euap-daily.pbidedicated.windows.net"
        client.kusto_connection_request("GET", WORKSPACE, SCHEMA, "unit-test-workload", target_host=host)
        self.assertEqual(
            f"https://{host}/webapi/capacities/{WORKSPACE}/workloads/Kusto/KustoService/direct"
            f"/v1/databases/{SCHEMA}/businessEventDataConnections",
            opener.calls[0].full_url,
        )
        self.assertEqual("MwcToken unit-test-workload", opener.calls[0].get_header("Authorization"))
        tokens.get.assert_not_called()
        for unsafe in (
            None, "attacker.example", "https://api.fabric.microsoft.com.attacker.example",
            "http://host.pbidedicated.windows.net", "https://user@host.pbidedicated.windows.net",
            "https://host.pbidedicated.windows.net:444", "https://host.pbidedicated.windows.net/path",
            "https://host.pbidedicated.windows.net?query=1",
        ):
            with self.subTest(host=unsafe), self.assertRaises(ValueError):
                client.kusto_connection_request("GET", WORKSPACE, SCHEMA, "unit-test-workload", target_host=unsafe)
        self.assertEqual(1, len(opener.calls))

    def test_native_batch_name_rejection_is_not_silently_retried(self):
        waits = []
        opener = Opener(Response(409, {"errorCode": "ItemDisplayNameNotAvailableYet"}))
        client = FabricApi(Tokens(), opener, waits.append)
        with self.assertRaisesRegex(RuntimeError, "ItemDisplayNameNotAvailableYet"):
            client.request(
                "POST", f"workspaces/{WORKSPACE}/items/bulkImportDefinitions?beta=true",
                native.bulk_payload(native.FOUNDATIONS),
            )
        self.assertEqual(1, len(opener.calls))
        self.assertEqual([], waits)

    def test_only_temporary_name_unavailability_is_retried_and_retry_is_bounded(self):
        body = {"displayName": "SalesVolumeCicdSource", "type": "Lakehouse"}
        opener = Opener(
            Response(409, {"errorCode": "ItemDisplayNameNotAvailableYet"}),
            Response(201, {"id": SCHEMA}),
        )
        waits = []
        client = FabricApi(Tokens(), opener, waits.append)
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(SCHEMA, client.request("POST", "workspaces/" + WORKSPACE + "/items", body).body["id"])
        self.assertEqual([30], waits)
        self.assertEqual(opener.calls[0].data, opener.calls[1].data)
        for code, count in (("ItemDisplayNameNotAvailableYet", 2), ("ItemDisplayNameAlreadyInUse", 1)):
            with self.subTest(code=code):
                opener = Opener(*(Response(409, {"errorCode": code}) for _ in range(count)))
                client = FabricApi(Tokens(), opener, sleep=lambda _: None)
                with patch("sys.stderr", new_callable=io.StringIO), self.assertRaisesRegex(RuntimeError, code):
                    client.request("POST", "workspaces/" + WORKSPACE + "/items", body)
                self.assertEqual(count, len(opener.calls))

    def test_daily_routes_control_requests_and_tokens_without_production_fallback(self):
        tokens = Mock()
        tokens.get.return_value = "unit-test-placeholder"
        opener = Opener(Response(200, {"value": []}))
        client = FabricApi(tokens, opener, environment="daily")
        self.assertEqual([], client.list_all("workspaces"))
        self.assertEqual(DAILY_FABRIC + "/v1/workspaces", opener.calls[0].full_url)
        tokens.get.assert_called_once_with(POWERBI)
        with self.assertRaisesRegex(ValueError, "unexpected service"):
            client.request("GET", FABRIC + "/v1/workspaces")
        with self.assertRaisesRegex(ValueError, "unexpected service"):
            client.complete(Reply(202, {"Location": FABRIC + "/v1/operations/" + RUN}, {}))
        self.assertEqual(1, len(opener.calls))

    def test_daily_gateway_can_poll_its_authenticated_native_cluster_operation(self):
        location = DAILY + "/v1/operations/" + RUN
        opener = Opener(Response(200, {"status": "Succeeded"}), Response(200, {"id": SCHEMA}))
        client = FabricApi(Tokens(), opener, sleep=lambda _: None, environment="daily")
        self.assertEqual(SCHEMA, client.complete(Reply(202, {"Location": location}, {}))["id"])
        self.assertEqual(location + "/result", opener.calls[-1].full_url)
        self.assertEqual(DAILY, client.metadata_host)

    def test_msit_routes_control_metadata_and_storage_without_cross_environment_fallback(self):
        tokens = Mock()
        tokens.get.return_value = "unit-test-placeholder"
        opener = Opener(Response(200, {"value": []}), Response(200, {}), Response(204, {}))
        client = FabricApi(tokens, opener, environment="msit")
        self.assertEqual([], client.list_all("workspaces"))
        self.assertEqual(MSIT_FABRIC + "/v1/workspaces", opener.calls[0].full_url)
        client.request("GET", MSIT + "/metadata/workspaces/" + WORKSPACE + "/artifacts")
        client.request("POST", "https://msit-onelake.pbidedicated.windows.net/security/enable", {}, resource=STORAGE)
        self.assertEqual([POWERBI, POWERBI, STORAGE], [call.args[0] for call in tokens.get.call_args_list])
        for url in (FABRIC + "/v1/workspaces", DAILY_FABRIC + "/v1/workspaces", DAILY + "/v1/operations/" + RUN):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "unexpected service"):
                client.request("GET", url)
        with self.assertRaisesRegex(ValueError, "unexpected service"):
            client.request("POST", "https://daily-onelake.pbidedicated.windows.net/security/enable", {}, resource=STORAGE)
        daily = FabricApi(environment="daily")
        with self.assertRaisesRegex(ValueError, "unexpected service"):
            daily.check_url(MSIT + "/v1/operations/" + RUN, POWERBI)
        self.assertEqual(3, len(opener.calls))

    def test_msit_native_operation_uses_its_own_cluster_and_ledger_namespace(self):
        location = MSIT + "/v1/operations/" + RUN
        opener = Opener(Response(200, {"status": "Succeeded"}), Response(200, {"id": SCHEMA}))
        client = FabricApi(Tokens(), opener, sleep=lambda _: None, environment="msit")
        self.assertEqual(SCHEMA, client.complete(Reply(202, {"Location": location}, {}))["id"])
        target = individual_config()
        target["environment"] = "msit"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({
                "environment": "daily", "workspace_id": WORKSPACE,
                "resource_prefix": target["resource_prefix"], "items": {},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another Fabric environment"):
                deploy.Deployment(target, client, path)

    def test_credentials_cannot_follow_foreign_continuation_or_location(self):
        client = FabricApi(Tokens())
        for url in [
            "https://attacker.example/v1/result",
            "https://api.fabric.microsoft.com.attacker.example/v1/result",
            "https://user:pass@api.fabric.microsoft.com/v1/result",
            "https://api.fabric.microsoft.com:444/v1/result",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                client.request("GET", url)

    def test_long_running_creation_polls_then_fetches_actual_result(self):
        location = FABRIC + "/v1/operations/" + RUN
        opener = Opener(Response(200, {"status": "Running"}), Response(200, {"status": "Succeeded"}), Response(200, {"id": SCHEMA}))
        client = FabricApi(Tokens(), opener, sleep=lambda _: None)
        result = client.complete(Reply(202, {"Location": location, "Retry-After": "0"}, {}))
        self.assertEqual(SCHEMA, result["id"])
        self.assertEqual(location + "/result", opener.calls[-1].full_url)

    def test_failed_operation_does_not_get_a_success_shaped_result(self):
        location = FABRIC + "/v1/operations/" + RUN
        opener = Opener(Response(200, {"status": "Failed"}))
        client = FabricApi(Tokens(), opener, sleep=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "Failed"):
            client.complete(Reply(202, {"Location": location}, {}))
        self.assertEqual(1, len(opener.calls))

    def test_native_operation_failure_exposes_codes_without_dumping_messages_or_payloads(self):
        location = FABRIC + "/v1/operations/" + RUN
        opener = Opener(Response(200, {
            "status": "Failed", "error": {
                "errorCode": "InvalidItemDefinition", "message": "PRIVATE PAYLOAD",
                "moreDetails": [{"errorCode": "MissingRequiredPart", "message": "PRIVATE DESTINATION"}],
            },
        }))
        client = FabricApi(Tokens(), opener, sleep=lambda _: None)
        with self.assertRaises(RuntimeError) as error:
            client.complete(Reply(202, {"Location": location}, {}))
        self.assertIn("InvalidItemDefinition", str(error.exception))
        self.assertIn("MissingRequiredPart", str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(1, len(opener.calls))

    def test_429_waits_and_retries_once_but_other_failures_do_not(self):
        waits = []
        opener = Opener(Response(429, {"errorCode": "TooManyRequests"}, {"Retry-After": "7"}), Response(200, {"value": []}))
        client = FabricApi(Tokens(), opener, waits.append)
        self.assertEqual([], client.list_all("workspaces"))
        self.assertEqual([7], waits)
        opener = Opener(Response(403, {"errorCode": "Forbidden"}))
        client = FabricApi(Tokens(), opener)
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            client.request("GET", "workspaces")
        self.assertEqual(1, len(opener.calls))

    def test_lists_all_pages(self):
        second = FABRIC + "/v1/workspaces?page=2"
        opener = Opener(
            Response(200, {"value": [{"id": "one"}], "continuationUri": second}),
            Response(200, {"value": [{"id": "two"}]}),
        )
        client = FabricApi(Tokens(), opener)
        self.assertEqual([{"id": "one"}, {"id": "two"}], client.list_all("workspaces"))


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.items = {"schema": SCHEMA, "database": RUN, "eventhouse": WORKSPACE}
        self.definition = native.bind(json.loads(
            (deploy.ROOT / "connections" / "business-event.json").read_text(encoding="utf-8"),
        ), {
            "${workspace_id}": WORKSPACE, "${schema_id}": SCHEMA,
            "${event_id}": f"/workspaces/{WORKSPACE}/eventschemasets/{SCHEMA}/eventtypes/{connections.EVENT_NAME}",
        })
        self.host = "pbipdailyeus2euap-daily.pbidedicated.windows.net"
        self.session = (WORKSPACE, self.host, "unit-test-workload", self.definition)

    def test_session_requires_verified_database_parent_and_routed_capacity(self):
        for fault in (None, "parent", "capacity"):
            with self.subTest(fault=fault):
                api = Mock(environment="daily", metadata_host=DAILY)
                artifacts = [
                    {"objectId": SCHEMA, "artifactType": "EventDefinition", "capacityObjectId": WORKSPACE},
                    {
                        "objectId": RUN, "artifactType": "KustoDatabase", "capacityObjectId": WORKSPACE,
                        "parentArtifactObjectId": SCHEMA if fault == "parent" else WORKSPACE,
                    },
                ]
                api.request.side_effect = [
                    Reply(200, {}, artifacts),
                    Reply(200, {}, {
                        "Token": "unit-test-workload", "TargetUriHost": self.host,
                        "CapacityObjectId": SCHEMA if fault == "capacity" else WORKSPACE,
                    }),
                ]
                if fault:
                    with self.assertRaises(ValueError):
                        connections.session(api, WORKSPACE, self.items)
                else:
                    self.assertEqual(self.session, connections.session(api, WORKSPACE, self.items))
                    request = api.request.call_args.args[2]
                    self.assertEqual(RUN, request["artifacts"][0]["artifactObjectId"])
                    self.assertEqual(WORKSPACE, request["workspaceObjectId"])

    def test_connection_readback_rejects_foreign_filtered_or_ambiguous_destinations(self):
        for fault in (None, "lowercase", "schema", "filter", "duplicate", "format", "summary"):
            with self.subTest(fault=fault):
                actual = copy.deepcopy(self.definition)
                if fault == "schema":
                    actual["EventSchemaSetArtifactId"] = RUN
                elif fault == "filter":
                    actual["AdvancedFilters"] = [{"value": -30}]
                elif fault == "format":
                    actual["DataFormat"] = "CSV"
                elif fault == "lowercase":
                    actual["DataFormat"] = "json"
                elif fault == "summary":
                    actual = {"businessEventName": connections.EVENT_NAME, "dataFormat": "json", "healthStatus": 1}
                records = [actual] * (2 if fault == "duplicate" else 1)
                api = Mock()
                api.kusto_connection_request.return_value = Reply(200, {}, records)
                with patch("connections.session", return_value=self.session):
                    if fault not in (None, "lowercase"):
                        with self.assertRaises(ValueError):
                            connections.ensure(api, WORKSPACE, self.items, create=True)
                    else:
                        result = connections.ensure(api, WORKSPACE, self.items, create=True)
                        self.assertEqual(SCHEMA, result["schema_id"])
                self.assertEqual(["GET"], [call.args[0] for call in api.kusto_connection_request.call_args_list])

    def test_pending_connection_does_not_resubmit_an_ambiguous_creation(self):
        api = Mock()
        api.kusto_connection_request.return_value = Reply(200, {}, [])
        with (
            patch("connections.session", return_value=self.session),
            patch("connections.time.monotonic", side_effect=[0, 121]),
            self.assertRaises(TimeoutError),
        ):
            connections.ensure(api, WORKSPACE, self.items, create=True, pending=True)
        self.assertEqual(["GET"], [call.args[0] for call in api.kusto_connection_request.call_args_list])

    def test_native_summary_requires_managed_creation_and_preserves_delivery_verification(self):
        summary = {
            "businessEventName": connections.EVENT_NAME, "dataFormat": "json",
            "dataConnectionName": "BusinessEvent-unit", "healthStatus": 1,
        }
        api = Mock()
        api.kusto_connection_request.return_value = Reply(200, {}, [summary])
        with patch("connections.session", return_value=self.session):
            with self.assertRaisesRegex(ValueError, "ownership"):
                connections.ensure(api, WORKSPACE, self.items)
            result = connections.ensure(api, WORKSPACE, self.items, pending=True)
            self.assertEqual("managed_request_and_healthy_summary", result["binding_readback"])
            self.assertEqual("native_association_verified_delivery_unverified", result["status"])
            self.assertEqual(result, connections.ensure(api, WORKSPACE, self.items, known=result))
            for change in ({"schema_id": RUN}, {"data_connection_name": "foreign"}):
                with self.assertRaisesRegex(ValueError, "ownership"):
                    connections.ensure(api, WORKSPACE, self.items, known={**result, **change})
            del summary["dataFormat"]
            with self.assertRaisesRegex(ValueError, "DataFormat"):
                connections.ensure(api, WORKSPACE, self.items, known=result)
            summary["dataFormat"] = "json"
            summary["healthStatus"] = 0
            with self.assertRaisesRegex(ValueError, "not healthy"):
                connections.ensure(api, WORKSPACE, self.items, pending=True)
        self.assertTrue(all(call.args[0] == "GET" for call in api.kusto_connection_request.call_args_list))

    def test_new_connection_is_created_once_then_verified_from_native_readback(self):
        api = Mock()
        api.kusto_connection_request.side_effect = [
            Reply(200, {}, []), Reply(202, {}, {}),
            Reply(200, {}, [{key[0].lower() + key[1:]: value for key, value in self.definition.items()}]),
        ]
        markers = []
        with patch("connections.session", return_value=self.session):
            result = connections.ensure(api, WORKSPACE, self.items, create=True, on_pending=markers.append)
        self.assertEqual(["GET", "POST", "GET"], [
            call.args[0] for call in api.kusto_connection_request.call_args_list
        ])
        self.assertEqual([True], markers)
        self.assertEqual(SCHEMA, result["schema_id"])
        self.assertEqual(self.definition, api.kusto_connection_request.call_args_list[1].args[4])

    def test_creation_is_saved_before_send_and_only_definite_rejections_clear_it(self):
        for failure, expected_markers in (
            (OSError("Connection lost"), [True]),
            (FabricRequestError("POST", "/test", 429, ["CapacityLimitExceeded"]), [True, False]),
        ):
            with self.subTest(failure=type(failure).__name__):
                markers = []
                api = Mock()

                def request(method, *args, **kwargs):
                    if method == "GET":
                        return Reply(200, {}, [])
                    self.assertEqual([True], markers)
                    raise failure

                api.kusto_connection_request.side_effect = request
                with patch("connections.session", return_value=self.session), self.assertRaises(type(failure)):
                    connections.ensure(api, WORKSPACE, self.items, create=True, on_pending=markers.append)
                self.assertEqual(expected_markers, markers)


class DeploymentTests(unittest.TestCase):
    def test_capacity_assignment_verifies_native_routing_and_saves_before_sending(self):
        destination = str(uuid.uuid5(uuid.NAMESPACE_URL, "capacity-case-normalization"))
        target = individual_config()
        target["environment"] = "daily"
        api = Mock(metadata_host=DAILY)
        api.list_all.return_value = [{
            "id": destination, "sku": "FT1", "state": "Active", "region": "Test region",
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(target, api, path)
            deployment.state["items"] = {"schema": SCHEMA}

            def request(method, route, body=None):
                if method == "POST":
                    self.assertEqual(destination, json.loads(path.read_text())["pending_capacity_assignment"]["to_capacity_id"])
                    self.assertEqual({"capacityId": destination}, body)
                    return Reply(202, {}, {})
                if route.startswith(DAILY):
                    return Reply(200, {}, [{"objectId": SCHEMA, "capacityObjectId": destination.upper()}])
                return Reply(200, {}, {"capacityId": destination})

            api.request.side_effect = request
            with patch.object(deployment, "preflight", return_value={
                "capacity_id": RUN, "capacity_region": "Test region",
            }):
                result = deployment.assign_capacity(destination)
            self.assertEqual("workspace_capacity_verified_compute_unverified", result["status"])
            self.assertNotIn("pending_capacity_assignment", deployment.state)
            self.assertEqual(["POST", "GET", "GET"], [call.args[0] for call in api.request.call_args_list])

    def test_completed_external_capacity_change_is_reconciled_without_reassigning_it(self):
        api = Mock(metadata_host=DAILY)
        api.request.side_effect = [
            Reply(200, {}, {"capacityId": WORKSPACE, "capacityAssignmentProgress": "Completed"}),
            Reply(200, {}, {"capacityId": WORKSPACE, "capacityAssignmentProgress": "Completed"}),
            Reply(200, {}, [{"objectId": SCHEMA, "capacityObjectId": WORKSPACE}]),
        ]
        target = individual_config()
        target["environment"] = "daily"
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(target, api, Path(directory) / "state.json")
            deployment.state.update(
                items={"schema": SCHEMA},
                pending_capacity_assignment={"from_capacity_id": SCHEMA, "to_capacity_id": RUN},
            )
            result = deployment.assign_capacity(WORKSPACE)
            self.assertEqual("external", result["assignment_origin"])
            self.assertEqual(RUN, result["superseded_capacity_id"])
            self.assertNotIn("pending_capacity_assignment", deployment.state)
            self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))

    def test_capacity_assignment_refuses_inactive_nonfabric_or_cross_region_targets(self):
        for fault in ("inactive", "premium", "region"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                api = Mock()
                api.list_all.return_value = [{
                    "id": WORKSPACE, "sku": "PP3" if fault == "premium" else "FT1",
                    "state": "Inactive" if fault == "inactive" else "Active",
                    "region": "Another region" if fault == "region" else "Test region",
                }]
                deployment = deploy.Deployment(individual_config(), api, Path(directory) / "state.json")
                with patch.object(deployment, "preflight", return_value={
                    "capacity_id": RUN, "capacity_region": "Test region",
                }), self.assertRaises(ValueError):
                    deployment.assign_capacity(WORKSPACE)
                api.request.assert_not_called()

    def test_ambiguous_capacity_request_resumes_readback_without_resubmitting(self):
        api = Mock()
        api.list_all.return_value = [{
            "id": WORKSPACE, "sku": "FT1", "state": "Active", "region": "Test region",
        }]
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(individual_config(), api, Path(directory) / "state.json")
            api.request.side_effect = OSError("Connection lost after request")
            with patch.object(deployment, "preflight", return_value={
                "capacity_id": RUN, "capacity_region": "Test region",
            }), self.assertRaises(OSError):
                deployment.assign_capacity(WORKSPACE)
            self.assertIn("pending_capacity_assignment", deployment.state)
            api.request.reset_mock(side_effect=True)
            api.request.return_value = Reply(200, {}, {"capacityId": RUN})
            with patch("deploy.time.monotonic", side_effect=[0, 181]), self.assertRaises(TimeoutError):
                deployment.assign_capacity(WORKSPACE)
            self.assertEqual(["GET"], [call.args[0] for call in api.request.call_args_list])
            with self.assertRaisesRegex(ValueError, "Resume assign-capacity"):
                deployment.preflight()

    def test_provision_merges_security_and_preconfigures_native_notebook_defaults(self):
        member = {"tenantId": WORKSPACE, "objectId": RUN, "objectType": "User"}
        existing = [{"name": "Unrelated", "decisionRules": [], "members": {"microsoftEntraMembers": []}}]
        for security_status in (200, 204):
            with self.subTest(security_status=security_status), tempfile.TemporaryDirectory() as directory:
                target = individual_config()
                target["environment"] = "daily"
                api = Mock(control_resource=POWERBI)
                api.tokens.identity.return_value = member
                replies = [
                    Reply(security_status, {}, {}),
                    Reply(200, {"ETag": "version-1"}, {"value": existing}),
                    Reply(200, {}, {}),
                    Reply(200, {}, {"value": deploy.merge_roles(existing, member)}),
                    Reply(200, {}, {"properties": {
                        "parentEventhouseItemId": WORKSPACE,
                        "queryServiceUri": "https://unit.kusto.fabric.microsoft.com",
                    }}),
                ]
                api.request.side_effect = replies
                deployment = deploy.Deployment(target, api, Path(directory) / "state.json")
                deployment.state["items"] = {
                    "schema": SCHEMA, "database": RUN, "eventhouse": WORKSPACE, "notebook": RUN,
                }
                with (
                    patch.object(deployment, "preflight", return_value={"workspace": "Test"}),
                    patch.object(deployment, "import_native") as importer,
                    patch.object(deployment, "get_definition") as definitions,
                ):
                    definitions.side_effect = lambda role, *args: (
                        deploy.schema_definition() if role == "schema"
                        else importer.call_args.kwargs["overrides"]["notebook"]
                    )
                    result = deployment.provision()
                self.assertEqual("native_foundations_deployed_not_end_to_end", result["status"])
                self.assertEqual(native.FOUNDATIONS, importer.call_args_list[0].args[0])
                self.assertEqual(("notebook",), importer.call_args_list[1].args[0])
                bound = importer.call_args.kwargs["overrides"]["notebook"]
                tree = ast.parse(base64.b64decode(bound["parts"][0]["payload"]).decode())
                value = next(
                    node.value for node in tree.body if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "config_json"
                )
                parameters = json.loads(ast.literal_eval(value))
                self.assertEqual({
                    "workspace_id": WORKSPACE, "schema_set_id": SCHEMA, "database_id": RUN,
                    "query_endpoint": "https://unit.kusto.fabric.microsoft.com", "operation": "publish",
                }, parameters)
                role_write = next(call for call in api.request.call_args_list if call.args[0] == "PUT")
                self.assertEqual({"If-Match": "version-1"}, role_write.kwargs["extra_headers"])
                enable = api.request.call_args_list[0]
                self.assertEqual("POST", enable.args[0])
                self.assertIn("daily-onelake.pbidedicated.windows.net", enable.args[1])
                self.assertEqual(STORAGE, enable.kwargs["resource"])

    def test_provision_stops_before_role_changes_when_security_enablement_is_pending(self):
        api = Mock(control_resource=POWERBI)
        api.request.return_value = Reply(202, {}, {})
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(individual_config(), api, Path(directory) / "state.json")
            deployment.state["items"]["schema"] = SCHEMA
            with (
                patch.object(deployment, "preflight"),
                patch.object(deployment, "import_native"),
                patch.object(deployment, "get_definition", return_value=deploy.schema_definition()),
                self.assertRaisesRegex(RuntimeError, "Security enablement has not completed"),
            ):
                deployment.provision()
            api.request.assert_called_once()
            self.assertEqual("POST", api.request.call_args.args[0])
            self.assertTrue(api.request.call_args.args[1].endswith("/security/enable"))

    def test_terminal_runtime_inspection_failure_is_archived_without_allowing_publisher_replay(self):
        for operation in ("inspect", "seed", "publish"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                api = Mock()
                reply = Reply(200, {}, {
                    "status": "Failed",
                    "failureReason": {"errorCode": "CapacityLimitExceeded", "message": "PRIVATE PAYLOAD"},
                })
                api.request.return_value = reply
                location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
                path = Path(directory) / "state.json"
                deployment = deploy.Deployment(individual_config(), api, path)
                deployment.state.update(
                    items={"notebook": SCHEMA}, pending_job=location, pending_job_operation=operation,
                )
                deployment.save()
                with self.assertRaisesRegex(RuntimeError, "CapacityLimitExceeded") as error:
                    deployment.wait_for_run(location, reply)
                self.assertNotIn("PRIVATE", str(error.exception))
                saved = json.loads(path.read_text())
                if operation in ("inspect", "seed"):
                    self.assertNotIn("pending_job", saved)
                    self.assertEqual(["CapacityLimitExceeded"], saved["last_failed_job"]["error_codes"])
                else:
                    self.assertEqual(location, saved["pending_job"])
                    self.assertNotIn("last_failed_job", saved)

    def test_reconciliation_reuses_only_exact_previously_tracked_native_receipts(self):
        for forged in (False, True):
            with self.subTest(forged=forged), tempfile.TemporaryDirectory() as directory:
                details = import_receipts()
                items = native.local_items()
                api = Mock()
                api.list_all.return_value = [{
                    "id": entry["itemId"], "displayName": entry["itemDisplayName"],
                    "type": entry["itemType"], "description": deploy.OWNER,
                } for entry in details]
                api.request.side_effect = AssertionError("Native export should not be needed for proven tracked identities")
                path = Path(directory) / "state.json"
                deployment = deploy.Deployment(config(), api, path)
                deployment.state.update(
                    items={role: entry["itemId"] for role, entry in zip(native.FOUNDATIONS, details)},
                    last_import=details,
                    pending_import={
                        "location": FABRIC + "/v1/operations/" + RUN, "terminal_status": "Failed",
                        "error_codes": ["ItemDefinitionsOperationFailed"],
                        "expected": {
                            role: {key: items[role][key] for key in ("name", "type", "logical_id")}
                            for role in native.FOUNDATIONS
                        },
                    },
                )
                if forged:
                    details[-1]["itemLogicalId"] = WORKSPACE
                deployment.save()
                if forged:
                    with self.assertRaises(AssertionError):
                        deployment.reconcile_import()
                    self.assertIn("pending_import", json.loads(path.read_text()))
                else:
                    result = deployment.reconcile_import()
                    self.assertEqual(4, len(result["recovered_items"]))
                    self.assertEqual(4, len(deployment.state["native_receipts"]))
                    self.assertNotIn("pending_import", deployment.state)
                    api.request.assert_not_called()

    def test_runtime_inspection_does_not_require_kql_configuration_or_submit_dummy_data(self):
        api = Mock(control_resource=FABRIC)
        location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
        api.request.return_value = Reply(202, {"Location": location}, {})
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(individual_config(), api, Path(directory) / "state.json")
            deployment.state["items"] = {"notebook": SCHEMA}
            with (
                patch.object(deployment, "preflight"),
                patch.object(deployment, "wait_for_run"),
                patch("deploy.fixtures", side_effect=AssertionError("Inspection must not read fixtures")),
            ):
                deployment.run(operation="inspect")
            parameters = {
                value["name"]: json.loads(value["value"]) for value in api.request.call_args.args[2]["parameters"]
            }
            self.assertEqual({"operation": "inspect"}, parameters["config_json"])
            self.assertEqual([], parameters["fixtures_json"])

    def test_optional_seed_runs_only_after_successful_provision(self):
        for fails in (False, True):
            with self.subTest(fails=fails), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(individual_config()), encoding="utf-8")
                with patch("deploy.Deployment") as constructor, patch("sys.stdout", new_callable=io.StringIO) as output:
                    deployment = constructor.return_value
                    deployment.provision.return_value = {"status": "native_foundations_deployed_not_end_to_end"}
                    deployment.run.return_value = {"status": "dummy_data_initialized_no_events_published"}
                    arguments = [
                        "provision", "--config", str(path),
                        "--confirm-workspace", WORKSPACE, "--seed-dummy-data",
                    ]
                    if fails:
                        deployment.provision.side_effect = RuntimeError("SQL endpoint provisioning failed")
                        with self.assertRaisesRegex(RuntimeError, "SQL endpoint"):
                            deploy.main(arguments)
                        deployment.run.assert_not_called()
                    else:
                        self.assertEqual(0, deploy.main(arguments))
                        deployment.run.assert_called_once_with(operation="seed")
                        self.assertEqual(
                            "native_foundations_and_dummy_data_ready_consumers_pending",
                            json.loads(output.getvalue())["status"],
                        )

    def test_provision_does_not_seed_data_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(individual_config()), encoding="utf-8")
            with patch("deploy.Deployment") as constructor, patch("sys.stdout", new_callable=io.StringIO):
                constructor.return_value.provision.return_value = {"status": "native_foundations_deployed_not_end_to_end"}
                deploy.main(["provision", "--config", str(path), "--confirm-workspace", WORKSPACE])
                constructor.return_value.run.assert_not_called()

    def test_seed_job_needs_no_consumer_definition_or_eventhouse_assertion(self):
        api = Mock()
        api.control_resource = FABRIC
        location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
        api.request.return_value = Reply(202, {"Location": location}, {})
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(individual_config(), api, Path(directory) / "state.json")
            deployment.state.update(
                items={"schema": SCHEMA, "notebook": RUN, "database": WORKSPACE},
                query_endpoint="https://unit.kusto.fabric.microsoft.com",
            )
            with (
                patch.object(deployment, "preflight"),
                patch.object(deployment, "get_definition") as definitions,
                patch.object(deployment, "wait_for_run"),
            ):
                deployment.run(operation="seed")
            definitions.assert_not_called()
            body = api.request.call_args.args[2]
            parameters = {parameter["name"]: json.loads(parameter["value"]) for parameter in body["parameters"]}
            self.assertEqual("seed", parameters["config_json"]["operation"])
            self.assertEqual(deploy.fixtures(), parameters["fixtures_json"])
            self.assertEqual("seed", deployment.state["pending_job_operation"])

    def test_resumed_seed_validates_source_evidence_and_never_records_a_publication(self):
        for wrong_count in (False, True):
            with self.subTest(wrong_count=wrong_count), tempfile.TemporaryDirectory() as directory:
                summary = {
                    "operation": "seed", "run_id": RUN, "window_start": WINDOW,
                    "published_count": 0, "expected_teams_matches": 2, "source_store_count": 5,
                    "source_transaction_count": sum(row["actual_transactions"] for row in deploy.fixtures()),
                }
                if wrong_count:
                    summary["source_transaction_count"] += 1
                location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
                api = Mock()
                api.request.side_effect = [
                    Reply(200, {}, {"status": "Completed"}),
                    Reply(200, {}, {"status": "Completed"}),
                    Reply(200, {}, {"exitValue": json.dumps(summary)}),
                ]
                path = Path(directory) / "state.json"
                deployment = deploy.Deployment(individual_config(), api, path)
                deployment.state.update(
                    items={"notebook": SCHEMA}, pending_job=location, pending_job_operation="seed",
                )
                deployment.save()
                if wrong_count:
                    with self.assertRaisesRegex(ValueError, "source contract"):
                        deployment.resume()
                    self.assertIn("pending_job", json.loads(path.read_text()))
                else:
                    result = deployment.resume()
                    self.assertEqual("dummy_data_initialized_no_events_published", result["status"])
                    self.assertEqual(summary, json.loads(path.read_text())["last_seed"])
                    self.assertNotIn("pending_job", deployment.state)
                self.assertNotIn("last_run", deployment.state)
                self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))

    def test_failed_import_reconciliation_requires_native_export_identity_not_names(self):
        items = native.local_items()
        expected = {
            role: {key: items[role][key] for key in ("name", "type", "logical_id")}
            for role in native.FOUNDATIONS
        }
        root = "/" + items["schema"]["folder"]
        for fault in (None, "logical_id", "owner", "running", "missing_export", "unrelated_item", "cached_failure"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                platform = json.loads(items["schema"]["files"][".platform"])
                if fault == "logical_id":
                    platform["config"]["logicalId"] = WORKSPACE
                exported = {
                    "itemDefinitionsIndex": [{"id": SCHEMA, "rootPath": root}],
                    "definitionParts": [
                        deploy.definition_part(root + "/.platform", platform),
                        native.part(root + "/EventSchemaSetDefinition.json",
                                    items["schema"]["files"]["EventSchemaSetDefinition.json"]),
                    ],
                }
                if fault == "missing_export":
                    exported["itemDefinitionsIndex"] = []
                api = Mock()
                api.request.side_effect = [
                    Reply(200, {}, {
                        "status": "Running" if fault == "running" else "Failed",
                        "error": {"errorCode": "SqlEndpointCreationFailedDueToUserError"},
                    }),
                    Reply(200, {}, exported),
                ]
                api.complete.side_effect = lambda reply: reply.body
                api.list_all.return_value = [{
                    "id": SCHEMA, "displayName": items["schema"]["name"], "type": "EventSchemaSet",
                    "description": "Foreign owner" if fault == "owner" else deploy.OWNER,
                }]
                if fault == "unrelated_item":
                    api.list_all.return_value.append({
                        "id": RUN, "displayName": "UnrelatedNotebook", "type": "Notebook",
                    })
                path = Path(directory) / "state.json"
                deployment = deploy.Deployment(config(), api, path)
                deployment.state["pending_import"] = {
                    "location": FABRIC + "/v1/operations/" + RUN, "expected": expected,
                }
                if fault == "cached_failure":
                    deployment.state["pending_import"].update(
                        terminal_status="Failed", error_codes=["SqlEndpointCreationFailedDueToUserError"],
                    )
                    api.request.side_effect = [Reply(200, {}, exported)]
                deployment.save()
                if fault not in (None, "unrelated_item", "cached_failure"):
                    with self.assertRaises(ValueError):
                        deployment.reconcile_import()
                    self.assertIn("pending_import", json.loads(path.read_text()))
                    self.assertEqual({}, deployment.state["items"])
                else:
                    result = deployment.reconcile_import()
                    self.assertEqual("failed_native_import_reconciled_not_deployed", result["status"])
                    self.assertFalse(result["cloud_writes"])
                    saved = json.loads(path.read_text())
                    self.assertEqual({"schema": SCHEMA}, saved["items"])
                    self.assertNotIn("pending_import", saved)
                    self.assertEqual(
                        ("POST", f"workspaces/{WORKSPACE}/items/bulkExportDefinitions",
                         {"mode": "Selective", "items": [{"id": SCHEMA}]} if fault == "unrelated_item"
                         else {"mode": "All"}),
                        api.request.call_args.args,
                    )
                    if fault == "cached_failure":
                        self.assertEqual(1, api.request.call_count)

    def test_live_status_reads_inventory_and_failure_without_creating_or_adopting_items(self):
        api = Mock()
        api.request.side_effect = [
            Reply(200, {}, {"displayName": "Test"}),
            Reply(200, {}, {"status": "Failed", "error": {"errorCode": "UnsupportedItemType"}}),
        ]
        api.list_all.return_value = [{
            "id": SCHEMA, "displayName": "SalesVolumeCicdV1Source", "type": "Lakehouse",
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(config(), api, path)
            deployment.state["pending_import"] = {"location": FABRIC + "/v1/operations/" + RUN}
            deployment.state["last_failed_job"] = {
                "location": FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}",
                "operation": "inspect", "status": "Failed", "error_codes": ["CapacityLimitExceeded"],
            }
            result = deployment.status()
            self.assertEqual(1, result["workspace_item_count"])
            self.assertFalse(result["scenario_items"][0]["tracked"])
            self.assertEqual(["UnsupportedItemType"], result["native_operation"]["error_codes"])
            self.assertFalse(result["cloud_writes"])
            self.assertEqual(["CapacityLimitExceeded"], result["last_failed_notebook_job"]["error_codes"])
            self.assertEqual(
                ["UnsupportedItemType"],
                json.loads(path.read_text())["pending_import"]["error_codes"],
            )
            self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))

    def test_native_publisher_job_reads_kql_source_without_lakehouse_binding(self):
        api = Mock()
        api.control_resource = FABRIC
        location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
        api.request.return_value = Reply(202, {"Location": location}, {})
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(individual_config(), api, Path(directory) / "state.json")
            deployment.state.update(
                items={"schema": SCHEMA, "notebook": RUN, "database": WORKSPACE},
                query_endpoint="https://unit.kusto.fabric.microsoft.com",
            )
            with (
                patch.object(deployment, "preflight"),
                patch.object(deployment, "connect_eventhouse") as connection,
                patch.object(deployment, "get_definition", return_value=deploy.build_activator(individual_config(), SCHEMA)),
                patch.object(deployment, "wait_for_run"),
            ):
                deployment.run(True)
            method, route, body = api.request.call_args.args
            self.assertEqual(("POST", f"workspaces/{WORKSPACE}/notebooks/{RUN}/jobs/execute/instances?beta=false"), (method, route))
            parameters = {parameter["name"]: json.loads(parameter["value"]) for parameter in body["parameters"]}
            self.assertEqual(SCHEMA, parameters["config_json"]["schema_set_id"])
            self.assertEqual([], parameters["fixtures_json"])
            self.assertEqual("publish", parameters["config_json"]["operation"])
            self.assertEqual(WORKSPACE, parameters["config_json"]["database_id"])
            connection.assert_called_once_with(create=False)
            self.assertTrue(all(parameter["type"] == "Text" for parameter in body["parameters"]))
            self.assertEqual({"compute": "Spark"}, body["executionData"])
            self.assertEqual(location, deployment.state["pending_job"])

    def test_creation_is_one_native_batch_with_exact_source_parts_and_receipts(self):
        api = Mock()
        items = native.local_items()
        details = import_receipts()
        api.request.return_value = Reply(200, {}, {"importItemDefinitionsDetails": details})
        api.complete.side_effect = lambda reply: reply.body
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(config(), api, path)
            deployment.import_native(native.FOUNDATIONS)
            api.request.assert_called_once()
            method, route, payload = api.request.call_args.args
            self.assertEqual(("POST", f"workspaces/{WORKSPACE}/items/bulkImportDefinitions"), (method, route))
            self.assertEqual({"allowPairingByName": False}, payload["options"])
            self.assertEqual(4, len(json.loads(path.read_text())["items"]))
            for entry in payload["definitionParts"]:
                folder, filename = entry["path"].lstrip("/").split("/", 1)
                expected = next(item for item in items.values() if item["folder"] == folder)["files"][filename]
                self.assertEqual(expected, base64.b64decode(entry["payload"]))

    def test_partial_missing_or_foreign_receipts_do_not_report_success(self):
        for fault in ("partial", "missing", "foreign", "duplicate", "identity"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                details = import_receipts()
                if fault == "partial":
                    details[-1]["operationStatus"] = "SucceededDespiteFailures"
                elif fault == "missing":
                    details.pop()
                elif fault == "foreign":
                    details[-1]["itemLogicalId"] = SCHEMA
                elif fault == "duplicate":
                    details.append(copy.deepcopy(details[0]))
                else:
                    details[-1]["itemType"] = "Reflex"
                deployment = deploy.Deployment(config(), Mock(), Path(directory) / "state.json")
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    deployment.record_import({"importItemDefinitionsDetails": details}, {
                        role: {key: item[key] for key in ("name", "type", "logical_id")}
                        for role, item in native.local_items().items() if role in native.FOUNDATIONS
                    })
                self.assertIn("schema", deployment.state["items"])

    def test_native_import_saves_operation_before_polling_and_resumes_without_post(self):
        location = FABRIC + "/v1/operations/" + RUN
        api = Mock()
        api.control_resource = FABRIC
        api.request.return_value = Reply(202, {"Location": location}, {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(config(), api, path)

            def interrupted(reply):
                self.assertEqual(location, json.loads(path.read_text())["pending_import"]["location"])
                raise TimeoutError("Interrupted")

            api.complete.side_effect = interrupted
            with self.assertRaises(TimeoutError):
                deployment.import_native(native.FOUNDATIONS)
            with self.assertRaisesRegex(ValueError, "resume-import"):
                deployment.import_native(native.FOUNDATIONS)
            self.assertEqual(1, api.request.call_count)
            api.complete.side_effect = None
            api.complete.return_value = {"importItemDefinitionsDetails": import_receipts()}
            deployment.resume_import()
            self.assertEqual(1, api.request.call_count)
            self.assertNotIn("pending_import", json.loads(path.read_text()))

    def test_native_import_persists_terminal_failure_before_operation_url_becomes_unavailable(self):
        location = FABRIC + "/v1/operations/" + RUN
        api = Mock()
        api.control_resource = FABRIC
        api.request.return_value = Reply(202, {"Location": location}, {})
        api.complete.side_effect = FabricOperationError("Failed", ["SqlEndpointCreationFailedDueToUserError"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(config(), api, path)
            with self.assertRaisesRegex(FabricOperationError, "SqlEndpointCreationFailedDueToUserError"):
                deployment.import_native(native.FOUNDATIONS)
            saved = json.loads(path.read_text())["pending_import"]
            self.assertEqual("Failed", saved["terminal_status"])
            self.assertEqual(["SqlEndpointCreationFailedDueToUserError"], saved["error_codes"])
            with self.assertRaises(FabricOperationError):
                deployment.resume_import()
            self.assertEqual(1, api.request.call_count)

    def test_preview_plan_uses_beta_bulk_operation_not_item_creation(self):
        api = Mock()
        roles = (*native.FOUNDATIONS, "deployment_plan")
        api.request.return_value = Reply(200, {}, {"importItemDefinitionsDetails": import_receipts(roles)})
        api.complete.side_effect = lambda reply: reply.body
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(config(), api, Path(directory) / "state.json")
            deployment.import_native(roles, with_plan=True)
        self.assertEqual(
            f"workspaces/{WORKSPACE}/items/bulkImportDefinitions?beta=true",
            api.request.call_args.args[1],
        )
        self.assertIn("deploymentPlan", api.request.call_args.args[2]["options"])

    def test_daily_does_not_hide_backend_workload_rejections(self):
        target = config()
        target["environment"] = "daily"
        api = Mock()
        api.request.side_effect = [
            Reply(200, {}, {"capacityId": RUN, "displayName": "Test"}),
            RuntimeError("POST failed: HTTP 403, code InsufficientCapacitySku."),
        ]
        api.list_all.side_effect = [[], [{"id": RUN, "sku": "F16", "state": "Active"}]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(target, api, path)
            with self.assertRaisesRegex(RuntimeError, "InsufficientCapacitySku"):
                deployment.provision()
            self.assertEqual(["GET", "POST"], [call.args[0] for call in api.request.call_args_list])
            self.assertFalse(path.exists())

    def test_premium_per_user_cannot_pass_as_a_fabric_capacity(self):
        for environment in ("fabric", "daily"):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as directory:
                target = config()
                target["environment"] = environment
                api = Mock()
                api.request.return_value = Reply(200, {}, {"capacityId": RUN, "displayName": "Test"})
                api.list_all.side_effect = [[], [{"id": RUN, "sku": "PP3", "state": "Active"}]]
                path = Path(directory) / "state.json"
                deployment = deploy.Deployment(target, api, path)
                with self.assertRaisesRegex(ValueError, "Premium Per User"):
                    deployment.provision()
                self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))
                self.assertFalse(path.exists())

    def test_current_user_recipient_is_resolved_without_changing_configuration(self):
        target = individual_config()
        target["teams_recipients"] = ["@me"]
        api = Mock()
        api.control_resource = FABRIC
        api.tokens.current_user_address.return_value = "operations@example.com"
        with tempfile.TemporaryDirectory() as directory:
            deployment = deploy.Deployment(target, api, Path(directory) / "state.json")
            deployment.state["items"]["schema"] = SCHEMA
            deployment.state["items"]["activator"] = RUN
            with (
                patch.object(deployment, "preflight"),
                patch.object(deployment, "import_native") as importer,
                patch.object(deployment, "get_definition") as readback,
            ):
                readback.side_effect = lambda _: importer.call_args.kwargs["overrides"]["activator"]
                deployment.enable_activator()
            action_definition = json.dumps(deploy.decode_part(
                importer.call_args.kwargs["overrides"]["activator"], "ReflexEntities.json",
            ))
            self.assertIn("operations@example.com", action_definition)
            self.assertNotIn("@me", action_definition)
            self.assertEqual(["@me"], target["teams_recipients"])
            api.tokens.current_user_address.assert_called_once_with(FABRIC)

    def test_saved_job_resumes_without_submitting_another_publication(self):
        summary = {
            "run_id": RUN, "window_start": WINDOW, "published_count": 5, "expected_teams_matches": 2,
        }
        location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
        api = Mock()
        api.request.side_effect = [
            Reply(200, {}, {"status": "Completed"}),
            Reply(200, {}, {"status": "Completed"}),
            Reply(200, {}, {"properties": {"exitValue": json.dumps(summary)}}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            deployment = deploy.Deployment(individual_config(), api, path)
            deployment.state.update(items={"notebook": SCHEMA}, pending_job=location)
            deployment.save()
            with self.assertRaisesRegex(ValueError, "Use resume"):
                deployment.run(True)
            api.request.assert_not_called()
            result = deployment.resume()
            self.assertEqual("publisher_completed_consumers_unverified", result["status"])
            self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(summary, saved["last_run"])
            self.assertNotIn("pending_job", saved)

    def test_unavailable_job_exit_evidence_keeps_pending_job(self):
        location = FABRIC + f"/v1/workspaces/{WORKSPACE}/items/{SCHEMA}/jobs/instances/{RUN}"
        for exit_value in (None, json.dumps({
            "run_id": RUN, "window_start": WINDOW, "published_count": 1, "expected_teams_matches": 2,
        })):
            with self.subTest(exit_value=exit_value), tempfile.TemporaryDirectory() as directory:
                api = Mock()
                api.request.side_effect = [
                    Reply(200, {}, {"status": "Completed"}),
                    Reply(200, {}, {"status": "Completed"}),
                    Reply(200, {}, {"properties": {"exitValue": exit_value}}),
                ]
                path = Path(directory) / "state.json"
                deployment = deploy.Deployment(individual_config(), api, path)
                deployment.state.update(items={"notebook": SCHEMA}, pending_job=location)
                deployment.save()
                with self.assertRaises((RuntimeError, ValueError)):
                    deployment.resume()
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(location, saved["pending_job"])
                self.assertNotIn("last_run", saved)

    def test_missing_workspace_and_existing_unmanaged_name_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "Confirm a workspace"):
            deploy.Deployment(deploy.load_config(deploy.ROOT / "config.example.json"))
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            class Api:
                def request(self, method, path):
                    return Reply(200, {}, {"capacityId": RUN, "displayName": "Test"})

                def list_all(self, path):
                    return [{"id": RUN, "displayName": "SalesVolumeCicdV1Schema"}]

            deployment = deploy.Deployment(config(), Api(), state_path)
            with self.assertRaisesRegex(ValueError, "Unmanaged name collision"):
                deployment.preflight()
            self.assertFalse(state_path.exists())

    def test_ingestion_verifies_all_findings_but_not_teams(self):
        summary = {"run_id": RUN, "window_start": WINDOW, "expected_teams_matches": 2}
        payloads = expected_findings(deploy.fixtures(), RUN, WINDOW)
        columns = [
            "store_id", "expected_transactions", "actual_transactions", "deviation_pct",
            "window_start", "severity", "Deliveries",
        ]
        rows = [[payload.get(key, 1) for key in columns] for payload in payloads]
        class Api:
            environment = "fabric"
            check_url = FabricApi.check_url

            def request(self, method, path, body=None, resource=FABRIC):
                if method == "GET":
                    return Reply(200, {}, {
                        "id": SCHEMA, "properties": {"queryServiceUri": "https://unit.kusto.fabric.microsoft.com"},
                    })
                self.query = body
                return Reply(200, {}, {"Exceptions": getattr(self, "exceptions", []), "Tables": [{
                    "Columns": [{"ColumnName": name} for name in columns], "Rows": rows,
                }]})

        with tempfile.TemporaryDirectory() as directory:
            api = Api()
            deployment = deploy.Deployment(config(), api, Path(directory) / "state.json")
            deployment.state.update(last_run=summary, items={"database": SCHEMA})
            result = deployment.verify()
            self.assertEqual("eventhouse_verified_teams_unverified", result["status"])
            self.assertEqual("not_verified", result["teams_evidence"])
            self.assertIn(RUN, api.query["csl"])
            api.exceptions = ["Partial query failure"]
            with self.assertRaisesRegex(ValueError, "complete findings"):
                deployment.verify()
            api.exceptions = []
            rows[0][4] = "2026-11-05T12:00:00Z"
            with self.assertRaisesRegex(ValueError, "window_start"):
                deployment.verify()
            rows[0][4] = WINDOW
            original_deviation = rows[0][3]
            rows[0][3] = float("nan")
            with self.assertRaisesRegex(ValueError, "deviation"):
                deployment.verify()
            rows[0][3] = original_deviation
            rows.pop()
            with self.assertRaisesRegex(ValueError, "missing findings"):
                deployment.verify()


if __name__ == "__main__":
    unittest.main()
