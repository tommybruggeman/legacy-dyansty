from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20261016_rollover_snapshot_v3_chunked_evidence.sql").read_text()
ROW_VECTOR_FIX = (ROOT / "supabase/migrations/20261022_snapshot_v3_row_vector_runtime_fix.sql").read_text()
HISTORY_LOCK_FIX = (ROOT / "supabase/migrations/20261023_snapshot_v3_history_lock_volatility_fix.sql").read_text()
EXECUTION_TIMEOUT = (ROOT / "supabase/migrations/20261024_rollover_execution_rpc_timeout.sql").read_text()
SQL_TEST = (ROOT / "supabase/tests/20261016_rollover_snapshot_v3_chunked_evidence_test.sql").read_text()
VERIFY = (ROOT / "supabase/verification/verify_rollover_snapshot_v3_chunked_evidence.sql").read_text()
RUNNER = (ROOT / "tests/phase_c_snapshot_v3_integration/run_phase_c_snapshot_v3_certification.py").read_text()
SENTINEL_HELPER = (ROOT / "tests/fixtures/certification_sentinel.py").read_text()


class SnapshotV3MigrationTests(unittest.TestCase):
    def test_execution_timeout_is_scoped_to_the_commissioner_rpc(self):
        sql = EXECUTION_TIMEOUT.lower()
        self.assertIn(
            "alter function public.execute_rollover_plan_authenticated(jsonb)",
            sql,
        )
        self.assertIn("set statement_timeout = '120s'", sql)
        self.assertNotIn("alter role", sql)
        self.assertNotIn("alter database", sql)

    def test_history_locking_verifier_is_explicitly_volatile(self):
        sql = HISTORY_LOCK_FIX.lower()
        self.assertTrue(sql.strip().startswith("-- the immutable-history verifier"))
        self.assertTrue(sql.strip().endswith("commit;"))
        self.assertIn(
            "alter function public.phase3b6c_verify_history_snapshot_compatible_private(",
            sql,
        )
        self.assertIn("jsonb,uuid,uuid,uuid", sql.replace(" ", "").replace("\n", ""))
        self.assertIn(") volatile;", sql)

    def test_runtime_row_vector_fix_preserves_arrays_and_canonicalizes_objects(self):
        sql = ROW_VECTOR_FIX.lower()
        self.assertTrue(sql.strip().startswith("-- real snapshot freeze rows"))
        self.assertTrue(sql.strip().endswith("commit;"))
        self.assertIn("jsonb_typeof(p_record->1) not in('array','object')", sql)
        self.assertIn("from jsonb_each(p_record->1)", sql)
        self.assertIn('order by field.key collate "c"', sql)
        self.assertIn("else positional_value:=p_record->1", sql)
        self.assertIn("snapshot_v3_record_invalid", sql)
        self.assertIn("from public,anon,authenticated,service_role", sql)

    def test_forward_migration_is_atomic_additive_and_bounded(self):
        sql = MIGRATION.lower()
        self.assertTrue(sql.strip().startswith("begin;"))
        self.assertTrue(sql.strip().endswith("commit;"))
        self.assertIn("rollover_execution_input_snapshot_component_chunks", sql)
        self.assertIn("payload_bytes between 2 and 73728", sql)
        self.assertIn(">65536", sql)
        self.assertIn(">1024", sql)
        self.assertIn(">67108864", sql)
        self.assertNotIn("disable trigger", sql)
        outside_bodies = re.sub(r"\$\$.*?\$\$", "", sql, flags=re.S)
        self.assertNotRegex(outside_bodies, r"(?m)^\s*(insert\s+into|update|delete\s+from)\s+public\.(leagues|league_seasons|contract|season_roster)")

    def test_writer_and_direct_consumers_are_v3_aware(self):
        sql = MIGRATION.lower()
        for marker in (
            "phase3b6c_freeze_snapshot_v3_private",
            "phase3b6c_verify_history_snapshot_compatible_private",
            "phase3b6c_snapshot_v3_assert_snapshot_private",
            "execute_rollover_typed_handler_phase3b6c1_private",
            "phase3b8a_is_preserved_off_roster_liability",
            "phase3b6c_snapshot_component_payload_private(s.id,'team_mapping')",
            "phase3b6c_snapshot_component_payload_private(s.id,'owner_option_decisions')",
            "phase3b6c_snapshot_component_payload_private(p_snapshot_id,'owner_option_reviews')",
        ):
            self.assertIn(marker, sql)
        # Operation 23 reads scalar league_rules, which intentionally stays inline.
        self.assertIn("'name','league_rules'", sql)

    def test_immutable_private_security_posture(self):
        sql = MIGRATION.lower()
        self.assertIn("before update or delete", sql)
        self.assertIn("enable row level security", sql)
        self.assertIn("revoke all on public.rollover_execution_input_snapshot_component_chunks from public,anon,authenticated", sql)
        self.assertIn("security definer set search_path=pg_catalog,public", sql)
        self.assertIn("from public,anon,authenticated,service_role", sql)

    def test_runtime_artifacts_are_rollback_read_only_and_sentinel_guarded(self):
        self.assertTrue(SQL_TEST.strip().lower().startswith("begin;"))
        self.assertTrue(SQL_TEST.strip().lower().endswith("rollback;"))
        self.assertNotIn("rollover-cardinality-certification", SQL_TEST)
        for variable in ("environment_name", "environment_type", "parent_project"):
            self.assertIn(f":{{?{variable}}}", SQL_TEST)
            self.assertIn(f"app.phasef_expected_{variable}", SQL_TEST)
            self.assertIn('command += ["-v", name.lower()', RUNNER)
        for variable in (
            "PHASE3B5H_EXPECTED_ENVIRONMENT_NAME",
            "PHASE3B5H_EXPECTED_ENVIRONMENT_TYPE",
            "PHASE3B5H_EXPECTED_PARENT_PROJECT",
        ):
            self.assertIn(variable, SENTINEL_HELPER)
        self.assertIn("array[0,1,2,100,2000]", SQL_TEST.lower().replace(" ", ""))
        self.assertIn("snapshot_v3_cross_language_vector_mismatch", SQL_TEST)
        self.assertTrue(VERIFY.strip().lower().startswith("begin read only;"))
        self.assertTrue(VERIFY.strip().lower().endswith("rollback;"))
        self.assertIn("PHASE3B5H_TEST_DB_", RUNNER)
        self.assertIn("LEGACY_PROD_DB_", RUNNER)
        self.assertIn("rollback state mismatch", RUNNER)


if __name__ == "__main__":
    unittest.main()
