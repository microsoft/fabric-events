import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import time
import uuid

import deploy
from fabric_api import KUSTO
import native
from powerbi import refresh_model

ROOT = Path(__file__).resolve().parent
FIELDS = ("region", "period_start", "revenue", "target", "variance_pct", "detected_at")


class TestRun:
    def __init__(self, state_file):
        self.path = Path(state_file).resolve()
        state = json.loads(self.path.read_text())
        config = native.validate_config(state["configuration"])
        self.target = deploy.Deployment(config, self.path)
        self.state = self.target.state
        self.config = config
        self.api = self.target.api
        self.items = self.state["items"]
        self.workspace = config["workspace_id"]
        self.base = f"workspaces/{self.workspace}/"
        self.fixture = json.loads((ROOT / "data" / "test-cases.json").read_text())

    def save(self):
        self.target.save()

    def query_model(self):
        result = self.api.request("POST", self.api.metadata_host + (
            f"/v1.0/myorg/groups/{self.workspace}/datasets/{self.items['source']}/executeQueries"
        ), {
            "queries": [{"query": 'EVALUATE ADDCOLUMNS(RevenuePerformance, "actual_variance_pct", [variance_pct])'}],
            "serializerSettings": {"includeNulls": True},
        }).body
        if result.get("error") or len(result.get("results", [])) != 1:
            raise RuntimeError("The actual semantic-model query failed or is incomplete.")
        return result["results"][0]["tables"][0]["rows"]

    def entities(self, role):
        result = self.api.complete(self.api.request("POST", self.base + (
            f"reflexes/{self.items[role]}/getDefinition"
        )))
        return result, json.loads(native.decoded(result.get("definition", result), "ReflexEntities.json"))

    def active_publisher(self):
        _, entities = self.entities("publisher")
        sources = [e for e in entities if e["type"] == "powerBiSource-v1"]
        rules = [e for e in entities if e.get("payload", {}).get("definition", {}).get("type") == "Rule"]
        if (
            len(sources) != 1 or sources[0]["payload"]["runSettings"]["isStopped"]
            or len(rules) != 1 or not rules[0]["payload"]["definition"]["settings"]["shouldRun"]
        ):
            raise RuntimeError("The actual native source and strict detector must stay active; a paused sequence cannot resume by pretending its baseline survived.")
        return sources[0]

    def init(self):
        if self.state.get("test"):
            raise ValueError("This explicit test already has a ledger. Resume it; never replay its baseline.")
        self.target.preflight()
        if "no_inputs" not in self.state["status"] or self.query_model():
            raise ValueError("A first test may populate only its actually empty owned deployment fixture.")
        _, entities = self.entities("publisher")
        source = next(e for e in entities if e["type"] == "powerBiSource-v1")
        rules = [e for e in entities if e.get("payload", {}).get("definition", {}).get("type") == "Rule"]
        if source["payload"]["runSettings"]["isStopped"] or len(rules) != 1 or not rules[0]["payload"]["definition"]["settings"]["shouldRun"]:
            raise RuntimeError("The genuine source and strict detector must already be active before the test baseline.")
        raw = [
            e for e in entities
            if e.get("payload", {}).get("definition", {}).get("type") == "Event"
            and json.loads(e["payload"]["definition"]["instance"])["templateId"] == "SourceEvent"
        ]
        if len(raw) != 1:
            raise RuntimeError("The actual native raw source view is ambiguous.")
        run_id = str(uuid.uuid4())
        self.state["test"] = {
            "run_id": run_id, "region": "test-Revenue-" + run_id,
            "period_start": self.fixture["context"]["period_start"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_view_id": raw[0]["uniqueIdentifier"],
            "pipeline_jobs_before": self.api.list_all(self.base + f"items/{self.items['pipeline']}/jobs/instances"),
            "steps": [], "status": "explicit_external_source_test_started_not_e2e_verified",
        }
        self.save()
        self.step(0)

    def case(self, index):
        if index < len(self.fixture["sequence"]):
            return self.fixture["sequence"][index]
        invalid = self.fixture["invalid_target_cases"][index - len(self.fixture["sequence"])]
        return {"id": "invalid-target-" + str(index), "revenue": 80000, **invalid}

    def operation(self, reply, record):
        if reply.status == 200:
            return
        if reply.status != 202 or not reply.headers.get("Location"):
            raise RuntimeError("A native definition update returned an unexpected response.")
        record["operation_location"] = reply.headers["Location"]
        self.save()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = self.api.request("GET", record["operation_location"]).body.get("status")
            if status == "Succeeded":
                return
            if status not in ("Running", "NotStarted"):
                raise RuntimeError(f"The actual definition update failed: {status}.")
            time.sleep(3)
        raise TimeoutError("The recorded definition update remains pending; do not replay it.")

    def step(self, index):
        self.target.preflight()
        run = self.state["test"]
        if len(run["steps"]) != index:
            raise ValueError("Do not replay an already requested source input.")
        if index and not run["steps"][-1].get("verified"):
            raise ValueError("Actual source/guard and publication verification of the previous input is required.")
        case = self.case(index)
        route = self.base + f"semanticModels/{self.items['source']}"
        result = self.api.complete(self.api.request("POST", route + "/getDefinition?format=TMSL"))
        definition = result.get("definition", result)
        part = next(p for p in definition["parts"] if p["path"] == "model.bim")
        model = json.loads(base64.b64decode(part["payload"]))
        tables = model["model"]["tables"]
        if len(tables) != 1 or tables[0]["name"] != "RevenuePerformance" or len(tables[0]["partitions"]) != 1:
            raise RuntimeError("The exact owned external fixture contract changed.")
        expression = tables[0]["partitions"][0]["source"]["expression"]
        lines = [i for i, line in enumerate(expression) if "Source = #table(" in line]
        if len(lines) != 1:
            raise RuntimeError("The exact known external fixture source was not found.")
        expression[lines[0]] = (
            "    Source = #table(type table [region = text, period_start = text, revenue = number, target = number], "
            f'{{{{"{run["region"]}", "{run["period_start"]}", {case["revenue"]}, {case["target"]}}}}})'
        )
        record = {**case, "index": index, "requested_at": datetime.now(timezone.utc).isoformat(), "status": "source_update_pending", "verified": False}
        run["steps"].append(record)
        self.save()
        part["payload"] = base64.b64encode(json.dumps(model).encode()).decode()
        self.operation(self.api.request("POST", route + "/updateDefinition", {"definition": definition}), record)
        actual = self.api.complete(self.api.request("POST", route + "/getDefinition?format=TMSL"))
        actual_model = json.loads(native.decoded(actual.get("definition", actual), "model.bim"))
        if actual_model["model"]["tables"][0]["partitions"][0]["source"]["expression"] != expression:
            raise RuntimeError("The actual external source input did not read back.")
        refresh_model(self.api, self.workspace, self.items["source"], record, self.save)
        record["status"] = "actual_model_refresh_completed_native_observation_pending"
        self.save()
        print(json.dumps({"index": index, "case": case["id"], "region": run["region"], "native_observation_verified": False}))

    def history(self):
        result = self.api.request("POST", self.items["query_endpoint"] + "/v1/rest/query", {
            "db": self.items["database"], "csl": (
                "['" + self.config["event_name"] + "'] | where region == " + json.dumps(self.state["test"]["region"])
                + " | project " + ", ".join(FIELDS)
            ),
        }, resource=KUSTO).body
        if result.get("error") or result.get("Exceptions"):
            raise RuntimeError("Actual event history is incomplete or failed.")
        tables = [t for t in result["Tables"] if [c["ColumnName"] for c in t["Columns"]] == list(FIELDS)]
        if len(tables) != 1:
            raise RuntimeError("The actual six-field history result is ambiguous.")
        return [dict(zip(FIELDS, row)) for row in tables[0]["Rows"]]

    def observe(self, snapshot_file):
        run = self.state["test"]
        step = run["steps"][-1]
        source = self.active_publisher()
        rows = self.query_model()
        if len(rows) != 1:
            raise RuntimeError("The real source model has an unexpected row count.")
        row = rows[0]
        if any(row["RevenuePerformance[" + f + "]"] != expected for f, expected in (
            ("region", run["region"]), ("period_start", run["period_start"]),
            ("revenue", step["revenue"]), ("target", step["target"]),
        )) or row["[actual_variance_pct]"] != step["expected_variance_pct"]:
            raise RuntimeError("The actual DAX source snapshot differs from its explicit fixture.")
        instant = row["RevenuePerformance[detected_at]"]
        datetime.strptime(instant, "%Y%m%dT%H%M%SZ")
        step["actual_model_snapshot"] = row
        if step["expected_variance_pct"] is None:
            step["actual_source_measure_blank_verified"] = True
            finished = datetime.fromisoformat(step["actual_refresh"]["endTime"].replace("Z", "+00:00"))
            interval = source["payload"]["runSettings"]["executionIntervalInSeconds"]
            step.setdefault("active_guard_observation_started_at", datetime.now(timezone.utc).isoformat())
            started = datetime.fromisoformat(step["active_guard_observation_started_at"])
            step["active_guard_last_observed_at"] = datetime.now(timezone.utc).isoformat()
            if datetime.now(timezone.utc) < max(finished, started) + timedelta(seconds=2 * interval):
                self.save()
                print("Actual blank source measure verified; two active evaluation intervals remain pending.")
                return 2
            step["invalid_target_quiet_window_seconds"] = 2 * interval
        else:
            if not snapshot_file:
                raise ValueError("A real native source capture is required; elapsed time cannot substitute for a valid source sample.")
            capture = json.loads(Path(snapshot_file).read_text(encoding="utf-8-sig"))
            if capture["source_view"] != run["source_view_id"]:
                raise RuntimeError("The native capture belongs to another deployment's raw source view.")
            matches = [
                sample for sample in capture["actual_native_rows"]
                if sample["region"] == run["region"] and sample["period_start"] == run["period_start"]
                and sample["detected_at"] == instant
                and all(float(sample[f]) == step["expected_variance_pct"] if f == "variance_pct" else float(sample[f]) == step[f]
                        for f in ("revenue", "target", "variance_pct"))
            ]
            if not matches:
                self.save()
                print("The actual refreshed model is verified; its complete native source sample remains pending.")
                return 2
            identity = run["region"] + "\u2022" + run["period_start"]
            if any(sample["Composite key"] != identity for sample in matches):
                raise RuntimeError("The actual native identity changed with monetary values or refresh timestamp.")
            step["actual_native_snapshot"] = matches[0]
            step["native_observation_verified"] = True
        events = self.history()
        expected_count = sum(s["expected_new_events"] for s in run["steps"])
        step["actual_history_events"] = events
        step["actual_event_count"] = len(events)
        self.save()
        if len(events) > expected_count:
            raise RuntimeError("The strict native detector published an unexpected extra event.")
        if len(events) < expected_count:
            print("The complete native source sample is verified; actual event delivery remains pending.")
            return 2
        seen = set()
        for event in events:
            datetime.strptime(event["detected_at"], "%Y%m%dT%H%M%SZ")
            if event["detected_at"] in seen:
                raise RuntimeError("An actual crossing event is duplicated.")
            seen.add(event["detected_at"])
            matching = [
                s for s in run["steps"]
                if s["expected_new_events"] and s.get("actual_native_snapshot", {}).get("detected_at") == event["detected_at"]
                and s["revenue"] == event["revenue"] and s["target"] == event["target"]
                and s["expected_variance_pct"] == event["variance_pct"]
            ]
            if len(matching) != 1 or event["period_start"] != run["period_start"]:
                raise RuntimeError("A real event payload is incoherent with its exact complete native source sample.")
        step["verified"] = True
        step["status"] = "actual_source_or_blank_guard_and_strict_native_publication_count_verified"
        self.save()
        print(json.dumps({"index": step["index"], "actual_events": len(events), "source_sample_observed": bool(step.get("native_observation_verified")), "actual_blank_guard_verified": bool(step.get("actual_source_measure_blank_verified"))}))
        return 0

    def verify(self):
        run = self.state["test"]
        if len(run["steps"]) != 8 or not all(s.get("verified") for s in run["steps"]):
            raise RuntimeError("All six genuine states and both actual blank-target guards must be verified.")
        self.target.preflight()
        self.target.connect_history(create=False)
        for role in ("publisher", "activator", "pipeline", "notebook"):
            self.target.readback(role)
        events = self.history()
        if len(events) != 2:
            raise RuntimeError("The complete source sequence did not produce exactly two real events.")
        old = {j["id"] for j in run["pipeline_jobs_before"]}
        jobs = [j for j in self.api.list_all(self.base + f"items/{self.items['pipeline']}/jobs/instances") if j["id"] not in old]
        run["actual_pipeline_jobs"] = jobs
        self.save()
        if any(j["status"] in ("Failed", "Cancelled", "Deduped") for j in jobs):
            raise RuntimeError("An actual native investigation pipeline run failed.")
        if len(jobs) != 2 or any(j["status"] != "Completed" for j in jobs):
            print("Exactly two independently completed native pipeline executions remain pending.")
            return 2
        response = self.api.request("POST", self.items["query_endpoint"] + "/v1/rest/query", {
            "db": self.items["database"],
            "csl": "revenue_investigations | project " + ", ".join(FIELDS) + ", shortfall, revenue_lift_pct, pipeline_run_id, investigated_at",
        }, resource=KUSTO).body
        if response.get("error") or response.get("Exceptions"):
            raise RuntimeError("The actual useful investigation output query failed.")
        tables = [t for t in response["Tables"] if "pipeline_run_id" in [c["ColumnName"] for c in t["Columns"]]]
        if len(tables) != 1:
            raise RuntimeError("The actual investigation output is ambiguous.")
        columns = [c["ColumnName"] for c in tables[0]["Columns"]]
        output = [dict(zip(columns, row)) for row in tables[0]["Rows"]]
        if {r["pipeline_run_id"] for r in output} != {j["id"] for j in jobs}:
            raise RuntimeError("Persisted useful output does not match exact actual native pipeline identities.")
        ordered_jobs = sorted(jobs, key=lambda job: job["startTimeUtc"])
        ordered_events = sorted(events, key=lambda event: event["detected_at"])
        for index, job in enumerate(ordered_jobs):
            persisted = {
                tuple(row[f] for f in FIELDS) for row in output if row["pipeline_run_id"] == job["id"]
            }
            required = {tuple(event[f] for f in FIELDS) for event in ordered_events[:index + 1]}
            if not required <= persisted:
                raise RuntimeError("An actual native pipeline did not persist the triggering crossing and full preceding investigation history.")
        for row in output:
            investigated = datetime.fromisoformat(row["investigated_at"].replace("Z", "+00:00"))
            if investigated.tzinfo is None:
                raise RuntimeError("The actual investigation timestamp must include its UTC offset.")
            if (
                not any(all(row[f] == event[f] for f in FIELDS) for event in events)
                or row["shortfall"] != row["target"] - row["revenue"]
                or not math.isclose(row["revenue_lift_pct"], 100 * row["shortfall"] / row["revenue"], abs_tol=1e-8)
            ):
                raise RuntimeError("The real persisted investigation output is incoherent with its event payload.")
        run["actual_investigation_rows"] = output
        evidence = run.get("actual_teams_cards", [])
        if len(evidence) != 2 or {
            tuple(card["actual_fields"][f] for f in FIELDS) for card in evidence
        } != {tuple(event[f] for f in FIELDS) for event in events}:
            raise RuntimeError("Two actual authorized Teams cards with matching complete fields are still required.")
        run["status"] = "fresh_native_deployment_and_complete_actual_source_teams_pipeline_history_e2e_verified"
        run["verified_at"] = datetime.now(timezone.utc).isoformat()
        self.state["status"] = run["status"]
        self.save()
        print(json.dumps({"status": run["status"], "actual_crossings": 2, "actual_completed_pipeline_jobs": 2, "actual_teams_cards": 2, "actual_investigation_rows": len(output)}, indent=2))
        return 0

    def teams(self, evidence_file):
        run = self.state["test"]
        evidence = json.loads(Path(evidence_file).read_text(encoding="utf-8-sig"))
        if (
            evidence["run_id"] != run["run_id"] or evidence["region"] != run["region"]
            or evidence["status"] != "actual_authorized_teams_cards_and_full_native_snapshot_fields_verified"
        ):
            raise RuntimeError("The actual Teams evidence is not from this exact authorized test run.")
        cards = evidence["cards"]
        crossings = [s for s in run["steps"] if s.get("verified") and s["expected_new_events"]]
        expected = {
            (run["region"], run["period_start"], s["revenue"], s["target"], s["expected_variance_pct"], s["actual_native_snapshot"]["detected_at"])
            for s in crossings
        }
        if (
            len(cards) != 2 or len(expected) != 2
            or len({card["message_body_id"] for card in cards}) != 2
            or {tuple(card["actual_fields"][f] for f in FIELDS) for card in cards} != expected
        ):
            raise RuntimeError("Two distinct real Teams cards must match both independently verified native crossings.")
        run["actual_teams_cards"] = cards
        run["teams_observed_at"] = evidence["observed_at"]
        self.save()
        print("Imported actual Teams evidence for both exact verified native crossings.")

    def pause(self):
        self.target.preflight()
        for role in ("publisher", "activator"):
            exported, entities = self.entities(role)
            definition = exported.get("definition", exported)
            for entity in entities:
                rule = entity.get("payload", {}).get("definition", {})
                if rule.get("type") == "Rule":
                    rule["settings"].update(shouldRun=False, shouldApplyRuleOnUpdate=False)
                if entity["type"] == "powerBiSource-v1":
                    entity["payload"]["runSettings"]["isStopped"] = True
            record = {"role": role, "requested_at": datetime.now(timezone.utc).isoformat(), "status": "owned_test_pause_pending"}
            self.state.setdefault("test_pause_operations", []).append(record)
            self.save()
            part = next(p for p in definition["parts"] if p["path"] == "ReflexEntities.json")
            part["payload"] = base64.b64encode(json.dumps(entities).encode()).decode()
            self.operation(self.api.request("POST", self.base + f"reflexes/{self.items[role]}/updateDefinition", {"definition": definition}), record)
            actual, _ = self.entities(role)
            native.assert_definition(definition, actual.get("definition", actual))
            record["status"] = "owned_source_and_rules_paused_native_readback_verified"
            self.save()
        print("Only this owned deployment's publisher/source and independent consumer rules were paused.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("init", "step", "observe", "verify", "teams", "pause"))
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--index", type=int, choices=range(8))
    parser.add_argument("--snapshot-file", type=Path)
    parser.add_argument("--teams-file", type=Path)
    args = parser.parse_args()
    target = TestRun(args.state_file)
    if args.phase == "init":
        target.init()
    elif args.phase == "step":
        if args.index is None:
            raise ValueError("An explicit source step index is required.")
        target.step(args.index)
    elif args.phase == "observe":
        return target.observe(args.snapshot_file)
    elif args.phase == "verify":
        return target.verify()
    elif args.phase == "teams":
        if not args.teams_file:
            raise ValueError("Actual native Teams evidence is required.")
        target.teams(args.teams_file)
    else:
        target.pause()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
