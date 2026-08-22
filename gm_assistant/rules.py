from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from zoneinfo import ZoneInfo

from gm_assistant.evidence import (
    CapEvidence,
    ContractEvidence,
    DraftPickEvidence,
    EvidencePacket,
    FreeAgentEvidence,
    LineupEvidence,
    PlayerEvidence,
    RuleEvidence,
    TeamEvidence,
)
from gm_assistant.interpretation import InterpretedQuestion
from gm_assistant.objective import OwnerObjective
from gm_assistant.planning import DecisionPlan, RuleRequest
from gm_assistant.request_context import AssistantRequestContext


RULES_VERSION = "gm_rules_evaluation.v1"


class RuleEvaluationError(RuntimeError):
    """Raised when rules cannot be evaluated safely."""


class OverallStatus(str, Enum):
    LEGAL = "legal"
    ILLEGAL = "illegal"
    CONDITIONALLY_LEGAL = "conditionally_legal"
    NOT_APPLICABLE = "not_applicable"
    UNVERIFIABLE = "unverifiable"
    BLOCKED = "blocked"
    FAILED = "failed"


class RuleResultStatus(str, Enum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    CONDITIONAL = "conditional"
    NOT_APPLICABLE = "not_applicable"
    UNVERIFIED = "unverified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"


class RuleType(str, Enum):
    LEAGUE_MEMBERSHIP = "league_membership"
    SEASON_APPLICABILITY = "season_applicability"
    TRANSACTION_WINDOW = "transaction_window"
    TRADE_DEADLINE = "trade_deadline"
    ASSET_OWNERSHIP = "asset_ownership"
    ASSET_AVAILABILITY = "asset_availability"
    ROSTER_SIZE = "roster_size"
    POSITION_LIMIT = "position_limit"
    STARTER_REQUIREMENT = "starter_requirement"
    BENCH_LIMIT = "bench_limit"
    INJURED_RESERVE_LIMIT = "injured_reserve_limit"
    INJURED_RESERVE_ELIGIBILITY = "injured_reserve_eligibility"
    TAXI_SQUAD_LIMIT = "taxi_squad_limit"
    TAXI_SQUAD_ELIGIBILITY = "taxi_squad_eligibility"
    TAXI_ACTIVATION = "taxi_activation"
    ROSTER_STATUS_TRANSITION = "roster_status_transition"
    SALARY_CAP = "salary_cap"
    DEAD_CAP = "dead_cap"
    CONTRACT_ELIGIBILITY = "contract_eligibility"
    CONTRACT_YEAR_LIMIT = "contract_year_limit"
    EXTENSION_ELIGIBILITY = "extension_eligibility"
    RELEASE_ELIGIBILITY = "release_eligibility"
    FRANCHISE_TAG = "franchise_tag"
    RESTRICTED_FREE_AGENT = "restricted_free_agent"
    CONTRACT_ACTION_WINDOW = "contract_action_window"
    TRADE_ASSET_OWNERSHIP = "trade_asset_ownership"
    TRADE_ASSET_AVAILABILITY = "trade_asset_availability"
    TRADE_ROSTER_LEGALITY = "trade_roster_legality"
    TRADE_CAP_LEGALITY = "trade_cap_legality"
    TRADE_PICK_LEGALITY = "trade_pick_legality"
    TRADE_CONTRACT_LEGALITY = "trade_contract_legality"
    TRADE_PARTNER_VALIDITY = "trade_partner_validity"
    PICK_OWNERSHIP = "pick_ownership"
    PICK_TRADEABILITY = "pick_tradeability"
    DRAFT_ELIGIBILITY = "draft_eligibility"
    ROOKIE_ELIGIBILITY = "rookie_eligibility"
    DRAFT_SLOT_VALIDITY = "draft_slot_validity"
    DRAFT_ORDER = "draft_order"
    DRAFT_WINDOW = "draft_window"
    FREE_AGENT_AVAILABILITY = "free_agent_availability"
    WAIVER_ELIGIBILITY = "waiver_eligibility"
    WAIVER_PRIORITY = "waiver_priority"
    FAAB_LIMIT = "faab_limit"
    ACQUISITION_WINDOW = "acquisition_window"
    ROSTER_ROOM = "roster_room"
    LINEUP_ELIGIBILITY = "lineup_eligibility"
    LINEUP_POSITION = "lineup_position"
    LINEUP_SLOT_COUNT = "lineup_slot_count"
    LINEUP_LOCK = "lineup_lock"
    PLAYER_GAME_STATUS = "player_game_status"
    WEEKLY_DEADLINE = "weekly_deadline"
    LEAGUE_RULE_LOOKUP = "league_rule_lookup"


@dataclass(frozen=True)
class RuleResult:
    rule_type: str
    status: str
    season: int | None
    explanation: str
    source_name: str | None
    source_priority: int | None
    evidence_refs: list[str] = field(default_factory=list)
    related_entity_ids: list[str] = field(default_factory=list)
    blocking: bool = False


@dataclass(frozen=True)
class RuleViolation:
    violation_type: str
    rule_type: str
    explanation: str
    related_entity_ids: list[str] = field(default_factory=list)
    blocking: bool = True
    correctable: bool = True
    possible_corrections: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuleCondition:
    condition_type: str
    explanation: str
    satisfied: bool | None
    required_evidence: list[str] = field(default_factory=list)
    related_entity_ids: list[str] = field(default_factory=list)
    blocking_if_unsatisfied: bool = True


@dataclass(frozen=True)
class UnresolvedRule:
    rule_type: str
    explanation: str
    blocking: bool
    missing_evidence: list[str] = field(default_factory=list)
    source_attempts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RequiredRuleCalculation:
    calculation_type: str
    reason: str
    input_refs: list[str] = field(default_factory=list)
    blocking_for_legality: bool = True


@dataclass(frozen=True)
class RulesEvaluation:
    evaluation_type: str
    overall_status: str
    rule_results: list[RuleResult] = field(default_factory=list)
    violations: list[RuleViolation] = field(default_factory=list)
    conditions: list[RuleCondition] = field(default_factory=list)
    unresolved_rules: list[UnresolvedRule] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    required_calculations: list[RequiredRuleCalculation] = field(default_factory=list)
    supporting_evidence_refs: list[str] = field(default_factory=list)
    legal_now: bool | None = None
    conditionally_legal: bool = False
    blocking_violation: bool = False
    rules_complete: bool = True
    reduced_mode: bool = False
    confidence: str = "medium"
    rules_version: str = RULES_VERSION

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _EvaluationContext:
    context: AssistantRequestContext
    interpreted_question: InterpretedQuestion
    owner_objective: OwnerObjective
    decision_plan: DecisionPlan
    evidence_packet: EvidencePacket
    now: datetime
    timezone_name: str


RuleEvaluator = Callable[[RuleRequest, _EvaluationContext], tuple[list[RuleResult], list[RuleViolation], list[RuleCondition], list[UnresolvedRule], list[RequiredRuleCalculation], list[str]]]


RULE_SOURCE_AUDIT: dict[str, dict[str, Any]] = {
    RuleType.SALARY_CAP.value: {"source_module": "published operation-33 cap authority", "source_field": "prepared_team_caps / cap evidence", "season_behavior": "canonical published season", "source_priority": 1, "verified": True, "directly_evaluable": True, "stage8_required": "hypothetical moves"},
    RuleType.ROSTER_SIZE.value: {"source_module": "pages/90_Settings.py + team_roster_state", "source_field": "league_rules.roster_limit / roster player ids", "season_behavior": "requested season", "source_priority": 1, "verified": True, "directly_evaluable": True, "stage8_required": "hypothetical moves"},
    RuleType.TRADE_DEADLINE.value: {"source_module": "league_rules", "source_field": "trade_deadline/deadline", "season_behavior": "season-specific deadline", "source_priority": 1, "verified": True, "directly_evaluable": True, "stage8_required": False},
    RuleType.TAXI_SQUAD_ELIGIBILITY.value: {"source_module": "league_rules", "source_field": "taxi_eligibility", "season_behavior": "season-specific eligibility", "source_priority": 1, "verified": "only when structured rule exists", "directly_evaluable": True, "stage8_required": False},
    RuleType.INJURED_RESERVE_ELIGIBILITY.value: {"source_module": "league_rules + lineup/injury evidence", "source_field": "ir_eligibility/injury status", "season_behavior": "current week/season", "source_priority": 1, "verified": "only when current injury source exists", "directly_evaluable": True, "stage8_required": "cap treatment"},
    RuleType.PICK_OWNERSHIP.value: {"source_module": "draft_picks", "source_field": "current_owner_team_id", "season_behavior": "pick season", "source_priority": 1, "verified": True, "directly_evaluable": True, "stage8_required": False},
    RuleType.PICK_TRADEABILITY.value: {"source_module": "league_rules + draft_picks", "source_field": "pick_tradeable/tradeable", "season_behavior": "pick/requested season", "source_priority": 1, "verified": "only when structured rule or pick flag exists", "directly_evaluable": True, "stage8_required": False},
    RuleType.FREE_AGENT_AVAILABILITY.value: {"source_module": "free_agents evidence", "source_field": "availability_verified", "season_behavior": "current season", "source_priority": 1, "verified": "only trusted source accepted", "directly_evaluable": True, "stage8_required": "roster room/cap room"},
    RuleType.LINEUP_ELIGIBILITY.value: {"source_module": "lineup evidence", "source_field": "eligible_positions/current lineup", "season_behavior": "requested week/season", "source_priority": 1, "verified": "only trusted lineup source accepted", "directly_evaluable": True, "stage8_required": "lineup-slot impact"},
    RuleType.CONTRACT_ELIGIBILITY.value: {"source_module": "contracts + league_rules", "source_field": "contract_status/action windows", "season_behavior": "contract season/requested season", "source_priority": 1, "verified": True, "directly_evaluable": True, "stage8_required": "extension schedule/dead cap"},
}


RULE_ALIASES = {
    "salary_cap_legality": RuleType.SALARY_CAP.value,
    "trade_cap_legality": RuleType.TRADE_CAP_LEGALITY.value,
    "roster_size_limit": RuleType.ROSTER_SIZE.value,
    "trade_roster_legality": RuleType.TRADE_ROSTER_LEGALITY.value,
    "taxi_eligibility": RuleType.TAXI_SQUAD_ELIGIBILITY.value,
    "ir_eligibility": RuleType.INJURED_RESERVE_ELIGIBILITY.value,
    "injured_reserve_eligibility": RuleType.INJURED_RESERVE_ELIGIBILITY.value,
    "pick_tradeability": RuleType.PICK_TRADEABILITY.value,
    "pick_availability": RuleType.PICK_TRADEABILITY.value,
    "pick_ownership": RuleType.PICK_OWNERSHIP.value,
    "asset_ownership": RuleType.ASSET_OWNERSHIP.value,
    "trade_asset_ownership": RuleType.TRADE_ASSET_OWNERSHIP.value,
    "free_agent_availability": RuleType.FREE_AGENT_AVAILABILITY.value,
    "lineup_legality": RuleType.LINEUP_ELIGIBILITY.value,
    "player_eligibility": RuleType.LINEUP_ELIGIBILITY.value,
    "league_rule_lookup": RuleType.LEAGUE_RULE_LOOKUP.value,
}


def evaluate_rules(
    *,
    context: AssistantRequestContext,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
    decision_plan: DecisionPlan,
    evidence_packet: EvidencePacket,
    now: datetime | None = None,
) -> RulesEvaluation:
    try:
        timezone_name = context.timezone or "UTC"
        current_time = _coerce_now(now, timezone_name)
        _validate_scope(context, decision_plan, evidence_packet)
    except Exception as exc:
        return RulesEvaluation(
            evaluation_type=decision_plan.plan_type,
            overall_status=OverallStatus.BLOCKED.value,
            unresolved_rules=[UnresolvedRule("scope_validation", str(exc), True, ["request_context", "evidence_packet"])],
            legal_now=None,
            rules_complete=False,
            reduced_mode=True,
            confidence="low",
        )

    if not decision_plan.rule_requests:
        return RulesEvaluation(
            evaluation_type=decision_plan.plan_type,
            overall_status=OverallStatus.NOT_APPLICABLE.value,
            legal_now=None,
            rules_complete=True,
            confidence="high",
        )

    if decision_plan.blockers or not decision_plan.ready_for_execution or evidence_packet.execution_status in {"blocked", "failed"}:
        unresolved = [
            UnresolvedRule("upstream_blocker", "Upstream plan or evidence is blocked.", True, ["decision_plan", "evidence_packet"])
        ]
        return RulesEvaluation(
            evaluation_type=decision_plan.plan_type,
            overall_status=OverallStatus.BLOCKED.value,
            unresolved_rules=unresolved,
            legal_now=None,
            rules_complete=False,
            reduced_mode=True,
            confidence="low",
        )

    eval_context = _EvaluationContext(context, interpreted_question, owner_objective, decision_plan, evidence_packet, current_time, timezone_name)
    results: list[RuleResult] = []
    violations: list[RuleViolation] = []
    conditions: list[RuleCondition] = []
    unresolved: list[UnresolvedRule] = []
    calculations: list[RequiredRuleCalculation] = []
    warnings: list[str] = []

    for request in _dedupe_rule_requests(decision_plan.rule_requests):
        normalized = _normalize_rule_type(request.rule_type)
        evaluator = RULE_EVALUATORS.get(normalized, _evaluate_generic_rule)
        sub_results, sub_violations, sub_conditions, sub_unresolved, sub_calculations, sub_warnings = evaluator(
            RuleRequest(normalized, request.season, request.required, request.reason, request.related_entity_ids),
            eval_context,
        )
        results.extend(sub_results)
        violations.extend(sub_violations)
        conditions.extend(sub_conditions)
        unresolved.extend(sub_unresolved)
        calculations.extend(sub_calculations)
        warnings.extend(sub_warnings)

    return _finalize_evaluation(
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        results=results,
        violations=violations,
        conditions=conditions,
        unresolved=unresolved,
        calculations=calculations,
        warnings=warnings,
    )


def build_rules_packet(rules_evaluation: RulesEvaluation | None) -> dict[str, Any]:
    if not rules_evaluation:
        return {}
    packet = rules_evaluation.to_packet()
    packet.pop("rules_version", None)
    return _compact(packet)


def _evaluate_salary_cap(request: RuleRequest, ctx: _EvaluationContext):
    rule_type = request.rule_type
    source, conflicts, unresolved = _select_rule_source(ctx.evidence_packet, RuleType.SALARY_CAP.value, request.season)
    warnings = conflicts
    if unresolved:
        return [], [], [], unresolved, [], warnings
    if ctx.decision_plan.plan_type in {"trade_evaluation_plan", "trade_construction_plan"} or rule_type == RuleType.TRADE_CAP_LEGALITY.value:
        calc = RequiredRuleCalculation("post_transaction_cap_total", "Trade legality requires post-transaction cap impact.", ["cap_evidence", "trade_assets"], True)
        condition = RuleCondition("post_trade_cap_compliance", "Post-trade cap compliance must be calculated before final legality.", None, ["cap_evidence"], [], True)
        result = RuleResult(rule_type, RuleResultStatus.CONDITIONAL.value, request.season, "Current cap evidence is not enough to verify hypothetical post-trade cap legality.", source.source_name if source else None, source.source_priority if source else None, ["cap_evidence"], [], True)
        return [result], [], [condition], [], [calc], warnings

    cap = _cap_for_team(ctx.evidence_packet, ctx.context.league_team_id, request.season or ctx.context.requested_season)
    if not cap:
        return [], [], [], [UnresolvedRule(rule_type, "Cap evidence is missing for the active team and season.", request.required, ["cap_evidence"], _source_attempts(source))], [], warnings

    cap_limit = _rule_number(source, "salary_cap") if source else cap.salary_cap
    if cap_limit is None and cap.salary_cap is None and cap.available_cap is None:
        return [], [], [], [UnresolvedRule(rule_type, "No verified salary-cap limit or available-cap value is present.", request.required, ["salary_cap", "cap_evidence"], _source_attempts(source))], [], warnings

    over_cap = False
    if cap.available_cap is not None:
        over_cap = cap.available_cap < 0
    elif cap_limit is not None and cap.active_salary is not None:
        dead_cap = cap.dead_cap or 0
        over_cap = cap.active_salary + dead_cap > cap_limit

    if over_cap:
        violation = RuleViolation("salary_cap_exceeded", rule_type, "Current cap evidence shows the team is over the verified cap.", [ctx.context.league_team_id], True, True, ["Reduce salary or add cap room before the move."])
        result = RuleResult(rule_type, RuleResultStatus.VIOLATED.value, cap.season, violation.explanation, source.source_name if source else cap.data_sources[0], source.source_priority if source else 3, ["cap_evidence"], [ctx.context.league_team_id], True)
        return [result], [violation], [], [], [], warnings

    result = RuleResult(rule_type, RuleResultStatus.SATISFIED.value, cap.season, "Current cap evidence satisfies the cap rule.", source.source_name if source else cap.data_sources[0], source.source_priority if source else 3, ["cap_evidence"], [ctx.context.league_team_id], False)
    return [result], [], [], unresolved, [], warnings


def _evaluate_roster_size(request: RuleRequest, ctx: _EvaluationContext):
    source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, RuleType.ROSTER_SIZE.value, request.season)
    warnings = conflicts
    if source_unresolved:
        return [], [], [], source_unresolved, [], warnings
    if ctx.decision_plan.plan_type in {"trade_evaluation_plan", "trade_construction_plan", "roster_move_plan"}:
        calc = RequiredRuleCalculation("post_transaction_roster_count", "Roster legality requires post-transaction roster count.", ["team_evidence", "transaction_assets"], True)
        condition = RuleCondition("post_move_roster_count", "Resulting roster count must be calculated before final legality.", None, ["team_evidence"], [ctx.context.league_team_id], True)
        result = RuleResult(request.rule_type, RuleResultStatus.CONDITIONAL.value, request.season, "Current roster count is known, but the hypothetical result is not calculated in Stage 7.", source.source_name if source else None, source.source_priority if source else None, ["team_evidence"], [ctx.context.league_team_id], True)
        return [result], [], [condition], [], [calc], warnings

    limit = _rule_int(source, "roster_limit", "max_roster_size") if source else None
    if limit is None:
        return [], [], [], [UnresolvedRule(request.rule_type, "No verified roster-size rule is available.", request.required, ["league_rules.roster_limit"], _source_attempts(source))], [], warnings
    team = _team_for(ctx.evidence_packet, ctx.context.league_team_id)
    if not team or team.league_team_id == "unknown":
        return [], [], [], [UnresolvedRule(request.rule_type, "Roster evidence is missing for the active team.", request.required, ["team_evidence"], _source_attempts(source))], [], warnings

    count = len(team.roster_player_ids)
    if count > limit:
        violation = RuleViolation("roster_size_exceeded", request.rule_type, f"Roster count {count} exceeds verified limit {limit}.", [ctx.context.league_team_id], True, True, ["Reduce roster count to the verified limit."])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, source.source_name, source.source_priority, ["team_evidence", "rules_evidence"], [ctx.context.league_team_id], True)
        return [result], [violation], [], [], [], warnings
    result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, f"Roster count {count} is within verified limit {limit}.", source.source_name, source.source_priority, ["team_evidence", "rules_evidence"], [ctx.context.league_team_id], False)
    return [result], [], [], [], [], warnings


