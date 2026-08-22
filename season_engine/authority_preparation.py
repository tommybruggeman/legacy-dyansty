from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from season_engine.rollover_service import stable_fingerprint

VALIDATOR_VERSION = "authority-preparation-v1"


class AuthorityType(str, Enum):
    PUBLICATION = "publication"
    DEAD_CAP = "dead_cap"
    SALARY_CAP = "salary_cap"


class AuthorityStatus(str, Enum):
    UNINITIALIZED = "uninitialized"
    PREPARATION_REQUIRED = "preparation_required"
    PREPARED = "prepared"
    BLOCKED = "blocked"
    APPROVED_FOR_EXECUTION = "approved_for_execution"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


def material_fingerprint(value: Any) -> str:
    def clean(item: Any) -> Any:
        if hasattr(item, "__dataclass_fields__"):
            return clean({field.name: getattr(item, field.name) for field in fields(item)})
        if isinstance(item, Mapping):
            return {str(k): clean(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))
                    if k not in {"generated_at", "prepared_at", "validated_at", "display_text"}}
        if isinstance(item, (tuple, list, set, frozenset)):
            return [clean(v) for v in item]
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        return item
    return stable_fingerprint(clean(value))


@dataclass(frozen=True)
class PublicationAuthorityInstruction:
    player_id: str
    agreement_id: str | None
    league_team_id: str | None
    source_status: str
    planned_contract_outcome: str
    publication_eligibility: str
    publication_action: str
    publication_blockers: tuple[str, ...]
    publication_warnings: tuple[str, ...]
    commissioner_review_id: str | None
    owner_decision_id: str | None
    evidence_fingerprint: str
    instruction_fingerprint: str


@dataclass(frozen=True)
class DeadCapAuthorityInstruction:
    player_id: str
    agreement_id: str | None
    league_team_id: str | None
    qualifying_event_id: str | None
    penalty_rule: str | None
    salary_basis: Decimal | None
    target_season: int
    calculated_amount: Decimal
    calculation_fingerprint: str
    planned_action: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence_fingerprint: str
    instruction_fingerprint: str


@dataclass(frozen=True)
class TeamCapProjection:
    league_team_id: str
    source_salary_total: Decimal
    retained_salary_total: Decimal
    recontract_salary_total: Decimal | None
    planned_release_relief: Decimal
    planned_dead_cap: Decimal
    cap_adjustments: Decimal
    cap_credits_in: Decimal
    cap_credits_out: Decimal
    projected_cap_charge: Decimal | None
    projected_cap_space: Decimal | None
    cap_legal: bool | None
    unresolved_owner_cases: int
    unresolved_commissioner_cases: int
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence_fingerprint: str


