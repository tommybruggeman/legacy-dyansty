from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from season_engine.authority_preparation import material_fingerprint
from season_engine.dry_run_simulator import (
    DryRunExecutionPlanInput,
    PersistedDryRunSimulation,
    RolloverDryRunResult,
    RolloverDryRunValidationResult,
)

PLANNER_VERSION = "rollover-execution-planner-v1"
PLAN_VALIDATOR_VERSION = "rollover-execution-plan-validator-v1"
CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "rollover_operation_catalog.yaml"
EXECUTION_FINAL_OPERATION = "FINALIZE_EXECUTED_UNPUBLISHED"

DOMAIN_ORDER = {
    "boundary": 10, "history": 20, "contract": 30, "roster": 70,
    "taxi": 80, "ir": 90, "publication": 100, "dead_cap": 110,
    "cap": 130, "draft": 160, "rookie_class": 170, "season": 180,
    "validation": 190, "refresh": 200,
}


@dataclass(frozen=True)
class ExecutionPlanOperation:
    operation_id: str
    operation_index: int
    operation_type: str
    domain: str
    entity_type: str
    entity_id: str
    league_id: str
    source_season: int
    target_season: int
    preconditions: Mapping[str, Any]
    expected_before_state: Mapping[str, Any]
    intended_after_state: Mapping[str, Any]
    dependency_ids: tuple[str, ...]
    conflict_key: str
    idempotency_key: str
    authority_source: str
    evidence_fingerprint: str
    operation_fingerprint: str
    reversible: bool
    compensation_metadata: Mapping[str, Any]
    blocking: bool
    metadata: Mapping[str, Any]
    handler_version: int = 1
    input_schema_version: str = "catalog-bound-input-v1"
    result_schema_version: str = "catalog-bound-result-v1"
    expected_team_count: int | None = None
    expected_eligible_option_count: int | None = None
    expected_notice_timestamp: str | None = None
    expected_deadline_timestamp: str | None = None


@dataclass(frozen=True)
class ExecutionPlanValidation:
    valid: bool
    executable: bool
    checks: Mapping[str, bool]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    validation_fingerprint: str
    validator_version: str = PLAN_VALIDATOR_VERSION


@dataclass(frozen=True)
class RolloverExecutionPlan:
    id: str
    rollover_execution_id: str
    league_id: str
    source_season: int
    target_season: int
    plan_version: int
    planner_version: str
    plan_status: str
    simulation_id: str
    simulation_version: int
    simulator_version: str
    validator_version: str
    simulation_input_fingerprint: str
    simulation_result_fingerprint: str
    preflight_fingerprint: str
    policy_fingerprint: str
    owner_population_fingerprint: str
    commissioner_population_fingerprint: str
    authority_preparation_fingerprint: str
    plan_input_fingerprint: str
    plan_fingerprint: str
    operation_count: int
    operation_summary: Mapping[str, int]
    ordered_operations: tuple[ExecutionPlanOperation, ...]
    validation_payload: Mapping[str, Any]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    executable: bool
    approved_for_execution: bool
    generated_by: str | None
    generated_at: datetime
    metadata: Mapping[str, Any]


class RolloverExecutionPlanValidator:
    def validate(self, operations: Sequence[ExecutionPlanOperation], *, expected_league_id: str,
                 expected_target_season: int) -> ExecutionPlanValidation:
        ids = [x.operation_id for x in operations]
        keys = [x.conflict_key for x in operations]
        fps = [x.operation_fingerprint for x in operations]
        index = {operation_id: i for i, operation_id in enumerate(ids)}
        missing = sorted({dep for op in operations for dep in op.dependency_ids if dep not in index})
        forward = sorted({dep for i, op in enumerate(operations) for dep in op.dependency_ids if index.get(dep, -1) >= i})
        checks = {
            "operation_count": all(x.operation_index == i for i, x in enumerate(operations, 1)),
            "unique_operation_ids": len(ids) == len(set(ids)),
            "unique_conflict_keys": len(keys) == len(set(keys)),
            "unique_operation_fingerprints": len(fps) == len(set(fps)),
            "dependencies_exist": not missing,
            "dependencies_precede_dependents": not forward,
            "supported_domains": all(bool(x.domain) for x in operations),
            "entity_identity": all(bool(x.entity_id) for x in operations),
            "authoritative_evidence": all(bool(x.evidence_fingerprint) for x in operations),
            "preconditions": all(bool(x.preconditions) for x in operations),
            "league_boundary": all(x.league_id == expected_league_id for x in operations),
            "season_boundary": all(x.target_season == expected_target_season for x in operations),
        }
        blockers = tuple(sorted(key for key, value in checks.items() if not value))
        fingerprint = material_fingerprint({"checks": checks, "blockers": blockers, "validator": PLAN_VALIDATOR_VERSION})
        return ExecutionPlanValidation(not blockers, not blockers, MappingProxyType(checks), blockers, (), fingerprint)