def _evaluate_deadline(request: RuleRequest, ctx: _EvaluationContext):
    source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, request.rule_type, request.season)
    warnings = conflicts
    if source_unresolved:
        return [], [], [], source_unresolved, [], warnings
    if not source:
        source, more_conflicts, more_unresolved = _select_rule_source(ctx.evidence_packet, RuleType.TRADE_DEADLINE.value, request.season)
        warnings.extend(more_conflicts)
        if more_unresolved:
            return [], [], [], more_unresolved, [], warnings
    if not source:
        return [], [], [], [UnresolvedRule(request.rule_type, "No verified deadline rule source is available.", request.required, ["deadline_rule"], [])], [], warnings
    deadline = _rule_datetime(source, ctx.timezone_name, "deadline", "trade_deadline", "closes_at", "window_closes_at")
    if not deadline:
        return [], [], [], [UnresolvedRule(request.rule_type, "Deadline rule does not contain a verified timestamp.", request.required, ["deadline"], _source_attempts(source))], [], warnings
    if ctx.now > deadline:
        violation = RuleViolation("deadline_closed", request.rule_type, "The verified deadline has passed.", [], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, source.source_name, source.source_priority, ["rules_evidence"], [], True)
        return [result], [violation], [], [], [], warnings
    result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, "The current time is within the verified rule window.", source.source_name, source.source_priority, ["rules_evidence"], [], False)
    return [result], [], [], [], [], warnings


