import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "cicd" / "fraud-response-before-fulfillment"
sys.path.insert(0, str(PACKAGE))
SPEC = importlib.util.spec_from_file_location("risk_flow_api", PACKAGE / "flow_api.py")
flow_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flow_api)
sys.path.pop(0)

WORKSPACE = "10000000-0000-4000-8000-000000000001"
ITEM = "10000000-0000-4000-8000-000000000002"
CAPACITY = "10000000-0000-4000-8000-000000000003"
HOST = CAPACITY.replace("-", "") + ".pbidedicated.windows.net"


class FakeApi:
    metadata_host = "https://df-msit-scus-redirect.analysis.windows.net"

    def __init__(self):
        self.calls = []
        self.artifact = {
            "objectId": ITEM, "artifactType": "ReflexProject", "capacityObjectId": CAPACITY,
            "extendedProperties": {"Aria.DocumentId": "a" * 32},
        }
        self.routing = {"Token": "private-workload-token", "TargetUriHost": HOST, "CapacityObjectId": CAPACITY}
        self.result = {
            "data": {"createFlowConnectionCreateRequest": [{
                "connectionString": f"{WORKSPACE}/reflexes/{ITEM}/config/powerAutomate/flow?connectionString=opaque",
                "flowConnectionId": "native-connection-identity", "connectionStringVersion": "V2",
            }]},
        }

    def request(self, method, url, body=None, resource=None):
        self.calls.append((method, url, body))
        value = (
            [self.artifact] if url.endswith("/artifacts")
            else self.routing if url.endswith("/generatemwctoken")
            else self.artifact
        )
        return SimpleNamespace(body=value)

    def _send(self, method, url, body, authorization):
        self.calls.append((method, url, body))
        if authorization() != "MwcToken private-workload-token":
            raise AssertionError("The native workload requires its own authorization scheme.")
        return SimpleNamespace(body=self.result)


class RiskNativeConnectionTests(unittest.TestCase):
    def test_exact_native_document_and_target_are_used(self):
        import json
        api = FakeApi()
        bridge = flow_api.ReflexActions(api, WORKSPACE, ITEM)
        result = bridge.create_connection()
        variables = json.loads(api.calls[-1][2]["variables"])["flowConnectionCreateRequestCreateInputType"]
        self.assertEqual(variables, {
            "artifactId": ITEM, "workspaceId": WORKSPACE,
            "tenant": {"__metadata": {"id": "a" * 32}},
        })
        self.assertEqual(result["flowConnectionId"], "native-connection-identity")
        self.assertTrue(api.calls[-1][1].startswith("https://" + HOST + "/webapi/capacities/" + CAPACITY))

    def test_untrusted_or_different_capacity_route_is_rejected_before_authorization(self):
        for field, value in (("TargetUriHost", "other.pbidedicated.windows.net"), ("CapacityObjectId", ITEM)):
            with self.subTest(field=field):
                api = FakeApi()
                api.routing[field] = value
                with self.assertRaises(ValueError):
                    flow_api.ReflexActions(api, WORKSPACE, ITEM)
                self.assertEqual(len(api.calls), 3)

    def test_another_activator_connection_is_not_adopted(self):
        api = FakeApi()
        bridge = flow_api.ReflexActions(api, WORKSPACE, ITEM)
        record = api.result["data"]["createFlowConnectionCreateRequest"][0]
        record["connectionString"] = record["connectionString"].replace(ITEM, CAPACITY)
        with self.assertRaises(ValueError):
            bridge.create_connection()

    def test_graphql_errors_and_ambiguous_results_do_not_look_successful(self):
        for result in (
            {"errors": [{"message": "Rejected"}]},
            {"data": {"createFlowConnectionCreateRequest": []}},
            {"data": {"createFlowConnectionCreateRequest": [{}, {}]}},
        ):
            with self.subTest(result=result):
                api = FakeApi()
                bridge = flow_api.ReflexActions(api, WORKSPACE, ITEM)
                api.result = result
                with self.assertRaises(RuntimeError):
                    bridge.create_connection()

    def test_action_preserves_the_native_connection_and_exact_field_contract(self):
        import json
        api = FakeApi()
        bridge = flow_api.ReflexActions(api, WORKSPACE, ITEM)
        api.result = {"data": {"createPowerAutomateFlowAction": [{"__metadata": {"id": "native-action"}}]}}
        bridge.create_action(ITEM, "Manual review", ["order_id", "customer_id", "risk_score"], "actual-native-connection")
        body = json.loads(api.calls[-1][2]["variables"])["powerAutomateFlowActionCreateInputType"]
        self.assertEqual(body["system"]["flowConnection"]["__metadata"]["id"], "actual-native-connection")
        self.assertEqual(body["payload"]["parameters"], [
            {"key": "order_id"}, {"key": "customer_id"}, {"key": "risk_score"},
        ])


if __name__ == "__main__":
    unittest.main()
