import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from test_public_cicd import ROOT, TENANT, WORKSPACE
from action_deploy import PAYMENT_PARAMETERS
import business_records
import payment_deploy
import public_api
import public_deploy


class BusinessActionTests(unittest.TestCase):
    def setUp(self):
        self.rule = str(uuid.uuid4())
        self.payment = {
            "transaction_id": "demo-transaction", "payment_method": "Card", "amount": 6000.0,
            "currency": "EUR", "store_id": "Store01", "processed_at": "2026-01-01T00:00:00Z",
        }

    def test_records_keep_context_idempotency_status_and_conflicting_fields_fail(self):
        first = business_records.make_record("payment", self.payment, self.rule)
        replay = business_records.make_record("payment", self.payment, self.rule)
        self.assertEqual(first["case_id"], replay["case_id"])
        self.assertEqual(business_records.check_record("payment", first, replay), first)
        for field, value in (("amount", 9000.0), ("currency", "USD"), ("store_id", "Store02")):
            with self.subTest(field=field):
                wanted = business_records.make_record("payment", {**self.payment, field: value}, self.rule)
                with self.assertRaisesRegex(ValueError, "conflicting"):
                    business_records.check_record("payment", first, wanted)
        for value in ({"status": "fraud_confirmed"}, {"created_at": "not-a-time"}, {"amount": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                business_records.check_record("payment", {**first, **value}, replay)

    def test_inventory_keeps_negative_stock_without_inventing_replenishment_quantity(self):
        payload = {
            "product_id": "Display", "product_name": "Display", "store_id": "Store01",
            "current_stock": -2, "threshold": 8, "supplier_id": "Supplier01",
            "detected_at": "2026-01-01T00:00:00Z",
        }
        first = business_records.make_record("inventory", payload, self.rule, str(uuid.uuid4()))
        second = business_records.make_record("inventory", payload, self.rule, str(uuid.uuid4()))
        self.assertEqual(business_records.check_record("inventory", first, second), first)
        self.assertEqual(first["current_stock"], -2)
        self.assertEqual(first["status"], "pending_approval")
        self.assertFalse(any("quantity" in field for field in first))
        changed = business_records.make_record(
            "inventory", {**payload, "detected_at": "2026-01-02T00:00:00Z"}, self.rule, str(uuid.uuid4()),
        )
        self.assertNotEqual(first["request_id"], changed["request_id"])

    def test_udf_returns_only_validated_storage_and_duplicate_delivery_never_overwrites(self):
        folder = ROOT / "cicd" / "real-time-stream-alert"
        config = public_deploy.configuration(folder, folder / "config.card.example.json")
        config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
        with tempfile.TemporaryDirectory() as directory:
            target = payment_deploy.PaymentDeployment(folder, config, Path(directory) / "ledger.json")
            store = str(uuid.uuid4())
            target.state["items"]["record_store"] = store
            target.state["record_store_readback"] = {"properties": {
                "oneLakeFilesPath": public_api.ONELAKE + "/" + WORKSPACE + "/" + store + "/Files",
            }}
            source = target.response_source()
        functions = ModuleType("fabric.functions")
        functions.UserDataFunctions = lambda: SimpleNamespace(
            function=lambda: lambda function: function, connection=lambda **kwargs: lambda function: function,
        )
        functions.FabricLakehouseClient = object
        fabric = ModuleType("fabric")
        fabric.functions = functions
        exceptions = ModuleType("azure.core.exceptions")
        class ExistsError(Exception):
            status_code = 412
        exceptions.ResourceExistsError = ExistsError
        exceptions.ResourceModifiedError = ExistsError
        azure_core = ModuleType("azure.core")
        azure_core.MatchConditions = SimpleNamespace(IfMissing="if_missing")
        namespace = {}
        with patch.dict(sys.modules, {
            "fabric": fabric, "fabric.functions": functions, "azure.core": azure_core,
            "azure.core.exceptions": exceptions,
        }):
            exec(compile(source, "function_app.py", "exec"), namespace)
        saved = []
        def create(match_condition):
            self.assertEqual(match_condition, "if_missing")
            if saved:
                raise ExistsError()
            saved.append(b"")
        def append(payload, offset, length):
            self.assertEqual((offset, length), (0, len(payload)))
            saved[0] = payload
        client = SimpleNamespace(
            create_file=Mock(side_effect=create), append_data=Mock(side_effect=append), flush_data=Mock(),
            download_file=lambda: SimpleNamespace(readall=lambda: saved[0]), close=Mock(),
        )
        files = SimpleNamespace(
            url=public_api.ONELAKE + "/" + WORKSPACE + "/" + store + "/Files",
            get_file_client=Mock(return_value=client), close=Mock(),
        )
        lakehouse = SimpleNamespace(connectToFiles=lambda: files)
        arguments = {name: self.payment[field] for name, (field, _) in PAYMENT_PARAMETERS.items()}
        function = namespace["register_payment_review"]
        first = function(lakehouse, **arguments)
        self.assertEqual(function(lakehouse, **arguments), first)
        self.assertEqual(len(saved), 1)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            function(lakehouse, **{**arguments, "currency": "USD"})
        saved[0] = b""
        with self.assertRaises(json.JSONDecodeError):
            function(lakehouse, **arguments)
        files.url = "https://private.example/" + WORKSPACE + "/" + store + "/Files"
        calls = client.create_file.call_count
        with self.assertRaisesRegex(ValueError, "public OneLake"):
            function(lakehouse, **arguments)
        self.assertEqual(client.create_file.call_count, calls)

    def test_service_provided_regional_endpoint_is_validated_not_hardcoded(self):
        folder = ROOT / "cicd" / "real-time-stream-alert"
        config = public_deploy.configuration(folder, folder / "config.card.example.json")
        config.update(tenant_id=TENANT, workspace_id=WORKSPACE)
        with tempfile.TemporaryDirectory() as directory:
            target = payment_deploy.PaymentDeployment(folder, config, Path(directory) / "ledger.json")
            store = str(uuid.uuid4())
            target.state["items"]["record_store"] = store
            endpoint = "https://westus-onelake.dfs.fabric.microsoft.com/" + WORKSPACE + "/" + store + "/Files"
            target.state["record_store_readback"] = {"properties": {"oneLakeFilesPath": endpoint}}
            self.assertIn(endpoint, target.response_source())
            for invalid in (endpoint.replace("westus-onelake.dfs.fabric.microsoft.com", "private.example"),
                            endpoint.replace(store, str(uuid.uuid4())), endpoint + "?credential=unsafe"):
                target.state["record_store_readback"]["properties"]["oneLakeFilesPath"] = invalid
                with self.assertRaises(ValueError):
                    target.response_source()

    def test_public_transport_rejects_other_storage_paths_and_header_overrides(self):
        folder = public_api.ONELAKE + "/" + WORKSPACE + "/" + str(uuid.uuid4()) + "/Files/PaymentReviewCases"
        self.assertEqual(public_api.public_url("PUT", folder + "?resource=directory")[1], public_api.STORAGE)
        for method, address in (
            ("DELETE", folder), ("PUT", folder + "/" + "a" * 64 + ".json"),
            ("GET", folder + "/../other.json"), ("GET", folder + "/unexpected.json"),
        ):
            with self.subTest(method=method, address=address), self.assertRaises(ValueError):
                public_api.public_url(method, address)

    def test_native_cli_triggers_without_outcome_checks_or_teams_configuration(self):
        for scenario, implementation in (
            ("low-stock-threshold", "inventory_deploy.InventoryDeployment"),
            ("real-time-stream-alert", "payment_deploy.PaymentDeployment"),
        ):
            folder = ROOT / "cicd" / scenario
            config = public_deploy.configuration(folder, folder / "config.card.example.json")
            config.update(tenant_id=TENANT, workspace_id=WORKSPACE, accept_preview_apis=True)
            with tempfile.TemporaryDirectory() as directory:
                config_path, ledger = Path(directory) / "config.json", Path(directory) / "ledger.json"
                public_deploy.save(config_path, config)
                public_deploy.save(ledger, {})
                for flags in ([], ["--source-only"]):
                    with self.subTest(scenario=scenario, flags=flags), patch(implementation) as constructor:
                        constructor.return_value.test.return_value = {"source_triggered": True, "e2e_verified": False}
                        with patch("sys.stdout"), patch("sys.stderr"):
                            code = public_deploy.main(folder, [
                                "test", "--config", str(config_path), "--state-file", str(ledger),
                                "--confirm-workspace", WORKSPACE, *flags,
                            ])
                        self.assertEqual(code, 0)
                        self.assertEqual(constructor.return_value.source_only, bool(flags))
                        constructor.return_value.test.assert_called_once_with(None)
                        constructor.return_value.verify.assert_not_called()
