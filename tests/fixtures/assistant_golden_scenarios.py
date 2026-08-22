from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gm_assistant.evidence import ProviderResult, RetrievalStatus
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


LEAGUE_ID = "league-golden"
OTHER_LEAGUE_ID = "league-other"
TEAM_ID = "team-condor"
OTHER_TEAM_ID = "team-rival"
OTHER_LEAGUE_TEAM_ID = "team-other-league"
USER_ID = "user-condor"


@dataclass(frozen=True)
class GoldenScenarioExpectation:
    interpreted_intent: str | None = None
    plan_type: str | None = None
    required_evidence_types: list[str] = field(default_factory=list)
    rule_status: str | None = None
    required_calculation_types: list[str] = field(default_factory=list)
    decision_action: str | None = None
    validation_status: str | None = None
    answer_mode: str | None = None
    response_status: str | None = None
    approved_for_action: bool | None = None
    must_include_concepts: list[str] = field(default_factory=list)
    must_exclude_concepts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GoldenScenario:
    name: str
    question: str
    expectation: GoldenScenarioExpectation
    rendered_text: str | None = None
    prior_players: list[str] = field(default_factory=list)
    prior_scenario: dict[str, Any] | None = None


COVERAGE_MATRIX = {
    "identity_team": {"identity", "interpretation", "planning", "evidence", "answer_packet", "fallback"},
    "roster_running_backs": {"identity", "facts", "roster", "evidence", "calculations", "fallback"},
    "taxi_players": {"facts", "roster", "evidence", "answer_packet"},
    "ir_players": {"facts", "roster", "evidence", "answer_packet"},
    "cap_space": {"facts", "cap", "evidence", "calculations", "answer_packet"},
    "contract_years": {"contracts", "evidence", "calculations", "validation"},
    "taxi_rule": {"rules", "evidence", "rules_evaluation", "answer_packet"},
    "player_eval": {"conversation", "player_evaluation", "decision", "validation"},
    "player_comparison": {"comparison", "decision", "validation", "rendered_validation"},
    "trade_reject": {"trade", "rules", "calculations", "decision", "validation"},
    "trade_follow_up": {"conversation", "trade", "planning", "validation"},
    "trade_discovery": {"trade_discovery", "scope", "evidence", "validation"},
    "trade_construction": {"trade_construction", "owned_assets", "validation"},
    "roster_strategy": {"strategy", "team_brain", "cap", "draft", "decision"},
    "draft_picks": {"draft", "facts", "evidence", "answer_packet"},
    "draft_recommendation_limited": {"draft", "missing_data", "limited_answer"},
    "free_agents": {"free_agent", "availability", "scope", "validation"},
    "lineup_limited": {"lineup", "missing_projection", "limited_answer"},
    "ambiguous_player": {"ambiguity", "clarification", "no_speculation"},
    "unsupported_execution": {"unsupported", "execution_guard", "fallback"},
    "adversarial_scope": {"scope", "isolation", "no_leakage", "fallback"},
}


