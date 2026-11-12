"""Native Business Event UDF definitions and their public readback."""

import copy
import json
import uuid

from public_api import POWERBI
from public_consumers import definition_part, matches_definition, part
from public_deploy import Deployment


class UdfEventSourceDeployment(Deployment):
    resource_types = {"schema": "EventSchemaSet", "publisher": "UserDataFunction", "activator": "Reflex"}

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if "activator_ids" not in self.state:
            self.state["activator_ids"] = {name: str(uuid.uuid4()) for name in (
                "container", "source", "event", "source_step",
            )}

    def preflight(self, resuming=False):
        if self.config["auth_provider"] != "azure_cli":
            raise ValueError("Native UDF invocation requires azure_cli and the documented Power BI audience.")
        result = super().preflight(resuming)
        self.api.tokens.token(POWERBI)
        return result

    def creation_request(self, role):
        if role == "schema":
            return super().creation_request(role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "publisher":
            body["definition"] = self.udf_definition()
            return self.base + "userDataFunctions", body
        if role != "activator":
            raise ValueError("This deployment creates only its schema, native UDF and Activator event source.")
        body.update(type="Reflex", definition={"parts": [part("ReflexEntities.json", self.activator_entities())]})
        return self.base + "items", body

    def verify_event_source(self):
        reply = self.api.request("POST", self.base + f"items/{self.state['items']['activator']}/getDefinition")
        definition = self.wait(reply, {}, need_result=True)["definition"]
        entities = json.loads(definition_part(definition, "ReflexEntities.json"))
        if not isinstance(entities, list):
            raise ValueError("The Activator definition must contain an entity array.")
        by_id = {entity["uniqueIdentifier"]: copy.deepcopy(entity) for entity in entities}
        if len(by_id) != len(entities):
            raise ValueError("The Activator contains ambiguous duplicate entities.")
        managed = []
        for expected in self.activator_entities():
            actual = by_id.get(expected["uniqueIdentifier"])
            if actual is None:
                raise ValueError("The owned source or event identity is missing.")
            if expected["type"] == "timeSeriesView-v1":
                expected["payload"]["definition"]["instance"] = json.loads(expected["payload"]["definition"]["instance"])
                actual["payload"]["definition"]["instance"] = json.loads(actual["payload"]["definition"]["instance"])
            if not matches_definition(expected, actual):
                raise ValueError("The event source or binding differs from the reviewed definition.")
            # Manual action connections may contain credentials; retain only the owned source graph.
            managed.append(expected)
        self.state.setdefault("definition_backup", {})["activator_source"] = managed
        return len(entities) - len(managed)


def publisher_definition(folder, config, schema_id, function, description, parameters, source=None):
    alias = config["resource_names"]["schema"]
    source = (folder / "function_app.py").read_text(encoding="utf-8") if source is None else source
    source = source.replace("${event_name}", config["event_name"]).replace("${schema_name}", alias)
    if "${" in source:
        raise ValueError("The UDF source contains an unresolved deployment binding.")
    definition = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/userDataFunction/definition/1.1.0/schema.json",
        "runtime": "PYTHON",
        "connectedDataSources": [{
            "alias": alias, "artifactId": schema_id,
            "artifactType": "EventDefinition", "workspaceId": config["workspace_id"],
        }],
        "functions": [{
            "name": function, "description": description, "isPublicEndpointEnabled": True,
        }],
        "libraries": {"public": [], "private": []},
    }
    metadata = {"runtime": "PYTHON", "functionsMetadata": [{
        "name": function, "scriptFile": "function_app.py",
        "bindings": [
            {"methods": ["POST"], "route": "", "authLevel": "Anonymous", "name": "req",
             "direction": "In", "type": "HttpTrigger"},
            {"itemType": None, "subType": "FabricBusinessEventsClient", "alias": alias,
             "name": "businessEventsClient", "direction": "In", "type": "FabricItem"},
        ],
        "fabricProperties": {
            "fabricMetadataSchemaVersion": "1.1.0",
            "fabricFunctionParameters": [
                {"name": name, "dataType": kind} for name, (_, kind) in parameters.items()
            ],
            "fabricFunctionReturnType": "dict",
        },
    }]}
    return {"parts": [part("definition.json", definition), part("function_app.py", source),
                      part("resources/functions.json", metadata)]}


def verify_publisher_definition(deployment):
    return verify_udf_definition(deployment, "publisher", deployment.udf_definition())


def verify_udf_definition(deployment, role, expected):
    reply = deployment.api.request(
        "POST", deployment.base + f"userDataFunctions/{deployment.state['items'][role]}/getDefinition",
    )
    actual = deployment.wait(reply, {}, need_result=True)["definition"]
    paths = [entry["path"] for entry in actual["parts"]]
    allowed = {"definition.json", "function_app.py", ".platform",
               "resources/functions.json", "resources\\functions.json"}
    if len(set(paths)) != len(paths) or set(paths) - allowed:
        raise ValueError("The UDF definition contains duplicate or unreviewed parts.")
    declared = json.loads(definition_part(expected, "definition.json"))
    received = json.loads(definition_part(actual, "definition.json"))
    if received.get("libraries") == {"public": [
        {"name": "fabric-user-data-functions", "type": "PYPI", "version": "1.0"},
    ], "private": []}:
        declared["libraries"] = received["libraries"]
    if not matches_definition(declared, received):
        raise ValueError("The UDF connection, function or public endpoint differs.")
    metadata_paths = [name for name in paths if name in ("resources/functions.json", "resources\\functions.json")]
    if len(metadata_paths) > 1:
        raise ValueError("The UDF metadata part is ambiguous.")
    if metadata_paths and not matches_definition(
        json.loads(definition_part(expected, "resources/functions.json")),
        json.loads(definition_part(actual, metadata_paths[0])),
    ):
        raise ValueError("The UDF parameter mappings differ.")
    if definition_part(expected, "function_app.py").replace("\r\n", "\n") != definition_part(
        actual, "function_app.py",
    ).replace("\r\n", "\n"):
        raise ValueError("The native UDF publisher code differs from the reviewed source.")
    deployment.state.setdefault("definition_backup", {})[role] = actual
    metadata_status = (
        "returned_and_verified" if metadata_paths else "not_returned_by_public_definition"
    )
    if role == "publisher":
        deployment.state["udf_metadata_readback"] = metadata_status
    deployment.state.setdefault("udf_metadata_readbacks", {})[role] = metadata_status
    return actual
