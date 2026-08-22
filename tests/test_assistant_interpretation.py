from __future__ import annotations

import importlib
import sys
import types
import unittest

from gm_assistant.conversation_state import ConversationState
from gm_assistant.interpretation import (
    Intent,
    ResolutionStatus,
    conversation_update_from_interpretation,
    interpret_question,
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
                {"id": "team-2", "league_id": "league-1", "team_name": "Giants", "owner_name": "Dylan Burruel"},
                {"id": "team-x", "league_id": "league-x", "team_name": "Other League Team", "owner_name": "Other Owner"},
            ],
            "player_strategic_profiles": [
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Patrick Mahomes", "sleeper_id": "p-mahomes"},
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_id": "p-wilson"},
                {"league_id": "league-1", "league_team_id": "team-2", "player_name": "Josh Allen", "sleeper_id": "p-josh-allen-qb"},
                {"league_id": "league-1", "league_team_id": "team-2", "player_name": "Brandon Allen", "sleeper_id": "p-brandon-allen"},
                {"league_id": "league-x", "league_team_id": "team-x", "player_name": "Other League Star", "sleeper_id": "p-other"},
            ],
            "league_relative_player_values": [],
            "contracts": [
                {"league_id": "league-1", "player_name": "CeeDee Lamb", "sleeper_player_id": "p-lamb"},
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


def make_context(league_id="league-1", league_team_id="team-1") -> AssistantRequestContext:
    return AssistantRequestContext(
        user_id="user-1",
        league_id=league_id,
        league_team_id=league_team_id,
        membership_id="membership-1",
        role="owner",
        current_season=2026,
        requested_season=2026,
        permission_scopes=(TEAM_ADVICE, LEAGUE_PUBLIC_READ),
        team_name="Team One",
        owner_name="Owner One",
    )


def make_state(*, players=None, teams=None, scenario=None, prior=None, conversation_id="c1") -> ConversationState:
    return ConversationState(
        conversation_id=conversation_id,
        user_id="user-1",
        league_id="league-1",
        league_team_id="team-1",
        discussed_player_ids=list(players or []),
        discussed_team_ids=list(teams or []),
        current_scenario=scenario,
        prior_recommendation_ref=prior,
    )


class IntentClassificationTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeClient()
        self.context = make_context()

    def assertIntent(self, question, intent):
        result = interpret_question(question, self.context, sb=self.sb)
        self.assertEqual(result.primary_intent, intent.value)

    def test_core_intent_taxonomy(self):
        cases = [
            ("What do you think about Garrett Wilson?", Intent.PLAYER_EVALUATION),
            ("Patrick Mahomes or Garrett Wilson?", Intent.PLAYER_COMPARISON),
            ("How does my roster look?", Intent.ROSTER_EVALUATION),
            ("Should I trade Garrett Wilson for my first?", Intent.TRADE_EVALUATION),
            ("Find me five receivers under 25 without moving my first.", Intent.TRADE_DISCOVERY),
            ("What should I offer for Patrick Mahomes?", Intent.TRADE_CONSTRUCTION),
            ("Who should I take at 1.03?", Intent.DRAFT_RECOMMENDATION),
            ("What is my 2027 first worth?", Intent.DRAFT_PICK_EVALUATION),
            ("Which free agent should I pick up?", Intent.FREE_AGENT_RECOMMENDATION),
            ("What is Garrett Wilson's contract?", Intent.CONTRACT_QUESTION),
            ("How much cap space do I have?", Intent.SALARY_CAP_QUESTION),
            ("Can I put him on taxi?", Intent.RULES_QUESTION),
            ("Should I start Patrick Mahomes?", Intent.LINEUP_QUESTION),
            ("Should I cut Garrett Wilson?", Intent.ROSTER_MOVE_QUESTION),
            ("What is my three-year plan?", Intent.LONG_TERM_PLANNING),
            ("Who are the strongest teams in the league?", Intent.LEAGUE_ANALYSIS),
            ("Who has Garrett Wilson?", Intent.DATA_LOOKUP),
            ("Thanks", Intent.GENERAL_CONVERSATION),
            ("What is the stock market doing?", Intent.UNSUPPORTED),
        ]
        for question, intent in cases:
            with self.subTest(question=question):
                self.assertIntent(question, intent)

    def test_team_identity_variants_are_factual_lookup(self):
        cases = [
            "What team am I managing?",
            "What team do I manage?",
            "Which team is mine?",
            "Who is my team?",
            "What is my team name?",
            "Which franchise do I control?",
            "Tell me my team.",
            "what team do I manage",
        ]
        for question in cases:
            with self.subTest(question=question):
                self.assertIntent(question, Intent.DATA_LOOKUP)

    def test_roster_list_variants_are_factual_lookup(self):
        cases = [
            "Who is on my team?",
            "Who is on my roster?",
            "Show me my roster.",
            "List my players.",
            "Which players do I have?",
            "Who do I own?",
            "who is on my team",
        ]
        for question in cases:
            with self.subTest(question=question):
                result = interpret_question(question, self.context, sb=self.sb)
                self.assertEqual(result.primary_intent, Intent.DATA_LOOKUP.value)
                self.assertEqual(result.player_refs, [])

    def test_football_analysis_phrased_casually_is_not_general_conversation(self):
        cases = [
            "Who is my best player?",
            "Who are my three best players?",
            "Rank my roster.",
            "Rank my entire roster",
            "Rank my full roster",
            "Rank all my players",
            "Rank my players from best to worst",
            "Give me a best-to-worst roster ranking",
            "Who are the best players on my roster?",
            "Show me my top five players",
            "List my strongest players",
            "Order my roster by value",
            "What is my biggest weakness?",
            "How should I fix my RB room?",
            "Which player should I market-check first?",
            "Am I a contender?",
            "What should my offseason strategy be?",
            "who is my best player",
        ]
        for question in cases:
            with self.subTest(question=question):
                result = interpret_question(question, self.context, sb=self.sb)
                self.assertNotEqual(result.primary_intent, Intent.GENERAL_CONVERSATION.value)

    def test_casual_greeting_remains_general_conversation(self):
        self.assertIntent("Hi", Intent.GENERAL_CONVERSATION)

    def test_multi_intent_trade_contract_cap(self):
        result = interpret_question(
            "Should I trade my 2027 first for this receiver, and can I afford his contract?",
            self.context,
            make_state(players=["p-wilson"]),
            sb=self.sb,
        )
        self.assertEqual(result.primary_intent, Intent.TRADE_EVALUATION.value)
        self.assertIn(Intent.SALARY_CAP_QUESTION.value, result.secondary_intents)
        self.assertIn(Intent.CONTRACT_QUESTION.value, result.secondary_intents)


class EntityResolutionTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeClient()
        self.context = make_context()

    def test_player_resolution_variations(self):
        cases = [
            "patrick mahomes",
            "Patrick Mahomes",
            "P. Mahomes",
            "Mahommes",
            "Mahomes",
        ]
        for question in cases:
            with self.subTest(question=question):
                result = interpret_question(f"Thoughts on {question}?", self.context, sb=self.sb)
                self.assertEqual(result.player_refs[0].canonical_id, "p-mahomes")
                self.assertEqual(result.player_refs[0].resolution_status, ResolutionStatus.RESOLVED.value)

    def test_ambiguous_surname_is_not_guessed(self):
        result = interpret_question("Thoughts on Allen?", self.context, sb=self.sb)
        self.assertEqual(result.player_refs[0].resolution_status, ResolutionStatus.AMBIGUOUS.value)
        self.assertTrue(result.ambiguities[0].blocking)

    def test_player_not_found_is_unresolved_reduced_mode(self):
        result = interpret_question("Should I trade Missing Player?", self.context, sb=self.sb)
        self.assertEqual(result.confidence, "low")
        self.assertTrue(result.unresolved_text)

    def test_contract_table_player_can_resolve(self):
        result = interpret_question("What is CeeDee Lamb's contract?", self.context, sb=self.sb)
        self.assertEqual(result.player_refs[0].canonical_id, "p-lamb")

    def test_active_league_only(self):
        result = interpret_question("What do you think about Other League Star?", self.context, sb=self.sb)
        self.assertFalse([ref for ref in result.player_refs if ref.canonical_id == "p-other"])

    def test_fantasy_team_resolution_and_cross_league_isolation(self):
        result = interpret_question("Compare my team to Dylan Burruel", self.context, sb=self.sb)
        ids = {ref.canonical_id for ref in result.fantasy_team_refs}
        self.assertIn("team-1", ids)
        self.assertIn("team-2", ids)
        self.assertNotIn("team-x", ids)

    def test_nfl_team_and_fantasy_team_conflict(self):
        result = interpret_question("Should I trade with Giants?", self.context, sb=self.sb)
        self.assertTrue(any(ref.canonical_id == "NYG" for ref in result.nfl_team_refs))
        self.assertTrue(any(ambiguity.ambiguity_type == "team_type" for ambiguity in result.ambiguities))


class DraftPickConstraintAndFollowUpTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeClient()
        self.context = make_context()

    def test_draft_pick_language(self):
        result = interpret_question("What if I add my 2027 first and the 1.03 but not their second?", self.context, sb=self.sb)
        rounds = [pick.round for pick in result.pick_refs]
        self.assertIn(1, rounds)
        self.assertIn(2, rounds)
        self.assertTrue(any(pick.slot == 3 for pick in result.pick_refs))
        self.assertTrue(any(pick.current_owner_team_id == "team-1" for pick in result.pick_refs))
        self.assertTrue(any(pick.current_owner_team_id is None and pick.round == 2 for pick in result.pick_refs))

    def test_constraints_and_assets_are_structured(self):
        result = interpret_question("Find five receivers under 25 without moving my first.", self.context, sb=self.sb)
        self.assertEqual(result.primary_intent, Intent.TRADE_DISCOVERY.value)
        self.assertEqual(result.positions, ["WR"])
        self.assertEqual(result.requested_count, 5)
        self.assertEqual(result.constraints["max_age"], 24)
        self.assertTrue(result.constraints["do_not_trade_first_round_pick"])
        self.assertTrue(result.excluded_assets)

    def test_timeframes_and_seasons(self):
        result = interpret_question("What is my next three years plan for the 2028 rookie draft?", self.context, sb=self.sb)
        self.assertEqual(result.timeframe["horizon_years"], 3)
        self.assertIn(2028, result.seasons)
        self.assertEqual(result.timeframe["event"], "rookie_draft")

    def test_follow_up_single_player(self):
        result = interpret_question("What about him?", self.context, make_state(players=["p-wilson"]), sb=self.sb)
        self.assertTrue(result.is_follow_up)
        self.assertEqual(result.player_refs[0].resolution_status, ResolutionStatus.INFERRED_FROM_CONVERSATION.value)
        self.assertEqual(result.player_refs[0].canonical_id, "p-wilson")

    def test_follow_up_multiple_players_is_ambiguous(self):
        result = interpret_question("What about him?", self.context, make_state(players=["p-wilson", "p-mahomes"]), sb=self.sb)
        self.assertTrue(result.is_follow_up)
        self.assertTrue(any(ambiguity.ambiguity_type == "current_player_reference" for ambiguity in result.ambiguities))
        self.assertEqual(result.confidence, "low")

    def test_prior_recommendation_and_current_scenario_follow_ups(self):
        option = interpret_question("What about the second option?", self.context, make_state(prior="second_option"), sb=self.sb)
        scenario = interpret_question("Would you still do it?", self.context, make_state(scenario={"type": "trade"}), sb=self.sb)
        self.assertEqual(option.follow_up_target, "prior_recommendation")
        self.assertEqual(scenario.follow_up_target, "trade")

    def test_conversation_update_from_interpretation_is_scope_safe(self):
        result = interpret_question("Thoughts on Garrett Wilson without moving my first?", self.context, sb=self.sb)
        update = conversation_update_from_interpretation(result, message_id="m1")
        self.assertIn("p-wilson", update.add_player_ids)
        self.assertTrue(update.add_constraints["do_not_trade_first_round_pick"])
        self.assertEqual(update.last_message_id, "m1")

    def test_reduced_mode_does_not_crash_without_supabase(self):
        result = interpret_question("Find two cheap RBs for next season", self.context)
        self.assertEqual(result.primary_intent, Intent.TRADE_DISCOVERY.value)
        self.assertEqual(result.positions, ["RB"])
        self.assertEqual(result.timeframe["label"], "next_season")
        self.assertIn(2027, result.seasons)


class OpenAICompatibilityTest(unittest.TestCase):
    def test_openai_service_accepts_omitted_and_supplied_interpretation(self):
        service = importlib.import_module("gm_assistant.openai_service")
        result = interpret_question("What is Garrett Wilson's contract?", make_context(), sb=FakeClient())
        without_packet = service._build_initial_messages("Question?", [], None, None)
        with_packet = service._build_initial_messages("Question?", [], None, result)
        self.assertEqual(without_packet[-1]["content"], "Question?")
        self.assertIn("Structured question interpretation", with_packet[0]["content"])
        self.assertEqual(with_packet[-1]["content"], "Question?")


if __name__ == "__main__":
    unittest.main()
