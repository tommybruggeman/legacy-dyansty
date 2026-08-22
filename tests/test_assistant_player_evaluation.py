from __future__ import annotations

import unittest

from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.openai_reasoning.prompt_builder import build_reasoning_request
from gm_assistant.player_evaluation import PlayerEvaluationService
from gm_assistant.runtime import _evidence_diagnostics
from tests.test_assistant_football_intelligence import FakeClient, context, player


def seeded_client() -> FakeClient:
    client = FakeClient()
    roster = [
        player("a", "Alpha QB", "QB", salary=48, years=3),
        player("b", "Balanced WR", "WR", salary=14, years=2),
        player("c", "Future RB", "RB", salary=6, years=4),
        player("d", "Cheap TE", "TE", salary=1, years=1),
        player("x", "Other Team Star", "WR", salary=1, years=4, team_id="team-2"),
    ]
    client.rows["team_roster_state"] = roster
    client.rows["contracts"] = [
        {
            "league_id": row["league_id"],
            "league_team_id": row["team_id"],
            "owner_name": "Owner One" if row["team_id"] == "team-1" else "Owner Two",
            "sleeper_player_id": row["sleeper_id"],
            "player_name": row["player_name"],
            "player_position": row["position"],
            "salary": row["salary"],
            "contract_years_left": row["contract_years_left"],
            "status": row["status"],
        }
        for row in roster
    ]
    client.rows["player_strategic_profiles"] = [
        profile("a", "Alpha QB", "QB", 94, 82, median=24),
        profile("b", "Balanced WR", "WR", 86, 88, median=18),
        profile("c", "Future RB", "RB", 58, 94, median=8),
        profile("d", "Cheap TE", "TE", 55, 45, median=6),
        profile("x", "Other Team Star", "WR", 99, 99, team_id="team-2"),
        profile("z", "Cross League Star", "WR", 100, 100, league_id="league-2", team_id="team-x"),
    ]
    client.rows["league_relative_player_values"] = [
        value("a", "Alpha QB", 91),
        value("b", "Balanced WR", 89),
        value("c", "Future RB", 77),
        value("d", "Cheap TE", 42),
        value("x", "Other Team Star", 99, team_id="team-2"),
        value("z", "Cross League Star", 100, league_id="league-2", team_id="team-x"),
    ]
    client.rows["player_universe"] = [
        universe("a", "Alpha QB", "QB", expected_ppg=24, dynasty=82, future=80, market=78),
        universe("b", "Balanced WR", "WR", expected_ppg=18, dynasty=88, future=87, market=86),
        universe("c", "Future RB", "RB", expected_ppg=8, dynasty=94, future=95, market=90, rookie=70, years_exp=0),
        universe("d", "Cheap TE", "TE", expected_ppg=6, dynasty=45, future=43, market=40),
        universe("x", "Other Team Star", "WR", expected_ppg=30, dynasty=99, future=99, market=99),
    ]
    client.rows["player_intelligence"] = [
        intel("a", "Alpha QB", "QB", recent=92, trade=80, rank=75),
        intel("b", "Balanced WR", "WR", recent=84, trade=88, rank=82),
        intel("c", "Future RB", "RB", recent=12, trade=90, rank=60, seasons=0),
        intel("d", "Cheap TE", "TE", recent=35, trade=35, rank=20),
        intel("x", "Other Team Star", "WR", recent=99, trade=99, rank=99),
    ]
    return client


def profile(player_id, name, pos, win_now, asset, *, median=None, league_id="league-1", team_id="team-1"):
    return {
        "league_id": league_id,
        "league_team_id": team_id,
        "sleeper_id": player_id,
        "player_name": name,
        "position": pos,
        "win_now_score": win_now,
        "asset_score": asset,
        "median_projection": median,
        "volatility_label": "low",
    }


def value(player_id, name, score, *, league_id="league-1", team_id="team-1"):
    return {
        "league_id": league_id,
        "league_team_id": team_id,
        "sleeper_id": player_id,
        "player_name": name,
        "overall_value_score": score,
        "overall_percentile": score,
        "league_value_tier": "tier",
}


def universe(player_id, name, pos, *, expected_ppg, dynasty, future, market, rookie=0, years_exp=4):
    return {
        "sleeper_id": player_id,
        "player_name": name,
        "pos": pos,
        "expected_ppg": expected_ppg,
        "dynasty_asset_score": dynasty,
        "future_projection_score": future,
        "market_consensus_score": market,
        "rookie_asset_score": rookie,
        "years_exp": years_exp,
    }


def intel(player_id, name, pos, *, recent, trade, rank, seasons=4):
    return {
        "sleeper_id": player_id,
        "player_name": name,
        "pos": pos,
        "recent_production_score": recent,
        "trade_value_score": trade,
        "rank_score": rank,
        "seasons_played": seasons,
    }