def make_context(**overrides: Any) -> AssistantRequestContext:
    data = {
        "user_id": USER_ID,
        "league_id": LEAGUE_ID,
        "league_team_id": TEAM_ID,
        "membership_id": "membership-condor",
        "role": "owner",
        "current_season": 2026,
        "requested_season": 2026,
        "permission_scopes": (TEAM_ADVICE, LEAGUE_PUBLIC_READ),
        "conversation_id": "conversation-golden",
        "team_name": "Condor Dynasty",
        "owner_name": "Tommy",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


TEAM_CONTEXT = {
    "team_brain": {
        "team_direction": "CONTEND_NOW",
        "competitive_window": "2026-2027",
        "position_strengths": ["WR"],
        "position_needs": ["RB", "TE"],
        "core_players": ["Garrett Wilson", "Breece Hall"],
        "contract_problems": ["High WR salary concentration"],
        "championship_window_score": 82,
    },
    "gm_memory": {
        "current_focus": "contend without trading first-round picks",
        "gm_style": "balanced",
        "trade_style": "patient",
    },
}


OWNER_PREFERENCES = {
    "current_focus": "contend without trading first-round picks",
    "gm_style": "balanced",
    "trade_style": "patient",
    "non_negotiables": ["do_not_trade_first_round_pick"],
}


PLAYER_ROWS = [
    {
        "league_id": LEAGUE_ID,
        "league_team_id": TEAM_ID,
        "sleeper_id": "p-garrett",
        "player_name": "Garrett Wilson",
        "position": "WR",
        "age": 25.2,
        "strategic_label": "CORE",
        "league_value_tier": "TOP_STARTER",
        "value_score": 86,
        "status": "active",
    },
    {
        "league_id": LEAGUE_ID,
        "league_team_id": TEAM_ID,
        "sleeper_id": "p-breece",
        "player_name": "Breece Hall",
        "position": "RB",
        "age": 25.1,
        "strategic_label": "CORE",
        "league_value_tier": "ELITE",
        "value_score": 91,
        "status": "active",
    },
    {
        "league_id": LEAGUE_ID,
        "league_team_id": TEAM_ID,
        "sleeper_id": "p-achane",
        "player_name": "De'Von Achane",
        "position": "RB",
        "age": 24.8,
        "strategic_label": "EXPENSIVE_STARTER",
        "league_value_tier": "HIGH_VARIANCE_STARTER",
        "value_score": 78,
        "status": "active",
    },
    {
        "league_id": LEAGUE_ID,
        "league_team_id": TEAM_ID,
        "sleeper_id": "p-lloyd",
        "player_name": "MarShawn Lloyd",
        "position": "RB",
        "age": 25,
        "strategic_label": "TAXI_HOLD",
        "league_value_tier": "DEPTH",
        "value_score": 42,
        "status": "taxi",
    },
    {
        "league_id": LEAGUE_ID,
        "league_team_id": TEAM_ID,
        "sleeper_id": "p-hock",
        "player_name": "T.J. Hockenson",
        "position": "TE",
        "age": 29,
        "strategic_label": "IR_STASH",
        "league_value_tier": "STARTER",
        "value_score": 66,
        "status": "ir",
    },
    {
        "league_id": LEAGUE_ID,
        "league_team_id": OTHER_TEAM_ID,
        "sleeper_id": "p-olave",
        "player_name": "Chris Olave",
        "position": "WR",
        "age": 26,
        "strategic_label": "CORE",
        "league_value_tier": "TOP_STARTER",
        "value_score": 82,
        "status": "active",
    },
    {
        "league_id": OTHER_LEAGUE_ID,
        "league_team_id": OTHER_LEAGUE_TEAM_ID,
        "sleeper_id": "p-cross",
        "player_name": "Cross League Star",
        "position": "RB",
        "value_score": 99,
        "status": "active",
    },
]


CONTRACT_ROWS = [
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "sleeper_player_id": "p-garrett", "player_name": "Garrett Wilson", "season": 2026, "salary": 24, "contract_years_left": 2, "dead_cap": 7, "status": "active"},
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "sleeper_player_id": "p-breece", "player_name": "Breece Hall", "season": 2026, "salary": 18, "contract_years_left": 1, "dead_cap": 4, "status": "active"},
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "sleeper_player_id": "p-achane", "player_name": "De'Von Achane", "season": 2026, "salary": 28, "contract_years_left": 3, "dead_cap": 10, "status": "active"},
    {"league_id": LEAGUE_ID, "league_team_id": OTHER_TEAM_ID, "sleeper_player_id": "p-olave", "player_name": "Chris Olave", "season": 2026, "salary": 21, "contract_years_left": 2, "dead_cap": 5, "status": "active"},
    {"league_id": OTHER_LEAGUE_ID, "league_team_id": OTHER_LEAGUE_TEAM_ID, "sleeper_player_id": "p-cross", "player_name": "Cross League Star", "season": 2026, "salary": 1, "contract_years_left": 5, "dead_cap": 0, "status": "active"},
]


ROSTER_ROWS = [
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "team_name": "Condor Dynasty", "owner_name": "Tommy", "roster_player_ids": ["p-garrett", "p-breece", "p-achane", "p-lloyd", "p-hock"], "roster_count": 22, "roster_limit": 24, "taxi_count": 1, "ir_count": 1},
    {"league_id": LEAGUE_ID, "league_team_id": OTHER_TEAM_ID, "team_name": "Rival Rebuild", "owner_name": "Riley", "roster_player_ids": ["p-olave"], "roster_count": 24, "roster_limit": 24},
    *PLAYER_ROWS,
]


