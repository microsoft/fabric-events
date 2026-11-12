"""Authenticated requests to a finite set of documented public Fabric APIs."""

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

FABRIC = "https://api.fabric.microsoft.com"
STORAGE = "https://storage.azure.com"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
GRAPH = "https://graph.microsoft.com"
POWERBI = "https://analysis.windows.net/powerbi/api"
POWERBI_API = "https://api.powerbi.com"
GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
WORKSPACE = rf"/v1/workspaces/{GUID}"
ROUTES = {
    "GET": (
        r"/v1/workspaces", r"/v1/capacities", WORKSPACE,
        WORKSPACE + r"/items", WORKSPACE + rf"/items/{GUID}",
        WORKSPACE + rf"/eventhouses/{GUID}", WORKSPACE + rf"/kqlDatabases/{GUID}",
        WORKSPACE + rf"/items/{GUID}/jobs/instances/{GUID}",
        WORKSPACE + rf"/items/{GUID}/jobs/instances",
        WORKSPACE + rf"/lakehouses/{GUID}",
        WORKSPACE + rf"/sqlDatabases/{GUID}",
        WORKSPACE + rf"/notebooks/{GUID}/jobs/execute/instances/{GUID}",
        WORKSPACE + rf"/items/{GUID}/dataAccessRoles",
        WORKSPACE + rf"/eventstreams/{GUID}/topology",
        WORKSPACE + rf"/eventstreams/{GUID}/sources/{GUID}/connection",
        rf"/v1/operations/{GUID}(?:/result)?",
    ),
    "POST": (
        WORKSPACE + r"/(?:eventSchemaSets|eventhouses|kqlDatabases|notebooks|eventstreams|userDataFunctions|dataPipelines|lakehouses|semanticModels|reports|items)",
        WORKSPACE + rf"/eventSchemaSets/{GUID}/getDefinition",
        WORKSPACE + rf"/(?:kqlDatabases|notebooks|eventstreams|items|dataPipelines)/{GUID}/getDefinition",
        WORKSPACE + rf"/(?:semanticModels|reports)/{GUID}/getDefinition",
        WORKSPACE + rf"/eventstreams/{GUID}/updateDefinition",
        WORKSPACE + rf"/eventstreams/{GUID}/(?:resume|pause)",
        WORKSPACE + rf"/items/{GUID}/updateDefinition",
        WORKSPACE + rf"/reflexes/{GUID}/queryActivations",
        WORKSPACE + rf"/reflexes/{GUID}/updateDefinition",
        WORKSPACE + rf"/userDataFunctions/{GUID}/getDefinition",
        WORKSPACE + rf"/userDataFunctions/{GUID}/updateDefinition",
        WORKSPACE + rf"/userDataFunctions/{GUID}/functions/[A-Za-z][A-Za-z0-9_]{{0,99}}/invoke",
        WORKSPACE + rf"/notebooks/{GUID}/jobs/execute/instances",
    ),
    "PUT": (WORKSPACE + rf"/items/{GUID}/dataAccessRoles",),
    "DELETE": (WORKSPACE + rf"/items/{GUID}",),
}


