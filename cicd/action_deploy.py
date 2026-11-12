"""Public native Fabric actions with independently read operational results."""

import ast
import datetime
from decimal import Decimal, InvalidOperation
import json
import re
import struct
import time
from urllib.parse import urlencode, urlsplit
import uuid

from business_records import check_record, make_record, record_contract, record_id, utc_time
from public_api import ONELAKE, PublicApiError
from public_consumers import ActivatorDeployment, activator_entities, definition_part, matches_definition, part
from public_deploy import Deployment, ROOT, canonical_scenario_id

PAYMENT_PARAMETERS = {
    "transactionId": ("transaction_id", "str"), "paymentMethod": ("payment_method", "str"),
    "amount": ("amount", "float"), "currency": ("currency", "str"),
    "storeId": ("store_id", "str"), "processedAt": ("processed_at", "str"),
}
REVIEW_FUNCTION = "register_payment_review"
STORAGE_HEADERS = {"x-ms-version": "2021-06-08"}


def query_rows(response):
    tables = response.get("tables") if isinstance(response, dict) else None
    if not isinstance(tables, list) or len(tables) != 1:
        raise ValueError("The public activation query must return exactly one result table.")
    table = tables[0]
    columns = [column["columnName"] for column in table["columns"]]
    if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in table["rows"]):
        raise ValueError("The activation query returned ambiguous columns or row lengths.")
    return [dict(zip(columns, row)) for row in table["rows"]]