class RolloverExecutionPlanner:
    def __init__(self, catalog_path: Path | str = CATALOG_PATH,
                 registry_snapshot: Sequence[Mapping[str, Any]] | None = None):
        self.catalog_path = Path(catalog_path)
        self.registry_snapshot = registry_snapshot

    def _catalog(self) -> tuple[str, tuple[Mapping[str, Any], ...]]:
        try:
            document = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("canonical operation catalog is unreadable") from exc
        schema = str(document.get("schema_version") or "")
        all_rows = document.get("operations")
        if not schema or not isinstance(all_rows, list):
            raise ValueError("canonical operation catalog is malformed")
        rows = [dict(row) for row in all_rows if row.get("owner") == "execution"]
        rows.sort(key=lambda row: int(row.get("order") or 0))
        terminal = [i for i, row in enumerate(rows) if row.get("code") == EXECUTION_FINAL_OPERATION]
        if terminal != [30]:
            raise ValueError("catalog operations 1-31 are incomplete")
        rows = rows[:31]
        codes = [str(row.get("code") or "") for row in rows]
        orders = [int(row.get("order") or 0) for row in rows]
        if len(codes) != 31 or len(set(codes)) != 31 or len(set(orders)) != 31 or not all(codes):
            raise ValueError("catalog operations 1-31 are incomplete or duplicated")
        positions = {code: index for index, code in enumerate(codes)}
        for index, row in enumerate(rows):
            dependencies = row.get("dependencies")
            if not isinstance(dependencies, list) or any(dep not in positions or positions[dep] >= index for dep in dependencies):
                raise ValueError("canonical operation catalog is cyclic or has invalid dependencies")
            for field in ("phase", "domain", "blocking_validations", "affected_data_objects", "postconditions"):
                if not row.get(field):
                    raise ValueError(f"canonical operation lacks required material: {codes[index]}:{field}")
        return schema, tuple(rows)

    def _verified_definitions(self) -> tuple[str, tuple[Mapping[str, Any], ...]]:
        schema, rows = self._catalog()
        if self.registry_snapshot is None:
            return schema, rows
        registry = sorted((dict(row) for row in self.registry_snapshot if row.get("enabled", True)),
                          key=lambda row: int(row.get("operation_order") or 0))
        expected = [(i, row["code"]) for i, row in enumerate(rows, 1)]
        actual = [(int(row.get("operation_order") or 0), str(row.get("operation_code") or "")) for row in registry]
        if actual != expected:
            raise ValueError("handler registry differs from canonical operation catalog")
        merged = []
        for catalog, handler in zip(rows, registry):
            item = dict(catalog)
            for key in ("handler_version", "input_schema_version", "result_schema_version"):
                if not handler.get(key):
                    raise ValueError("handler registry lacks canonical material")
                item[key] = handler[key]
            merged.append(item)
        return schema, tuple(merged)

    def build_plan(self, plan_input: DryRunExecutionPlanInput, simulation: RolloverDryRunResult,
                   validation: RolloverDryRunValidationResult, *, generated_by: str | None = None,
                   generated_at: datetime | None = None) -> tuple[RolloverExecutionPlan, ExecutionPlanValidation]:
        self._validate_simulation(plan_input, simulation, validation)
        operations = self.derive_operations(plan_input, simulation)
        plan_validation = RolloverExecutionPlanValidator().validate(
            operations, expected_league_id=plan_input.league_id, expected_target_season=plan_input.target_season
        )
        if not plan_validation.valid:
            raise ValueError("structurally invalid execution plan: " + ",".join(plan_validation.blockers))
        input_fp = self.derive_plan_input_fingerprint(plan_input)
        summary: dict[str, int] = {}
        for operation in operations:
            summary[operation.operation_type] = summary.get(operation.operation_type, 0) + 1
        blockers = tuple(sorted(set(plan_validation.blockers)))
        warnings = tuple(sorted(set((*simulation.warnings, *plan_validation.warnings))))
        executable = plan_validation.executable and not blockers
        status = "valid" if executable else "blocked"
        fingerprint_material = {
            "plan_input_fingerprint": input_fp,
            "ordered_operations": operations,
            "operation_summary": summary,
            "validation": plan_validation,
            "blockers": blockers,
            "warnings": warnings,
            "executable": executable,
            "plan_status": status,
            "planner_version": plan_input.planner_version,
        }
        plan_fp = material_fingerprint(fingerprint_material)
        plan_id = str(uuid5(NAMESPACE_URL, f"legacy-plan:{plan_input.rollover_execution_id}:{plan_input.expected_plan_version}:{plan_fp}"))
        plan = RolloverExecutionPlan(
            plan_id, plan_input.rollover_execution_id, plan_input.league_id, plan_input.source_season,
            plan_input.target_season, plan_input.expected_plan_version, plan_input.planner_version, status,
            plan_input.simulation_id, plan_input.simulation_version, plan_input.simulator_version,
            plan_input.validator_version, plan_input.simulation_input_fingerprint,
            plan_input.simulation_result_fingerprint, plan_input.preflight_fingerprint,
            plan_input.policy_fingerprint, plan_input.owner_population_fingerprint,
            plan_input.commissioner_population_fingerprint, plan_input.authority_preparation_fingerprint,
            input_fp, plan_fp, len(operations), MappingProxyType(summary), operations,
            MappingProxyType({"checks": dict(plan_validation.checks), "validation_fingerprint": plan_validation.validation_fingerprint,
                              "validator_version": plan_validation.validator_version}),
            blockers, warnings, executable, False, generated_by, generated_at or datetime.now(timezone.utc),
            MappingProxyType(dict(plan_input.metadata)),
        )
        return plan, plan_validation

    @staticmethod
    def derive_plan_input_fingerprint(value: DryRunExecutionPlanInput) -> str:
        material = {key: getattr(value, key) for key in value.__dataclass_fields__
                    if key not in {"requested_by", "idempotency_key"}}
        return material_fingerprint(material)

    def derive_operations(self, plan_input: DryRunExecutionPlanInput,
                          simulation: RolloverDryRunResult) -> tuple[ExecutionPlanOperation, ...]:
        catalog_schema, rows = self._verified_definitions()
        catalog_fingerprint = material_fingerprint({"schema_version": catalog_schema, "operations": rows})
        built: list[ExecutionPlanOperation] = []
        ids: dict[str, str] = {}
        for index, row in enumerate(rows, 1):
            code = str(row["code"])
            dependencies = tuple(ids[dep] for dep in row["dependencies"])
            binding = dict((plan_input.metadata or {}).get("operation_material", {}).get(code, {}))
            evidence = str(binding.get("evidence_fingerprint") or material_fingerprint({
                "catalog_fingerprint": catalog_fingerprint,
                "simulation_result_fingerprint": simulation.result_fingerprint,
                "preflight_fingerprint": plan_input.preflight_fingerprint,
                "policy_fingerprint": plan_input.policy_fingerprint,
                "owner_population_fingerprint": plan_input.owner_population_fingerprint,
                "commissioner_population_fingerprint": plan_input.commissioner_population_fingerprint,
                "authority_preparation_fingerprint": plan_input.authority_preparation_fingerprint,
            }))
            if code in {"VERIFY_TEAM_ROSTER_MAPPINGS", "VERIFY_OPTION_WINDOW_CLOSED"} and not binding:
                raise ValueError(f"canonical operation lacks required source binding: {code}")
            preconditions = {"catalog_schema_version": catalog_schema,
                             "catalog_order": int(row["order"]),
                             "required_material_fingerprint": evidence}
            material = {
                "index": index, "operation_type": code, "domain": row["domain"],
                "entity_id": plan_input.rollover_execution_id, "league": plan_input.league_id,
                "source_season": plan_input.source_season, "target_season": plan_input.target_season,
                "preconditions": preconditions, "dependencies": dependencies,
                "evidence": evidence, "handler_version": int(row.get("handler_version") or 1),
                "input_schema_version": row.get("input_schema_version") or "catalog-bound-input-v1",
                "result_schema_version": row.get("result_schema_version") or "catalog-bound-result-v1",
            }
            op_fp = material_fingerprint(material)
            operation_id = str(uuid5(NAMESPACE_URL, f"legacy-operation:{plan_input.rollover_execution_id}:{op_fp}"))
            ids[code] = operation_id
            operation = ExecutionPlanOperation(
                operation_id, index, code, str(row["domain"]), "rollover_execution",
                plan_input.rollover_execution_id, plan_input.league_id, plan_input.source_season, plan_input.target_season,
                MappingProxyType(preconditions), MappingProxyType({"status": "certified_input"}),
                MappingProxyType({"postconditions": tuple(row["postconditions"])}), dependencies,
                f"rollover:{plan_input.rollover_execution_id}:{index}",
                f"rollover-plan:{plan_input.rollover_execution_id}:{operation_id}", "canonical_operation_catalog",
                evidence, op_fp, True, MappingProxyType({}), False,
                MappingProxyType({"catalog_order": row["order"], "phase": row["phase"],
                                  "blocking_validations": tuple(row["blocking_validations"]),
                                  "affected_data_objects": tuple(row["affected_data_objects"]),
                                  "domain_analysis_fingerprint": simulation.result_fingerprint}),
                int(row.get("handler_version") or 1),
                str(row.get("input_schema_version") or "catalog-bound-input-v1"),
                str(row.get("result_schema_version") or "catalog-bound-result-v1"),
                binding.get("expected_team_count"), binding.get("expected_eligible_option_count"),
                binding.get("expected_notice_timestamp"), binding.get("expected_deadline_timestamp"),
            )
            built.append(operation)
        return tuple(built)

    @staticmethod
    def _raw(domain: str, operation_type: str, entity_type: str, entity_id: str,
             preconditions: Mapping[str, Any], before: Mapping[str, Any], after: Mapping[str, Any],
             dependencies: tuple[str, ...], evidence: str, blocking: bool) -> dict[str, Any]:
        return {"domain": domain, "operation_type": operation_type, "entity_type": entity_type,
                "entity_id": str(entity_id), "preconditions": dict(preconditions) or {"authoritative": True},
                "before": dict(before), "after": dict(after), "authority_source": ",".join(dependencies) or domain,
                "evidence_fingerprint": evidence, "blocking": blocking, "reversible": domain not in {"season", "history"}}

    @staticmethod
    def _validate_simulation(plan_input: DryRunExecutionPlanInput, result: RolloverDryRunResult,
                             validation: RolloverDryRunValidationResult) -> None:
        if result.id != plan_input.simulation_id or result.execution_id != plan_input.rollover_execution_id:
            raise ValueError("simulation identity mismatch")
        if result.input_fingerprint != plan_input.simulation_input_fingerprint or result.result_fingerprint != plan_input.simulation_result_fingerprint:
            raise ValueError("simulation fingerprint mismatch")
        if result.preflight_fingerprint != plan_input.preflight_fingerprint:
            raise ValueError("preflight fingerprint mismatch")
        if result.status != "valid" or not result.valid or not result.executable or result.blockers:
            raise ValueError("simulation is not plan eligible")
        if not validation.valid or not validation.executable or not validation.plan_eligible or validation.blockers:
            raise ValueError("simulation validation is not plan eligible")
        if plan_input.target_season != plan_input.source_season + 1:
            raise ValueError("non-sequential plan boundary")


