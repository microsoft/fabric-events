"""Public-only Business Events deployment. Incomplete scenarios fail closed before writes."""

import argparse
import base64
import copy
import json
from pathlib import Path
import re
import sys
import time
import uuid

from public_api import FABRIC, ONELAKE, PublicApi, public_url

ROOT = Path(__file__).resolve().parent
FOUNDATIONS = {"schema": "EventSchemaSet", "eventhouse": "Eventhouse", "database": "KQLDatabase"}


def canonical_scenario_id(identifier):
    if identifier == "revenue":
        return "revenue-below-target"
    if identifier == "inventory":
        return "low-stock-threshold"
    if identifier == "sales-volume":
        return "sales-volume-alert"
    if identifier == "discount":
        return "discount-approval-routing"
    if identifier == "fraud":
        return "fraud-response-before-fulfillment"
    return "real-time-stream-alert" if identifier == "high-value-payment" else identifier


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sql_database_destination(deployment, role):
    item = deployment.api.request(
        "GET", deployment.base + "sqlDatabases/" + deployment.state["items"][role],
    ).body
    if deployment.check_item(role, item) != deployment.state["items"][role]:
        raise ValueError("The SQL database readback changed its exact identity.")
    properties = item.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("The SQL database did not return its documented connection properties.")
    server, name = properties.get("serverFqdn"), properties.get("databaseName")
    if (not isinstance(server, str) or not re.fullmatch(
            r"[a-z0-9-]+\.(?:[a-z0-9]+-)?database\.fabric\.microsoft\.com(?:,1433)?", server)
            or not isinstance(name, str) or not name.strip() or any(char in name for char in ";\r\n")):
        raise ValueError("The SQL database did not return its documented public server and database name.")
    return {"server": server, "database": name}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def validate_teams_chat_id(chat_id):
    if not isinstance(chat_id, str) or not re.fullmatch(r"[A-Za-z0-9_:@.+-]{1,256}", chat_id):
        raise ValueError("Provide the exact approved individual Teams chat ID, not a URL.")


def configuration(folder, path):
    value, scenario = read(path), read(folder / "scenario.json")
    if not isinstance(value, dict):
        raise ValueError("The public configuration must be a JSON object.")
    if value.get("scenario_id") is not None and canonical_scenario_id(value["scenario_id"]) != canonical_scenario_id(scenario["scenario_id"]):
        raise ValueError("Configuration belongs to a different scenario.")
    required = {
        "configuration_version", "scenario_id", "tenant_id", "workspace_id", "auth_provider",
        "resource_prefix", "event_name", "resource_names", "accept_preview_apis",
    }
    single_package = scenario.get("package_policy") == "single_current_package"
    if single_package:
        required.remove("configuration_version")
        if "configuration_version" in value and not isinstance(value["configuration_version"], str):
            raise ValueError("Provide a complete scenario configuration with valid field types.")
        value.pop("configuration_version", None)
    optional = {"$schema"}
    if canonical_scenario_id(scenario["scenario_id"]) == "abandoned-cart-recovery":
        required.add("test_recipient")
        recipient = value.get("test_recipient")
        if recipient is not None and (
            not isinstance(recipient, str) or len(recipient) > 254
            or not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", recipient)
        ):
            raise ValueError("test_recipient must be one approved mailbox or null before manual setup.")
    if canonical_scenario_id(scenario["scenario_id"]) in ("sales-volume-alert", "discount-approval-routing"):
        required.add("teams_recipients")
        if canonical_scenario_id(scenario["scenario_id"]) == "sales-volume-alert":
            optional.update(("teams_chat_id", "teams_verifier"))
    version = scenario.get("configuration_version", "2.0")
    if single_package and (required - value.keys() or value.keys() - required - optional):
        raise ValueError("Use the complete scenario configuration with its required fields.")
    if not single_package and (
        required - value.keys() or value.keys() - required - optional or value["configuration_version"] != version
    ):
        raise ValueError(f"Use the complete public configuration v{version}, not an archived configuration.")
    for key in ("tenant_id", "workspace_id"):
        if value[key] is not None:
            if not isinstance(value[key], str):
                raise ValueError(f"{key} must be a UUID string or null for an offline plan.")
            value[key] = str(uuid.UUID(value[key]))
    if value["auth_provider"] not in ("azure_cli", "fabric_cli"):
        raise ValueError("Choose azure_cli or fabric_cli explicitly.")
    if type(value["accept_preview_apis"]) is not bool:
        raise ValueError("accept_preview_apis must be an explicit boolean.")
    if "teams_recipients" in value:
        recipients = value["teams_recipients"]
        if (not isinstance(recipients, list) or not 1 <= len(recipients) <= 10
                or any(not isinstance(recipient, str) or (
                    recipient != "@me" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient)
                ) for recipient in recipients)
                or len({recipient.casefold() for recipient in recipients}) != len(recipients)):
            raise ValueError("Provide 1-10 distinct Teams recipients: @me or explicit email addresses.")
    if value.get("teams_chat_id") is not None:
        validate_teams_chat_id(value["teams_chat_id"])
    if "teams_verifier" in value and value["teams_verifier"] not in ("graph", "windows_ui"):
        raise ValueError("Choose graph or windows_ui for teams_verifier.")
    if not isinstance(value["resource_prefix"], str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{2,39}", value["resource_prefix"],
    ) or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value["resource_prefix"], re.I):
        raise ValueError("resource_prefix requires 3-40 portable characters.")
    if not isinstance(value["event_name"], str) or not 3 <= len(value["event_name"]) <= 80 or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", value["event_name"],
    ):
        raise ValueError("event_name must be an Avro-qualified name.")
    names = value["resource_names"]
    if not isinstance(names, dict) or any(not isinstance(name, str) for name in names.values()):
        raise ValueError("resource_names must be a map of strings.")
    if set(names) != set(scenario["resource_roles"]) or len(set(n.casefold() for n in names.values())) != len(names):
        raise ValueError("Provide all distinct reviewed resource names.")
    for role, name in names.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{2,99}", name)
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name, re.I)):
            raise ValueError(f"Invalid portable resource name for {role}.")
    if scenario["source_backend"] == "native_udf" and len(names["schema"]) > 20:
        raise ValueError("The UDF schema connection alias must not exceed 20 characters.")
    stream_role = "publisher" if canonical_scenario_id(scenario["scenario_id"]) == "real-time-stream-alert" else (
        "eventstream" if canonical_scenario_id(scenario["scenario_id"]) in (
            "discount-approval-routing", "customer-loyalty-milestone",
        ) else None
    )
    if stream_role and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,99}", names[stream_role]):
        raise ValueError("Eventstream names require 3-100 letters, numbers or underscores, starting with a letter.")
    return value


