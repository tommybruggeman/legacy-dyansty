from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from gm_assistant.conversation_state import ConversationState
from gm_assistant.draft_intelligence import DraftIntelligenceService
from gm_assistant.interpretation import InterpretedQuestion
from gm_assistant.objective import OwnerObjective
from gm_assistant.player_evaluation import PlayerEvaluationService
from gm_assistant.player_intelligence import PlayerIntelligenceService
from gm_assistant.planning import DecisionPlan, RetrievalRequest
from gm_assistant.repositories import ContractRepository, LeagueRepository, TeamRepository
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.scenario_simulator import ScenarioSimulatorService, parse_scenario_actions


EVIDENCE_VERSION = "gm_evidence_packet.v1"
MAX_EVIDENCE_RECORDS = 40


class EvidenceExecutionError(RuntimeError):
    """Raised when evidence execution cannot be safely scoped."""


class RetrievalStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class EvidenceExecutionStatus(str, Enum):
    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"
    REDUCED = "reduced"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class EvidenceContextRef:
    user_id: str
    league_id: str
    league_team_id: str
    conversation_id: str | None
    current_season: int
    requested_seasons: list[int]


@dataclass(frozen=True)
class EvidenceRetrievalResult:
    retrieval_type: str
    required: bool
    status: str
    record_count: int
    source_name: str | None
    reason: str
    error_code: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class UnresolvedEvidence:
    requirement_type: str
    explanation: str
    blocking: bool
    related_entity_ids: list[str] = field(default_factory=list)
    retrieval_type: str | None = None


@dataclass(frozen=True)
class EvidenceLineage:
    domain: str
    source_name: str
    scope: str
    league_id: str | None = None
    league_team_id: str | None = None
    player_id: str | None = None
    status: str = RetrievalStatus.SUCCESS.value


@dataclass(frozen=True)
class ProviderResult:
    status: str
    records: list[dict[str, Any]] = field(default_factory=list)
    source_name: str | None = None
    error_code: str | None = None
    warning: str | None = None
    lineage: list[EvidenceLineage] = field(default_factory=list)

    @classmethod
    def success(cls, records: list[dict[str, Any]], source_name: str, lineage: list[EvidenceLineage] | None = None) -> "ProviderResult":
        return cls(RetrievalStatus.SUCCESS.value if records else RetrievalStatus.EMPTY.value, records, source_name, lineage=lineage or [])

    @classmethod
    def unavailable(cls, source_name: str, warning: str) -> "ProviderResult":
        return cls(RetrievalStatus.UNAVAILABLE.value, [], source_name, warning=warning)


@dataclass(frozen=True)
class PlayerEvidence:
    player_id: str
    canonical_name: str | None
    position: str | None
    nfl_team: str | None
    age: float | None
    experience: int | None
    status: str | None
    fantasy_team_id: str | None
    is_free_agent: bool | None
    strategic_profile: dict[str, Any]
    league_relative_value: dict[str, Any]
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class TeamEvidence:
    league_team_id: str
    team_name: str | None
    owner_name: str | None
    roster_player_ids: list[str]
    roster_summary: dict[str, Any]
    team_brain_summary: dict[str, Any]
    positional_summary: dict[str, Any]
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class ContractEvidence:
    player_id: str
    league_team_id: str | None
    season: int | None
    salary: float | None
    years_remaining: int | None
    contract_status: str | None
    dead_cap_summary: dict[str, Any]
    contract_terms: dict[str, Any]
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)
    agreement_id: str | None = None
    contract_operational_season: int | None = None
    expiration_season: int | None = None
    historical_obligations: list[dict[str, Any]] = field(default_factory=list)
    future_obligations: list[dict[str, Any]] = field(default_factory=list)
    roster_status: str | None = None
    roster_team_id: str | None = None
    free_agent_publication_status: str | None = None
    lifecycle_classification: str | None = None
    trade_eligibility_status: str | None = None
    trade_legality_status: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CapEvidence:
    league_team_id: str
    season: int
    salary_cap: float | None
    active_salary: float | None
    dead_cap: float | None
    available_cap: float | None
    committed_future_salary: dict[str, Any]
    source_fields: dict[str, Any]
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class DraftPickEvidence:
    canonical_pick_id: str | None
    season: int | None
    round: int | None
    slot: int | None
    original_team_id: str | None
    current_owner_team_id: str | None
    pick_status: str | None
    verified_ownership: bool | None
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class TransactionEvidence:
    transaction_id: str | None
    transaction_type: str | None
    season: int | None
    occurred_at: str | None
    team_ids: list[str]
    player_ids: list[str]
    pick_ids: list[str]
    summary: str | None
    data_sources: list[str]
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class RuleEvidence:
    rule_type: str
    season: int | None
    structured_value: Any
    source_name: str
    source_priority: int
    verified: bool
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class LeagueEvidence:
    league_id: str
    season: int
    league_size: int | None
    scoring_summary: dict[str, Any]
    roster_settings_summary: dict[str, Any]
    draft_settings_summary: dict[str, Any]
    league_status: str | None
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class LineupEvidence:
    league_team_id: str
    season: int
    week: int | None
    starter_player_ids: list[str]
    bench_player_ids: list[str]
    eligible_positions: dict[str, Any]
    injuries: dict[str, Any]
    projections: dict[str, Any]
    data_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class FreeAgentEvidence:
    player_id: str
    canonical_name: str | None
    position: str | None
    availability_verified: bool
    availability_source: str | None
    expected_cost: dict[str, Any]
    player_summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class PlayerEvaluationEvidence:
    league_id: str
    league_team_id: str
    player_id: str
    player_name: str
    position: str | None
    current_contribution_score: float | None
    future_outlook_score: float | None
    league_relative_score: float | None
    contract_efficiency_score: float | None
    contender_value_score: float | None
    rebuild_value_score: float | None
    risk_score: float | None
    neutral_overall_value: float | None
    confidence: float
    status: str
    missing_inputs: list[str]
    explanation: str
    fact_refs: list[str]
    fact_id: str
    data_sources: list[str]
    source_rows_used: list[str] = field(default_factory=list)
    component_sources: dict[str, list[str]] = field(default_factory=dict)
    effective_weights: dict[str, float] = field(default_factory=dict)
    rookie_prospect_pathway_used: bool = False
    positional_adjustment_applied: bool = False
    positional_adjustment_source: str | None = None
    warnings: list[str] = field(default_factory=list)
    lineage: list[EvidenceLineage] = field(default_factory=list)


