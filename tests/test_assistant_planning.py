from __future__ import annotations

import importlib
import sys
import types
import unittest

from gm_assistant.conversation_state import ConversationState
from gm_assistant.interpretation import interpret_question
from gm_assistant.objective import Goal, OwnerObjective, build_owner_objective
from gm_assistant.planning import (
    DecisionEngine,
    FallbackStrategy,
    PlanType,
    ResponseMode,
    build_decision_plan,
    build_plan_packet,
)
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)


class FakeClient:
    def __init__(self):
        self.rows = {
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Team One", "owner_name": "Owner One"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Team Two", "owner_name": "Owner Two"},
                {"id": "team-3", "league_id": "league-1", "team_name": "Team Three", "owner_name": "Owner Three"},
                {"id": "team-x", "league_id": "league-x", "team_name": "Other Team", "owner_name": "Other Owner"},
            ],
            "player_strategic_profiles": [
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_id": "p-wilson"},
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Patrick Mahomes", "sleeper_id": "p-mahomes"},
                {"league_id": "league-1", "league_team_id": "team-2", "player_name": "CeeDee Lamb", "sleeper_id": "p-lamb"},
                {"league_id": "league-1", "league_team_id": "team-2", "player_name": "Josh Allen", "sleeper_id": "p-josh-allen"},
                {"league_id": "league-1", "league_team_id": "team-3", "player_name": "Brandon Allen", "sleeper_id": "p-brandon-allen"},
                {"league_id": "league-x", "league_team_id": "team-x", "player_name": "Other League Star", "sleeper_id": "p-other"},
            ],
            "league_relative_player_values": [],
            "contracts": [
                {"league_id": "league-1", "player_name": "Garrett Wilson", "sleeper_player_id": "p-wilson"},
                {"league_id": "league-1", "player_name": "CeeDee Lamb", "sleeper_player_id": "p-lamb"},
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


def make_context() -> AssistantRequestContext:
    return AssistantRequestContext(
        user_id="user-1",
        league_id="league-1",
        league_team_id="team-1",
        membership_id="membership-1",
        role="owner",
        current_season=2026,
        requested_season=2026,
        permission_scopes=(TEAM_ADVICE, LEAGUE_PUBLIC_READ),
        team_name="Team One",
        owner_name="Owner One",
    )


def make_state(*, players=None, scenario=None, goal=None):
    return ConversationState(
        conversation_id="c1",
        user_id="user-1",
        league_id="league-1",
        league_team_id="team-1",
        discussed_player_ids=list(players or []),
        current_scenario=scenario,
        active_objective=goal,
    )


def make_objective(context, state, interpreted) -> OwnerObjective:
    return build_owner_objective(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted,
        owner_preferences={"risk_tolerance": "balanced", "team_build_preference": None, "notes": []},
        team_context={"team_brain": {"team_direction": "CONTEND_NOW", "position_strengths": ["QB"], "position_needs": ["RB"], "championship_window_score": 88}},
    )


def plan_for(question, *, state=None):
    context = make_context()
    interpreted = interpret_question(question, context, state, sb=FakeClient())
    objective = make_objective(context, state, interpreted)
    return build_decision_plan(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted,
        owner_objective=objective,
    )


def retrieval_types(plan):
    return {item.retrieval_type for item in plan.retrieval_requests}


def calculation_types(plan):
    return {item.calculation_type for item in plan.calculation_requests}


def validation_types(plan):
    return {item.validation_type for item in plan.validation_steps}


class PlanMappingTest(unittest.TestCase):
    def test_intent_to_plan_mapping(self):
        cases = [
            ("Who has Garrett Wilson?", PlanType.FACTUAL_LOOKUP.value),
            ("Can I put Garrett Wilson on taxi?", PlanType.RULES_LOOKUP.value),
            ("What do you think about Garrett Wilson?", PlanType.PLAYER_EVALUATION.value),
            ("Garrett Wilson or CeeDee Lamb?", PlanType.PLAYER_COMPARISON.value),
            ("How does my roster look?", PlanType.ROSTER_EVALUATION.value),
            ("Should I trade Garrett Wilson for CeeDee Lamb?", PlanType.TRADE_EVALUATION.value),
            ("Find me five young receivers.", PlanType.TRADE_DISCOVERY.value),
            ("Build me a trade for CeeDee Lamb.", PlanType.TRADE_CONSTRUCTION.value),
            ("Who should I take at 1.03?", PlanType.DRAFT_RECOMMENDATION.value),
            ("What is my 2028 first worth?", PlanType.DRAFT_PICK_EVALUATION.value),
            ("Which free agent running backs should I target?", PlanType.FREE_AGENT.value),
            ("What is Garrett Wilson's contract?", PlanType.CONTRACT.value),
            ("How much cap space do I have?", PlanType.SALARY_CAP.value),
            ("Who should I start at flex?", PlanType.LINEUP.value),
            ("Should I cut Garrett Wilson?", PlanType.ROSTER_MOVE.value),
            ("Build me a three-year plan.", PlanType.LONG_TERM_PLANNING.value),
            ("Compare my team to Team Two.", PlanType.TEAM_COMPARISON.value),
            ("Who are the strongest teams in the league?", PlanType.LEAGUE_ANALYSIS.value),
            ("Thanks", PlanType.GENERAL_CONVERSATION.value),
            ("What is the stock market doing?", PlanType.UNSUPPORTED.value),
        ]
        for question, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(plan_for(question).plan_type, expected)

    def test_response_modes_and_future_engines_are_symbolic(self):
        trade = plan_for("Should I trade Garrett Wilson for CeeDee Lamb?")
        self.assertEqual(trade.response_mode, ResponseMode.STRUCTURED_EVALUATION.value)
        self.assertEqual(trade.decision_engine, DecisionEngine.TRADE_EVALUATION.value)
        self.assertFalse(any("recommend" in c.calculation_type for c in trade.calculation_requests))


class MinimalRetrievalAndScopeTest(unittest.TestCase):
    def test_team_identity_lookup_requests_only_current_user_context(self):
        plan = plan_for("what team do I manage")

        self.assertEqual(plan.plan_type, PlanType.FACTUAL_LOOKUP.value)
        self.assertEqual(plan.response_mode, ResponseMode.DIRECT_FACTUAL.value)
        self.assertEqual(retrieval_types(plan), {"current_user_context"})
        self.assertEqual(calculation_types(plan), set())
        self.assertIn("team-1", plan.retrieval_requests[0].team_ids)

    def test_roster_list_lookup_requests_only_team_roster(self):
        for question in ("Who is on my team?", "Show me my roster.", "List my players.", "Which players do I have?", "Who do I own?"):
            with self.subTest(question=question):
                plan = plan_for(question)
                self.assertEqual(plan.plan_type, PlanType.FACTUAL_LOOKUP.value)
                self.assertEqual(plan.response_mode, ResponseMode.DIRECT_FACTUAL.value)
                self.assertEqual(retrieval_types(plan), {"team_roster"})
                self.assertEqual(calculation_types(plan), set())
                self.assertNotIn("draft_pick", retrieval_types(plan))

    def test_cap_lookup_is_minimal(self):
        plan = plan_for("How much cap space do I have?")
        self.assertEqual(retrieval_types(plan), {"cap_summary"})
        self.assertEqual(calculation_types(plan), {"available_cap"})
        self.assertIn("all_rosters", plan.unnecessary_data)

    def test_contract_lookup_does_not_request_league_history(self):
        plan = plan_for("What is Garrett Wilson's contract?")
        self.assertIn("player_contract", retrieval_types(plan))
        self.assertNotIn("recent_transactions", retrieval_types(plan))
        self.assertIn("league_history", plan.unnecessary_data)

    def test_rules_question_does_not_request_market_values(self):
        plan = plan_for("Can I put Garrett Wilson on taxi next season?")
        self.assertIn("league_rules", retrieval_types(plan))
        self.assertTrue(any(rule.rule_type == "taxi_eligibility" for rule in plan.rule_requests))
        self.assertNotIn("league_relative_value", retrieval_types(plan))

    def test_trade_discovery_may_request_league_wide_rosters(self):
        plan = plan_for("Find me five young receivers without moving my first.")
        self.assertIn("league_rosters", retrieval_types(plan))
        self.assertTrue(any(item.scope == "league_wide" for item in plan.retrieval_requests))
        self.assertTrue(any(v.validation_type == "excluded_assets" for v in plan.validation_steps))

    def test_lineup_and_long_term_avoid_unrelated_data(self):
        lineup = plan_for("Who should I start at flex?")
        long_term = plan_for("Build me a three-year plan.")
        self.assertNotIn("draft_picks", retrieval_types(lineup))
        self.assertIn("multi_year_draft_picks", lineup.unnecessary_data)
        self.assertNotIn("weekly_projection_summary", retrieval_types(long_term))
        self.assertIn("weekly_lineup_projections_by_default", long_term.unnecessary_data)

    def test_every_retrieval_is_scoped_to_league(self):
        plan = plan_for("Should I trade Garrett Wilson for CeeDee Lamb?")
        self.assertTrue(plan.retrieval_requests)
        for request in plan.retrieval_requests:
            self.assertEqual(request.filters["league_id"], "league-1")
            if request.scope == "team":
                self.assertIn("team-1", request.team_ids)

    def test_owner_plan_never_requests_another_users_memory(self):
        plan = plan_for("Compare my team to Team Two.")
        self.assertNotIn("another_user_memory", retrieval_types(plan))
        self.assertIn("another_user_memory", plan.unnecessary_data)


class EntityTradeDraftRuleCalculationTest(unittest.TestCase):
    def test_unresolved_player_blocks_player_evaluation(self):
        plan = plan_for("What do you think about Missing Player?")
        self.assertFalse(plan.ready_for_execution)
        self.assertEqual(plan.response_mode, ResponseMode.CLARIFICATION_REQUIRED.value)
        self.assertTrue(any(b.blocker_type in {"missing_player_reference", "low_confidence_interpretation"} for b in plan.blockers))

    def test_missing_trade_side_blocks_trade_evaluation(self):
        plan = plan_for("Should I trade Garrett Wilson?")
        self.assertFalse(plan.ready_for_execution)
        self.assertTrue(any(b.blocker_type == "incomplete_trade_sides" for b in plan.blockers))

    def test_resolved_follow_up_player_can_proceed(self):
        plan = plan_for("What about him?", state=make_state(players=["p-wilson"]))
        self.assertTrue(plan.ready_for_execution)
        self.assertIn("player_contract", retrieval_types(plan))

    def test_ambiguous_follow_up_blocks(self):
        plan = plan_for("What about him?", state=make_state(players=["p-wilson", "p-lamb"]))
        self.assertFalse(plan.ready_for_execution)
        self.assertTrue(any(b.blocker_type == "current_player_reference" for b in plan.blockers))

    def test_trade_plan_requests_assets_rules_calculations_and_validations(self):
        plan = plan_for("Should I trade Garrett Wilson and my 2027 second for CeeDee Lamb without moving my first?")
        self.assertIn("asset_ownership", retrieval_types(plan))
        self.assertIn("team_roster", retrieval_types(plan))
        self.assertIn("cap_summary", retrieval_types(plan))
        self.assertTrue(any(rule.rule_type == "pick_tradeability" for rule in plan.rule_requests))
        self.assertIn("trade_value_delta", calculation_types(plan))
        self.assertIn("cap_impact", calculation_types(plan))
        self.assertIn("pick_availability", validation_types(plan))
        self.assertIn("roster_size_impact", validation_types(plan))

    def test_draft_plans(self):
        exact = plan_for("Who should I take at 1.03?")
        future = plan_for("What is my 2028 first worth?")
        unresolved = plan_for("What is their second worth?")
        self.assertIn("prospect_pool", retrieval_types(exact))
        self.assertIn("prospect_eligibility", validation_types(exact))
        self.assertIn("future_prospect_source_required", exact.warnings)
        self.assertIn("draft_pick", retrieval_types(future))
        self.assertFalse(unresolved.ready_for_execution)
        self.assertTrue(any(b.blocker_type == "unresolved_pick_owner" for b in unresolved.blockers))

    def test_rule_plan_variants(self):
        taxi = plan_for("Can I put Garrett Wilson on taxi next season?")
        deadline = plan_for("When is the trade deadline?")
        cap = plan_for("Is this move legal under the cap?")
        self.assertTrue(any(rule.rule_type == "taxi_eligibility" for rule in taxi.rule_requests))
        self.assertTrue(any(rule.rule_type == "trade_deadline" for rule in deadline.rule_requests))
        self.assertTrue(any(rule.rule_type == "salary_cap_legality" for rule in cap.rule_requests))
        self.assertEqual(taxi.fallback_strategy, FallbackStrategy.CONDITIONAL_ON_RULES.value)

    def test_calculation_requests_by_plan_type(self):
        self.assertIn("contract_efficiency", calculation_types(plan_for("What do you think about Garrett Wilson?")))
        self.assertIn("value_comparison", calculation_types(plan_for("Garrett Wilson or CeeDee Lamb?")))
        self.assertIn("roster_depth", calculation_types(plan_for("How does my roster look?")))
        self.assertIn("savings_by_action", calculation_types(plan_for("How can I clear salary?")))
        self.assertIn("lineup_projection", calculation_types(plan_for("Who should I start at flex?")))
        self.assertEqual(calculation_types(plan_for("How much cap space do I have?")), {"available_cap"})


class FallbackBoundsCompatibilityTest(unittest.TestCase):
    def test_fallbacks(self):
        self.assertEqual(plan_for("How much cap space do I have?").fallback_strategy, FallbackStrategy.FACTUAL_ONLY.value)
        self.assertEqual(plan_for("What do you think about Garrett Wilson?").fallback_strategy, FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value)
        self.assertEqual(plan_for("Build me a trade for CeeDee Lamb.").fallback_strategy, FallbackStrategy.CLARIFICATION_REQUIRED.value)
        self.assertEqual(plan_for("What is the stock market doing?").fallback_strategy, FallbackStrategy.UNSUPPORTED.value)

    def test_unbounded_trade_discovery_is_capped_and_deduped(self):
        plan = plan_for("Find me 99 receivers I can acquire.")
        self.assertIn("requested_count_capped_to_12", plan.warnings)
        league_wide = [request for request in plan.retrieval_requests if request.scope == "league_wide"]
        self.assertLessEqual(len(league_wide), 2)
        keys = [(r.retrieval_type, r.scope, tuple(r.player_ids), tuple(r.team_ids)) for r in plan.retrieval_requests]
        self.assertEqual(len(keys), len(set(keys)))

    def test_excessive_seasons_are_bounded(self):
        plan = plan_for("Plan for 2027 2028 2029 2030.")
        for request in plan.retrieval_requests:
            self.assertLessEqual(len(request.seasons), 3)

    def test_plan_packet_is_compact(self):
        plan = plan_for("Should I trade Garrett Wilson for CeeDee Lamb?")
        packet = build_plan_packet(plan)
        self.assertEqual(packet["plan_type"], PlanType.TRADE_EVALUATION.value)
        self.assertIn("retrieval_type", packet["retrieval_requests"][0])
        self.assertNotIn("__dict__", str(packet))

    def test_openai_service_accepts_plan_omitted_or_supplied(self):
        service = importlib.import_module("gm_assistant.openai_service")
        plan = plan_for("How much cap space do I have?")
        without_plan = service._build_initial_messages("Question?", [], None, None, None, None)
        with_plan = service._build_initial_messages("Question?", [], None, None, None, plan)
        self.assertEqual(without_plan[-1]["content"], "Question?")
        self.assertIn("Structured decision plan", with_plan[0]["content"])
        self.assertEqual(with_plan[-1]["content"], "Question?")


if __name__ == "__main__":
    unittest.main()
