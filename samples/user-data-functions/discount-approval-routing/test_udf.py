import datetime
import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


class FakeUserDataFunctions:
    def connection(self, **_kwargs):
        return lambda function: function

    def function(self):
        return lambda function: function


fake_functions = types.ModuleType("fabric.functions")
fake_functions.UserDataFunctions = FakeUserDataFunctions
fake_functions.FabricBusinessEventsClient = object
fake_fabric = types.ModuleType("fabric")
fake_fabric.functions = fake_functions
sys.modules.setdefault("fabric", fake_fabric)
sys.modules.setdefault("fabric.functions", fake_functions)

spec = importlib.util.spec_from_file_location(
    "discount_approval_udf",
    Path(__file__).with_name("evaluate_discount_request.py"),
)
udf_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(udf_module)


class FakeBusinessEventsClient:
    def __init__(self):
        self.calls = []

    def PublishEvent(self, **kwargs):
        self.calls.append(kwargs)


def evaluate(discount=12, margin=31):
    client = FakeBusinessEventsClient()
    result = udf_module.evaluate_discount_request(
        client,
        "DEAL-1001",
        "Sample Retail",
        "Central",
        125000.0,
        discount,
        margin,
        datetime.datetime(
            2026,
            9,
            15,
            17,
            10,
            tzinfo=datetime.timezone.utc,
        ),
    )
    return client, result


class UdfTests(unittest.TestCase):
    def test_within_policy_does_not_publish(self):
        client, result = evaluate(10, 25)
        self.assertEqual("none", result["approval_level"])
        self.assertFalse(result["event_published"])
        self.assertEqual([], client.calls)

    def test_manager_boundary_publishes_v1_nine_field_event(self):
        client, result = evaluate(15, 25)
        self.assertEqual("manager", result["approval_level"])
        self.assertTrue(result["event_published"])
        call = client.calls[0]
        self.assertEqual(
            "Sales.Deals.DiscountApprovalRequired",
            call["type"],
        )
        self.assertEqual("v1", call["data_version"])
        self.assertEqual(
            {
                "deal_id",
                "account_name",
                "region",
                "deal_value",
                "requested_discount_percent",
                "estimated_margin_percent",
                "approval_level",
                "approval_reason",
                "event_time",
            },
            set(call["event_data"]),
        )

    def test_executive_policy_boundaries(self):
        for discount, margin in [
            (math.nextafter(15, math.inf), 25),
            (10, math.nextafter(25, -math.inf)),
        ]:
            with self.subTest(discount=discount, margin=margin):
                _, result = evaluate(discount, margin)
                self.assertEqual("executive", result["approval_level"])

    def test_rejects_non_finite_and_out_of_range_numbers(self):
        for discount in (math.nan, math.inf, -1, 101):
            with self.subTest(discount=discount):
                with self.assertRaises(ValueError):
                    evaluate(discount, 30)

    def test_rejects_invalid_strings_and_non_utc_time(self):
        client = FakeBusinessEventsClient()
        with self.assertRaisesRegex(ValueError, "non-empty"):
            udf_module.evaluate_discount_request(
                client,
                " ",
                "Sample Retail",
                "Central",
                125000,
                12,
                31,
                datetime.datetime.now(datetime.timezone.utc),
            )
        with self.assertRaisesRegex(ValueError, "must use UTC"):
            udf_module.evaluate_discount_request(
                client,
                "DEAL-1001",
                "Sample Retail",
                "Central",
                125000,
                12,
                31,
                datetime.datetime(
                    2026,
                    9,
                    15,
                    10,
                    10,
                    tzinfo=datetime.timezone(
                        datetime.timedelta(hours=-7)
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
