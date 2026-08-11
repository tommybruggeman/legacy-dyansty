from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from season_engine.authority_preparation import material_fingerprint

APPROVAL_SCHEMA_VERSION = "rollover-execution-approval-v1"
APPROVAL_STATEMENT_CODE = "ROLLOVER_EXECUTION_PLAN_APPROVED"
APPROVAL_STATEMENT_VERSION = 1


@dataclass(frozen=True)
class RolloverExecutionApprovalInput:
    rollover_execution_id: str
    league_id: str
    source_season: int
    target_season: int
    execution_plan_id: str
    execution_plan_version: int
    simulation_id: str
    simulation_version: int
    expected_execution_status: str
    expected_plan_status: str
    expected_plan_input_fingerprint: str
    expected_plan_fingerprint: str
    expected_simulation_input_fingerprint: str
    expected_simulation_result_fingerprint: str
    expected_preflight_fingerprint: str
    expected_policy_fingerprint: str
    expected_owner_population_fingerprint: str
    expected_commissioner_population_fingerprint: str
    expected_authority_preparation_fingerprint: str
    expected_operation_count: int
    operation_fingerprints: tuple[str, ...]
    approval_statement_code: str
    approval_statement_version: int
    approval_statement: str
    idempotency_key: str
    material_metadata: Mapping[str, Any]
    approval_version: int = 1


@dataclass(frozen=True)
class RolloverExecutionApproval:
    id: str
    rollover_execution_id: str
    league_id: str
    source_season: int
    target_season: int
    execution_plan_id: str
    execution_plan_version: int
    simulation_id: str
    simulation_version: int
    approval_version: int
    approval_status: str
    execution_status_at_approval: str
    plan_status_at_approval: str
    plan_input_fingerprint: str
    plan_fingerprint: str
    simulation_input_fingerprint: str
    simulation_result_fingerprint: str
    preflight_fingerprint: str
    policy_fingerprint: str
    owner_population_fingerprint: str
    commissioner_population_fingerprint: str
    authority_preparation_fingerprint: str
    operation_count: int
    operation_fingerprints: tuple[str, ...]
    approval_fingerprint: str
    approval_statement_code: str
    approval_statement_version: int
    approval_statement: str
    approved_by: str
    approved_at: datetime
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class DurableCutoverLock:
    id: str
    rollover_execution_id: str
    league_id: str
    source_season: int
    target_season: int
    execution_plan_id: str
    execution_plan_version: int
    approval_id: str
    lock_type: str
    lock_status: str
    plan_fingerprint: str
    simulation_result_fingerprint: str
    policy_fingerprint: str
    authority_preparation_fingerprint: str
    acquired_by: str


def build_approval_input(plan: Mapping[str, Any], *, approval_statement: str, idempotency_key: str,
                         execution_status: str = "authority_ready",
                         material_metadata: Mapping[str, Any] | None = None) -> RolloverExecutionApprovalInput:
    operations = tuple(plan.get("ordered_operations") or ())
    return RolloverExecutionApprovalInput(
        str(plan["rollover_execution_id"]), str(plan["league_id"]), int(plan["source_season"]),
        int(plan["target_season"]), str(plan["id"]), int(plan["plan_version"]),
        str(plan["simulation_id"]), int(plan["simulation_version"]), execution_status,
        str(plan["plan_status"]), str(plan["plan_input_fingerprint"]), str(plan["plan_fingerprint"]),
        str(plan["simulation_input_fingerprint"]), str(plan["simulation_result_fingerprint"]),
        str(plan["preflight_fingerprint"]), str(plan["policy_fingerprint"]),
        str(plan["owner_population_fingerprint"]), str(plan["commissioner_population_fingerprint"]),
        str(plan["authority_preparation_fingerprint"]), int(plan["operation_count"]),
        tuple(str(x["operation_fingerprint"]) for x in operations), APPROVAL_STATEMENT_CODE,
        APPROVAL_STATEMENT_VERSION, approval_statement, idempotency_key,
        MappingProxyType(dict(material_metadata or {})),
    )


