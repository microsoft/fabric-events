import inspect
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class LowStockPublisherTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT / "scenario.json").read_text())
        self.item = ROOT / self.spec["publisher"]["item"]
        functions = types.ModuleType("fabric.functions")
        functions.FabricBusinessEventsClient = object
        self.registration = Mock()
        self.registration.connection.side_effect = lambda **kwargs: lambda function: function
        self.registration.function.side_effect = lambda: lambda function: function
        functions.UserDataFunctions = Mock(return_value=self.registration)
        package = types.ModuleType("fabric")
        package.functions = functions
        source = (self.item / "function_app.py").read_text().replace(
            "${event_name}", self.spec["event_name"],
        ).replace("${schema_name}", self.spec["resource_names"]["schema"])
        namespace = {}
        with patch.dict(sys.modules, {"fabric": package, "fabric.functions": functions}):
            exec(compile(source, "native_function_app.py", "exec"), namespace)
        self.function = namespace[self.spec["publisher"]["function_name"]]
        self.cases = json.loads((ROOT / "data" / "test-cases.json").read_text())
        self.base = {
            target: self.cases[0]["input"][source]
            for source, target in self.spec["parameter_map"].items()
        }

    def test_native_function_and_metadata_match_the_payload_contract(self):
        schema = json.loads((ROOT / "schema.avsc").read_text())
        self.assertEqual(schema["name"], self.spec["event_name"])
        self.assertEqual(set(self.spec["parameter_map"]), {field["name"] for field in schema["fields"]})
        metadata = json.loads((self.item / "resources" / "functions.json").read_text())["functionsMetadata"][0]
        definition = json.loads((self.item / "definition.json").read_text())
        self.assertEqual(definition["functions"][0]["name"], metadata["name"])
        self.assertEqual(metadata["name"], self.function.__name__)
        self.assertEqual(
            {name: parameter.annotation.__name__ for name, parameter in inspect.signature(self.function).parameters.items() if name != "businessEventsClient"},
            {parameter["name"]: parameter["dataType"] for parameter in metadata["fabricProperties"]["fabricFunctionParameters"]},
        )
        self.registration.connection.assert_called_once_with(
            argName="businessEventsClient", alias=self.spec["resource_names"]["schema"],
        )
        for case in self.cases:
            with self.subTest(case=case["id"]):
                client = Mock()
                arguments = {target: case["input"][source] for source, target in self.spec["parameter_map"].items()}
                self.assertEqual(self.function(client, **arguments), {"event_published": True})
                client.PublishEvent.assert_called_once_with(
                    type=schema["name"], event_data=case["input"], data_version="v1",
                )

    def test_publisher_does_not_rebuild_the_warehouse_detector(self):
        for stock in (self.base["threshold"], self.base["threshold"] + 1):
            with self.subTest(stock=stock):
                client = Mock()
                self.function(client, **{**self.base, "currentStock": stock})
                self.assertEqual(client.PublishEvent.call_args.kwargs["event_data"]["current_stock"], stock)

    def test_normalizes_text_and_utc_without_adding_payload_fields(self):
        client = Mock()
        self.function(client, **{**self.base, "productId": " test-product ", "detectedAt": "2026-11-01T10:00:00+00:00"})
        payload = client.PublishEvent.call_args.kwargs["event_data"]
        self.assertEqual(payload["product_id"], "test-product")
        self.assertEqual(payload["detected_at"], "2026-11-01T10:00:00Z")
        self.assertEqual(set(payload), set(self.cases[0]["input"]))

    def test_invalid_inputs_never_publish(self):
        for field, value in (
            ("productId", ""), ("productName", " "), ("supplierId", None),
            ("storeId", "x" * 129), ("currentStock", True),
            ("currentStock", 3.5), ("currentStock", 2 ** 31),
            ("threshold", -(2 ** 31) - 1), ("threshold", "10"),
            ("detectedAt", "2026-11-01T10:00:00"),
            ("detectedAt", "2026-11-01T10:00:00+02:00"),
            ("detectedAt", "not-a-time"),
        ):
            with self.subTest(field=field, value=value):
                client = Mock()
                with self.assertRaises(ValueError):
                    self.function(client, **{**self.base, field: value})
                client.PublishEvent.assert_not_called()

    def test_publish_failure_is_not_reported_as_success(self):
        client = Mock()
        client.PublishEvent.side_effect = RuntimeError("synthetic transport failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic transport failure"):
            self.function(client, **self.base)
        client.PublishEvent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