def public_url(method, address):
    url = address if address.startswith("https://") else FABRIC + "/v1/" + address.lstrip("/")
    parts = urlsplit(url)
    if parts.username or parts.password or parts.fragment or parts.port:
        raise ValueError("Only documented public HTTPS routes without credentials or fragments are allowed.")
    if parts.scheme != "https":
        raise ValueError("HTTPS is required.")
    if parts.netloc == "api.fabric.microsoft.com":
        if not any(re.fullmatch(pattern, parts.path) for pattern in ROUTES.get(method, ())):
            raise ValueError("The method and route have no approved public API contract.")
        query = parse_qs(parts.query, keep_blank_values=True)
        collection = parts.path in ("/v1/workspaces", "/v1/capacities") or re.fullmatch(
            WORKSPACE + rf"/items(?:/{GUID}/dataAccessRoles|/{GUID}/jobs/instances)?", parts.path,
        )
        allowed = {"continuationToken"} if method == "GET" and collection else set()
        if method == "POST" and re.fullmatch(WORKSPACE + rf"/(?:semanticModels|reports)/{GUID}/getDefinition", parts.path):
            expected_format = "TMSL" if "/semanticModels/" in parts.path else "PBIR"
            if query != {"format": [expected_format]}:
                raise ValueError("Source readback requires its exact documented TMSL or PBIR format.")
            allowed = {"format"}
        if re.fullmatch(WORKSPACE + rf"/notebooks/{GUID}/jobs/execute/instances(?:/{GUID})?", parts.path):
            allowed = {"beta"}
            if method == "GET" and query.get("beta") != ["true"]:
                raise ValueError("Notebook readback requires the documented beta=true exit-value contract.")
            if method == "POST" and query.get("beta") != ["false"]:
                raise ValueError("Notebook execution requires the documented beta=false request contract.")
        if query.keys() - allowed:
            raise ValueError("Undocumented query parameters are not allowed.")
        return url, POWERBI if parts.path.endswith("/invoke") else FABRIC
    if parts.netloc == "api.powerbi.com" and not parts.query:
        datasets = rf"/v1\.0/myorg/groups/{GUID}/datasets"
        if ((method == "GET" and re.fullmatch(datasets, parts.path))
                or method in ("GET", "POST") and re.fullmatch(datasets + rf"/{GUID}/refreshes", parts.path)):
            return url, POWERBI
    if (parts.netloc == "graph.microsoft.com" and method == "GET"
            and re.fullmatch(r"/v1\.0/me/chats(?:/[A-Za-z0-9_%:@.+-]+(?:/messages)?)?", parts.path)):
        query = parse_qs(parts.query, keep_blank_values=True)
        if query.keys() - {"$top", "$skiptoken"} or (
            "$top" in query and query["$top"] != ["50"]
        ):
            raise ValueError("Only bounded, documented Teams chat pagination is allowed.")
        if parts.path == "/v1.0/me/chats" and query.get("$top") != ["50"]:
            raise ValueError("Self chat discovery requires the bounded $top=50 query.")
        return url, GRAPH
    if (parts.netloc == "onelake.dfs.fabric.microsoft.com" and method == "POST"
            and not parts.query and re.fullmatch(
                rf"/v1\.0/workspaces/{GUID}/artifacts/{GUID}/security/enable", parts.path,
            )):
        return url, STORAGE
    if parts.netloc == "onelake.dfs.fabric.microsoft.com":
        query = parse_qs(parts.query, keep_blank_values=True)
        folder = rf"/{GUID}/{GUID}/Files/(?:ReplenishmentRequests|PaymentReviewCases|RevenueInvestigations|ViewerRecommendations)"
        if method == "GET" and re.fullmatch(folder + r"/[a-f0-9]{64}\.json", parts.path) and not query:
            return url, STORAGE
        if method == "PUT" and re.fullmatch(folder, parts.path) and query == {"resource": ["directory"]}:
            return url, STORAGE
        if (method == "GET" and re.fullmatch(rf"/{GUID}", parts.path)
                and query.keys() <= {"resource", "directory", "recursive", "maxResults", "continuation"}
                and query.get("resource") == ["filesystem"] and query.get("recursive") == ["false"]
                and query.get("maxResults") == ["500"] and len(query.get("directory", [])) == 1
                and re.fullmatch(GUID + r"/Files/(?:ReplenishmentRequests|PaymentReviewCases|RevenueInvestigations|ViewerRecommendations)", query["directory"][0])
                and ("continuation" not in query or len(query["continuation"]) == 1)):
            return url, STORAGE
    raise ValueError("The destination is not an approved public API endpoint.")


class PublicApiError(RuntimeError):
    def __init__(self, method, url, status, code):
        self.status = status
        self.code = code
        super().__init__(f"{method} {urlsplit(url).path}: HTTP {status}, code {code}.")


@dataclass
class Reply:
    status: int
    headers: dict
    body: object


def cli_json(arguments):
    executable = shutil.which(arguments[0])
    if not executable:
        raise RuntimeError(f"{arguments[0]} is required. Install the documented CLI and authenticate explicitly.")
    # Windows .cmd launchers require cmd.exe; never put credentials in arguments.
    command = [executable, *arguments[1:]]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        command = '"' + os.environ["ComSpec"] + '" /d /s /c "' + subprocess.list2cmdline(command) + '"'
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=240)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("The CLI timed out. Retain pending evidence; do not replay an ambiguous write.") from error
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed. Check its installation and existing session; no login was started.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{arguments[0]} did not return the documented JSON output.") from error


