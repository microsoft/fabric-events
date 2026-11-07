"""Observe real native flows, Eventhouse payloads and persisted fulfillment."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request
import uuid

from fabric_api import KUSTO, NoRedirect
import foundation
import native

ROOT = Path(__file__).resolve().parent


class TestRun:
    def __init__(self, deployment):
        self.deployment = deployment
        self.api = deployment.api
        self.backend = deployment.backend
        self.flows = deployment.flows
        self.config = deployment.config
        self.state = None
        self.path = None

    def save(self):
        foundation.atomic_json(self.path, self.state)

    def runs(self, decision):
        identifier = self.deployment.state["flows"][decision]
        return self.flows.request("GET", "flows/" + identifier + "/runs")["value"]

    def history(self):
        database = self.api.request(
            "GET", self.deployment.base + "kqlDatabases/" + self.deployment.state["items"]["database"],
        ).body
        endpoint = database["properties"]["queryServiceUri"].rstrip("/") + "/v1/rest/query"
        query = (
            "['" + self.config["event_name"] + "']\n"
            f'| where order_id startswith "cicd-{self.state["run_id"]}-"\n'
            "| summarize Deliveries=count() by order_id, customer_id, risk_score, decision, decided_at"
        )
        result = self.api.request("POST", endpoint, {"db": database["id"], "csl": query}, resource=KUSTO).body
        tables = [
            table for table in result["Tables"]
            if "order_id" in [column["ColumnName"] for column in table["Columns"]]
        ]
        if len(tables) != 1 or result.get("error") or result.get("Exceptions"):
            raise RuntimeError("The Eventhouse query returned errors or incomplete payload columns.")
        table = tables[0]
        return [
            dict(zip([column["ColumnName"] for column in table["Columns"]], row))
            for row in table["Rows"]
        ]

    def observe(self):
        observed = []
        for decision, identifier in self.deployment.state["flows"].items():
            for run in self.runs(decision):
                if run["name"] in self.state["baseline_run_ids"][decision]:
                    continue
                properties = self.flows.request(
                    "GET", "flows/" + identifier + "/runs/" + run["name"],
                )["properties"]
                observation = {"decision": decision, "run_id": run["name"], "status": properties["status"]}
                link = properties.get("trigger", {}).get("outputsLink", {}).get("uri")
                if link:
                    parsed = urllib.parse.urlsplit(link)
                    environment = uuid.UUID(self.config["flow_environment_id"]).hex
                    host = environment[:30] + "." + environment[30:] + ".environment.api.powerplatformusercontent.com"
                    if (
                        parsed.scheme != "https" or parsed.hostname != host or parsed.port not in (None, 443)
                        or parsed.username or parsed.password or parsed.fragment
                    ):
                        raise ValueError("The signed run-output URL is outside the exact owned runtime host.")
                    opener = urllib.request.build_opener(NoRedirect())
                    with opener.open(link, timeout=90) as response:
                        body = json.load(response)["body"]
                    observation["trigger"] = {
                        key: body[key] for key in ("customProperties", "inputFields", "eventId", "ruleId")
                        if key in body
                    }
                observed.append(observation)
        self.state["flow_runs"] = observed
        self.state["history_rows"] = self.history()
        self.state["orders"] = [
            self.backend.request("GET", "/orders/" + case["order"]["order_id"])
            for case in self.state["cases"]
        ]
        self.save()
        return observed

    def publish(self):
        for case in self.state["cases"]:
            if case["status"] == "prepared":
                case["status"] = "registration_started"
                self.save()
                case["registration"] = self.backend.request("POST", "/orders", case["order"], expected=201)
                registered = case["registration"]
                if (
                    registered["status"] != "pending" or registered["action_count"] != 0
                    or any(registered[key] != value for key, value in case["order"].items())
                ):
                    raise ValueError("The fresh synthetic order did not start with the exact pending context.")
                case["status"] = "registered"
                self.save()
            elif case["status"] == "registration_started":
                order = self.backend.request("GET", "/orders/" + case["order"]["order_id"])
                if any(order[key] != value for key, value in case["order"].items()):
                    raise ValueError("An ambiguous registration has different persisted context.")
                if order["status"] != "pending" or order["action_count"] != 0:
                    raise ValueError("An ambiguously registered order is not pending with zero actions.")
                case["registration"] = order
                case["status"] = "registered"
                self.save()
        for case in self.state["cases"]:
            if case["status"] != "registered":
                continue
            case["status"] = "native_invocation_started"
            self.save()
            response = self.api.request(
                "POST", self.deployment.base + "userDataFunctions/"
                + self.deployment.state["items"]["publisher"] + "/functions/evaluate_order_risk/invoke",
                case["parameters"],
            ).body
            output = response.get("output")
            output = json.loads(output) if isinstance(output, str) else output
            if response.get("status") != "Succeeded" or output != {
                "decision": case["decision"], "event_published": True,
            }:
                raise RuntimeError("The actual UDF did not publish the expected boundary decision.")
            case["publisher_response"] = response
            case["status"] = "native_event_published"
            self.save()

    def verify_case(self, case, orders, rows, observed, rule_ids):
        order_id, decision = case["order"]["order_id"], case["decision"]
        order, row = orders[order_id], rows[order_id]
        matching = [
            run for run in observed
            if run.get("trigger", {}).get("customProperties", {}).get("order_id") == order_id
        ]
        if len(matching) != 1 or matching[0]["decision"] != decision:
            raise RuntimeError("The test order does not have exactly one matching native decision flow.")
        trigger = matching[0]["trigger"]
        fields = next(action["parameters"] for action in self.deployment.spec["actions"] if action["decision"] == decision)
        for name in ("customProperties", "inputFields"):
            context = trigger.get(name, {})
            if set(context) != set(fields):
                raise RuntimeError("The native action's exact parameter contract differs.")
            for field in fields:
                if field == "risk_score":
                    if not math.isclose(float(context[field]), case["order"][field], rel_tol=0, abs_tol=1e-6):
                        raise RuntimeError("The native action score differs from the registered order.")
                elif context[field] != case["order"][field]:
                    raise RuntimeError("The native action context differs from the registered order.")
        expected_status = {"hold": "held", "manual_review": "review_requested", "release": "released"}[decision]
        if decision == "release" and case.get("fulfillment_started") and order["status"] == "fulfilled":
            expected_status = "fulfilled"
        if (
            order["status"] != expected_status or order["decision"] != decision or order["action_count"] != 1
            or order["activation_id"] != trigger["eventId"] or trigger["ruleId"] != rule_ids[decision]
            or row["Deliveries"] != 1 or row["decision"] != decision
            or row["customer_id"] != case["order"]["customer_id"]
            or not math.isclose(row["risk_score"], case["order"]["risk_score"], rel_tol=0, abs_tol=1e-6)
            or row["decided_at"] != case["parameters"]["decidedAt"]
        ):
            raise RuntimeError("Persisted action, native activation and complete history do not correlate.")
        if decision == "manual_review" and (not order["review_request_id"] or order["review_completed"] is not False):
            raise RuntimeError("Manual review must create an actual request, not claim completed human review.")
        if not case.get("fulfillment_started"):
            case["persisted_after_native_action"] = order
            case["fulfillment_started"] = True
            self.save()
        code = 200 if decision == "release" else 409
        if "fulfillment_http_status" not in case:
            if decision == "release" and order["status"] == "fulfilled":
                case["fulfillment_recovered_from_persisted_state"] = True
            else:
                case["fulfillment_response"] = self.backend.request(
                    "POST", "/orders/" + order_id + "/fulfill", {}, expected=code,
                )
            case["fulfillment_http_status"] = code
            self.save()
        actual = self.backend.request("GET", "/orders/" + order_id)
        if actual["status"] != ("fulfilled" if decision == "release" else expected_status):
            raise RuntimeError("Independent readback did not confirm persisted fulfillment enforcement.")
        case["persisted_after_fulfillment"] = actual
        case["native_run_id"] = matching[0]["run_id"]
        case["status"] = "native_end_to_end_and_fulfillment_verified"
        self.save()

    def verify(self):
        deadline = time.monotonic() + 300
        while True:
            observed = self.observe()
            failed = any(run["status"] in ("Failed", "Cancelled", "TimedOut") for run in observed)
            if failed or (
                len(observed) >= 6 and len(self.state["history_rows"]) >= 6
                and all(run["status"] == "Succeeded" for run in observed)
            ):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Six real native runs and six complete history payloads were not observed.")
            time.sleep(10)
        if len(observed) != 6 or any(run["status"] != "Succeeded" for run in observed):
            raise RuntimeError("The actual flow runs do not prove six exclusive successful decisions.")
        if len(self.state["history_rows"]) != 6:
            raise RuntimeError("The history does not contain exactly six complete test payloads.")
        rule_ids = {}
        for node in self.deployment.entities():
            definition = node["payload"].get("definition", {})
            if definition.get("type") == "Rule":
                instance = json.loads(definition["instance"])
                condition = next(step for step in instance["steps"] if step["name"] == "EventDetectStep")
                value = next(argument["value"] for row in condition["rows"] for argument in row["arguments"] if argument["name"] == "value")
                rule_ids[value] = node["uniqueIdentifier"]
        orders = {order["order_id"]: order for order in self.state["orders"]}
        rows = {row["order_id"]: row for row in self.state["history_rows"]}
        expected_orders = {case["order"]["order_id"] for case in self.state["cases"]}
        if set(orders) != expected_orders or set(rows) != expected_orders:
            raise RuntimeError("The complete history and persisted order identities differ from this exact test run.")
        for case in self.state["cases"]:
            self.verify_case(case, orders, rows, observed, rule_ids)
        self.state["status"] = "six_boundary_native_end_to_end_verified"
        self.save()
        return {"run_id": self.state["run_id"], "status": self.state["status"], "native_runs": 6, "fulfillment_checks": 6}

    def run(self, new_run=False, verify_only=False):
        self.deployment.preflight()
        if self.deployment.state.get("status") != "deployed_native_flows_ready_no_test_inputs":
            raise ValueError("The exact deployment has not completed all native flow readbacks.")
        self.backend.connect()
        selected = self.deployment.state.get("active_test_file")
        if new_run and selected:
            prior = json.loads(Path(selected).read_text(encoding="utf-8"))
            if prior["status"] != "six_boundary_native_end_to_end_verified":
                raise ValueError("Finish or inspect the current test before creating another batch.")
        if not selected or new_run:
            if verify_only:
                raise ValueError("There is no test to verify; no batch was created.")
            run_id = str(uuid.uuid4())
            self.path = self.deployment.state_path.parent / ("test-" + run_id + ".json")
            self.state = {
                "run_id": run_id, "status": "prepared",
                "baseline_run_ids": {decision: [run["name"] for run in self.runs(decision)] for decision in self.deployment.state["flows"]},
                "cases": [],
            }
            for fixture in json.loads((ROOT / "data" / "test-cases.json").read_text(encoding="utf-8")):
                order = {
                    "order_id": f"cicd-{run_id}-{fixture['id']}",
                    "customer_id": f"test-{run_id}-{fixture['id']}", "risk_score": fixture["input"]["risk_score"],
                }
                self.state["cases"].append({
                    "case_id": fixture["id"], "decision": fixture["expected"]["decision"],
                    "order": order, "status": "prepared", "parameters": {
                        "orderId": order["order_id"], "customerId": order["customer_id"],
                        "riskScore": order["risk_score"], "decidedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    },
                })
            self.save()
            self.deployment.state["active_test_file"] = str(self.path)
            self.deployment.save()
        else:
            self.path = Path(selected)
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        if not verify_only:
            self.deployment.apply_rules(True)
            self.publish()
        return self.verify()
