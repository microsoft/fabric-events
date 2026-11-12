"""Revenue hybrid deployment: public provisioning and an explicit manual handoff."""

import copy
from pathlib import Path

from public_deploy import Deployment, read
from revenue_components import RevenueComponents
from revenue_source import RevenueSourceContract


class RevenueDeployment(Deployment):
    resource_types = {**RevenueSourceContract.resource_types, **RevenueComponents.resource_types}

    def __init__(self, folder, config, state_path, api=None, source_state_path=None, component_state_path=None):
        super().__init__(folder, config, state_path, api)
        if self.state["items"] and self.state.get("revenue_public_hybrid_version") != 3:
            raise ValueError("The ledger does not match this exercise's resources. No resource adoption is permitted.")
        defaults = {
            "source": str(Path(source_state_path or self.path.with_name(self.path.stem + ".source.json")).resolve()),
            "components": str(Path(component_state_path or self.path.with_name(self.path.stem + ".components.json")).resolve()),
        }
        recorded = self.state.get("child_state_files", defaults)
        if (source_state_path and recorded["source"] != defaults["source"]
                or component_state_path and recorded["components"] != defaults["components"]):
            raise ValueError("The hybrid deployment cannot be rebound to different source/component ledgers.")
        if len({self.path.resolve(), Path(recorded["source"]).resolve(), Path(recorded["components"]).resolve()}) != 3:
            raise ValueError("The hybrid deployment requires three distinct exact ledger files.")
        self.state.update(revenue_public_hybrid_version=3, child_state_files=recorded)
        self.source = RevenueSourceContract(folder, config, Path(recorded["source"]), api)
        self.components = None
        self.sync_children()

    def load_components(self):
        self.components = RevenueComponents(
            self.folder, self.config, Path(self.state["child_state_files"]["components"]),
            Path(self.state["child_state_files"]["source"]), self.api,
        )
        return self.components

    def sync_children(self):
        items = dict(self.source.state["items"])
        path = Path(self.state["child_state_files"]["components"])
        if path.exists():
            components = self.load_components()
            items.update(components.state["items"])
        pending = [child.state["pending"] for child in (self.source, self.components)
                   if child is not None and child.state.get("pending")]
        if len(pending) > 1:
            raise ValueError("Resolve the exact source operation before starting component provisioning.")
        if pending:
            self.state["pending"] = copy.deepcopy(pending[0])
        else:
            self.state.pop("pending", None)
        if any(items.get(role) != identifier for role, identifier in self.state["items"].items()):
            raise ValueError("The exact child deployment identities changed; no resource will be adopted.")
        self.state["items"] = items

    def verify(self, unused_chat_id=None):
        self.sync_children()
        if self.state.get("pending"):
            raise ValueError("Resume the exact pending provisioning before verification.")
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Resume the exact incomplete hybrid provisioning before verification.")
        self.source.api = self.api
        self.source.verify_source()
        self.source.verify_queryset()
        publisher = self.source.verify_publisher(allow_manual_rules=True)
        publication = self.source.state.get("publisher_publication")
        if not publication or publication["phase"] != "verified" or publication["schema_id"] != self.state["items"]["schema"]:
            raise ValueError("Prepare and verify the exact stopped publication rule before the manual handoff.")
        publisher_rule = next(entity for entity in publisher
                              if entity["uniqueIdentifier"] == self.source.state["source_ids"]["publication_rule"])
        components = self.load_components()
        components.verify_components()
        if not components.state.get("consumer_rule_enabled"):
            raise ValueError("Resume the exact CI/CD consumer activation before reporting the manual handoff.")
        result = {
            "status": "manual_setup_required", "implemented_scope": "hybrid_deployment",
            "ci_cd_configuration_verified": True, "items": copy.deepcopy(self.state["items"]),
            "consumer_rule_enabled": components.state.get("consumer_rule_enabled", False),
            "business_event_publication_configured": True,
            "publication_rule_enabled": publisher_rule["payload"]["definition"]["settings"]["shouldRun"],
            "manual_steps": [
                "pin_current_revenue_query_to_real_time_dashboard",
                "review_then_activate_preconfigured_kql_publication_rule",
                "append_source_snapshots_and_observe_each_state",
                "observe_native_source_states_and_verify_real_pipeline_and_persisted_analysis",
            ],
            "publication_rule_managed_by_cicd": True, "eventhouse_subscription_configured": False,
            "source_inputs_submitted": 0, "e2e_verified": False,
            "native_pipeline_invocation_verified": False, "persisted_analysis_verified": False,
            "source_eventhouse_required": True, "managed_event_history_required": False,
            "eventhouse_required": True,
            "manual_resources": {"dashboard": self.config["resource_names"]["dashboard"]},
        }
        self.state.update(status=result["status"], verification=result)
        self.save()
        return result

    def deploy(self, scope="full", resume=False):
        if scope != "full":
            raise ValueError("Revenue uses the hybrid deployment without additional scope parameters.")
        self.sync_children()
        self.preflight(resuming=resume)
        self.source.api = self.api
        if not self.state.get("hybrid_provisioning_completed"):
            try:
                if self.source.state.get("pending"):
                    if not resume:
                        raise ValueError("Resume the exact pending native source operation.")
                    self.source.preflight(resuming=True)
                    self.source.resume_pending()
                self.source.prepare()
                self.load_components().prepare(resume=resume)
            finally:
                self.sync_children()
                self.save()
            self.state["hybrid_provisioning_completed"] = True
            self.save()
        components = self.load_components()
        self.source.verify_source()
        self.source.verify_queryset()
        components.verify_foundations()
        self.source.configure_publication(components.state["items"]["schema"])
        if not components.state.get("consumer_rule_enabled"):
            if not resume and components.state.get("pending_consumer_activation"):
                raise ValueError("Resume the exact recorded consumer activation, not another deployment.")
            self.source.verify_source()
            self.source.verify_queryset()
            self.source.verify_publisher(allow_manual_rules=True)
            components.enable_consumer()
        return self.verify()

    def test(self, unused_chat_id=None):
        self.sync_children()
        self.preflight()
        if set(self.state["items"]) != set(self.resource_types):
            raise ValueError("Complete the hybrid deployment before starting a manual source test.")
        result = {
            "status": "manual_source_trigger_required", "source_triggered": False,
            "automated_source_trigger_available": False, "automated_e2e_test_available": False,
            "e2e_verified": False, "source_inputs_submitted": 0,
            "outcome_verification_requested": False,
            "source_item_name": self.config["resource_names"]["source"],
            "queryset_item_name": self.config["resource_names"]["queryset"],
            "dashboard_item_name": self.config["resource_names"]["dashboard"],
            "publisher_item_name": self.config["resource_names"]["publisher"],
            "manual_trigger_steps": [
                "Review the KQL publication rule and stable region-period identity, then activate it.",
                "In the source Queryset, run sample-input.kql to append the baseline snapshot.",
                "Observe the actual native baseline: revenue 90000, target 100000, variance -10.",
                "Keep the same region and period; append snapshots at 85000 and then 80000, observing each state.",
                "Observe the native variance -20 crossing, then inspect the Activator action, pipeline run and Lakehouse investigation.",
            ],
            "direct_consumer_invocation_performed": False,
        }
        self.state.update(status=result["status"], trigger=result)
        self.save()
        return result
