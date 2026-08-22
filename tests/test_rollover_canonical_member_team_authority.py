from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261005_rollover_canonical_member_team_authority.sql"
SQL = MIGRATION.read_text()
VERIFY = (ROOT / "supabase/verification/verify_rollover_canonical_member_team_authority.sql").read_text()


class CanonicalMemberTeamAuthorityMigrationTests(unittest.TestCase):
    def test_is_forward_only_and_does_not_change_membership_constraint_or_data(self):
        self.assertTrue(MIGRATION.name.startswith("20261005_"))
        self.assertNotIn("league_memberships_role_check", SQL)
        scrubbed = re.sub(r"--.*$", "", SQL, flags=re.MULTILINE).lower()
        self.assertNotRegex(scrubbed, r"\b(?:insert|update|delete|truncate)\s+(?:into\s+|from\s+)?public\.league_memberships\b")

    def test_actor_is_authenticated_and_team_is_server_resolved(self):
        helper = SQL.split("-- This implementation remains non-executable", 1)[0]
        self.assertIn("public.require_authenticated_user()", helper)
        self.assertIn("from public.rollover_owner_decisions", helper)
        self.assertIn("resolved_team_id<>decision_row.league_team_id", helper)
        self.assertNotRegex(helper, r"p_request")

    def test_only_member_role_grants_team_decision_authority(self):
        helper = SQL.split("create or replace function public.submit_rollover_owner_decision", 1)[0]
        self.assertIn("lower(m.role)='member'", helper)
        for legacy in ("'owner'", "'co_owner'", "'co-owner'"):
            self.assertNotIn(legacy, helper)

    def test_missing_duplicate_ambiguous_and_cross_team_memberships_fail_closed(self):
        self.assertGreaterEqual(SQL.count("membership_count<>1"), 2)
        self.assertIn("resolved_team_id is null", SQL)
        self.assertIn("resolved_team_id<>decision_row.league_team_id", SQL)
        self.assertIn("resolved_team_id<>d.league_team_id", SQL)
        self.assertGreaterEqual(SQL.count("t.league_id="), 2)

    def test_commissioner_is_not_part_of_member_helper(self):
        helper = SQL.split("-- This implementation remains non-executable", 1)[0]
        self.assertNotIn("commissioner", helper.lower())
        existing = (ROOT / "supabase/migrations/20260806_rollover_window_operation_hardening.sql").read_text()
        self.assertIn("perform public.require_commissioner_authority(d.league_id)", existing)
        self.assertIn("commissioner_owner_override", existing)

    def test_lower_level_mutation_rechecks_member_team_and_preserves_commissioner_override(self):
        self.assertIn("if resolved_role='member'", SQL)
        self.assertIn("elsif resolved_role not in ('commissioner','admin','host')", SQL)
        self.assertNotIn("('owner','co_owner','co-owner'", SQL)

    def test_helper_and_lower_level_function_remain_private(self):
        for signature in (
            "public.require_team_decision_authority(uuid)",
            "public.submit_rollover_owner_decision(jsonb)",
        ):
            self.assertRegex(SQL, rf"revoke all on function {re.escape(signature)} from public,anon,authenticated,service_role")
        self.assertGreaterEqual(SQL.count("security definer"), 2)
        self.assertGreaterEqual(SQL.count("set search_path=pg_catalog,public"), 2)

    def test_authenticated_wrapper_still_owns_actor_and_calls_corrected_chain(self):
        wrapper = (ROOT / "supabase/migrations/20260806_rollover_window_operation_hardening.sql").read_text()
        self.assertIn("actor:=public.require_authenticated_user()", wrapper)
        self.assertIn("role_name:=public.require_team_decision_authority(d.id)", wrapper)
        self.assertIn("p_request||jsonb_build_object('submitted_by',actor::text)", wrapper)
        self.assertNotIn("league_team_id", (ROOT / "services/season_rollover_owner_ui.py").read_text().split("request =", 1)[1])

    def test_verification_artifact_is_read_only_and_checks_grants_and_definition(self):
        scrubbed = re.sub(r"--.*$", "", VERIFY, flags=re.MULTILINE).lower()
        self.assertRegex(scrubbed.strip(), r"^select\b")
        self.assertNotRegex(scrubbed, r"\b(?:insert|update|delete|create|alter|drop|truncate|call|grant|revoke)\b")
        for marker in (
            "canonical_member_required", "ambiguity_rejected", "decision_team_enforced",
            "auth_uid_chain_enforced", "helper_private", "implementation_private", "wrapper_executable",
        ):
            self.assertIn(marker, VERIFY)


if __name__ == "__main__":
    unittest.main()