class ActionDeployment(ActivatorDeployment):
    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        self.kind = "inventory" if canonical_scenario_id(config["scenario_id"]) == "low-stock-threshold" else "payment"
        self.source_only = False
        self.state["activator_ids"].setdefault("fabric_action", str(uuid.uuid4()))

    @property
    def action_role(self):
        return "response_pipeline" if self.kind == "inventory" else "response_function"

    @property
    def record_folder(self):
        return record_contract(self.kind)[3]

    @property
    def action_parameters(self):
        return (
            [(name, name, "Number" if name in ("current_stock", "threshold") else "String")
             for name in record_contract(self.kind)[0]] if self.kind == "inventory" else
            [(name, field, "Number" if kind == "float" else "String")
             for name, (field, kind) in PAYMENT_PARAMETERS.items()]
        )

    @property
    def persisted_status(self):
        return record_contract(self.kind)[2]

    def outcome_id(self, payload):
        return record_id(self.kind, payload)

    def make_outcome(self, payload, actual):
        return make_record(
            self.kind, payload, self.state["activator_ids"]["rule"],
            actual.get("pipeline_run_id") if self.kind == "inventory" else None,
        )

    def check_outcome(self, actual, expected):
        return check_record(self.kind, actual, expected)

    def preflight(self, resuming=False):
        return Deployment.preflight(self, resuming)

    def activator_entities(self, should_run=True):
        action = {
            "item_id": self.state["items"][self.action_role],
            "item_type": "Pipeline" if self.kind == "inventory" else "UserDataFunctions",
            "job_type": "Pipeline" if self.kind == "inventory" else "Execute",
            "parameters": self.action_parameters,
        }
        if self.kind == "payment":
            action["function"] = REVIEW_FUNCTION
        return activator_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"], [],
            should_run=should_run, action=action,
        )

    def response_source(self):
        name = "response-notebook.py" if self.kind == "inventory" else "response-function.py"
        source = (self.folder / name).read_text(encoding="utf-8")
        source = source.replace("# ${record_helpers}", (ROOT / "business_records.py").read_text(encoding="utf-8"))
        for marker, value in {
            "workspace_id": self.config["workspace_id"], "lakehouse_id": self.state["items"]["record_store"],
            "rule_id": self.state["activator_ids"]["rule"],
        }.items():
            source = source.replace("${" + marker + "}", value)
        if self.kind == "payment":
            source = source.replace("${lakehouse_files_endpoint}", self.files_endpoint())
        if "${" in source:
            raise ValueError("The native response has unresolved resource bindings.")
        ast.parse(source)
        return source

    def files_endpoint(self):
        store = self.state.get("record_store_readback")
        if store is None:
            store = self.api.request("GET", self.base + "lakehouses/" + self.state["items"]["record_store"]).body
            self.check_item("record_store", store)
            self.state["record_store_readback"] = store
            self.save()
        endpoint = store["properties"]["oneLakeFilesPath"]
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
                or parsed.query or parsed.fragment
                or not re.fullmatch(r"(?:[a-z][a-z0-9]*-)?onelake\.dfs\.fabric\.microsoft\.com", parsed.netloc)
                or parsed.path.rstrip("/") != "/" + self.config["workspace_id"] + "/"
                + self.state["items"]["record_store"] + "/Files"):
            raise ValueError("The public Lakehouse readback did not identify its exact public OneLake Files endpoint.")
        return endpoint.rstrip("/")

    def pipeline_content(self):
        fields = record_contract("inventory")[0]
        parameters = {
            name: {
                "type": "int" if name in ("current_stock", "threshold") else "string",
                "value": {"type": "Expression", "value": "@pipeline().parameters." + name},
            } for name in fields
        }
        parameters["pipeline_run_id"] = {
            "type": "string", "value": {"type": "Expression", "value": "@pipeline().RunId"},
        }
        return {"properties": {
            "parameters": {
                name: {"type": "Int" if name in ("current_stock", "threshold") else "String"} for name in fields
            },
            "activities": [{
                "name": "Persist replenishment request", "type": "TridentNotebook", "dependsOn": [],
                "policy": {"timeout": "0.00:30:00", "retry": 0, "retryIntervalInSeconds": 30},
                "typeProperties": {"notebookId": self.state["items"]["response_notebook"],
                                   "workspaceId": self.config["workspace_id"], "parameters": parameters},
            }],
        }}

    def response_udf_definition(self):
        declared = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/userDataFunction/definition/1.1.0/schema.json",
            "runtime": "PYTHON",
            "connectedDataSources": [{
                "alias": "CaseRecords", "artifactId": self.state["items"]["record_store"],
                "artifactType": "Lakehouse", "workspaceId": self.config["workspace_id"],
            }],
            "functions": [{"name": REVIEW_FUNCTION, "description": "Persist a payment case pending review.",
                           "isPublicEndpointEnabled": True}],
            "libraries": {"public": [], "private": []},
        }
        metadata = {"runtime": "PYTHON", "functionsMetadata": [{
            "name": REVIEW_FUNCTION, "scriptFile": "function_app.py",
            "bindings": [
                {"methods": ["POST"], "route": "", "authLevel": "Anonymous", "name": "req",
                 "direction": "In", "type": "HttpTrigger"},
                {"itemType": None, "subType": "FabricLakehouseClient", "alias": "CaseRecords",
                 "name": "caseLakehouse", "direction": "In", "type": "FabricItem"},
            ],
            "fabricProperties": {
                "fabricMetadataSchemaVersion": "1.1.0", "fabricFunctionReturnType": "dict",
                "fabricFunctionParameters": [
                    {"name": name, "dataType": kind} for name, (_, kind) in PAYMENT_PARAMETERS.items()
                ],
            },
        }]}
        return {"parts": [part("definition.json", declared), part("function_app.py", self.response_source()),
                          part("resources/functions.json", metadata)]}

    def creation_request(self, role):
        if role == "schema":
            return Deployment.creation_request(self, role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "record_store":
            return self.base + "lakehouses", body
        if role == "response_notebook":
            body["definition"] = {"format": "fabricGitSource", "parts": [
                part("notebook-content.py", self.response_source()),
            ]}
            return self.base + "notebooks", body
        if role == "response_pipeline":
            body["definition"] = {"parts": [part("pipeline-content.json", self.pipeline_content())]}
            return self.base + "dataPipelines", body
        if role == "response_function":
            body["definition"] = self.response_udf_definition()
            return self.base + "userDataFunctions", body
        raise ValueError("Unknown native response resource role.")

    def list_records(self):
        workspace = self.config["workspace_id"]
        directory = self.state["items"]["record_store"] + "/Files/" + self.record_folder
        query = {"resource": "filesystem", "directory": directory, "recursive": "false", "maxResults": "500"}
        rows, seen = [], set()
        while True:
            reply = self.api.request("GET", ONELAKE + "/" + workspace + "?" + urlencode(query), headers=STORAGE_HEADERS)
            paths = reply.body.get("paths") if isinstance(reply.body, dict) else None
            if not isinstance(paths, list):
                raise ValueError("OneLake did not return the documented file listing.")
            for entry in paths:
                name = entry["name"]
                prefixes = (directory + "/", self.config["resource_names"]["record_store"] + ".Lakehouse/Files/"
                            + self.record_folder + "/")
                if (entry.get("isDirectory") not in (None, False, "false")
                        or not any(name.startswith(prefix) for prefix in prefixes)
                        or not re.fullmatch(r"[a-f0-9]{64}\.json", name.rsplit("/", 1)[-1])):
                    raise ValueError("The owned operational folder contains an unexpected path.")
                path = name.rsplit("/", 1)[-1]
                if any(previous["file_name"] == path for previous in rows):
                    raise ValueError("OneLake repeated a record path.")
                address = ONELAKE + "/" + workspace + "/" + directory + "/" + path
                content = self.api.request("GET", address, headers=STORAGE_HEADERS)
                rows.append({"file_name": path, "etag": content.headers.get("etag"), "record": content.body})
            continuation = reply.headers.get("x-ms-continuation")
            if not continuation:
                return rows
            if continuation in seen or len(seen) >= 100:
                raise ValueError("OneLake repeated or exceeded its bounded continuation.")
            seen.add(continuation)
            query["continuation"] = continuation

    def prepare_record_store(self):
        if self.state.get("record_folder_created"):
            return
        address = (ONELAKE + "/" + self.config["workspace_id"] + "/" + self.state["items"]["record_store"]
                   + "/Files/" + self.record_folder + "?resource=directory")
        try:
            reply = self.api.request("PUT", address, headers={**STORAGE_HEADERS, "If-None-Match": "*"})
            if reply.status != 201:
                raise RuntimeError("OneLake did not confirm creation of the operational folder.")
        except PublicApiError as error:
            if error.status not in (409, 412) or error.code not in ("PathAlreadyExists", "ConditionNotMet"):
                raise
            # Only this positively owned Lakehouse can contain a resumed create.
            self.list_records()
        self.state["record_folder_created"] = True
        self.save()

    def verify_response(self):
        store = self.api.request("GET", self.base + "lakehouses/" + self.state["items"]["record_store"]).body
        self.check_item("record_store", store)
        self.state["record_store_readback"] = store
        backups = self.state.setdefault("definition_backup", {})
        if self.kind == "inventory":
            notebook = self.get_definition("response_notebook")
            if ast.dump(ast.parse(definition_part(notebook, "notebook-content.py"))) != ast.dump(ast.parse(self.response_source())):
                raise ValueError("The persisted request worker or OneLake destination differs.")
            pipeline = self.get_definition("response_pipeline")
            if not matches_definition(self.pipeline_content(), json.loads(definition_part(pipeline, "pipeline-content.json"))):
                raise ValueError("The pipeline's seven parameters, native Notebook activity or RunId mapping differs.")
            backups.update(response_notebook=notebook, response_pipeline=pipeline)
        else:
            actual = self.get_definition("response_function")
            expected = self.response_udf_definition()
            paths = [entry["path"] for entry in actual["parts"]]
            allowed = {"definition.json", "function_app.py", ".platform",
                       "resources/functions.json", "resources\\functions.json"}
            if len(set(paths)) != len(paths) or set(paths) - allowed:
                raise ValueError("The response definition contains duplicate or unreviewed parts.")
            declared = json.loads(definition_part(expected, "definition.json"))
            received = json.loads(definition_part(actual, "definition.json"))
            if received.get("libraries") == {"public": [
                {"name": "fabric-user-data-functions", "type": "PYPI", "version": "1.0"},
            ], "private": []}:
                declared["libraries"] = received["libraries"]
            if not matches_definition(declared, received):
                raise ValueError("The response function registration, public endpoint or Lakehouse connection differs.")
            if ast.dump(ast.parse(definition_part(actual, "function_app.py"))) != ast.dump(ast.parse(self.response_source())):
                raise ValueError("The persisted response function or OneLake destination differs.")
            metadata = [path for path in paths if path in ("resources/functions.json", "resources\\functions.json")]
            if len(metadata) > 1:
                raise ValueError("Ambiguous response function metadata.")
            if metadata and not matches_definition(
                json.loads(definition_part(expected, "resources/functions.json")),
                json.loads(definition_part(actual, metadata[0])),
            ):
                raise ValueError("The registered response function's input parameters differ.")
            backups["response_function"] = actual
        self.list_records()
        self.save()

    def verify_business_outcomes(self, attempt, result, expected, excluded=()):
        if self.source_only:
            self.state.update(status=result["status"], verification=result)
            self.save()
            return result
        deadline = time.monotonic() + (1200 if self.kind == "inventory" else 600)
        started = utc_time(attempt["started_at"])
        target_ids = {self.outcome_id(payload): payload for payload in expected}
        if not target_ids:
            raise ValueError("A business-outcome test requires at least one matching event.")
        excluded_ids = {self.outcome_id(payload) for payload in excluded}
        table = "ruleactivations_" + uuid.UUID(self.state["activator_ids"]["rule"]).hex
        address = self.base + f"reflexes/{self.state['items']['activator']}/queryActivations"
        observation_started = None
        while True:
            files = self.list_records()
            observed = {row["file_name"].removesuffix(".json"): row for row in files}
            attempt["operational_records"] = files
            if set(observed) & excluded_ids or set(observed) - target_ids.keys():
                self.save()
                raise ValueError("An excluded finding or an unexpected business record was persisted.")
            for identifier, row in observed.items():
                payload = target_ids[identifier]
                if self.kind == "payment":
                    amount = row["record"]["amount"]
                    allowed = (float(payload["amount"]), struct.unpack("!f", struct.pack("!f", payload["amount"]))[0])
                    if type(amount) is not float or amount not in allowed:
                        raise ValueError("The stored case changed the original payment amount.")
                    payload = {**payload, "amount": amount}
                wanted = self.make_outcome(payload, row["record"])
                self.check_outcome(row["record"], wanted)
                if utc_time(row["record"]["created_at"]) < started:
                    raise ValueError("A pre-existing record cannot certify this source run.")
            tables = query_rows(self.api.request("POST", address, {"query": ".show tables"}).body)
            activations = []
            activation_keys = set()
            if any(row["TableName"] == table for row in tables):
                activations = query_rows(self.api.request("POST", address, {
                    "query": table + " | where ['___time'] >= datetime(" + attempt["started_at"] + ") | take 1000",
                }).body)
                if len(activations) >= 1000:
                    raise ValueError("Activation evidence exceeded its bounded result window.")
                for row in activations:
                    prefix = "System.Action.FabricItem."
                    item_types = ("Pipeline",) if self.kind == "inventory" else ("UserDataFunctions", "FunctionSet")
                    if (row["System.RuleId"] != self.state["activator_ids"]["rule"]
                            or row["System.ActionType"] != "FabricItem"
                            or row[prefix + "WorkspaceId"] != self.config["workspace_id"]
                            or row[prefix + "ItemId"] != self.state["items"][self.action_role]
                            or row[prefix + "FabricItemConnectionDocumentId"] != self.state["activator_ids"]["fabric_action"]
                            or row[prefix + "ItemType"] not in item_types
                            or row[prefix + "JobType"] != ("Pipeline" if self.kind == "inventory" else "Execute")):
                        raise ValueError("The observed rule invocation is not the required native Fabric action.")
                    bindings = self.action_parameters
                    action_payload = {}
                    for name, field, kind in bindings:
                        value = row[prefix + "Parameters." + name + "." + kind]
                        if kind == "Number":
                            try:
                                number = Decimal(str(value))
                            except InvalidOperation as error:
                                raise ValueError("The public action contains an invalid numeric parameter.") from error
                            if not number.is_finite():
                                raise ValueError("The public action contains a nonfinite numeric parameter.")
                            value = float(number) if self.kind == "payment" else int(number)
                            if self.kind == "inventory" and number != value:
                                raise ValueError("The action changed a stock integer.")
                        action_payload[field] = value
                    key = self.outcome_id(action_payload)
                    if key not in target_ids:
                        raise ValueError("An excluded or unexpected finding reached the native response rule.")
                    activation_keys.add(key)
                    wanted_payload = target_ids[key]
                    if self.kind == "payment":
                        allowed = (
                            float(wanted_payload["amount"]),
                            struct.unpack("!f", struct.pack("!f", wanted_payload["amount"]))[0],
                        )
                        if action_payload["amount"] not in allowed:
                            raise ValueError("The action changed the original payment amount.")
                        wanted_payload = {**wanted_payload, "amount": action_payload["amount"]}
                        if key in observed and observed[key]["record"]["amount"] != action_payload["amount"]:
                            raise ValueError("The committed case differs from the actual action parameter.")
                    if action_payload != wanted_payload:
                        raise ValueError("The actual Fabric action changed the original event context.")
                identities = [row["___id"] for row in activations]
                if any(not isinstance(identifier, str) or not identifier for identifier in identities) or len(set(identities)) != len(identities):
                    raise ValueError("The public activation query returned duplicate or missing execution identities.")
            attempt["public_activations"] = activations
            jobs = self.api.list_all(self.base + f"items/{self.state['items'][self.action_role]}/jobs/instances")
            current_jobs = []
            for job in jobs:
                instant = datetime.datetime.fromisoformat(job["startTimeUtc"].replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=datetime.timezone.utc)
                if instant >= started:
                    if (job["itemId"] != self.state["items"][self.action_role]
                            or job["jobType"] != ("Pipeline" if self.kind == "inventory" else "Execute")):
                        raise ValueError("The public consumer job changed its exact target or job type.")
                    current_jobs.append(job)
            jobs = current_jobs
            attempt["consumer_jobs"] = jobs
            if any(job["status"] in ("Failed", "Cancelled", "Deduped") for job in jobs):
                attempt["pipeline_jobs"] = jobs if self.kind == "inventory" else []
                self.save()
                raise RuntimeError(
                    "A native replenishment pipeline failed or was skipped; retain its evidence."
                    if self.kind == "inventory" else
                    "A native response function failed or was skipped; retain its evidence."
                )
            matching_jobs = []
            if self.kind == "inventory":
                for file in files:
                    matches = [job for job in jobs if job["id"] == file["record"]["pipeline_run_id"]]
                    if len(matches) > 1 or matches and matches[0]["itemId"] != self.state["items"]["response_pipeline"]:
                        raise ValueError("A request has no exact native pipeline execution.")
                    matching_jobs.extend(matches)
            attempt["pipeline_jobs"] = jobs if self.kind == "inventory" else []
            complete = set(observed) == set(target_ids) and activation_keys == set(target_ids)
            if self.kind == "inventory":
                complete = (
                    complete and len(matching_jobs) == len(files)
                    and all(job["status"] == "Completed" and not job.get("failureReason") for job in matching_jobs)
                )
            else:
                complete = (
                    complete and len(jobs) >= len(target_ids)
                    and all(job["status"] == "Completed" and not job.get("failureReason") for job in jobs)
                )
            self.save()
            if complete:
                if observation_started is None:
                    observation_started = time.monotonic()
                if time.monotonic() - observation_started >= 300:
                    break
            else:
                observation_started = None
            if time.monotonic() >= deadline:
                raise RuntimeError("Native action execution or persistent business outcomes remain incomplete. Retain this run.")
            self.api.sleep(5)
        result.update(
            status=self.kind + "_public_business_action_e2e_verified_no_event_analysis",
            e2e_verified=True, events_published=len(target_ids), operational_records_verified=len(target_ids),
            public_rule_executions=len(activations), consumer_execution_verified=True,
            persisted_outcome_status=self.persisted_status, excluded_records_observed=0,
            outcome_verifier="public_onelake_files_and_native_action_evidence", observation_seconds=300,
            consumer_jobs_completed=len(jobs),
        )
        attempt["phase"] = "e2e_verified"
        self.state.update(status=result["status"], verification=result)
        self.save()
        return result