TEAM_BRAIN_ROWS = [
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "team_name": "Condor Dynasty", "owner_name": "Tommy", "team_direction": "CONTEND_NOW", "position_strengths": ["WR"], "position_needs": ["RB", "TE"], "core_players": ["Garrett Wilson", "Breece Hall"], "championship_window_score": 82},
    {"league_id": LEAGUE_ID, "league_team_id": OTHER_TEAM_ID, "team_name": "Rival Rebuild", "owner_name": "Riley", "team_direction": "REBUILD", "position_strengths": ["WR"], "position_needs": ["QB", "RB"], "core_players": ["Chris Olave"], "championship_window_score": 48},
]


CAP_ROWS = [
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "season": 2026, "salary_cap": 250, "active_salary": 224, "dead_cap": 6, "available_cap": 20, "cap_space": 20, "future_salary": {"2027": 188}},
    {"league_id": LEAGUE_ID, "league_team_id": OTHER_TEAM_ID, "season": 2026, "salary_cap": 250, "active_salary": 231, "dead_cap": 3, "available_cap": 16, "cap_space": 16},
]


DRAFT_PICK_ROWS = [
    {"league_id": LEAGUE_ID, "canonical_pick_id": "2026_1.03", "season": 2026, "round": 1, "slot": 3, "current_owner_team_id": TEAM_ID, "original_team_id": OTHER_TEAM_ID, "status": "available"},
    {"league_id": LEAGUE_ID, "canonical_pick_id": "2027_2_condor", "season": 2027, "round": 2, "current_owner_team_id": TEAM_ID, "original_team_id": TEAM_ID, "status": "available"},
]


RULE_ROWS = [
    {"league_id": LEAGUE_ID, "rule_type": "taxi_eligibility", "season": 2026, "structured_value": {"rookie_draft_only": True, "max_taxi_players": 5}, "source_priority": 1, "verified": True},
    {"league_id": LEAGUE_ID, "rule_type": "ir_eligibility", "season": 2026, "structured_value": {"requires_ir_or_out_status": True}, "source_priority": 1, "verified": True},
    {"league_id": LEAGUE_ID, "rule_type": "trade_deadline", "season": 2026, "structured_value": {"date": "2026-11-15"}, "source_priority": 1, "verified": True},
    {"league_id": LEAGUE_ID, "rule_type": "roster_size_limit", "season": 2026, "structured_value": {"active_roster_limit": 24}, "source_priority": 1, "verified": True},
    {"league_id": LEAGUE_ID, "rule_type": "salary_cap_legality", "season": 2026, "structured_value": {"must_remain_under_cap": True}, "source_priority": 1, "verified": True},
]


LINEUP_ROWS = [
    {"league_id": LEAGUE_ID, "league_team_id": TEAM_ID, "season": 2026, "week": 1, "starter_player_ids": ["p-garrett", "p-breece"], "bench_player_ids": ["p-achane"], "eligible_positions": {"flex": ["RB", "WR", "TE"]}, "injuries": {}, "projections": {}}
]


FREE_AGENT_ROWS = [
    {"league_id": LEAGUE_ID, "sleeper_id": "p-fa-rb", "player_name": "Jaylen Wright", "position": "RB", "availability_verified": True, "expected_cost": {"salary": 3}, "status": "available"},
    {"league_id": LEAGUE_ID, "sleeper_id": "p-olave", "player_name": "Chris Olave", "position": "WR", "availability_verified": False, "status": "rostered"},
]


LEAGUE_ROWS = [
    {"league_id": LEAGUE_ID, "season": 2026, "league_size": 10, "league_status": "active", "scoring_summary": {"ppr": 1}, "roster_settings_summary": {"active_roster_limit": 24, "taxi_limit": 5}, "draft_settings_summary": {"rookie_draft_rounds": 5}},
]


class GoldenRetrievalProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_team_roster(self, _context, request): return self._result("get_team_roster", _scope_rows(ROSTER_ROWS, request))
    def get_team_brain(self, _context, request): return self._result("get_team_brain", _scope_rows(TEAM_BRAIN_ROWS, request))
    def get_team_roster_summary(self, _context, request): return self._result("get_team_roster_summary", _scope_rows(ROSTER_ROWS, request))
    def get_league_brain(self, _context, request): return self._result("get_league_brain", LEAGUE_ROWS)
    def get_team_brain_rankings(self, _context, request): return self._result("get_team_brain_rankings", TEAM_BRAIN_ROWS)
    def get_cap_summary(self, _context, request): return self._result("get_cap_summary", _scope_rows(CAP_ROWS, request))
    def get_draft_picks(self, _context, request): return self._result("get_draft_picks", _scope_rows(DRAFT_PICK_ROWS, request))
    def get_transactions(self, _context, request): return self._result("get_transactions", [])
    def get_player_profiles(self, _context, request): return self._result("get_player_profiles", _filter_players(PLAYER_ROWS, request.player_ids))
    def get_player_contracts(self, _context, request): return self._result("get_player_contracts", _filter_players(CONTRACT_ROWS, request.player_ids))
    def get_league_settings(self, _context, request): return self._result("get_league_settings", LEAGUE_ROWS)
    def get_rule_sources(self, _context, request): return self._result("get_rule_sources", RULE_ROWS)
    def get_lineup_sources(self, _context, request):
        if request.retrieval_type in {"injury_status", "weekly_projection_summary"}:
            return ProviderResult(RetrievalStatus.UNAVAILABLE.value, [], request.retrieval_type, warning=f"{request.retrieval_type} unavailable in golden fixture.")
        return self._result("get_lineup_sources", LINEUP_ROWS)
    def get_free_agent_sources(self, _context, request): return self._result("get_free_agent_sources", FREE_AGENT_ROWS)

    def _result(self, method: str, rows: list[dict[str, Any]]) -> ProviderResult:
        self.calls.append(method)
        return ProviderResult.success(rows, method)


class GoldenLookupClient:
    def table(self, name: str):
        return _LookupQuery(_TABLES.get(name, []))


class _LookupQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.filters: list[tuple[str, Any]] = []
        self._limit: int | None = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field: str, value: Any):
        self.filters.append((field, value))
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def execute(self):
        rows = [row for row in self.rows if all(str(row.get(field)) == str(value) for field, value in self.filters)]
        if self._limit is not None:
            rows = rows[: self._limit]
        return type("LookupResult", (), {"data": rows})()


_TABLES = {
    "player_strategic_profiles": PLAYER_ROWS,
    "league_relative_player_values": PLAYER_ROWS,
    "contracts": CONTRACT_ROWS,
    "league_teams": [
        {"id": TEAM_ID, "league_id": LEAGUE_ID, "team_name": "Condor Dynasty", "owner_name": "Tommy"},
        {"id": OTHER_TEAM_ID, "league_id": LEAGUE_ID, "team_name": "Rival Rebuild", "owner_name": "Riley"},
        {"id": OTHER_LEAGUE_TEAM_ID, "league_id": OTHER_LEAGUE_ID, "team_name": "Other League Team", "owner_name": "Other"},
    ],
}


