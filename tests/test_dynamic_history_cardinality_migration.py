from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261007_dynamic_pre_rollover_history_cardinality.sql"
TEST_SQL = ROOT / "supabase/tests/20261007_dynamic_pre_rollover_history_cardinality_test.sql"
VERIFY_SQL = ROOT / "supabase/verification/verify_dynamic_pre_rollover_history_cardinality.sql"


class DynamicHistoryCardinalityMigrationTest(unittest.TestCase):
    def test_forward_migration_uses_exact_bidirectional_sets(self):
        sql = MIGRATION.read_text()
        self.assertNotIn("exactly 10 team mappings", sql.lower())
        self.assertGreaterEqual(sql.lower().count("except select"), 8)
        for marker in ("source roster set does not equal", "mapping team set does not equal",
                       "mapping roster set does not equal", "standings set does not equal"):
            self.assertIn(marker, sql.lower())
        for diagnostic in ("canonical_team_count", "canonical_team_set_fingerprint",
                           "source_roster_set_fingerprint", "mapping_set_fingerprint",
                           "standings_set_fingerprint"):
            self.assertIn(diagnostic, sql)

    def test_service_role_only_and_atomic(self):
        sql = MIGRATION.read_text().lower()
        self.assertTrue(sql.startswith("begin;"))
        self.assertTrue(sql.rstrip().endswith("commit;"))
        self.assertIn("security definer set search_path = pg_catalog, public", sql)
        self.assertIn("revoke all on function public.capture_pre_rollover_history(jsonb) from public, anon, authenticated", sql)
        self.assertIn("grant execute on function public.capture_pre_rollover_history(jsonb) to service_role", sql)

    def test_database_test_is_rollback_only(self):
        sql = TEST_SQL.read_text().lower()
        self.assertIn("begin;", sql)
        self.assertTrue(sql.rstrip().endswith("rollback;"))
        self.assertNotIn("commit;", sql)

    def test_verification_is_read_only(self):
        sql = VERIFY_SQL.read_text().lower()
        for token in ("insert ", "update ", "delete ", "truncate ", "alter ", "drop ", "create "):
            self.assertNotIn(token, sql)


if __name__ == "__main__":
    unittest.main()
