from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from statistics import median
from typing import Any, Callable

from gm_assistant.evidence import (
    CapEvidence,
    ContractEvidence,
    DraftPickEvidence,
    EvidencePacket,
    LineupEvidence,
    PlayerEvidence,
    TeamEvidence,
)
from gm_assistant.interpretation import AssetRef, InterpretedQuestion
from gm_assistant.objective import OwnerObjective
from gm_assistant.planning import CalculationRequest, DecisionPlan
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.rules import RequiredRuleCalculation, RulesEvaluation


CALCULATION_VERSION = "gm_calculation_packet.v1"
FORMULA_VERSION = "gm_deterministic_formulas.v1"
MAX_SCENARIOS = 3
MAX_ASSETS = 12
MAX_SEASONS = 5
MAX_PLAYERS = 40


class CalculationStatus(str, Enum):
    SUCCESS = "success"
    ESTIMATED = "estimated"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_INPUTS = "insufficient_inputs"
    BLOCKED = "blocked"
    FAILED = "failed"


class CalculationExecutionStatus(str, Enum):
    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"
    PARTIAL = "partial"
    REDUCED = "reduced"
    BLOCKED = "blocked"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class CalculationType(str, Enum):
    CURRENT_CAP_TOTAL = "current_cap_total"
    AVAILABLE_CAP = "available_cap"
    POST_TRANSACTION_CAP = "post_transaction_cap"
    POST_TRANSACTION_CAP_TOTAL = "post_transaction_cap_total"
    SALARY_DELTA = "salary_delta"
    DEAD_CAP_IMPACT = "dead_cap_impact"
    RELEASE_SAVINGS = "release_savings"
    EXTENSION_SCHEDULE = "extension_schedule"
    FUTURE_SALARY_COMMITMENT = "future_salary_commitment"
    CONTRACT_EFFICIENCY = "contract_efficiency"
    CONTRACT_YEARS_REMAINING = "contract_years_remaining"
    CONTRACT_ROLLOVER = "contract_rollover"
    CURRENT_ROSTER_COUNT = "current_roster_count"
    POST_TRANSACTION_ROSTER_COUNT = "post_transaction_roster_count"
    POST_ACQUISITION_ROSTER_ROOM = "post_acquisition_roster_room"
    ROSTER_SLOT_DELTA = "roster_slot_delta"
    POSITION_COUNT = "position_count"
    POST_TRANSACTION_POSITION_COUNT = "post_transaction_position_count"
    ROSTER_DEPTH = "roster_depth"
    POSITIONAL_NEED = "positional_need"
    POSITIONAL_SURPLUS = "positional_surplus"
    AVERAGE_ROSTER_AGE = "average_roster_age"
    POSITION_GROUP_AGE = "position_group_age"
    AGE_PROFILE = "age_profile"
    AGE_CONCENTRATION = "age_concentration"
    PLAYER_VALUE = "player_value"
    LEAGUE_RELATIVE_VALUE = "league_relative_value"
    VALUE_DELTA = "value_delta"
    VALUE_COMPARISON = "value_comparison"
    VALUE_RANGE = "value_range"
    AGE_ADJUSTED_VALUE = "age_adjusted_value"
    RISK_ADJUSTED_VALUE = "risk_adjusted_value"
    CONTRACT_ADJUSTED_VALUE = "contract_adjusted_value"
    REPLACEMENT_VALUE = "replacement_value"
    TRADE_VALUE_OUT = "trade_value_out"
    TRADE_VALUE_IN = "trade_value_in"
    TRADE_VALUE_DELTA = "trade_value_delta"
    TRADE_CAP_DELTA = "trade_cap_delta"
    TRADE_ROSTER_DELTA = "trade_roster_delta"
    TRADE_CONTRACT_DELTA = "trade_contract_delta"
    TRADE_FAIRNESS = "trade_fairness"
    TRADE_REALISM = "trade_realism"
    PICK_VALUE = "pick_value"
    PICK_RANGE_ESTIMATE = "pick_range_estimate"
    DRAFT_CAPITAL_TOTAL = "draft_capital_total"
    DRAFT_CAPITAL_DELTA = "draft_capital_delta"
    COMPETITIVE_WINDOW_FIT = "competitive_window_fit"
    TEAM_STRENGTH = "team_strength"
    FUTURE_VALUE = "future_value"
    IMMEDIATE_PRODUCTION = "immediate_production"
    RISK_PROFILE = "risk_profile"
    DEPTH_SCORE = "depth_score"
    CAP_FLEXIBILITY = "cap_flexibility"
    CONTRACT_EXPOSURE = "contract_exposure"
    TEAM_STRENGTH_COMPARISON = "team_strength_comparison"
    LINEUP_PROJECTION = "lineup_projection"
    FLOOR_CEILING = "floor_ceiling"
    LINEUP_DELTA = "lineup_delta"
    LINEUP_SLOT_COUNT = "lineup_slot_count"
    LINEUP_SLOT_IMPACT = "lineup_slot_impact"
    LINEUP_LEGALITY_INPUTS = "lineup_legality_inputs"
    YEAR_BY_YEAR_ROSTER_OUTLOOK = "year_by_year_roster_outlook"


CALCULATION_ALIASES = {
    "cap_impact": CalculationType.TRADE_CAP_DELTA.value,
    "future_cap_impact": CalculationType.FUTURE_SALARY_COMMITMENT.value,
    "savings_by_action": CalculationType.RELEASE_SAVINGS.value,
    "roster_impact": CalculationType.TRADE_ROSTER_DELTA.value,
    "contract_delta": CalculationType.TRADE_CONTRACT_DELTA.value,
    "contract_efficiency_comparison": "contract_efficiency_comparison",
    "risk_comparison": "risk_comparison",
    "timeframe_fit": CalculationType.COMPETITIVE_WINDOW_FIT.value,
    "draft_capital_strength": CalculationType.DRAFT_CAPITAL_TOTAL.value,
    "market_pick_value": CalculationType.PICK_VALUE.value,
    "historical_pick_value": CalculationType.PICK_VALUE.value,
    "extension_cost": CalculationType.EXTENSION_SCHEDULE.value,
    "replacement_cost": CalculationType.REPLACEMENT_VALUE.value,
    "roster_value_loss": CalculationType.VALUE_DELTA.value,
    "candidate_fit": "candidate_fit",
    "affordability": CalculationType.AVAILABLE_CAP.value,
    "holder_motivation": "holder_motivation",
    "target_value": CalculationType.PLAYER_VALUE.value,
    "offer_value": CalculationType.PLAYER_VALUE.value,
    "bilateral_fit": "bilateral_fit",
    "best_player_available": "best_player_available",
    "need_fit": CalculationType.POSITIONAL_NEED.value,
    "positional_scarcity": CalculationType.POSITION_COUNT.value,
    "long_term_value": CalculationType.FUTURE_VALUE.value,
    "trade_down_opportunity": "trade_down_opportunity",
    "roster_fit": CalculationType.POSITIONAL_NEED.value,
    "expected_production": CalculationType.IMMEDIATE_PRODUCTION.value,
    "volatility": CalculationType.RISK_PROFILE.value,
    "year_by_year_roster_outlook": CalculationType.YEAR_BY_YEAR_ROSTER_OUTLOOK.value,
    "future_asset_strength": CalculationType.FUTURE_VALUE.value,
    "positional_replacement_priority": CalculationType.POSITIONAL_NEED.value,
    "positional_need_comparison": CalculationType.POSITIONAL_NEED.value,
    "league_strength_summary": CalculationType.TEAM_STRENGTH.value,
    "trade_fit_context": CalculationType.COMPETITIVE_WINDOW_FIT.value,
    "post_transaction_cap_total": CalculationType.POST_TRANSACTION_CAP_TOTAL.value,
}