class RolloverExecutionApprovalService:
    FORBIDDEN = {"approval_status", "approval_fingerprint", "approved_by", "approved_at",
                 "lock_status", "lock_type", "execution_status_at_approval"}

    def __init__(self, client=None):
        self.client = client

    @staticmethod
    def derive_approval_fingerprint(value: RolloverExecutionApprovalInput) -> str:
        material = {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "execution_id": value.rollover_execution_id, "league_id": value.league_id,
            "source_season": value.source_season, "target_season": value.target_season,
            "plan_id": value.execution_plan_id, "plan_version": value.execution_plan_version,
            "plan_input_fingerprint": value.expected_plan_input_fingerprint,
            "plan_fingerprint": value.expected_plan_fingerprint,
            "simulation_id": value.simulation_id, "simulation_version": value.simulation_version,
            "simulation_input_fingerprint": value.expected_simulation_input_fingerprint,
            "simulation_result_fingerprint": value.expected_simulation_result_fingerprint,
            "preflight_fingerprint": value.expected_preflight_fingerprint,
            "policy_fingerprint": value.expected_policy_fingerprint,
            "owner_population_fingerprint": value.expected_owner_population_fingerprint,
            "commissioner_population_fingerprint": value.expected_commissioner_population_fingerprint,
            "authority_preparation_fingerprint": value.expected_authority_preparation_fingerprint,
            "operation_count": value.expected_operation_count,
            "operation_fingerprints": value.operation_fingerprints,
            "statement_code": value.approval_statement_code,
            "statement_version": value.approval_statement_version,
            "statement": value.approval_statement.strip(), "approval_version": value.approval_version,
            "metadata": dict(value.material_metadata),
        }
        return material_fingerprint(material)

    @staticmethod
    def validate_approval(value: RolloverExecutionApprovalInput, plan: Mapping[str, Any],
                          simulation: Mapping[str, Any]) -> None:
        if not value.idempotency_key.strip(): raise ValueError("idempotency key required")
        if value.approval_statement_code != APPROVAL_STATEMENT_CODE or value.approval_statement_version != APPROVAL_STATEMENT_VERSION:
            raise ValueError("invalid approval statement code or version")
        if not value.approval_statement.strip(): raise ValueError("nonblank approval statement required")
        if value.target_season != value.source_season + 1: raise ValueError("non-sequential season boundary")
        expected = {
            "id": value.execution_plan_id, "rollover_execution_id": value.rollover_execution_id,
            "league_id": value.league_id, "source_season": value.source_season,
            "target_season": value.target_season, "plan_version": value.execution_plan_version,
            "plan_status": value.expected_plan_status, "plan_input_fingerprint": value.expected_plan_input_fingerprint,
            "plan_fingerprint": value.expected_plan_fingerprint,
            "simulation_input_fingerprint": value.expected_simulation_input_fingerprint,
            "simulation_result_fingerprint": value.expected_simulation_result_fingerprint,
            "preflight_fingerprint": value.expected_preflight_fingerprint,
            "policy_fingerprint": value.expected_policy_fingerprint,
            "owner_population_fingerprint": value.expected_owner_population_fingerprint,
            "commissioner_population_fingerprint": value.expected_commissioner_population_fingerprint,
            "authority_preparation_fingerprint": value.expected_authority_preparation_fingerprint,
            "operation_count": value.expected_operation_count,
        }
        mismatches = sorted(k for k, v in expected.items() if plan.get(k) != v)
        if mismatches: raise ValueError("stale plan evidence: " + ",".join(mismatches))
        if plan.get("plan_status") != "valid" or not plan.get("executable") or plan.get("approved_for_execution") or plan.get("blockers"):
            raise ValueError("plan is not approval eligible")
        actual_ops = tuple(str(x.get("operation_fingerprint")) for x in plan.get("ordered_operations") or ())
        if actual_ops != value.operation_fingerprints or len(actual_ops) != value.expected_operation_count:
            raise ValueError("operation fingerprints changed")
        sim_expected = {"id": value.simulation_id, "rollover_execution_id": value.rollover_execution_id,
                        "simulation_version": value.simulation_version,
                        "input_fingerprint": value.expected_simulation_input_fingerprint,
                        "result_fingerprint": value.expected_simulation_result_fingerprint,
                        "preflight_fingerprint": value.expected_preflight_fingerprint,
                        "policy_fingerprint": value.expected_policy_fingerprint,
                        "owner_population_fingerprint": value.expected_owner_population_fingerprint,
                        "commissioner_population_fingerprint": value.expected_commissioner_population_fingerprint,
                        "authority_preparation_fingerprint": value.expected_authority_preparation_fingerprint}
        sim_mismatch = sorted(k for k, v in sim_expected.items() if simulation.get(k) != v)
        if sim_mismatch: raise ValueError("stale simulation evidence: " + ",".join(sim_mismatch))
        if simulation.get("simulation_status") != "valid" or not simulation.get("valid") or not simulation.get("executable") or not simulation.get("plan_eligible") or simulation.get("blockers"):
            raise ValueError("simulation is not approval eligible")

    def approve(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        supplied = self.FORBIDDEN.intersection(request)
        if supplied: raise ValueError("caller-authoritative approval fields forbidden: " + ",".join(sorted(supplied)))
        if self.client is None: raise RuntimeError("approval client required")
        result = self.client.rpc("approve_rollover_execution_plan_authenticated", {"p_request": dict(request)}).execute().data
        if not isinstance(result, Mapping) or not isinstance(result.get("approval"), Mapping) or not isinstance(result.get("lock"), Mapping):
            raise ValueError("malformed approval result")
        return MappingProxyType(dict(result))

    def revoke(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.client is None: raise RuntimeError("approval client required")
        result = self.client.rpc("revoke_rollover_execution_plan_approval_authenticated", {"p_request": dict(request)}).execute().data
        if not isinstance(result, Mapping) or not isinstance(result.get("approval"), Mapping):
            raise ValueError("malformed revocation result")
        return MappingProxyType(dict(result))

    @staticmethod
    def build_approval(value: RolloverExecutionApprovalInput, actor: str,
                       approved_at: datetime | None = None) -> RolloverExecutionApproval:
        fp = RolloverExecutionApprovalService.derive_approval_fingerprint(value)
        approval_id = str(uuid5(NAMESPACE_URL, f"legacy-rollover-approval:{value.rollover_execution_id}:{value.approval_version}:{fp}"))
        return RolloverExecutionApproval(
            approval_id, value.rollover_execution_id, value.league_id, value.source_season, value.target_season,
            value.execution_plan_id, value.execution_plan_version, value.simulation_id, value.simulation_version,
            value.approval_version, "approved", value.expected_execution_status, value.expected_plan_status,
            value.expected_plan_input_fingerprint, value.expected_plan_fingerprint,
            value.expected_simulation_input_fingerprint, value.expected_simulation_result_fingerprint,
            value.expected_preflight_fingerprint, value.expected_policy_fingerprint,
            value.expected_owner_population_fingerprint, value.expected_commissioner_population_fingerprint,
            value.expected_authority_preparation_fingerprint, value.expected_operation_count,
            value.operation_fingerprints, fp, value.approval_statement_code, value.approval_statement_version,
            value.approval_statement.strip(), actor, approved_at or datetime.now(timezone.utc),
            MappingProxyType(dict(value.material_metadata)),
        )

    @staticmethod
    def build_lock(approval: RolloverExecutionApproval) -> DurableCutoverLock:
        lock_id = str(uuid5(NAMESPACE_URL, f"legacy-cutover-lock:{approval.id}:{approval.approval_fingerprint}"))
        return DurableCutoverLock(lock_id, approval.rollover_execution_id, approval.league_id,
                                  approval.source_season, approval.target_season, approval.execution_plan_id,
                                  approval.execution_plan_version, approval.id, "cutover", "active",
                                  approval.plan_fingerprint, approval.simulation_result_fingerprint,
                                  approval.policy_fingerprint, approval.authority_preparation_fingerprint,
                                  approval.approved_by)

    @staticmethod
    def serialize(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return {k: RolloverExecutionApprovalService.serialize(getattr(value, k)) for k in value.__dataclass_fields__}
        if isinstance(value, Mapping): return {str(k): RolloverExecutionApprovalService.serialize(v) for k, v in sorted(value.items())}
        if isinstance(value, (tuple, list)): return [RolloverExecutionApprovalService.serialize(v) for v in value]
        if isinstance(value, datetime): return value.astimezone(timezone.utc).isoformat()
        return value


def execution_approval_readiness(execution: Mapping[str, Any] | None, plan: Mapping[str, Any] | None,
                                 approval: Mapping[str, Any] | None, lock: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not execution: return {"status": "execution_control_ready", "blockers": ("rollover execution not created",)}
    if not plan: return {"status": "execution_plan_required", "blockers": ("current execution plan required",)}
    if approval and approval.get("approval_status") == "revoked": return {"status": "execution_plan_approval_revoked", "blockers": ()}
    if not approval: return {"status": "execution_plan_ready", "blockers": ()}
    matching = (approval.get("approval_status") == "approved" and plan.get("plan_status") == "approved_for_execution"
                and plan.get("approved_for_execution") is True and lock and lock.get("lock_status", lock.get("status")) == "active"
                and lock.get("approval_id") == approval.get("id") and lock.get("execution_plan_id") == plan.get("id")
                and lock.get("plan_fingerprint") == plan.get("plan_fingerprint")
                and approval.get("simulation_result_fingerprint") == plan.get("simulation_result_fingerprint"))
    if matching: return {"status": "cutover_locked", "approval_status": "execution_plan_approved", "blockers": ()}
    return {"status": "execution_plan_approval_stale", "blockers": ("approval, plan, or durable lock evidence mismatch",)}
