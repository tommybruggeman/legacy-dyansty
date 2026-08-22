from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

PATCH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20261025_owner_option_canonical_extend_and_snapshot_capacity.sql"
)

ORIGINAL = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260910_phase3b10d_prepared_expiring_contracts.sql"
)


class Operation25PreservedOffRosterPatchTests(unittest.TestCase):

    def test_original_operation25_requires_target_assignment(self):
        sql = ORIGINAL.read_text()

        self.assertIn(
            "and cs.obligation_status='active' and ra.id is null)",
            sql,
        )
        self.assertIn(
            "expiring_target_assignment_missing",
            sql,
        )

    def test_patch_exempts_only_certified_preserved_off_roster_liability(self):
        sql = PATCH.read_text()

        expected = (
            "ra.id is null and not "
            "public.phase3b8a_is_preserved_off_roster_liability("
            "snap.id,a.id,a.player_id,a.league_team_id)"
        )

        self.assertIn(expected, sql)

    def test_patch_keeps_missing_assignment_failure(self):
        sql = PATCH.read_text()

        self.assertIn(
            "expiring_target_assignment_missing",
            sql,
        )

    def test_patch_is_fail_closed_against_upstream_function_drift(self):
        sql = PATCH.read_text()

        self.assertIn(
            "Expected Phase 3B.10D target-assignment check not found",
            sql,
        )

    def test_patch_targets_only_operation25_private_writer(self):
        sql = PATCH.read_text()

        self.assertIn(
            "'public.write_prepared_expiring_phase3b10d_private"
            "(uuid,uuid,uuid,uuid)'::regprocedure",
            sql,
        )


if __name__ == "__main__":
    unittest.main()