@dataclass(frozen=True)
class CapAuthorityPlan:
    league_id: str
    source_season: int
    target_season: int
    source_cap: Decimal
    target_cap: Decimal
    cap_rule: str
    base_cap: Decimal
    scaling_factor: Decimal
    rounding_rule: str
    retained_salary_total: Decimal
    proposed_recontract_salary_total: Decimal | None
    planned_dead_cap_total: Decimal
    cap_adjustment_total: Decimal
    cap_credit_total: Decimal
    projected_team_cap_states: tuple[TeamCapProjection, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence_fingerprint: str
    authority_fingerprint: str


@dataclass(frozen=True)
class AuthorityDomainPlan:
    authority_type: str
    authority_status: str
    instructions: tuple[Any, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    source_evidence_fingerprint: str
    authority_fingerprint: str


@dataclass(frozen=True)
class RolloverAuthorityPreparationPackage:
    league_id: str
    source_season: int
    target_season: int
    policy_id: str
    execution_id: str | None
    owner_population_fingerprint: str
    commissioner_population_fingerprint: str
    publication_authority_plan: AuthorityDomainPlan
    dead_cap_authority_plan: AuthorityDomainPlan
    cap_authority_plan: CapAuthorityPlan
    owner_outcome_summary: Mapping[str, Any]
    commissioner_outcome_summary: Mapping[str, Any]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    preparation_fingerprint: str
    generated_at: datetime
    provenance: tuple[str, ...]
    validator_version: str


@dataclass(frozen=True)
class AuthorityValidationResult:
    valid: bool
    prepared: bool
    execution_approvable: bool
    authority_type: str
    checks: Mapping[str, bool]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence_fingerprint: str
    authority_fingerprint: str
    validator_version: str
    validated_at: datetime


@dataclass(frozen=True)
class AuthoritySimulationInput:
    execution_id: str
    league_id: str
    source_season: int
    target_season: int
    policy_fingerprint: str
    owner_population_fingerprint: str
    commissioner_population_fingerprint: str
    authority_preparation_fingerprint: str
    publication_instructions: tuple[PublicationAuthorityInstruction, ...]
    dead_cap_instructions: tuple[DeadCapAuthorityInstruction, ...]
    cap_authority_plan: CapAuthorityPlan
    team_cap_projections: tuple[TeamCapProjection, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    policy_id: str = ""
    preflight_fingerprint: str = ""
    publication_authority_fingerprint: str = ""
    dead_cap_authority_fingerprint: str = ""
    salary_cap_authority_fingerprint: str = ""
    finalized_owner_outcomes: tuple[Mapping[str, Any], ...] = ()
    finalized_commissioner_outcomes: tuple[Mapping[str, Any], ...] = ()
    owner_expected_count: int | None = None


@dataclass(frozen=True)
class PersistedAuthorityPreparation:
    id: str
    rollover_execution_id: str
    league_id: str
    source_season: int
    target_season: int
    authority_type: str
    authority_status: str
    version: int
    policy_id: str
    policy_fingerprint: str
    owner_population_fingerprint: str
    commissioner_population_fingerprint: str
    evidence_fingerprint: str
    authority_fingerprint: str
    preparation_fingerprint: str
    preparation_payload: Mapping[str, Any]
    blockers: tuple[Any, ...]
    warnings: tuple[Any, ...]
    prepared_by: str
    prepared_at: str
    approved_by: str | None
    approved_at: str | None
    activated_at: str | None
    superseded_at: str | None
    superseded_by: str | None
    cancelled_at: str | None
    metadata: Mapping[str, Any]

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PersistedAuthorityPreparation":
        if "status" in row or "authority_status" not in row:
            raise ValueError("canonical authority_status required")
        required = ("id", "rollover_execution_id", "league_id", "source_season", "target_season",
                    "authority_type", "version", "policy_id", "policy_fingerprint",
                    "owner_population_fingerprint", "commissioner_population_fingerprint",
                    "evidence_fingerprint", "authority_fingerprint", "preparation_fingerprint",
                    "preparation_payload", "prepared_by", "prepared_at")
        missing = [key for key in required if row.get(key) is None]
        if missing: raise ValueError("malformed authority preparation result: " + ",".join(missing))
        return cls(id=str(row["id"]), rollover_execution_id=str(row["rollover_execution_id"]),
                   league_id=str(row["league_id"]), source_season=int(row["source_season"]),
                   target_season=int(row["target_season"]), authority_type=str(row["authority_type"]),
                   authority_status=str(row["authority_status"]), version=int(row["version"]),
                   policy_id=str(row["policy_id"]), policy_fingerprint=str(row["policy_fingerprint"]),
                   owner_population_fingerprint=str(row["owner_population_fingerprint"]),
                   commissioner_population_fingerprint=str(row["commissioner_population_fingerprint"]),
                   evidence_fingerprint=str(row["evidence_fingerprint"]),
                   authority_fingerprint=str(row["authority_fingerprint"]),
                   preparation_fingerprint=str(row["preparation_fingerprint"]),
                   preparation_payload=MappingProxyType(dict(row["preparation_payload"])),
                   blockers=tuple(row.get("blockers") or ()), warnings=tuple(row.get("warnings") or ()),
                   prepared_by=str(row["prepared_by"]), prepared_at=str(row["prepared_at"]),
                   approved_by=row.get("approved_by"), approved_at=row.get("approved_at"),
                   activated_at=row.get("activated_at"), superseded_at=row.get("superseded_at"),
                   superseded_by=row.get("superseded_by"), cancelled_at=row.get("cancelled_at"),
                   metadata=MappingProxyType(dict(row.get("metadata") or {})))


class PublicationAuthorityPlanner:
    def plan(self, records: Sequence[Mapping[str, Any]]) -> AuthorityDomainPlan:
        instructions = []
        for row in sorted(records, key=lambda x: (str(x.get("player_id")), str(x.get("agreement_id")))):
            blockers = []
            if row.get("active_agreement"): blockers.append("blocked_by_active_agreement")
            if row.get("original_team_liability"): blockers.append("blocked_by_original_team_liability")
            if row.get("second_agreement_conflict"): blockers.append("blocked_by_second_agreement_conflict")
            for key in ("identity", "waiver", "rookie_draft"):
                if row.get(f"{key}_conflict"): blockers.append(f"blocked_by_{key}_conflict")
            if not row.get("owner_outcome_final"): blockers.append("blocked_by_owner_decision")
            if not row.get("commissioner_outcome_final"): blockers.append("blocked_by_commissioner_review")
            approved = row.get("commissioner_outcome") == "approve_publication" and not blockers
            eligibility = "approved_for_future_publication" if approved else (
                blockers[0] if blockers else "naturally_expired_but_not_approved")
            action = "plan_publication_at_execution" if approved else (
                "do_not_publish" if row.get("planned_contract_outcome") == "retain" else "hold")
            evidence = material_fingerprint(dict(row))
            basis = {"player": row.get("player_id"), "agreement": row.get("agreement_id"),
                     "eligibility": eligibility, "action": action, "blockers": blockers, "evidence": evidence}
            instructions.append(PublicationAuthorityInstruction(
                str(row.get("player_id") or ""), row.get("agreement_id"), row.get("league_team_id"),
                str(row.get("source_status") or "unknown"), str(row.get("planned_contract_outcome") or "unresolved"),
                eligibility, action, tuple(blockers), tuple(row.get("warnings") or ()),
                row.get("commissioner_review_id"), row.get("owner_decision_id"), evidence,
                material_fingerprint(basis)))
        domain_blockers = tuple(sorted({b for item in instructions for b in item.publication_blockers}))
        evidence_fp = material_fingerprint([x.evidence_fingerprint for x in instructions])
        return AuthorityDomainPlan(AuthorityType.PUBLICATION.value,
            AuthorityStatus.BLOCKED.value if domain_blockers else AuthorityStatus.PREPARED.value,
            tuple(instructions), domain_blockers, (), evidence_fp,
            material_fingerprint({"type": "publication", "instructions": instructions}))


class DeadCapAuthorityPlanner:
    def plan(self, records: Sequence[Mapping[str, Any]], target_season: int) -> AuthorityDomainPlan:
        instructions = []
        for row in sorted(records, key=lambda x: (str(x.get("player_id")), str(x.get("agreement_id")))):
            blockers = []
            event = row.get("qualifying_event_id")
            natural = row.get("termination_type") == "natural_expiration"
            requested = bool(row.get("dead_cap_requested")) and not natural
            rule = row.get("penalty_rule")
            basis = Decimal(str(row["salary_basis"])) if row.get("salary_basis") is not None else None
            if requested and not event: blockers.append("qualifying_event_required")
            if requested and not rule: blockers.append("penalty_rule_required")
            if requested and basis is None: blockers.append("salary_basis_required")
            amount = Decimal("0")
            if requested and not blockers:
                amount = (basis * Decimal(str(row.get("penalty_rate") or 0))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            action = "approved_for_future_creation" if amount and not blockers else (
                "blocked_dead_cap" if blockers else "no_dead_cap")
            evidence = material_fingerprint(dict(row))
            calculation = material_fingerprint({"event": event, "rule": rule, "basis": basis,
                                                 "rate": row.get("penalty_rate"), "amount": amount,
                                                 "target": target_season})
            instructions.append(DeadCapAuthorityInstruction(
                str(row.get("player_id") or ""), row.get("agreement_id"), row.get("league_team_id"), event,
                rule, basis, target_season, amount, calculation, action, tuple(blockers),
                tuple(row.get("warnings") or ()), evidence,
                material_fingerprint({"evidence": evidence, "calculation": calculation, "action": action,
                                      "blockers": blockers})))
        domain_blockers = tuple(sorted({b for item in instructions for b in item.blockers}))
        evidence_fp = material_fingerprint([x.evidence_fingerprint for x in instructions])
        return AuthorityDomainPlan(AuthorityType.DEAD_CAP.value,
            AuthorityStatus.BLOCKED.value if domain_blockers else AuthorityStatus.PREPARED.value,
            tuple(instructions), domain_blockers, (), evidence_fp,
            material_fingerprint({"type": "dead_cap", "instructions": instructions}))


class CapAuthorityPlanner:
    @staticmethod
    def scale_amount(base_amount: Decimal, base_cap: Decimal, target_cap: Decimal) -> Decimal:
        if base_amount == Decimal("1"): return Decimal("1")
        return (base_amount * target_cap / base_cap).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    def plan(self, *, league_id: str, source_season: int, target_season: int, source_cap: Any,
             target_cap: Any, teams: Sequence[Mapping[str, Any]], base_cap: Any | None = None) -> CapAuthorityPlan:
        source_cap, target_cap = Decimal(str(source_cap)), Decimal(str(target_cap))
        base_cap = Decimal(str(base_cap if base_cap is not None else source_cap))
        projections = []
        for row in sorted(teams, key=lambda x: str(x.get("league_team_id"))):
            source = Decimal(str(row.get("source_salary_total") or 0)); retained = Decimal(str(row.get("retained_salary_total") or 0))
            release = Decimal(str(row.get("planned_release_relief") or 0)); dead = Decimal(str(row.get("planned_dead_cap") or 0))
            adjustments = Decimal(str(row.get("cap_adjustments") or 0)); credits_in = Decimal(str(row.get("cap_credits_in") or 0)); credits_out = Decimal(str(row.get("cap_credits_out") or 0))
            unresolved_owner = int(row.get("unresolved_owner_cases") or 0); unresolved_comm = int(row.get("unresolved_commissioner_cases") or 0)
            recontract = None if row.get("recontract_salary_total") is None and (unresolved_owner or unresolved_comm) else Decimal(str(row.get("recontract_salary_total") or 0))
            blockers = ("unresolved_material_salary_outcomes",) if recontract is None else ()
            charge = None if recontract is None else retained + recontract + dead + adjustments + credits_out - credits_in
            space = None if charge is None else target_cap - charge
            evidence = material_fingerprint(dict(row))
            projections.append(TeamCapProjection(str(row.get("league_team_id") or ""), source, retained, recontract,
                release, dead, adjustments, credits_in, credits_out, charge, space,
                None if blockers else bool(space >= 0), unresolved_owner, unresolved_comm, blockers, (), evidence))
        blockers = tuple(sorted({b for p in projections for b in p.blockers}))
        retained_total = sum((p.retained_salary_total for p in projections), Decimal("0"))
        recontract_total = None if any(p.recontract_salary_total is None for p in projections) else sum((p.recontract_salary_total or Decimal("0") for p in projections), Decimal("0"))
        evidence = material_fingerprint({"source_cap": source_cap, "target_cap": target_cap, "base_cap": base_cap,
                                         "teams": projections})
        draft = dict(league_id=league_id, source_season=source_season, target_season=target_season,
                     source_cap=source_cap, target_cap=target_cap, cap_rule="hard_cap_target_season",
                     base_cap=base_cap, scaling_factor=target_cap/base_cap,
                     rounding_rule="nearest_dollar_half_up_no_compounding", retained_salary_total=retained_total,
                     proposed_recontract_salary_total=recontract_total,
                     planned_dead_cap_total=sum((p.planned_dead_cap for p in projections), Decimal("0")),
                     cap_adjustment_total=sum((p.cap_adjustments for p in projections), Decimal("0")),
                     cap_credit_total=sum((p.cap_credits_in-p.cap_credits_out for p in projections), Decimal("0")),
                     projected_team_cap_states=tuple(projections), blockers=blockers, warnings=(),
                     evidence_fingerprint=evidence)
        return CapAuthorityPlan(**draft, authority_fingerprint=material_fingerprint(draft))


class AuthorityPreparationValidator:
    def validate(self, authority_type: str, plan: Any, dependencies: Mapping[str, bool], *, validated_at: datetime | None = None) -> AuthorityValidationResult:
        checks = MappingProxyType(dict(sorted(dependencies.items())))
        blockers = tuple(k for k, value in checks.items() if not value) + tuple(getattr(plan, "blockers", ()))
        fp = getattr(plan, "source_evidence_fingerprint", getattr(plan, "evidence_fingerprint", ""))
        authority_fp = getattr(plan, "authority_fingerprint", "")
        prepared = not blockers
        return AuthorityValidationResult(prepared, prepared, prepared and bool(dependencies.get("execution_exists")),
            authority_type, checks, tuple(dict.fromkeys(blockers)), tuple(getattr(plan, "warnings", ())), fp,
            authority_fp, VALIDATOR_VERSION, validated_at or datetime.now(timezone.utc))


def authority_preparation_readiness(execution: Mapping[str, Any] | None,
                                    preparations: Sequence[Mapping[str, Any]] = (),
                                    blockers: Sequence[str] = ()) -> Mapping[str, Any]:
    if not execution: return {"status": "execution_control_ready", "blockers": ("rollover execution not created",)}
    if blockers: return {"status": "authority_preparation_blocked", "blockers": tuple(blockers)}
    current = [x for x in preparations if x.get("status") == "prepared" and not x.get("superseded_at")]
    if not current: return {"status": "authority_preparation_required", "blockers": ()}
    if len({x.get("authority_type") for x in current}) < 3:
        return {"status": "authority_preparation_in_progress", "blockers": ()}
    return {"status": "dry_run_required", "authority_status": "authority_prepared", "blockers": ()}


class AuthorityPreparationService:
    """Authenticated facade. These methods are never called by preview generation."""
    def __init__(self, client): self.client = client
    @staticmethod
    def request_fingerprint(operation_type: str, payload: Mapping[str, Any], actor_user_id: str) -> str:
        material = {k: v for k, v in payload.items() if k not in {"idempotency_key", "display_text", "requested_by", "actor_user_id"}}
        return material_fingerprint({"operation": operation_type, "actor": actor_user_id, "request": material})
    @staticmethod
    def _validate_result(data: Any) -> Mapping[str, Any]:
        if not isinstance(data, Mapping) or not isinstance(data.get("preparations"), list):
            raise ValueError("malformed authority preparation RPC result")
        rows = tuple(PersistedAuthorityPreparation.from_row(row) for row in data["preparations"])
        if data.get("operation") == "authority_preparation_prepare" and len(rows) != 3:
            raise ValueError("prepare result must contain exactly three authority domains")
        return MappingProxyType({**dict(data), "preparations": rows})
    def prepare(self, payload): return self._validate_result(self.client.rpc("prepare_rollover_authorities_authenticated", {"p_request": payload}).execute().data)
    def supersede(self, payload): return self._validate_result(self.client.rpc("supersede_rollover_authority_preparation_authenticated", {"p_request": payload}).execute().data)
    def cancel(self, payload): return self._validate_result(self.client.rpc("cancel_rollover_authority_preparation_authenticated", {"p_request": payload}).execute().data)


def build_preparation_package(*, league_id: str, source_season: int, target_season: int, policy_id: str,
                              execution_id: str | None, owner_population_fingerprint: str,
                              commissioner_population_fingerprint: str, publication_plan: AuthorityDomainPlan,
                              dead_cap_plan: AuthorityDomainPlan, cap_plan: CapAuthorityPlan,
                              owner_summary: Mapping[str, Any], commissioner_summary: Mapping[str, Any],
                              generated_at: datetime | None = None) -> RolloverAuthorityPreparationPackage:
    blockers = tuple(dict.fromkeys((*publication_plan.blockers, *dead_cap_plan.blockers, *cap_plan.blockers,
        *(("rollover execution not created",) if not execution_id else ()))))
    warnings = tuple(dict.fromkeys((*publication_plan.warnings, *dead_cap_plan.warnings, *cap_plan.warnings)))
    basis = {"league_id": league_id, "source_season": source_season, "target_season": target_season,
             "policy_id": policy_id, "execution_id": execution_id, "owner_population_fingerprint": owner_population_fingerprint,
             "commissioner_population_fingerprint": commissioner_population_fingerprint,
             "publication": publication_plan.authority_fingerprint, "dead_cap": dead_cap_plan.authority_fingerprint,
             "cap": cap_plan.authority_fingerprint, "owner_summary": owner_summary,
             "commissioner_summary": commissioner_summary, "blockers": blockers, "warnings": warnings,
             "validator_version": VALIDATOR_VERSION}
    return RolloverAuthorityPreparationPackage(league_id, source_season, target_season, policy_id, execution_id,
        owner_population_fingerprint, commissioner_population_fingerprint, publication_plan, dead_cap_plan, cap_plan,
        MappingProxyType(dict(owner_summary)), MappingProxyType(dict(commissioner_summary)), blockers, warnings,
        material_fingerprint(basis), generated_at or datetime.now(timezone.utc),
        ("league_rollover_policies", "rollover_owner_decisions", "rollover_commissioner_reviews",
         "contract_agreements", "contract_events", "cap_adjustments"), VALIDATOR_VERSION)


def to_simulation_input(package: RolloverAuthorityPreparationPackage, policy_fingerprint: str) -> AuthoritySimulationInput:
    if not package.execution_id: raise ValueError("execution_required_for_simulation_input")
    return AuthoritySimulationInput(package.execution_id, package.league_id, package.source_season, package.target_season,
        policy_fingerprint, package.owner_population_fingerprint, package.commissioner_population_fingerprint,
        package.preparation_fingerprint, package.publication_authority_plan.instructions,
        package.dead_cap_authority_plan.instructions, package.cap_authority_plan,
        package.cap_authority_plan.projected_team_cap_states, package.blockers, package.warnings)
