"""Public native interaction publisher, filtered UDF response and recommendation snapshots."""

import ast
import copy
import datetime
import json
import re
import uuid

from action_deploy import ActionDeployment
from public_api import FABRIC, POWERBI, Reply
from public_consumers import activator_entities, definition_part, part
from public_deploy import Deployment, ROOT, read
from public_udf import publisher_definition, verify_publisher_definition
from viewer_records import (
    REFRESH_ACTIONS, catalog_items, check_recommendations, make_recommendations,
    recommendation_id, utc_time, validate_event,
)

FUNCTION = "publish_viewer_behavior_event"
RESPONSE = "refresh_session_recommendations"
PARAMETERS = {
    "viewerId": ("viewer_id", "str"), "sessionId": ("session_id", "str"),
    "contentId": ("content_id", "str"), "action": ("action", "str"), "occurredAt": ("occurred_at", "str"),
}
RESPONSE_PARAMETERS = dict(PARAMETERS)


def fixture_inputs(fixtures, catalog, run_id, occurred_at):
    run_id = str(uuid.UUID(run_id))
    instant = utc_time(occurred_at)
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide nonempty synthetic viewer fixtures.")
    rows, labels = [], set()
    for index, row in enumerate(fixtures):
        if (not isinstance(row, dict)
                or set(row) != {"id", "viewer", "session", "content_id", "action", "expected"}
                or any(not isinstance(row[key], str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", row[key])
                       for key in ("id", "viewer", "session"))
                or row["id"] in labels):
            raise ValueError("Viewer fixtures require distinct portable labels and exact reviewed fields.")
        labels.add(row["id"])
        event = validate_event({
            "viewer_id": f"cicd-{run_id}-{row['viewer']}", "session_id": f"cicd-{run_id}-{row['session']}",
            "content_id": row["content_id"], "action": row["action"],
            "occurred_at": (instant - datetime.timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
        })
        if event["action"] in REFRESH_ACTIONS:
            calculated = make_recommendations(event, catalog, str(uuid.UUID(int=0)))
            if row["expected"] != [item["content_id"] for item in calculated["recommendations"]]:
                raise ValueError("The fixture recommendations differ from the configured catalog and ranking.")
        elif row["expected"] is not None:
            raise ValueError("Excluded interactions must not expect recommendations.")
        rows.append({"fixture_id": row["id"], "event": event, "expected": row["expected"]})
    return rows


def invocation_output(reply):
    response = reply.body
    if (reply.status != 200 or not isinstance(response, dict) or response.get("status") != "Succeeded"
            or response.get("errors") not in (None, [])
            or "functionName" in response and response["functionName"] != FUNCTION):
        raise RuntimeError("The viewer publisher did not confirm successful execution. Retain the attempt.")
    output = response.get("output")
    if isinstance(output, str):
        output = json.loads(output)
    if (not isinstance(output, dict) or set(output) != {"event_published"} or output["event_published"] is not True
            or not isinstance(response.get("invocationId"), str) or not response["invocationId"].strip()):
        raise RuntimeError("The viewer publisher did not confirm its publication request and invocation identity.")
    return output


class ViewerDeployment(ActionDeployment):
    resource_types = {
        "schema": "EventSchemaSet", "publisher": "UserDataFunction", "activator": "Reflex",
        "record_store": "Lakehouse", "response_function": "UserDataFunction",
    }

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("viewer_public_version") != 1:
            raise ValueError("The ledger does not match the native Viewer implementation. Do not adopt resources.")
        self.state["viewer_public_version"] = 1
        self.kind = "viewer"
        self.catalog = read(self.folder / "catalog.json")
        catalog_items(self.catalog)
        fixture_inputs(read(self.folder / "fixtures.json"), self.catalog, str(uuid.UUID(int=0)),
                       "2026-01-01T00:00:00Z")

    @property
    def record_folder(self):
        return "ViewerRecommendations"

    @property
    def persisted_status(self):
        return "ready_for_serving"

    @property
    def action_parameters(self):
        return [(name, field, "String") for name, (field, _) in RESPONSE_PARAMETERS.items()]

    def outcome_id(self, payload):
        return recommendation_id(payload)

    def make_outcome(self, payload, actual):
        return make_recommendations(payload, self.catalog, self.state["activator_ids"]["rule"])

    def check_outcome(self, actual, expected):
        return check_recommendations(actual, expected)

    def preflight(self, resuming=False):
        if self.config["auth_provider"] != "azure_cli":
            raise ValueError("Native Viewer UDF invocation requires azure_cli and the documented Power BI audience.")
        result = Deployment.preflight(self, resuming)
        self.api.tokens.token(POWERBI)
        return result

    def udf_definition(self):
        source = (self.folder / "function_app.py").read_text(encoding="utf-8").replace(
            "# ${viewer_helpers}", (ROOT / "viewer_records.py").read_text(encoding="utf-8"),
        )
        return publisher_definition(
            self.folder, self.config, self.state["items"]["schema"], FUNCTION,
            "Publish the original five viewer interaction facts without calculating recommendations.", PARAMETERS,
            source=source,
        )

    def response_source(self):
        source = (self.folder / "response-function.py").read_text(encoding="utf-8")
        source = source.replace("# ${viewer_helpers}", (ROOT / "viewer_records.py").read_text(encoding="utf-8"))
        for marker, value in {
            "rule_id": self.state["activator_ids"]["rule"], "lakehouse_files_endpoint": self.files_endpoint(),
            "catalog_json": repr(json.dumps(self.catalog, allow_nan=False)),
        }.items():
            source = source.replace("${" + marker + "}", value)
        if "${" in source:
            raise ValueError("The Viewer response has unresolved bindings.")
        ast.parse(source)
        return source

    def response_udf_definition(self):
        declared = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/userDataFunction/definition/1.1.0/schema.json",
            "runtime": "PYTHON",
            "connectedDataSources": [{
                "alias": "RecommendationData", "artifactId": self.state["items"]["record_store"],
                "artifactType": "Lakehouse", "workspaceId": self.config["workspace_id"],
            }],
            "functions": [{"name": RESPONSE, "description": "Persist three content-based session recommendations.",
                           "isPublicEndpointEnabled": True}],
            "libraries": {"public": [], "private": []},
        }
        metadata = {"runtime": "PYTHON", "functionsMetadata": [{
            "name": RESPONSE, "scriptFile": "function_app.py", "bindings": [
                {"methods": ["POST"], "route": "", "authLevel": "Anonymous", "name": "req",
                 "direction": "In", "type": "HttpTrigger"},
                {"itemType": None, "subType": "FabricLakehouseClient", "alias": "RecommendationData",
                 "name": "recommendationLakehouse", "direction": "In", "type": "FabricItem"},
            ],
            "fabricProperties": {
                "fabricMetadataSchemaVersion": "1.1.0", "fabricFunctionReturnType": "dict",
                "fabricFunctionParameters": [
                    {"name": name, "dataType": kind} for name, (_, kind) in RESPONSE_PARAMETERS.items()
                ],
            },
        }]}
        return {"parts": [part("definition.json", declared), part("function_app.py", self.response_source()),
                          part("resources/functions.json", metadata)]}

    def activator_entities(self, should_run=True):
        return activator_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"], [],
            should_run=should_run, action={
                "item_id": self.state["items"]["response_function"], "item_type": "UserDataFunctions",
                "job_type": "Execute", "function": RESPONSE, "parameters": self.action_parameters,
            },
        )

    def creation_request(self, role):
        if role == "publisher":
            return self.base + "userDataFunctions", {
                "displayName": self.config["resource_names"][role], "description": self.owner,
                "definition": self.udf_definition(),
            }
        if role == "activator":
            return self.base + "items", {
                "displayName": self.config["resource_names"][role], "description": self.owner,
                "type": "Reflex", "definition": {"parts": [part("ReflexEntities.json", self.activator_entities(False))]},
            }
        return super().creation_request(role)

    def get_definition(self, role):
        if role == "publisher":
            reply = self.api.request("POST", self.base + f"userDataFunctions/{self.state['items'][role]}/getDefinition")
            return self.wait(reply, {}, need_result=True)["definition"]
        return super().get_definition(role)

    def resume_pending(self):
        pending = self.state.get("pending", {})
        failure = pending.get("operation", {})
        error = failure.get("error")
        if (pending.get("role") != "publisher" or failure.get("status") != "Failed"
                or not isinstance(error, dict) or error.get("errorCode") != "MetadataValidationError"):
            return super().resume_pending()
        operation_id = pending.get("operation_headers", {}).get("x-ms-operation-id")
        if (pending.get("actual_status") != 202 or not operation_id
                or pending.get("location") != FABRIC + "/v1/operations/" + str(uuid.UUID(operation_id))):
            raise ValueError("The failed publisher has no exact public operation identity. Retain the attempt.")
        address, corrected = self.creation_request("publisher")
        if address != pending["path"] or corrected == pending["request"]:
            raise ValueError("Correct the rejected publisher definition before resuming; do not replay it.")
        observed = self.api.request("GET", pending["location"]).body
        observed_error = observed.get("error")
        if (observed.get("status") != "Failed"
                or not isinstance(observed_error, dict) or observed_error.get("errorCode") != "MetadataValidationError"):
            raise ValueError("The publisher operation is not a confirmed terminal validation failure.")
        inventory = self.api.list_all(self.base + "items")
        if any(item["displayName"].casefold() == self.config["resource_names"]["publisher"].casefold()
               and item.get("type") in (None, self.resource_types["publisher"]) for item in inventory):
            raise ValueError("A publisher item exists after the failed create. Retain it; no adoption or retry is allowed.")
        failed = copy.deepcopy(pending)
        failed.update(operation=observed, replacement_reason="corrected_native_udf_definition")
        self.state.setdefault("failed_creates", []).append(failed)
        self.state.pop("pending")
        self.save()

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy all five Viewer resources before testing.")
        self.verify_schema()
        self.roles()
        verify_publisher_definition(self)
        self.verify_response()
        self.check_activator(self.get_definition("activator"))
        definition = self.state["definition_backup"]["response_function"]
        result = {
            "status": "viewer_public_provisioning_verified_e2e_pending", "items": self.state["items"],
            "source_binding_verified": True, "e2e_verified": False, "eventhouse_subscription_configured": False,
            "udf_metadata_readback": self.state["udf_metadata_readback"],
            "response_metadata_readback": (
                "returned_and_verified" if any(entry["path"] in ("resources/functions.json", "resources\\functions.json")
                                              for entry in definition["parts"]) else "not_returned_by_public_definition"
            ),
            "trained_model": False, "serving_application_updated": False,
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Viewer prepares its complete native response, not unused history containers.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the pending create without repeating it.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("record_store")
        self.prepare_record_store()
        self.create("response_function")
        self.verify_response()
        self.create("publisher")
        self.create("activator")
        self.start_activator()
        result = self.verify_provisioning()
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Viewer recommendations do not use Teams.")
        if self.state.get("test_run"):
            raise ValueError("This Viewer deployment already has a source attempt. Do not replay its interactions.")
        result = self.verify_provisioning()
        if self.list_records():
            raise ValueError("A fresh Viewer source test requires an empty owned recommendation folder.")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        run_id = str(uuid.uuid4())
        rows = fixture_inputs(read(self.folder / "fixtures.json"), self.catalog, run_id, now)
        attempt = {"run_id": run_id, "started_at": now, "inputs": rows, "invocations": [],
                   "phase": "invoking_native_publisher"}
        self.state["test_run"] = attempt
        self.save()
        for row in rows:
            attempt["pending_input"] = row["fixture_id"]
            self.save()
            reply = self.api.request(
                "POST", self.base + f"userDataFunctions/{self.state['items']['publisher']}/functions/{FUNCTION}/invoke",
                {name: row["event"][field] for name, (field, _) in PARAMETERS.items()},
            )
            attempt["last_response"] = {"http_status": reply.status, "body": reply.body}
            self.save()
            output = invocation_output(reply)
            if any(saved["response"]["invocationId"] == reply.body["invocationId"] for saved in attempt["invocations"]):
                raise ValueError("The Viewer publisher returned a repeated invocation identity.")
            attempt["invocations"].append({"fixture_id": row["fixture_id"], "response": reply.body, "output": output})
            attempt.pop("pending_input")
            self.save()
        attempt["phase"] = "native_publication_requests_succeeded_delivery_pending"
        result.update(
            status="source_triggered_verification_pending", source_triggered=True, source_run_id=run_id,
            source_execution_verified=True, publication_requests=len(rows), events_published=None,
            expected_interactions=rows, outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("Viewer recommendations do not use Teams.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if not attempt:
            return result
        if attempt.get("pending_input") or len(attempt["invocations"]) != len(attempt["inputs"]):
            raise ValueError("The Viewer source run is incomplete or ambiguous. Retain it without replay.")
        rows = attempt["inputs"]
        for row, invocation in zip(rows, attempt["invocations"]):
            if invocation["fixture_id"] != row["fixture_id"]:
                raise ValueError("The saved Viewer source evidence does not match its interactions.")
            invocation_output(Reply(200, {}, invocation["response"]))
        result.update(source_run_id=attempt["run_id"], source_execution_verified=True,
                      publication_requests=len(attempt["invocations"]), events_published=None)
        return self.verify_business_outcomes(
            attempt, result, [row["event"] for row in rows if row["expected"] is not None],
            [row["event"] for row in rows if row["expected"] is None],
        )