def golden_scenarios() -> list[GoldenScenario]:
    return [
        GoldenScenario("identity_team", "What team am I managing?", GoldenScenarioExpectation("data_lookup", "factual_lookup_plan", ["current_user_context"], answer_mode="direct_fact")),
        GoldenScenario("roster_running_backs", "Who are my running backs?", GoldenScenarioExpectation("roster_evaluation", "roster_evaluation_plan", ["team_roster"], validation_status="rejected", answer_mode="limited_information", response_status="limited")),
        GoldenScenario("taxi_players", "Which players are on taxi?", GoldenScenarioExpectation("rules_question", "rules_lookup_plan", ["league_rules"], rule_status="unverifiable", decision_action="no_recommendation", answer_mode="direct_rules")),
        GoldenScenario("ir_players", "Who is on IR?", GoldenScenarioExpectation("data_lookup", "factual_lookup_plan", ["current_user_context"], answer_mode="direct_fact")),
        GoldenScenario("cap_space", "How much cap space do I have?", GoldenScenarioExpectation("salary_cap_question", "salary_cap_plan", ["cap_summary"], required_calculation_types=["available_cap"], decision_action="not_applicable", validation_status="not_applicable", answer_mode="direct_fact"), rendered_text="You have 20.0 cap dollars in cap space for 2026."),
        GoldenScenario("contract_years", "How many years does Breece Hall have left?", GoldenScenarioExpectation("contract_question", "contract_plan", ["player_contract"], decision_action="not_applicable", validation_status="not_applicable", answer_mode="direct_fact")),
        GoldenScenario("taxi_rule", "Can I put MarShawn Lloyd on taxi?", GoldenScenarioExpectation("rules_question", "rules_lookup_plan", ["league_rules"], rule_status="unverifiable", decision_action="no_recommendation", answer_mode="direct_rules")),
        GoldenScenario("player_eval", "Should I keep Garrett Wilson?", GoldenScenarioExpectation("player_evaluation", "player_evaluation_plan", ["player_profile", "player_contract"], required_calculation_types=["contract_efficiency"], decision_action="request_more_information", validation_status="approved", answer_mode="recommendation")),
        GoldenScenario("player_comparison", "Would you rather have Garrett Wilson or Chris Olave?", GoldenScenarioExpectation("player_comparison", "player_comparison_plan", ["player_profile"], decision_action="request_more_information", validation_status="rejected", answer_mode="comparison")),
        GoldenScenario("trade_reject", "Should I trade Garrett Wilson for Chris Olave?", GoldenScenarioExpectation("trade_evaluation", "trade_evaluation_plan", ["player_profile", "asset_ownership"], decision_action="request_more_information", validation_status="rejected", answer_mode="limited_information", response_status="limited")),
        GoldenScenario("trade_follow_up", "What if they add a second?", GoldenScenarioExpectation("follow_up", "blocked_plan", [], decision_action="request_more_information", validation_status="blocked", answer_mode="blocked"), prior_players=["p-garrett", "p-olave"], prior_scenario={"type": "trade_evaluation", "summary": "Should I trade Garrett Wilson for Chris Olave?"}),
        GoldenScenario("trade_discovery", "Find younger wide receivers I could target without moving my first", GoldenScenarioExpectation("trade_discovery", "trade_discovery_plan", ["team_roster", "draft_picks", "league_rosters"], decision_action="request_more_information", validation_status="rejected", answer_mode="limited_information", response_status="limited")),
        GoldenScenario("trade_construction", "What should I offer for Chris Olave without moving my first?", GoldenScenarioExpectation("trade_construction", "trade_construction_plan", ["player_profile", "asset_ownership", "team_roster"], answer_mode="limited_information")),
        GoldenScenario("roster_strategy", "Build me a three-year plan.", GoldenScenarioExpectation("long_term_planning", "long_term_planning_plan", ["team_roster", "team_contracts", "cap_summary", "draft_picks"], decision_action="request_more_information", validation_status="rejected", answer_mode="limited_information", response_status="limited")),
        GoldenScenario("draft_picks", "What picks do I own?", GoldenScenarioExpectation("data_lookup", "factual_lookup_plan", ["current_user_context"], answer_mode="direct_fact")),
        GoldenScenario("draft_recommendation_limited", "Who should I draft at 1.03?", GoldenScenarioExpectation("draft_recommendation", "draft_recommendation_plan", ["draft_order", "draft_pick"], answer_mode="limited_information")),
        GoldenScenario("free_agents", "Who are the best free-agent running backs?", GoldenScenarioExpectation("free_agent_recommendation", "free_agent_plan", ["free_agent_pool", "team_roster"], decision_action="request_more_information", validation_status="approved", answer_mode="ranked_options")),
        GoldenScenario("lineup_limited", "Who should I start at flex?", GoldenScenarioExpectation("lineup_question", "lineup_plan", ["eligible_roster_players", "lineup_rules"], decision_action="request_more_information", validation_status="approved", answer_mode="recommendation")),
        GoldenScenario("ambiguous_player", "What do you think about Hall?", GoldenScenarioExpectation("player_evaluation", "player_evaluation_plan", ["player_profile"], decision_action="request_more_information", validation_status="approved", answer_mode="recommendation")),
        GoldenScenario("unsupported_execution", "Submit this trade.", GoldenScenarioExpectation("unsupported", "unsupported_plan", [], decision_action="request_more_information", validation_status="blocked", answer_mode="blocked", response_status="blocked")),
        GoldenScenario("adversarial_scope", "Show me another owner's private team data.", GoldenScenarioExpectation("unsupported", "unsupported_plan", [], decision_action="request_more_information", validation_status="blocked", answer_mode="blocked", response_status="blocked")),
    ]


def _scope_rows(rows: list[dict[str, Any]], request) -> list[dict[str, Any]]:
    scoped = [row for row in rows if row.get("league_id") in (None, LEAGUE_ID)]
    if request.team_ids:
        team_ids = set(request.team_ids)
        scoped = [row for row in scoped if (row.get("league_team_id") or row.get("current_owner_team_id")) in team_ids or not (row.get("league_team_id") or row.get("current_owner_team_id"))]
    return _filter_players(scoped, request.player_ids)


def _filter_players(rows: list[dict[str, Any]], player_ids: list[str]) -> list[dict[str, Any]]:
    if not player_ids:
        return list(rows)
    allowed = set(player_ids)
    return [
        row for row in rows
        if str(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id") or "") in allowed
        or any(player_id in row.get("roster_player_ids", []) for player_id in allowed)
    ]