def plan(folder, config):
    scenario = read(folder / "scenario.json")
    contracts = read(ROOT / "public_contracts.json")
    gaps = [
        gap for gap in contracts["full_deployment_gaps"]
        if gap["required_by"] == "all" or canonical_scenario_id(scenario["scenario_id"]) in [
            canonical_scenario_id(name) for name in (
                gap["required_by"] if isinstance(gap["required_by"], list) else [gap["required_by"]]
            )
        ]
    ]
    gaps = [
        gap for gap in gaps
        if not (
            gap.get("required_capability") == "business_event_analysis"
            and scenario.get("event_analysis", {}).get("scope") == "excluded"
        )
    ]
    result = {
        "scenario_id": scenario["scenario_id"], "api_policy": contracts["policy"], "cloud_writes": False,
        "tenant_id": config["tenant_id"], "workspace_id": config["workspace_id"],
        "full_deployment_status": "blocked_public_api_gap" if gaps else "pending_implementation",
        "public_api_gaps": gaps, "implemented_scope": "foundations",
        "implemented_resources": {role: config["resource_names"][role] for role in FOUNDATIONS
                                  if role in config["resource_names"]},
        "remaining_resources": {role: name for role, name in config["resource_names"].items() if role not in FOUNDATIONS},
        "unimplemented_steps": ["source_and_publisher_provisioning", "activator_actions",
                                "external_test_inputs", "full_consumer_evidence"],
        "publisher_backend": scenario["source_backend"], "event_name": config["event_name"],
        "eventhouse_subscription_configured": False, "e2e_verified": False,
        "preview_consent_required": True, "preview_consent_given": config["accept_preview_apis"],
        "public_references": contracts["operations"],
    }
    if "scenario_name" in scenario:
        result["scenario_name"] = scenario["scenario_name"]
    if "command_behavior" in scenario:
        result["command_behavior"] = scenario["command_behavior"]
    if "public_contract_review" in scenario:
        result["public_contract_review"] = scenario["public_contract_review"]
    if "business_response" in scenario:
        response = copy.deepcopy(scenario["business_response"])
        response["target_item_name"] = config["resource_names"][response["target_role"]]
        result["planned_business_response"] = response
    if "event_analysis" in scenario:
        result["event_analysis"] = scenario["event_analysis"]
    if "eventhouse_usage" in scenario:
        result["eventhouse_usage"] = scenario["eventhouse_usage"]
        if scenario["eventhouse_usage"]["implementation_status"] == "containers_only":
            result["unimplemented_steps"].insert(0, "source_kql_tables_and_notebook_binding")
    if scenario.get("implemented_scope") == "public_full_deployment":
        configured = bool(config["tenant_id"] and config["workspace_id"] and config["accept_preview_apis"])
        result.update(
            implemented_scope="full_deployment",
            full_deployment_status=("blocked_public_api_gap" if gaps else (
                "ready_to_deploy" if configured else "configuration_required")),
            implemented_resources=dict(config["resource_names"]), remaining_resources={}, unimplemented_steps=[],
            live_deployment_verified=False,
            test_requirements=["explicit_test_invocation", "exact_workspace_confirmation", "native_source_submission"],
            verification_requirements=["approved_teams_chat_id_or_windows_ui",
                                       "same_run_source_and_publication_and_Teams_delivery"],
        )
        if canonical_scenario_id(scenario["scenario_id"]) in ("real-time-stream-alert", "low-stock-threshold"):
            result["verification_requirements"] = [
                "original_native_publisher_execution", "exact_dynamic_event_parameter_binding",
                "public_owned_rule_execution_evidence", "same_run_committed_onelake_operational_records",
                "completed_native_pipeline_jobs" if canonical_scenario_id(scenario["scenario_id"]) == "low-stock-threshold"
                else "six_inputs_three_selected_cases_no_boundary_or_below_threshold_cases",
            ]
        if canonical_scenario_id(scenario["scenario_id"]) == "real-time-viewer-recommendations":
            result["verification_requirements"] = [
                "six_original_native_interactions", "watch_like_native_subscription_filter",
                "five_dynamic_udf_parameters", "public_owned_rule_and_consumer_execution_evidence",
                "three_immutable_ranked_recommendation_snapshots", "no_click_search_skip_responses",
                "persisted_recommendations_are_not_proof_of_serving_or_model_inference",
            ]
    if scenario.get("implemented_scope") == "public_hybrid_deployment":
        configured = bool(config["tenant_id"] and config["workspace_id"] and config["accept_preview_apis"])
        manual_role = {
            "revenue-below-target": "dashboard",
            "discount-approval-routing": "eventstream",
            "fraud-response-before-fulfillment": None,
            "abandoned-cart-recovery": None,
            "data-pipeline-failure-follow-up": None,
            "customer-loyalty-milestone": None,
        }[canonical_scenario_id(scenario["scenario_id"])]
        result.update(
            implemented_scope="hybrid_deployment",
            full_deployment_status="ready_for_manual_handoff" if configured else "configuration_required",
            implemented_resources={role: name for role, name in config["resource_names"].items() if role != manual_role},
            remaining_resources={manual_role: config["resource_names"][manual_role]} if manual_role else {},
            unimplemented_steps=["manual_real_time_dashboard_pin", "manual_kql_source_snapshots",
                                 "manual_publisher_activation", "manual_native_e2e_validation"],
            manual_setup_required=True, automated_e2e_test_available=False,
            automated_source_trigger_available=False,
        )
        if canonical_scenario_id(scenario["scenario_id"]) == "discount-approval-routing":
            result.update(
                implemented_resources={role: name for role, name in config["resource_names"].items()
                                       if role != "eventstream"},
                remaining_resources={"eventstream": config["resource_names"]["eventstream"]},
                unimplemented_steps=["manual_business_event_eventstream_subscription",
                                     "personal_teams_and_eventstream_delivery_evaluation"],
                automated_source_trigger_available=True,
                test_requirements=["explicit_test_invocation", "exact_workspace_confirmation",
                                   "native_udf_policy_evaluation"],
                verification_requirements=["same_run_native_udf_results", "six_individual_teams_notifications",
                                           "six_complete_eventstream_payloads", "no_within_policy_events"],
            )
        if canonical_scenario_id(scenario["scenario_id"]) == "fraud-response-before-fulfillment":
            result.update(
                unimplemented_steps=["manual_power_automate_actions_and_decision_rules",
                                     "manual_test_oms_setup_and_order_registration",
                                     "independent_flow_and_oms_outcome_verification"],
                automated_source_trigger_available=True, manual_consumer_actions_verified=False,
                test_requirements=["explicit_test_invocation", "exact_workspace_confirmation",
                                   "register_the_prepared_synthetic_orders_before_invoking_native_udf"],
                verification_requirements=["six_original_five_field_decision_events",
                                           "one_matching_flow_per_order", "same_run_persisted_oms_states",
                                           "held_and_review_orders_cannot_be_fulfilled",
                                           "review_requested_is_not_review_completed"],
            )
        if canonical_scenario_id(scenario["scenario_id"]) == "abandoned-cart-recovery":
            result.update(
                unimplemented_steps=["manual_sql_schema_scripts_and_connection_permissions",
                                     "manual_power_automate_email_and_sql_actions",
                                     "independent_email_receipt_and_sql_task_evaluation"],
                automated_source_trigger_available=True, manual_consumer_actions_verified=False,
                test_requirements=["explicit_test_invocation", "exact_workspace_confirmation",
                                   "approved_test_recipient", "complete_both_manual_responses_before_test"],
                verification_requirements=["six_original_six_field_cart_events",
                                           "three_recovery_emails_strictly_above_30",
                                           "six_sql_tasks_correlated_by_cart_id",
                                           "no_boundary_below_or_zero_value_emails",
                                           "no_claim_of_offer_eligibility_or_conversion"],
            )
        if canonical_scenario_id(scenario["scenario_id"]) == "data-pipeline-failure-follow-up":
            result.update(
                unimplemented_steps=["manual_sql_schema_initialization", "independent_sql_case_evaluation"],
                automated_source_trigger_available=True,
                test_requirements=["explicit_test_invocation", "exact_workspace_confirmation",
                                   "initialize_sql_before_starting_the_original_notebook"],
                verification_requirements=["six_original_workload_outcomes", "native_failed_status_filter",
                                           "two_pending_triage_sql_cases", "no_completed_or_skipped_cases",
                                           "same_run_response_jobs_and_exact_persisted_facts"],
            )
        if canonical_scenario_id(scenario["scenario_id"]) == "customer-loyalty-milestone":
            manual_roles = {"publisher", "eventstream"}
            result.update(
                implemented_resources={role: name for role, name in config["resource_names"].items()
                                       if role not in manual_roles},
                remaining_resources={role: name for role, name in config["resource_names"].items()
                                     if role in manual_roles},
                unimplemented_steps=["manual_catalog_notebook_execution", "manual_publisher_setup_and_activation",
                                     "manual_business_event_eventstream_subscription",
                                     "manual_reference_join_filters_projection_and_delta_destinations",
                                     "manual_native_kql_observation_and_delivery_evaluation"],
                test_requirements=["fresh_customer_ids", "observe_4900_baselines_in_activator",
                                   "submit_native_kql_balances_only_after_consumer_setup"],
                verification_requirements=["three_gold_crossings", "active_campaign_candidate",
                                           "inactive_campaign_excluded", "unmatched_campaign_review",
                                           "no_boundary_below_or_continued_above_threshold_publications"],
                campaign_preparation=scenario["campaign_preparation"],
            )
    return result


