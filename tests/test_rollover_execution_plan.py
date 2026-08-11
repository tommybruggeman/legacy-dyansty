from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from season_engine.dry_run_simulator import RolloverDryRunSimulator, RolloverDryRunValidator, to_execution_plan_input
from season_engine.execution_plan import (
    ExecutionPlanOperation, RolloverExecutionPlanValidator, RolloverExecutionPlanner,
    TrustedExecutionPlanService, execution_plan_readiness,
)
from tests.test_rollover_dry_run_simulator import source


class Response:
    def __init__(self, data): self.data = data
class Call:
    def __init__(self, data): self.data = data
    def execute(self): return Response(self.data)
class Client:
    def __init__(self, data, tables=None): self.data = data; self.calls = []; self.tables = tables or {}
    def rpc(self, name, args):
        self.calls.append((name, args))
        if name == "get_rollover_execution_plan_material_authenticated":
            return Call({"operation_material": {
                "VERIFY_TEAM_ROSTER_MAPPINGS": {"evidence_fingerprint": "a" * 64, "expected_team_count": 2},
                "VERIFY_OPTION_WINDOW_CLOSED": {"evidence_fingerprint": "b" * 64,
                    "expected_eligible_option_count": 0, "expected_notice_timestamp": "2026-01-01T00:00:00+00:00",
                    "expected_deadline_timestamp": "2026-01-08T00:00:00+00:00"}}})
        return Call(self.data)
    def table(self, name): return Query(self.tables.get(name, []))
class Query:
    def __init__(self, rows): self.rows = rows
    def select(self, *args): return self
    def eq(self, *args): return self
    def execute(self): return Response(self.rows)
class EvidenceClient(Client):
    def __init__(self, rows): super().__init__({"authorized": True, "actor_user_id": "u"}); self.rows = rows
    def table(self, name): return Query(self.rows[name])