@dataclass(frozen=True)
class CalculationInputRef:
    input_type: str
    source_ref: str
    value: Any
    verified: bool


@dataclass(frozen=True)
class CalculationResult:
    calculation_type: str
    status: str
    value: Any
    unit: str | None
    output_key: str
    method: str
    formula_version: str
    inputs: list[CalculationInputRef] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    exact: bool = False
    estimated: bool = False
    required: bool = True
    confidence: str = "medium"
    related_entity_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    satisfies_rule_calculation_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioCalculation:
    scenario_id: str
    scenario_type: str
    label: str
    results: list[CalculationResult] = field(default_factory=list)
    rule_status_before: str | None = None
    projected_rule_status_after: str | None = None
    changed_team_ids: list[str] = field(default_factory=list)
    changed_player_ids: list[str] = field(default_factory=list)
    changed_pick_ids: list[str] = field(default_factory=list)
    complete: bool = True
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CalculationAssumption:
    assumption_type: str
    explanation: str
    source: str
    material: bool


@dataclass(frozen=True)
class UnresolvedCalculation:
    calculation_type: str
    explanation: str
    blocking: bool
    missing_inputs: list[str] = field(default_factory=list)
    related_entity_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CalculationPacket:
    plan_type: str
    decision_engine: str
    results: list[CalculationResult] = field(default_factory=list)
    scenario_results: list[ScenarioCalculation] = field(default_factory=list)
    unresolved_calculations: list[UnresolvedCalculation] = field(default_factory=list)
    assumptions: list[CalculationAssumption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    required_calculations_complete: bool = True
    reduced_mode: bool = False
    execution_status: str = CalculationExecutionStatus.COMPLETE.value
    confidence: str = "medium"
    calculation_version: str = CALCULATION_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _WorkItem:
    calculation_type: str
    required: bool
    reason: str
    input_refs: list[str]
    output_key: str
    source_ref: str


@dataclass(frozen=True)
class _CalculationContext:
    context: AssistantRequestContext
    interpreted_question: InterpretedQuestion
    owner_objective: OwnerObjective
    decision_plan: DecisionPlan
    evidence_packet: EvidencePacket
    rules_evaluation: RulesEvaluation


CalculationHandler = Callable[[_WorkItem, _CalculationContext], tuple[list[CalculationResult], list[ScenarioCalculation], list[UnresolvedCalculation], list[CalculationAssumption], list[str]]]


def build_calculation_packet(
    *,
    context: AssistantRequestContext,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
    decision_plan: DecisionPlan,
    evidence_packet: EvidencePacket,
    rules_evaluation: RulesEvaluation,
) -> CalculationPacket:
    try:
        _validate_scope(context, decision_plan, evidence_packet, rules_evaluation)
    except Exception as exc:
        return CalculationPacket(
            plan_type=decision_plan.plan_type,
            decision_engine=decision_plan.decision_engine,
            unresolved_calculations=[UnresolvedCalculation("scope_validation", str(exc), True, ["request_context", "evidence_packet", "rules_evaluation"])],
            required_calculations_complete=False,
            reduced_mode=True,
            execution_status=CalculationExecutionStatus.BLOCKED.value,
            confidence="low",
        )

    if decision_plan.blockers or not decision_plan.ready_for_execution or evidence_packet.execution_status in {"blocked", "failed"}:
        return CalculationPacket(
            plan_type=decision_plan.plan_type,
            decision_engine=decision_plan.decision_engine,
            unresolved_calculations=[UnresolvedCalculation("upstream_blocker", "Upstream plan or evidence is blocked.", True, ["decision_plan", "evidence_packet"])],
            required_calculations_complete=False,
            reduced_mode=True,
            execution_status=CalculationExecutionStatus.BLOCKED.value,
            confidence="low",
        )

    work_items, work_warnings = _build_work_items(decision_plan, rules_evaluation)
    if not work_items:
        return CalculationPacket(
            plan_type=decision_plan.plan_type,
            decision_engine=decision_plan.decision_engine,
            warnings=work_warnings,
            execution_status=CalculationExecutionStatus.NOT_APPLICABLE.value,
            confidence="high",
        )

    calc_context = _CalculationContext(context, interpreted_question, owner_objective, decision_plan, evidence_packet, rules_evaluation)
    results: list[CalculationResult] = []
    scenarios: list[ScenarioCalculation] = []
    unresolved: list[UnresolvedCalculation] = []
    assumptions: list[CalculationAssumption] = []
    warnings: list[str] = list(work_warnings)

    try:
        for item in work_items:
            handler = CALCULATION_HANDLERS.get(item.calculation_type, _unsupported_calculation)
            sub_results, sub_scenarios, sub_unresolved, sub_assumptions, sub_warnings = handler(item, calc_context)
            results.extend(sub_results)
            scenarios.extend(sub_scenarios)
            unresolved.extend(sub_unresolved)
            assumptions.extend(sub_assumptions)
            warnings.extend(sub_warnings)
    except Exception:
        return CalculationPacket(
            plan_type=decision_plan.plan_type,
            decision_engine=decision_plan.decision_engine,
            results=_dedupe_results(results),
            scenario_results=_dedupe_scenarios(scenarios),
            unresolved_calculations=_dedupe_unresolved(unresolved + [UnresolvedCalculation("system_error", "Calculation engine failed safely.", True, [])]),
            assumptions=_dedupe_assumptions(assumptions),
            warnings=_dedupe_strings(warnings),
            required_calculations_complete=False,
            reduced_mode=True,
            execution_status=CalculationExecutionStatus.FAILED.value,
            confidence="low",
        )

    return _finalize_packet(decision_plan, results, scenarios, unresolved, assumptions, warnings, work_items)


def build_calculation_packet_payload(calculation_packet: CalculationPacket | None) -> dict[str, Any]:
    if not calculation_packet:
        return {}
    payload = calculation_packet.to_payload()
    payload.pop("calculation_version", None)
    return _compact(payload)


def _build_work_items(plan: DecisionPlan, rules: RulesEvaluation) -> tuple[list[_WorkItem], list[str]]:
    warnings: list[str] = []
    raw_items: list[_WorkItem] = []
    for request in plan.calculation_requests:
        normalized = _normalize_calculation_type(request.calculation_type)
        raw_items.append(_WorkItem(normalized, request.required, request.reason, request.input_refs, request.output_key or normalized, "decision_plan"))
    for index, request in enumerate(rules.required_calculations):
        normalized = _normalize_calculation_type(request.calculation_type)
        raw_items.append(_WorkItem(normalized, request.blocking_for_legality, request.reason, request.input_refs, normalized, f"rules_evaluation.required_calculations[{index}]"))

    out: list[_WorkItem] = []
    seen = set()
    for item in raw_items:
        key = (item.calculation_type, item.output_key)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if len(out) > MAX_SCENARIOS + MAX_ASSETS:
        warnings.append("calculation_request_count_capped")
        out = out[: MAX_SCENARIOS + MAX_ASSETS]
    return out, warnings


def _validate_scope(
    context: AssistantRequestContext,
    plan: DecisionPlan,
    evidence: EvidencePacket,
    rules: RulesEvaluation,
) -> None:
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise ValueError("Calculation execution requires authenticated user, league, and team scope.")
    ref = evidence.request_context_ref
    if ref.user_id != context.user_id:
        raise ValueError("Evidence packet user scope does not match request context.")
    if ref.league_id != context.league_id:
        raise ValueError("Evidence packet league scope does not match request context.")
    if ref.league_team_id != context.league_team_id:
        raise ValueError("Evidence packet team scope does not match request context.")
    if context.conversation_id and ref.conversation_id and ref.conversation_id != context.conversation_id:
        raise ValueError("Evidence packet conversation id does not match request context.")
    if evidence.plan_type != plan.plan_type or evidence.decision_engine != plan.decision_engine:
        raise ValueError("Evidence packet plan metadata does not match decision plan.")
    if rules.evaluation_type != plan.plan_type:
        raise ValueError("Rules evaluation plan metadata does not match decision plan.")
    if rules.overall_status in {"blocked", "failed"}:
        raise ValueError("Rules evaluation is blocked or failed.")
    allowed_teams = _allowed_team_ids(context, plan)
    for team in evidence.team_evidence:
        if team.league_team_id not in allowed_teams and team.league_team_id != "unknown":
            raise ValueError("Team evidence crossed requested team scope.")
    for cap in evidence.cap_evidence:
        if cap.league_team_id not in allowed_teams and cap.league_team_id != "unknown":
            raise ValueError("Cap evidence crossed requested team scope.")
    for contract in evidence.contract_evidence:
        if contract.league_team_id and contract.league_team_id not in allowed_teams and not _is_transaction_plan(plan):
            raise ValueError("Contract evidence crossed requested team scope.")
    for lineup in evidence.lineup_evidence:
        if lineup.league_team_id not in allowed_teams and lineup.league_team_id != "unknown":
            raise ValueError("Lineup evidence crossed requested team scope.")
    for player in evidence.player_evidence:
        if player.fantasy_team_id and player.fantasy_team_id not in allowed_teams and not _is_transaction_plan(plan):
            raise ValueError("Player evidence crossed requested team scope.")
    for pick in evidence.draft_pick_evidence:
        if pick.current_owner_team_id and pick.current_owner_team_id not in allowed_teams and not _is_transaction_plan(plan):
            raise ValueError("Draft-pick evidence crossed requested team scope.")


def _current_cap(item: _WorkItem, ctx: _CalculationContext):
    cap = _cap_for(ctx, ctx.context.league_team_id)
    if not cap:
        return [], [], [_unresolved(item, "Cap evidence is missing for the active team.", ["cap_evidence"])], [], []
    results = []
    warnings = list(cap.warnings)
    if cap.salary_cap is not None and cap.active_salary is not None and cap.dead_cap is not None:
        adjustment_total = _safe_float((cap.source_fields or {}).get("adjustment_total"))
        charge_total = adjustment_total if adjustment_total is not None else cap.dead_cap
        computed = _money(cap.salary_cap - cap.active_salary - charge_total)
        inputs = [
            _input("salary_cap", "cap_evidence.salary_cap", cap.salary_cap),
            _input("active_salary", "cap_evidence.active_salary", cap.active_salary),
            _input("cap_adjustments", "cap_evidence.source_fields.adjustment_total", charge_total),
        ]
        results.append(_result(item, computed, "salary", "canonical_cap_identity: salary_cap - active_salary - cap_adjustments", inputs, related=[cap.league_team_id]))
        if cap.available_cap is not None and abs(computed - cap.available_cap) > 1:
            warnings.append("available_cap_conflicts_with_salary_cap_formula")
    if item.calculation_type in {CalculationType.AVAILABLE_CAP.value, CalculationType.CAP_FLEXIBILITY.value}:
        if cap.available_cap is None:
            return results, [], [_unresolved(item, "Available-cap evidence is missing.", ["available_cap"])], [], warnings
        results.append(_result(item, _money(cap.available_cap), "salary", "canonical_live_cap_evidence:cap_evidence.available_cap", [_input("available_cap", "cap_evidence.available_cap", cap.available_cap)], related=[cap.league_team_id]))
    return results, [], [], [], warnings


def _salary_delta(item: _WorkItem, ctx: _CalculationContext):
    outgoing, incoming = _trade_contract_sides(ctx)
    if not outgoing and not incoming:
        return [], [], [_unresolved(item, "No interpreted trade contract sides are available.", ["included_assets", "contract_evidence"])], [], []
    missing = [asset.canonical_id or asset.label or "unknown" for asset in _player_assets(ctx.interpreted_question.included_assets) if not _contract_for(ctx.evidence_packet, asset.canonical_id)]
    if missing:
        return [], [], [_unresolved(item, "One or more trade assets are missing contract salary.", ["contract_evidence"], missing)], [], []
    outgoing_salary = sum(_cap_hit(contract) for contract in outgoing)
    incoming_salary = sum(_cap_hit(contract) for contract in incoming)
    value = _money(incoming_salary - outgoing_salary)
    inputs = [_input("outgoing_salary", "contract_evidence.outgoing", outgoing_salary), _input("incoming_salary", "contract_evidence.incoming", incoming_salary)]
    return [_result(item, value, "salary", "transaction_engine.cap_hit_for_contract_delta", inputs, related=[c.player_id for c in outgoing + incoming])], [], [], [], []


def _post_transaction_cap(item: _WorkItem, ctx: _CalculationContext):
    cap = _cap_for(ctx, ctx.context.league_team_id)
    if not cap or cap.available_cap is None:
        return [], [], [_unresolved(item, "Current available-cap evidence is missing.", ["available_cap"])], [], []
    raw = (ctx.interpreted_question.raw_question or "").lower()
    if any(term in raw for term in ("release", "drop", "cut")):
        contracts = _scoped_contracts(ctx.evidence_packet, ctx.context.league_team_id)
        if not contracts:
            return [], [], [_unresolved(item, "Contract evidence is missing for post-release cap.", ["contract_evidence"])], [], []
        contract = contracts[0]
        if contract.salary is None:
            return [], [], [_unresolved(item, "Contract salary is missing for post-release cap.", ["contract_salary"], [contract.player_id])], [], []
        savings = _money(_cap_hit(contract) - _cap_hit(contract))
        post_cap = _money(cap.available_cap + savings)
        inputs = [
            _input("current_available_cap", "cap_evidence.available_cap", cap.available_cap),
            _input("release_savings", "calculation.release_savings", savings),
        ]
        result = _result(item, post_cap, "salary", "post_release_cap: available_cap + release_savings", inputs, related=[ctx.context.league_team_id, contract.player_id])
        scenario = _scenario(ctx, "proposed_release", [result], complete=True)
        return [result], [scenario], [], [CalculationAssumption("compatibility_formula", "Post-release cap uses the same V1 release-savings assumption as the transaction engine.", "services/transaction_engine.py", False)], []
    delta_result, _, unresolved, _, warnings = _salary_delta(_with_type(item, CalculationType.SALARY_DELTA.value), ctx)
    if unresolved:
        return [], [], unresolved, [], warnings
    delta = _safe_float(delta_result[0].value)
    post_cap = _money(cap.available_cap - (delta or 0))
    inputs = [_input("current_available_cap", "cap_evidence.available_cap", cap.available_cap), _input("salary_delta", "calculation.salary_delta", delta)]
    result = _result(item, post_cap, "salary", "post_transaction_cap: available_cap - salary_delta", inputs, related=[ctx.context.league_team_id])
    scenario = _scenario(ctx, "proposed_transaction", [result], complete=True)
    return [result], [scenario], [], [], warnings


def _dead_cap(item: _WorkItem, ctx: _CalculationContext):
    contracts = _scoped_contracts(ctx.evidence_packet, ctx.context.league_team_id)
    if not contracts:
        return [], [], [_unresolved(item, "Contract evidence is missing for dead-cap calculation.", ["contract_evidence"])], [], []
    results = []
    for contract in contracts[:MAX_PLAYERS]:
        if contract.salary is None:
            return results, [], [_unresolved(item, "Contract salary is missing.", ["contract_salary"], [contract.player_id])], [], []
        dead_cap = _money(_cap_hit(contract))
        inputs = [_input("salary", f"contract_evidence[{contract.player_id}].salary", contract.salary), _input("status", f"contract_evidence[{contract.player_id}].contract_status", contract.contract_status)]
        results.append(_result(item, dead_cap, "salary", "transaction_engine.drop_contract:v1_dead_cap_equals_current_cap_hit", inputs, related=[contract.player_id]))
    return results, [], [], [CalculationAssumption("compatibility_formula", "Dead cap uses the existing transaction engine V1 drop assumption.", "services/transaction_engine.py", False)], []


def _release_savings(item: _WorkItem, ctx: _CalculationContext):
    contracts = _scoped_contracts(ctx.evidence_packet, ctx.context.league_team_id)
    if not contracts:
        return [], [], [_unresolved(item, "Contract evidence is missing for release-savings calculation.", ["contract_evidence"])], [], []
    results = []
    assumptions = [CalculationAssumption("compatibility_formula", "Release savings uses removed active cap hit minus V1 dead cap.", "services/transaction_engine.py", False)]
    for contract in contracts[:MAX_PLAYERS]:
        if contract.salary is None:
            return results, [], [_unresolved(item, "Contract salary is missing.", ["contract_salary"], [contract.player_id])], assumptions, []
        removed = _cap_hit(contract)
        dead_cap = _cap_hit(contract)
        value = _money(removed - dead_cap)
        inputs = [_input("removed_active_salary", f"contract_evidence[{contract.player_id}].salary", removed), _input("new_dead_cap", "transaction_engine.drop_contract", dead_cap)]
        results.append(_result(item, value, "salary", "transaction_engine.release_savings: removed_active_salary - new_dead_cap", inputs, related=[contract.player_id]))
    return results, [], [], assumptions, []


def _extension_schedule(item: _WorkItem, ctx: _CalculationContext):
    contracts = _scoped_contracts(ctx.evidence_packet, ctx.context.league_team_id)
    if not contracts:
        return [], [], [_unresolved(item, "Contract evidence is missing for extension schedule.", ["contract_evidence"])], [], []
    contract = contracts[0]
    terms = contract.contract_terms or {}
    years = _safe_int(terms.get("proposed_extension_years") or terms.get("extension_years"))
    annual = _safe_float(terms.get("proposed_annual_salary") or terms.get("extension_salary"))
    if years is None or annual is None:
        return [], [], [_unresolved(item, "Extension calculation requires proposed years and annual salary; Stage 8 will not invent market terms.", ["proposed_extension_years", "proposed_annual_salary"], [contract.player_id])], [], []
    start = (contract.season or ctx.context.requested_season) + max(contract.years_remaining or 0, 0)
    schedule = {str(start + offset): _money(annual) for offset in range(min(years, MAX_SEASONS))}
    inputs = [_input("extension_years", "contract_terms.proposed_extension_years", years), _input("annual_salary", "contract_terms.proposed_annual_salary", annual)]
    return [_result(item, schedule, "salary_by_season", "extension_schedule: proposed annual salary for supplied extension years", inputs, exact=True, related=[contract.player_id])], [], [], [], []


def _future_commitment(item: _WorkItem, ctx: _CalculationContext):
    contracts = _scoped_contracts(ctx.evidence_packet, ctx.context.league_team_id)
    if not contracts:
        return [], [], [_unresolved(item, "Contract evidence is missing for future salary commitment.", ["contract_evidence"])], [], []
    by_season: dict[str, float] = {}
    missing = []
    for contract in contracts[:MAX_PLAYERS]:
        if contract.contract_operational_season is None and not contract.future_obligations:
            if contract.salary is None or contract.years_remaining is None:missing.append(contract.player_id);continue
            start=contract.season or ctx.context.requested_season
            for offset in range(min(max(contract.years_remaining,0),MAX_SEASONS)):
                season=str(start+offset);by_season[season]=_money(by_season.get(season,0)+contract.salary)
            continue
        if contract.contract_status=="active" and contract.salary is not None and contract.contract_operational_season is not None:
            season=str(contract.contract_operational_season);by_season[season]=_money(by_season.get(season,0)+contract.salary)
        for obligation in contract.future_obligations:
            season=str(obligation.get("season"));salary=_safe_float(obligation.get("salary"))
            if not season or salary is None:missing.append(contract.player_id);continue
            by_season[season]=_money(by_season.get(season,0)+salary)
    if missing:
        return [], [], [_unresolved(item, "Some contracts are missing salary or years remaining.", ["contract_salary", "years_remaining"], missing)], [], []
    return [_result(item, by_season, "salary_by_season", "future_salary_commitment: normalized operational and scheduled obligations", [_input("contracts", "contract_evidence", len(contracts))], related=[ctx.context.league_team_id])], [], [], [], []


def _contract_years(item: _WorkItem, ctx: _CalculationContext):
    contracts = _scoped_contracts(ctx.evidence_packet, ctx.context.league_team_id)
    if not contracts:
        return [], [], [_unresolved(item, "Contract evidence is missing.", ["contract_evidence"])], [], []
    values = {}
    for contract in contracts[:MAX_PLAYERS]:
        if contract.years_remaining is None:
            return [], [], [_unresolved(item, "Contract years remaining is missing.", ["years_remaining"], [contract.player_id])], [], []
        values[contract.player_id] = contract.years_remaining
    return [_result(item, values, "years", "contract_years_remaining: verified contract field", [_input("contracts", "contract_evidence", len(values))], related=list(values))], [], [], [], []


def _roster_count(item: _WorkItem, ctx: _CalculationContext):
    team = _team_for(ctx.evidence_packet, ctx.context.league_team_id)
    if not team:
        return [], [], [_unresolved(item, "Team roster evidence is missing.", ["team_evidence"])], [], []
    unique_players = _dedupe_strings(team.roster_player_ids)
    value = {
        "active_roster_count": len(unique_players),
        "taxi_count": _summary_int(team.roster_summary, "taxi_count"),
        "ir_count": _summary_int(team.roster_summary, "ir_count"),
        "bench_count": _summary_int(team.roster_summary, "bench_count"),
    }
    return [_result(item, value, "players", "live_roster_status_count: distinct roster player ids with status summaries when present", [_input("roster_player_ids", "team_evidence.roster_player_ids", len(unique_players))], related=[team.league_team_id])], [], [], [], []


def _post_roster_count(item: _WorkItem, ctx: _CalculationContext):
    base = _team_for(ctx.evidence_packet, ctx.context.league_team_id)
    if not base:
        return [], [], [_unresolved(item, "Team roster evidence is missing.", ["team_evidence"])], [], []
    outgoing, incoming = _trade_player_sides(ctx)
    if not outgoing and not incoming and item.calculation_type != CalculationType.POST_ACQUISITION_ROSTER_ROOM.value:
        return [], [], [_unresolved(item, "No interpreted transaction assets are available.", ["included_assets"])], [], []
    current = len(_dedupe_strings(base.roster_player_ids))
    incoming_count = len(incoming)
    outgoing_count = len(outgoing)
    if item.calculation_type == CalculationType.POST_ACQUISITION_ROSTER_ROOM.value:
        incoming_count = max(incoming_count, len(ctx.evidence_packet.free_agent_evidence))
    post = current - outgoing_count + incoming_count
    value = {"current_count": current, "incoming_count": incoming_count, "outgoing_count": outgoing_count, "post_count": post, "slot_delta": incoming_count - outgoing_count}
    result = _result(item, value, "players", "post_transaction_roster_count: current - outgoing + incoming", [_input("current_count", "team_evidence.roster_player_ids", current), _input("incoming_count", "interpreted_assets.incoming", incoming_count), _input("outgoing_count", "interpreted_assets.outgoing", outgoing_count)], related=[base.league_team_id])
    return [result], [_scenario(ctx, "proposed_roster_change", [result], complete=True)], [], [], []


def _position_count(item: _WorkItem, ctx: _CalculationContext):
    players = _scoped_players(ctx.evidence_packet, ctx.context.league_team_id)
    if not players:
        return [], [], [_unresolved(item, "Player roster evidence is missing.", ["player_evidence"])], [], []
    counts: dict[str, int] = {}
    warnings = []
    for player in _dedupe_players(players):
        pos = _normalize_position(player.position)
        if not pos:
            warnings.append(f"unknown_position_for_{player.player_id}")
            continue
        counts[pos] = counts.get(pos, 0) + 1
    return [_result(item, counts, "players_by_position", "position_count: normalized verified player positions", [_input("players", "player_evidence", len(players))], related=[p.player_id for p in players])], [], [], [], warnings


def _age_profile(item: _WorkItem, ctx: _CalculationContext):
    players = _scoped_players(ctx.evidence_packet, ctx.context.league_team_id)
    if not players:
        return [], [], [_unresolved(item, "Player evidence is missing for age calculation.", ["player_evidence"])], [], []
    ages = [_safe_float(player.age) for player in _dedupe_players(players)]
    ages = [age for age in ages if age is not None and age > 0]
    if not ages:
        return [], [], [_unresolved(item, "No verified player ages are available.", ["player_age"])], [], []
    coverage = round(len(ages) / max(len(_dedupe_players(players)), 1), 2)
    value = {"average_age": round(sum(ages) / len(ages), 2), "median_age": round(median(ages), 2), "sample_size": len(ages), "coverage": coverage}
    warnings = ["age_coverage_incomplete"] if coverage < 0.8 else []
    return [_result(item, value, "years", "average_roster_age: average/median from verified non-missing ages", [_input("age_sample_size", "player_evidence.age", len(ages))], exact=coverage == 1, estimated=coverage < 1, confidence="medium" if coverage >= 0.8 else "low")], [], [], [], warnings


def _player_value(item: _WorkItem, ctx: _CalculationContext):
    players = _dedupe_players(ctx.evidence_packet.player_evidence)
    if not players:
        return [], [], [_unresolved(item, "Player evidence is missing for value calculation.", ["player_evidence"])], [], []
    values = {}
    missing = []
    for player in players[:MAX_PLAYERS]:
        value = _value_from_player(player)
        if value is None:
            missing.append(player.player_id)
            continue
        values[player.player_id] = value
    if missing and item.required:
        unresolved = [_unresolved(item, "One or more players are missing compatible stored value fields.", ["league_relative_value.overall_value_score", "strategic_profile.asset_score"], missing)]
    else:
        unresolved = []
    if not values:
        return [], [], unresolved or [_unresolved(item, "No compatible stored player values are available.", ["stored_player_value"])], [], []
    exact = len(missing) == 0
    return [_result(item, values, "value_points", "stored_league_relative_or_strategic_value", [_input("valued_players", "player_evidence.league_relative_value", len(values))], exact=exact, estimated=not exact, confidence="high" if exact else "low", related=list(values))], [], unresolved, [], ["partial_player_value_subtotal"] if missing else []


def _value_delta(item: _WorkItem, ctx: _CalculationContext):
    outgoing_assets, incoming_assets = _trade_player_sides(ctx)
    if not outgoing_assets and not incoming_assets:
        return [], [], [_unresolved(item, "No interpreted trade sides are available for value delta.", ["included_assets"])], [], []
    players = {player.player_id: player for player in ctx.evidence_packet.player_evidence}
    missing = []
    out_values = []
    in_values = []
    for asset in outgoing_assets:
        value = _value_from_player(players.get(asset.canonical_id or ""))
        if value is None:
            missing.append(asset.canonical_id or asset.label or "unknown")
        else:
            out_values.append(value)
    for asset in incoming_assets:
        value = _value_from_player(players.get(asset.canonical_id or ""))
        if value is None:
            missing.append(asset.canonical_id or asset.label or "unknown")
        else:
            in_values.append(value)
    value = {"value_sent": _money(sum(out_values)), "value_received": _money(sum(in_values)), "value_delta": _money(sum(in_values) - sum(out_values)), "complete": not missing}
    result = _result(item, value, "value_points", "trade_value_delta: compatible stored values received - sent", [_input("outgoing_values", "player_evidence.outgoing_values", out_values), _input("incoming_values", "player_evidence.incoming_values", in_values)], exact=not missing, estimated=bool(missing), confidence="high" if not missing else "low", related=_dedupe_strings([a.canonical_id for a in outgoing_assets + incoming_assets]))
    unresolved = [_unresolved(item, "Missing asset values are not treated as zero.", ["stored_player_value"], missing)] if missing and item.required else []
    warnings = ["partial_value_delta"] if missing else []
    scenario = _scenario(ctx, "proposed_trade_value", [result], complete=not missing, warnings=warnings)
    return [result], [scenario], unresolved, [], warnings


def _contract_efficiency(item: _WorkItem, ctx: _CalculationContext):
    players = {player.player_id: player for player in ctx.evidence_packet.player_evidence}
    contracts = ctx.evidence_packet.contract_evidence
    if not contracts:
        return [], [], [_unresolved(item, "Contract evidence is missing for efficiency calculation.", ["contract_evidence"])], [], []
    values = {}
    unresolved = []
    for contract in contracts[:MAX_PLAYERS]:
        value = _value_from_player(players.get(contract.player_id))
        if value is None or contract.salary in (None, 0):
            unresolved.append(_unresolved(item, "Contract efficiency requires stored value and non-zero salary.", ["stored_player_value", "contract_salary"], [contract.player_id]))
            continue
        values[contract.player_id] = round(value / contract.salary, 2)
    if not values:
        return [], [], unresolved or [_unresolved(item, "No contract efficiency inputs are available.", ["stored_player_value", "contract_salary"])], [], []
    result = _result(item, values, "value_per_salary", "contract_efficiency: stored player value / salary", [_input("contracts", "contract_evidence", len(contracts))], exact=not unresolved, estimated=bool(unresolved), confidence="high" if not unresolved else "low", related=list(values))
    return [result], [], unresolved, [], ["partial_contract_efficiency"] if unresolved else []


def _draft_capital(item: _WorkItem, ctx: _CalculationContext):
    picks = ctx.evidence_packet.draft_pick_evidence
    if not picks:
        return [], [], [_unresolved(item, "Draft-pick evidence is missing.", ["draft_pick_evidence"])], [], []
    by_season: dict[str, dict[str, int]] = {}
    premium = 0
    for pick in picks[:MAX_ASSETS]:
        season = str(pick.season or "unknown")
        round_key = f"round_{pick.round or 'unknown'}"
        by_season.setdefault(season, {})
        by_season[season][round_key] = by_season[season].get(round_key, 0) + 1
        if pick.round == 1:
            premium += 1
    value = {"pick_count": len(picks[:MAX_ASSETS]), "by_season": by_season, "premium_pick_count": premium}
    warnings = ["draft_pick_count_capped"] if len(picks) > MAX_ASSETS else []
    return [_result(item, value, "picks", "draft_capital_total: verified pick count by season and round", [_input("draft_picks", "draft_pick_evidence", len(picks[:MAX_ASSETS]))], related=[pick.canonical_pick_id or "" for pick in picks[:MAX_ASSETS]])], [], [], [], warnings


def _pick_value(item: _WorkItem, ctx: _CalculationContext):
    picks = ctx.evidence_packet.draft_pick_evidence
    if not picks:
        return [], [], [_unresolved(item, "Draft-pick evidence is missing.", ["draft_pick_evidence"])], [], []
    values = {}
    missing = []
    for pick in picks[:MAX_ASSETS]:
        pick_id = pick.canonical_pick_id or f"{pick.season}_{pick.round}.{pick.slot}"
        stored = _safe_float(getattr(pick, "pick_value", None))
        if stored is None:
            missing.append(pick_id)
        else:
            values[pick_id] = stored
    if not values:
        return [], [], [_unresolved(item, "No verified pick-value source exists; Stage 8 will not import a public chart.", ["verified_pick_value"])], [], []
    return [_result(item, values, "value_points", "verified_stored_pick_value", [_input("valued_picks", "draft_pick_evidence.pick_value", len(values))], exact=not missing, estimated=bool(missing), confidence="high" if not missing else "low")], [], [_unresolved(item, "Some picks are missing verified value.", ["verified_pick_value"], missing)] if missing and item.required else [], [], []


def _team_brain_value(item: _WorkItem, ctx: _CalculationContext):
    teams = ctx.evidence_packet.team_evidence
    if not teams:
        return [], [], [_unresolved(item, "Team-brain or roster summary evidence is missing.", ["team_evidence"])], [], []
    values = {}
    for team in teams:
        summary = dict(team.team_brain_summary or {})
        if item.calculation_type == CalculationType.COMPETITIVE_WINDOW_FIT.value:
            value = summary.get("team_direction") or summary.get("competitive_window") or summary.get("window") or summary.get("context_summary")
        elif item.calculation_type == CalculationType.TEAM_STRENGTH.value:
            value = summary.get("championship_window_score") or summary.get("team_strength") or summary.get("avg_asset_score")
        elif item.calculation_type == CalculationType.POSITIONAL_NEED.value:
            value = summary.get("position_needs") or team.positional_summary.get("position_needs")
        elif item.calculation_type == CalculationType.POSITIONAL_SURPLUS.value:
            value = summary.get("position_strengths") or team.positional_summary.get("position_strengths")
        else:
            value = summary or team.roster_summary
        if value not in (None, "", [], {}):
            values[team.league_team_id] = value
    if not values:
        return [], [], [_unresolved(item, "No verified stored team-brain value is available for this calculation.", ["team_brain_summary"])], [], []
    return [_result(item, values, "stored_descriptor", "verified_stored_team_brain_field", [_input("teams", "team_evidence.team_brain_summary", len(values))], related=list(values))], [], [], [], []


def _lineup_projection(item: _WorkItem, ctx: _CalculationContext):
    lineup = _lineup_for(ctx.evidence_packet, ctx.context.league_team_id)
    if not lineup:
        return [], [], [_unresolved(item, "Trusted lineup evidence is missing.", ["lineup_evidence"])], [], []
    projections = lineup.projections or {}
    if item.calculation_type == CalculationType.LINEUP_SLOT_IMPACT.value:
        value = {"starter_count": len(_dedupe_strings(lineup.starter_player_ids)), "bench_count": len(_dedupe_strings(lineup.bench_player_ids)), "locked": bool(lineup.eligible_positions.get("locked"))}
        return [_result(item, value, "players", "lineup_slot_impact: verified starter and bench ids", [_input("lineup", "lineup_evidence", lineup.week)], related=[lineup.league_team_id])], [], [], [], list(lineup.warnings)
    if not projections:
        return [], [], [_unresolved(item, "Projection evidence is unavailable; Stage 8 will not fabricate projected points.", ["lineup_evidence.projections"])], [], list(lineup.warnings)
    value = _compact({key: projections.get(key) for key in ("projected_points", "floor", "ceiling", "lineup_delta")})
    if not value:
        return [], [], [_unresolved(item, "Lineup projections do not include supported point, floor, ceiling, or delta fields.", ["projected_points", "floor", "ceiling"])], [], list(lineup.warnings)
    return [_result(item, value, "points", "verified_lineup_projection_fields", [_input("projections", "lineup_evidence.projections", list(value))], exact=False, estimated=True, confidence="medium", related=[lineup.league_team_id])], [], [], [CalculationAssumption("projection_source", "Lineup values are estimates from trusted projection evidence.", "lineup_evidence.projections", True)], list(lineup.warnings)


def _unsupported_calculation(item: _WorkItem, _ctx: _CalculationContext):
    return [], [], [_unresolved(item, f"No deterministic Stage 8 formula or verified stored source is registered for {item.calculation_type}.", [item.calculation_type])], [], []


def _finalize_packet(
    plan: DecisionPlan,
    results: list[CalculationResult],
    scenarios: list[ScenarioCalculation],
    unresolved: list[UnresolvedCalculation],
    assumptions: list[CalculationAssumption],
    warnings: list[str],
    work_items: list[_WorkItem],
) -> CalculationPacket:
    results = _dedupe_results(results)
    scenarios = _dedupe_scenarios(scenarios[:MAX_SCENARIOS])
    unresolved = _dedupe_unresolved(unresolved)
    assumptions = _dedupe_assumptions(assumptions)
    warnings = _dedupe_strings(warnings)
    required_types = {item.calculation_type for item in work_items if item.required}
    completed_required = {result.calculation_type for result in results if result.required and result.status in {CalculationStatus.SUCCESS.value, CalculationStatus.ESTIMATED.value}}
    blocking_unresolved = [item for item in unresolved if item.blocking]
    required_complete = required_types.issubset(completed_required) and not blocking_unresolved
    any_estimated = any(result.estimated or result.status == CalculationStatus.ESTIMATED.value for result in results)
    reduced = any_estimated or bool([item for item in unresolved if not item.blocking]) or any(a.material for a in assumptions)
    if not results and unresolved:
        status = CalculationExecutionStatus.PARTIAL.value
    elif not required_complete:
        status = CalculationExecutionStatus.PARTIAL.value
    elif reduced:
        status = CalculationExecutionStatus.REDUCED.value
    elif warnings:
        status = CalculationExecutionStatus.COMPLETE_WITH_WARNINGS.value
    else:
        status = CalculationExecutionStatus.COMPLETE.value
    if not results and not unresolved:
        status = CalculationExecutionStatus.NOT_APPLICABLE.value
    confidence = "low" if blocking_unresolved else "medium" if reduced or warnings else "high"
    return CalculationPacket(
        plan_type=plan.plan_type,
        decision_engine=plan.decision_engine,
        results=results,
        scenario_results=scenarios,
        unresolved_calculations=unresolved,
        assumptions=assumptions,
        warnings=warnings,
        required_calculations_complete=required_complete,
        reduced_mode=reduced,
        execution_status=status,
        confidence=confidence,
    )


def _result(
    item: _WorkItem,
    value: Any,
    unit: str | None,
    method: str,
    inputs: list[CalculationInputRef],
    *,
    exact: bool = True,
    estimated: bool = False,
    confidence: str = "high",
    related: list[str] | None = None,
) -> CalculationResult:
    return CalculationResult(
        calculation_type=item.calculation_type,
        status=CalculationStatus.ESTIMATED.value if estimated else CalculationStatus.SUCCESS.value,
        value=_compact(value),
        unit=unit,
        output_key=item.output_key,
        method=method,
        formula_version=FORMULA_VERSION,
        inputs=inputs,
        assumptions=[],
        exact=exact,
        estimated=estimated,
        required=item.required,
        confidence=confidence,
        related_entity_ids=_dedupe_strings(related or []),
        satisfies_rule_calculation_refs=[item.source_ref] if item.source_ref.startswith("rules_evaluation") else [],
    )


def _unresolved(item: _WorkItem, explanation: str, missing: list[str], related: list[str] | None = None) -> UnresolvedCalculation:
    return UnresolvedCalculation(item.calculation_type, explanation, item.required, missing, _dedupe_strings(related or []))


def _input(input_type: str, source_ref: str, value: Any, verified: bool = True) -> CalculationInputRef:
    return CalculationInputRef(input_type, source_ref, _compact(value), verified)


def _scenario(ctx: _CalculationContext, scenario_type: str, results: list[CalculationResult], *, complete: bool, warnings: list[str] | None = None) -> ScenarioCalculation:
    changed_players = [asset.canonical_id for asset in ctx.interpreted_question.included_assets if asset.asset_type == "player" and asset.canonical_id]
    changed_picks = [asset.canonical_id or asset.label for asset in ctx.interpreted_question.included_assets if asset.asset_type == "draft_pick"]
    return ScenarioCalculation(
        scenario_id=f"{scenario_type}:1",
        scenario_type=scenario_type,
        label=scenario_type.replace("_", " ").title(),
        results=results,
        rule_status_before=ctx.rules_evaluation.overall_status,
        projected_rule_status_after=None,
        changed_team_ids=_dedupe_strings([ctx.context.league_team_id]),
        changed_player_ids=_dedupe_strings(changed_players),
        changed_pick_ids=_dedupe_strings(changed_picks),
        complete=complete,
        warnings=_dedupe_strings(warnings or []),
    )


def _normalize_calculation_type(value: str) -> str:
    text = str(value or "").strip().lower()
    return CALCULATION_ALIASES.get(text, text)


def _allowed_team_ids(context: AssistantRequestContext, plan: DecisionPlan) -> set[str]:
    ids = {context.league_team_id}
    for request in plan.retrieval_requests:
        ids.update(_dedupe_strings(request.team_ids))
    return ids


def _is_transaction_plan(plan: DecisionPlan) -> bool:
    return plan.plan_type in {"trade_evaluation_plan", "trade_construction_plan", "roster_move_plan", "free_agent_plan"}


def _cap_for(ctx: _CalculationContext, team_id: str, season: int | None = None) -> CapEvidence | None:
    requested = season or ctx.context.requested_season
    candidates = [cap for cap in ctx.evidence_packet.cap_evidence if cap.league_team_id == team_id]
    for cap in candidates:
        if cap.season == requested:
            return cap
    return candidates[0] if candidates else None


def _team_for(evidence: EvidencePacket, team_id: str) -> TeamEvidence | None:
    for team in evidence.team_evidence:
        if team.league_team_id == team_id:
            return team
    return None


def _lineup_for(evidence: EvidencePacket, team_id: str) -> LineupEvidence | None:
    for lineup in evidence.lineup_evidence:
        if lineup.league_team_id == team_id:
            return lineup
    return None


def _scoped_players(evidence: EvidencePacket, team_id: str) -> list[PlayerEvidence]:
    return [player for player in evidence.player_evidence if player.fantasy_team_id in {team_id, None}]


def _scoped_contracts(evidence: EvidencePacket, team_id: str) -> list[ContractEvidence]:
    return [contract for contract in evidence.contract_evidence if contract.league_team_id in {team_id, None}]


def _contract_for(evidence: EvidencePacket, player_id: str | None) -> ContractEvidence | None:
    if not player_id:
        return None
    for contract in evidence.contract_evidence:
        if contract.player_id == player_id:
            return contract
    return None


def _trade_player_sides(ctx: _CalculationContext) -> tuple[list[AssetRef], list[AssetRef]]:
    outgoing: list[AssetRef] = []
    incoming: list[AssetRef] = []
    for asset in _player_assets(ctx.interpreted_question.included_assets[:MAX_ASSETS]):
        side = str(asset.ownership_context or "").lower()
        if side in {"outgoing", "send", "sent", "mine", "from_active_team", ctx.context.league_team_id.lower()}:
            outgoing.append(asset)
        elif side in {"incoming", "receive", "received", "target", "to_active_team"}:
            incoming.append(asset)
        else:
            player = next((item for item in ctx.evidence_packet.player_evidence if item.player_id == asset.canonical_id), None)
            if player and player.fantasy_team_id == ctx.context.league_team_id:
                outgoing.append(asset)
            else:
                incoming.append(asset)
    return outgoing, incoming


def _trade_contract_sides(ctx: _CalculationContext) -> tuple[list[ContractEvidence], list[ContractEvidence]]:
    outgoing_assets, incoming_assets = _trade_player_sides(ctx)
    outgoing = [_contract_for(ctx.evidence_packet, asset.canonical_id) for asset in outgoing_assets]
    incoming = [_contract_for(ctx.evidence_packet, asset.canonical_id) for asset in incoming_assets]
    return [item for item in outgoing if item], [item for item in incoming if item]


def _player_assets(assets: list[AssetRef]) -> list[AssetRef]:
    return [asset for asset in assets if asset.asset_type == "player" and asset.canonical_id]


def _cap_hit(contract: ContractEvidence) -> float:
    salary = _safe_float(contract.salary) or 0.0
    status = str(contract.contract_status or "Active").strip().lower()
    if status == "taxi":
        return _money(salary * (2 / 3))
    if status == "ir":
        return _money(salary * 0.5)
    if status in {"inactive", "expired", "void"}:
        return 0.0
    return _money(salary)


def _value_from_player(player: PlayerEvidence | None) -> float | None:
    if not player:
        return None
    for source in (player.league_relative_value, player.strategic_profile):
        for key in ("overall_value_score", "value_score", "asset_score", "dynasty_score", "win_now_score"):
            value = _safe_float(source.get(key) if isinstance(source, dict) else None)
            if value is not None:
                return round(value, 2)
    return None


def _normalize_position(position: str | None) -> str | None:
    text = str(position or "").strip().upper()
    aliases = {"DEF": "DST", "D/ST": "DST", "PK": "K"}
    text = aliases.get(text, text)
    return text if text in {"QB", "RB", "WR", "TE", "K", "DST", "FLEX", "SUPERFLEX"} else None


def _summary_int(team_summary: dict[str, Any], key: str) -> int | None:
    value = _safe_int(team_summary.get(key) if isinstance(team_summary, dict) else None)
    return value if value is not None else None


def _with_type(item: _WorkItem, calculation_type: str) -> _WorkItem:
    return _WorkItem(calculation_type, item.required, item.reason, item.input_refs, calculation_type, item.source_ref)


def _money(value: Any) -> float:
    number = _safe_float(value) or 0.0
    return round(number, 2)


def _safe_float(value: Any) -> float | None:
    try:
        text = str(value).strip().replace("$", "").replace(",", "")
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return float(text)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return int(float(text))
    except Exception:
        return None


def _dedupe_players(players: list[PlayerEvidence]) -> list[PlayerEvidence]:
    out = []
    seen = set()
    for player in players:
        if player.player_id in seen:
            continue
        seen.add(player.player_id)
        out.append(player)
    return out


def _dedupe_results(items: list[CalculationResult]) -> list[CalculationResult]:
    out = []
    seen = set()
    for item in items:
        key = (item.calculation_type, item.output_key, tuple(item.related_entity_ids))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_scenarios(items: list[ScenarioCalculation]) -> list[ScenarioCalculation]:
    out = []
    seen = set()
    for item in items:
        if item.scenario_id in seen:
            continue
        seen.add(item.scenario_id)
        out.append(item)
    return out


def _dedupe_unresolved(items: list[UnresolvedCalculation]) -> list[UnresolvedCalculation]:
    out = []
    seen = set()
    for item in items:
        key = (item.calculation_type, item.explanation, tuple(item.related_entity_ids))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_assumptions(items: list[CalculationAssumption]) -> list[CalculationAssumption]:
    out = []
    seen = set()
    for item in items:
        key = (item.assumption_type, item.explanation, item.source)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_strings(items: list[str | None]) -> list[str]:
    out = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


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


CALCULATION_HANDLERS: dict[str, CalculationHandler] = {
    CalculationType.CURRENT_CAP_TOTAL.value: _current_cap,
    CalculationType.AVAILABLE_CAP.value: _current_cap,
    CalculationType.CAP_FLEXIBILITY.value: _current_cap,
    CalculationType.SALARY_DELTA.value: _salary_delta,
    CalculationType.TRADE_CAP_DELTA.value: _post_transaction_cap,
    CalculationType.POST_TRANSACTION_CAP.value: _post_transaction_cap,
    CalculationType.POST_TRANSACTION_CAP_TOTAL.value: _post_transaction_cap,
    CalculationType.DEAD_CAP_IMPACT.value: _dead_cap,
    CalculationType.RELEASE_SAVINGS.value: _release_savings,
    CalculationType.EXTENSION_SCHEDULE.value: _extension_schedule,
    CalculationType.FUTURE_SALARY_COMMITMENT.value: _future_commitment,
    CalculationType.CONTRACT_YEARS_REMAINING.value: _contract_years,
    CalculationType.CONTRACT_ROLLOVER.value: _contract_years,
    CalculationType.CURRENT_ROSTER_COUNT.value: _roster_count,
    CalculationType.ROSTER_DEPTH.value: _roster_count,
    CalculationType.POST_TRANSACTION_ROSTER_COUNT.value: _post_roster_count,
    CalculationType.POST_ACQUISITION_ROSTER_ROOM.value: _post_roster_count,
    CalculationType.TRADE_ROSTER_DELTA.value: _post_roster_count,
    CalculationType.ROSTER_SLOT_DELTA.value: _post_roster_count,
    CalculationType.POSITION_COUNT.value: _position_count,
    CalculationType.POST_TRANSACTION_POSITION_COUNT.value: _position_count,
    CalculationType.POSITIONAL_NEED.value: _team_brain_value,
    CalculationType.POSITIONAL_SURPLUS.value: _team_brain_value,
    CalculationType.AVERAGE_ROSTER_AGE.value: _age_profile,
    CalculationType.POSITION_GROUP_AGE.value: _age_profile,
    CalculationType.AGE_PROFILE.value: _age_profile,
    CalculationType.AGE_CONCENTRATION.value: _age_profile,
    CalculationType.PLAYER_VALUE.value: _player_value,
    CalculationType.LEAGUE_RELATIVE_VALUE.value: _player_value,
    CalculationType.VALUE_COMPARISON.value: _player_value,
    CalculationType.VALUE_DELTA.value: _value_delta,
    CalculationType.TRADE_VALUE_DELTA.value: _value_delta,
    CalculationType.TRADE_VALUE_IN.value: _value_delta,
    CalculationType.TRADE_VALUE_OUT.value: _value_delta,
    CalculationType.TRADE_FAIRNESS.value: _value_delta,
    CalculationType.CONTRACT_EFFICIENCY.value: _contract_efficiency,
    CalculationType.TRADE_CONTRACT_DELTA.value: _contract_years,
    CalculationType.REPLACEMENT_VALUE.value: _player_value,
    CalculationType.PICK_VALUE.value: _pick_value,
    CalculationType.PICK_RANGE_ESTIMATE.value: _pick_value,
    CalculationType.DRAFT_CAPITAL_TOTAL.value: _draft_capital,
    CalculationType.DRAFT_CAPITAL_DELTA.value: _draft_capital,
    CalculationType.COMPETITIVE_WINDOW_FIT.value: _team_brain_value,
    CalculationType.TEAM_STRENGTH.value: _team_brain_value,
    CalculationType.FUTURE_VALUE.value: _team_brain_value,
    CalculationType.IMMEDIATE_PRODUCTION.value: _player_value,
    CalculationType.RISK_PROFILE.value: _team_brain_value,
    CalculationType.DEPTH_SCORE.value: _team_brain_value,
    CalculationType.CONTRACT_EXPOSURE.value: _future_commitment,
    CalculationType.TEAM_STRENGTH_COMPARISON.value: _team_brain_value,
    CalculationType.LINEUP_PROJECTION.value: _lineup_projection,
    CalculationType.FLOOR_CEILING.value: _lineup_projection,
    CalculationType.LINEUP_DELTA.value: _lineup_projection,
    CalculationType.LINEUP_SLOT_COUNT.value: _lineup_projection,
    CalculationType.LINEUP_SLOT_IMPACT.value: _lineup_projection,
    CalculationType.YEAR_BY_YEAR_ROSTER_OUTLOOK.value: _future_commitment,
}