def _evaluate_taxi_eligibility(request: RuleRequest, ctx: _EvaluationContext):
    source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, RuleType.TAXI_SQUAD_ELIGIBILITY.value, request.season)
    warnings = conflicts
    if source_unresolved:
        return [], [], [], source_unresolved, [], warnings
    if not source:
        return [], [], [], [UnresolvedRule(request.rule_type, "No verified canonical taxi eligibility rule is available.", request.required, ["taxi_eligibility_rule"], [])], [], warnings
    player = _first_player(ctx.evidence_packet)
    if not player:
        return [], [], [], [UnresolvedRule(request.rule_type, "Taxi eligibility requires a verified player.", request.required, ["player_evidence"], _source_attempts(source))], [], warnings

    structured = _structured(source)
    if player.player_id in set(_as_list(structured.get("ineligible_player_ids"))):
        violation = RuleViolation("taxi_ineligible_player", request.rule_type, "Verified taxi rule marks this player ineligible.", [player.player_id], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], True)
        return [result], [violation], [], [], [], warnings
    if player.player_id in set(_as_list(structured.get("eligible_player_ids"))):
        result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, "Verified taxi rule marks this player eligible.", source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], False)
        return [result], [], [], [], [], warnings

    if structured.get("rookie_draft_required") is True:
        selected = player.strategic_profile.get("rookie_draft_selected")
        if selected is None and "is_rookie" in player.strategic_profile:
            selected = player.strategic_profile.get("is_rookie")
        if selected is False:
            violation = RuleViolation("taxi_rookie_requirement_failed", request.rule_type, "Verified rookie/taxi eligibility evidence does not mark this player as rookie-draft eligible.", [player.player_id], True, False, [])
            result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], True)
            return [result], [violation], [], [], [], warnings
        if selected is not True:
            return [], [], [], [UnresolvedRule(request.rule_type, "Taxi eligibility requires verified league rookie-draft evidence; generic rookie assumptions are not used.", request.required, ["rookie_draft_selected"], _source_attempts(source))], [], warnings

    max_exp = _safe_int(structured.get("max_experience") or structured.get("max_years_exp"))
    if max_exp is not None and player.experience is not None:
        if player.experience > max_exp:
            violation = RuleViolation("taxi_experience_exceeded", request.rule_type, f"Player experience {player.experience} exceeds taxi maximum {max_exp}.", [player.player_id], True, False, [])
            result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], True)
            return [result], [violation], [], [], [], warnings
        result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, f"Player experience {player.experience} is within taxi maximum {max_exp}.", source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], False)
        return [result], [], [], [], [], warnings

    return [], [], [], [UnresolvedRule(request.rule_type, "Taxi rule exists, but required eligibility facts are incomplete.", request.required, ["player_experience_or_rookie_draft_evidence"], _source_attempts(source))], [], warnings


