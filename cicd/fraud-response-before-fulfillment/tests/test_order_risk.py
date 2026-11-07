import inspect
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class OrderRiskPublisherTests(unittest.TestCase):
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

    def test_native_metadata_matches_the_publisher_signature(self):
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

    def test_native_policy_boundaries_publish_one_card_scoped_decision(self):
        schema = json.loads((ROOT / "schema.avsc").read_text())
        self.assertEqual(schema["name"], self.spec["event_name"])
        fields = {field["name"] for field in schema["fields"]}
        for case in self.cases:
            with self.subTest(case=case["id"]):
                client = Mock()
                arguments = {target: case["input"][source] for source, target in self.spec["parameter_map"].items()}
                self.assertEqual(self.function(client, **arguments), {
                    "decision": case["expected"]["decision"], "event_published": True,
                })
                expected = {**case["input"], **case["expected"]}
                self.assertEqual(set(expected), fields)
                client.PublishEvent.assert_called_once_with(
                    type=schema["name"], event_data=expected, data_version="v1",
                )
                matching = [action for action in self.spec["actions"] if action["decision"] == case["expected"]["decision"]]
                self.assertEqual(len(matching), 1)
                self.assertTrue(set(matching[0]["parameters"]) <= fields)

    def test_invalid_inputs_never_publish(self):
        for field, value in (
            ("orderId", ""), ("customerId", " "), ("customerId", "x" * 129),
            ("riskScore", True), ("riskScore", "0.85"),
            ("riskScore", math.nan), ("riskScore", math.inf),
            ("riskScore", -0.01), ("riskScore", 1.01),
            ("decidedAt", "2026-11-01T10:00:00"),
            ("decidedAt", "2026-11-01T10:00:00-03:00"),
            ("decidedAt", "not-a-time"),
        ):
            with self.subTest(field=field, value=value):
                client = Mock()
                with self.assertRaises(ValueError):
                    self.function(client, **{**self.base, field: value})
                client.PublishEvent.assert_not_called()

    def test_normalizes_identifiers_and_utc(self):
        client = Mock()
        self.function(client, **{**self.base, "orderId": " test-order ", "decidedAt": "2026-11-01T10:00:00+00:00"})
        payload = client.PublishEvent.call_args.kwargs["event_data"]
        self.assertEqual(payload["order_id"], "test-order")
        self.assertEqual(payload["decided_at"], "2026-11-01T10:00:00Z")

    def test_publish_failure_does_not_claim_success_or_invoke_another_action(self):
        client = Mock()
        client.PublishEvent.side_effect = RuntimeError("synthetic transport failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic transport failure"):
            self.function(client, **self.base)
        client.PublishEvent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
