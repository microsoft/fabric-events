"""Public Sales provisioning and independently observed Teams delivery."""

import ast
import datetime
import json
import re
import runpy
import time
from urllib.parse import urlsplit

from public_api import FABRIC, GUID, public_url
from public_consumers import (
    ActivatorDeployment, activator_entities, definition_part, is_teams_receipt, part,
    teams_content, teams_evidence, ui_fields_match,
)
from public_deploy import FOUNDATIONS, read, source_tables


class SalesDeployment(ActivatorDeployment):
    resource_types = {**FOUNDATIONS, "notebook": "Notebook", "activator": "Reflex"}

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("sales_public_version") != 1:
            raise ValueError("The ledger does not match this exercise's resources. No resource adoption is permitted.")
        self.state["sales_public_version"] = 1
        self.logic = runpy.run_path(str(self.folder / "notebook-content.py"))

    def notebook_parameters(self, operation):
        item = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["database"]).body
        self.check_item("database", item)
        properties = item["properties"]
        address = properties["queryServiceUri"]
        parsed = urlsplit(address)
        if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".kusto.fabric.microsoft.com")
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or properties["parentEventhouseItemId"] != self.state["items"]["eventhouse"]):
            raise ValueError("The owned source database returned an invalid public Kusto endpoint or parent.")
        return {"query_endpoint": address.rstrip("/"), "database_id": self.state["items"]["database"],
                "workspace_id": self.config["workspace_id"], "schema_set_id": self.state["items"]["schema"],
                "event_name": self.config["event_name"], "operation": operation}

    def notebook_source(self):
        source = (self.folder / "notebook-content.py").read_text(encoding="utf-8-sig")
        marker = 'config_json = "{}"'
        event_marker = 'EVENT_NAME = "Retail.Sales.VolumeAlert"'
        if source.count(marker) != 1 or source.count(event_marker) != 1:
            raise ValueError("The publisher template has no unique reviewed configuration markers.")
        source = source.replace(marker, "config_json = " + json.dumps(json.dumps(self.notebook_parameters("publish"))))
        return source.replace(event_marker, "EVENT_NAME = " + json.dumps(self.config["event_name"]))

    def creation_request(self, role):
        if role not in ("database", "notebook", "activator"):
            return super().creation_request(role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "database":
            body["definition"] = {"parts": [
                part("DatabaseProperties.json", {"databaseType": "ReadWrite",
                     "parentEventhouseItemId": self.state["items"]["eventhouse"]}),
                part("DatabaseSchema.kql", (self.folder / "DatabaseSchema.kql").read_text()),
            ]}
            collection = "kqlDatabases"
        elif role == "notebook":
            body["definition"] = {"format": "fabricGitSource", "parts": [
                part("notebook-content.py", self.notebook_source()),
            ]}
            collection = "notebooks"
        else:
            body.update(type="Reflex", definition={"parts": [part("ReflexEntities.json", activator_entities(
                self.config, self.state["items"]["schema"], self.state["activator_ids"], self.state["recipients"],
            ))]})
            collection = "items"
        return self.base + collection, body

    def verify_source(self):
        definition = self.get_definition("database")
        actual = source_tables(definition_part(definition, "DatabaseSchema.kql"))
        expected = source_tables((self.folder / "DatabaseSchema.kql").read_text())
        if not expected or actual != expected:
            raise ValueError("The actual KQL source tables and column types do not match the deployed contract.")
        properties = json.loads(definition_part(definition, "DatabaseProperties.json"))
        if (properties["databaseType"] != "ReadWrite"
                or properties["parentEventhouseItemId"] != self.state["items"]["eventhouse"]):
            raise ValueError("The source definition is not bound to the owned Eventhouse.")
        self.notebook_parameters("publish")
        return definition


    def verify_provisioning(self):
        result = super().verify_foundations()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("The complete Sales source, publisher and notification resources must be deployed.")
        source = self.verify_source()
        notebook = self.get_definition("notebook")
        actual_code = definition_part(notebook, "notebook-content.py")
        if ast.dump(ast.parse(actual_code)) != ast.dump(ast.parse(self.notebook_source())):
            raise ValueError("The Notebook's actual source or target binding differs from the approved publisher.")
        activator = self.get_definition("activator")
        self.check_activator(activator)
        self.state["definition_backup"] = {"schema": self.get_definition("schema"), "database": source,
                                          "notebook": notebook, "activator": activator}
        result.update(status="sales_public_provisioning_verified_e2e_pending",
                      source_binding_verified=True, full_deployment_status="deployed_e2e_pending")
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Use the shared runner for explicit foundations-only provisioning.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public create, never start it again.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("eventhouse")
        self.create("database")
        self.verify_source()
        self.create("notebook")
        self.create("activator")
        self.start_activator()
        result = self.verify_provisioning()
        self.state["status"] = "sales_public_deployed_e2e_pending"
        self.save()
        return result

    def test(self, chat_id=None):
        if self.state.get("test_run"):
            raise ValueError("This deployment already has a test attempt. Use verify; do not replay its publication.")
        result = self.verify_provisioning()
        fixtures = read(self.folder / "fixtures.json")
        self.logic["validate_fixtures"](fixtures)
        parameters = self.notebook_parameters("seed-and-publish")
        attempt = {
            "phase": "request_started", "chat_id": chat_id, "fixtures": fixtures,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.state["test_run"] = attempt
        self.save()
        response = self.api.request("POST", self.base + f"notebooks/{self.state['items']['notebook']}/jobs/execute/instances?beta=false", {
            "parameters": [
                {"name": "config_json", "value": json.dumps(parameters), "type": "Text"},
                {"name": "fixtures_json", "value": json.dumps(fixtures), "type": "Text"},
            ], "executionData": {"compute": "Spark"},
        })
        attempt.update(actual_status=response.status, phase="accepted_location_pending")
        self.save()
        if response.status != 202:
            raise RuntimeError("Notebook execution did not return its documented accepted status.")
        location = response.headers.get("location")
        if not isinstance(location, str):
            raise RuntimeError("Accepted Notebook run has no job Location. Do not repeat the request.")
        parsed = urlsplit(location)
        expected = f"/v1/workspaces/{self.config['workspace_id']}/items/{self.state['items']['notebook']}/jobs/instances/"
        attempt["returned_job_location"] = {"authority": parsed.hostname, "path": parsed.path}
        self.save()
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment or not re.fullmatch(re.escape(expected) + GUID, parsed.path)):
            raise ValueError("The Notebook job Location has no valid identity in the exact workspace and item.")
        location = public_url("GET", FABRIC + parsed.path)[0]
        attempt.update(location=location, job_id=parsed.path.split("/")[-1],
                       retry_after=response.headers.get("retry-after", "1"), phase="running")
        self.save()
        result.update(
            status="source_triggered_verification_pending", source_triggered=True,
            source_job_id=attempt["job_id"], source_item_id=self.state["items"]["notebook"],
            source_job_url=location, source_execution_verified=False,
            events_published=None, e2e_verified=False, outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def poll_notebook(self):
        attempt = self.state["test_run"]
        if not attempt.get("location"):
            raise ValueError("The Notebook request is ambiguous. Retain the ledger; no publication will be replayed.")
        deadline = time.monotonic() + 1200
        while True:
            delay = attempt["retry_after"]
            if not isinstance(delay, str) or not delay.isdigit() or not 0 <= int(delay) <= 120:
                raise ValueError("Invalid Notebook polling Retry-After.")
            self.api.sleep(max(1, int(delay)))
            response = self.api.request("GET", attempt["location"])
            attempt["job"] = response.body
            attempt["retry_after"] = response.headers.get("retry-after", "5")
            self.save()
            job = response.body
            if job["id"] != attempt["job_id"] or job["itemId"] != self.state["items"]["notebook"]:
                raise ValueError("Notebook job readback changed its invocation identity.")
            if job["status"] == "Completed":
                detail = self.api.request("GET", self.base + f"notebooks/{self.state['items']['notebook']}"
                                          + f"/jobs/execute/instances/{attempt['job_id']}?beta=true").body
                if (detail["id"] != attempt["job_id"] or detail["itemId"] != self.state["items"]["notebook"]
                        or detail["status"] != "Completed" or detail.get("failureReason")):
                    raise ValueError("Notebook exit-value readback changed the tested invocation.")
                exit_value = detail.get("exitValue")
                if exit_value is None:
                    exit_value = detail.get("properties", {}).get("exitValue")
                if not isinstance(exit_value, str) or not exit_value:
                    raise RuntimeError("The completed Notebook has no documented exitValue; publication is unverified.")
                summary = json.loads(exit_value)
                expected = self.logic["expected_findings"](attempt["fixtures"], summary["run_id"], summary["window_start"])
                if (summary["operation"] != "seed-and-publish"
                        or sorted(summary["payloads"], key=lambda row: row["store_id"]) != sorted(
                            expected, key=lambda row: row["store_id"])
                        or summary["source_store_count"] != len(expected)
                        or summary["published_count"] != len(expected)
                        or summary["source_transaction_count"] != sum(row["actual_transactions"] for row in expected)
                        or summary["expected_teams_matches"] != sum(row["deviation_pct"] < -30 for row in expected)):
                    raise ValueError("The real source results or publication counts differ from the tested fixture contract.")
                attempt.update(job=detail, summary=summary, phase="published_teams_pending")
                self.save()
                return summary
            if job["status"] not in ("NotStarted", "InProgress", "Running"):
                raise RuntimeError("The Notebook failed or was cancelled. Retain resources and the recorded job failure.")
            if time.monotonic() >= deadline:
                raise TimeoutError("The Notebook remains running. Use verify for its saved job, never submit again.")

    def verify(self, chat_id=None):
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if not attempt:
            return result
        if chat_id is not None and attempt["chat_id"] is not None and chat_id != attempt["chat_id"]:
            raise ValueError("Teams verification must use this test run's original approved chat.")
        summary = self.poll_notebook()
        if attempt["chat_id"] is None and chat_id is not None:
            address = self.chat_address(chat_id)
            self.chat_messages(address)
            attempt["chat_id"] = chat_id
            self.save()
        address = self.teams_address(attempt["chat_id"])
        if address is None:
            result.update(status="sales_public_source_verified_teams_pending", e2e_verified=False,
                          events_published=summary["published_count"], source_run_id=summary["run_id"],
                          teams_verification_status="approved_chat_and_graph_readback_required")
            self.state.update(status=result["status"], verification=result)
            self.save()
            return result
        expected = {row["store_id"]: row for row in summary["payloads"] if row["deviation_pct"] < -30}
        excluded = {row["store_id"] for row in summary["payloads"] if row["deviation_pct"] >= -30}
        started = datetime.datetime.fromisoformat(attempt["started_at"])
        deadline = time.monotonic() + 300
        matched = {}
        while True:
            for message in self.chat_messages(address):
                if not is_teams_receipt(message):
                    continue
                created = datetime.datetime.fromisoformat(message["createdDateTime"].replace("Z", "+00:00"))
                if created < started - datetime.timedelta(seconds=30):
                    continue
                content = teams_content(message)
                content = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", content)
                if any(store in content for store in excluded):
                    attempt["unexpected_message_id"] = message["id"]
                    self.save()
                    raise ValueError("Teams received a non-matching or -30% boundary fixture. The real filter failed.")
                for store, payload in expected.items():
                    if (store in content and ui_fields_match(message, payload)
                            and all(str(payload[key]) in content and key in content for key in (
                            "expected_transactions", "actual_transactions", "window_start", "severity",
                        )) and any(value in content for value in (
                            str(payload["deviation_pct"]), format(payload["deviation_pct"], "g"),
                        ))):
                        if store in matched and matched[store]["id"] != message["id"]:
                            raise ValueError("Teams received duplicate receipts for a tested store.")
                        matched[store] = {"store_id": store, **teams_evidence(message)}
            attempt["teams_messages"] = list(matched.values())
            self.save()
            if time.monotonic() >= deadline:
                break
            self.api.sleep(5)
        if set(matched) != set(expected) or len({message["id"] for message in matched.values()}) != len(expected):
            raise RuntimeError("Actual Teams delivery is incomplete. Retain the deployment and verify this same run.")
        result.update(status="sales_public_e2e_verified_no_event_analysis", e2e_verified=True,
                      events_published=summary["published_count"], source_run_id=summary["run_id"],
                      teams_message_ids=[message["id"] for message in matched.values()],
                      non_matching_notifications_observed=0, observation_seconds=300,
                      teams_verifier=("public_windows_ui_automation" if address == "windows_ui" else "public_graph"))
        attempt["phase"] = "e2e_verified"
        self.state.update(status=result["status"], verification=result)
        self.save()
        return result
