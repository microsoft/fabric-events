import importlib.util
from pathlib import Path
import unittest
import uuid

path = Path(__file__).resolve().parents[1] / "test-order-api" / "orders.py"
spec = importlib.util.spec_from_file_location("risk_test_orders", path)
orders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orders)


class OrderApiTests(unittest.TestCase):
    def setUp(self):
        self.order = orders.new_order({
            "order_id": f"cicd-{uuid.uuid4()}-boundary",
            "customer_id": "test-customer-01",
            "risk_score": 0.85,
        })

    def test_hold_is_persistent_transition_and_blocks_fulfillment(self):
        payload = {"order_id": self.order["order_id"], "risk_score": 0.8500000238}
        held = orders.transition(self.order, "hold", payload, "activation-one")
        self.assertEqual(held["status"], "held")
        with self.assertRaises(orders.OrderError) as failure:
            orders.fulfill(held)
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(orders.transition(held, "hold", payload, "activation-one"), held)
        with self.assertRaises(orders.OrderError):
            orders.transition(held, "release", {"order_id": held["order_id"]}, "activation-two")

    def test_release_actually_allows_fulfillment(self):
        released = orders.transition(
            self.order, "release", {"order_id": self.order["order_id"]}, "activation-release",
        )
        self.assertEqual(orders.fulfill(released)["status"], "fulfilled")

    def test_review_creates_request_but_is_not_completed_review(self):
        reviewed = orders.transition(self.order, "manual_review", {
            "order_id": self.order["order_id"], "customer_id": self.order["customer_id"],
            "risk_score": self.order["risk_score"],
        }, "activation-review")
        self.assertEqual(reviewed["status"], "review_requested")
        self.assertTrue(reviewed["review_request_id"])
        self.assertFalse(reviewed["review_completed"])
        with self.assertRaises(orders.OrderError):
            orders.fulfill(reviewed)

    def test_rejects_real_orders_or_mismatched_parameters(self):
        with self.assertRaises(orders.OrderError):
            orders.identity("production-order-123")
        with self.assertRaises(orders.OrderError):
            orders.transition(self.order, "hold", {
                "order_id": self.order["order_id"], "risk_score": 0.2,
            }, "activation-mismatch")
        with self.assertRaises(orders.OrderError):
            orders.transition(self.order, "manual_review", {
                "order_id": self.order["order_id"], "customer_id": "test-another",
                "risk_score": self.order["risk_score"],
            }, "activation-mismatch")


if __name__ == "__main__":
    unittest.main()
