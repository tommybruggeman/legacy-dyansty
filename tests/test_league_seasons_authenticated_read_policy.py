from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20261006_league_seasons_authenticated_member_read.sql").read_text()
VERIFY = (ROOT / "supabase/verification/verify_league_seasons_authenticated_member_read.sql").read_text()


class LeagueSeasonsAuthenticatedReadPolicyTests(unittest.TestCase):
    def test_forward_policy_preserves_rls_and_grants_only_authenticated_read(self):
        self.assertIn("alter table public.league_seasons enable row level security", MIGRATION)
        self.assertIn("revoke select on table public.league_seasons from public,anon", MIGRATION)
        self.assertIn("grant select on table public.league_seasons to authenticated", MIGRATION)
        self.assertNotRegex(MIGRATION.lower(), r"disable\s+row\s+level\s+security")
        self.assertNotRegex(MIGRATION.lower(), r"grant\s+(?:insert|update|delete|truncate|all)")

    def test_policy_uses_auth_uid_and_exactly_one_canonical_membership(self):
        self.assertIn("auth.uid() is not null", MIGRATION)
        self.assertGreaterEqual(MIGRATION.count("membership.user_id=auth.uid()"), 2)
        self.assertIn("select count(*)", MIGRATION)
        self.assertIn(")=1", MIGRATION)
        self.assertIn("lower(membership.role) in ('commissioner','member')", MIGRATION)
        for legacy_role in ("'owner'", "'co_owner'", "'co-owner'"):
            self.assertNotIn(legacy_role, MIGRATION)

    def test_policy_has_no_recursive_league_seasons_membership_helper(self):
        self.assertNotIn("create function", MIGRATION.lower())
        policy_body = MIGRATION.split("create policy", 1)[1]
        self.assertEqual(policy_body.count("from public.league_memberships"), 2)
        self.assertNotIn("from public.league_seasons", policy_body)

    def test_migration_contains_no_fantasy_data_mutation(self):
        scrubbed = re.sub(r"--.*$", "", MIGRATION, flags=re.MULTILINE).lower()
        self.assertNotRegex(scrubbed, r"\b(?:insert|update|delete|truncate)\b")

    def test_verification_is_read_only_and_exercises_authenticated_and_anon_roles(self):
        scrubbed = re.sub(r"--.*$", "", VERIFY, flags=re.MULTILINE).lower()
        self.assertIn("begin transaction read only", scrubbed)
        self.assertNotRegex(scrubbed, r"\b(?:insert|update|delete|create|alter|drop|truncate|grant|revoke|call)\b")
        for marker in (
            "rls_enabled", "canonical_select_policy_exists",
            "commissioner_reads_exact_fixture_pair", "source_2025_visible",
            "target_2026_visible", "commissioner_cannot_read_other_leagues",
            "authenticated_nonmember_cannot_read_fixture_league",
            "anon_reads_no_league_seasons",
        ):
            self.assertIn(marker, VERIFY)
        self.assertIn("set local role authenticated", VERIFY)
        self.assertIn("set local role anon", VERIFY)
        self.assertIn("request.jwt.claim.sub", VERIFY)


if __name__ == "__main__":
    unittest.main()