def artifacts():
    result = RolloverDryRunSimulator().simulate(source(), generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    validation = RolloverDryRunValidator().validate(result, validated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    plan_input = replace(to_execution_plan_input(result, validation), metadata={"operation_material": {
        "VERIFY_TEAM_ROSTER_MAPPINGS": {"evidence_fingerprint": "a" * 64, "expected_team_count": 2},
        "VERIFY_OPTION_WINDOW_CLOSED": {"evidence_fingerprint": "b" * 64,
            "expected_eligible_option_count": 0, "expected_notice_timestamp": "2026-01-01T00:00:00+00:00",
            "expected_deadline_timestamp": "2026-01-08T00:00:00+00:00"},
    }})
    return plan_input, result, validation


def registry():
    rows = json.loads((Path(__file__).parents[1] / "config/rollover_operation_catalog.yaml").read_text())["operations"]
    rows = sorted((x for x in rows if x["owner"] == "execution"), key=lambda x: x["order"])[:31]
    return [{"operation_code": row["code"], "operation_order": index, "handler_version": 1,
             "input_schema_version": f"input-{index}", "result_schema_version": f"result-{index}",
             "enabled": True, "execution_owner": "execution"} for index, row in enumerate(rows, 1)]


class PlannerTests(unittest.TestCase):
    def test_deterministic_plan_and_operation_ids(self):
        value, result, validation = artifacts(); planner = RolloverExecutionPlanner()
        first, _ = planner.build_plan(value, result, validation, generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        second, _ = planner.build_plan(value, result, validation, generated_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(first.plan_fingerprint, second.plan_fingerprint)
        self.assertEqual([x.operation_id for x in first.ordered_operations], [x.operation_id for x in second.ordered_operations])
    def test_order_and_dependencies(self):
        value, result, validation = artifacts(); plan, checked = RolloverExecutionPlanner().build_plan(value, result, validation)
        ranks = [x.operation_index for x in plan.ordered_operations]
        self.assertEqual(ranks, list(range(1, len(ranks)+1))); self.assertTrue(checked.checks["dependencies_precede_dependents"])
        self.assertEqual(len(plan.ordered_operations), 31)
        self.assertEqual(plan.ordered_operations[-1].operation_type, "FINALIZE_EXECUTED_UNPUBLISHED")
        self.assertNotIn("verify_execution_boundary", [x.operation_type for x in plan.ordered_operations])
    def test_registry_mismatch_fails_closed(self):
        value, result, validation = artifacts(); rows = registry()
        with self.assertRaisesRegex(ValueError, "registry differs"):
            RolloverExecutionPlanner(registry_snapshot=rows[:-1]).build_plan(value, result, validation)
        with self.assertRaisesRegex(ValueError, "registry differs"):
            RolloverExecutionPlanner(registry_snapshot=rows + [{**rows[-1], "operation_code": "EXTRA", "operation_order": 32}]).build_plan(value, result, validation)
    def test_serialization_excludes_generated_time_and_actor(self):
        value, result, validation = artifacts(); plan, _ = RolloverExecutionPlanner().build_plan(value, result, validation, generated_by="actor")
        payload = TrustedExecutionPlanService.serialize_plan(plan)
        self.assertNotIn("generated_at", payload); self.assertNotIn("generated_by", payload)
    def test_stale_and_blocked_simulations_rejected(self):
        value, result, validation = artifacts()
        with self.assertRaisesRegex(ValueError, "fingerprint"): RolloverExecutionPlanner().build_plan(replace(value, simulation_result_fingerprint="bad"), result, validation)
        with self.assertRaisesRegex(ValueError, "not plan eligible"): RolloverExecutionPlanner().build_plan(value, replace(result, status="blocked", executable=False, blockers=("x",)), validation)
    def test_duplicate_conflict_key_and_cycle_rejected(self):
        value, result, validation = artifacts(); plan, _ = RolloverExecutionPlanner().build_plan(value, result, validation)
        first = plan.ordered_operations[0]
        duplicate = replace(first, operation_id="other", operation_index=2, operation_fingerprint="f2")
        checked = RolloverExecutionPlanValidator().validate((first, duplicate), expected_league_id=value.league_id, expected_target_season=value.target_season)
        self.assertFalse(checked.checks["unique_conflict_keys"])
        cycle = replace(first, dependency_ids=("later",)); later = replace(duplicate, operation_id="later", dependency_ids=(first.operation_id,))
        checked = RolloverExecutionPlanValidator().validate((cycle, later), expected_league_id=value.league_id, expected_target_season=value.target_season)
        self.assertFalse(checked.checks["dependencies_precede_dependents"])
    def test_plan_has_no_domain_writes(self):
        value, result, validation = artifacts(); plan, _ = RolloverExecutionPlanner().build_plan(value, result, validation)
        self.assertFalse(plan.approved_for_execution); self.assertEqual(result.metadata["writes_performed"], 0)


class ServiceTests(unittest.TestCase):
    def test_caller_plan_conclusions_rejected(self):
        value, result, validation = artifacts(); service = TrustedExecutionPlanService(Client({"authorized": True, "actor_user_id": "u"}), Client({}))
        for field in service.FORBIDDEN:
            with self.assertRaisesRegex(ValueError, "forbidden"): service.generate(value, result, validation, {field: True})
    def test_canonical_plan_sent_to_service_boundary(self):
        value, result, validation = artifacts(); user = Client({"authorized": True, "actor_user_id": "u"}, {"rollover_execution_handler_registry": registry()}); persistence = Client({"plan": {"id": "p"}})
        row = TrustedExecutionPlanService(user, persistence).generate(value, result, validation, {"idempotency_key": "k"})
        self.assertEqual(row["id"], "p"); self.assertIn("ordered_operations", persistence.calls[0][1]["p_request"]["plan"])
    def test_malformed_persistence_rejected(self):
        value, result, validation = artifacts(); service = TrustedExecutionPlanService(Client({"authorized": True, "actor_user_id": "u"}, {"rollover_execution_handler_registry": registry()}), Client({}))
        with self.assertRaisesRegex(ValueError, "malformed"): service.generate(value, result, validation, {})
    def test_authoritative_simulation_reconstruction(self):
        value, result, validation = artifacts()
        rows = {"rollover_executions":[{"id":value.rollover_execution_id,"league_id":value.league_id}],
                "rollover_dry_run_simulations":[{"id":value.simulation_id,"rollover_execution_id":value.rollover_execution_id,
                 "league_id":value.league_id,"simulation_status":"valid","valid":True,"executable":True,"plan_eligible":True,"blockers":[]}]}
        service = TrustedExecutionPlanService(EvidenceClient(rows), Client({}))
        decoded = service.reconstruct_simulation(value.rollover_execution_id, value.simulation_id, lambda execution, simulation:(value,result,validation))
        self.assertEqual(decoded[0].simulation_result_fingerprint, result.result_fingerprint)


class ReadinessTests(unittest.TestCase):
    def test_readiness_mapping(self):
        self.assertEqual(execution_plan_readiness(None, None, None)["status"], "execution_control_ready")
        self.assertEqual(execution_plan_readiness({"id":"x"}, None, None)["status"], "dry_run_required")
        self.assertEqual(execution_plan_readiness({"id":"x"}, {"id":"s"}, None)["status"], "execution_plan_required")
        self.assertEqual(execution_plan_readiness({"id":"x"}, {"id":"s"}, {"plan_status":"valid","executable":True})["status"], "execution_plan_ready")

if __name__ == "__main__": unittest.main()
