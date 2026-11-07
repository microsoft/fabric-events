"""Provision or verify only the explicitly configured synthetic order backend."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from fabric_api import NoRedirect

ROOT = Path(__file__).resolve().parent


def az(*arguments):
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI is required; no login window will be opened.")
    try:
        result = subprocess.run(
            [executable, *arguments, "--only-show-errors"], capture_output=True, text=True,
            check=False, timeout=900,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Azure {' '.join(arguments[:2])} timed out; inspect the saved operation before resuming."
        ) from None
    if result.returncode:
        raise RuntimeError(f"Azure {' '.join(arguments[:2])} failed (exit {result.returncode}).")
    return result.stdout.strip()


class Backend:
    def __init__(self, config, state, save, directory):
        self.config = config
        self.state = state
        self.save = save
        self.directory = Path(directory)
        self.opener = urllib.request.build_opener(NoRedirect())
        self.key = None
        self.base_url = None

    def command(self, *arguments):
        return az(
            *arguments, "--subscription", self.config["subscription_id"],
            "--resource-group", self.config["resource_group"],
        )

    def resources(self):
        return json.loads(self.command("resource", "list", "-o", "json"))

    def provision(self):
        if self.config["mode"] == "existing":
            self.connect()
            return
        owned = self.state.setdefault("azure_backend", {
            "attempt_id": str(uuid.uuid4()), "completed_steps": [],
        })
        self.save()
        names = {
            "storage-account": self.config["storage_account"],
            "identity": self.config["identity_name"],
            "function-app": self.config["function_app"],
        }
        inventory = self.resources()
        for step, name in names.items():
            matches = [resource for resource in inventory if resource["name"].casefold() == name.casefold()]
            if matches and (
                len(matches) != 1
                or matches[0].get("tags", {}).get("fabric-events-attempt") != owned["attempt_id"]
            ):
                raise ValueError(f"Unmanaged Azure name collision for {step}; nothing will be adopted.")
        pending = owned.get("pending_step")
        if pending:
            if pending not in names:
                raise ValueError(f"Azure step {pending} is unresolved; inspect before resuming.")
            found = [resource for resource in inventory if resource["name"] == names[pending]]
            if len(found) != 1:
                raise ValueError("An ambiguous Azure creation has no exact owned readback; it will not be replayed.")
            owned["completed_steps"].append(pending)
            owned.pop("pending_step")
            self.save()

        def step(name, operation):
            if name in owned["completed_steps"]:
                return
            owned["pending_step"] = name
            self.save()
            operation()
            owned["completed_steps"].append(name)
            owned.pop("pending_step")
            self.save()

        tags = ["--tags", "owner=fabric-events-cicd", "fabric-events-attempt=" + owned["attempt_id"]]
        step("storage-account", lambda: self.command(
            "storage", "account", "create", "--name", self.config["storage_account"],
            "--location", self.config["location"], "--sku", "Standard_LRS", "--kind", "StorageV2",
            "--https-only", "true", "--min-tls-version", "TLS1_2",
            "--allow-blob-public-access", "false", "--allow-shared-key-access", "false", *tags, "-o", "none",
        ))
        step("identity", lambda: self.command(
            "identity", "create", "--name", self.config["identity_name"],
            "--location", self.config["location"], *tags, "-o", "none",
        ))
        identity = json.loads(self.command("identity", "show", "--name", self.config["identity_name"], "-o", "json"))
        scope = (
            f"/subscriptions/{self.config['subscription_id']}/resourceGroups/{self.config['resource_group']}"
            f"/providers/Microsoft.Storage/storageAccounts/{self.config['storage_account']}"
        )
        for role in ("Storage Blob Data Owner", "Storage Table Data Contributor"):
            role_id = str(uuid.uuid5(uuid.UUID(owned["attempt_id"]), role))
            step("role-" + role_id, lambda role=role, role_id=role_id: az(
                "role", "assignment", "create", "--subscription", self.config["subscription_id"],
                "--name", role_id, "--assignee-object-id", identity["principalId"],
                "--assignee-principal-type", "ServicePrincipal", "--role", role, "--scope", scope, "-o", "none",
            ))
        step("function-app", lambda: self.command(
            "functionapp", "create", "--name", self.config["function_app"],
            "--storage-account", self.config["storage_account"],
            "--flexconsumption-location", self.config["location"],
            "--runtime", "python", "--runtime-version", "3.11",
            "--deployment-storage-auth-type", "UserAssignedIdentity",
            "--deployment-storage-auth-value", self.config["identity_name"],
            "--instance-memory", "512", "--maximum-instance-count", "40",
            "--disable-app-insights", "true", *tags, "-o", "none",
        ))

        def settings():
            self.command("functionapp", "update", "--name", self.config["function_app"],
                         "--set", "httpsOnly=true", "-o", "none")
            self.command(
                "functionapp", "config", "appsettings", "set", "--name", self.config["function_app"],
                "--settings", f"ORDER_TABLE_ENDPOINT=https://{self.config['storage_account']}.table.core.windows.net",
                "ORDER_TABLE_NAME=" + self.config["table_name"], "ORDER_IDENTITY_CLIENT_ID=" + identity["clientId"],
                "AzureWebJobsStorage__accountName=" + self.config["storage_account"],
                "AzureWebJobsStorage__credential=managedidentity",
                "AzureWebJobsStorage__clientId=" + identity["clientId"], "-o", "none",
            )
            self.command(
                "functionapp", "config", "appsettings", "delete", "--name", self.config["function_app"],
                "--setting-names", "AzureWebJobsStorage", "-o", "none",
            )

        step("settings", settings)

        def deploy_code():
            archive = self.directory / "test-order-api.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                for name in ("function_app.py", "orders.py", "requirements.txt", "host.json"):
                    output.write(ROOT / "test-order-api" / name, name)
            self.command(
                "functionapp", "deployment", "source", "config-zip", "--name", self.config["function_app"],
                "--src", str(archive), "--build-remote", "true", "--timeout", "600", "-o", "none",
            )

        step("code", deploy_code)
        self.connect(wait=True)
        owned["status"] = "protected_synthetic_backend_ready_no_orders_created"
        self.save()

    def connect(self, wait=False):
        app = json.loads(self.command("functionapp", "show", "--name", self.config["function_app"], "-o", "json"))
        host = app.get("defaultHostName") or app.get("properties", {}).get("defaultHostName")
        if not isinstance(host, str) or not re.fullmatch(r"[a-z0-9-]+\.azurewebsites\.net", host):
            raise ValueError("The configured Function App has no verified Azure hostname.")
        if not (app.get("httpsOnly") or app.get("properties", {}).get("httpsOnly")):
            raise ValueError("The synthetic backend must enforce HTTPS.")
        self.base_url = "https://" + host + "/api/test"
        self.key = self.command(
            "functionapp", "keys", "list", "--name", self.config["function_app"],
            "--query", "functionKeys.default", "-o", "tsv",
        )
        if not self.key:
            raise ValueError("The synthetic backend Function key is unavailable.")
        deadline = time.monotonic() + (300 if wait else 0)
        while True:
            try:
                health = self.request("GET", "/health")
                break
            except urllib.error.HTTPError as error:
                if error.code not in (404, 502, 503) or time.monotonic() >= deadline:
                    raise RuntimeError(f"Synthetic backend health failed: HTTP {error.code}.") from None
                time.sleep(5)
        if health.get("status") != "ready" or health.get("test_only") is not True:
            raise ValueError("The configured backend is not the isolated synthetic order service.")
        if health.get("table") != self.config["table_name"]:
            raise ValueError("The backend's actual table differs from the configuration.")

    def request(self, method, path, body=None, expected=200):
        if not self.key or not self.base_url:
            raise ValueError("Connect to the configured backend before making a request.")
        if not re.fullmatch(r"/health|/orders(?:/cicd-[a-f0-9-]{36}-[a-z0-9-]+(?:/(?:hold|manual_review|release|fulfill))?)?", path):
            raise ValueError("Unexpected synthetic order service route.")
        request = urllib.request.Request(
            self.base_url + path, data=json.dumps(body).encode() if body is not None else None,
            headers={"x-functions-key": self.key, "Content-Type": "application/json"}, method=method,
        )
        try:
            response = self.opener.open(request, timeout=90)
        except urllib.error.HTTPError as error:
            if error.code != expected:
                raise
            response = error
        with response:
            if response.status != expected:
                raise RuntimeError(f"Order operation returned HTTP {response.status}, expected {expected}.")
            return json.load(response)