def source_tables(script):
    tables = {}
    for name, columns in re.findall(
        r"\.create(?:-merge)?\s+table\s+(\['[^']+'\]|[A-Za-z0-9_]+)\s*\(([^)]+)\)", script, re.I,
    ):
        name = name.removeprefix("['").removesuffix("']")
        if name in tables:
            raise ValueError("Source definition contains repeated table declarations.")
        tables[name] = [tuple(piece.strip().lower() for piece in column.split(":")) for column in columns.split(",")]
    return tables


def schema_definition(folder, config):
    avro = copy.deepcopy(read(folder / "schema.avsc"))
    avro["name"] = config["event_name"]
    event = config["event_name"]
    envelope = {
        key: {"type": "timestamp" if key == "time" else "string", "required": True}
        for key in ("id", "type", "source", "specversion", "time", "datacontenttype", "dataversion", "dataschema")
    }
    envelope["type"]["value"] = event
    definition = {
        "eventTypes": [{
            "id": event, "category": "BusinessEventType", "format": "CloudEvents/1.0",
            "envelopeMetadata": envelope, "schemaUrl": "#/schemas/" + event, "schemaFormat": "Avro/1.12.0",
        }],
        "schemas": [{
            "id": event, "format": "Avro/1.12.0",
            "versions": [{"id": "v1", "format": "Avro/1.12.0", "schema": json.dumps(avro)}],
        }],
    }
    return {"parts": [{
        "path": "EventSchemaSetDefinition.json", "payloadType": "InlineBase64",
        "payload": base64.b64encode(json.dumps(definition).encode("utf-8")).decode("ascii"),
    }]}


