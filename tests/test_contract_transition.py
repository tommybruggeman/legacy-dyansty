from copy import deepcopy
import unittest

from contract_engine.transition_compatibility import compare_transition_to_legacy
from contract_engine.transition_planner import build_transition_plan


LEAGUE = "league-1"
TEAM = "team-1"


def fixture(years=1):
    seasons = [
        {"id": "ls25", "league_id": LEAGUE, "season": 2025, "status": "active", "is_active": True, "previous_league_season_id": None, "sleeper_league_id": "sl25"},
        {"id": "ls26", "league_id": LEAGUE, "season": 2026, "status": "scheduled", "is_active": False, "previous_league_season_id": "ls25", "sleeper_league_id": "sl26"},
        {"id": "ls27", "league_id": LEAGUE, "season": 2027, "status": "scheduled", "is_active": False, "previous_league_season_id": "ls26", "sleeper_league_id": None},
    ]
    legacy = [{"id": "legacy-1", "league_id": LEAGUE, "league_team_id": TEAM, "sleeper_player_id": "p1", "player_name": "Player One", "contract_years_left": years, "annual_salary": "12.50"}]
    agreements = [{"id": "agreement-1", "league_id": LEAGUE, "league_team_id": TEAM, "player_id": "p1", "sleeper_player_id": "p1", "source_legacy_contract_id": "legacy-1", "status": "active", "contract_type": "standard"}]
    obligations = [{"id": "cs25", "contract_id": "agreement-1", "league_id": LEAGUE, "league_team_id": TEAM, "player_id": "p1", "season": 2025, "salary": "12.50", "cap_hit": "12.50", "status": "active"}]
    if years >= 2:
        obligations.append({"id": "cs26", "contract_id": "agreement-1", "league_id": LEAGUE, "league_team_id": TEAM, "player_id": "p1", "season": 2026, "salary": "12.50", "cap_hit": "12.50", "status": "scheduled"})
    if years >= 3:
        obligations.append({"id": "cs27", "contract_id": "agreement-1", "league_id": LEAGUE, "league_team_id": TEAM, "player_id": "p1", "season": 2027, "salary": "12.50", "cap_hit": "12.50", "status": "scheduled"})
    return dict(league_id=LEAGUE, source_season=2025, target_season=2026, league_seasons=seasons,
        legacy_contracts=legacy, agreements=agreements, contract_seasons=obligations,
        events=[{"id": "event-1", "league_id": LEAGUE, "contract_id": "agreement-1", "event_type": "imported", "effective_season": 2025}],
        teams=[{"id": TEAM, "league_id": LEAGUE, "owner_name": "Owner"}],
        roster_assignments=[{"sleeper_player_id": "p1", "roster_designation": "taxi"}],
        cap_adjustments=[], dead_cap=[], free_agent_source=[], draft_picks=[],
        historical_facts={"season_team_mappings": [], "season_matchups": [], "season_standings": [], "season_playoff_brackets": []})


def plan(data):
    return build_transition_plan(**data, requested_at="2026-07-29T00:00:00+00:00")


