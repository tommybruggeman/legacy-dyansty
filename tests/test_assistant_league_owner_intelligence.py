from __future__ import annotations

import inspect
import unittest

from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.league_owner_intelligence import (
    BehavioralTendencyType,
    LeagueOwnerIntelligenceService,
    TransactionActionCategory,
)
from gm_assistant.league_owner_intelligence import service as league_owner_service_source
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


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
        self.client.selects.append((self.table_name, list(self.filters), self.limit_value))
        if self.table_name in self.client.fail_tables:
            raise RuntimeError(f"{self.table_name} unavailable")
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
        self.fail_tables = set()
        self.selects = []
        self.rows = {
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Condor Dynasty", "owner_name": "Owner One", "sleeper_roster_id": "1"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Rival Team", "owner_name": "Owner Two", "sleeper_roster_id": "2"},
                {"id": "team-3", "league_id": "league-1", "team_name": "Twin Name", "owner_name": "Owner Two", "sleeper_roster_id": "3"},
                {"id": "team-x", "league_id": "league-2", "team_name": "Condor Dynasty", "owner_name": "Owner One", "sleeper_roster_id": "9"},
            ],
            "transaction_ledger": [
                {"id": "trade-1", "league_id": "league-1", "season": 2026, "created_at": "2026-01-01", "transaction_type": "trade", "league_team_id": "team-2", "added_player_id": "p1"},
                {"id": "trade-1", "league_id": "league-1", "season": 2026, "created_at": "2026-01-01", "transaction_type": "trade", "league_team_id": "team-2", "added_player_id": "p1"},
                {"id": "trade-2", "league_id": "league-1", "season": 2026, "created_at": "2026-02-01", "transaction_type": "trade", "league_team_id": "team-2", "dropped_player_id": "p2"},
                {"id": "trade-3", "league_id": "league-1", "season": 2027, "created_at": "2026-03-01", "transaction_type": "trade", "league_team_id": "team-2", "pick_label": "2028 1st", "direction": "out"},
                {"id": "trade-4", "league_id": "league-1", "season": 2027, "created_at": "2026-04-01", "transaction_type": "trade", "league_team_id": "team-2", "pick_label": "2029 1st", "direction": "out"},
                {"id": "trade-5", "league_id": "league-1", "season": 2027, "created_at": "2026-04-15", "transaction_type": "trade", "from_team": "Condor Dynasty", "to_team": "Rival Team", "pick_label": "2028 2nd", "direction": "in"},
                {"id": "fa-1", "league_id": "league-1", "season": 2026, "created_at": "2026-05-01", "transaction_type": "free_agent", "league_team_id": "team-1", "added_player_id": "p3"},
                {"id": "drop-1", "league_id": "league-1", "season": 2026, "created_at": "2026-05-02", "transaction_type": "drop", "roster_id": "1", "dropped_player_id": "p4"},
                {"id": "draft-1", "league_id": "league-1", "season": 2026, "created_at": "2026-06-01", "transaction_type": "draft_selection", "league_team_id": "team-1", "player_id": "rookie-1"},
                {"id": "bad-1", "league_id": "league-1", "season": 2026, "created_at": "2026-07-01", "transaction_type": "mystery", "league_team_id": "missing-team"},
                {"id": "xtrade", "league_id": "league-2", "season": 2026, "created_at": "2026-01-01", "transaction_type": "trade", "league_team_id": "team-x", "added_player_id": "x"},
            ],
            "transactions_enriched": [],
            "team_roster_state": [
                {"league_id": "league-1", "team_id": "team-1", "player_name": "Josh Allen", "position": "QB", "status": "active"},
                {"league_id": "league-1", "team_id": "team-1", "player_name": "Rookie RB", "position": "RB", "status": "taxi"},
                {"league_id": "league-1", "team_id": "team-2", "player_name": "Veteran WR", "position": "WR", "status": "ir"},
                {"league_id": "league-2", "team_id": "team-x", "player_name": "Leak", "position": "QB"},
            ],
            "contracts": [
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Josh Allen", "player_position": "QB", "salary": 40, "contract_years_left": 2},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Expiring RB", "player_position": "RB", "salary": 5, "contract_years_left": 1},
                {"league_id": "league-1", "owner_name": "Owner Two", "player_name": "Veteran WR", "player_position": "WR", "salary": 30, "contract_years_left": 1},
                {"league_id": "league-2", "owner_name": "Owner One", "player_name": "Leak", "player_position": "QB", "salary": 99, "contract_years_left": 1},
            ],
            "v_team_caps": [
                {"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "available_cap": 12},
                {"league_id": "league-1", "league_team_id": "team-2", "season": 2026, "available_cap": 30},
            ],
            "draft_picks": [
                {"league_id": "league-1", "season": 2028, "round": 1, "current_owner": "Owner One", "original_team": "Owner One", "pick_label": "2028 1st"},
                {"league_id": "league-1", "season": 2028, "round": 2, "current_owner": "Rival Team", "original_team": "Condor Dynasty", "pick_label": "2028 2nd"},
            ],
            "team_brain": [],
            "league_brain": [],
            "league_settings": [],
            "league_rules": [{"league_id": "league-1", "salary_cap": 100}],
            "player_strategic_profiles": [],
            "league_relative_player_values": [],
            "player_intelligence": [],
            "rookie_draft_board": [],
            "player_prospect_context": [],
            "rookie_class_registry": [],
            "rookie_draft_results": [],
            "draft_selections": [],
            "cap_adjustments": [],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


def context(**overrides):
    data = {
        "user_id": "user-1",
        "league_id": "league-1",
        "league_team_id": "team-1",
        "membership_id": "membership-1",
        "role": "owner",
        "current_season": 2026,
        "requested_season": 2026,
        "permission_scopes": (TEAM_ADVICE, LEAGUE_PUBLIC_READ),
        "conversation_id": "conversation-1",
        "team_name": "Condor Dynasty",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


class LeagueOwnerIntelligenceTest(unittest.TestCase):
    def test_canonical_team_identity_and_co_owner_no_duplicate_profile(self):
        ctx = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context())

        self.assertEqual([profile.identity.league_team_id for profile in ctx.profiles], ["team-1", "team-2", "team-3"])
        self.assertEqual(ctx.profiles[0].identity.display_name, "Condor Dynasty")

    def test_team_name_resolution_owner_name_resolution_and_ambiguity(self):
        service = LeagueOwnerIntelligenceService(FakeClient())

        self.assertEqual(service.resolve_team_reference(context(), "Rival Team").league_team_id, "team-2")
        self.assertEqual(service.resolve_team_reference(context(), "1").matched_by, "sleeper_roster_id")
        ambiguous = service.resolve_team_reference(context(), "Owner Two")
        self.assertEqual(ambiguous.status, "ambiguous")
        self.assertEqual(set(ambiguous.candidates), {"team-2", "team-3"})

    def test_duplicate_owner_name_across_leagues_stays_isolated(self):
        ctx = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context())

        self.assertNotIn("team-x", [profile.identity.league_team_id for profile in ctx.profiles])
        self.assertEqual(ctx.profile_for_team("team-1").current_state.roster_count, 2)

    def test_transaction_normalization_supported_categories_and_malformed_row(self):
        ctx = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context())
        categories = {item.action_category for item in ctx.observed_transactions}

        self.assertIn(TransactionActionCategory.TRADE_PLAYER_IN.value, categories)
        self.assertIn(TransactionActionCategory.TRADE_PLAYER_OUT.value, categories)
        self.assertIn(TransactionActionCategory.TRADE_PICK_OUT.value, categories)
        self.assertIn(TransactionActionCategory.FREE_AGENT_ADD.value, categories)
        self.assertIn(TransactionActionCategory.PLAYER_RELEASE.value, categories)
        self.assertIn(TransactionActionCategory.DRAFT_SELECTION.value, categories)
        self.assertIn(TransactionActionCategory.UNSUPPORTED.value, categories)
        self.assertTrue(any("duplicate_transaction" in conflict for conflict in ctx.conflicts))
        self.assertTrue(any("no_canonical_team_resolved" in item.warnings for item in ctx.observed_transactions))

    def test_current_team_state_summary(self):
        profile = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context()).profile_for_team("team-1")

        self.assertEqual(profile.current_state.roster_count, 2)
        self.assertEqual(profile.current_state.positional_counts["QB"], 1)
        self.assertEqual(profile.current_state.contract_count, 2)
        self.assertEqual(profile.current_state.committed_salary, 45.0)
        self.assertEqual(profile.current_state.available_cap, 12.0)
        self.assertEqual(profile.current_state.future_pick_counts_by_round["1"], 1)
        self.assertEqual(profile.current_state.taxi_count, 1)
        self.assertEqual(profile.current_state.expiring_contract_count, 1)

    def test_observation_window_and_unavailable_history(self):
        available = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context())
        self.assertEqual(available.observation_window.first_recorded_transaction, "2026-01-01")
        self.assertIn(2027, available.observation_window.seasons_included)

        client = FakeClient()
        client.rows["transaction_ledger"] = []
        unavailable = LeagueOwnerIntelligenceService(client).get_context(context=context())
        self.assertEqual(unavailable.observation_window.history_state, "unavailable")
        self.assertIn("transaction_history_unavailable", unavailable.warnings)

    def test_complete_zero_activity_is_distinct_from_unavailable_history(self):
        client = FakeClient()
        client.rows["transaction_ledger"] = [{"id": "unsupported", "league_id": "league-1", "transaction_type": "mystery", "league_team_id": "team-1"}]

        ctx = LeagueOwnerIntelligenceService(client).get_context(context=context())

        self.assertEqual(ctx.observation_window.history_state, "available")
        self.assertEqual(ctx.profile_for_team("team-3").activity_summary.transaction_count, 0)
        self.assertIn("no_supported_transaction_records_for_team", ctx.profile_for_team("team-3").activity_summary.warnings)

    def test_tendencies_require_thresholds_and_low_evidence_is_unlabeled(self):
        ctx = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context())
        team_two = ctx.profile_for_team("team-2")
        team_one = ctx.profile_for_team("team-1")
        kinds = {item.tendency_type for item in team_two.tendencies}

        self.assertIn(BehavioralTendencyType.ACTIVE_TRADER.value, kinds)
        self.assertIn(BehavioralTendencyType.FUTURE_FIRST_SELLER.value, kinds)
        self.assertFalse(team_one.tendencies)

    def test_net_pick_acquirer_threshold(self):
        client = FakeClient()
        client.rows["transaction_ledger"].extend([
            {"id": "pick-in-1", "league_id": "league-1", "season": 2027, "created_at": "2026-08-01", "transaction_type": "trade", "league_team_id": "team-1", "pick_label": "2028 2nd", "direction": "in"},
            {"id": "pick-in-2", "league_id": "league-1", "season": 2027, "created_at": "2026-08-02", "transaction_type": "trade", "league_team_id": "team-1", "pick_label": "2029 3rd", "direction": "in"},
        ])

        profile = LeagueOwnerIntelligenceService(client).get_context(context=context()).profile_for_team("team-1")

        self.assertIn(BehavioralTendencyType.NET_PICK_ACQUIRER.value, {item.tendency_type for item in profile.tendencies})

    def test_trade_partner_history_and_authenticated_owner_history(self):
        history = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context()).profile_for_team("team-2").trade_partner_history

        self.assertTrue(any(item.team_a_id == "team-1" and item.team_b_id == "team-2" for item in history))
        self.assertGreaterEqual(LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context()).profile_for_team("team-2").activity_summary.trades_with_authenticated_team, 1)

    def test_scope_requires_trusted_context_and_refuses_outside_target(self):
        service = LeagueOwnerIntelligenceService(FakeClient())
        with self.assertRaises(Exception):
            service.get_context(context=context(league_team_id=""))

        unavailable = service.get_context(context=context(), target_team_id="team-x")
        self.assertEqual(unavailable.availability, "unavailable")

    def test_bounded_repository_calls_for_representative_league(self):
        client = FakeClient()
        client.rows["league_teams"] = [
            {"id": f"team-{idx}", "league_id": "league-1", "team_name": f"Team {idx}", "owner_name": f"Owner {idx}", "sleeper_roster_id": str(idx)}
            for idx in range(1, 11)
        ]

        LeagueOwnerIntelligenceService(client).get_context(context=context())

        table_reads = [item[0] for item in client.selects]
        self.assertLessEqual(table_reads.count("league_teams"), 1)
        self.assertLessEqual(table_reads.count("transaction_ledger"), 1)
        self.assertLessEqual(table_reads.count("transactions_enriched"), 1)
        self.assertLessEqual(table_reads.count("contracts"), 1)
        self.assertLessEqual(table_reads.count("draft_picks"), 1)

    def test_scenario_pipeline_still_works_and_context_is_optional(self):
        result = run_assistant_pipeline(
            context=context(),
            question="What happens if I cut Josh Allen?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
            interpreter_sb=FakeClient(),
        )

        self.assertEqual(result.interpreted_question.primary_intent, "scenario_simulation")
        self.assertTrue(result.displayed_answer)
        self.assertEqual(result.league_owner_intelligence_context.availability, "available")

    def test_initial_supported_questions_answer_as_direct_facts(self):
        cases = {
            "Who trades the most in this league?": "most verified trade activity",
            "Which teams have acquired the most future picks?": "acquired the most verified picks",
            "Who owns the most future first-round picks?": "future first-round picks",
            "Which teams have the most cap space?": "verified cap space",
            "Which teams currently need a quarterback?": "verified roster counts",
            "Who might have the assets to trade for Josh Allen?": "verified cap and pick assets only",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                result = run_assistant_pipeline(
                    context=context(),
                    question=question,
                    retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
                    interpreter_sb=FakeClient(),
                )
                self.assertEqual(result.interpreted_question.primary_intent, "data_lookup")
                self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
                self.assertIn(expected, result.displayed_answer)
                self.assertNotIn("likely to accept", result.displayed_answer.lower())
                self.assertNotIn("validated recommendation", result.displayed_answer.lower())

    def test_have_i_traded_with_team_before_uses_scoped_team_reference(self):
        result = run_assistant_pipeline(
            context=context(),
            question="Have I traded with Rival Team before?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
            interpreter_sb=FakeClient(),
        )

        self.assertIn("Rival Team", result.displayed_answer)
        self.assertIn("verified recorded trades", result.displayed_answer)

    def test_owner_preferences_do_not_label_opponents(self):
        result = run_assistant_pipeline(
            context=context(),
            question="Who is on my team?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
            interpreter_sb=FakeClient(),
            owner_preferences={"team_build_preference": "rebuild"},
        )

        packet = result.league_owner_intelligence_context.to_packet()
        self.assertNotIn("rebuild", repr(packet).lower())

    def test_no_acceptance_prediction_or_psychological_labels(self):
        ctx = LeagueOwnerIntelligenceService(FakeClient()).get_context(context=context())
        text = repr(ctx.to_packet()).lower()

        for forbidden in ("likely to accept", "easy to trade", "desperate", "stubborn", "gullible", "psychological"):
            self.assertNotIn(forbidden, text)

    def test_source_contains_no_mutation_or_external_provider_calls(self):
        source = inspect.getsource(league_owner_service_source)

        for forbidden in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc(", "openai", "requests."):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