class Deployment:
    resource_types = FOUNDATIONS

    def __init__(self, folder, config, state_path, api=None):
        self.folder, self.config, self.path = Path(folder), config, Path(state_path)
        self.owner = "Managed by fabric-events/cicd public-only/" + config["scenario_id"] + "."
        self.state = read(self.path) if self.path.exists() else {
            "configuration": config, "api_policy": "documented_public_apis_only", "items": {}, "status": "new",
        }
        recorded = self.state.get("configuration")
        ignored = {"teams_chat_id", "teams_verifier"}
        if canonical_scenario_id(config["scenario_id"]) == "abandoned-cart-recovery":
            ignored.add("test_recipient")
        if read(self.folder / "scenario.json").get("package_policy") == "single_current_package":
            ignored.add("configuration_version")
        # Source-input and readback settings do not rebind deployed resources.
        if (not isinstance(recorded, dict)
                or {key: value for key, value in recorded.items() if key not in ignored}
                != {key: value for key, value in config.items() if key not in ignored}
                or self.state.get("api_policy") != "documented_public_apis_only"):
            raise ValueError("The ledger is not this exact public-only configuration.")
        if self.state.get("deployment_lifecycle") == "test_resources_deleted":
            raise ValueError("These resources were deleted. Never resume a cleaned ledger.")
        if self.state["items"].keys() - self.resource_types.keys():
            raise ValueError("The ledger contains resources outside this exact deployment scope.")
        self.resource_types = {role: kind for role, kind in self.resource_types.items()
                               if role in config["resource_names"]}
        self.api = api
        self.base = f"workspaces/{config['workspace_id']}/"

    def save(self):
        save(self.path, self.state)

    def preflight(self, resuming=False):
        if not self.config["tenant_id"] or not self.config["workspace_id"]:
            raise ValueError("Select the actual tenant_id and workspace_id before any cloud operation.")
        if not self.config["accept_preview_apis"]:
            raise ValueError("EventSchemaSet is a documented public preview API. Explicit preview consent is required.")
        if self.api is None:
            self.api = PublicApi(self.config["tenant_id"], self.config["auth_provider"])
        identity = self.api.identity()
        if identity["tenantId"] != self.config["tenant_id"] or identity["objectType"] != "User":
            raise ValueError("The authenticated delegated identity differs from the approved tenant.")
        workspace = self.api.request("GET", self.base.rstrip("/")).body
        capacities = self.api.list_all("capacities")
        if not any(c["id"] == workspace.get("capacityId") and c["state"] == "Active"
                   and re.fullmatch(r"(?:F[T]?[1-9]\d*|P[1-9]\d*)", c["sku"]) for c in capacities):
            raise ValueError("The workspace must use an accessible active supported Fabric capacity.")
        inventory = self.api.list_all(self.base + "items")
        for role in self.resource_types:
            name = self.config["resource_names"][role]
            identifier = self.state["items"].get(role)
            pending = self.state.get("pending", {})
            if pending.get("role") == role and pending.get("item_id"):
                identifier = pending["item_id"]
            resolving_known_create = resuming and pending.get("role") == role and (
                pending.get("location") or pending.get("actual_status") == 201
                or pending.get("actual_status") == 202
                and pending.get("operation_headers", {}).get("x-ms-operation-id")
            )
            if not resolving_known_create and any(
                item["displayName"].casefold() == name.casefold() and item["id"] != identifier
                and item.get("type") in (None, self.resource_types[role]) for item in inventory
            ):
                raise ValueError(f"Unmanaged {role} name collision. No item will be adopted or overwritten.")
            if role in self.state["items"]:
                matches = [item for item in inventory if item["id"] == identifier]
                if len(matches) != 1:
                    raise ValueError(f"Tracked {role} was deleted or is missing.")
                self.check_item(role, matches[0])
        self.identity = identity
        return {"tenant_id": identity["tenantId"], "workspace_id": self.config["workspace_id"],
                "workspace": workspace["displayName"], "public_only": True}

    def check_item(self, role, item):
        if (item.get("type") != self.resource_types[role] or item.get("displayName") != self.config["resource_names"][role]
                or item.get("description") != self.owner or item.get("workspaceId") != self.config["workspace_id"]):
            raise ValueError("The public item response changed the reviewed identity, workspace or ownership.")
        return str(uuid.UUID(item["id"]))

    def wait(self, reply, pending, need_result):
        if reply.status != 202:
            return reply.body
        operation_id = reply.headers.get("x-ms-operation-id")
        location = (FABRIC + "/v1/operations/" + str(uuid.UUID(operation_id))
                    if operation_id else reply.headers.get("location"))
        if not location:
            raise RuntimeError("Accepted operation has no public polling identity. Inspect the ledger; do not replay.")
        public_url("GET", location)
        if not re.fullmatch(r"/v1/operations/[0-9a-f-]{36}", location.removeprefix(FABRIC)):
            raise ValueError("The returned Location is not a documented public operation-state route.")
        pending.update(location=location, retry_after=reply.headers.get("retry-after", "1"))
        self.save()
        deadline = time.monotonic() + 600
        while True:
            delay = pending["retry_after"]
            if not delay.isdigit() or not 0 <= int(delay) <= 120:
                raise ValueError("Invalid public LRO Retry-After.")
            self.api.sleep(int(delay))
            operation = self.api.request("GET", location)
            pending["operation"] = operation.body
            pending["retry_after"] = operation.headers.get("retry-after", "1")
            self.save()
            status = operation.body.get("status")
            if status == "Succeeded":
                return self.api.request("GET", location + "/result").body if need_result else None
            if status not in ("NotStarted", "Running"):
                error = operation.body.get("error")
                details = [str(status)]
                if isinstance(error, dict):
                    details.extend(error[key] for key in ("errorCode", "message")
                                   if isinstance(error.get(key), str) and error[key])
                raise RuntimeError("The public operation failed (" + "; ".join(details)
                                   + "); retain the exact resources and ledger.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Public operation remains pending. Resume its saved Location, never recreate.")

    def complete_create(self, pending, reply):
        actual = self.wait(reply, pending, need_result=True)
        identifier = self.check_item(pending["role"], actual)
        pending.update(phase="created_readback_pending", item_id=identifier)
        self.save()
        item = self.api.request("GET", self.base + "items/" + identifier).body
        if self.check_item(pending["role"], item) != identifier:
            raise ValueError("The created item's independent identity readback differs.")
        self.state["items"][pending["role"]] = identifier
        self.state.pop("pending")
        self.save()

    def creation_request(self, role):
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        collection = {"schema": "eventSchemaSets", "eventhouse": "eventhouses", "database": "kqlDatabases"}[role]
        if role == "schema":
            body["definition"] = schema_definition(self.folder, self.config)
        elif role == "database":
            body["creationPayload"] = {
                "databaseType": "ReadWrite", "parentEventhouseItemId": self.state["items"]["eventhouse"],
            }
        return self.base + collection, body

    def create(self, role):
        if role in self.state["items"]:
            return
        if self.state.get("pending"):
            raise ValueError("Resume the pending public operation before creating another resource.")
        address, body = self.creation_request(role)
        pending = {"role": role, "phase": "request_started", "path": address, "request": body}
        self.state["pending"] = pending
        self.save()
        reply = self.api.request("POST", pending["path"], body)
        pending.update(actual_status=reply.status, actual_response=reply.body,
                       operation_headers={key: reply.headers[key] for key in (
                           "x-ms-operation-id", "retry-after",
                       ) if key in reply.headers})
        self.save()
        if reply.status not in (201, 202):
            raise RuntimeError("Create did not return its documented successful status.")
        self.complete_create(pending, reply)

    def resume_pending(self):
        pending = self.state.get("pending")
        if not pending:
            return
        if pending.get("item_id"):
            actual = self.api.request("GET", self.base + "items/" + pending["item_id"]).body
            self.state["items"][pending["role"]] = self.check_item(pending["role"], actual)
            self.state.pop("pending")
            self.save()
        elif pending.get("location"):
            from public_api import Reply
            self.complete_create(pending, Reply(202, {
                "location": pending["location"], "retry-after": pending.get("retry_after", "1"),
            }, None))
        elif pending.get("actual_status") == 202 and pending.get("operation_headers", {}).get("x-ms-operation-id"):
            from public_api import Reply
            self.complete_create(pending, Reply(202, pending["operation_headers"], pending.get("actual_response")))
        elif pending.get("actual_status") == 201:
            from public_api import Reply
            self.complete_create(pending, Reply(201, {}, pending["actual_response"]))
        else:
            raise ValueError("The create outcome is ambiguous. Inspect this attempt; no write will be replayed.")

    def verify_schema(self):
        reply = self.api.request("POST", self.base + f"eventSchemaSets/{self.state['items']['schema']}/getDefinition")
        readback = self.wait(reply, {}, need_result=True)
        parts = [part for part in readback["definition"]["parts"]
                 if part["path"] in ("EventSchemaSetDefinition.json", "EventSchemaSetV1.json")]
        if len(parts) != 1 or parts[0]["payloadType"] != "InlineBase64":
            raise ValueError("The documented Business Event schema definition part was not returned.")
        actual = json.loads(base64.b64decode(parts[0]["payload"], validate=True))
        expected = json.loads(base64.b64decode(schema_definition(self.folder, self.config)["parts"][0]["payload"]))
        def equivalent(required, received):
            if isinstance(required, dict):
                return isinstance(received, dict) and all(
                    key in received and (
                        equivalent(json.loads(value), json.loads(received[key]))
                        if key == "schema" and isinstance(value, str) else equivalent(value, received[key])
                    ) for key, value in required.items()
                )
            if isinstance(required, list):
                return isinstance(received, list) and len(required) == len(received) and all(
                    equivalent(left, right) for left, right in zip(required, received)
                )
            return required == received
        if not equivalent(expected, actual):
            raise ValueError("Business Event type, public category or payload schema differs from the contract.")

    def roles(self, create=False):
        path = self.base + f"items/{self.state['items']['schema']}/dataAccessRoles"
        name = "PublicScenarioPublisherConsumer"
        if create:
            enable = self.api.request(
                "POST", ONELAKE + f"/v1.0/workspaces/{self.config['workspace_id']}/artifacts/"
                + self.state["items"]["schema"] + "/security/enable", {"enableOneSecurity": True},
            )
            if enable.status not in (200, 204):
                raise RuntimeError("Public OneLake security enablement is not complete.")
        previous = self.api.request("GET", path)
        collection = copy.deepcopy(self.api.list_all(path, first_reply=previous))
        matching = [role for role in collection if role["name"] == name]
        if len(matching) > 1:
            raise ValueError("The public role collection contains duplicate managed names.")
        expected = {
            "name": name, "decisionRules": [{
                "effect": "Permit", "permission": [
                    {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
                    {"attributeName": "Action", "attributeValueIncludedIn": ["Publish", "Consume"]},
                ],
            }], "members": {"microsoftEntraMembers": [self.identity]},
        }
        if create:
            if matching:
                managed = matching[0]
                if managed["decisionRules"] != expected["decisionRules"]:
                    raise ValueError("A different role uses the reserved managed name. Its permissions will not be replaced.")
                members = managed["members"]["microsoftEntraMembers"]
                if self.identity not in members:
                    members.append(self.identity)
            else:
                collection.append(expected)
            etag = previous.headers.get("etag")
            if not etag or etag == "*":
                raise ValueError("An exact collection ETag is required to preserve concurrent role changes.")
            headers = {"If-Match": '"' + etag.strip('"') + '"'}
            self.api.request("PUT", path, {"value": collection}, headers=headers)
        actual = self.api.list_all(path)
        managed = [role for role in actual if role["name"] == name]
        if (len(managed) != 1 or managed[0]["decisionRules"] != expected["decisionRules"]
                or not any(member.get("tenantId") == self.identity["tenantId"]
                           and member.get("objectId") == self.identity["objectId"]
                           for member in managed[0]["members"]["microsoftEntraMembers"])):
            raise ValueError("Actual public Publish/Consume permissions do not match the selected identity.")

    def verify_foundations(self):
        self.preflight()
        foundations = set(FOUNDATIONS) & self.resource_types.keys()
        if not foundations <= self.state["items"].keys():
            raise ValueError("All required public foundations must be created first.")
        self.verify_schema()
        self.roles()
        if "database" in foundations:
            database = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["database"]).body
            house = self.api.request("GET", self.base + "eventhouses/" + self.state["items"]["eventhouse"]).body
            if (database["properties"].get("parentEventhouseItemId") != self.state["items"]["eventhouse"]
                    or self.state["items"]["database"] not in house["properties"]["databasesItemIds"]):
                raise ValueError("The public database is not a child of the exact created Eventhouse.")
        result = {"status": "public_foundations_verified_full_scenario_blocked", "items": self.state["items"],
                  "full_deployment_status": plan(self.folder, self.config)["full_deployment_status"],
                  "events_published": 0, "e2e_verified": False, "eventhouse_subscription_configured": False}
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "foundations":
            raise ValueError(
                "Full deployment is not implemented. Review the plan's unfinished steps and public API gaps. "
                "No cloud operation was attempted."
            )
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Use resume for this exact pending public ledger.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        for role in ("eventhouse", "database"):
            if role in self.resource_types:
                self.create(role)
        result = self.verify_foundations()
        self.state["status"] = "public_foundations_configured_full_scenario_blocked"
        self.save()
        return result