class AzureCli:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id
        self.cache = {}
        self.object_id = None

    def token(self, resource):
        previous = self.cache.get(resource)
        if previous and previous[1]["exp"] > time.time() + 120:
            return previous[0]
        result = cli_json([
            "az", "account", "get-access-token", "--tenant", self.tenant_id,
            "--resource", resource, "--output", "json", "--only-show-errors",
        ])
        token = result["accessToken"]
        try:
            encoded = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if str(uuid.UUID(claims["tid"])) != self.tenant_id or claims["exp"] <= time.time() + 60:
                raise ValueError("The authenticated tenant differs or its access token is expired.")
            if not claims.get("scp") or claims.get("idtyp", "user") != "user":
                raise ValueError("EventSchemaSet creation requires a delegated user, not an app-only identity.")
            identifier = str(uuid.UUID(claims["oid"]))
            if self.object_id and self.object_id != identifier:
                raise ValueError("Fabric and Storage tokens must belong to the same selected delegated user.")
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("The existing CLI session did not provide a verifiable delegated identity.") from error
        self.cache[resource] = (token, claims)
        self.object_id = identifier
        return token

    def identity(self):
        self.token(FABRIC)
        claims = self.cache[FABRIC][1]
        return {"tenantId": self.tenant_id, "objectId": str(uuid.UUID(claims["oid"])), "objectType": "User"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class PublicApi:
    def __init__(self, tenant_id, provider="azure_cli"):
        self.tenant_id = str(uuid.UUID(tenant_id))
        if provider not in ("azure_cli", "fabric_cli"):
            raise ValueError("Choose azure_cli or fabric_cli; no alternate transport will be selected.")
        self.provider = provider
        self.tokens = AzureCli(self.tenant_id) if provider == "azure_cli" else None
        self.opener = build_opener(NoRedirect())
        self.sleep = time.sleep
        self.object_id = None
        self.account = None

    def identity(self):
        if self.tokens:
            return self.tokens.identity()
        status = cli_json(["fab", "auth", "status", "--output_format", "json"])
        if (not isinstance(status, dict) or status.get("logged_in") is not True
                or status.get("tenant_id") != self.tenant_id or not isinstance(status.get("account"), str)
                or "@" not in status["account"]):
            raise ValueError("Fabric CLI must have an authenticated delegated user in the configured tenant.")
        identifier = str(uuid.UUID(status["principal_id"]))
        if self.object_id and self.object_id != identifier:
            raise ValueError("The selected Fabric CLI delegated user changed during this operation.")
        self.object_id = identifier
        self.account = status["account"]
        return {
            "tenantId": self.tenant_id, "objectId": identifier, "objectType": "User",
        }

    def principal_name(self):
        self.identity()
        if self.tokens:
            claims = self.tokens.cache[FABRIC][1]
            account = claims.get("preferred_username") or claims.get("upn") or claims.get("unique_name")
        else:
            account = self.account
        if not isinstance(account, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", account):
            raise ValueError("The selected delegated identity has no verifiable Teams recipient address.")
        return account

    def request(self, method, address, body=None, headers=None):
        method = method.upper()
        url, resource = public_url(method, address)
        headers = headers or {}
        if set(headers) - {"If-Match", "If-None-Match", "x-ms-version"}:
            raise ValueError("Authentication and arbitrary headers cannot be overridden.")
        if set(headers) - {"If-Match"}:
            parsed = urlsplit(url)
            if (parsed.netloc != "onelake.dfs.fabric.microsoft.com"
                    or headers.get("x-ms-version") != "2021-06-08"
                    or "If-None-Match" in headers and (method != "PUT" or headers["If-None-Match"] != "*")):
                raise ValueError("Conditional OneLake headers require the exact approved storage operation.")
        for attempt in range(2):
            reply = self._request(method, url, resource, body, headers)
            if reply.status == 429 and attempt == 0:
                delay = reply.headers.get("retry-after")
                if not delay or not delay.isdigit() or not 0 <= int(delay) <= 120:
                    raise RuntimeError("Rate limit response has no bounded, valid Retry-After. Do not replay blindly.")
                print(f"WARNING: Public API rate limit; retrying once after {delay}s.", file=sys.stderr)
                self.sleep(int(delay))
                continue
            if not 200 <= reply.status < 300:
                value = reply.body if isinstance(reply.body, dict) else {}
                detail = value.get("error")
                code = value.get("errorCode") or (detail.get("code") if isinstance(detail, dict) else None)
                if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code):
                    code = "Unspecified"
                raise PublicApiError(method, url, reply.status, code)
            failed_operation = method == "GET" and re.fullmatch(
                rf"/v1/operations/{GUID}", urlsplit(url).path,
            ) and isinstance(reply.body, dict) and reply.body.get("status") == "Failed"
            if isinstance(reply.body, dict) and reply.body.get("error") and not failed_operation:
                raise RuntimeError("The public API returned an error in a successful HTTP response.")
            return reply
        raise RuntimeError("The public API rate-limit retry was exhausted.")

    def _request(self, method, url, resource, body, headers):
        if self.provider == "fabric_cli":
            if resource == POWERBI:
                raise ValueError("Power BI requests and public UDF invocation require azure_cli with the documented resource token.")
            if resource == GRAPH:
                raise ValueError("Automated Teams readback requires azure_cli with delegated Graph Chat.Read.")
            self.identity()
            prefix = FABRIC + "/v1/" if resource == FABRIC else ONELAKE + "/"
            arguments = [
                "fab", "api", url[len(prefix):], "-A", "fabric" if resource == FABRIC else "storage",
                "-X", method.lower(), "--show_headers", "--output_format", "json",
            ]
            if headers:
                arguments += ["-H", ",".join(key + "=" + value for key, value in headers.items())]
            if body is not None:
                with tempfile.TemporaryDirectory(prefix="fabric-public-request-") as directory:
                    input_file = Path(directory) / "request.json"
                    input_file.write_text(json.dumps(body, allow_nan=False), encoding="utf-8")
                    arguments += ["-i", str(input_file)]
                    result = cli_json(arguments)
            else:
                result = cli_json(arguments)
            if (not isinstance(result, dict) or type(result.get("status_code")) is not int
                    or not isinstance(result.get("headers"), dict) or "text" not in result):
                raise RuntimeError("Fabric CLI returned an unsupported API response envelope.")
            data = result.get("text")
            return Reply(result["status_code"], {k.lower(): v for k, v in result["headers"].items()},
                         None if data == "(Empty)" else data)
        data = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.tokens.token(resource), "Content-Type": "application/json", **headers,
        })
        try:
            response = self.opener.open(request, timeout=120)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError) as error:
            raise RuntimeError("Public API delivery failed or is ambiguous. Inspect the ledger; do not replay writes.") from error
        with response:
            content = response.read()
            return Reply(response.status, {key.lower(): value for key, value in response.headers.items()},
                         json.loads(content) if content else None)

    def list_all(self, address, first_reply=None):
        values, seen = [], set()
        initial = urlsplit(public_url("GET", address)[0])
        etag = first_reply.headers.get("etag") if first_reply else None
        while address:
            current = urlsplit(public_url("GET", address)[0])
            if (current.netloc, current.path) != (initial.netloc, initial.path):
                raise ValueError("The continuation changed the selected public collection.")
            if address in seen or len(seen) >= 100:
                raise ValueError("The public API repeated its continuation route.")
            seen.add(address)
            reply = first_reply or self.request("GET", address)
            first_reply = None
            if etag and reply.headers.get("etag") != etag:
                raise ValueError("The role collection changed while reading its pages. No roles will be replaced.")
            if not isinstance(reply.body, dict) or not isinstance(reply.body.get("value"), list):
                raise ValueError("The public API did not return a documented list response.")
            values.extend(reply.body["value"])
            address = reply.body.get("continuationUri")
            if not address and reply.body.get("continuationToken"):
                raise ValueError("A continuation token without its public URI cannot be silently ignored.")
        return values
