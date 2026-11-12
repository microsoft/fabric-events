import copy
import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from test_public_cicd import ActionApi, ROOT, TENANT, WORKSPACE
import payment_deploy
import public_api
import public_deploy
from public_consumers import definition_part, part

REAL_SEND_EVENT = payment_deploy.send_event


class PaymentApi(ActionApi):
    def __init__(self):
        super().__init__()
        self.source_id = str(uuid.uuid4())
        self.chat_id = "19:payment@thread.v2"
        self.messages, self.inputs = [], []
        self.status = "Running"
        self.ignore_rule_update = False
        self.notify_boundary = False
        self.duplicate_receipt = False
        self.omit_destination_schema = False
        self.connection = {
            "type": "CustomEndpoint", "fullyQualifiedNamespace": "test.servicebus.windows.net",
            "eventHubName": "payment-input", "accessKeys": {
                "primaryKey": "unit-test-key",
                "primaryConnectionString": "Endpoint=sb://test.servicebus.windows.net/;"
                "SharedAccessKeyName=Send;SharedAccessKey=unit-test-key;EntityPath=payment-input",
            },
        }

    def _request(self, method, url, resource, body, headers):
        path = urlsplit(url).path
        if path.endswith("/updateDefinition"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            if not self.ignore_rule_update:
                self.definitions[path.split("/")[-2]] = copy.deepcopy(body["definition"])
            return public_api.Reply(200, {}, None)
        if path.endswith("/topology"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            definition = self.definitions[path.split("/")[-2]]
            topology = json.loads(definition_part(definition, "eventstream.json"))
            if self.omit_destination_schema:
                for destination in topology["destinations"]:
                    destination.pop("inputSchemas", None)
            for row in topology["sources"] + topology["destinations"]:
                row.update(id=self.source_id, status=self.status, error=None)
            return public_api.Reply(200, {}, topology)
        if path.endswith("/connection"):
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            return public_api.Reply(200, {}, copy.deepcopy(self.connection))
        if resource == public_api.GRAPH:
            self.calls.append((method, url, copy.deepcopy(body), headers, resource))
            result = {"value": self.messages} if "/messages" in path else {
                "id": self.chat_id, "chatType": "oneOnOne",
            }
            return public_api.Reply(200, {}, copy.deepcopy(result))
        return super()._request(method, url, resource, body, headers)

    def send(self, connection, payload):
        self.assert_connection = connection
        self.inputs.append(copy.deepcopy(payload))
        if payload["amount"] > 5000 or self.notify_boundary and payload["amount"] == 5000:
            self.emit_record("payment", payload)
            message = {
                "id": str(len(self.messages)), "from": {"application": {"id": "unit-test-bot"}},
                "createdDateTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "body": {"content": " ".join(f"{key}: {payload[key]}" for key in payment_deploy.FIELDS)},
                "attachments": [],
            }
            self.messages.append(message)
            if self.duplicate_receipt:
                self.messages.append({**message, "id": message["id"] + "-duplicate"})
        return 201


class PaymentPublicTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = ROOT / "cicd" / "real-time-stream-alert"
        self.config = public_deploy.configuration(self.folder, self.folder / "config.card.example.json")
        self.config.update(tenant_id=TENANT, workspace_id=WORKSPACE, accept_preview_apis=True)
        self.api = PaymentApi()
        self.target = payment_deploy.PaymentDeployment(
            self.folder, self.config, Path(directory.name) / "ledger.json", self.api,
        )
        self.clock = [0]
        self.api.sleep = lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds)
        self.addCleanup(patch.stopall)
        patch.object(payment_deploy.time, "monotonic", side_effect=lambda: self.clock[0]).start()
        patch.object(payment_deploy, "send_event", side_effect=self.api.send).start()

    def test_deployment_keeps_filter_native_and_creates_no_unused_eventhouse(self):
        result = self.target.deploy()
        self.assertEqual({row["type"] for row in self.api.items.values()},
                         {"EventSchemaSet", "Eventstream", "Reflex", "Lakehouse", "UserDataFunction"})
        self.assertEqual(self.api.inputs, [])
        self.assertFalse(result["e2e_verified"])
        topology = self.target.state["stream_topology"]
        self.assertEqual(topology["operators"][0]["properties"]["conditions"][0]["operatorType"], "GreaterThan")
        self.assertEqual(topology["operators"][0]["properties"]["conditions"][0]["value"]["value"], "5000")
        self.assertEqual(topology["destinations"][0]["properties"]["itemId"], self.target.state["items"]["schema"])
        self.assertEqual(len(topology["operators"][1]["properties"]["columns"]), 6)
        entities = json.loads(definition_part(self.target.get_definition("activator"), "ReflexEntities.json"))
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        detector = next(row for row in json.loads(rule["payload"]["definition"]["instance"])["steps"]
                        if row["name"] == "EventDetectStep")
        self.assertEqual(detector["rows"], [{"name": "OnEveryValue", "kind": "OnEveryValue", "arguments": []}])
        self.assertFalse(any("/jobs/" in call[1] for call in self.api.calls))

    def test_consuming_udf_persists_cases_without_teams_or_direct_consumer_invocation(self):
        self.target.deploy()
        triggered = self.target.test()
        self.assertFalse(triggered["e2e_verified"])
        self.assertEqual(len(triggered["transaction_ids"]), 6)
        self.assertFalse(any("/queryActivations" in call[1] for call in self.api.calls))
        result = self.target.verify()
        self.assertTrue(result["e2e_verified"])
        self.assertEqual(result["persisted_outcome_status"], "pending_review")
        self.assertEqual(len(self.api.inputs), 6)
        self.assertEqual(result["operational_records_verified"], 3)
        self.assertFalse(any(call[-1] == public_api.GRAPH for call in self.api.calls))
        self.assertFalse(any(call[0] == "POST" and ("/invoke" in call[1] or "/jobs/" in call[1]) for call in self.api.calls))

    def test_functionset_normalization_is_allowed_only_on_the_standalone_action(self):
        self.target.deploy()
        identifier = self.target.state["items"]["activator"]
        entities = self.target.activator_entities()
        action = next(row for row in entities if row["type"] == "fabricItemAction-v1")
        action["payload"]["fabricItem"]["itemType"] = "FunctionSet"
        self.api.definitions[identifier]["parts"][0] = part("ReflexEntities.json", entities)
        self.target.verify_provisioning()
        rule = next(row for row in entities if row["payload"].get("definition", {}).get("type") == "Rule")
        instance = json.loads(rule["payload"]["definition"]["instance"])
        arguments = instance["steps"][-1]["rows"][0]["arguments"]
        self.assertEqual(next(row["value"] for row in arguments if row["name"] == "subitemId"), "register_payment_review")
        next(row for row in arguments if row["name"] == "itemType")["value"] = "FunctionSet"
        rule["payload"]["definition"]["instance"] = json.dumps(instance)
        self.api.definitions[identifier]["parts"][0] = part("ReflexEntities.json", entities)
        with self.assertRaisesRegex(ValueError, "action differs"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_legacy_configuration_and_owned_ledger_keep_their_original_identity(self):
        legacy = {**self.config, "scenario_id": "high-value-payment"}
        config_path = self.target.path.with_name("legacy.config.json")
        public_deploy.save(config_path, legacy)
        loaded = public_deploy.configuration(self.folder, config_path)
        self.assertEqual(loaded, legacy)
        ledger = self.target.path.with_name("legacy.ledger.json")
        target = payment_deploy.PaymentDeployment(self.folder, loaded, ledger, self.api)
        target.deploy()
        original = ledger.read_bytes()
        resumed = payment_deploy.PaymentDeployment(self.folder, loaded, ledger, self.api)
        self.assertEqual(ledger.read_bytes(), original)
        self.assertEqual(resumed.owner, "Managed by fabric-events/cicd public-only/high-value-payment.")
        self.assertTrue(resumed.verify_provisioning()["source_binding_verified"])
        sales = ROOT / "cicd" / "sales-volume-alert"
        with self.assertRaisesRegex(ValueError, "different scenario"):
            public_deploy.configuration(sales, config_path)

    def test_changed_filter_is_rejected_before_any_input_submission(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        native = json.loads(definition_part(definition, "eventstream.json"))
        native["operators"][0]["properties"]["conditions"][0]["operatorType"] = "GreaterThanOrEqual"
        definition["parts"][0] = part("eventstream.json", native)
        with self.assertRaisesRegex(ValueError, "native filter"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_service_node_reordering_preserves_graph_identity_but_duplicate_nodes_fail(self):
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        native = json.loads(definition_part(definition, "eventstream.json"))
        native["operators"].reverse()
        definition["parts"][0] = part("eventstream.json", native)
        self.target.verify_provisioning()
        native["operators"].append(copy.deepcopy(native["operators"][0]))
        definition["parts"][0] = part("eventstream.json", native)
        with self.assertRaisesRegex(ValueError, "duplicate nodes"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_topology_can_omit_destination_schema_but_definition_must_preserve_it(self):
        self.api.omit_destination_schema = True
        self.target.deploy()
        definition = self.api.definitions[self.target.state["items"]["publisher"]]
        native = json.loads(definition_part(definition, "eventstream.json"))
        native["destinations"][0]["inputSchemas"][0]["schema"]["columns"][2]["type"] = "Nvarchar(max)"
        definition["parts"][0] = part("eventstream.json", native)
        with self.assertRaisesRegex(ValueError, "six-field mapping"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_source_only_submits_all_six_without_claiming_publication_or_delivery(self):
        self.target.deploy()
        self.target.source_only = True
        result = self.target.test()
        self.assertEqual(len(self.api.inputs), 6)
        self.assertEqual([row["amount"] for row in self.api.inputs], [4999.99, 5000, 5000, 5000.01, 6000, 9000])
        self.assertFalse(result["e2e_verified"])
        self.assertIsNone(result["events_published"])
        self.assertFalse(any(call[-1] == public_api.GRAPH for call in self.api.calls))
        self.assertNotIn("unit-test-key", self.target.path.read_text())
        self.target.source_only = False
        verified = self.target.verify()
        self.assertTrue(verified["e2e_verified"])
        self.assertEqual(verified["events_published"], 3)
        self.assertEqual(len(self.api.inputs), 6)

    def test_native_filter_boundary_records_fail_certification(self):
        self.target.deploy()
        self.api.notify_boundary = True
        self.target.test()
        with self.assertRaisesRegex(ValueError, "excluded finding"):
            self.target.verify()
        self.assertFalse(self.target.state["verification"]["e2e_verified"])
        self.assertEqual(len(self.api.inputs), 6)

    def test_modified_committed_context_fails_instead_of_claiming_success(self):
        self.target.deploy()
        self.target.test()
        next(iter(self.api.records.values()))["currency"] = "USD"
        next(iter(self.api.records.values()))["store_id"] = "unexpected-store"
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.target.verify()

    def test_ambiguous_source_delivery_never_replays_inputs(self):
        self.target.deploy()
        with patch.object(payment_deploy, "send_event", side_effect=RuntimeError("Ambiguous input")):
            with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
                self.target.test()
        with self.assertRaisesRegex(ValueError, "already has a test"):
            self.target.test()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.target.verify()
        self.assertIn("pending_input", public_deploy.read(self.target.path)["test_run"])

    def test_missing_consumer_delivery_stays_incomplete(self):
        self.target.deploy()
        self.api.omit_records = True
        self.assertTrue(self.target.test()["source_triggered"])
        with self.assertRaisesRegex(RuntimeError, "remain incomplete"):
            self.target.verify()
        self.assertFalse(self.target.state["verification"]["e2e_verified"])

    def test_failed_topology_or_unpersisted_rule_blocks_sample_input(self):
        self.api.ignore_rule_update = True
        with self.assertRaisesRegex(ValueError, "run settings"):
            self.target.deploy()
        self.assertEqual(self.api.inputs, [])
        self.api.ignore_rule_update = False
        self.target.deploy(resume=True)
        self.api.status = "Warning"
        with self.assertRaisesRegex(RuntimeError, "destination failed"):
            self.target.test()
        self.assertEqual(self.api.inputs, [])

    def test_fixture_and_connection_guards_reject_nonfinite_values_and_host_substitution(self):
        fixtures = public_deploy.read(self.folder / "fixtures.json")
        fixtures[0]["amount"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            payment_deploy.fixture_payloads(fixtures, str(uuid.uuid4()), "2026-11-07T20:00:00Z")
        connection = copy.deepcopy(self.api.connection)
        connection["fullyQualifiedNamespace"] = "test.servicebus.windows.net.attacker.example"
        with self.assertRaisesRegex(ValueError, "unexpected"):
            payment_deploy.source_connection(connection)
        connection = copy.deepcopy(self.api.connection)
        connection["eventHubName"] = "different-hub"
        with self.assertRaisesRegex(ValueError, "exact returned endpoint"):
            payment_deploy.source_connection(connection)

    def test_eventhub_rest_send_uses_the_owned_public_endpoint_without_redirects(self):
        payload = {"transaction_id": "unit-test", "amount": 5000}
        response = unittest.mock.MagicMock()
        response.status = 201
        response.__enter__.return_value = response
        with patch.object(payment_deploy, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(REAL_SEND_EVENT(self.api.connection, payload), 201)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://test.servicebus.windows.net/payment-input/messages?timeout=60&api-version=2014-01")
            self.assertEqual(json.loads(request.data), payload)
            self.assertTrue(request.get_header("Authorization").startswith("SharedAccessSignature "))
            self.assertIsInstance(opener.call_args.args[0], public_api.NoRedirect)