class TrustedExecutionPlanService:
    FORBIDDEN = {"ordered_operations", "operation_summary", "blockers", "warnings", "executable",
                 "plan_status", "plan_fingerprint", "plan_input_fingerprint", "operation_count",
                 "validation_payload"}

    def __init__(self, user_client, service_client):
        self.user_client = user_client
        self.service_client = service_client

    def reconstruct_simulation(self, execution_id: str, simulation_id: str, decoder):
        executions = self.user_client.table("rollover_executions").select("*").eq("id", execution_id).execute().data or []
        simulations = self.user_client.table("rollover_dry_run_simulations").select("*").eq("id", simulation_id).execute().data or []
        if len(executions) != 1 or len(simulations) != 1:
            raise ValueError("exactly one execution and simulation required")
        execution, simulation = executions[0], simulations[0]
        if simulation.get("rollover_execution_id") != execution.get("id") or simulation.get("league_id") != execution.get("league_id"):
            raise ValueError("simulation execution boundary mismatch")
        if simulation.get("simulation_status") != "valid" or not simulation.get("valid") or not simulation.get("executable") or not simulation.get("plan_eligible") or simulation.get("blockers"):
            raise ValueError("stored simulation is not plan eligible")
        decoded = decoder(MappingProxyType(dict(execution)), MappingProxyType(dict(simulation)))
        if not isinstance(decoded, tuple) or len(decoded) != 3:
            raise TypeError("decoder must return plan input, simulation result, and validation")
        return decoded

    def generate_from_storage(self, execution_id: str, simulation_id: str, request: Mapping[str, Any], decoder):
        plan_input, simulation, validation = self.reconstruct_simulation(execution_id, simulation_id, decoder)
        return self.generate(plan_input, simulation, validation, request)

    def generate(self, plan_input: DryRunExecutionPlanInput, simulation: RolloverDryRunResult,
                 validation: RolloverDryRunValidationResult, request: Mapping[str, Any]) -> Mapping[str, Any]:
        supplied = self.FORBIDDEN.intersection(request)
        if supplied:
            raise ValueError("caller-authoritative plan fields forbidden: " + ",".join(sorted(supplied)))
        auth = self.user_client.rpc("assert_rollover_plan_commissioner_authenticated",
                                    {"p_execution_id": plan_input.rollover_execution_id}).execute().data
        if not isinstance(auth, Mapping) or not auth.get("authorized"):
            raise PermissionError("commissioner authority required")
        material = self.user_client.rpc("get_rollover_execution_plan_material_authenticated",
                                        {"p_execution_id": plan_input.rollover_execution_id}).execute().data
        if not isinstance(material, Mapping) or not isinstance(material.get("operation_material"), Mapping):
            raise ValueError("canonical operation material is unavailable")
        plan_input = replace(plan_input, metadata=MappingProxyType({
            **dict(plan_input.metadata), "operation_material": dict(material["operation_material"])
        }))
        registry = (self.user_client.table("rollover_execution_handler_registry")
                    .select("operation_code,operation_order,handler_version,input_schema_version,result_schema_version,enabled")
                    .eq("execution_owner", "execution").execute().data or [])
        plan, plan_validation = RolloverExecutionPlanner(registry_snapshot=registry).build_plan(
            plan_input, simulation, validation, generated_by=str(auth.get("actor_user_id"))
        )
        payload = self.serialize_plan(plan)
        assertions = {
            "execution_id": plan_input.rollover_execution_id,
            "simulation_id": plan_input.simulation_id,
            "expected_simulation_version": plan_input.simulation_version,
            "expected_simulation_input_fingerprint": plan_input.simulation_input_fingerprint,
            "expected_simulation_result_fingerprint": plan_input.simulation_result_fingerprint,
            "expected_preflight_fingerprint": plan_input.preflight_fingerprint,
            "expected_policy_fingerprint": plan_input.policy_fingerprint,
            "expected_owner_population_fingerprint": plan_input.owner_population_fingerprint,
            "expected_commissioner_population_fingerprint": plan_input.commissioner_population_fingerprint,
            "expected_authority_preparation_fingerprint": plan_input.authority_preparation_fingerprint,
        }
        body = {**dict(request), **assertions, "trusted_actor_user_id": auth.get("actor_user_id"),
                "plan": dict(payload), "validation": self._clean(plan_validation)}
        result = self.service_client.rpc("persist_rollover_execution_plan_service", {"p_request": body}).execute().data
        if not isinstance(result, Mapping) or not isinstance(result.get("plan"), Mapping):
            raise ValueError("malformed execution plan persistence result")
        return MappingProxyType(dict(result["plan"]))

    @classmethod
    def serialize_plan(cls, plan: RolloverExecutionPlan) -> Mapping[str, Any]:
        return MappingProxyType(cls._clean(plan))

    @staticmethod
    def _clean(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return {key: TrustedExecutionPlanService._clean(getattr(value, key)) for key in value.__dataclass_fields__
                    if key not in {"generated_at", "generated_by"}}
        if isinstance(value, Mapping):
            return {str(key): TrustedExecutionPlanService._clean(item) for key, item in sorted(value.items())}
        if isinstance(value, (tuple, list)):
            return [TrustedExecutionPlanService._clean(item) for item in value]
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return value


def execution_plan_readiness(execution: Mapping[str, Any] | None, simulation: Mapping[str, Any] | None,
                             plan: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not execution:
        return {"status": "execution_control_ready", "blockers": ("rollover execution not created",)}
    if not simulation:
        return {"status": "dry_run_required", "blockers": ("canonical dry run required",)}
    if not plan:
        return {"status": "execution_plan_required", "blockers": ()}
    if plan.get("plan_status") == "blocked" or not plan.get("executable"):
        return {"status": "execution_plan_blocked", "blockers": tuple(plan.get("blockers") or ())}
    if plan.get("plan_status") == "approved_for_execution":
        return {"status": "execution_plan_approved", "blockers": ()}
    return {"status": "execution_plan_ready", "blockers": ()}