class PlayerEvaluationServiceTest(unittest.TestCase):
    def test_all_scoped_roster_players_are_evaluated_when_data_exists(self):
        evaluations = PlayerEvaluationService(seeded_client()).evaluate_roster(context())

        self.assertEqual({item.player_id for item in evaluations}, {"a", "b", "c", "d"})
        self.assertTrue(all(item.status == "evaluated" for item in evaluations))
        self.assertTrue(all(item.league_id == "league-1" for item in evaluations))
        self.assertTrue(all(item.league_team_id == "team-1" for item in evaluations))

    def test_no_players_from_another_team_appear(self):
        evaluations = PlayerEvaluationService(seeded_client()).evaluate_roster(context())

        self.assertNotIn("x", {item.player_id for item in evaluations})
        self.assertNotIn("z", {item.player_id for item in evaluations})

    def test_missing_values_do_not_become_zero(self):
        client = seeded_client()
        client.rows["player_strategic_profiles"] = [
            row for row in client.rows["player_strategic_profiles"] if row["sleeper_id"] != "c"
        ]

        future = next(item for item in PlayerEvaluationService(client).evaluate_roster(context()) if item.player_id == "c")

        self.assertEqual(future.status, "evaluated")
        self.assertGreater(future.neutral_overall_value, 0)
        self.assertNotEqual(future.neutral_overall_value, 0)
        self.assertNotIn("player_strategic_profiles", future.source_rows_used)
        self.assertIn("player_universe", future.source_rows_used)
        self.assertIn("player_intelligence", future.source_rows_used)

    def test_current_and_future_scores_can_differ(self):
        evaluations = PlayerEvaluationService(seeded_client()).evaluate_roster(context())
        alpha = next(item for item in evaluations if item.player_id == "a")

        self.assertIsNotNone(alpha.current_contribution_score)
        self.assertIsNotNone(alpha.future_outlook_score)
        self.assertNotEqual(alpha.current_contribution_score, alpha.future_outlook_score)

    def test_rookie_future_value_is_not_forced_to_current_contribution(self):
        evaluations = PlayerEvaluationService(seeded_client()).evaluate_roster(context())
        future = next(item for item in evaluations if item.player_id == "c")

        self.assertTrue(future.rookie_prospect_pathway_used)
        self.assertGreater(future.future_outlook_score, future.current_contribution_score)

    def test_missing_player_is_not_assigned_zero_value(self):
        client = seeded_client()
        for table in ("player_strategic_profiles", "league_relative_player_values", "player_universe", "player_intelligence"):
            client.rows[table] = [row for row in client.rows.get(table, []) if row.get("sleeper_id") != "d"]

        missing = next(item for item in PlayerEvaluationService(client).evaluate_roster(context()) if item.player_id == "d")

        self.assertIsNone(missing.neutral_overall_value)
        self.assertEqual(missing.status, "insufficient_data")
        self.assertNotEqual(missing.neutral_overall_value, 0)

    def test_salary_alone_cannot_make_player_rank_first(self):
        client = seeded_client()
        client.rows["player_strategic_profiles"].append(profile("cheap", "Minimum Salary", "WR", 20, 20))
        client.rows["league_relative_player_values"].append(value("cheap", "Minimum Salary", 20))
        client.rows["team_roster_state"].append(player("cheap", "Minimum Salary", "WR", salary=1, years=4))
        client.rows["contracts"].append({
            "league_id": "league-1",
            "league_team_id": "team-1",
            "owner_name": "Owner One",
            "sleeper_player_id": "cheap",
            "player_name": "Minimum Salary",
            "player_position": "WR",
            "salary": 1,
            "contract_years_left": 4,
        })

        evaluations = PlayerEvaluationService(client).evaluate_roster(context())

        self.assertNotEqual(evaluations[0].player_id, "cheap")

    def test_positional_premium_is_not_applied_twice(self):
        evaluations = PlayerEvaluationService(seeded_client()).evaluate_roster(context())

        self.assertTrue(all(not item.positional_adjustment_applied for item in evaluations))
        self.assertTrue(any("league_relative_player_values" in (item.positional_adjustment_source or "") for item in evaluations))

    def test_ordering_is_deterministic(self):
        client = seeded_client()
        client.rows["team_roster_state"] = [
            player("2", "Same Player", "WR", salary=10, years=2),
            player("1", "Same Player", "WR", salary=10, years=2),
        ]
        client.rows["contracts"] = [
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_player_id": "2", "player_name": "Same Player", "player_position": "WR", "salary": 10, "contract_years_left": 2},
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_player_id": "1", "player_name": "Same Player", "player_position": "WR", "salary": 10, "contract_years_left": 2},
        ]
        client.rows["player_strategic_profiles"] = [profile("1", "Same Player", "WR", 80, 80), profile("2", "Same Player", "WR", 80, 80)]
        client.rows["league_relative_player_values"] = [value("1", "Same Player", 80), value("2", "Same Player", 80)]

        evaluations = PlayerEvaluationService(client).evaluate_roster(context())

        self.assertEqual([item.player_id for item in evaluations], ["1", "2"])

    def test_best_top_three_and_rank_roster_use_same_evaluator(self):
        for question in ("Who is my best player?", "Who are my three best players?", "Rank my roster."):
            with self.subTest(question=question):
                result = run_assistant_pipeline(
                    context=context(),
                    question=question,
                    retrieval_provider=SupabaseEvidenceRetrievalProvider(seeded_client()),
                )
                self.assertIn("player_evaluations", {item.retrieval_type for item in result.decision_plan.retrieval_requests})
                self.assertEqual(len(result.evidence_packet.player_evaluation_evidence), 4)

    def test_roster_ranking_variants_request_player_evaluations(self):
        cases = [
            "Rank my roster",
            "Rank my entire roster",
            "Rank my full roster",
            "Rank all my players",
            "Rank my players from best to worst",
            "Give me a best-to-worst roster ranking",
            "Who are the best players on my roster?",
            "Show me my top five players",
            "Who are my three best players?",
            "List my strongest players",
            "Order my roster by value",
        ]
        for question in cases:
            with self.subTest(question=question):
                result = run_assistant_pipeline(
                    context=context(),
                    question=question,
                    retrieval_provider=SupabaseEvidenceRetrievalProvider(seeded_client()),
                )

                self.assertIn("player_evaluations", {item.retrieval_type for item in result.decision_plan.retrieval_requests})
                self.assertEqual(len(result.evidence_packet.player_evaluation_evidence), 4)
                self.assertNotIn("roster-construction fallback", result.displayed_answer.lower())

    def test_openai_receives_complete_evaluated_roster_with_fact_refs(self):
        client = seeded_client()
        client.rows["team_roster_state"] = [
            player(str(index), f"Player {index:02d}", "WR", salary=10, years=2)
            for index in range(22)
        ]
        client.rows["contracts"] = [
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_player_id": str(index), "player_name": f"Player {index:02d}", "player_position": "WR", "salary": 10, "contract_years_left": 2}
            for index in range(22)
        ]
        client.rows["player_strategic_profiles"] = [
            profile(str(index), f"Player {index:02d}", "WR", 80 - index, 80 - index)
            for index in range(22)
        ]
        client.rows["league_relative_player_values"] = [
            value(str(index), f"Player {index:02d}", 80 - index)
            for index in range(22)
        ]
        pipeline = run_assistant_pipeline(
            context=context(),
            question="Rank my roster.",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(client),
        )

        request = build_reasoning_request(
            request_id="request-1",
            league_id="league-1",
            league_team_id="team-1",
            question="Rank my roster.",
            conversation_history=[],
            conversation_state=pipeline.conversation_state,
            interpreted_question=pipeline.interpreted_question,
            owner_objective=pipeline.owner_objective,
            decision_plan=pipeline.decision_plan,
            evidence_packet=pipeline.evidence_packet,
            rules_evaluation=pipeline.rules_evaluation,
            calculation_packet=pipeline.calculation_packet,
            decision_output=pipeline.decision_output,
            recommendation_validation=pipeline.recommendation_validation,
            answer_packet=pipeline.answer_packet,
            football_intelligence_context=pipeline.football_intelligence_context,
        )

        evaluations = request.player_intelligence["player_evaluations"]
        self.assertEqual(len(evaluations), 22)
        self.assertIn(evaluations[0]["fact_id"], request.allowed_fact_refs)
        self.assertIn("22", request.validation_constraints["authoritative_numbers"])
        self.assertIn("ranking_instruction", request.player_intelligence)

    def test_full_roster_query_includes_22_evaluations_and_diagnostics(self):
        client = seeded_client()
        client.rows["team_roster_state"] = [
            player(str(index), f"Player {index:02d}", "WR", salary=10, years=2)
            for index in range(22)
        ]
        client.rows["contracts"] = [
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_player_id": str(index), "player_name": f"Player {index:02d}", "player_position": "WR", "salary": 10, "contract_years_left": 2}
            for index in range(22)
        ]
        client.rows["player_strategic_profiles"] = [
            profile(str(index), f"Player {index:02d}", "WR", 90 - index, 90 - index, median=20 - (index / 2))
            for index in range(22)
        ]
        client.rows["league_relative_player_values"] = [
            value(str(index), f"Player {index:02d}", 90 - index)
            for index in range(22)
        ]
        client.rows["player_universe"] = [
            universe(str(index), f"Player {index:02d}", "WR", expected_ppg=20 - (index / 2), dynasty=90 - index, future=90 - index, market=90 - index)
            for index in range(22)
        ]
        client.rows["player_intelligence"] = [
            intel(str(index), f"Player {index:02d}", "WR", recent=90 - index, trade=90 - index, rank=90 - index)
            for index in range(22)
        ]

        result = run_assistant_pipeline(
            context=context(),
            question="Rank my entire roster from best to worst and briefly explain the top five.",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(client),
        )
        diagnostics = _evidence_diagnostics(result.evidence_packet)

        self.assertEqual(len(result.evidence_packet.player_evaluation_evidence), 22)
        self.assertTrue(diagnostics["player_evaluation_requested"])
        self.assertTrue(diagnostics["player_evaluation_included"])
        self.assertEqual(diagnostics["evaluated_player_count"], 22)
        self.assertGreater(diagnostics["player_evaluation_fact_ref_count"], 22)


if __name__ == "__main__":
    unittest.main()
