"""Public Revenue consumer preparation, not native publisher or E2E certification."""

import ast
import copy
import json
from pathlib import Path
import uuid

from action_deploy import ActionDeployment
from revenue_records import REVENUE_FIELDS
from public_api import Reply
from public_consumers import activator_entities, definition_part, matches_definition, part
from public_deploy import Deployment, ROOT, canonical_scenario_id
from revenue_source import RevenueSourceContract, normalize_definition


class RevenueComponents(Deployment):
    files_endpoint = ActionDeployment.files_endpoint
    list_records = ActionDeployment.list_records
    prepare_record_store = ActionDeployment.prepare_record_store

    resource_types = {
        "schema": "EventSchemaSet", "record_store": "Lakehouse",
        "notebook": "Notebook", "pipeline": "DataPipeline", "activator": "Reflex",
    }

    def __init__(self, folder, config, state_path, source_state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if canonical_scenario_id(config["scenario_id"]) != "revenue-below-target":
            raise ValueError("Revenue component preparation belongs only to Revenue Below Target.")
        if self.state["items"] and self.state.get("revenue_public_components_version") != 2:
            raise ValueError("Do not adopt or upgrade another component ledger.")
        self.source_path = Path(source_state_path).resolve()
        self.source = RevenueSourceContract(folder, config, self.source_path, api)
        if (set(self.source.state["items"]) != set(self.source.resource_types)
                or not self.source.state.get("source_contract_verification", {}).get("source_contract_verified")):
            raise ValueError("Prepare and verify the exact native source before adding its consumers.")
        reference = {"state_file": str(self.source_path), "items": copy.deepcopy(self.source.state["items"])}
        if self.state.get("source_reference", reference) != reference:
            raise ValueError("The component ledger cannot be rebound to another native source.")
        self.state.update(source_reference=reference, revenue_public_components_version=2)
        self.state.setdefault("activator_ids", {name: str(uuid.uuid4()) for name in (
            "container", "source", "event", "rule", "source_step", "fields_step", "detect_step", "act_step", "fabric_action",
        )})

    @property
    def record_folder(self):
        return "RevenueInvestigations"

    def notebook_source(self):
        source = (self.folder / "response-notebook.py").read_text(encoding="utf-8")
        source = source.replace("# ${record_helpers}", (ROOT / "revenue_records.py").read_text(encoding="utf-8"))
        for marker, value in {
            "workspace_id": self.config["workspace_id"], "lakehouse_id": self.state["items"]["record_store"],
            "rule_id": self.state["activator_ids"]["rule"],
        }.items():
            source = source.replace("${" + marker + "}", value)
        if "${" in source:
            raise ValueError("The investigation notebook contains unresolved resource bindings.")
        ast.parse(source)
        return source

    def pipeline_content(self):
        numeric = {"revenue", "target", "variance_pct"}
        parameters = {
            name: {"type": "float" if name in numeric else "string",
                   "value": {"type": "Expression", "value": "@pipeline().parameters." + name}}
            for name in REVENUE_FIELDS
        }
        parameters["pipeline_run_id"] = {
            "type": "string", "value": {"type": "Expression", "value": "@pipeline().RunId"},
        }
        return {"properties": {
            "description": "Prepare Revenue analysis from the six fields of the triggering Business Event.",
            "parameters": {name: {"type": "Float" if name in numeric else "String"} for name in REVENUE_FIELDS},
            "activities": [{
                "name": "Materialize actual revenue investigation", "type": "TridentNotebook", "dependsOn": [],
                "policy": {"timeout": "0.00:30:00", "retry": 0, "retryIntervalInSeconds": 30},
                "typeProperties": {
                    "notebookId": self.state["items"]["notebook"], "workspaceId": self.config["workspace_id"],
                    "parameters": parameters,
                },
            }],
        }}

    def consumer_entities(self):
        return activator_entities(
            self.config, self.state["items"]["schema"], self.state["activator_ids"], [],
            should_run=self.state.get("consumer_rule_enabled", False),
            action={"item_id": self.state["items"]["pipeline"], "item_type": "Pipeline",
                    "job_type": "Pipeline", "parameters": [
                        (name, name, "Number" if name in ("revenue", "target", "variance_pct") else "String")
                        for name in REVENUE_FIELDS
                    ]},
        )

    def creation_request(self, role):
        if role == "schema":
            return Deployment.creation_request(self, role)
        if role == "record_store":
            return ActionDeployment.creation_request(self, role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "notebook":
            body["definition"] = {"format": "fabricGitSource", "parts": [
                part("notebook-content.py", self.notebook_source()),
            ]}
            return self.base + "notebooks", body
        if role == "pipeline":
            body["definition"] = {"parts": [part("pipeline-content.json", self.pipeline_content())]}
            return self.base + "dataPipelines", body
        if role == "activator":
            body.update(type="Reflex", definition={"parts": [part("ReflexEntities.json", self.consumer_entities())]})
            return self.base + "items", body
        raise ValueError("Unknown Revenue component role.")

    def get_definition(self, role):
        collection = {"notebook": "notebooks", "pipeline": "dataPipelines", "activator": "items"}[role]
        record = {"role": role, "phase": "request_started"}
        self.state.setdefault("component_readbacks", {})[role] = record
        self.save()
        reply = self.api.request("POST", self.base + collection + "/" + self.state["items"][role] + "/getDefinition")
        record.update(actual_status=reply.status, headers=reply.headers)
        self.save()
        definition = self.wait(reply, record, need_result=True)["definition"]
        record.update(phase="returned", definition=definition)
        self.save()
        return definition

    def verify_components(self):
        self.verify_foundations()
        store = self.api.request("GET", self.base + "lakehouses/" + self.state["items"]["record_store"]).body
        self.check_item("record_store", store)
        self.state["record_store_readback"] = store
        self.files_endpoint()
        self.list_records()
        notebook = definition_part(self.get_definition("notebook"), "notebook-content.py")
        if ast.dump(ast.parse(notebook)) != ast.dump(ast.parse(self.notebook_source())):
            raise ValueError("The actual investigation notebook differs from the event-driven analysis contract.")
        pipeline = json.loads(definition_part(self.get_definition("pipeline"), "pipeline-content.json"))
        expected = self.pipeline_content()
        if not matches_definition(expected, pipeline):
            raise ValueError("The actual pipeline differs from its six event parameters, notebook or native run binding.")
        if set(pipeline["properties"]["parameters"]) != set(REVENUE_FIELDS):
            raise ValueError("Revenue analysis must accept exactly the six native event fields.")
        received = json.loads(definition_part(self.get_definition("activator"), "ReflexEntities.json"))
        expected = self.consumer_entities()
        by_id = {entity["uniqueIdentifier"]: entity for entity in received}
        if len(by_id) != len(received) or set(by_id) != {entity["uniqueIdentifier"] for entity in expected}:
            raise ValueError("The actual consumer has missing, duplicated or additional entities.")
        for entity in expected:
            if not matches_definition(normalize_definition(entity), normalize_definition(by_id[entity["uniqueIdentifier"]])):
                raise ValueError("The actual native Pipeline consumer differs from its reviewed definition.")
        result = {
            "status": "public_revenue_components_verified_full_e2e_blocked",
            "component_definitions_verified": True, "items": copy.deepcopy(self.state["items"]),
            "consumer_rule_enabled": self.state.get("consumer_rule_enabled", False),
            "business_event_publication_configured": False,
            "native_pipeline_invocation_verified": False, "persisted_analysis_verified": False,
            "eventhouse_subscription_configured": False, "source_inputs_submitted": 0, "e2e_verified": False,
            "eventhouse_required": False,
        }
        self.state.update(status=result["status"], verification=result)
        self.save()
        return result

    def enable_consumer(self):
        if self.state.get("consumer_rule_enabled"):
            return
        pending = self.state.get("pending_consumer_activation")
        if pending and pending.get("actual_status") == 202:
            self.wait(Reply(202, pending["headers"], None), pending, need_result=False)
        if not pending:
            self.verify_components()
        actual = json.loads(definition_part(self.get_definition("activator"), "ReflexEntities.json"))
        by_id = {entity["uniqueIdentifier"]: entity for entity in actual}
        expected = self.consumer_entities()
        if len(by_id) != len(actual) or set(by_id) != {entity["uniqueIdentifier"] for entity in expected}:
            raise ValueError("Do not activate a changed or incomplete native consumer.")
        rule = by_id[self.state["activator_ids"]["rule"]]
        already_enabled = rule["payload"]["definition"]["settings"]["shouldRun"] is True
        if self.state.get("pending_consumer_activation"):
            if not already_enabled:
                raise ValueError("The previous activation is ambiguous; retain evidence and do not replay it.")
        elif already_enabled:
            raise ValueError("The consumer was enabled outside this exact deployment.")
        for entity in expected:
            if already_enabled and entity["uniqueIdentifier"] == self.state["activator_ids"]["rule"]:
                entity["payload"]["definition"]["settings"]["shouldRun"] = True
            if not matches_definition(normalize_definition(entity), normalize_definition(by_id[entity["uniqueIdentifier"]])):
                raise ValueError("The native consumer changed before activation.")
        if not already_enabled:
            rule["payload"]["definition"]["settings"]["shouldRun"] = True
            record = {"phase": "request_started"}
            self.state["pending_consumer_activation"] = record
            self.save()
            reply = self.api.request(
                "POST", self.base + "items/" + self.state["items"]["activator"] + "/updateDefinition",
                {"definition": {"parts": [part("ReflexEntities.json", actual)]}},
            )
            record.update(actual_status=reply.status, headers=reply.headers)
            self.save()
            self.wait(reply, record, need_result=False)
        self.state["consumer_rule_enabled"] = True
        self.save()
        self.verify_components()
        self.state.pop("pending_consumer_activation", None)
        self.save()

    def prepare(self, resume=False):
        self.preflight(resuming=resume)
        if "preparation_inventory_before" not in self.state:
            if self.state["items"] or self.state.get("pending"):
                raise ValueError("Recover the actual pre-creation inventory; do not infer ownership from a name.")
            self.state["preparation_inventory_before"] = self.api.list_all(self.base + "items")
            self.save()
        self.source.api = self.api
        self.source.preflight()
        self.source.verify_source()
        self.source.verify_queryset()
        self.source.verify_publisher()
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending component operation; do not create another attempt.")
            self.resume_pending()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("record_store")
        self.prepare_record_store()
        for role in ("notebook", "pipeline", "activator"):
            self.create(role)
        return self.verify_components()