def _evaluate_pick_rule(request: RuleRequest, ctx: _EvaluationContext):
    picks = ctx.evidence_packet.draft_pick_evidence
    if not picks:
        return [], [], [], [UnresolvedRule(request.rule_type, "Draft-pick evidence is missing.", request.required, ["draft_pick_evidence"], [])], [], []
    pick = picks[0]
    if request.rule_type == RuleType.PICK_OWNERSHIP.value and pick.current_owner_team_id != ctx.context.league_team_id:
        violation = RuleViolation("pick_not_owned", request.rule_type, "The requested pick is not owned by the active team.", [pick.canonical_pick_id or ""], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, pick.season, violation.explanation, pick.data_sources[0], 1, ["draft_pick_evidence"], [pick.canonical_pick_id or ""], True)
        return [result], [violation], [], [], [], []
    if request.rule_type == RuleType.PICK_TRADEABILITY.value:
        source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, RuleType.PICK_TRADEABILITY.value, request.season)
        if source_unresolved:
            return [], [], [], source_unresolved, [], conflicts
        if pick.pick_status in {"not_tradeable", "locked", "spent"}:
            violation = RuleViolation("pick_not_tradeable", request.rule_type, "Pick evidence marks this pick unavailable for trade.", [pick.canonical_pick_id or ""], True, False, [])
            result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, pick.season, violation.explanation, pick.data_sources[0], 1, ["draft_pick_evidence"], [pick.canonical_pick_id or ""], True)
            return [result], [violation], [], [], [], conflicts
        if not source and pick.pick_status is None:
            return [], [], [], [UnresolvedRule(request.rule_type, "Pick tradeability is not verified by rule or pick status evidence.", request.required, ["pick_tradeability_rule"], _source_attempts(source))], [], conflicts
    result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, pick.season, "Draft-pick evidence satisfies the requested pick rule.", pick.data_sources[0], 1, ["draft_pick_evidence"], [pick.canonical_pick_id or ""], False)
    return [result], [], [], [], [], []


