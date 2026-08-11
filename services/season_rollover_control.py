from __future__ import annotations

"""Narrow server-side boundary for the commissioner rollover control center.

Only canonical dry-run and execution-plan generation may cross the service-role
boundary. All lifecycle mutations remain authenticated-user RPC calls.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import os
from types import MappingProxyType
from typing import Any, Callable, Mapping

from supabase import create_client

from season_engine.authority_preparation import (
    AuthoritySimulationInput,
    AuthorityPreparationService,
    CapAuthorityPlan,
    CapAuthorityPlanner,
    DeadCapAuthorityInstruction,
    DeadCapAuthorityPlanner,
    PublicationAuthorityInstruction,
    PublicationAuthorityPlanner,
    TeamCapProjection,
    build_preparation_package,
)
from season_engine.commissioner_review import OUTCOME_MATRIX, commissioner_review_readiness
from season_engine.dry_run_simulator import (
    DryRunExecutionPlanInput,
    RolloverDryRunResult,
    RolloverDryRunValidationResult,
    SimulationChange,
    TableMutationSummary,
    TeamDryRunResult,
    TrustedDryRunGenerationService,
)
from season_engine.execution_plan import TrustedExecutionPlanService
from season_engine.commissioner_policy_draft import (
    CommissionerPolicyDraftService, RELEASE_TO_HOLD, SEVEN_DAY_NOTICE_RULE,
)
from season_engine.history.service import PreRolloverHistoryService
from season_engine.history.sleeper_source import HistorySource, SleeperHistorySource
from season_engine.rollover_window import RolloverPreflightRequest, RolloverPreflightService, canonical
from season_engine.contract_authority_preflight import ContractAuthorityPreflightService
from season_engine.rollover_service import stable_fingerprint

COMMISSIONER_ROLES = frozenset({"commissioner", "host", "admin"})
TRUSTED_CAPABILITIES = frozenset({"history_capture", "preflight_analysis", "dry_run_persistence", "plan_persistence"})

AUTHENTICATED_RPCS = frozenset({
    "approve_canonical_rollover_policy_authenticated",
    "create_rollover_execution_authenticated",
    "open_rollover_notice_window_authenticated",
    "submit_rollover_owner_decision_authenticated",
    "override_rollover_owner_decision_authenticated",
    "close_rollover_decision_window_authenticated",
    "cancel_rollover_execution_authenticated",
    "initialize_rollover_commissioner_reviews_authenticated",
    "begin_rollover_commissioner_review_authenticated",
    "submit_rollover_commissioner_review_authenticated",
    "supersede_rollover_commissioner_review_authenticated",
    "cancel_rollover_commissioner_review_authenticated",
    "prepare_rollover_authorities_authenticated",
    "supersede_rollover_authority_preparation_authenticated",
    "cancel_rollover_authority_preparation_authenticated",
    "approve_rollover_execution_plan_authenticated",
    "revoke_rollover_execution_plan_approval_authenticated",
    "execute_rollover_plan_authenticated",
    "publish_target_season_authority_authenticated",
    "activate_target_cap_authority_authenticated",
    "enable_target_free_agent_visibility_authenticated",
    "release_cutover_restrictions_authenticated",
    "refresh_published_ui_and_ai_context_authenticated",
})


class RolloverControlError(RuntimeError):
    """A deliberately sanitized, user-displayable control-center error."""


@dataclass(frozen=True)
class TrustedGenerationResult:
    kind: str
    id: str
    execution_id: str
    league_id: str
    status: str
    version: int
    fingerprint: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InitiationReadiness:
    league_id: str
    source_season: int | None
    target_season: int | None
    sleeper_league_id: str | None
    canonical_team_count: int
    mapped_team_count: int
    history_status: str
    policy_status: str
    policy_id: str | None
    execution_status: str
    blockers: tuple[str, ...]
    contract_authority_status: str = "not_available"
    contract_agreement_count: int | None = None
    contract_source_season_count: int | None = None
    contract_authority_blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleStage:
    number: int
    name: str
    status: str
    summary: str
    blockers: tuple[str, ...] = ()


STAGE_NAMES = ("Readiness", "Policy", "Owner Option Window", "History Capture", "Preflight, Reviews & Authority",
               "Dry Run", "Execution Plan", "Approval", "Execute", "Validate", "Publish", "Complete")


def derive_lifecycle_timeline(state: Mapping[str, Any], readiness: InitiationReadiness | None = None) -> tuple[LifecycleStage, ...]:
    execution = state.get("execution") or {}
    status = str(execution.get("status") or "not_started")
    simulations = list(state.get("simulations") or ())
    plans = list(state.get("plans") or ())
    approvals = list(state.get("approvals") or ())
    results = list(state.get("operation_results") or ())
    completed_ops = {int(row.get("operation_index") or 0) for row in results}
    finalizations = list(state.get("finalizations") or ())
    reviews = list(state.get("commissioner_reviews") or ())
    preparations = list(state.get("preparations") or ())
    publication_count = sum(bool(state.get(key)) for key in
        ("season_publications", "cap_publications", "market_publications", "cutover_releases", "context_generations"))
    status_rank = {"not_started": 0, "preflight_ready": 1, "notice_open": 2, "decision_window_open": 2,
                   "decision_window_closed": 3, "authority_initializing": 4, "authority_ready": 5,
                   "plan_ready": 6, "awaiting_execution_approval": 7, "execution_ready": 8,
                   "executing": 9, "executed_unpublished": 10, "completed": 11}
    readiness_blockers = tuple(readiness.blockers) if readiness else ()
    complete = [
        bool(readiness and not readiness_blockers) or bool(execution),
        bool(readiness and readiness.policy_status == "approved") or bool(execution),
        status_rank.get(status, 0) >= 3,
        bool(readiness and readiness.history_status == "validated") or bool(execution),
        status_rank.get(status, 0) >= 5,
        any(row.get("simulation_status") == "valid" for row in simulations) or status_rank.get(status, 0) >= 8,
        any(row.get("plan_status") in {"valid", "approved_for_execution"} for row in plans) or status_rank.get(status, 0) >= 8,
        any(row.get("approval_status") == "approved" for row in approvals) or status_rank.get(status, 0) >= 8,
        set(range(1, 32)).issubset(completed_ops) or status in {"executed_unpublished", "completed"},
        bool(finalizations),
        publication_count == 5,
        bool(state.get("context_generations")) and status == "completed",
    ]
    stage_blockers: list[tuple[str, ...]] = [() for _ in STAGE_NAMES]
    stage_blockers[0] = readiness_blockers
    if simulations:
        stage_blockers[5] = tuple(str(x) for row in simulations for x in (row.get("blockers") or ()))
    if plans:
        stage_blockers[6] = tuple(str(x) for row in plans for x in (row.get("blockers") or ()))
    if finalizations and not bool(finalizations[0].get("publication_eligible")):
        stage_blockers[10] = ("publication_not_eligible",)
    blocked_reviews = [str(row.get("id")) for row in reviews if row.get("review_state") in {"blocked", "evidence_required"}]
    if blocked_reviews: stage_blockers[4] = tuple(blocked_reviews)
    if not execution and readiness and not readiness_blockers:
        if readiness.policy_status != "approved": current = 1
        elif readiness.history_status != "validated": current = 3
        else: current = 4
    else:
        current = next((i for i, value in enumerate(complete) if not value), len(complete) - 1)
    stages = []
    for index, name in enumerate(STAGE_NAMES):
        if complete[index]: stage_status = "complete"
        elif stage_blockers[index]: stage_status = "blocked"
        elif index == current: stage_status = "current"
        else: stage_status = "pending"
        if index == 2 and status in {"notice_open", "decision_window_open"}: stage_status = "warning"
        if index == 8 and status == "executing": stage_status = "warning"
        summary = {
            0: "Canonical prerequisites", 1: "Certified seven-day policy", 2: status.replace("_", " "),
            3: (readiness.history_status if readiness else "persisted evidence"),
            4: (f"{sum(r.get('review_state') in {'approved','rejected'} for r in reviews)}/{len(reviews)} reviews; "
                f"{sum(p.get('authority_status') == 'prepared' for p in preparations)}/3 authorities; {status.replace('_',' ')}"),
            5: f"{len(simulations)} simulation(s)", 6: f"{len(plans)} plan(s)", 7: f"{len(approvals)} approval(s)",
            8: f"{len(completed_ops.intersection(range(1, 32)))} of 31 operations", 9: f"{len(finalizations)} finalization(s)",
            10: f"{publication_count} of 5 publication steps", 11: status.replace("_", " "),
        }[index]
        stages.append(LifecycleStage(index + 1, name, stage_status, summary, stage_blockers[index]))
    return tuple(stages)


REPORT_FIELDS = (
    "source_season", "target_season", "canonical_teams", "contracts_continuing", "contracts_advancing",
    "options_exercised", "releases", "commissioner_holds", "dead_cap_obligations", "dead_cap_total",
    "target_roster_assignments", "taxi_unlocks", "taxi_non_return", "ir_carry_forward", "draft_classes",
    "draft_picks", "rookie_eligibility", "free_agent_eligibility", "expiring_contracts", "target_cap_teams",
    "over_cap_publication_blockers", "prepared_standings_rows", "prepared_matchup_state", "prepared_playoff_structure",
    "validation_checks", "warnings", "blockers", "commissioner_review_cases", "commissioner_reviews_completed",
    "commissioner_reviews_blocked", "commissioner_review_outcomes", "authority_preparation_status",
    "publication_candidate_count", "commissioner_hold_count", "dead_cap_instruction_count", "cap_plan_team_count",
    "authority_blockers", "authority_warnings",
)


def build_commissioner_rollover_report(state: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = state.get("execution") or {}
    simulations = list(state.get("simulations") or ())
    plans = list(state.get("plans") or ())
    preparations = list(state.get("preparations") or ())
    reviews = list(state.get("commissioner_reviews") or ())
    report: dict[str, Any] = {key: None for key in REPORT_FIELDS}
    report["source_season"] = execution.get("source_season")
    report["target_season"] = execution.get("target_season")
    cap_sets = list(state.get("prepared_cap_sets") or ())
    free_sets = list(state.get("prepared_free_agent_sets") or ())
    expiring_sets = list(state.get("prepared_expiring_sets") or ())
    report["target_cap_teams"] = cap_sets[0].get("canonical_team_count") if cap_sets else None
    report["free_agent_eligibility"] = free_sets[0].get("expected_player_count") if free_sets else None
    report["expiring_contracts"] = expiring_sets[0].get("expected_row_count") if expiring_sets else None
    if reviews:
        report["commissioner_review_cases"] = len(reviews)
        report["commissioner_reviews_completed"] = sum(x.get("review_state") in {"approved", "rejected"} for x in reviews)
        report["commissioner_reviews_blocked"] = sum(x.get("review_state") in {"blocked", "evidence_required"} for x in reviews)
        outcomes: dict[str, int] = {}
        for row in reviews:
            if row.get("outcome"): outcomes[str(row["outcome"])] = outcomes.get(str(row["outcome"]), 0) + 1
        report["commissioner_review_outcomes"] = outcomes
    if preparations:
        report["authority_preparation_status"] = {str(x.get("authority_type")): x.get("authority_status") for x in preparations}
        report["authority_blockers"] = [str(v) for x in preparations for v in (x.get("blockers") or ())]
        report["authority_warnings"] = [str(v) for x in preparations for v in (x.get("warnings") or ())]
        payloads = {str(x.get("authority_type")): x.get("preparation_payload") or {} for x in preparations}
        report["publication_candidate_count"] = len(payloads.get("publication", {}).get("instructions") or ())
        report["commissioner_hold_count"] = sum(i.get("publication_action") == "hold" for i in payloads.get("publication", {}).get("instructions") or ())
        report["dead_cap_instruction_count"] = len(payloads.get("dead_cap", {}).get("instructions") or ())
        cap_payload = payloads.get("salary_cap", {}).get("cap_authority_plan") or {}
        report["cap_plan_team_count"] = len(cap_payload.get("projected_team_cap_states") or ())
    if simulations:
        payload = simulations[-1].get("result_payload") or {}
        simulation = payload.get("simulation") or {}
        summary = simulation.get("validation_summary") or {}
        for key in REPORT_FIELDS:
            if report[key] is None and key in summary: report[key] = summary[key]
        report["warnings"] = list(simulations[-1].get("warnings") or ())
        report["blockers"] = list(simulations[-1].get("blockers") or ())
    if plans and report["validation_checks"] is None:
        report["validation_checks"] = (plans[-1].get("validation_payload") or {}).get("checks")
    if report["warnings"] is None:
        report["warnings"] = [str(x) for row in preparations for x in (row.get("warnings") or ())]
    if report["blockers"] is None:
        report["blockers"] = [str(x) for row in preparations for x in (row.get("blockers") or ())]
    return MappingProxyType(report)


def _secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        try:
            import streamlit as st
            value = str(st.secrets.get(name, "")).strip()
        except Exception:
            value = ""
    return value


def rollover_admin_password_configured() -> bool:
    return bool(_secret("ROLLOVER_ADMIN_PASSWORD"))


def verify_rollover_admin_password(candidate: str) -> bool:
    import hmac
    expected = _secret("ROLLOVER_ADMIN_PASSWORD")
    return bool(expected) and hmac.compare_digest(expected, str(candidate or ""))


def _service_client() -> Any:
    """Create, but never expose or cache, the narrowly used service client."""
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RolloverControlError("Trusted rollover generation is not configured.")
    return create_client(url, key)


def _rows(client: Any, table: str, **filters: Any) -> list[dict[str, Any]]:
    query = client.table(table).select("*")
    for key, value in filters.items():
        query = query.eq(key, value)
    return list(query.execute().data or [])


def _authenticated_actor(client: Any) -> str:
    try:
        response = client.auth.get_user()
        user = getattr(response, "user", None)
        actor = str(getattr(user, "id", "") or "")
    except Exception as exc:
        raise RolloverControlError("Authentication could not be verified.") from None
    if not actor:
        raise RolloverControlError("Authentication is required.")
    return actor


def require_canonical_commissioner(client: Any, league_id: str) -> str:
    if not str(league_id or "").strip():
        raise RolloverControlError("An active league is required.")
    actor = _authenticated_actor(client)
    try:
        memberships = _rows(client, "league_memberships", league_id=league_id, user_id=actor)
    except Exception:
        raise RolloverControlError("Canonical league membership could not be verified.") from None
    roles = {str(row.get("role") or "").strip().lower() for row in memberships}
    if len(memberships) != 1 or not roles.intersection(COMMISSIONER_ROLES):
        raise RolloverControlError("Canonical commissioner authority is required.")
    return actor


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _json_safe(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_safe(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Mapping): return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)): return [_json_safe(item) for item in value]
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, datetime): return value.astimezone(timezone.utc).isoformat()
    return value


def _team_cap(row: Mapping[str, Any]) -> TeamCapProjection:
    values = dict(row)
    for key in ("source_salary_total", "retained_salary_total", "recontract_salary_total",
                "planned_release_relief", "planned_dead_cap", "cap_adjustments", "cap_credits_in",
                "cap_credits_out", "projected_cap_charge", "projected_cap_space"):
        if values.get(key) is not None:
            values[key] = _decimal(values[key])
    for key in ("blockers", "warnings"):
        values[key] = tuple(values.get(key) or ())
    return TeamCapProjection(**values)


def _simulation_input(evidence: Mapping[str, Any], league_id: str) -> AuthoritySimulationInput:
    execution = dict(evidence["execution"])
    preparations = {row["authority_type"]: row for row in evidence["preparations"]}
    if str(execution.get("league_id")) != league_id:
        raise ValueError("execution league mismatch")
    payloads = {name: dict(row.get("preparation_payload") or {}) for name, row in preparations.items()}
    publication_rows = payloads["publication"].get("instructions") or payloads["publication"].get("publication_instructions")
    dead_rows = payloads["dead_cap"].get("instructions") or payloads["dead_cap"].get("dead_cap_instructions")
    cap_row = payloads["salary_cap"].get("cap_authority_plan") or payloads["salary_cap"].get("plan") or payloads["salary_cap"]
    if not isinstance(publication_rows, list) or not isinstance(dead_rows, list) or not isinstance(cap_row, Mapping):
        raise ValueError("canonical authority payload is incomplete")
    teams = tuple(_team_cap(row) for row in (cap_row.get("projected_team_cap_states") or ()))
    cap_values = dict(cap_row)
    cap_values["projected_team_cap_states"] = teams
    for key in ("source_cap", "target_cap", "base_cap", "scaling_factor", "retained_salary_total",
                "proposed_recontract_salary_total", "planned_dead_cap_total", "cap_adjustment_total", "cap_credit_total"):
        if cap_values.get(key) is not None:
            cap_values[key] = _decimal(cap_values[key])
    for key in ("blockers", "warnings"):
        cap_values[key] = tuple(cap_values.get(key) or ())
    cap = CapAuthorityPlan(**cap_values)
    pubs = tuple(PublicationAuthorityInstruction(**{**row,
        "publication_blockers": tuple(row.get("publication_blockers") or ()),
        "publication_warnings": tuple(row.get("publication_warnings") or ())}) for row in publication_rows)
    dead = tuple(DeadCapAuthorityInstruction(**{**row,
        "salary_basis": None if row.get("salary_basis") is None else _decimal(row["salary_basis"]),
        "calculated_amount": _decimal(row["calculated_amount"]),
        "blockers": tuple(row.get("blockers") or ()), "warnings": tuple(row.get("warnings") or ())}) for row in dead_rows)
    prep_fp = {str(row["preparation_fingerprint"]) for row in preparations.values()}
    if len(prep_fp) != 1:
        raise ValueError("authority preparation fingerprint mismatch")
    return AuthoritySimulationInput(
        execution_id=str(execution["id"]), league_id=league_id,
        source_season=int(execution["source_season"]), target_season=int(execution["target_season"]),
        policy_fingerprint=str(execution["policy_fingerprint"]),
        owner_population_fingerprint=str(execution["decision_population_fingerprint"]),
        commissioner_population_fingerprint=str(preparations["publication"]["commissioner_population_fingerprint"]),
        authority_preparation_fingerprint=next(iter(prep_fp)), publication_instructions=pubs,
        dead_cap_instructions=dead, cap_authority_plan=cap, team_cap_projections=teams,
        blockers=tuple(x for row in preparations.values() for x in (row.get("blockers") or ())),
        warnings=tuple(x for row in preparations.values() for x in (row.get("warnings") or ())),
        policy_id=str(execution["policy_id"]), preflight_fingerprint=str(execution["preflight_fingerprint"]),
        publication_authority_fingerprint=str(preparations["publication"]["authority_fingerprint"]),
        dead_cap_authority_fingerprint=str(preparations["dead_cap"]["authority_fingerprint"]),
        salary_cap_authority_fingerprint=str(preparations["salary_cap"]["authority_fingerprint"]),
        finalized_owner_outcomes=tuple(evidence["owner_outcomes"]),
        finalized_commissioner_outcomes=tuple(evidence["commissioner_outcomes"]),
    )


def _change(row: Mapping[str, Any]) -> SimulationChange:
    values = dict(row)
    for key in ("current_state", "simulated_state"):
        values[key] = MappingProxyType(dict(values.get(key) or {}))
    for key in ("dependencies", "blockers", "warnings"):
        values[key] = tuple(values.get(key) or ())
    return SimulationChange(**values)


def _decode_simulation(row: Mapping[str, Any]) -> tuple[DryRunExecutionPlanInput, RolloverDryRunResult, RolloverDryRunValidationResult]:
    payload = dict(row.get("result_payload") or {})
    raw_result, raw_validation = payload.get("simulation"), payload.get("validation")
    if not isinstance(raw_result, Mapping) or not isinstance(raw_validation, Mapping):
        raise ValueError("canonical simulation payload is incomplete")
    result_values = dict(raw_result)

    # Canonical payload intentionally excludes runtime persistence fields.
    # Restore them from the persisted simulation row.
    result_values["id"] = str(row["id"])
    result_values["generated_by"] = str(row["generated_by"])
    result_values["generated_at"] = datetime.fromisoformat(
        str(row["generated_at"]).replace("Z", "+00:00")
    )
    for key in ("blockers", "warnings", "provenance"):
        result_values[key] = tuple(result_values.get(key) or ())
    for key in ("contract_changes", "roster_changes", "publication_changes", "dead_cap_changes", "cap_changes",
                "season_changes", "taxi_changes", "ir_changes", "draft_changes", "rookie_class_changes", "transaction_effects"):
        result_values[key] = tuple(_change(item) for item in (result_values.get(key) or ()))
    result_values["team_results"] = tuple(TeamDryRunResult(**{**item,
        **{k: (None if item.get(k) is None else _decimal(item[k])) for k in ("source_cap", "target_cap", "retained_salary", "recontract_salary", "rookie_salary", "planned_dead_cap", "cap_adjustments", "cap_credits_in", "cap_credits_out", "total_cap_charge", "projected_cap_space")},
        "warnings": tuple(item.get("warnings") or ())}) for item in (result_values.get("team_results") or ()))
    result_values["player_results"] = tuple(MappingProxyType(dict(x)) for x in (result_values.get("player_results") or ()))
    result_values["page_previews"] = MappingProxyType({k: tuple(MappingProxyType(dict(x)) for x in v) for k, v in (result_values.get("page_previews") or {}).items()})
    result_values["table_mutation_summary"] = tuple(TableMutationSummary(**{**item,
        "expected_primary_keys": tuple(item.get("expected_primary_keys") or ()), "blockers": tuple(item.get("blockers") or ())}) for item in (result_values.get("table_mutation_summary") or ()))
    result_values["validation_summary"] = MappingProxyType(dict(result_values.get("validation_summary") or {}))
    result_values["metadata"] = MappingProxyType(dict(result_values.get("metadata") or {}))
    result = RolloverDryRunResult(**result_values)
    validation_values = dict(raw_validation)
    validation_values["validated_at"] = datetime.fromisoformat(str(validation_values["validated_at"]).replace("Z", "+00:00"))
    validation_values["checks"] = MappingProxyType(dict(validation_values.get("checks") or {}))
    validation_values["blockers"] = tuple(validation_values.get("blockers") or ())
    validation_values["warnings"] = tuple(validation_values.get("warnings") or ())
    validation = RolloverDryRunValidationResult(**validation_values)
    plan_input = DryRunExecutionPlanInput(
        result.execution_id, result.league_id, result.source_season, result.target_season, result.id,
        result.simulation_version, result.simulator_version, validation.validator_version,
        result.input_fingerprint, result.result_fingerprint, result.preflight_fingerprint,
        result.policy_fingerprint, result.owner_population_fingerprint,
        result.commissioner_population_fingerprint, result.authority_preparation_fingerprint,
    )
    return plan_input, result, validation


class SeasonRolloverControlService:
    def __init__(self, authenticated_client: Any, league_id: str,
                 service_client_factory: Callable[[], Any] = _service_client,
                 history_source_factory: Callable[[], HistorySource] = SleeperHistorySource):
        self._client = authenticated_client
        self.league_id = str(league_id or "")
        self._service_client_factory = service_client_factory
        self._history_source_factory = history_source_factory

    def _trusted_client(self, capability: str) -> Any:
        if capability not in TRUSTED_CAPABILITIES:
            raise RolloverControlError("Trusted rollover capability is not allowed.")
        return self._service_client_factory()

    def authorize(self) -> str:
        return require_canonical_commissioner(self._client, self.league_id)

    def load_initiation_readiness(self) -> InitiationReadiness:
        self.authorize()
        try:
            service = self._client
            seasons = _rows(service, "league_seasons", league_id=self.league_id)
            active = [row for row in seasons if row.get("is_active") and row.get("status") == "active"]
            if len(active) != 1:
                return InitiationReadiness(self.league_id, None, None, None, 0, 0, "missing",
                    "missing", None, "not_started", ("exactly_one_active_season_required",))
            source = active[0]; source_season = int(source["season"]); target_season = source_season + 1
            target = [row for row in seasons if int(row.get("season") or 0) == target_season]
            teams = _rows(service, "league_teams", league_id=self.league_id)
            mappings = _rows(service, "season_team_mappings", league_season_id=source["id"])
            policies = [row for row in _rows(service, "league_rollover_policies", league_id=self.league_id)
                        if int(row.get("source_season") or 0) == source_season and int(row.get("target_season") or 0) == target_season
                        and row.get("status") == "approved" and row.get("effective_at") is None]
            history = _rows(service, "historical_capture_executions", league_season_id=source["id"])
            executions = [row for row in _rows(service, "rollover_executions", league_id=self.league_id)
                          if int(row.get("source_season") or 0) == source_season and int(row.get("target_season") or 0) == target_season
                          and row.get("status") != "cancelled"]
            blockers = []
            if len(target) != 1 or target[0].get("is_active") or target[0].get("status") != "scheduled": blockers.append("target_season_not_scheduled")
            if not source.get("sleeper_league_id"): blockers.append("source_sleeper_linkage_missing")
            if not teams or len(mappings) != len(teams): blockers.append("canonical_team_mapping_incomplete")
            if len(policies) > 1: blockers.append("duplicate_approved_policy")
            if len(executions) > 1: blockers.append("duplicate_active_execution")
            history_valid = len(history) == 1 and history[0].get("status") in {"validated", "finalized"}
            contract = ContractAuthorityPreflightService(service).run(self.league_id, source_season, target_season)
            return InitiationReadiness(self.league_id, source_season, target_season,
                source.get("sleeper_league_id"), len(teams), len(mappings), "validated" if history_valid else "required",
                "approved" if len(policies) == 1 else "required", str(policies[0]["id"]) if len(policies) == 1 else None,
                str(executions[0].get("status")) if len(executions) == 1 else "not_started", tuple(blockers),
                "ready" if contract.ready else "blocked", contract.agreement_count,
                contract.source_season_count, contract.blockers)
        except RolloverControlError:
            raise
        except Exception:
            raise RolloverControlError("Rollover initiation readiness could not be loaded.") from None

    def approve_canonical_seven_day_policy(self) -> TrustedGenerationResult:
        self.authorize(); readiness = self.load_initiation_readiness()
        if readiness.source_season != 2025 or readiness.target_season != 2026:
            raise RolloverControlError("The certified policy supports only the canonical 2025-to-2026 boundary.")
        if readiness.policy_status == "approved":
            rows = _rows(self._client, "league_rollover_policies", id=readiness.policy_id)
            row = rows[0]
            return TrustedGenerationResult("policy", str(row["id"]), "", self.league_id, "approved", int(row["version"]), str(row["fingerprint"]))
        try:
            response = self.authenticated_rpc("approve_canonical_rollover_policy_authenticated", {
                "league_id": self.league_id,
                "source_season": readiness.source_season,
                "target_season": readiness.target_season,
                "deadline_rule": SEVEN_DAY_NOTICE_RULE,
                "failure_to_act_outcome": RELEASE_TO_HOLD,
            })
            row = dict(response.get("policy") or response)
            return TrustedGenerationResult("policy", str(row["id"]), "", self.league_id, "approved",
                                           int(row["version"]), str(row["fingerprint"]))
        except Exception:
            raise RolloverControlError("Canonical rollover policy approval was rejected.") from None

    def capture_immutable_history(self) -> TrustedGenerationResult:
        self.authorize(); readiness = self.load_initiation_readiness()
        if readiness.history_status == "validated":
            service = self._trusted_client("history_capture")
            seasons = [x for x in _rows(service, "league_seasons", league_id=self.league_id) if x.get("is_active")]
            rows = _rows(service, "historical_capture_executions", league_season_id=seasons[0]["id"])
            row = rows[0]
            return TrustedGenerationResult("history", str(row["id"]), "", self.league_id,
                str(row["status"]), 1, str(row["source_fingerprint"]))
        try:
            # Authority is checked before the narrowly scoped service client exists.
            source = self._history_source_factory()
            result = PreRolloverHistoryService(self._trusted_client("history_capture"), source=source).capture(self.league_id, dry_run=False)
            data = result.database_result or {}
            return TrustedGenerationResult("history", str(data.get("execution_id") or "captured"), "",
                self.league_id, str(data.get("status") or "validated"), 1, result.plan.source_fingerprint)
        except Exception:
            raise RolloverControlError("Immutable historical capture was rejected.") from None

    def run_preflight_and_create_execution(self) -> Mapping[str, Any]:
        actor = self.authorize(); readiness = self.load_initiation_readiness()
        if readiness.execution_status != "not_started":
            raise RolloverControlError("A rollover execution already exists for this season boundary.")
        if readiness.policy_status != "approved" or readiness.history_status != "validated" or readiness.blockers:
            raise RolloverControlError("Policy, history, season, and mapping prerequisites must pass before preflight.")
        try:
            trusted = self._trusted_client("preflight_analysis")
            policy = _rows(trusted, "league_rollover_policies", id=readiness.policy_id)
            if len(policy) != 1: raise ValueError("approved policy missing")
            request = RolloverPreflightRequest(self.league_id, int(readiness.source_season), int(readiness.target_season),
                str(policy[0]["id"]), str(policy[0]["fingerprint"]), actor,
                f"control-center:{self.league_id}:{readiness.source_season}:{readiness.target_season}", datetime.now(timezone.utc))
            preflight = RolloverPreflightService(trusted).run(request)
            if not preflight.execution_creation_eligible or preflight.blockers:
                raise RolloverControlError("Canonical preflight is blocked: " + ", ".join(preflight.blockers))
            owner_population = [canonical(case) for case in preflight.owner_population_preview.cases]
            metadata = {"canonical_preflight": {
                "owner_population": owner_population,
                "owner_population_fingerprint": preflight.owner_population_preview.fingerprint,
                "commissioner_population_fingerprint": preflight.commissioner_population_preview.fingerprint,
                "commissioner_population": [{**canonical(case), "league_id": self.league_id,
                    "source_season": readiness.source_season, "target_season": readiness.target_season,
                    "player_name": case.player_name, "evidence": dict(case.preserved_facts)}
                    for case in preflight.commissioner_population_preview.cases],
                "checks": dict(preflight.checks), "warnings": list(preflight.warnings),
            }}
            response = self.authenticated_rpc("create_rollover_execution_authenticated", {
                "league_id": self.league_id, "source_season": readiness.source_season,
                "target_season": readiness.target_season, "policy_id": policy[0]["id"],
                "expected_policy_fingerprint": policy[0]["fingerprint"],
                "expected_preflight_fingerprint": preflight.preflight_fingerprint,
                "before_state_fingerprint": str(preflight.contract_authority_state["source_fingerprint"]),
                "metadata": metadata, "material_metadata": {"source": "commissioner_rollover_control"},
                "idempotency_key": f"rollover-init:{self.league_id}:{readiness.source_season}:{readiness.target_season}",
            })
            return MappingProxyType({"execution": dict(response.get("execution") or {}),
                "preflight_status": "eligible", "blockers": (), "warnings": tuple(preflight.warnings)})
        except RolloverControlError:
            raise
        except Exception:
            raise RolloverControlError("Canonical preflight or execution creation was rejected.") from None

    def _scoped_execution(self, execution_id: str) -> Mapping[str, Any]:
        self.authorize()
        rows = _rows(self._client, "rollover_executions", id=execution_id, league_id=self.league_id)
        if len(rows) != 1:
            raise RolloverControlError("The rollover execution is missing or outside the active league.")
        return MappingProxyType(dict(rows[0]))

    def load_state(self) -> Mapping[str, Any]:
        self.authorize()
        try:
            executions = _rows(self._client, "rollover_executions", league_id=self.league_id)
            executions.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
            execution = executions[0] if executions else None
            result: dict[str, Any] = {"execution": execution}
            if execution:
                eid = execution["id"]
                for key, table in (("preparations", "rollover_authority_preparations"),
                                   ("simulations", "rollover_dry_run_simulations"),
                                   ("plans", "rollover_execution_plans"),
                                   ("approvals", "rollover_execution_plan_approvals"),
                                   ("locks", "rollover_execution_locks"),
                                   ("operation_results", "rollover_execution_operation_results"),
                                   ("commissioner_reviews", "rollover_commissioner_reviews"),
                                   ("finalizations", "rollover_executed_unpublished_finalizations"),
                                   ("season_publications", "rollover_target_season_authority_publications"),
                                   ("cap_publications", "rollover_target_cap_authority_publications"),
                                   ("market_publications", "rollover_target_market_visibility_publications"),
                                   ("cutover_releases", "rollover_cutover_release_publications"),
                                   ("context_generations", "publication_context_generations"),
                                   ("prepared_cap_sets", "prepared_team_cap_sets"),
                                   ("prepared_free_agent_sets", "prepared_free_agent_eligibility_sets"),
                                   ("prepared_expiring_sets", "prepared_expiring_contract_sets"),
                                   ("cache_manifests", "season_cache_invalidation_manifests")):
                    result[key] = _rows(self._client, table, rollover_execution_id=eid)
                result["owner_decisions"] = _rows(self._client, "rollover_owner_decisions", rollover_execution_id=eid)
            return MappingProxyType(result)
        except RolloverControlError:
            raise
        except Exception:
            raise RolloverControlError("Rollover state could not be loaded.") from None

    def review_readiness(self, execution_id: str) -> Mapping[str, Any]:
        execution = self._scoped_execution(execution_id)
        reviews = _rows(self._client, "rollover_commissioner_reviews", rollover_execution_id=execution_id)
        decisions = _rows(self._client, "rollover_owner_decisions", rollover_execution_id=execution_id)
        return MappingProxyType(dict(commissioner_review_readiness(execution, reviews, decisions)))

    @staticmethod
    def allowed_review_outcomes(review: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(sorted(OUTCOME_MATRIX.get(str(review.get("review_type") or ""), frozenset())))

    def initialize_canonical_reviews(self, execution_id: str) -> Mapping[str, Any]:
        execution = self._scoped_execution(execution_id)
        if execution.get("status") != "decision_window_closed":
            raise RolloverControlError("Commissioner reviews require a closed owner-option window.")
        preflight = dict((execution.get("metadata") or {}).get("canonical_preflight") or {})
        population = list(preflight.get("commissioner_population") or ())
        fingerprint = str(preflight.get("commissioner_population_fingerprint") or "")
        if not population or not fingerprint:
            raise RolloverControlError("Canonical commissioner-review population is unavailable.")
        return self.authenticated_rpc("initialize_rollover_commissioner_reviews_authenticated", {
            "rollover_execution_id": execution_id, "commissioner_population": population,
            "expected_commissioner_population_fingerprint": fingerprint,
            "calculated_commissioner_population_fingerprint": fingerprint,
            "idempotency_key": f"review-initialize:{execution_id}",
            "material_metadata": {"source": "commissioner_rollover_control"},
        })

    def begin_canonical_review(self, review: Mapping[str, Any]) -> Mapping[str, Any]:
        self._scoped_execution(str(review.get("rollover_execution_id") or ""))
        return self.authenticated_rpc("begin_rollover_commissioner_review_authenticated", {
            "review_id": review["id"], "expected_revision_number": review["revision_number"],
            "expected_review_fingerprint": review["review_fingerprint"],
            "reason": "Commissioner Control Center review started",
            "idempotency_key": f"review-begin:{review['id']}:{review['revision_number']}",
        })

    def submit_canonical_review(self, review: Mapping[str, Any], outcome: str, reason: str,
                                evidence: Mapping[str, Any]) -> Mapping[str, Any]:
        self._scoped_execution(str(review.get("rollover_execution_id") or ""))
        if outcome not in OUTCOME_MATRIX.get(str(review.get("review_type") or ""), frozenset()):
            raise RolloverControlError("The selected outcome is not allowed for this review type.")
        return self.authenticated_rpc("submit_rollover_commissioner_review_authenticated", {
            "review_id": review["id"], "proposed_outcome": outcome, "reason": str(reason).strip(),
            "evidence": dict(evidence), "expected_revision_number": review["revision_number"],
            "expected_review_fingerprint": review["review_fingerprint"],
            "idempotency_key": f"review-submit:{review['id']}:{review['revision_number']}:{outcome}",
        })

    def prepare_canonical_authorities(self, execution_id: str) -> TrustedGenerationResult:
        execution = self._scoped_execution(execution_id)
        readiness = self.review_readiness(execution_id)
        if readiness.get("status") != "authority_preparation_required":
            raise RolloverControlError("Every commissioner review must be complete before authority preparation.")
        decisions = _rows(self._client, "rollover_owner_decisions", rollover_execution_id=execution_id)
        reviews = _rows(self._client, "rollover_commissioner_reviews", rollover_execution_id=execution_id)
        teams = _rows(self._client, "league_teams", league_id=self.league_id)
        rules = _rows(self._client, "league_rules", league_id=self.league_id)
        if len(rules) != 1 or not teams: raise RolloverControlError("Canonical cap inputs are incomplete.")
        publication_records = []
        for row in decisions:
            publication_records.append({"player_id": row.get("player_id"), "agreement_id": row.get("agreement_id"),
                "league_team_id": row.get("league_team_id"), "source_status": "expired",
                "planned_contract_outcome": "retain" if row.get("planned_outcome") == "retain" else "release",
                "owner_outcome_final": True, "commissioner_outcome_final": True,
                "commissioner_outcome": "reject_publication", "owner_decision_id": row.get("id")})
        for row in reviews:
            publication_records.append({"player_id": row.get("player_id"), "agreement_id": row.get("agreement_id"),
                "league_team_id": row.get("league_team_id"), "source_status": "expired",
                "planned_contract_outcome": "release", "owner_outcome_final": True,
                "commissioner_outcome_final": row.get("review_state") in {"approved", "rejected"},
                "commissioner_outcome": row.get("outcome"), "commissioner_review_id": row.get("id")})
        publication = PublicationAuthorityPlanner().plan(publication_records)
        dead = DeadCapAuthorityPlanner().plan([{"player_id": x.get("player_id"), "agreement_id": x.get("agreement_id"),
            "league_team_id": x.get("league_team_id"), "dead_cap_requested": False,
            "termination_type": "natural_expiration"} for x in (*decisions, *reviews)], int(execution["target_season"]))
        cap_value = rules[0].get("salary_cap") or rules[0].get("salary_cap_limit")
        cap = CapAuthorityPlanner().plan(league_id=self.league_id, source_season=int(execution["source_season"]),
            target_season=int(execution["target_season"]), source_cap=cap_value, target_cap=cap_value,
            teams=[{"league_team_id": row["id"], "recontract_salary_total": 0} for row in teams])
        preflight = dict((execution.get("metadata") or {}).get("canonical_preflight") or {})
        package = build_preparation_package(league_id=self.league_id, source_season=int(execution["source_season"]),
            target_season=int(execution["target_season"]), policy_id=str(execution["policy_id"]), execution_id=execution_id,
            owner_population_fingerprint=str(execution["decision_population_fingerprint"]),
            commissioner_population_fingerprint=str(preflight["commissioner_population_fingerprint"]),
            publication_plan=publication, dead_cap_plan=dead, cap_plan=cap,
            owner_summary={"count": len(decisions)}, commissioner_summary={"count": len(reviews)})
        def domain(kind: str, plan: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
            evidence = getattr(plan, "source_evidence_fingerprint", getattr(plan, "evidence_fingerprint", ""))
            return {"authority_type": kind, "version": 1, "evidence_fingerprint": evidence,
                "authority_fingerprint": plan.authority_fingerprint,
                "preparation_fingerprint": package.preparation_fingerprint,
                "preparation_payload": _json_safe(payload), "blockers": list(plan.blockers), "warnings": list(plan.warnings)}
        request = {"execution_id": execution_id, "league_id": self.league_id,
            "source_season": execution["source_season"], "target_season": execution["target_season"],
            "expected_execution_status": "decision_window_closed", "expected_policy_fingerprint": execution["policy_fingerprint"],
            "expected_owner_population_fingerprint": execution["decision_population_fingerprint"],
            "expected_commissioner_population_fingerprint": preflight["commissioner_population_fingerprint"],
            "expected_publication_evidence_fingerprint": publication.source_evidence_fingerprint,
            "expected_dead_cap_evidence_fingerprint": dead.source_evidence_fingerprint,
            "expected_cap_evidence_fingerprint": cap.evidence_fingerprint,
            "expected_aggregate_preparation_fingerprint": package.preparation_fingerprint,
            "authority_preparations": [domain("publication", publication, {"instructions": publication.instructions}),
                domain("dead_cap", dead, {"instructions": dead.instructions}),
                domain("salary_cap", cap, {"cap_authority_plan": cap})],
            "idempotency_key": f"authority-prepare:{execution_id}",
            "material_metadata": {"source": "commissioner_rollover_control"}}
        result = AuthorityPreparationService(self._client).prepare(request)
        rows = result["preparations"]
        blockers = tuple(str(x) for row in rows for x in row.blockers)
        return TrustedGenerationResult("authority_preparation", execution_id, execution_id, self.league_id,
            "prepared" if not blockers else "blocked", 1, package.preparation_fingerprint, blockers, package.warnings)

    def authenticated_rpc(self, name: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if name not in AUTHENTICATED_RPCS:
            raise RolloverControlError("This lifecycle action is not allowed by the control center.")
        self.authorize()
        try:
            data = self._client.rpc(name, {"p_request": dict(request)}).execute().data
        except Exception:
            raise RolloverControlError("The lifecycle action was rejected. Reload state and verify its prerequisites.") from None
        if not isinstance(data, Mapping):
            raise RolloverControlError("The lifecycle action returned an invalid response.")
        return MappingProxyType(dict(data))

    def generate_canonical_dry_run(self, execution_id: str, request: Mapping[str, Any]) -> TrustedGenerationResult:
        execution = self._scoped_execution(execution_id)
        if execution.get("status") != "authority_ready":
            raise RolloverControlError("The execution is not ready for canonical simulation.")
        try:
            service = TrustedDryRunGenerationService(self._client, self._trusted_client("dry_run_persistence"))
            row = service.generate_from_execution(execution_id, dict(request),
                lambda evidence: _simulation_input(evidence, self.league_id))
            return TrustedGenerationResult("dry_run", row.id, row.rollover_execution_id, row.league_id,
                row.simulation_status, row.simulation_version, row.result_fingerprint,
                tuple(str(x) for x in row.blockers), tuple(str(x) for x in row.warnings))
        except RolloverControlError:
            raise
        except Exception:
            raise RolloverControlError(
                "Canonical dry-run generation was rejected."
            ) from None

    def generate_canonical_execution_plan(self, execution_id: str, simulation_id: str,
                                          request: Mapping[str, Any]) -> TrustedGenerationResult:
        self._scoped_execution(execution_id)
        simulations = _rows(self._client, "rollover_dry_run_simulations", id=simulation_id,
                            rollover_execution_id=execution_id, league_id=self.league_id)
        if len(simulations) != 1:
            raise RolloverControlError("The canonical simulation is missing or stale.")
        try:
            service = TrustedExecutionPlanService(self._client, self._trusted_client("plan_persistence"))
            row = service.generate_from_storage(execution_id, simulation_id, dict(request),
                lambda _execution, simulation: _decode_simulation(simulation))
            return TrustedGenerationResult("execution_plan", str(row["id"]), execution_id,
                self.league_id, str(row["plan_status"]), int(row["plan_version"]),
                str(row["plan_fingerprint"]), tuple(str(x) for x in (row.get("blockers") or ())),
                tuple(str(x) for x in (row.get("warnings") or ())))
        except RolloverControlError:
            raise
        except Exception:
            raise RolloverControlError(
                "Canonical execution-plan generation was rejected."
            ) from None


def sanitized_result(value: TrustedGenerationResult) -> Mapping[str, Any]:
    return MappingProxyType(asdict(value))
