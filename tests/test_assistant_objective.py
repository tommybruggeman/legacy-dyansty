from __future__ import annotations

import importlib
import sys
import types
import unittest

from gm_assistant.conversation_state import ConversationState, update_conversation_state
from gm_assistant.interpretation import interpret_question
from gm_assistant.objective import (
    Goal,
    RiskTolerance,
    build_objective_packet,
    build_owner_objective,
    conversation_update_from_objective,
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
            ],
            "player_strategic_profiles": [
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_id": "p-wilson"},
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Patrick Mahomes", "sleeper_id": "p-mahomes"},
            ],
            "league_relative_player_values": [],
            "contracts": [
                {"league_id": "league-1", "player_name": "Garrett Wilson", "sleeper_player_id": "p-wilson"},
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


def make_state(goal=None, timeframe=None, *, user_id="user-1", league_id="league-1", league_team_id="team-1"):
    return ConversationState(
        conversation_id="c1",
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        active_objective=goal,
        active_timeframe=timeframe,
    )


def team_context(direction="CONTEND_NOW", *, cap=None, draft=None):
    return {
        "team_brain": {
            "league_id": "league-1",
            "league_team_id": "team-1",
            "team_name": "Team One",
            "team_direction": direction,
            "position_strengths": ["QB", "WR"],
            "position_needs": ["RB", "TE"],
            "core_players": ["Garrett Wilson", "Patrick Mahomes"],
            "contract_problems": ["Expensive WR"],
            "championship_window_score": 88 if direction == "CONTEND_NOW" else 42,
        },
        "cap_summary": cap or {"cap_space": 12, "total_salary": 210, "internal_notes": "raw details excluded"},
        "draft_picks": draft or [
            {"season": 2027, "round": 1, "private_notes": "not preserved"},
            {"season": 2028, "round": 2},
        ],
    }


def memory(**overrides):
    base = {
        "user_id": "user-1",
        "league_id": "league-1",
        "league_team_id": "team-1",
        "risk_tolerance": "balanced",
        "team_build_preference": None,
        "preferred_strategy": "preserve cap flexibility",
        "notes": [],
    }
    base.update(overrides)
    return base


def objective_for(question, *, state=None, prefs=None, brain=None):
    context = make_context()
    interpreted = interpret_question(question, context, state, sb=FakeClient())
    return build_owner_objective(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted,
        owner_preferences=prefs if prefs is not None else memory(),
        team_context=brain if brain is not None else team_context(),
    )


class ExplicitGoalTest(unittest.TestCase):
    def test_explicit_goal_detection(self):
        cases = [
            ("I want to win this year.", Goal.WIN_NOW.value),
            ("I need to compete this season.", Goal.CONTEND_THIS_SEASON.value),
            ("I am rebuilding.", Goal.REBUILD.value),
            ("I want to stay competitive but get younger.", Goal.RETOOL.value),
            ("Help me get younger.", Goal.GET_YOUNGER.value),
            ("I want more picks.", Goal.ACQUIRE_DRAFT_CAPITAL.value),
            ("Do not trade my first.", Goal.PRESERVE_DRAFT_CAPITAL.value),
            ("I need to clear salary.", Goal.REDUCE_SALARY.value),
            ("Preserve future cap.", Goal.PRESERVE_CAP_FLEXIBILITY.value),
            ("I need a receiver.", Goal.IMPROVE_SPECIFIC_POSITION.value),
            ("I need more tight-end depth.", Goal.IMPROVE_SPECIFIC_POSITION.value),
            ("Replace my starting quarterback.", Goal.IMPROVE_SPECIFIC_POSITION.value),
            ("Give me a long-term roster plan.", Goal.LONG_TERM_ROSTER_PLAN.value),
            ("Prioritize upside.", Goal.INCREASE_UPSIDE.value),
            ("I want a safer option.", Goal.REDUCE_RISK.value),
        ]
        for question, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(objective_for(question).request_goal, expected)

    def test_multi_goal_keeps_secondary_goals(self):
        result = objective_for("Find five young receivers without moving my first.")
        self.assertEqual(result.request_goal, Goal.GET_YOUNGER.value)
        self.assertIn(Goal.PRESERVE_DRAFT_CAPITAL.value, result.secondary_goals)
        self.assertIn(Goal.IMPROVE_SPECIFIC_POSITION.value, result.secondary_goals)


class SourcePriorityAndReplacementTest(unittest.TestCase):
    def test_current_instruction_overrides_active_state_and_team_context(self):
        result = objective_for(
            "I know my roster is weak, but I still want to compete this season.",
            state=make_state(goal=Goal.REBUILD.value),
            brain=team_context("REBUILD"),
        )
        self.assertEqual(result.request_goal, Goal.CONTEND_THIS_SEASON.value)
        self.assertEqual(result.active_strategic_goal, Goal.CONTEND_THIS_SEASON.value)
        self.assertTrue(result.strategic_conflicts)

    def test_active_state_overrides_durable_preference_when_current_request_is_silent(self):
        result = objective_for(
            "Should I trade Garrett Wilson?",
            state=make_state(goal=Goal.REBUILD.value),
            prefs=memory(team_build_preference="contend"),
        )
        self.assertEqual(result.active_strategic_goal, Goal.REBUILD.value)
        self.assertEqual(result.request_goal, Goal.EVALUATE_ASSET.value)

    def test_durable_preference_supports_objective_when_silent(self):
        result = objective_for(
            "What should I do next?",
            prefs=memory(team_build_preference="rebuild"),
        )
        self.assertEqual(result.request_goal, Goal.REBUILD.value)
        self.assertEqual(result.explicitness, "durable")

    def test_intent_default_is_conservative(self):
        self.assertEqual(objective_for("Who has Garrett Wilson?").request_goal, Goal.FACTUAL_LOOKUP.value)
        self.assertEqual(objective_for("Can I put him on taxi?").request_goal, Goal.UNDERSTAND_RULES.value)
        self.assertEqual(objective_for("Build me a trade for Garrett Wilson.").request_goal, Goal.CONSTRUCT_TRANSACTION.value)

    def test_objective_replacement_and_constraint_removal(self):
        win = objective_for("Forget rebuilding. I want to win now.", state=make_state(goal=Goal.REBUILD.value))
        self.assertEqual(win.active_strategic_goal, Goal.WIN_NOW.value)
        allowed = objective_for("I am willing to move my first.", state=make_state(goal=Goal.REBUILD.value))
        update = conversation_update_from_objective(allowed, message_id="m1")
        self.assertIn("do_not_trade_first_round_pick", update.remove_constraint_keys)

    def test_factual_lookup_does_not_erase_active_strategy(self):
        state = make_state(goal=Goal.REBUILD.value)
        result = objective_for("How much cap space do I have?", state=state)
        update = conversation_update_from_objective(result)
        update_conversation_state(state, update)
        self.assertEqual(result.request_goal, Goal.FACTUAL_LOOKUP.value)
        self.assertEqual(result.active_strategic_goal, Goal.REBUILD.value)
        self.assertEqual(state.active_objective, Goal.REBUILD.value)


class TimeframeRiskConstraintContextTest(unittest.TestCase):
    def test_timeframes(self):
        self.assertEqual(objective_for("Help me compete this season.").target_seasons, [2026])
        self.assertEqual(objective_for("Plan for next season.").target_seasons, [2027])
        self.assertIn(2028, objective_for("Plan for 2028.").target_seasons)
        self.assertEqual(objective_for("Give me my next three years plan.").horizon_years, 3)
        self.assertEqual(objective_for("Think long term.", state=make_state(timeframe="trade_deadline")).timeframe, "long_term")
        self.assertEqual(objective_for("What is the trade deadline plan?").timeframe, "trade_deadline")
        self.assertEqual(objective_for("Who should I start this week?").timeframe, "this_week")
        self.assertEqual(objective_for("Should I trade Garrett Wilson?", state=make_state(timeframe="next_season")).timeframe, "next_season")

    def test_risk_tolerance(self):
        self.assertEqual(objective_for("I want a safer option.").risk_tolerance, RiskTolerance.CONSERVATIVE.value)
        self.assertEqual(objective_for("I am willing to take a swing.").risk_tolerance, RiskTolerance.AGGRESSIVE.value)
        self.assertEqual(objective_for("Should I trade Garrett Wilson?", prefs=memory(risk_tolerance="balanced")).risk_tolerance, RiskTolerance.BALANCED.value)
        self.assertEqual(objective_for("Should I trade Garrett Wilson?", prefs=memory(risk_tolerance=None)).risk_tolerance, RiskTolerance.UNKNOWN.value)

    def test_hard_and_soft_constraints(self):
        hard = objective_for("Find receivers under 25 without moving my first.")
        self.assertTrue(any(c.constraint_type == "max_age" and c.hard for c in hard.non_negotiables))
        self.assertTrue(any(c.constraint_type == "do_not_trade_first_round_pick" for c in hard.non_negotiables))
        soft = objective_for("I would prefer someone younger and try to preserve cap space.")
        self.assertTrue(any(c.constraint_type == "prefers_younger_players" for c in soft.soft_preferences))
        salary = objective_for("Find a receiver under $10 salary.")
        self.assertTrue(any(c.constraint_type == "max_salary" and c.value == 10 for c in salary.non_negotiables))
        fa = objective_for("Only free agents at RB.")
        self.assertTrue(any(c.constraint_type == "only_free_agents" for c in fa.non_negotiables))

    def test_limited_team_context_is_compact_and_missing_context_warns(self):
        result = objective_for("Should I trade Garrett Wilson?", brain=team_context())
        packet = build_objective_packet(result)
        self.assertEqual(packet["factual_context"]["team_direction"], "CONTEND_NOW")
        self.assertNotIn("core_players", packet["factual_context"]["roster_strength_summary"])
        self.assertEqual(packet["factual_context"]["draft_capital_summary"]["pick_count"], 2)
        missing = objective_for("I am rebuilding.", brain={})
        self.assertIn("team_brain_missing", missing.factual_context.data_quality_warnings)
        self.assertEqual(missing.request_goal, Goal.REBUILD.value)


class ConflictAmbiguityMemoryCompatibilityTest(unittest.TestCase):
    def test_strategic_conflicts_preserve_owner_instruction(self):
        rebuild = objective_for("I am rebuilding.", brain=team_context("CONTEND_NOW"))
        contend = objective_for("I am going all in.", brain=team_context("REBUILD"))
        veteran = objective_for("I want young players but I need an older veteran receiver.")
        self.assertEqual(rebuild.request_goal, Goal.REBUILD.value)
        self.assertTrue(rebuild.strategic_conflicts)
        self.assertTrue(contend.strategic_conflicts)
        self.assertTrue(veteran.strategic_conflicts)

    def test_ambiguities_and_low_confidence(self):
        unclear = objective_for("Make my team better.")
        self.assertTrue(any(a.ambiguity_type == "unclear_improvement_goal" for a in unclear.ambiguities))
        self.assertEqual(unclear.confidence, "low")
        vague = objective_for("Find young receivers.")
        self.assertTrue(any(a.ambiguity_type == "vague_youth_preference" and not a.blocking for a in vague.ambiguities))
        unresolved = objective_for("Help me trade for him.")
        self.assertTrue(any(a.blocking for a in unresolved.ambiguities))

    def test_durable_preferences_are_scoped_inputs_and_not_written(self):
        prefs = memory(
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
            team_build_preference="prioritize_youth",
            notes=["explicit_preference:do_not_trade_first_round_pick"],
        )
        result = objective_for("What should I do next?", prefs=prefs)
        self.assertEqual(result.request_goal, Goal.GET_YOUNGER.value)
        self.assertTrue(any(c.source == "durable_owner_preference" for c in result.non_negotiables + result.soft_preferences))
        self.assertEqual(prefs["team_build_preference"], "prioritize_youth")

    def test_current_instruction_overrides_durable_memory(self):
        result = objective_for(
            "For this move, I am fine acquiring an older veteran receiver.",
            prefs=memory(team_build_preference="prioritize_youth", notes=["explicit_preference:prefers_younger_players"]),
        )
        self.assertNotEqual(result.request_goal, Goal.GET_YOUNGER.value)
        self.assertTrue(any(c.constraint_type == "durable_youth_preference_overridden" for c in result.soft_preferences))

    def test_openai_service_accepts_objective_omitted_or_supplied(self):
        service = importlib.import_module("gm_assistant.openai_service")
        objective = objective_for("I am rebuilding.")
        without_objective = service._build_initial_messages("Question?", [], None, None, None)
        with_objective = service._build_initial_messages("Question?", [], None, None, objective)
        self.assertEqual(without_objective[-1]["content"], "Question?")
        self.assertIn("Structured owner objective", with_objective[0]["content"])
        self.assertEqual(with_objective[-1]["content"], "Question?")


if __name__ == "__main__":
    unittest.main()
