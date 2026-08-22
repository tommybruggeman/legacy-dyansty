from __future__ import annotations

import unittest

from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.player_intelligence import PlayerIntelligenceAvailability, PlayerIntelligenceService
from gm_assistant.player_intelligence.normalization import normalize_player_id
from gm_assistant.planning import RetrievalRequest
from tests.test_assistant_repositories import FakeClient, make_context


def request(player_ids=None):
    return RetrievalRequest(
        retrieval_type="player_profile",
        scope="league",
        player_ids=list(player_ids or []),
        team_ids=[],
        seasons=[2026],
        filters={"league_id": "league-1"},
        required=True,
        reason="Load player profile.",
    )


class PlayerIntelligenceServiceTest(unittest.TestCase):
    def test_global_and_scoped_sources_compose_complete_profile(self):
        client = FakeClient()
        client.rows["contracts"][0].update({"status": "active"})
        profile = PlayerIntelligenceService(client).get_profile(make_context(), player_id="4984")

        self.assertEqual(profile.identity.player_id, "4984")
        self.assertEqual(profile.identity.player_name, "Josh Allen")
        self.assertEqual(profile.global_intelligence["global_grade"], "elite")
        self.assertEqual(profile.league_relative_value["value_score"], 95)
        self.assertEqual(profile.league_context.league_team_id, "team-1")
        self.assertIn(profile.availability, {
            PlayerIntelligenceAvailability.FOUND_COMPLETE_ENOUGH.value,
            PlayerIntelligenceAvailability.PARTIAL.value,
        })

    def test_global_intelligence_remains_unscoped(self):
        client = FakeClient()
        PlayerIntelligenceService(client).get_profile(make_context(), player_id="4984", include_league_context=False)

        player_selects = [item for item in client.selects if item[0] == "player_intelligence"]
        self.assertTrue(player_selects)
        self.assertEqual(player_selects[0][2], [])

    def test_contract_only_profile_is_partial_without_global(self):
        client = FakeClient()
        client.rows["player_intelligence"] = []
        client.rows["player_strategic_profiles"] = []
        client.rows["league_relative_player_values"] = []

        profile = PlayerIntelligenceService(client).get_profile(make_context(), player_id="4984")

        self.assertEqual(profile.identity.player_name, "Josh Allen")
        self.assertEqual(profile.availability, PlayerIntelligenceAvailability.GLOBAL_INTELLIGENCE_UNAVAILABLE.value)
        self.assertIn("contract_context", profile.completeness.present_groups)

    def test_missing_player_returns_not_found_without_fake_profile(self):
        profile = PlayerIntelligenceService(FakeClient()).get_profile(make_context(), player_id="missing")

        self.assertEqual(profile.availability, PlayerIntelligenceAvailability.NOT_FOUND.value)
        self.assertEqual(profile.identity.player_id, "missing")

    def test_malformed_requested_id_is_safe(self):
        profile = PlayerIntelligenceService(FakeClient()).get_profile(make_context(), player_id="None")

        self.assertEqual(profile.availability, PlayerIntelligenceAvailability.MALFORMED_SOURCE_DATA.value)
        self.assertIn("requested_player_id_is_missing_or_malformed", profile.warnings)

    def test_ambiguous_exact_name_fallback_does_not_guess(self):
        client = FakeClient()
        client.rows["player_intelligence"] = [
            {"sleeper_id": "p1", "player_name": "Mike Williams"},
            {"sleeper_id": "p2", "player_name": "Mike Williams"},
        ]
        client.rows["player_strategic_profiles"] = []
        client.rows["league_relative_player_values"] = []
        client.rows["team_roster_state"] = []
        client.rows["contracts"] = []

        profile = PlayerIntelligenceService(client).get_profile(make_context(), player_name="Mike Williams")

        self.assertEqual(profile.availability, PlayerIntelligenceAvailability.AMBIGUOUS_IDENTITY.value)
        self.assertIn("player_name_matches_multiple_ids", profile.warnings)

    def test_identity_conflicts_are_recorded_but_deterministic(self):
        client = FakeClient()
        client.rows["player_intelligence"] = [{"sleeper_id": "4984", "player_name": "Josh Allen", "position": "QB"}]
        client.rows["player_strategic_profiles"] = [{"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": "4984", "player_name": "Josh Allen", "position": "RB"}]

        profile = PlayerIntelligenceService(client).get_profile(make_context(), player_id="4984")

        self.assertEqual(profile.identity.position, "QB")
        self.assertEqual(profile.conflicts[0].field, "position")
        self.assertEqual(profile.conflicts[0].rejected_value, "RB")

    def test_normalize_player_id_handles_missing_values_and_exact_strings(self):
        for value in (None, "None", "null", "", "   ", "nan", True):
            with self.subTest(value=value):
                self.assertIsNone(normalize_player_id(value))
        self.assertEqual(normalize_player_id("000012345678901234567890"), "000012345678901234567890")
        self.assertIsNone(normalize_player_id(12_345_678_901_234_567_890.0))

    def test_roster_designations_and_derived_contract_fields(self):
        client = FakeClient()
        client.rows["team_roster_state"] = [{
            "league_id": "league-1",
            "team_id": "team-1",
            "sleeper_id": "taxi-1",
            "player_name": "Taxi Player",
            "position": "WR",
            "roster_designation": "taxi",
        }]
        client.rows["contracts"] = [{
            "league_id": "league-1",
            "league_team_id": "team-1",
            "player_name": "Taxi Player",
            "player_position": "WR",
            "sleeper_player_id": "taxi-1",
            "salary": 12,
            "contract_years_left": 3,
        }]
        client.rows["player_intelligence"] = [{"sleeper_id": "taxi-1", "player_name": "Taxi Player"}]
        client.rows["player_strategic_profiles"] = []
        client.rows["league_relative_player_values"] = []

        profile = PlayerIntelligenceService(client).get_profile(make_context(), player_id="taxi-1")

        self.assertTrue(profile.league_context.is_taxi)
        self.assertEqual(profile.derived_fields["contract_cost_per_remaining_year"], 4.0)

    def test_batch_lookup_is_not_n_plus_one_for_199_players(self):
        client = FakeClient()
        player_ids = [str(index) for index in range(199)]
        client.rows["player_intelligence"] = [{"sleeper_id": player_id, "player_name": f"Player {player_id}"} for player_id in player_ids]
        client.rows["player_strategic_profiles"] = [
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": player_id, "player_name": f"Player {player_id}"}
            for player_id in player_ids
        ]
        client.rows["league_relative_player_values"] = [
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": player_id, "value_score": index}
            for index, player_id in enumerate(player_ids)
        ]
        client.rows["team_roster_state"] = [
            {"league_id": "league-1", "team_id": "team-1", "sleeper_id": player_id, "player_name": f"Player {player_id}"}
            for player_id in player_ids
        ]
        client.rows["contracts"] = [
            {"league_id": "league-1", "league_team_id": "team-1", "sleeper_player_id": player_id, "player_name": f"Player {player_id}"}
            for player_id in player_ids
        ]

        profiles = PlayerIntelligenceService(client).get_profiles(make_context(), player_ids=player_ids)

        self.assertEqual(len(profiles), 199)
        self.assertLessEqual(len(client.selects), 7)

    def test_evidence_provider_returns_compatibility_player_rows_with_lineage(self):
        provider_result = SupabaseEvidenceRetrievalProvider(FakeClient()).get_player_profiles(make_context(), request(["4984"]))

        self.assertEqual(provider_result.source_name, "player_intelligence_profile")
        self.assertEqual(provider_result.records[0]["sleeper_id"], "4984")
        self.assertEqual(provider_result.records[0]["value_score"], 95)
        self.assertTrue(provider_result.lineage)

    def test_evidence_provider_omits_unresolved_profiles(self):
        provider_result = SupabaseEvidenceRetrievalProvider(FakeClient()).get_player_profiles(make_context(), request(["missing"]))

        self.assertEqual(provider_result.records, [])


if __name__ == "__main__":
    unittest.main()
