"""Native UDF publication of WMS findings through documented public APIs."""

import datetime
import re
import time
import uuid

from public_api import POWERBI
from action_deploy import ActionDeployment
from public_consumers import part
from public_deploy import read
from public_udf import publisher_definition, verify_publisher_definition

FUNCTION = "publish_low_stock_event"
PARAMETERS = {
    "productId": ("product_id", "str"),
    "productName": ("product_name", "str"),
    "storeId": ("store_id", "str"),
    "currentStock": ("current_stock", "int"),
    "threshold": ("threshold", "int"),
    "supplierId": ("supplier_id", "str"),
    "detectedAt": ("detected_at", "str"),
}


def fixture_payloads(fixtures, run_id, detected_at):
    run_id = str(uuid.UUID(run_id))
    instant = datetime.datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
    if instant.utcoffset() != datetime.timedelta(0):
        raise ValueError("Fixture detection timestamps must use UTC.")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Provide a nonempty list of synthetic WMS findings.")
    result, identifiers = [], set()
    for row in fixtures:
        if not isinstance(row, dict) or set(row) != {
            "id", "productName", "storeId", "currentStock", "threshold", "supplierId",
        }:
            raise ValueError("WMS fixtures must contain the exact reviewed fields.")
        identifier = row["id"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", identifier)
                or identifier in identifiers):
            raise ValueError("WMS fixture IDs must be distinct portable lowercase labels.")
        identifiers.add(identifier)
        for key, maximum in (("productName", 256), ("storeId", 128), ("supplierId", 128)):
            if not isinstance(row[key], str) or not row[key].strip() or len(row[key].strip()) > maximum:
                raise ValueError(f"Invalid WMS fixture {key}.")
        for key in ("currentStock", "threshold"):
            if type(row[key]) is not int or not -(2 ** 31) <= row[key] < 2 ** 31:
                raise ValueError(f"WMS fixture {key} must fit a signed Avro int.")
        result.append({
            "productId": f"cicd-{run_id}-{identifier}", "detectedAt": instant.isoformat().replace("+00:00", "Z"),
            **{key: row[key].strip() if isinstance(row[key], str) else row[key]
               for key in PARAMETERS if key not in ("productId", "detectedAt")},
        })
    return result


def card_matches(content, payload):
    for parameter, (field, kind) in PARAMETERS.items():
        value = payload[parameter]
        if kind == "int":
            values = re.findall(rf"{field}[^A-Za-z0-9+-]*([+-]?\d+)(?![\d.])", content)
            if not any(int(actual) == value for actual in values):
                return False
        elif field not in content or value not in content:
            return False
    return True