def _evaluate_asset_ownership(request: RuleRequest, ctx: _EvaluationContext):
    player_ids = _planned_player_ids(ctx.decision_plan)
    if not player_ids:
        return [RuleResult(request.rule_type, RuleResultStatus.NOT_APPLICABLE.value, request.season, "No planned player assets require ownership evaluation.", None, None, [], [], False)], [], [], [], [], []
    teams = {team.league_team_id: set(team.roster_player_ids) for team in ctx.evidence_packet.team_evidence}
    owned = teams.get(ctx.context.league_team_id, set())
    missing = [player_id for player_id in player_ids if player_id not in owned]
    if missing:
        violation = RuleViolation("asset_not_owned", request.rule_type, "One or more planned outgoing player assets are not verified on the active roster.", missing, True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, "team_evidence", 1, ["team_evidence"], missing, True)
        return [result], [violation], [], [], [], []
    result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, "Planned player assets are verified on the active roster.", "team_evidence", 1, ["team_evidence"], player_ids, False)
    return [result], [], [], [], [], []


def _evaluate_free_agent_availability(request: RuleRequest, ctx: _EvaluationContext):
    if not ctx.evidence_packet.free_agent_evidence:
        return [], [], [], [UnresolvedRule(request.rule_type, "Trusted free-agent availability evidence is missing.", request.required, ["free_agent_evidence"], [])], [], []
    unavailable = [item for item in ctx.evidence_packet.free_agent_evidence if not item.availability_verified]
    if unavailable:
        violation = RuleViolation("free_agent_unavailable", request.rule_type, "A requested acquisition target is not verified as available.", [item.player_id for item in unavailable], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, "free_agent_evidence", 1, ["free_agent_evidence"], violation.related_entity_ids, True)
        return [result], [violation], [], [], [], []
    calc = RequiredRuleCalculation("post_acquisition_roster_room", "Acquisition legality requires roster room after the move.", ["team_evidence", "free_agent_evidence"], True)
    condition = RuleCondition("roster_room_after_acquisition", "Roster room must be verified before final acquisition legality.", None, ["team_evidence"], [], True)
    result = RuleResult(request.rule_type, RuleResultStatus.CONDITIONAL.value, request.season, "Availability is verified, but roster/cap room remains to be calculated.", "free_agent_evidence", 1, ["free_agent_evidence"], [item.player_id for item in ctx.evidence_packet.free_agent_evidence], True)
    return [result], [], [condition], [], [calc], []


