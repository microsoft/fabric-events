"""Native Notebook outcomes and filtered SQL failure-case registration."""

import ast
import datetime
import json
import re
import runpy
from urllib.parse import urlsplit
import uuid

from failure_records import FIELDS, validate_event
from public_api import FABRIC, GUID, public_url
from public_consumers import ActivatorDeployment, activator_entities, definition_part, part
from public_deploy import Deployment, ROOT, read, sql_database_destination
from public_udf import verify_udf_definition

FUNCTION = "register_pipeline_failure"
PARAMETERS = {
    "pipelineId": ("pipeline_id", "str"), "pipelineName": ("pipeline_name", "str"),
    "runId": ("run_id", "str"), "status": ("status", "str"),
    "rowsProcessed": ("rows_processed", "int"), "durationSeconds": ("duration_seconds", "int"),
    "triggeredBy": ("triggered_by", "str"), "completedAt": ("completed_at", "str"),
}


class FailureDeployment(ActivatorDeployment):
    resource_types = {
        "schema": "EventSchemaSet", "notebook": "Notebook", "activator": "Reflex",
        "case_store": "SQLDatabase", "response_function": "UserDataFunction",
    }

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("failure_public_version") != 1:
            raise ValueError("The ledger does not match the failure follow-up implementation. No adoption is allowed.")
        self.state["failure_public_version"] = 1
        self.state["activator_ids"].setdefault("fabric_action", str(uuid.uuid4()))
        self.logic = runpy.run_path(str(self.folder / "notebook-content.py"))
        self.logic["validate_fixtures"](read(self.folder / "fixtures.json"))

    def preflight(self, resuming=False):
        return Deployment.preflight(self, resuming)

    def source(self, name):
        source = (self.folder / name).read_text(encoding="utf-8").replace(
            "# ${failure_helpers}", (ROOT / "failure_records.py").read_text(encoding="utf-8"),
        )
        source = source.replace("${rule_id}", self.state["activator_ids"]["rule"])
        if "${" in source:
            raise ValueError("The failure follow-up source has unresolved deployment bindings.")
        ast.parse(source)
        return source

    @property
    def action_parameters(self):
        return [(name, field, "Number" if kind == "int" else "String")
                for name, (field, kind) in PARAMETERS.items()]

    def activator_entities(self, should_run=True):
        return activator_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"], [],
            should_run=should_run, action={
                "item_id": self.state["items"]["response_function"], "item_type": "UserDataFunctions",
                "job_type": "Execute", "function": FUNCTION, "parameters": self.action_parameters,
            },
        )

    def response_definition(self):
        declaration = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/userDataFunction/definition/1.1.0/schema.json",
            "runtime": "PYTHON", "connectedDataSources": [{
                "alias": "FailureCases", "artifactId": self.state["items"]["case_store"],
                "artifactType": "SqlDbNative", "workspaceId": self.config["workspace_id"],
            }],
            "functions": [{"name": FUNCTION, "description": "Register one SQL triage case per failed execution.",
                           "isPublicEndpointEnabled": True}],
            "libraries": {"public": [], "private": []},
        }
        metadata = {"runtime": "PYTHON", "functionsMetadata": [{
            "name": FUNCTION, "scriptFile": "function_app.py", "bindings": [
                {"methods": ["POST"], "route": "", "authLevel": "Anonymous", "name": "req",
                 "direction": "In", "type": "HttpTrigger"},
                {"itemType": None, "subType": "FabricSqlConnection", "alias": "FailureCases",
                 "name": "caseDatabase", "direction": "In", "type": "FabricItem"},
            ], "fabricProperties": {
                "fabricMetadataSchemaVersion": "1.1.0", "fabricFunctionReturnType": "dict",
                "fabricFunctionParameters": [
                    {"name": name, "dataType": kind} for name, (_, kind) in PARAMETERS.items()
                ],
            },
        }]}
        return {"parts": [
            part("definition.json", declaration), part("function_app.py", self.source("response-function.py")),
            part("resources/functions.json", metadata),
        ]}

    def creation_request(self, role):
        if role == "schema":
            return Deployment.creation_request(self, role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "case_store":
            return self.base + "items", dict(body, type="SQLDatabase")
        if role == "notebook":
            body["definition"] = {"format": "fabricGitSource", "parts": [
                part("notebook-content.py", self.source("notebook-content.py")),
            ]}
            return self.base + "notebooks", body
        if role == "response_function":
            body["definition"] = self.response_definition()
            return self.base + "userDataFunctions", body
        if role == "activator":
            return self.base + "items", dict(body, type="Reflex", definition={
                "parts": [part("ReflexEntities.json", self.activator_entities(False))],
            })
        raise ValueError("Unknown failure follow-up resource role.")

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy all five failure follow-up resources before testing.")
        self.verify_schema()
        self.roles()
        self.state["sql_destination"] = sql_database_destination(self, "case_store")
        notebook = self.get_definition("notebook")
        if ast.dump(ast.parse(definition_part(notebook, "notebook-content.py"))) != ast.dump(
            ast.parse(self.source("notebook-content.py")),
        ):
            raise ValueError("The actual Notebook differs from the original workload publisher.")
        verify_udf_definition(self, "response_function", self.response_definition())
        self.check_activator(self.get_definition("activator"))
        self.state.setdefault("definition_backup", {})["notebook"] = notebook
        result = {
            "status": "manual_setup_required", "items": self.state["items"],
            "sql_destination": self.state["sql_destination"], "sql_schema_verified": False,
            "source_binding_verified": True, "manual_setup_required": ["run_sql_setup_scripts"],
            "eventhouse_subscription_configured": False, "e2e_verified": False,
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Deploy the operational failure follow-up resources as one reviewed configuration.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public create; never replay it.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("case_store")
        self.create("notebook")
        self.create("response_function")
        self.create("activator")
        self.start_activator()
        result = self.verify_provisioning()
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Failure follow-up uses SQL cases, not a Teams chat.")
        if self.state.get("test_run"):
            raise ValueError("This deployment already has a source attempt. Do not replay its workload outcomes.")
        result = self.verify_provisioning()
        fixtures = read(self.folder / "fixtures.json")
        run_id = str(uuid.uuid4())
        parameters = {
            "source_run_id": run_id, "workspace_id": self.config["workspace_id"],
            "schema_set_id": self.state["items"]["schema"], "event_name": self.config["event_name"],
            "notebook_id": self.state["items"]["notebook"], "notebook_name": self.config["resource_names"]["notebook"],
        }
        attempt = {
            "run_id": run_id, "fixtures": fixtures, "parameters": parameters, "phase": "request_started",
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.state["test_run"] = attempt
        self.save()
        reply = self.api.request(
            "POST", self.base + f"notebooks/{self.state['items']['notebook']}/jobs/execute/instances?beta=false",
            {"parameters": [
                {"name": "config_json", "value": json.dumps(parameters), "type": "Text"},
                {"name": "fixtures_json", "value": json.dumps(fixtures), "type": "Text"},
            ], "executionData": {"compute": "Spark"}},
        )
        attempt.update(actual_status=reply.status, phase="accepted_location_pending")
        self.save()
        location = reply.headers.get("location")
        parsed = urlsplit(location) if isinstance(location, str) else None
        expected = f"/v1/workspaces/{self.config['workspace_id']}/items/{self.state['items']['notebook']}/jobs/instances/"
        if (reply.status != 202 or parsed is None or parsed.scheme != "https"
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment or not re.fullmatch(re.escape(expected) + GUID, parsed.path)):
            raise RuntimeError("The Notebook request has no exact accepted job identity. Retain the attempt; do not replay.")
        attempt.update(location=public_url("GET", FABRIC + parsed.path)[0],
                       job_id=parsed.path.split("/")[-1], phase="running")
        result.update(
            status="source_triggered_verification_pending", source_triggered=True, source_run_id=run_id,
            source_job_id=attempt["job_id"], source_execution_verified=False, outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Inspect SQL follow-up cases, not a Teams chat.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if not attempt:
            return result
        if not attempt.get("location"):
            raise ValueError("The source attempt is ambiguous. Retain it without resubmitting.")
        job = self.api.request("GET", attempt["location"]).body
        if job.get("id") != attempt["job_id"] or job.get("itemId") != self.state["items"]["notebook"]:
            raise ValueError("The Notebook job readback changed its exact source identity.")
        attempt["job"] = job
        result.update(source_run_id=attempt["run_id"], source_execution_verified=False, source_job=job)
        if job.get("status") == "Completed":
            detail = self.api.request(
                "GET", self.base + f"notebooks/{self.state['items']['notebook']}"
                + f"/jobs/execute/instances/{attempt['job_id']}?beta=true",
            ).body
            if (detail.get("id") != attempt["job_id"] or detail.get("itemId") != self.state["items"]["notebook"]
                    or detail.get("status") != "Completed" or detail.get("failureReason")):
                raise ValueError("The completed Notebook detail does not match the saved source job.")
            summary = json.loads(detail.get("exitValue") or detail.get("properties", {}).get("exitValue", ""))
            self.check_summary(attempt, summary)
            attempt.update(summary=summary, phase="source_completed_sql_evaluation_pending")
            result.update(source_execution_verified=True, publication_requests=summary["publication_requests"],
                          expected_events=summary["payloads"], expected_cases=summary["expected_cases"])
        elif job.get("status") not in ("NotStarted", "InProgress", "Running"):
            self.save()
            raise RuntimeError("The source Notebook failed or was cancelled. Retain its resources and evidence.")
        self.state["verification"] = result
        self.save()
        return result

    def check_summary(self, attempt, summary):
        if (not isinstance(summary, dict) or summary.get("source_run_id") != attempt["run_id"]
                or summary.get("source_item_id") != self.state["items"]["notebook"]
                or not isinstance(summary.get("payloads"), list)
                or len(summary["payloads"]) != len(attempt["fixtures"])
                or summary.get("publication_requests") != len(attempt["fixtures"])):
            raise ValueError("The source summary does not match the saved workload attempt.")
        for fixture, payload in zip(attempt["fixtures"], summary["payloads"]):
            event = validate_event(payload)
            if (event["pipeline_id"] != self.state["items"]["notebook"]
                    or event["pipeline_name"] != self.config["resource_names"]["notebook"]
                    or event["run_id"] != str(uuid.uuid5(uuid.UUID(attempt["run_id"]), fixture["id"]))
                    or event["status"] != {"fail": "failed", "complete": "completed", "skip": "skipped"}[fixture["operation"]]
                    or event["rows_processed"] != fixture["rows"] or event["triggered_by"] != "manual"
                    or datetime.datetime.fromisoformat(event["completed_at"].replace("Z", "+00:00"))
                    < datetime.datetime.fromisoformat(attempt["started_at"])):
                raise ValueError("The Notebook's actual workload facts differ from its source fixtures.")
        if summary.get("expected_cases") != sum(row["operation"] == "fail" for row in attempt["fixtures"]):
            raise ValueError("The source case expectation differs from its original failed executions.")
