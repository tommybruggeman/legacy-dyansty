from __future__ import annotations

import unittest

from gm_assistant.draft_intelligence import (
    DraftIntelligenceAvailability,
    DraftIntelligenceService,
    parse_pick_references,
)
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.planning import RetrievalRequest
from tests.test_assistant_production_evidence_plumbing import FakeClient, make_context, run_question


def pick_request(kind="draft_pick", *, pick_ids=None, team_ids=None, seasons=None):
    return RetrievalRequest(
        retrieval_type=kind,
        scope="league" if not team_ids else "team",
        pick_ids=list(pick_ids or []),
        team_ids=list(team_ids or []),
        player_ids=[],
        seasons=list(seasons or [2026]),
        filters={"league_id": "league-1"},
        required=True,
        reason="Load draft context.",
    )


class DraftPickParserTest(unittest.TestCase):
    def parsed(self, text):
        return parse_pick_references(text, current_team_id="team-1")

    def test_exact_pick_labels(self):
        cases = [("1.01", "1.01"), ("1.02", "1.02"), ("2.07", "2.07")]
        for raw, label in cases:
            with self.subTest(raw=raw):
                refs = self.parsed(raw)
                self.assertEqual(refs[0].reference_type, "exact_slot")
                self.assertEqual(refs[0].label, label)

    def test_ordinal_overall_and_pick_language(self):
        expectations = {
            "first overall": "1.01",
            "second overall": "1.02",
            "third overall": "1.03",
            "first pick": "1.01",
            "second pick": "1.02",
            "pick two": "1.02",
        }
        for raw, label in expectations.items():
            with self.subTest(raw=raw):
                refs = self.parsed(raw)
                self.assertEqual(len(refs), 1)
                self.assertEqual(refs[0].label, label)
                self.assertEqual(refs[0].reference_type, "exact_slot")

    def test_second_overall_does_not_create_duplicate_textual_second_pick(self):
        refs = self.parsed("Who should I draft with the second overall rookie draft pick?")

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].label, "1.02")
        self.assertEqual(refs[0].raw_text, "second overall")

    def test_future_round_assets_are_not_exact_slots(self):
        for raw in ("2027 first", "2028 second", "2028 2nd"):
            with self.subTest(raw=raw):
                refs = self.parsed(raw)
                self.assertEqual(refs[0].reference_type, "future_asset")
                self.assertIsNone(refs[0].slot)

    def test_round_only_does_not_become_second_overall(self):
        refs = self.parsed("second-round pick")
        self.assertEqual(refs[0].reference_type, "round_only")
        self.assertEqual(refs[0].round, 2)
        self.assertIsNone(refs[0].slot)

        refs = self.parsed("round two")
        self.assertEqual(refs[0].reference_type, "round_only")
        self.assertEqual(refs[0].round, 2)
        self.assertIsNone(refs[0].slot)

    def test_malformed_pick_label_is_unresolved(self):
        self.assertEqual(self.parsed("pick banana"), [])


