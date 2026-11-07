"""Target-scoped native Activator connections and Power Automate management."""

import json
import re
import urllib.parse
import uuid

from fabric_api import POWERBI

FLOW_RESOURCE = "https://service.flow.microsoft.com/"
POWERAPPS_RESOURCE = "https://service.powerapps.com/"
CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_dataactivator"
CONNECTION_QUERY = """mutation createFlowConnectionCreateRequest(
  $flowConnectionCreateRequestCreateInputType: flowConnectionCreateRequestCreateInputType
) {
  createFlowConnectionCreateRequest(
    flowConnectionCreateRequestCreateInputType: $flowConnectionCreateRequestCreateInputType
  ) { connectionString flowConnectionId connectionStringVersion }
}"""
ACTION_QUERY = """mutation createPowerAutomateFlowAction(
  $powerAutomateFlowActionCreateInputType: powerAutomateFlowActionCreateInputType
) {
  createPowerAutomateFlowAction(
    powerAutomateFlowActionCreateInputType: $powerAutomateFlowActionCreateInputType
  ) { __metadata { id } }
}"""


class ReflexActions:
    def __init__(self, api, workspace_id, item_id):
        if not api.metadata_host:
            raise ValueError("This native connection bridge requires a supported metadata route.")
        self.api = api
        self.workspace_id = str(uuid.UUID(workspace_id))
        self.item_id = str(uuid.UUID(item_id))
        inventory = api.request(
            "GET", api.metadata_host + "/metadata/workspaces/" + self.workspace_id + "/artifacts",
            resource=POWERBI,
        ).body
        matches = [
            item for item in inventory
            if item["objectId"] == self.item_id and item["artifactType"] == "ReflexProject"
        ]
        if len(matches) != 1:
            raise ValueError("The exact Activator is not present in the selected workspace.")
        artifact = api.request(
            "GET", api.metadata_host + "/metadata/artifacts/" + self.item_id, resource=POWERBI,
        ).body
        self.capacity_id = str(uuid.UUID(matches[0]["capacityObjectId"]))
        if (
            artifact["objectId"] != self.item_id or artifact["artifactType"] != "ReflexProject"
            or str(uuid.UUID(artifact["capacityObjectId"])) != self.capacity_id
        ):
            raise ValueError("Native artifact routing differs from the verified workspace inventory.")
        self.document_id = artifact["extendedProperties"]["Aria.DocumentId"]
        if not isinstance(self.document_id, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", self.document_id):
            raise ValueError("The native Activator document identity is unavailable.")
        response = api.request(
            "POST", api.metadata_host + "/metadata/v201606/generatemwctoken",
            {
                "type": "[Start] GetMWCToken", "artifactObjectIds": [self.item_id],
                "capacityObjectId": self.capacity_id, "workloadType": "Reflex",
                "workspaceObjectId": self.workspace_id,
                "asyncId": str(uuid.uuid4()), "iframeId": str(uuid.uuid4()),
            }, resource=POWERBI,
        ).body
        host = uuid.UUID(self.capacity_id).hex + ".pbidedicated.windows.net"
        if response["TargetUriHost"] != host or str(uuid.UUID(response["CapacityObjectId"])) != self.capacity_id:
            raise ValueError("Refusing workload authorization for an unexpected capacity route.")
        self.token = response["Token"]
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("Native workload authorization is unavailable.")
        self.url = (
            f"https://{host}/webapi/capacities/{self.capacity_id}"
            "/workloads/Reflex/Config/Automatic/GraphQL"
        )

    @property
    def tenant(self):
        return {"__metadata": {"id": self.document_id}}

    def operation(self, name, query, variables):
        response = self.api._send(
            "POST", self.url, {
                "operationName": name, "query": query, "variables": json.dumps(variables),
            }, lambda: "MwcToken " + self.token,
        ).body
        if response.get("errors"):
            raise RuntimeError(f"Native {name} returned GraphQL errors; no success was recorded.")
        records = response.get("data", {}).get(name)
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise RuntimeError(f"Native {name} returned an incomplete or ambiguous result.")
        return records[0]

    def create_connection(self):
        result = self.operation("createFlowConnectionCreateRequest", CONNECTION_QUERY, {
            "flowConnectionCreateRequestCreateInputType": {
                "artifactId": self.item_id, "tenant": self.tenant, "workspaceId": self.workspace_id,
            },
        })
        expected = f"{self.workspace_id}/reflexes/{self.item_id}/config/powerAutomate/flow"
        parsed = urllib.parse.urlsplit(result.get("connectionString", ""))
        parameters = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        if (
            parsed.scheme or parsed.netloc or parsed.path != expected or parsed.fragment
            or set(parameters) != {"connectionString"} or len(parameters["connectionString"]) != 1
            or not parameters["connectionString"][0] or not result.get("flowConnectionId")
            or result.get("connectionStringVersion") != "V2"
        ):
            raise ValueError("Native connection information does not belong to the exact new Activator.")
        return result

    def create_action(self, identifier, name, fields, connection_id):
        if not isinstance(connection_id, str) or not connection_id:
            raise ValueError("A genuine native flow connection identity is required.")
        return self.operation("createPowerAutomateFlowAction", ACTION_QUERY, {
            "powerAutomateFlowActionCreateInputType": {
                "payload": {"name": name, "parameters": [{"key": field} for field in fields]},
                "system": {
                    "artifactId": self.item_id, "flowConnection": {"__metadata": {"id": connection_id}},
                },
                "tenant": self.tenant, "uniqueIdentifier": str(uuid.UUID(identifier)),
            },
        })


class Flows:
    def __init__(self, api, environment_id):
        self.api = api
        self.environment_id = str(uuid.UUID(environment_id))
        self.base = (
            "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/"
            + self.environment_id + "/"
        )

    def request(self, method, path, body=None):
        if not re.fullmatch(r"flows(?:/[a-zA-Z0-9-]+(?:/runs(?:/[a-zA-Z0-9-]+)?)?)?", path):
            raise ValueError("Unexpected Power Automate management route.")
        if method not in ("GET", "POST") or (method == "POST" and path != "flows"):
            raise ValueError("Unexpected Power Automate management operation.")
        return self.api._send(
            method, self.base + path + "?api-version=2016-11-01", body,
            lambda: "Bearer " + self.api.tokens.get(FLOW_RESOURCE),
        ).body

    def verify_connection(self, connection_name):
        if not isinstance(connection_name, str) or not re.fullmatch(
            r"shared-dataactivator-[a-zA-Z0-9-]+", connection_name,
        ):
            raise ValueError("Choose an actual authenticated Data Activator connection.")
        query = urllib.parse.urlencode({
            "api-version": "2016-11-01", "$filter": f"environment eq '{self.environment_id}'",
        })
        response = self.api._send(
            "GET", "https://api.powerapps.com/providers/Microsoft.PowerApps/connections?" + query,
            None, lambda: "Bearer " + self.api.tokens.get(POWERAPPS_RESOURCE),
        ).body
        matches = [item for item in response["value"] if item["name"] == connection_name]
        if len(matches) != 1 or matches[0]["properties"]["apiId"] != CONNECTOR:
            raise ValueError("The selected connector does not belong to this Power Automate target.")
        statuses = matches[0]["properties"].get("statuses", [])
        if not statuses or any(status["status"] != "Connected" for status in statuses):
            raise ValueError("The selected Data Activator connector is not authenticated.")

    def list(self):
        response = self.request("GET", "flows")
        records = list(response["value"])
        visited = set()
        while response.get("nextLink"):
            url = response["nextLink"]
            parsed = urllib.parse.urlsplit(url)
            if (
                not url.startswith(self.base + "flows?") or parsed.scheme != "https"
                or parsed.hostname != "api.flow.microsoft.com" or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment or url in visited
            ):
                raise ValueError("Unexpected or repeated Power Automate continuation route.")
            visited.add(url)
            response = self.api._send(
                "GET", url, None, lambda: "Bearer " + self.api.tokens.get(FLOW_RESOURCE),
            ).body
            records.extend(response["value"])
        return records