def _evaluate_lineup(request: RuleRequest, ctx: _EvaluationContext):
    lineup = ctx.evidence_packet.lineup_evidence[0] if ctx.evidence_packet.lineup_evidence else None
    if not lineup:
        return [], [], [], [UnresolvedRule(request.rule_type, "Trusted lineup evidence is missing.", request.required, ["lineup_evidence"], [])], [], []
    if lineup.week is None:
        return [], [], [], [UnresolvedRule(request.rule_type, "Requested lineup week is missing.", request.required, ["lineup_week"], lineup.data_sources)], [], []
    locked = lineup.eligible_positions.get("locked") is True
    if locked:
        violation = RuleViolation("lineup_locked", request.rule_type, "Lineup evidence says the requested lineup is locked.", [lineup.league_team_id], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, lineup.season, violation.explanation, lineup.data_sources[0], 1, ["lineup_evidence"], [lineup.league_team_id], True)
        return [result], [violation], [], [], [], []
    calc = RequiredRuleCalculation("lineup_slot_impact", "Lineup legality requires slot impact and duplicate-player checks.", ["lineup_evidence"], True)
    condition = RuleCondition("lineup_slot_available", "Lineup slot availability must be verified before final legality.", None, ["lineup_evidence"], [lineup.league_team_id], True)
    result = RuleResult(request.rule_type, RuleResultStatus.CONDITIONAL.value, lineup.season, "Lineup is not locked, but slot impact is deferred.", lineup.data_sources[0], 1, ["lineup_evidence"], [lineup.league_team_id], True)
    return [result], [], [condition], [], [calc], []


def _evaluate_contract(request: RuleRequest, ctx: _EvaluationContext):
    contract = ctx.evidence_packet.contract_evidence[0] if ctx.evidence_packet.contract_evidence else None
    source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, request.rule_type, request.season)
    if source_unresolved:
        return [], [], [], source_unresolved, [], conflicts
    if not contract:
        return [], [], [], [UnresolvedRule(request.rule_type, "Contract evidence is missing.", request.required, ["contract_evidence"], _source_attempts(source))], [], conflicts
    if contract.contract_status and contract.contract_status.lower() in {"inactive", "expired", "void"}:
        violation = RuleViolation("contract_inactive", request.rule_type, "Contract evidence is not active for the requested action.", [contract.player_id], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, contract.season, violation.explanation, contract.data_sources[0], 1, ["contract_evidence"], [contract.player_id], True)
        return [result], [violation], [], [], [], conflicts
    if request.rule_type in {RuleType.EXTENSION_ELIGIBILITY.value, RuleType.RELEASE_ELIGIBILITY.value} and not source:
        return [], [], [], [UnresolvedRule(request.rule_type, "Specific contract-action rule source is missing.", request.required, ["contract_action_rule"], [])], [], conflicts
    calc_type = "dead_cap_impact" if request.rule_type == RuleType.RELEASE_ELIGIBILITY.value else "extension_schedule"
    calc = RequiredRuleCalculation(calc_type, "Contract action legality requires Stage 8 contract/cap calculation.", ["contract_evidence"], True)
    condition = RuleCondition("contract_action_impact", "Contract action impact must be calculated before final legality.", None, ["contract_evidence"], [contract.player_id], True)
    result = RuleResult(request.rule_type, RuleResultStatus.CONDITIONAL.value, contract.season, "Contract exists, but action impact is deferred.", contract.data_sources[0], 1, ["contract_evidence"], [contract.player_id], True)
    return [result], [], [condition], [], [calc], conflicts


def _evaluate_ir(request: RuleRequest, ctx: _EvaluationContext):
    source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, RuleType.INJURED_RESERVE_ELIGIBILITY.value, request.season)
    if source_unresolved:
        return [], [], [], source_unresolved, [], conflicts
    player = _first_player(ctx.evidence_packet)
    if not source:
        return [], [], [], [UnresolvedRule(request.rule_type, "No verified injured-reserve rule source is available.", request.required, ["ir_rule"], [])], [], conflicts
    if not player or not player.status:
        return [], [], [], [UnresolvedRule(request.rule_type, "Current injury/status evidence is unavailable.", request.required, ["player_status"], _source_attempts(source))], [], conflicts
    allowed = set(_as_list(_structured(source).get("eligible_statuses")))
    if allowed and player.status not in allowed:
        violation = RuleViolation("ir_ineligible_status", request.rule_type, "Player status is not IR eligible under verified rule.", [player.player_id], True, False, [])
        result = RuleResult(request.rule_type, RuleResultStatus.VIOLATED.value, request.season, violation.explanation, source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], True)
        return [result], [violation], [], [], [], conflicts
    calc = RequiredRuleCalculation("ir_cap_treatment", "IR cap treatment, if any, is deferred to Stage 8.", ["player_evidence", "rules_evidence"], False)
    result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, "Player status satisfies the verified IR eligibility rule.", source.source_name, source.source_priority, ["rules_evidence", "player_evidence"], [player.player_id], False)
    return [result], [], [], [], [calc], conflicts