class DraftIntelligenceServiceTest(unittest.TestCase):
    def client_with_draft(self):
        client = FakeClient()
        client.rows["draft_picks"] = [
            {"league_id": "league-1", "season": 2026, "round": 1, "pick_label": "1.02", "original_pick_rank": 2, "current_owner": "Owner One", "original_team": "Owner Two", "status": "available"},
            {"league_id": "league-1", "season": 2027, "round": 1, "current_owner": "Owner One", "original_team": "Owner One", "status": "available"},
            {"league_id": "league-1", "season": 2028, "round": 2, "current_owner": "Owner Two", "original_team": "Owner One", "status": "available"},
            {"league_id": "league-1", "season": 2026, "round": 2, "pick_label": "2.04", "current_owner": "Owner Two", "original_team": "Owner One", "status": "available"},
            {"league_id": "league-2", "season": 2026, "round": 1, "pick_label": "1.02", "current_owner": "Owner One", "original_team": "Owner One", "status": "available"},
        ]
        client.rows["rookie_draft_board"] = [
            {"league_id": "league-1", "draft_year": 2026, "round": 1, "pick": 1, "player_name": "Selected Prospect", "sleeper_id": "rookie-1", "position": "QB", "rookie_rank": 1},
            {"league_id": "league-1", "draft_year": 2026, "round": 1, "pick": 2, "player_name": "Available Prospect", "sleeper_id": "rookie-2", "position": "RB", "rookie_rank": 2},
        ]
        client.rows["rookie_draft_results"] = [
            {"league_id": "league-1", "draft_year": 2026, "round": 1, "pick": 1, "player_name": "Selected Prospect", "sleeper_id": "rookie-1", "position": "QB"}
        ]
        client.rows["player_intelligence"] = [{"sleeper_id": "rookie-2", "player_name": "Available Prospect", "position": "RB"}]
        return client

    def test_owned_acquired_and_traded_away_picks(self):
        ctx = DraftIntelligenceService(self.client_with_draft()).get_context(make_context(), season=2026)

        owned_labels = [pick.pick_label for pick in ctx.owned_picks]
        self.assertIn("1.02", owned_labels)
        self.assertNotIn("2.04", owned_labels)
        acquired = next(pick for pick in ctx.owned_picks if pick.pick_label == "1.02")
        self.assertEqual(acquired.current_owner_team_id, "team-1")
        self.assertEqual(acquired.original_team_id, "team-2")

    def test_future_round_only_asset_has_no_verified_exact_slot(self):
        ctx = DraftIntelligenceService(self.client_with_draft()).get_context(make_context(), season=2027)

        self.assertEqual(ctx.owned_picks[0].round, 1)
        self.assertIsNone(ctx.owned_picks[0].exact_slot)

    def test_requested_exact_slot_filters_context(self):
        ctx = DraftIntelligenceService(self.client_with_draft()).get_context(make_context(), season=2026, requested_pick_labels=["1.02"])

        self.assertEqual(len(ctx.requested_picks), 1)
        self.assertEqual(ctx.requested_picks[0].pick_label, "1.02")
        self.assertEqual(ctx.requested_picks[0].exact_slot.display_label, "1.02")

    def test_missing_prospect_pool_is_explicit_not_fabricated(self):
        client = FakeClient()
        client.rows["draft_picks"] = [{"league_id": "league-1", "season": 2026, "round": 1, "pick_label": "1.02", "current_owner": "Owner One", "original_team": "Owner One"}]

        ctx = DraftIntelligenceService(client).get_context(make_context(), season=2026)

        self.assertIn(DraftIntelligenceAvailability.PROSPECT_POOL_UNAVAILABLE.value, ctx.availability_states)
        self.assertIn("prospect_pool", ctx.completeness.missing_groups)

    def test_completed_selection_excludes_selected_prospect_from_available_pool(self):
        client = self.client_with_draft()
        ctx = DraftIntelligenceService(client).get_context(make_context(), season=2026)

        names = [prospect.player_name for prospect in ctx.board_state.available_prospects]
        self.assertIn("Available Prospect", names)
        self.assertNotIn("Selected Prospect", names)

    def test_cross_league_duplicate_owner_name_is_filtered_by_league(self):
        ctx = DraftIntelligenceService(self.client_with_draft()).get_context(make_context(), season=2026)

        self.assertTrue(all(pick.league_id == "league-1" for pick in ctx.owned_picks))
        self.assertEqual([pick.pick_label for pick in ctx.owned_picks], ["1.02"])

    def test_batched_reads_for_representative_board(self):
        client = self.client_with_draft()
        DraftIntelligenceService(client).get_context(make_context(), season=2026, requested_pick_labels=["1.02"])

        self.assertLessEqual(len(client.selects), 17)

    def test_evidence_provider_uses_draft_intelligence_rows(self):
        result = SupabaseEvidenceRetrievalProvider(self.client_with_draft()).get_draft_picks(make_context(), pick_request(pick_ids=["1.02"]))

        self.assertEqual(result.source_name, "draft_intelligence")
        self.assertEqual(result.records[0]["pick_label"], "1.02")
        self.assertEqual(result.records[0]["resolved_current_owner_team_id"], "team-1")
        self.assertTrue(result.lineage)

    def test_prospect_pool_provider_returns_verified_existing_rows(self):
        result = SupabaseEvidenceRetrievalProvider(self.client_with_draft()).get_player_profiles(
            make_context(),
            pick_request("prospect_pool"),
        )

        self.assertEqual(result.source_name, "draft_intelligence")
        self.assertTrue(any(row["player_name"] == "Available Prospect" for row in result.records))

    def test_second_overall_question_keeps_limited_missing_prospect_behavior(self):
        client = FakeClient()
        client.rows["draft_picks"] = [{"league_id": "league-1", "season": 2026, "round": 1, "pick_label": "1.02", "current_owner": "Owner One", "original_team": "Owner One"}]

        result = run_question("Who should I draft with the second overall rookie draft pick?", client=client)

        self.assertEqual(result.interpreted_question.pick_refs[0].slot, 2)
        self.assertEqual(result.decision_plan.retrieval_requests[1].pick_ids, ["1.02"])
        self.assertEqual(result.answer_packet.answer_mode, "limited_information")
        self.assertNotIn("second", result.decision_plan.retrieval_requests[1].pick_ids)


if __name__ == "__main__":
    unittest.main()