class ContractTransitionPlannerTests(unittest.TestCase):
    def test_one_year_contract_expires_without_dead_cap(self):
        result = plan(fixture(1))
        self.assertTrue(result.safe_to_transition)
        self.assertEqual((result.counts["expires"], result.counts["continues"]), (1, 0))
        self.assertEqual(result.classifications[0]["planned_event_type"], "expired")
        self.assertEqual(result.source_fingerprints["dead_cap"], plan(fixture(1)).source_fingerprints["dead_cap"])

    def test_two_year_contract_continues(self):
        result = plan(fixture(2))
        self.assertEqual(result.classifications[0]["outcome"], "CONTINUES")
        self.assertEqual(result.counts["planned_active"], 1)

    def test_three_year_contract_and_missing_2027_metadata_warning(self):
        result = plan(fixture(3))
        self.assertEqual(result.counts["season_2027_obligations"], 1)
        self.assertIn("future_sleeper_metadata_missing", {x["code"] for x in result.warnings})

    def test_free_agent_candidate_preserves_roster_designation(self):
        candidate = plan(fixture(1)).free_agent_candidates[0]
        self.assertEqual(candidate["captured_roster_state"], "taxi")
        self.assertTrue(candidate["remains_on_captured_roster"])
        self.assertEqual(candidate["readiness_status"], "contract_expiration_only_roster_action_not_planned")

    def test_team_projection_uses_decimal_values(self):
        summary = plan(fixture(1)).team_projections[0]
        self.assertEqual(summary["source_active_salary"], "12.50")
        self.assertEqual(summary["projected_salary_reduction"], "12.50")

    def test_missing_source_obligation_blocks(self):
        data = fixture(1); data["contract_seasons"] = []
        self.assertIn("INVALID_MISSING_2025", {x["outcome"] for x in plan(data).classifications})

    def test_gap_in_obligations_blocks(self):
        data = fixture(3); data["contract_seasons"] = [x for x in data["contract_seasons"] if x["season"] != 2026]
        self.assertEqual(plan(data).classifications[0]["outcome"], "INVALID_GAP")

    def test_duplicate_obligation_blocks(self):
        data = fixture(1); duplicate = deepcopy(data["contract_seasons"][0]); duplicate["id"] = "duplicate"
        data["contract_seasons"].append(duplicate)
        self.assertEqual(plan(data).classifications[0]["outcome"], "INVALID_DUPLICATE")

    def test_unsupported_agreement_status_blocks(self):
        data = fixture(1); data["agreements"][0]["status"] = "mystery"
        self.assertFalse(plan(data).safe_to_transition)

    def test_already_transitioned_state_blocks_reexecution(self):
        data = fixture(2); data["contract_seasons"][0]["status"] = "satisfied"; data["contract_seasons"][1]["status"] = "active"
        result = plan(data)
        self.assertEqual(result.classifications[0]["outcome"], "ALREADY_TRANSITIONED")
        self.assertEqual(result.counts["already_transitioned"], 1)
        self.assertFalse(result.safe_to_transition)

    def test_overlapping_live_agreements_block(self):
        data = fixture(1); extra = deepcopy(data["agreements"][0]); extra["id"] = "agreement-2"
        data["agreements"].append(extra)
        self.assertIn("overlapping_agreements", {x["code"] for x in plan(data).blocking_errors})

    def test_authority_must_be_active_to_scheduled_and_linked(self):
        data = fixture(1); data["league_seasons"][1]["previous_league_season_id"] = "wrong"
        self.assertIn("broken_previous_link", {x["code"] for x in plan(data).blocking_errors})

    def test_source_must_be_active(self):
        data = fixture(1); data["league_seasons"][0].update(status="completed", is_active=False)
        self.assertIn("source_not_active", {x["code"] for x in plan(data).blocking_errors})

    def test_target_must_be_scheduled_and_inactive(self):
        data = fixture(1); data["league_seasons"][1].update(status="active", is_active=True)
        self.assertIn("target_not_scheduled", {x["code"] for x in plan(data).blocking_errors})

    def test_skipped_season_blocks(self):
        data = fixture(1); data["target_season"] = 2027
        self.assertIn("season_sequence", {x["code"] for x in plan(data).blocking_errors})

    def test_cross_league_season_authority_blocks(self):
        data = fixture(1); data["league_seasons"][1]["league_id"] = "other"
        self.assertIn("season_authority", {x["code"] for x in plan(data).blocking_errors})

    def test_incomplete_player_identity_blocks(self):
        data = fixture(1); data["agreements"][0]["player_id"] = None
        self.assertFalse(plan(data).safe_to_transition)

    def test_partial_lifecycle_event_blocks(self):
        data = fixture(1); data["events"].append({"event_type": "expired", "effective_season": 2026})
        self.assertIn("partial_transition_events", {x["code"] for x in plan(data).blocking_errors})

    def test_exact_legacy_compatibility(self):
        self.assertTrue(compare_transition_to_legacy(plan(fixture(3)))["exact"])

    def test_plan_fingerprint_is_stable_but_source_changes_are_detected(self):
        first = plan(fixture(2)); second_data = fixture(2)
        second = build_transition_plan(**second_data, requested_at="2030-01-01T00:00:00+00:00")
        self.assertEqual(first.plan_fingerprint, second.plan_fingerprint)
        self.assertEqual(first.source_fingerprint, second.source_fingerprint)
        second_data["contract_seasons"][1]["salary"] = "13.00"
        changed = plan(second_data)
        self.assertNotEqual(first.source_fingerprint, changed.source_fingerprint)

    def test_expected_fingerprint_guard(self):
        data = fixture(1); data["expected_source_fingerprint"] = "wrong"
        result = build_transition_plan(**data, requested_at="2026-07-29T00:00:00+00:00")
        self.assertIn("source_fingerprint_changed", {x["code"] for x in result.blocking_errors})

    def test_planning_does_not_mutate_any_input_dataset(self):
        data = fixture(3); before = deepcopy(data)
        plan(data)
        self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
