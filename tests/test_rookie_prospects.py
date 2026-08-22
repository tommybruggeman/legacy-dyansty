from __future__ import annotations

import unittest

from services.rookie_prospects import (
    RookieIdentity,
    build_completed_draft_import_plan,
    build_prospect_import_plan,
    build_rookie_class_diagnostics,
    classify_rookie_identity,
    is_current_rookie_eligible,
    synthetic_prospect_id,
)


def prospect(name="Duce Robinson", **updates):
    row = {
        "player_name": name,
        "pos": "WR",
        "college": "Florida State",
        "rookie_class_year": 2026,
        "draft_year": 2026,
    }
    row.update(updates)
    return row


def drafted(name="Duce Robinson", **updates):
    row = {
        "player_name": name,
        "search_name": name.lower(),
        "pos": "WR",
        "college": "Florida State",
        "rookie_class_year": 2026,
        "draft_year": 2026,
        "draft_round": 2,
        "draft_pick": 40,
        "nfl_team": "ATL",
        "source": "NFL.com 2026 Draft final results",
        "source_updated_at": "2026-04-26",
    }
    row.update(updates)
    return row


class RookieProspectTest(unittest.TestCase):
    def test_synthetic_id_is_deterministic(self):
        self.assertEqual(synthetic_prospect_id(2026, "Duce Robinson", "WR"), "prospect_2026_duce_robinson_wr")
        self.assertEqual(synthetic_prospect_id("2026", "Duce  Robinson!", "wr"), "prospect_2026_duce_robinson_wr")

    def test_current_prospect_can_be_inactive_without_team_or_canonical_id(self):
        row = prospect(active=False, nfl_team=None, canonical_player_id=None, nfl_status="PROSPECT")
        self.assertTrue(is_current_rookie_eligible(row, 2026))
        self.assertEqual(classify_rookie_identity(row, 2026), RookieIdentity.PRE_DRAFT_PROSPECT)

    def test_prior_class_and_permanently_inactive_nonprospect_are_excluded(self):
        self.assertFalse(is_current_rookie_eligible(prospect(rookie_class_year=2025, draft_year=2025, active=True), 2026))
        self.assertFalse(is_current_rookie_eligible(prospect(active=False, nfl_status="INACTIVE", market_pool=None), 2026))

    def test_import_defaults_and_repeated_import_are_idempotent(self):
        first = build_prospect_import_plan([prospect()], [])
        second = build_prospect_import_plan([prospect()], list(first.upserts))
        self.assertEqual(first.upserts, second.upserts)
        row = first.upserts[0]
        self.assertEqual(row["sleeper_id"], "prospect_2026_duce_robinson_wr")
        self.assertEqual((row["nfl_status"], row["active"], row["market_pool"]), ("PROSPECT", False, "ROOKIE_PROSPECT"))
        self.assertIsNone(row["canonical_player_id"])

    def test_canonical_merge_removes_synthetic_and_preserves_history(self):
        synthetic = {
            **prospect(),
            "sleeper_id": "prospect_2026_duce_robinson_wr",
            "rookie_rank_history": [{"rank": 4}],
            "ai_projection_rank": 4,
            "draft_round": 2,
        }
        incoming = prospect(sleeper_id="12345", active=True, nfl_status="Active", nfl_team="ATL")
        plan = build_prospect_import_plan([incoming], [synthetic])
        self.assertEqual(plan.synthetic_ids_to_remove, ("prospect_2026_duce_robinson_wr",))
        self.assertEqual(plan.upserts[0]["sleeper_id"], "12345")
        self.assertEqual(plan.upserts[0]["rookie_rank_history"], [{"rank": 4}])
        self.assertEqual(plan.upserts[0]["ai_projection_rank"], 4)
        self.assertEqual(plan.upserts[0]["draft_round"], 2)
        self.assertTrue(plan.merge_events)

    def test_ambiguous_identity_does_not_auto_merge(self):
        existing = [
            {**prospect(), "sleeper_id": "111", "college": "School A"},
            {**prospect(), "sleeper_id": "222", "college": "School B"},
        ]
        incoming = prospect(college=None)
        plan = build_prospect_import_plan([incoming], existing)
        self.assertEqual(plan.upserts[0]["sleeper_id"], "prospect_2026_duce_robinson_wr")
        self.assertEqual(plan.ambiguous_records, ("Duce Robinson",))

    def test_explicit_alias_wins_over_ambiguous_candidates(self):
        existing = [{**prospect(), "sleeper_id": "111"}, {**prospect(), "sleeper_id": "222"}]
        plan = build_prospect_import_plan([{**prospect(), "source_id": "feed-9"}], existing, source_aliases={"feed-9": "222"})
        self.assertEqual(plan.upserts[0]["sleeper_id"], "222")

    def test_identity_classification_never_uses_age_or_experience(self):
        row = prospect(active=False, nfl_status=None, market_pool=None, age=20, years_exp=0)
        self.assertEqual(classify_rookie_identity(row, 2026), RookieIdentity.UNKNOWN_ROOKIE_STATUS)

    def test_diagnostics_flag_confirmed_sparse_2026_class(self):
        rows = [
            {**prospect("Duce Robinson"), "sleeper_id": "prospect_2026_duce_robinson_wr", "nfl_status": "PROSPECT", "active": False},
            {**prospect("Makhi Hughes", pos="RB"), "sleeper_id": "prospect_2026_makhi_hughes_rb", "market_pool": "ROOKIE_PROSPECT", "active": False},
        ]
        report = build_rookie_class_diagnostics(rows)[0]
        self.assertEqual((report.class_year, report.total_records, report.prospects), (2026, 2, 2))
        self.assertIn("INCOMPLETE_CLASS_TOO_FEW_FANTASY_RECORDS", report.flags)

    def test_completed_draft_matches_current_sleeper_and_preserves_league_fields(self):
        sleeper = {"9001": {"full_name": "Duce Robinson", "position": "WR", "team": "ATL", "active": True}}
        existing = [{
            "sleeper_id": "9001", "player_name": "Duce Robinson", "owner_id": "owner-1",
            "salary": 27, "contract_years_left": 3, "scoring_history": [12.5], "ai_rank_history": [{"rank": 4}],
        }]
        plan = build_completed_draft_import_plan([drafted()], sleeper, existing)
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual((plan.matched_count, plan.synthetic_count, plan.updated_count), (1, 0, 1))
        row = plan.upserts[0]
        self.assertEqual((row["sleeper_id"], row["draft_pick"], row["nfl_team"]), ("9001", 40, "ATL"))
        self.assertEqual((row["owner_id"], row["salary"], row["contract_years_left"]), ("owner-1", 27, 3))
        self.assertEqual((row["scoring_history"], row["ai_rank_history"]), ([12.5], [{"rank": 4}]))

    def test_completed_draft_uses_deterministic_synthetic_when_secure_match_is_missing(self):
        plan = build_completed_draft_import_plan([drafted()], {}, [])
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual((plan.matched_count, plan.synthetic_count, plan.missing_count), (0, 1, 1))
        self.assertEqual(plan.upserts[0]["sleeper_id"], "prospect_2026_duce_robinson_wr")
        self.assertEqual(plan.reports[0].proposed_action, "synthetic_insert")

    def test_completed_draft_repeat_is_idempotent(self):
        first = build_completed_draft_import_plan([drafted()], {}, [])
        second = build_completed_draft_import_plan([drafted()], {}, list(first.upserts))
        self.assertEqual((second.upserts, second.unchanged_count), ((), 1))

    def test_completed_draft_aborts_on_ambiguity_duplicate_match_and_duplicate_pick(self):
        ambiguous_sleepers = {
            "1": {"full_name": "Duce Robinson", "position": "WR", "college": "A"},
            "2": {"full_name": "Duce Robinson", "position": "WR", "college": "B"},
        }
        ambiguous = build_completed_draft_import_plan([drafted(college=None)], ambiguous_sleepers, [])
        self.assertFalse(ambiguous.safe_to_apply)
        self.assertTrue(any(error.startswith("ambiguous_identity:") for error in ambiguous.errors))

        duplicate_match = build_completed_draft_import_plan(
            [drafted(), drafted("Duce Robinson Jr.", search_name="duce robinson", draft_pick=41)],
            {"1": {"full_name": "Duce Robinson", "position": "WR"}},
            [],
        )
        self.assertFalse(duplicate_match.safe_to_apply)
        self.assertTrue(any(error.startswith("duplicate_sleeper_match:") for error in duplicate_match.errors))

        duplicate_pick = build_completed_draft_import_plan([drafted(), drafted("Other Player", draft_pick=40)], {}, [])
        self.assertFalse(duplicate_pick.safe_to_apply)
        self.assertTrue(any(error.startswith("duplicate_overall_pick:") for error in duplicate_pick.errors))

    def test_completed_draft_canonical_merge_preserves_synthetic_history(self):
        synthetic = {
            **prospect(), "sleeper_id": "prospect_2026_duce_robinson_wr",
            "ai_rank_history": [{"rank": 2}], "draft_projection_history": [35],
        }
        sleeper = {"9001": {"full_name": "Duce Robinson", "position": "WR", "college": "Florida State", "team": "ATL"}}
        plan = build_completed_draft_import_plan([drafted()], sleeper, [synthetic])
        self.assertEqual(plan.synthetic_ids_to_remove, ("prospect_2026_duce_robinson_wr",))
        self.assertEqual((plan.merged_count, plan.upserts[0]["ai_rank_history"]), (1, [{"rank": 2}]))
        self.assertEqual(plan.upserts[0]["draft_projection_history"], [35])


if __name__ == "__main__":
    unittest.main()