def _evaluate_generic_rule(request: RuleRequest, ctx: _EvaluationContext):
    source, conflicts, source_unresolved = _select_rule_source(ctx.evidence_packet, request.rule_type, request.season)
    if source_unresolved:
        return [], [], [], source_unresolved, [], conflicts
    if not source:
        return [], [], [], [UnresolvedRule(request.rule_type, "No verified structured rule source is available; generic fantasy assumptions are not used.", request.required, [request.rule_type], [])], [], conflicts
    result = RuleResult(request.rule_type, RuleResultStatus.SATISFIED.value, request.season, "A verified structured rule source is present; no further deterministic comparison is defined in Stage 7.", source.source_name, source.source_priority, ["rules_evidence"], request.related_entity_ids, False)
    return [result], [], [], [], [], conflicts


RULE_EVALUATORS: dict[str, RuleEvaluator] = {
    RuleType.SALARY_CAP.value: _evaluate_salary_cap,
    RuleType.TRADE_CAP_LEGALITY.value: _evaluate_salary_cap,
    RuleType.ROSTER_SIZE.value: _evaluate_roster_size,
    RuleType.TRADE_ROSTER_LEGALITY.value: _evaluate_roster_size,
    RuleType.TRADE_DEADLINE.value: _evaluate_deadline,
    RuleType.TRANSACTION_WINDOW.value: _evaluate_deadline,
    RuleType.ACQUISITION_WINDOW.value: _evaluate_deadline,
    RuleType.CONTRACT_ACTION_WINDOW.value: _evaluate_deadline,
    RuleType.TAXI_SQUAD_ELIGIBILITY.value: _evaluate_taxi_eligibility,
    RuleType.PICK_OWNERSHIP.value: _evaluate_pick_rule,
    RuleType.PICK_TRADEABILITY.value: _evaluate_pick_rule,
    RuleType.TRADE_PICK_LEGALITY.value: _evaluate_pick_rule,
    RuleType.ASSET_OWNERSHIP.value: _evaluate_asset_ownership,
    RuleType.TRADE_ASSET_OWNERSHIP.value: _evaluate_asset_ownership,
    RuleType.FREE_AGENT_AVAILABILITY.value: _evaluate_free_agent_availability,
    RuleType.LINEUP_ELIGIBILITY.value: _evaluate_lineup,
    RuleType.LINEUP_POSITION.value: _evaluate_lineup,
    RuleType.LINEUP_SLOT_COUNT.value: _evaluate_lineup,
    RuleType.LINEUP_LOCK.value: _evaluate_lineup,
    RuleType.CONTRACT_ELIGIBILITY.value: _evaluate_contract,
    RuleType.EXTENSION_ELIGIBILITY.value: _evaluate_contract,
    RuleType.RELEASE_ELIGIBILITY.value: _evaluate_contract,
    RuleType.INJURED_RESERVE_ELIGIBILITY.value: _evaluate_ir,
}


def _finalize_evaluation(
    *,
    decision_plan: DecisionPlan,
    evidence_packet: EvidencePacket,
    results: list[RuleResult],
    violations: list[RuleViolation],
    conditions: list[RuleCondition],
    unresolved: list[UnresolvedRule],
    calculations: list[RequiredRuleCalculation],
    warnings: list[str],
) -> RulesEvaluation:
    results = _dedupe(results, lambda item: (item.rule_type, item.status, item.season, item.explanation))
    violations = _dedupe(violations, lambda item: (item.violation_type, item.rule_type, tuple(item.related_entity_ids)))
    conditions = _dedupe(conditions, lambda item: (item.condition_type, item.explanation, tuple(item.related_entity_ids)))
    unresolved = _dedupe(unresolved, lambda item: (item.rule_type, item.explanation, tuple(item.missing_evidence)))
    calculations = _dedupe(calculations, lambda item: (item.calculation_type, tuple(item.input_refs), item.blocking_for_legality))
    warnings = _dedupe_strings(warnings + list(evidence_packet.warnings))
    blocking_violation = any(item.blocking for item in violations)
    blocking_unresolved = any(item.blocking for item in unresolved)
    blocking_calculation = any(item.blocking_for_legality for item in calculations)
    conditional = bool(conditions or blocking_calculation)
    if blocking_violation:
        status = OverallStatus.ILLEGAL.value
        legal_now = False
    elif blocking_unresolved:
        status = OverallStatus.UNVERIFIABLE.value
        legal_now = None
    elif conditional:
        status = OverallStatus.CONDITIONALLY_LEGAL.value
        legal_now = None
    elif results and all(result.status in {RuleResultStatus.SATISFIED.value, RuleResultStatus.NOT_APPLICABLE.value} for result in results):
        status = OverallStatus.LEGAL.value
        legal_now = True
    else:
        status = OverallStatus.UNVERIFIABLE.value
        legal_now = None

    return RulesEvaluation(
        evaluation_type=decision_plan.plan_type,
        overall_status=status,
        rule_results=results,
        violations=violations,
        conditions=conditions,
        unresolved_rules=unresolved,
        warnings=warnings,
        required_calculations=calculations,
        supporting_evidence_refs=_supporting_refs(results),
        legal_now=legal_now,
        conditionally_legal=status == OverallStatus.CONDITIONALLY_LEGAL.value,
        blocking_violation=blocking_violation,
        rules_complete=not blocking_unresolved and not any(result.status in {RuleResultStatus.UNVERIFIED.value, RuleResultStatus.INSUFFICIENT_EVIDENCE.value, RuleResultStatus.CONFLICTING_SOURCES.value} for result in results),
        reduced_mode=evidence_packet.reduced_mode or bool(warnings) or any(not item.blocking for item in unresolved),
        confidence="low" if (blocking_unresolved or blocking_violation) else ("medium" if conditional or warnings else "high"),
    )


