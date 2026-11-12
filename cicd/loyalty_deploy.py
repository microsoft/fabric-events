"""Prepare Loyalty's public resources; native publisher and Eventstream setup remain manual."""

import ast
import json
from pathlib import Path
from string import Template
from urllib.parse import urlsplit
import uuid

from loyalty_catalog import validate_catalog
from public_consumers import definition_part, matches_definition, part
from public_deploy import Deployment, read, source_tables

ROOT = Path(__file__).resolve().parent


class LoyaltyDeployment(Deployment):
    resource_types = {
        "schema": "EventSchemaSet", "eventhouse": "Eventhouse", "source": "KQLDatabase",
        "queryset": "KQLQueryset", "record_store": "Lakehouse", "notebook": "Notebook",
    }

    def __init__(self, folder, config, state_path, api=None):
        super().__init__(folder, config, state_path, api)
        if config["scenario_id"] != "customer-loyalty-milestone":
            raise ValueError("This deployment prepares only Customer Loyalty Milestone.")
        identifiers = self.state.setdefault("loyalty_query_ids", {})
        for key in ("source", "observations", "current"):
            identifiers.setdefault(key, str(uuid.uuid4()))
        validate_catalog(read(self.folder / "catalog.json"))

    def source_connection(self):
        source = self.api.request("GET", self.base + "kqlDatabases/" + self.state["items"]["source"]).body
        self.check_item("source", source)
        if source["properties"].get("parentEventhouseItemId") != self.state["items"]["eventhouse"]:
            raise ValueError("Loyalty's source database is not the prepared Eventhouse child.")
        endpoint = source["properties"]["queryServiceUri"].rstrip("/")
        address = urlsplit(endpoint)
        if (address.scheme != "https" or not (address.hostname or "").endswith(".kusto.fabric.microsoft.com")
                or address.port not in (None, 443) or address.username or address.password
                or address.path or address.query or address.fragment):
            raise ValueError("Loyalty's source requires a public Fabric Kusto endpoint.")
        return endpoint

    def queryset_definition(self):
        identifiers = self.state["loyalty_query_ids"]
        source_query = (self.folder / "source-query.kql").read_text(encoding="utf-8")
        return {"parts": [part("RealTimeQueryset.json", {"queryset": {
            "version": "1.0.0",
            "dataSources": [{
                "id": identifiers["source"], "clusterUri": self.source_connection(),
                "type": "AzureDataExplorer", "databaseName": self.state["items"]["source"],
            }],
            "tabs": [
                {"id": identifiers["observations"], "title": "Spend observations",
                 "content": source_query, "dataSourceId": identifiers["source"]},
                {"id": identifiers["current"], "title": "Current customer spend",
                 "content": source_query + "\n| summarize arg_max(observed_at, *) by customer_id",
                 "dataSourceId": identifiers["source"]},
            ],
        }})]}

    def notebook_source(self):
        template = (self.folder / "notebook-content.py").read_text(encoding="utf-8")
        return Template(template).substitute(
            catalog_helpers=(ROOT / "loyalty_catalog.py").read_text(encoding="utf-8"),
            catalog_literal=repr(validate_catalog(read(self.folder / "catalog.json"))),
            workspace_id=self.config["workspace_id"], lakehouse_id=self.state["items"]["record_store"],
        )

    def verify_lakehouse(self):
        lakehouse = self.api.request("GET", self.base + "lakehouses/" + self.state["items"]["record_store"]).body
        self.check_item("record_store", lakehouse)
        if "defaultSchema" in lakehouse.get("properties", {}):
            raise ValueError("Loyalty's prepared catalog requires a Lakehouse without schemas.")

    def creation_request(self, role):
        if role in ("schema", "eventhouse"):
            return super().creation_request(role)
        body = {"displayName": self.config["resource_names"][role], "description": self.owner}
        if role == "source":
            body["definition"] = {"parts": [
                part("DatabaseProperties.json", {
                    "databaseType": "ReadWrite", "parentEventhouseItemId": self.state["items"]["eventhouse"],
                }),
                part("DatabaseSchema.kql", (self.folder / "DatabaseSchema.kql").read_text(encoding="utf-8")),
            ]}
            return self.base + "kqlDatabases", body
        if role == "queryset":
            body.update(type="KQLQueryset", definition=self.queryset_definition())
            return self.base + "items", body
        if role == "record_store":
            return self.base + "lakehouses", body
        if role == "notebook":
            body["definition"] = {"format": "fabricGitSource", "parts": [
                part("notebook-content.py", self.notebook_source()),
            ]}
            return self.base + "notebooks", body
        raise ValueError("The publisher and Eventstream must be configured manually, not created by this package.")

    def get_definition(self, role):
        collection = {"source": "kqlDatabases", "queryset": "items", "notebook": "notebooks"}[role]
        reply = self.api.request("POST", self.base + collection + "/" + self.state["items"][role] + "/getDefinition")
        return self.wait(reply, {}, need_result=True)["definition"]

    def verify(self, chat_id=None):
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("All six prepared Loyalty resources are required before inspection.")
        self.verify_foundations()
        self.source_connection()
        self.verify_lakehouse()
        house = self.api.request("GET", self.base + "eventhouses/" + self.state["items"]["eventhouse"]).body
        if self.state["items"]["source"] not in house["properties"]["databasesItemIds"]:
            raise ValueError("Loyalty's prepared Eventhouse does not contain its source database.")
        actual_source = self.get_definition("source")
        expected_tables = source_tables((self.folder / "DatabaseSchema.kql").read_text(encoding="utf-8"))
        if source_tables(definition_part(actual_source, "DatabaseSchema.kql")) != expected_tables:
            raise ValueError("Loyalty's source table no longer matches the spend observation contract.")
        expected_query = json.loads(definition_part(self.queryset_definition(), "RealTimeQueryset.json"))
        actual_query = json.loads(definition_part(self.get_definition("queryset"), "RealTimeQueryset.json"))
        expected, actual = expected_query["queryset"], actual_query["queryset"]
        if actual.get("version") != expected["version"]:
            raise ValueError("Loyalty's prepared Queryset version differs.")
        for collection in ("dataSources", "tabs"):
            for entry in expected[collection]:
                matches = [row for row in actual[collection] if row.get("id") == entry["id"]]
                if len(matches) != 1 or not matches_definition(entry, matches[0]):
                    raise ValueError("Loyalty's Queryset no longer matches the prepared customer-wide source.")
        actual_notebook = definition_part(self.get_definition("notebook"), "notebook-content.py")
        if ast.dump(ast.parse(actual_notebook)) != ast.dump(ast.parse(self.notebook_source())):
            raise ValueError("Loyalty's catalog Notebook or destination differs from the prepared configuration.")
        result = {
            "status": "manual_setup_required", "items": dict(self.state["items"]),
            "manual_resources": {role: self.config["resource_names"][role] for role in ("publisher", "eventstream")},
            "manual_steps": [
                "run_catalog_setup_notebook", "configure_and_activate_customer_keyed_publisher",
                "subscribe_eventstream_to_business_event", "configure_reference_join_filters_and_delta_destinations",
                "observe_native_source_baselines_then_run_kql_sample",
            ],
            "catalog_initialization_verified": False, "business_event_subscription_verified": False,
            "events_published_by_deployment": 0, "e2e_verified": False,
            "coverage": "prepared_resource_definitions_only_not_manual_setup_or_live_delivery",
        }
        self.state["verification"] = result
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Loyalty prepares its six resources together.")
        if self.state.get("pending"):
            if not resume:
                raise ValueError("Resume the exact pending public ledger before continuing.")
            self.preflight(resuming=True)
            self.resume_pending()
        self.preflight()
        self.create("schema")
        self.verify_schema()
        self.roles(create=True)
        for role in ("eventhouse", "source", "queryset", "record_store", "notebook"):
            self.create(role)
            if role == "record_store":
                self.verify_lakehouse()
        result = self.verify()
        self.state["status"] = "manual_setup_required"
        self.save()
        return result