@dataclass(frozen=True)
class EvidencePacket:
    request_context_ref: EvidenceContextRef
    plan_type: str
    decision_engine: str
    team_evidence: list[TeamEvidence] = field(default_factory=list)
    player_evidence: list[PlayerEvidence] = field(default_factory=list)
    contract_evidence: list[ContractEvidence] = field(default_factory=list)
    cap_evidence: list[CapEvidence] = field(default_factory=list)
    draft_pick_evidence: list[DraftPickEvidence] = field(default_factory=list)
    transaction_evidence: list[TransactionEvidence] = field(default_factory=list)
    rules_evidence: list[RuleEvidence] = field(default_factory=list)
    league_evidence: list[LeagueEvidence] = field(default_factory=list)
    lineup_evidence: list[LineupEvidence] = field(default_factory=list)
    free_agent_evidence: list[FreeAgentEvidence] = field(default_factory=list)
    player_evaluation_evidence: list[PlayerEvaluationEvidence] = field(default_factory=list)
    retrieval_results: list[EvidenceRetrievalResult] = field(default_factory=list)
    unresolved_requirements: list[UnresolvedEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    required_evidence_complete: bool = True
    reduced_mode: bool = False
    execution_status: str = EvidenceExecutionStatus.COMPLETE.value
    evidence_version: str = EVIDENCE_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceRetrievalProvider(Protocol):
    def get_team_roster(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_team_brain(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_team_roster_summary(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_league_brain(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_team_brain_rankings(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_cap_summary(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_draft_picks(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_transactions(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_scenario_simulation(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_player_profiles(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_player_contracts(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_league_settings(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_rule_sources(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_lineup_sources(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_free_agent_sources(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...
    def get_player_evaluations(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult: ...


class SupabaseEvidenceRetrievalProvider:
    def __init__(self, sb: Any):
        self.sb = sb

    @staticmethod
    def _from_repository_result(result: Any) -> ProviderResult:
        if not result.ok:
            return ProviderResult(RetrievalStatus.FAILED.value, [], result.source.source_name, result.error, f"{result.source.domain} retrieval failed.")
        return ProviderResult.success(
            result.rows,
            result.source.source_name,
            [
                EvidenceLineage(
                    domain=result.source.domain,
                    source_name=result.source.source_name,
                    scope=result.source.scope,
                    league_id=result.source.league_id,
                    league_team_id=result.source.league_team_id,
                    player_id=result.source.player_id,
                    status=RetrievalStatus.SUCCESS.value if result.rows else RetrievalStatus.EMPTY.value,
                )
            ],
        )

    def get_team_roster(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        from gm_assistant import retrieval

        rows: list[dict[str, Any]] = []
        for team_id in request.team_ids or [context.league_team_id]:
            result = retrieval.get_team_roster(self.sb, context, league_team_id=team_id)
            if not result.ok:
                return ProviderResult(RetrievalStatus.FAILED.value, [], result.source.table, result.error, "Team roster retrieval failed.")
            rows.extend(result.rows)
        rows = _filter_players(rows, request.player_ids)
        source = "contracts" if rows and all(row.get("sleeper_player_id") for row in rows) else "team_roster_state/player_strategic_profiles"
        return ProviderResult.success(rows, source)

    def get_team_brain(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        from gm_assistant import retrieval

        rows: list[dict[str, Any]] = []
        sources: list[str] = []
        for team_id in request.team_ids or [context.league_team_id]:
            result = retrieval.get_team_brain(self.sb, context, league_team_id=team_id)
            if not result.ok:
                return ProviderResult(RetrievalStatus.FAILED.value, [], result.source.table, result.error, "Team brain retrieval failed.")
            rows.extend(result.rows)
            sources.append(result.source.table)
        source = "/".join(_dedupe_strings(sources)) or "team_brain"
        return ProviderResult.success(rows, source)

    def get_team_roster_summary(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        return self.get_team_roster(context, request)

    def get_league_brain(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        from gm_assistant import retrieval

        result = retrieval.get_league_brain(self.sb, context)
        if not result.ok:
            return ProviderResult(RetrievalStatus.FAILED.value, [], result.source.table, result.error, "League brain retrieval failed.")
        return ProviderResult.success(result.rows, "league_brain")

    def get_team_brain_rankings(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        result = TeamRepository(self.sb).get_team_brain_rankings(context, limit=int(request.filters.get("limit") or 12))
        return self._from_repository_result(result)

    def get_cap_summary(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        from gm_assistant import retrieval

        rows: list[dict[str, Any]] = []
        for team_id in request.team_ids or [context.league_team_id]:
            result = retrieval.get_cap_summary(self.sb, context, league_team_id=team_id)
            if not result.ok:
                return ProviderResult(RetrievalStatus.FAILED.value, [], result.source.table, result.error, "Cap summary retrieval failed.")
            rows.extend(result.rows)
        source = "v_team_caps"
        if rows and any(row.get("source_name") == "computed_cap_summary" for row in rows):
            source = "contracts/cap_adjustments/league_rules"
        return ProviderResult.success(rows, source)

    def get_draft_picks(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        service = DraftIntelligenceService(self.sb)
        rows: list[dict[str, Any]] = []
        lineage: list[EvidenceLineage] = []
        team_ids = request.team_ids or [None]
        seasons = request.seasons or [context.requested_season]
        for season in seasons:
            for team_id in team_ids:
                draft_context = service.get_context(
                    context,
                    season=season,
                    requested_pick_labels=request.pick_ids,
                    team_id=team_id,
                )
                if request.pick_ids:
                    assets = draft_context.requested_picks
                elif team_id:
                    assets = draft_context.owned_picks
                else:
                    assets = service.get_pick_assets(context, season=season)
                rows.extend([asset.to_evidence_row() for asset in assets])
                lineage.extend([
                    EvidenceLineage(
                        domain=item.domain,
                        source_name=item.source_name,
                        scope=item.scope,
                        league_id=item.league_id,
                        league_team_id=item.league_team_id,
                        player_id=item.player_id,
                        status=item.status,
                    )
                    for item in draft_context.lineage
                ])
        return ProviderResult.success(_dedupe_rows(rows), "draft_intelligence", lineage)

    def get_transactions(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        from gm_assistant import retrieval

        result = retrieval.get_transactions(self.sb, context, limit=int(request.filters.get("limit") or 25))
        if not result.ok:
            return ProviderResult(RetrievalStatus.FAILED.value, [], result.source.table, result.error, "Transaction retrieval failed.")
        rows = _filter_players(result.rows, request.player_ids)
        return ProviderResult.success(rows, "transactions_enriched")

    def get_scenario_simulation(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        actions = parse_scenario_actions(request.filters.get("raw_question"))
        if not actions:
            return ProviderResult(RetrievalStatus.BLOCKED.value, [], "scenario_simulator", "scenario_ambiguous", "Scenario request is ambiguous or unsupported by the read-only simulator.")
        result = ScenarioSimulatorService(self.sb).simulate(context, actions)
        lineage = [
            EvidenceLineage(
                domain="scenario_simulation",
                source_name="scenario_simulator",
                scope="team",
                league_id=context.league_id,
                league_team_id=context.league_team_id,
                status=result.status,
            )
        ]
        return ProviderResult.success([result.to_evidence_row()], "scenario_simulator", lineage)

    def get_player_profiles(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        if request.retrieval_type == "prospect_pool":
            season = request.seasons[0] if request.seasons else context.requested_season
            prospects, missing, _linked = DraftIntelligenceService(self.sb).get_prospects(context, season=season)
            if missing:
                return ProviderResult.unavailable("draft_intelligence", "No verified internal rookie prospect source is configured.")
            rows = [_prospect_profile_row(prospect) for prospect in prospects]
            lineage = [
                EvidenceLineage(
                    domain=item.domain,
                    source_name=item.source_name,
                    scope=item.scope,
                    league_id=item.league_id,
                    league_team_id=item.league_team_id,
                    player_id=item.player_id,
                    status=item.status,
                )
                for prospect in prospects
                for item in prospect.lineage
            ]
            return ProviderResult.success(rows, "draft_intelligence", lineage)
        profiles = PlayerIntelligenceService(self.sb).get_profiles(
            context,
            player_ids=request.player_ids,
            include_league_context=True,
        )
        rows = [
            profile.to_evidence_row()
            for profile in profiles
            if profile.identity.player_id and profile.availability not in {"not_found", "ambiguous_identity", "malformed_source_data"}
        ]
        lineage = [
            EvidenceLineage(
                domain=item.domain,
                source_name=item.source_name,
                scope=item.scope,
                league_id=item.league_id,
                league_team_id=item.league_team_id,
                player_id=item.player_id,
                status=item.status,
            )
            for profile in profiles
            for item in profile.lineage
        ]
        return ProviderResult.success(rows, "player_intelligence_profile", lineage)

    def get_player_contracts(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        team_ids = request.team_ids or ([context.league_team_id] if request.scope == "team" or request.retrieval_type == "team_contracts" else [])
        years_left = _safe_int((request.filters or {}).get("contract_years_left"))
        result = ContractRepository(self.sb).get_contracts(
            context,
            league_team_ids=team_ids or None,
            player_ids=request.player_ids,
            contract_years_left=years_left,
        )
        return self._from_repository_result(result)

    def get_league_settings(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        result = LeagueRepository(self.sb).get_league_settings(context)
        return self._from_repository_result(result)

    def get_rule_sources(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        repo_result = LeagueRepository(self.sb).get_rule_sources(context)
        rows = repo_result.rows
        if not rows:
            return ProviderResult.unavailable("league_rules", "No trusted structured rule source is available.")
        return ProviderResult.success(
            _normalize_league_rule_rows(rows, context, request),
            "league_rules",
            self._from_repository_result(repo_result).lineage,
        )

    def get_lineup_sources(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        if request.retrieval_type in {"injury_status", "weekly_projection_summary"}:
            return ProviderResult.unavailable(request.retrieval_type, f"{request.retrieval_type} source is unavailable.")
        rows = _safe_rows(
            self.sb.table("team_roster_state")
            .select("*")
            .eq("league_id", context.league_id)
            .eq("team_id", context.league_team_id)
        )
        return ProviderResult.success(rows, "team_roster_state")

    def get_free_agent_sources(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        rows = _safe_rows(self.sb.table("free_agents").select("*").eq("league_id", context.league_id))
        if not rows:
            return ProviderResult.unavailable("free_agents", "Trusted free-agent availability source is unavailable.")
        return ProviderResult.success(rows, "free_agents")

    def get_player_evaluations(self, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
        team_id = (request.team_ids or [context.league_team_id])[0]
        evaluations = PlayerEvaluationService(self.sb).evaluate_roster(context, league_team_id=team_id)
        rows_out = [item.to_evidence_row() for item in evaluations]
        lineage = [
            EvidenceLineage(
                domain="player_evaluation",
                source_name="player_strategic_profiles/league_relative_player_values/contracts/team_roster",
                scope="team",
                league_id=context.league_id,
                league_team_id=team_id,
                status=RetrievalStatus.SUCCESS.value if rows_out else RetrievalStatus.EMPTY.value,
            )
        ]
        return ProviderResult.success(rows_out, "player_evaluation_engine", lineage)


def build_evidence_packet(
    *,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
    decision_plan: DecisionPlan,
    retrieval_provider: EvidenceRetrievalProvider,
) -> EvidencePacket:
    warnings: list[str] = []
    unresolved: list[UnresolvedEvidence] = []
    results: list[EvidenceRetrievalResult] = []
    team_evidence: list[TeamEvidence] = []
    player_evidence: list[PlayerEvidence] = []
    contract_evidence: list[ContractEvidence] = []
    cap_evidence: list[CapEvidence] = []
    draft_pick_evidence: list[DraftPickEvidence] = []
    transaction_evidence: list[TransactionEvidence] = []
    rules_evidence: list[RuleEvidence] = []
    league_evidence: list[LeagueEvidence] = []
    lineup_evidence: list[LineupEvidence] = []
    free_agent_evidence: list[FreeAgentEvidence] = []
    player_evaluation_evidence: list[PlayerEvaluationEvidence] = []

    try:
        _validate_upstream_scope(context, conversation_state, interpreted_question, owner_objective, decision_plan)
    except EvidenceExecutionError as exc:
        unresolved.append(UnresolvedEvidence("scope_validation", str(exc), True, retrieval_type=None))
        return _packet(context, decision_plan, results, unresolved, warnings, execution_status=EvidenceExecutionStatus.BLOCKED.value)

    if decision_plan.blockers or not decision_plan.ready_for_execution:
        for blocker in decision_plan.blockers:
            unresolved.append(UnresolvedEvidence(blocker.blocker_type, blocker.explanation, True, retrieval_type=None))
        return _packet(context, decision_plan, results, unresolved, warnings, execution_status=EvidenceExecutionStatus.BLOCKED.value)

    if decision_plan.plan_type == "unsupported_plan":
        unresolved.append(UnresolvedEvidence("unsupported_plan", "Unsupported requests do not execute evidence retrieval.", True, retrieval_type=None))
        return _packet(context, decision_plan, results, unresolved, warnings, execution_status=EvidenceExecutionStatus.BLOCKED.value)

    for request in _dedupe_requests(decision_plan.retrieval_requests):
        if not _request_scope_matches_context(context, request):
            result = EvidenceRetrievalResult(request.retrieval_type, request.required, RetrievalStatus.BLOCKED.value, 0, None, request.reason, "scope_mismatch", "Requested evidence is outside the active context.")
            results.append(result)
            unresolved.append(UnresolvedEvidence("scope_mismatch", "Requested evidence is outside the active context.", request.required, _request_entity_ids(request), request.retrieval_type))
            continue

        provider_result = _execute_request(retrieval_provider, context, request)
        result = EvidenceRetrievalResult(
            retrieval_type=request.retrieval_type,
            required=request.required,
            status=provider_result.status,
            record_count=len(provider_result.records),
            source_name=provider_result.source_name,
            reason=request.reason,
            error_code=provider_result.error_code,
            warning=provider_result.warning,
        )
        results.append(result)
        if provider_result.warning:
            warnings.append(provider_result.warning)

        if provider_result.status in {RetrievalStatus.FAILED.value, RetrievalStatus.BLOCKED.value}:
            unresolved.append(UnresolvedEvidence(provider_result.error_code or request.retrieval_type, provider_result.warning or "Evidence retrieval failed.", request.required, _request_entity_ids(request), request.retrieval_type))
            continue
        if provider_result.status == RetrievalStatus.UNAVAILABLE.value:
            if request.required and request.retrieval_type not in _OPTIONAL_BY_NATURE:
                unresolved.append(UnresolvedEvidence("evidence_unavailable", provider_result.warning or "Required evidence source is unavailable.", True, _request_entity_ids(request), request.retrieval_type))
            continue

        normalized = _normalize_records(request, provider_result)
        team_evidence.extend(normalized["teams"])
        player_evidence.extend(normalized["players"])
        contract_evidence.extend(normalized["contracts"])
        cap_evidence.extend(normalized["caps"])
        draft_pick_evidence.extend(normalized["draft_picks"])
        transaction_evidence.extend(normalized["transactions"])
        rules_evidence.extend(normalized["rules"])
        league_evidence.extend(normalized["leagues"])
        lineup_evidence.extend(normalized["lineups"])
        free_agent_evidence.extend(normalized["free_agents"])
        player_evaluation_evidence.extend(normalized["player_evaluations"])
        warnings.extend(normalized["warnings"])

    return _packet(
        context,
        decision_plan,
        results,
        unresolved,
        warnings,
        team_evidence=_merge_team_evidence(team_evidence),
        player_evidence=_dedupe_dataclasses(player_evidence, lambda item: item.player_id),
        contract_evidence=_dedupe_dataclasses(contract_evidence, lambda item: (item.player_id, item.season, item.league_team_id)),
        cap_evidence=_dedupe_dataclasses(cap_evidence, lambda item: (item.league_team_id, item.season)),
        draft_pick_evidence=_dedupe_dataclasses(draft_pick_evidence, lambda item: (item.canonical_pick_id, item.season, item.round, item.slot, item.current_owner_team_id)),
        transaction_evidence=_dedupe_dataclasses(transaction_evidence, lambda item: item.transaction_id or (item.summary, item.occurred_at)),
        rules_evidence=_dedupe_dataclasses(rules_evidence, lambda item: (item.rule_type, item.season, item.source_name)),
        league_evidence=_dedupe_dataclasses(league_evidence, lambda item: (item.league_id, item.season)),
        lineup_evidence=_dedupe_dataclasses(lineup_evidence, lambda item: (item.league_team_id, item.season, item.week)),
        free_agent_evidence=_dedupe_dataclasses(free_agent_evidence, lambda item: item.player_id),
        player_evaluation_evidence=_dedupe_dataclasses(player_evaluation_evidence, lambda item: item.player_id),
    )


def build_evidence_packet_payload(evidence_packet: EvidencePacket | None) -> dict[str, Any]:
    if not evidence_packet:
        return {}
    payload = evidence_packet.to_payload()
    payload.pop("evidence_version", None)
    return _compact(payload)


_RETRIEVAL_METHODS = {
    "current_user_context": "get_team_brain",
    "team_roster": "get_team_roster",
    "eligible_roster_players": "get_lineup_sources",
    "team_roster_summary": "get_team_roster_summary",
    "league_rosters": "get_team_roster_summary",
    "team_needs": "get_team_brain",
    "team_contracts": "get_player_contracts",
    "team_brain": "get_team_brain",
    "team_brain_rankings": "get_team_brain_rankings",
    "league_brain": "get_league_brain",
    "league_rules": "get_rule_sources",
    "league_settings": "get_league_settings",
    "roster_rules": "get_rule_sources",
    "lineup_rules": "get_rule_sources",
    "league_acquisition_rules": "get_rule_sources",
    "rookie_contract_rules": "get_rule_sources",
    "player_profile": "get_player_profiles",
    "player_roster_status": "get_team_roster",
    "player_contract": "get_player_contracts",
    "league_relative_value": "get_player_profiles",
    "asset_ownership": "get_team_roster_summary",
    "target_owner": "get_team_roster_summary",
    "counterparty_context": "get_team_brain",
    "cap_summary": "get_cap_summary",
    "cap_summaries": "get_cap_summary",
    "draft_picks": "get_draft_picks",
    "draft_pick": "get_draft_picks",
    "draft_order": "get_draft_picks",
    "prospect_pool": "get_player_profiles",
    "projected_team_strength": "get_team_brain_rankings",
    "historical_pick_value": "get_draft_picks",
    "recent_transactions": "get_transactions",
    "transaction_history": "get_transactions",
    "scenario_simulation": "get_scenario_simulation",
    "free_agent_pool": "get_free_agent_sources",
    "injury_status": "get_lineup_sources",
    "weekly_projection_summary": "get_lineup_sources",
    "player_evaluations": "get_player_evaluations",
}

_OPTIONAL_BY_NATURE = {
    "injury_status",
    "weekly_projection_summary",
    "projected_team_strength",
    "historical_pick_value",
    "counterparty_context",
    "rookie_contract_rules",
    "cap_summaries",
}


def _execute_request(provider: EvidenceRetrievalProvider, context: AssistantRequestContext, request: RetrievalRequest) -> ProviderResult:
    method_name = _RETRIEVAL_METHODS.get(request.retrieval_type)
    if not method_name:
        return ProviderResult(RetrievalStatus.UNAVAILABLE.value, [], "evidence_provider", "unsupported_retrieval", f"No provider method is registered for {request.retrieval_type}.")
    try:
        method = getattr(provider, method_name)
        return method(context, request)
    except Exception:
        return ProviderResult(RetrievalStatus.FAILED.value, [], method_name, "provider_exception", "Evidence provider failed safely.")


def _safe_rows(query: Any) -> list[dict[str, Any]]:
    return query.execute().data or []


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for row in rows:
        key = tuple(sorted((key, str(value)) for key, value in row.items() if key in {"canonical_pick_id", "season", "round", "slot", "current_owner_team_id", "resolved_current_owner_team_id"}))
        if key not in seen:
            out.append(row)
            seen.add(key)
    return out


def _prospect_profile_row(prospect: Any) -> dict[str, Any]:
    row = {
        "player_id": prospect.prospect_id,
        "sleeper_id": prospect.sleeper_id,
        "player_name": prospect.player_name,
        "position": prospect.position,
        "college": prospect.college,
        "age": prospect.age,
        "rookie_status": prospect.rookie_status,
        "rookie_class_year": prospect.draft_class,
        "rookie_rank": prospect.stored_ranking,
        "rookie_tier": prospect.stored_tier,
        "availability": prospect.availability_state,
        "profile_completeness": prospect.completeness,
    }
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _scoped_player_rows(sb: Any, table_name: str, context: AssistantRequestContext, player_ids: list[str]) -> list[dict[str, Any]]:
    query = sb.table(table_name).select("*").eq("league_id", context.league_id)
    rows = _safe_rows(query)
    return _filter_players(rows, player_ids)


def _filter_contracts_to_requested_team(
    sb: Any,
    context: AssistantRequestContext,
    request: RetrievalRequest,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    team_ids = request.team_ids or ([context.league_team_id] if request.scope == "team" or request.retrieval_type == "team_contracts" else [])
    if not team_ids:
        return rows
    teams = [_load_team_for_evidence(sb, context, team_id) for team_id in team_ids]
    teams = [team for team in teams if team]
    allowed_ids = {str(team.get("id")) for team in teams if team.get("id")}
    allowed_names = {
        str(value).strip().lower()
        for team in teams
        for value in (team.get("owner_name"), team.get("team_name"))
        if value is not None and str(value).strip()
    }
    out = []
    for row in rows:
        row_team_id = _clean_id(row.get("league_team_id") or row.get("team_id"))
        row_owner = _clean_text(row.get("owner_name") or row.get("team_name") or row.get("owner"))
        if row_team_id and row_team_id in allowed_ids:
            out.append(row)
        elif row_owner and row_owner.lower() in allowed_names:
            out.append(row)
    return out


def _filter_contracts_by_request(rows: list[dict[str, Any]], request: RetrievalRequest) -> list[dict[str, Any]]:
    years_left = _safe_int((request.filters or {}).get("contract_years_left"))
    if years_left is None:
        return rows
    return [
        row for row in rows
        if _safe_int(row.get("contract_years_left") or row.get("years_remaining")) == years_left
    ]


def _load_team_for_evidence(sb: Any, context: AssistantRequestContext, team_id: str | None) -> dict[str, Any] | None:
    if not team_id:
        return None
    try:
        rows = _safe_rows(
            sb.table("league_teams")
            .select("id,league_id,team_name,owner_name")
            .eq("id", team_id)
            .eq("league_id", context.league_id)
            .limit(1)
        )
    except Exception:
        return None
    return rows[0] if rows else None


def _contract_row_with_scope(row: dict[str, Any], context: AssistantRequestContext) -> dict[str, Any]:
    out = dict(row)
    if not out.get("season"):
        out["season"] = context.current_season
    team_id = _clean_id(out.get("league_team_id") or out.get("team_id"))
    if not team_id and _clean_text(out.get("owner_name")) == _clean_text(context.owner_name):
        team_id = context.league_team_id
    if team_id:
        out["league_team_id"] = team_id
        out["team_id"] = team_id
    if not out.get("status"):
        out["status"] = "active"
    return out


def _normalize_league_rule_rows(rows: list[dict[str, Any]], context: AssistantRequestContext, request: RetrievalRequest) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        season = _safe_int(row.get("season")) or (request.seasons[0] if request.seasons else context.current_season)
        if row.get("structured_value") is not None or row.get("rule_type") or row.get("key"):
            normalized.append(row)
            continue
        league_id = row.get("league_id") or context.league_id
        normalized.extend([
            {
                "league_id": league_id,
                "rule_type": "salary_cap_legality",
                "season": season,
                "structured_value": {
                    "salary_cap": row.get("salary_cap"),
                    "must_remain_under_cap": True,
                    "drop_dead_cap_multiplier": row.get("drop_dead_cap_multiplier"),
                    "default_dead_cap_pct": row.get("default_dead_cap_pct"),
                },
                "source_priority": 1,
                "verified": row.get("salary_cap") is not None,
            },
            {
                "league_id": league_id,
                "rule_type": "taxi_eligibility",
                "season": season,
                "structured_value": {
                    "taxi_limit": row.get("taxi_limit"),
                    "rookie_draft_required": True,
                    "rookie_draft_only": True,
                    "rookie_scale_enabled": row.get("rookie_scale_enabled"),
                },
                "source_priority": 1,
                "verified": row.get("taxi_limit") is not None,
            },
            {
                "league_id": league_id,
                "rule_type": "roster_size_limit",
                "season": season,
                "structured_value": {
                    "roster_limit": row.get("roster_limit"),
                    "ir_limit": row.get("ir_limit"),
                },
                "source_priority": 1,
                "verified": row.get("roster_limit") is not None,
            },
        ])
    wanted = {rule.rule_type for rule in request.filters.get("rule_requests", [])} if request.filters.get("rule_requests") else set()
    if wanted:
        return [row for row in normalized if row.get("rule_type") in wanted]
    return normalized


def _filter_players(rows: list[dict[str, Any]], player_ids: list[str]) -> list[dict[str, Any]]:
    if not player_ids:
        return rows
    allowed = set(player_ids)
    return [
        row for row in rows
        if _clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id")) in allowed
    ]


def _pick_label_from_row(row: dict[str, Any]) -> str | None:
    labels = _pick_labels_from_row(row)
    return next(iter(labels), None) if labels else None


def _pick_labels_from_row(row: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    explicit = _clean_text(row.get("pick_label") or row.get("label"))
    if explicit:
        normalized = _normalize_pick_label(explicit)
        if normalized:
            labels.add(normalized)
    season = _safe_int(row.get("season"))
    round_number = _safe_int(row.get("round"))
    slot = _safe_int(row.get("slot") or row.get("pick") or row.get("original_pick_rank"))
    if season and round_number and slot:
        labels.add(f"{season}_{round_number}.{slot:02d}")
    if round_number and slot:
        labels.add(f"{round_number}.{slot:02d}")
    if season and round_number:
        labels.add(f"{season}_round_{round_number}")
    return labels


def _normalize_pick_label(value: str) -> str | None:
    text = str(value or "").strip().lower()
    match = re.search(r"\b([1-9])\.(0?[1-9]|1[0-9]|2[0-9])\b", text)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2)):02d}"
    match = re.search(r"\bround\s+([1-9]).{0,12}pick\s+([1-9]|1[0-9]|2[0-9])\b", text)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2)):02d}"
    return None


def _validate_upstream_scope(
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
    decision_plan: DecisionPlan,
) -> None:
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise EvidenceExecutionError("Evidence execution requires authenticated user, league, and team scope.")
    if conversation_state:
        if conversation_state.user_id != context.user_id:
            raise EvidenceExecutionError("Conversation state user scope does not match request context.")
        if conversation_state.league_id != context.league_id:
            raise EvidenceExecutionError("Conversation state league scope does not match request context.")
        if conversation_state.league_team_id != context.league_team_id:
            raise EvidenceExecutionError("Conversation state team scope does not match request context.")
        if context.conversation_id and conversation_state.conversation_id != context.conversation_id:
            raise EvidenceExecutionError("Conversation state conversation id does not match request context.")
    interpreted_league_id = getattr(interpreted_question, "league_id", None)
    if interpreted_league_id and interpreted_league_id != context.league_id:
        raise EvidenceExecutionError("Interpreted question league scope does not match request context.")
    _ = owner_objective
    for request in decision_plan.retrieval_requests:
        if request.filters.get("league_id") and request.filters.get("league_id") != context.league_id:
            raise EvidenceExecutionError("Decision plan league filter does not match request context.")


def _request_scope_matches_context(context: AssistantRequestContext, request: RetrievalRequest) -> bool:
    if request.filters.get("league_id") and request.filters["league_id"] != context.league_id:
        return False
    if request.scope == "team":
        team_ids = request.team_ids or [context.league_team_id]
        return all(team_id == context.league_team_id for team_id in team_ids)
    if request.filters.get("league_team_id") and request.filters["league_team_id"] != context.league_team_id:
        return False
    return True


def _normalize_records(request: RetrievalRequest, result: ProviderResult) -> dict[str, list[Any]]:
    out = {
        "teams": [],
        "players": [],
        "contracts": [],
        "caps": [],
        "draft_picks": [],
        "transactions": [],
        "rules": [],
        "leagues": [],
        "lineups": [],
        "free_agents": [],
        "player_evaluations": [],
        "warnings": [],
    }
    lineage = result.lineage or [
        EvidenceLineage(
            domain=request.retrieval_type,
            source_name=result.source_name or "unknown",
            scope=request.scope,
            status=result.status,
        )
    ]
    for row in result.records[:MAX_EVIDENCE_RECORDS]:
        if request.retrieval_type in {"team_roster", "team_roster_summary", "league_rosters", "asset_ownership", "target_owner", "current_user_context"}:
            out["teams"].append(_team_evidence(row, result.source_name, lineage=lineage))
            player = _player_evidence(row, result.source_name, lineage=lineage)
            if player:
                out["players"].append(player)
        elif request.retrieval_type in {"team_brain", "team_needs", "counterparty_context", "team_brain_rankings"}:
            out["teams"].append(_team_evidence(row, result.source_name, brain_only=True, lineage=lineage))
        elif request.retrieval_type in {"player_profile", "league_relative_value", "prospect_pool", "player_roster_status"}:
            player = _player_evidence(row, result.source_name, lineage=lineage)
            if player:
                out["players"].append(player)
            else:
                out["warnings"].append(f"Malformed player evidence skipped for {request.retrieval_type}.")
        elif request.retrieval_type in {"player_contract", "team_contracts"}:
            contract = _contract_evidence(row, result.source_name, lineage=lineage)
            if contract:
                out["contracts"].append(contract)
            else:
                out["warnings"].append("Malformed contract evidence skipped.")
        elif request.retrieval_type in {"cap_summary", "cap_summaries"}:
            cap = _cap_evidence(row, request, result.source_name, lineage=lineage)
            out["caps"].append(cap)
            out["warnings"].extend(cap.warnings)
        elif request.retrieval_type in {"draft_picks", "draft_pick", "draft_order", "historical_pick_value"}:
            out["draft_picks"].append(_draft_pick_evidence(row, result.source_name, lineage=lineage))
        elif request.retrieval_type in {"recent_transactions", "transaction_history", "scenario_simulation"}:
            out["transactions"].append(_transaction_evidence(row, result.source_name, lineage=lineage))
        elif "rules" in request.retrieval_type or request.retrieval_type in {"league_acquisition_rules", "rookie_contract_rules"}:
            out["rules"].append(_rule_evidence(row, request, result.source_name, lineage=lineage))
        elif request.retrieval_type in {"league_brain", "league_settings"}:
            out["leagues"].append(_league_evidence(row, request, result.source_name, lineage=lineage))
        elif request.retrieval_type in {"eligible_roster_players", "lineup_rules", "injury_status", "weekly_projection_summary"}:
            lineup = _lineup_evidence(row, request, result.source_name, lineage=lineage)
            out["lineups"].append(lineup)
            out["warnings"].extend(lineup.warnings)
        elif request.retrieval_type == "free_agent_pool":
            free_agent = _free_agent_evidence(row, result.source_name, lineage=lineage)
            if free_agent:
                out["free_agents"].append(free_agent)
        elif request.retrieval_type == "player_evaluations":
            evaluation = _player_evaluation_evidence(row, result.source_name, lineage=lineage)
            if evaluation:
                out["player_evaluations"].append(evaluation)
    if len(result.records) > MAX_EVIDENCE_RECORDS:
        out["warnings"].append(f"{request.retrieval_type} evidence capped to {MAX_EVIDENCE_RECORDS} records.")
    return out


def _team_evidence(row: dict[str, Any], source_name: str | None, *, brain_only: bool = False, lineage: list[EvidenceLineage] | None = None) -> TeamEvidence:
    player_ids = _list_values(row.get("roster_player_ids") or row.get("player_ids") or row.get("sleeper_ids"))
    player_id = _clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id"))
    if player_id and player_id not in player_ids and not brain_only:
        player_ids.append(player_id)
    return TeamEvidence(
        league_team_id=_clean_id(row.get("league_team_id") or row.get("team_id")) or "unknown",
        team_name=_clean_text(row.get("team_name") or row.get("owner_team_name")),
        owner_name=_clean_text(row.get("owner_name") or row.get("owner_team_name")),
        roster_player_ids=player_ids,
        roster_summary=_compact_mapping(row, {"league_id", "league_team_id", "team_id", "team_name", "owner_name", "owner_team_name", "league_name", "player_name", "sleeper_id"}),
        team_brain_summary=_compact_mapping(row, {"league_name", "team_direction", "position_strengths", "position_needs", "core_players", "contract_problems", "championship_window_score"}),
        positional_summary=_compact_mapping(row, {"position_strengths", "position_needs", "positional_summary"}),
        data_sources=[source_name or "unknown"],
        lineage=lineage or [],
    )


def _player_evidence(row: dict[str, Any], source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> PlayerEvidence | None:
    player_id = _clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id"))
    if not player_id:
        return None
    return PlayerEvidence(
        player_id=player_id,
        canonical_name=_clean_text(row.get("player_name") or row.get("name")),
        position=_clean_text(row.get("position") or row.get("player_position") or row.get("pos")),
        nfl_team=_clean_text(row.get("nfl_team") or row.get("team")),
        age=_safe_float(row.get("age")),
        experience=_safe_int(row.get("experience") or row.get("years_exp")),
        status=_clean_text(row.get("status") or row.get("contract_flag")),
        fantasy_team_id=_clean_id(row.get("league_team_id") or row.get("team_id")),
        is_free_agent=None,
        strategic_profile=_compact_mapping(row, {"strategic_label", "priority", "role", "risk", "window_fit", "contract_flag", "is_rookie", "rookie_draft_selected"}),
        league_relative_value=_compact_mapping(row, {"league_value_tier", "overall_percentile", "position_percentile", "rank", "value_score"}),
        data_sources=[source_name or "unknown"],
        lineage=lineage or [],
    )


def _contract_evidence(row: dict[str, Any], source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> ContractEvidence | None:
    player_id = _clean_id(row.get("sleeper_player_id") or row.get("sleeper_id") or row.get("player_id"))
    if not player_id:
        return None
    return ContractEvidence(
        player_id=player_id,
        league_team_id=_clean_id(row.get("league_team_id") or row.get("team_id")),
        season=_safe_int(row.get("season")),
        salary=_safe_float(row.get("salary") or row.get("contract_salary")),
        years_remaining=_safe_int(row.get("contract_years_left") or row.get("years_remaining")),
        contract_status=_clean_text(row.get("contract_status") or row.get("status")),
        dead_cap_summary=_compact_mapping(row, {"dead_cap", "dead_cap_current", "dead_cap_future"}),
        contract_terms=_compact_mapping(row, {"player_name", "player_position", "owner_name", "owner_team_name", "salary", "contract_years_left", "contract_total_years", "contract_type", "franchise_tag", "is_rookie", "season"}),
        data_sources=[source_name or "unknown"],
        warnings=list(row.get("warnings") or ()),
        lineage=lineage or [],
        agreement_id=_clean_id(row.get("agreement_id")),
        contract_operational_season=_safe_int(row.get("contract_operational_season") or row.get("season")),
        expiration_season=_safe_int(row.get("expiration_season")),
        historical_obligations=list(row.get("historical_obligations") or []),
        future_obligations=list(row.get("future_obligations") or []),
        roster_status=_clean_text(row.get("roster_status")),
        roster_team_id=_clean_id(row.get("roster_team_id")),
        free_agent_publication_status=_clean_text(row.get("free_agent_publication_status")),
        lifecycle_classification=_clean_text(row.get("lifecycle_classification")),
        trade_eligibility_status=_clean_text(row.get("trade_eligibility_status")),
        trade_legality_status=_clean_text(row.get("trade_legality_status")),
        provenance=dict(row.get("provenance") or {}),
        uncertainty=list(row.get("uncertainty") or []),
    )


def _cap_evidence(row: dict[str, Any], request: RetrievalRequest, source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> CapEvidence:
    salary_cap = _safe_float(row.get("salary_cap") or row.get("cap_limit"))
    active_salary = _safe_float(row.get("active_salary") or row.get("total_salary"))
    dead_cap = _safe_float(row.get("dead_cap") or row.get("dead_money"))
    adjustment_total = _safe_float(row.get("adjustment_total"))
    available_cap = _safe_float(row.get("available_cap") or row.get("cap_space"))
    warnings = []
    if salary_cap is not None and active_salary is not None and dead_cap is not None and available_cap is not None:
        charge_total = adjustment_total if adjustment_total is not None else dead_cap
        if abs((salary_cap - active_salary - charge_total) - available_cap) > 1:
            warnings.append("cap_total_fields_are_inconsistent")
    return CapEvidence(
        league_team_id=_clean_id(row.get("league_team_id") or row.get("team_id")) or (request.team_ids[0] if request.team_ids else "unknown"),
        season=_safe_int(row.get("season")) or (request.seasons[0] if request.seasons else 0),
        salary_cap=salary_cap,
        active_salary=active_salary,
        dead_cap=dead_cap,
        available_cap=available_cap,
        committed_future_salary=_compact_mapping(row, {"future_salary", "committed_future_salary"}),
        source_fields=_compact_mapping(row, {"salary_cap", "cap_limit", "active_salary", "total_salary", "dead_cap", "dead_money", "adjustment_total", "cap_used", "available_cap", "cap_space"}),
        data_sources=[source_name or "unknown"],
        warnings=warnings,
        lineage=lineage or [],
    )


def _draft_pick_evidence(row: dict[str, Any], source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> DraftPickEvidence:
    current_owner = _clean_id(row.get("current_owner_team_id") or row.get("league_team_id") or row.get("team_id") or row.get("resolved_current_owner_team_id"))
    if not current_owner:
        current_owner = _clean_id(row.get("current_owner"))
    original_team = _clean_id(row.get("original_team_id") or row.get("resolved_original_team_id") or row.get("original_team"))
    return DraftPickEvidence(
        canonical_pick_id=_clean_id(row.get("canonical_pick_id") or row.get("pick_id") or row.get("id")),
        season=_safe_int(row.get("season")),
        round=_safe_int(row.get("round")),
        slot=_safe_int(row.get("slot") or row.get("pick") or row.get("original_pick_rank")),
        original_team_id=original_team,
        current_owner_team_id=current_owner,
        pick_status=_clean_text(row.get("status") or row.get("pick_status")),
        verified_ownership=bool(current_owner),
        data_sources=[source_name or "unknown"],
        lineage=lineage or [],
    )


def _transaction_evidence(row: dict[str, Any], source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> TransactionEvidence:
    return TransactionEvidence(
        transaction_id=_clean_id(row.get("transaction_id") or row.get("id")),
        transaction_type=_clean_text(row.get("transaction_type") or row.get("type")),
        season=_safe_int(row.get("season")),
        occurred_at=_clean_text(row.get("occurred_at") or row.get("created_at") or row.get("date")),
        team_ids=_list_values(row.get("team_ids") or row.get("league_team_ids") or row.get("teams")),
        player_ids=_list_values(row.get("player_ids") or row.get("sleeper_ids")),
        pick_ids=_list_values(row.get("pick_ids")),
        summary=_clean_text(row.get("summary") or row.get("description")),
        data_sources=[source_name or "unknown"],
        lineage=lineage or [],
    )


def _rule_evidence(row: dict[str, Any], request: RetrievalRequest, source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> RuleEvidence:
    return RuleEvidence(
        rule_type=_clean_text(row.get("rule_type") or row.get("key")) or request.retrieval_type,
        season=_safe_int(row.get("season")) or (request.seasons[0] if request.seasons else None),
        structured_value=row.get("structured_value") if "structured_value" in row else row.get("value"),
        source_name=source_name or _clean_text(row.get("source_name")) or "rule_source",
        source_priority=_safe_int(row.get("source_priority")) or 100,
        verified=bool(row.get("verified") is True or row.get("structured_value") is not None or row.get("value") is not None),
        warnings=[] if (row.get("structured_value") is not None or row.get("value") is not None) else ["rule_source_has_no_structured_value"],
        lineage=lineage or [],
    )


def _league_evidence(row: dict[str, Any], request: RetrievalRequest, source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> LeagueEvidence:
    return LeagueEvidence(
        league_id=_clean_id(row.get("league_id")) or request.filters.get("league_id") or "unknown",
        season=_safe_int(row.get("season")) or (request.seasons[0] if request.seasons else 0),
        league_size=_safe_int(row.get("league_size") or row.get("num_teams")),
        scoring_summary=_compact_mapping(row, {"scoring_summary", "scoring_settings"}),
        roster_settings_summary=_compact_mapping(row, {"roster_settings_summary", "roster_settings"}),
        draft_settings_summary=_compact_mapping(row, {"draft_settings_summary", "draft_settings"}),
        league_status=_clean_text(row.get("league_status") or row.get("status")),
        data_sources=[source_name or "unknown"],
        lineage=lineage or [],
    )


def _lineup_evidence(row: dict[str, Any], request: RetrievalRequest, source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> LineupEvidence:
    warnings = []
    if request.retrieval_type == "weekly_projection_summary" and not row.get("projections"):
        warnings.append("projections_unavailable")
    if request.retrieval_type == "injury_status" and not row.get("injuries"):
        warnings.append("injuries_unavailable")
    return LineupEvidence(
        league_team_id=_clean_id(row.get("league_team_id") or row.get("team_id")) or (request.team_ids[0] if request.team_ids else "unknown"),
        season=_safe_int(row.get("season")) or (request.seasons[0] if request.seasons else 0),
        week=_safe_int(row.get("week")),
        starter_player_ids=_list_values(row.get("starter_player_ids") or row.get("starters")),
        bench_player_ids=_list_values(row.get("bench_player_ids") or row.get("bench")),
        eligible_positions=row.get("eligible_positions") if isinstance(row.get("eligible_positions"), dict) else {},
        injuries=row.get("injuries") if isinstance(row.get("injuries"), dict) else {},
        projections=row.get("projections") if isinstance(row.get("projections"), dict) else {},
        data_sources=[source_name or "unknown"],
        warnings=warnings,
        lineage=lineage or [],
    )


def _free_agent_evidence(row: dict[str, Any], source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> FreeAgentEvidence | None:
    player_id = _clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id"))
    verified = bool(row.get("availability_verified") is True or row.get("is_free_agent") is True)
    if not player_id or not verified:
        return None
    return FreeAgentEvidence(
        player_id=player_id,
        canonical_name=_clean_text(row.get("player_name") or row.get("name")),
        position=_clean_text(row.get("position")),
        availability_verified=verified,
        availability_source=source_name or _clean_text(row.get("availability_source")),
        expected_cost=_compact_mapping(row, {"expected_cost", "faab_cost", "salary"}),
        player_summary=_compact_mapping(row, {"player_name", "position", "age", "status"}),
        lineage=lineage or [],
    )


def _player_evaluation_evidence(row: dict[str, Any], source_name: str | None, *, lineage: list[EvidenceLineage] | None = None) -> PlayerEvaluationEvidence | None:
    player_id = _clean_id(row.get("player_id") or row.get("sleeper_id") or row.get("sleeper_player_id"))
    if not player_id:
        return None
    fact_refs = _list_values(row.get("fact_refs"))
    fact_id = _clean_text(row.get("fact_id")) or f"player_eval.{row.get('league_id')}.{row.get('league_team_id')}.{player_id}.derived.neutral_overall_value"
    if fact_id not in fact_refs:
        fact_refs.insert(0, fact_id)
    return PlayerEvaluationEvidence(
        league_id=_clean_id(row.get("league_id")) or "",
        league_team_id=_clean_id(row.get("league_team_id")) or "",
        player_id=player_id,
        player_name=_clean_text(row.get("player_name")) or player_id,
        position=_clean_text(row.get("position")),
        current_contribution_score=_safe_float(row.get("current_contribution_score")),
        future_outlook_score=_safe_float(row.get("future_outlook_score")),
        league_relative_score=_safe_float(row.get("league_relative_score")),
        contract_efficiency_score=_safe_float(row.get("contract_efficiency_score")),
        contender_value_score=_safe_float(row.get("contender_value_score")),
        rebuild_value_score=_safe_float(row.get("rebuild_value_score")),
        risk_score=_safe_float(row.get("risk_score")),
        neutral_overall_value=_safe_float(row.get("neutral_overall_value")),
        confidence=_safe_float(row.get("confidence")) or 0.0,
        status=_clean_text(row.get("status")) or "evaluated",
        missing_inputs=_list_values(row.get("missing_inputs")),
        explanation=_clean_text(row.get("explanation")) or "",
        fact_refs=fact_refs,
        fact_id=fact_id,
        data_sources=[source_name or "player_evaluation_engine"],
        source_rows_used=_list_values(row.get("source_rows_used")),
        component_sources=row.get("component_sources") if isinstance(row.get("component_sources"), dict) else {},
        effective_weights=row.get("effective_weights") if isinstance(row.get("effective_weights"), dict) else {},
        rookie_prospect_pathway_used=bool(row.get("rookie_prospect_pathway_used")),
        positional_adjustment_applied=bool(row.get("positional_adjustment_applied")),
        positional_adjustment_source=_clean_text(row.get("positional_adjustment_source")),
        warnings=_list_values(row.get("warnings")),
        lineage=lineage or [],
    )


def _packet(
    context: AssistantRequestContext,
    plan: DecisionPlan,
    results: list[EvidenceRetrievalResult],
    unresolved: list[UnresolvedEvidence],
    warnings: list[str],
    *,
    execution_status: str | None = None,
    team_evidence: list[TeamEvidence] | None = None,
    player_evidence: list[PlayerEvidence] | None = None,
    contract_evidence: list[ContractEvidence] | None = None,
    cap_evidence: list[CapEvidence] | None = None,
    draft_pick_evidence: list[DraftPickEvidence] | None = None,
    transaction_evidence: list[TransactionEvidence] | None = None,
    rules_evidence: list[RuleEvidence] | None = None,
    league_evidence: list[LeagueEvidence] | None = None,
    lineup_evidence: list[LineupEvidence] | None = None,
    free_agent_evidence: list[FreeAgentEvidence] | None = None,
    player_evaluation_evidence: list[PlayerEvaluationEvidence] | None = None,
) -> EvidencePacket:
    unique_warnings = _dedupe_strings(warnings)
    required_complete = not any(item.blocking for item in unresolved) and not any(
        result.required and result.status in {RetrievalStatus.FAILED.value, RetrievalStatus.BLOCKED.value}
        for result in results
    )
    reduced = any(
        result.status in {RetrievalStatus.UNAVAILABLE.value, RetrievalStatus.PARTIAL.value, RetrievalStatus.FAILED.value}
        and not result.required
        for result in results
    ) or any("unavailable" in warning for warning in unique_warnings)
    status = execution_status or _execution_status(required_complete, reduced, unique_warnings, results, unresolved)
    return EvidencePacket(
        request_context_ref=EvidenceContextRef(
            user_id=context.user_id,
            league_id=context.league_id,
            league_team_id=context.league_team_id,
            conversation_id=context.conversation_id,
            current_season=context.current_season,
            requested_seasons=_dedupe_ints([context.requested_season] + [season for request in plan.retrieval_requests for season in request.seasons]),
        ),
        plan_type=plan.plan_type,
        decision_engine=plan.decision_engine,
        team_evidence=team_evidence or [],
        player_evidence=player_evidence or [],
        contract_evidence=contract_evidence or [],
        cap_evidence=cap_evidence or [],
        draft_pick_evidence=draft_pick_evidence or [],
        transaction_evidence=transaction_evidence or [],
        rules_evidence=rules_evidence or [],
        league_evidence=league_evidence or [],
        lineup_evidence=lineup_evidence or [],
        free_agent_evidence=free_agent_evidence or [],
        player_evaluation_evidence=player_evaluation_evidence or [],
        retrieval_results=results,
        unresolved_requirements=_dedupe_dataclasses(unresolved, lambda item: (item.requirement_type, item.explanation, item.retrieval_type)),
        warnings=unique_warnings,
        required_evidence_complete=required_complete,
        reduced_mode=reduced or status == EvidenceExecutionStatus.REDUCED.value,
        execution_status=status,
    )


def _execution_status(
    required_complete: bool,
    reduced: bool,
    warnings: list[str],
    results: list[EvidenceRetrievalResult],
    unresolved: list[UnresolvedEvidence],
) -> str:
    if any(result.status == RetrievalStatus.FAILED.value and result.required for result in results):
        return EvidenceExecutionStatus.FAILED.value
    if not required_complete or any(item.blocking for item in unresolved):
        return EvidenceExecutionStatus.BLOCKED.value
    if reduced:
        return EvidenceExecutionStatus.REDUCED.value
    if warnings:
        return EvidenceExecutionStatus.COMPLETE_WITH_WARNINGS.value
    return EvidenceExecutionStatus.COMPLETE.value


def _dedupe_requests(requests: list[RetrievalRequest]) -> list[RetrievalRequest]:
    return _dedupe_dataclasses(
        requests,
        lambda item: (
            item.retrieval_type,
            item.scope,
            tuple(item.team_ids),
            tuple(item.player_ids),
            tuple(item.pick_ids),
            tuple(item.seasons),
            tuple(sorted((key, _hashable_filter_value(value)) for key, value in (item.filters or {}).items())),
        ),
    )


def _hashable_filter_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable_filter_value(inner)) for key, inner in value.items()))
    if isinstance(value, list):
        return tuple(_hashable_filter_value(inner) for inner in value)
    if isinstance(value, set):
        return tuple(sorted(_hashable_filter_value(inner) for inner in value))
    return value


def _dedupe_dataclasses(items: list[Any], key_fn: Any) -> list[Any]:
    out = []
    seen = set()
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_team_evidence(items: list[TeamEvidence]) -> list[TeamEvidence]:
    by_team: dict[str, TeamEvidence] = {}
    for item in items:
        existing = by_team.get(item.league_team_id)
        if not existing:
            by_team[item.league_team_id] = item
            continue
        by_team[item.league_team_id] = TeamEvidence(
            league_team_id=item.league_team_id,
            team_name=existing.team_name or item.team_name,
            owner_name=existing.owner_name or item.owner_name,
            roster_player_ids=_dedupe_strings(existing.roster_player_ids + item.roster_player_ids),
            roster_summary={**item.roster_summary, **existing.roster_summary},
            team_brain_summary={**item.team_brain_summary, **existing.team_brain_summary},
            positional_summary={**item.positional_summary, **existing.positional_summary},
            data_sources=_dedupe_strings(existing.data_sources + item.data_sources),
            warnings=_dedupe_strings(existing.warnings + item.warnings),
            lineage=_dedupe_dataclasses(existing.lineage + item.lineage, lambda entry: (entry.domain, entry.source_name, entry.scope, entry.league_id, entry.league_team_id, entry.player_id, entry.status)),
        )
    return list(by_team.values())


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact(inner)
            for key, inner in value.items()
            if inner not in (None, "", [], {}) and key not in {"raw", "raw_row", "exception", "traceback", "service_key", "access_token", "refresh_token"}
        }
    if isinstance(value, list):
        return [_compact(item) for item in value if item not in (None, "", [], {})]
    return value


def _compact_mapping(row: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return _compact({key: row.get(key) for key in keys if key in row})


def _request_entity_ids(request: RetrievalRequest) -> list[str]:
    return _dedupe_strings(list(request.entity_ids) + list(request.team_ids) + list(request.player_ids) + list(request.pick_ids))


def _clean_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


def _clean_text(value: Any) -> str | None:
    return _clean_id(value)


def _list_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _dedupe_strings([_clean_id(item) for item in value if _clean_id(item)])
    text = _clean_id(value)
    if not text:
        return []
    return [text]


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace("$", "").replace(",", ""))
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _dedupe_strings(items: list[str | None]) -> list[str]:
    out = []
    for item in items:
        text = _clean_id(item)
        if text and text not in out:
            out.append(text)
    return out


def _dedupe_ints(items: list[int | None]) -> list[int]:
    out = []
    for item in items:
        value = _safe_int(item)
        if value is not None and value not in out:
            out.append(value)
    return out