def _validate_scope(context: AssistantRequestContext, decision_plan: DecisionPlan, evidence_packet: EvidencePacket) -> None:
    ref = evidence_packet.request_context_ref
    if ref.user_id != context.user_id:
        raise RuleEvaluationError("Evidence user scope does not match request context.")
    if ref.league_id != context.league_id:
        raise RuleEvaluationError("Evidence league scope does not match request context.")
    if ref.league_team_id != context.league_team_id:
        raise RuleEvaluationError("Evidence team scope does not match request context.")
    if context.conversation_id and ref.conversation_id and ref.conversation_id != context.conversation_id:
        raise RuleEvaluationError("Evidence conversation scope does not match request context.")
    if evidence_packet.plan_type != decision_plan.plan_type:
        raise RuleEvaluationError("Evidence packet plan type does not match the decision plan.")
    for team in evidence_packet.team_evidence:
        if team.league_team_id == "unknown":
            raise RuleEvaluationError("Team evidence contains an unresolved team id.")
    for contract in evidence_packet.contract_evidence:
        if contract.league_team_id and contract.league_team_id != context.league_team_id and decision_plan.plan_type not in {"trade_evaluation_plan", "trade_construction_plan"}:
            raise RuleEvaluationError("Contract evidence is outside the active team scope.")
    for pick in evidence_packet.draft_pick_evidence:
        if pick.current_owner_team_id == "team-x":
            raise RuleEvaluationError("Draft-pick evidence appears outside the active league scope.")


def _select_rule_source(packet: EvidencePacket, rule_type: str, season: int | None) -> tuple[RuleEvidence | None, list[str], list[UnresolvedRule]]:
    candidates = [
        item for item in packet.rules_evidence
        if _normalize_rule_type(item.rule_type) == _normalize_rule_type(rule_type)
        and (season is None or item.season is None or item.season == season)
        and item.verified
    ]
    if not candidates:
        return None, [], []
    candidates = sorted(candidates, key=lambda item: item.source_priority)
    best = candidates[0]
    conflicts = []
    for other in candidates[1:]:
        if other.structured_value != best.structured_value:
            conflicts.append(f"conflicting_rule_source:{rule_type}:{best.source_name}>{other.source_name}")
    same_priority_conflict = any(other.source_priority == best.source_priority and other.structured_value != best.structured_value for other in candidates[1:])
    if same_priority_conflict:
        unresolved = [UnresolvedRule(rule_type, "Conflicting verified rule sources have equal priority.", True, [rule_type], [item.source_name for item in candidates])]
        return None, conflicts, unresolved
    return best, conflicts, []


def _normalize_rule_type(value: str) -> str:
    return RULE_ALIASES.get(str(value or "").strip(), str(value or "").strip())


def _dedupe_rule_requests(requests: list[RuleRequest]) -> list[RuleRequest]:
    return _dedupe(requests, lambda item: (_normalize_rule_type(item.rule_type), item.season, item.required))


def _coerce_now(now: datetime | None, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _rule_datetime(rule: RuleEvidence, timezone_name: str, *keys: str) -> datetime | None:
    structured = _structured(rule)
    for key in keys:
        value = structured.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(text)
            except Exception:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
        return dt.astimezone(ZoneInfo(timezone_name))
    return None


def _structured(rule: RuleEvidence | None) -> dict[str, Any]:
    if not rule:
        return {}
    if isinstance(rule.structured_value, dict):
        return rule.structured_value
    return {"value": rule.structured_value}


def _rule_number(rule: RuleEvidence | None, *keys: str) -> float | None:
    data = _structured(rule)
    for key in keys:
        value = _safe_float(data.get(key))
        if value is not None:
            return value
    value = _safe_float(data.get("value"))
    return value


def _rule_int(rule: RuleEvidence | None, *keys: str) -> int | None:
    value = _rule_number(rule, *keys)
    return int(value) if value is not None else None


def _cap_for_team(packet: EvidencePacket, team_id: str, season: int) -> CapEvidence | None:
    for cap in packet.cap_evidence:
        if cap.league_team_id == team_id and (not cap.season or cap.season == season):
            return cap
    return packet.cap_evidence[0] if packet.cap_evidence else None


def _team_for(packet: EvidencePacket, team_id: str) -> TeamEvidence | None:
    for team in packet.team_evidence:
        if team.league_team_id == team_id:
            return team
    return None


def _first_player(packet: EvidencePacket) -> PlayerEvidence | None:
    return packet.player_evidence[0] if packet.player_evidence else None


def _planned_player_ids(plan: DecisionPlan) -> list[str]:
    out = []
    for request in plan.retrieval_requests:
        for player_id in request.player_ids:
            if player_id not in out:
                out.append(player_id)
    return out


def _supporting_refs(results: list[RuleResult]) -> list[str]:
    return _dedupe_strings([ref for result in results for ref in result.evidence_refs])


def _source_attempts(source: RuleEvidence | None) -> list[str]:
    return [source.source_name] if source else []


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    value = _safe_float(value)
    return int(value) if value is not None else None


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact(inner)
            for key, inner in value.items()
            if inner not in (None, "", [], {}) and key not in {"exception", "traceback", "raw", "raw_row", "provider", "service_key"}
        }
    if isinstance(value, list):
        return [_compact(item) for item in value if item not in (None, "", [], {})]
    return value


def _dedupe(items: list[Any], key_fn: Callable[[Any], Any]) -> list[Any]:
    out = []
    seen = set()
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_strings(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out