class InventoryDeployment(ActionDeployment):
    resource_types = {
        "schema": "EventSchemaSet", "publisher": "UserDataFunction", "activator": "Reflex",
        "record_store": "Lakehouse", "response_notebook": "Notebook", "response_pipeline": "DataPipeline",
    }

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("inventory_public_version") != 2:
            raise ValueError("The ledger does not match this exercise's resources. No resource adoption is permitted.")
        self.state["inventory_public_version"] = 2

    def preflight(self, resuming=False):
        if self.config["auth_provider"] != "azure_cli":
            raise ValueError("This UDF deployment requires azure_cli for the documented Power BI invocation audience.")
        result = super().preflight(resuming)
        self.api.tokens.token(POWERBI)
        return result

    def udf_definition(self):
        return publisher_definition(
            self.folder, self.config, self.state["items"]["schema"], FUNCTION,
            "Publish a low-stock finding already detected by the WMS.", PARAMETERS,
        )

    def creation_request(self, role):
        if role not in ("publisher", "activator"):
            return super().creation_request(role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "publisher":
            body["definition"] = self.udf_definition()
            return self.base + "userDataFunctions", body
        body.update(type="Reflex", definition={"parts": [part("ReflexEntities.json", self.activator_entities())]})
        return self.base + "items", body

    def verify_udf(self):
        return verify_publisher_definition(self)

    def verify_provisioning(self):
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Deploy the complete schema, UDF publisher and Activator before testing.")
        self.verify_schema()
        self.roles()
        self.check_activator(self.get_definition("activator"))
        self.verify_udf()
        self.verify_response()
        result = {
            "status": "inventory_public_provisioning_verified_e2e_pending", "items": self.state["items"],
            "source_binding_verified": True, "e2e_verified": False, "eventhouse_subscription_configured": False,
            "udf_metadata_readback": self.state["udf_metadata_readback"],
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Use the shared runner for explicit schema-only foundations.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public create, never repeat it.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        self.create("record_store")
        self.prepare_record_store()
        self.create("response_notebook")
        self.create("response_pipeline")
        self.create("activator")
        self.start_activator()
        self.create("publisher")
        result = self.verify_provisioning()
        self.state["status"] = result["status"]
        self.save()
        return result

    def test(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("This scenario uses Activator (Data Pipeline), not a Teams chat.")
        if self.state.get("test_run"):
            raise ValueError("This deployment already has a test attempt. Use verify; do not replay its invocations.")
        result = self.verify_provisioning()
        if self.list_records():
            raise ValueError("A fresh source test requires an empty owned operational record folder.")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        run_id = str(uuid.uuid4())
        payloads = fixture_payloads(read(self.folder / "fixtures.json"), run_id, now)
        attempt = {
            "run_id": run_id, "started_at": now, "payloads": payloads, "chat_id": chat_id,
            "phase": "invoking_native_udf", "invocations": [],
        }
        self.state["test_run"] = attempt
        self.save()
        for payload in payloads:
            attempt["pending_input"] = payload["productId"]
            self.save()
            reply = self.api.request(
                "POST", self.base + f"userDataFunctions/{self.state['items']['publisher']}/functions/{FUNCTION}/invoke",
                payload,
            )
            attempt["last_response"] = {"http_status": reply.status, "body": reply.body}
            self.save()
            response = reply.body
            if (reply.status != 200 or not isinstance(response, dict) or response.get("functionName") != FUNCTION
                    or response.get("status") != "Succeeded" or response.get("errors") != []
                    or not isinstance(response.get("invocationId"), str) or not response["invocationId"]
                    or not isinstance(response.get("output"), dict)
                    or set(response["output"]) != {"event_published"}
                    or response["output"]["event_published"] is not True):
                raise RuntimeError("The native UDF did not confirm successful execution. Retain this attempt; never replay.")
            if any(entry["response"]["invocationId"] == response["invocationId"] for entry in attempt["invocations"]):
                raise ValueError("The UDF returned a repeated invocation identity.")
            attempt["invocations"].append({"product_id": payload["productId"], "response": response})
            attempt.pop("pending_input")
            self.save()
        attempt["phase"] = "native_invocations_succeeded_delivery_pending"
        self.save()
        result.update(
            status="source_triggered_verification_pending", source_triggered=True,
            source_run_id=run_id, source_execution_verified=True,
            native_udf_invocations=len(attempt["invocations"]),
            native_udf_invocation_ids=[entry["response"]["invocationId"] for entry in attempt["invocations"]],
            source_item_id=self.state["items"]["publisher"],
            product_ids=[payload["productId"] for payload in payloads],
            events_published=None, e2e_verified=False, outcome_verification_requested=False,
        )
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result

    def verify(self, chat_id=None):
        if chat_id is not None:
            raise ValueError("This scenario uses Activator (Data Pipeline), not a Teams chat.")
        result = self.verify_provisioning()
        attempt = self.state.get("test_run")
        if not attempt:
            return result
        if attempt.get("pending_input") or len(attempt["invocations"]) != len(attempt["payloads"]):
            raise ValueError("UDF execution is incomplete or ambiguous. Retain this run; never replay its inputs.")
        result.update(
            source_run_id=attempt["run_id"], source_execution_verified=True,
            native_udf_invocations=len(attempt["invocations"]), events_published=None,
            status="inventory_public_invocations_succeeded_delivery_pending",
        )
        expected = [
            {field: payload[parameter] for parameter, (field, _) in PARAMETERS.items()}
            for payload in attempt["payloads"]
        ]
        return self.verify_business_outcomes(attempt, result, expected)
