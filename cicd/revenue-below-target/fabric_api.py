"""Small Fabric transport. Credentials stay in memory and redirects are refused."""

import base64
import datetime
import email.utils
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

FABRIC = "https://api.fabric.microsoft.com"
POWERBI = "https://analysis.windows.net/powerbi/api"
DAILY = "https://wabi-daily-us-east2-redirect.analysis.windows.net"
DAILY_API = "https://dailyapi.powerbi.com"
DAILY_FABRIC = "https://dailyapi.fabric.microsoft.com"
MSIT = "https://df-msit-scus-redirect.analysis.windows.net"
MSIT_FABRIC = "https://msitapi.fabric.microsoft.com"
STORAGE = "https://storage.azure.com"
KUSTO = "https://api.kusto.windows.net"


def decode_claims(token):
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class CliTokens:
    def __init__(self):
        self.cache = {}

    def get(self, resource):
        cached = self.cache.get(resource)
        if cached and decode_claims(cached).get("exp", 0) > time.time() + 300:
            return cached
        token = self.acquire(resource)
        if decode_claims(token).get("exp", 0) <= time.time() + 60:
            raise RuntimeError("The acquired access token is already expired or about to expire.")
        self.cache[resource] = token
        return token

    def acquire(self, resource):
        executable = shutil.which("az")
        if not executable:
            raise RuntimeError("Azure CLI is required. No authentication window will be opened.")
        result = subprocess.run(
            [executable, "account", "get-access-token", "--resource", resource,
             "--output", "json", "--only-show-errors"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(
                "The existing Azure CLI session cannot acquire the required token. "
                "No browser or interactive login was started."
            )
        return json.loads(result.stdout)["accessToken"]

    def identity(self, resource=FABRIC):
        claims = decode_claims(self.get(resource))
        if "idtyp" in claims and claims["idtyp"] != "user":
            raise RuntimeError("Event Schema Set creation requires a supported user identity.")
        if "scp" not in claims:
            raise RuntimeError("This bootstrap requires a delegated user token, not an app-only token.")
        return {"tenantId": claims["tid"], "objectId": claims["oid"], "objectType": "User"}

    def current_user_address(self, resource=FABRIC):
        self.identity(resource)
        claims = decode_claims(self.get(resource))
        address = claims.get("preferred_username") or claims.get("upn") or claims.get("unique_name")
        if not isinstance(address, str) or "@" not in address or any(character.isspace() for character in address):
            raise RuntimeError("The existing delegated session does not provide a Teams recipient address.")
        return address


class NotebookTokens(CliTokens):
    def __init__(self, credentials):
        super().__init__()
        self.credentials = credentials

    def acquire(self, resource):
        audiences = {FABRIC: "pbi", POWERBI: "pbi", STORAGE: "storage", KUSTO: "kusto"}
        if resource not in audiences:
            raise ValueError("This resource has no supported NotebookUtils token audience.")
        return self.credentials.getToken(audiences[resource])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Reply:
    status: int
    headers: dict
    body: object


def retry_after(headers):
    value = headers.get("Retry-After", "2")
    try:
        return max(0, int(value))
    except ValueError:
        instant = email.utils.parsedate_to_datetime(value)
        return max(0, (instant - datetime.datetime.now(datetime.timezone.utc)).total_seconds())


def operation_error_codes(value):
    codes = []
    if isinstance(value, dict):
        for key in ("errorCode", "code"):
            code = value.get(key)
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", code):
                codes.append(code)
        for key in ("error", "moreDetails", "details"):
            codes.extend(operation_error_codes(value.get(key)))
    elif isinstance(value, list):
        for child in value:
            codes.extend(operation_error_codes(child))
    return list(dict.fromkeys(codes))


class FabricOperationError(RuntimeError):
    def __init__(self, status, error_codes):
        self.status = status
        self.error_codes = error_codes
        detail = " Error codes: " + ", ".join(error_codes) + "." if error_codes else ""
        super().__init__(f"Fabric operation terminated with status {status!r}.{detail}")


class FabricRequestError(RuntimeError):
    def __init__(self, method, route, status, error_codes, details=None):
        self.status = status
        self.error_codes = error_codes
        self.details = details
        detail = ", ".join(error_codes) if error_codes else "RequestFailed"
        super().__init__(f"{method} {route} failed: HTTP {status}, code {detail}.")


class FabricApi:
    def __init__(self, tokens=None, opener=None, sleep=time.sleep, environment="fabric"):
        if environment not in ("fabric", "daily", "msit"):
            raise ValueError("environment must be fabric, daily or msit.")
        self.environment = environment
        self.control_host = {"fabric": FABRIC, "daily": DAILY_FABRIC, "msit": MSIT_FABRIC}[environment]
        self.metadata_host = {"daily": DAILY, "msit": MSIT}.get(environment)
        self.control_resource = POWERBI if self.metadata_host else FABRIC
        self.tokens = tokens or CliTokens()
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.sleep = sleep

    def check_url(self, url, resource):
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        permitted = {
            FABRIC: host == "api.fabric.microsoft.com",
            POWERBI: host in ((
                "dailyapi.fabric.microsoft.com", "dailyapi.powerbi.com",
                "wabi-daily-us-east2-redirect.analysis.windows.net",
            ) if self.environment == "daily" else (
                "msitapi.fabric.microsoft.com", "df-msit-scus-redirect.analysis.windows.net",
            ) if self.environment == "msit" else ()),
            STORAGE: host in {
                "fabric": ("onelake.dfs.fabric.microsoft.com",),
                "daily": ("daily-onelake.dfs.fabric.microsoft.com", "daily-onelake.pbidedicated.windows.net"),
                "msit": ("msit-onelake.pbidedicated.windows.net",),
            }[self.environment],
            KUSTO: host.endswith(".kusto.fabric.microsoft.com"),
        }.get(resource, False)
        if (
            not permitted or parsed.scheme != "https" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment
        ):
            raise ValueError("Refusing to send credentials to an unexpected service URL.")

    def request(self, method, path, body=None, resource=FABRIC, extra_headers=None):
        if resource == FABRIC:
            resource = self.control_resource
        url = self.control_host + "/v1/" + path.lstrip("/") if not path.startswith("https://") else path
        self.check_url(url, resource)
        return self._send(
            method, url, body, lambda: "Bearer " + self.tokens.get(resource), extra_headers,
        )

    def kusto_connection_request(self, method, capacity_id, database_id, mwc_token, body=None, target_host=None):
        if self.environment not in ("daily", "msit"):
            raise ValueError("The workload association bridge is scoped to Daily and MSIT environments.")
        if method not in ("GET", "POST"):
            raise ValueError("The Business Event association bridge supports only GET and POST.")
        capacity_id, database_id = str(uuid.UUID(capacity_id)), str(uuid.UUID(database_id))
        if not isinstance(mwc_token, str) or not mwc_token:
            raise ValueError("Kusto workload authorization is unavailable.")
        if not isinstance(target_host, str) or not target_host:
            raise ValueError("The native workload response must provide its routed TargetUriHost.")
        address = urllib.parse.urlsplit(
            target_host if target_host.startswith("https://") else "https://" + target_host
        )
        if (
            address.scheme != "https" or not (address.hostname or "").endswith((
                ".analysis.windows.net", ".pbidedicated.windows.net", ".fabric.microsoft.com",
            ))
            or address.port not in (None, 443) or address.username or address.password
            or address.path not in ("", "/") or address.query or address.fragment
        ):
            raise ValueError("Refusing to send workload authorization to an unexpected routed host.")
        url = "https://" + address.netloc + (
            f"/webapi/capacities/{capacity_id}/workloads/Kusto/KustoService/direct/v1"
            f"/databases/{database_id}/businessEventDataConnections"
        )
        return self._send(method, url, body, lambda: "MwcToken " + mwc_token)

    def _send(self, method, url, body, authorization, extra_headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(2):
            headers = {
                "Authorization": authorization(),
                "Accept": "application/json",
            }
            if data is not None:
                headers["Content-Type"] = "application/json"
            headers.update(extra_headers or {})
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                response = self.opener.open(request, timeout=120)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read()
                try:
                    payload = json.loads(raw) if raw else {}
                except (ValueError, UnicodeDecodeError):
                    raise RuntimeError(f"{method} returned non-JSON data (HTTP {response.status}).") from None
                reply = Reply(response.status, response.headers, payload)
            codes = operation_error_codes(payload)
            code = codes[0] if codes else "RequestFailed"
            if reply.status == 429 and attempt == 0:
                self.sleep(retry_after(reply.headers))
                continue
            if (
                reply.status == 409 and code == "ItemDisplayNameNotAvailableYet" and attempt == 0
                and not urllib.parse.urlsplit(url).path.endswith("/items/bulkImportDefinitions")
            ):
                delay = max(30, retry_after(reply.headers))
                print(
                    f"WARNING: ItemDisplayNameNotAvailableYet; retrying the same request once in {delay}s.",
                    file=sys.stderr,
                )
                self.sleep(delay)
                continue
            if not 200 <= reply.status < 300:
                route = urllib.parse.urlsplit(url).path
                raise FabricRequestError(method, route, reply.status, codes, payload)
            return reply
        raise RuntimeError("Rate-limit retry exhausted.")

    def complete(self, reply, timeout=1200):
        if reply.status != 202:
            return reply.body
        location = reply.headers.get("Location")
        if not location:
            raise RuntimeError("Accepted operation did not return a polling Location.")
        self.check_url(location, self.control_resource)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sleep(retry_after(reply.headers))
            reply = self.request("GET", location)
            status = reply.body.get("status")
            if status == "Succeeded":
                return self.request("GET", location.rstrip("/") + "/result").body
            if status not in ("NotStarted", "Running"):
                raise FabricOperationError(status, operation_error_codes(reply.body))
        raise TimeoutError("Fabric operation did not finish. Do not blindly resubmit the create request.")

    def list_all(self, path):
        items = []
        seen = set()
        while path:
            if path in seen:
                raise RuntimeError("Fabric returned a repeated continuation URI.")
            seen.add(path)
            body = self.request("GET", path).body
            items.extend(body["value"])
            path = body.get("continuationUri")
            if not path and body.get("continuationToken"):
                raise RuntimeError("A continuation token was returned without a continuation URI.")
        return items