def main(folder, argv=None):
    folder = Path(folder)
    try:
        scenario_id = canonical_scenario_id(read(folder / "scenario.json")["scenario_id"])
        manual_folder = scenario_id in (
            "revenue-below-target", "discount-approval-routing", "fraud-response-before-fulfillment",
            "real-time-viewer-recommendations",
            "abandoned-cart-recovery",
            "data-pipeline-failure-follow-up",
            "customer-loyalty-milestone",
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    commands = ("configure", "plan", "workspaces", "preflight", "deploy", "resume", "verify", "test")
    parser.add_argument("command", choices=commands)
    parser.add_argument("--config", type=Path, default=folder / "config.card.example.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tenant-id")
    parser.add_argument("--workspace-id")
    parser.add_argument("--auth-provider", choices=("azure_cli", "fabric_cli"))
    if manual_folder:
        parser.set_defaults(scope="full", teams_chat_id=None, source_only=False)
    else:
        parser.add_argument("--scope", choices=("full", "foundations"), default="full")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--confirm-workspace")
    if not manual_folder:
        parser.add_argument("--teams-chat-id", help="Override teams_chat_id from the private configuration.")
        parser.add_argument("--source-only", action="store_true",
                            help="Compatibility alias: test already submits inputs without outcome verification.")
    args = parser.parse_args(argv)
    try:
        config = configuration(folder, args.config)
        if args.source_only and (args.command != "test" or args.teams_chat_id):
            raise ValueError("--source-only applies only to test without --teams-chat-id.")
        if args.teams_chat_id is not None:
            validate_teams_chat_id(args.teams_chat_id)
        verifier = "source_only" if args.source_only else (
            "graph" if args.teams_chat_id is not None else config.get("teams_verifier", "graph")
        )
        chat_id = None if verifier in ("source_only", "windows_ui") else (
            args.teams_chat_id if args.teams_chat_id is not None else config.get("teams_chat_id")
        )
        if args.command not in ("configure", "workspaces") and any(
            (args.tenant_id, args.workspace_id, args.auth_provider),
        ):
            raise ValueError("Target/provider flags apply to configure or workspace discovery, not deployment overrides.")
        if args.command == "configure":
            if args.output is None or args.output.exists():
                raise ValueError("Choose --output for a new private configuration; existing files are never overwritten.")
            if args.teams_chat_id is not None:
                if "teams_recipients" not in config:
                    raise ValueError("teams_chat_id applies only to the supported Teams alerting scenarios.")
                config["teams_chat_id"] = args.teams_chat_id
                config["teams_verifier"] = "graph"
            config["tenant_id"] = str(uuid.UUID(args.tenant_id or input("Tenant ID: ").strip()))
            config["workspace_id"] = str(uuid.UUID(args.workspace_id or input("Workspace ID: ").strip()))
            config["auth_provider"] = args.auth_provider or config["auth_provider"]
            save(args.output, config)
            print("Private public-only configuration saved. No authentication or deployment was performed.")
            return 0
        current_plan = plan(folder, config)
        sales_full = canonical_scenario_id(config["scenario_id"]) == "sales-volume-alert" and args.scope == "full"
        payment_full = canonical_scenario_id(config["scenario_id"]) == "real-time-stream-alert" and args.scope == "full"
        inventory_full = canonical_scenario_id(config["scenario_id"]) == "low-stock-threshold" and args.scope == "full"
        viewer_full = canonical_scenario_id(config["scenario_id"]) == "real-time-viewer-recommendations"
        supported_full = sales_full or payment_full or inventory_full or viewer_full
        revenue = canonical_scenario_id(config["scenario_id"]) == "revenue-below-target"
        revenue_hybrid = revenue and current_plan["implemented_scope"] == "hybrid_deployment"
        discount_hybrid = (canonical_scenario_id(config["scenario_id"]) == "discount-approval-routing"
                           and current_plan["implemented_scope"] == "hybrid_deployment")
        fraud_hybrid = (canonical_scenario_id(config["scenario_id"]) == "fraud-response-before-fulfillment"
                        and current_plan["implemented_scope"] == "hybrid_deployment")
        cart_hybrid = (canonical_scenario_id(config["scenario_id"]) == "abandoned-cart-recovery"
                       and current_plan["implemented_scope"] == "hybrid_deployment")
        failure_hybrid = (canonical_scenario_id(config["scenario_id"]) == "data-pipeline-failure-follow-up"
                          and current_plan["implemented_scope"] == "hybrid_deployment")
        loyalty_hybrid = (canonical_scenario_id(config["scenario_id"]) == "customer-loyalty-milestone"
                          and current_plan["implemented_scope"] == "hybrid_deployment")
        hybrid = (revenue_hybrid or discount_hybrid or fraud_hybrid or cart_hybrid
                  or failure_hybrid or loyalty_hybrid)
        if loyalty_hybrid and args.command == "test":
            raise ValueError("Loyalty has no automated source trigger. Run the separate native KQL sample blocks "
                             "after completing the guide's manual setup and observing each source baseline.")
        if (payment_full or inventory_full or revenue or discount_hybrid) and args.teams_chat_id is not None:
            raise ValueError("Teams overrides apply only to Sales Volume Alert. These scenarios use native Fabric actions.")
        if args.command == "plan" or args.command in ("deploy", "resume") and args.scope == "full" and (
            not (hybrid and current_plan["full_deployment_status"] == "ready_for_manual_handoff")
            and (not supported_full or current_plan["full_deployment_status"] != "ready_to_deploy")
        ):
            print(json.dumps(current_plan, indent=2))
            return 0 if args.command == "plan" else 3
        if args.command == "workspaces":
            tenant = args.tenant_id or config["tenant_id"]
            if not tenant:
                raise ValueError("Set --tenant-id for workspace discovery or select a configured tenant.")
            api = PublicApi(tenant, args.auth_provider or config["auth_provider"])
            api.identity()
            print(json.dumps(api.list_all("workspaces"), indent=2))
            return 0
        if not config["tenant_id"] or not config["workspace_id"]:
            raise ValueError("Set the real tenant_id and workspace_id.")
        if args.command == "test" and (
            not (supported_full or hybrid) or args.confirm_workspace != config["workspace_id"]
        ):
            if revenue_hybrid:
                raise ValueError("Revenue test requires the exact --confirm-workspace; native E2E remains manual.")
            raise ValueError(
                "Scenario test requires a supported native source and the exact --confirm-workspace."
            )
        directory = folder / ".deployments" / config["tenant_id"] / config["workspace_id"]
        active = directory / (config["resource_prefix"] + ".active.json")
        if args.command == "deploy":
            if args.confirm_workspace != config["workspace_id"]:
                raise ValueError("Confirm the reviewed --confirm-workspace before any deployment.")
            path = args.state_file or directory / (config["resource_prefix"] + "." + uuid.uuid4().hex + ".json")
            if path.exists():
                raise ValueError("A new deployment requires a fresh ledger. Use resume for an existing attempt.")
        elif args.command == "preflight":
            path = args.state_file or directory / "preflight.json"
        else:
            if args.state_file:
                path = args.state_file
            elif active.exists():
                path = Path(read(active)["state_file"])
            else:
                raise ValueError("No exact current public deployment ledger was selected.")
            if not path.exists():
                raise ValueError("The selected public ledger is missing.")
            if args.command == "resume" and args.confirm_workspace != config["workspace_id"]:
                raise ValueError("Confirm the exact workspace before resuming.")
        if sales_full:
            from sales_deploy import SalesDeployment
            target = SalesDeployment(folder, config, path)
        elif payment_full:
            from payment_deploy import PaymentDeployment
            target = PaymentDeployment(folder, config, path)
        elif inventory_full:
            from inventory_deploy import InventoryDeployment
            target = InventoryDeployment(folder, config, path)
        elif viewer_full:
            from viewer_deploy import ViewerDeployment
            target = ViewerDeployment(folder, config, path)
        elif revenue_hybrid:
            from revenue_deploy import RevenueDeployment
            target = RevenueDeployment(folder, config, path)
        elif discount_hybrid:
            from discount_deploy import DiscountDeployment
            target = DiscountDeployment(folder, config, path)
        elif fraud_hybrid:
            from fraud_deploy import FraudDeployment
            target = FraudDeployment(folder, config, path)
        elif cart_hybrid:
            from cart_deploy import CartDeployment
            target = CartDeployment(folder, config, path)
        elif failure_hybrid:
            from failure_deploy import FailureDeployment
            target = FailureDeployment(folder, config, path)
        elif loyalty_hybrid:
            from loyalty_deploy import LoyaltyDeployment
            target = LoyaltyDeployment(folder, config, path)
        else:
            target = Deployment(folder, config, path)
        if supported_full:
            target.teams_verifier = verifier
        if payment_full or inventory_full:
            target.source_only = args.source_only
        if args.command == "preflight":
            result = target.preflight()
        elif args.command in ("deploy", "resume"):
            if args.command == "deploy":
                target.save()
                pointer = {"state_file": str(path.resolve())}
                if "configuration_version" in config:
                    pointer["configuration_version"] = config["configuration_version"]
                save(active, pointer)
            result = target.deploy(args.scope, resume=args.command == "resume")
        elif args.command == "test":
            result = target.test(chat_id)
        elif supported_full or hybrid:
            result = target.verify(chat_id)
        else:
            result = target.verify_foundations()
        if "scenario_name" in current_plan:
            result["scenario_name"] = current_plan["scenario_name"]
        print(json.dumps(result, indent=2))
        if args.command == "test":
            return 0 if result.get("source_triggered") else 2
        return 2 if (result.get("status") == "manual_setup_required"
                     or args.command == "verify" and not result.get("e2e_verified")) else 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
