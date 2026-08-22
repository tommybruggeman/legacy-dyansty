from __future__ import annotations

from pathlib import Path
import re
import stat
import tempfile
import unittest

from scripts.generate_season_rollover_gate3_ui_acceptance_fixture import (
    COMMISSIONER_EMAIL,
    OWNER_EMAIL,
    build_fixture_sql,
    write_credentials,
)


def substitute_psql_literal(sql: str, name: str, value: str) -> str:
    """Model psql's :'name' SQL-literal quoting for static regression checks."""
    return sql.replace(f":'{name}'", "'" + value.replace("'", "''") + "'")


class Gate3UiAcceptanceFixtureTests(unittest.TestCase):
    def test_memberships_use_live_canonical_roles(self):
        sql, _ = build_fixture_sql("gate3-role-test")
        membership_sql = "\n".join(
            line for line in sql.splitlines()
            if line.lower().startswith("insert into public.league_memberships")
        )
        self.assertEqual(membership_sql.count("'commissioner'"), 1)
        self.assertEqual(membership_sql.count("'member'"), 11)
        self.assertNotIn("'owner'", membership_sql)

    def test_two_normal_email_password_identities_are_complete(self):
        sql, factory = build_fixture_sql("gate3-auth-test")
        self.assertEqual(sql.count("insert into auth.identities("), 2)
        self.assertEqual(sql.count("encrypted_password"), 2)
        self.assertEqual(sql.count("email_confirmed_at"), 2)
        self.assertIn("extensions.crypt(:'gate3_commissioner_password'", sql)
        self.assertIn("extensions.crypt(:'gate3_owner_password'", sql)
        self.assertIn(COMMISSIONER_EMAIL, sql)
        self.assertIn(OWNER_EMAIL, sql)
        self.assertIn(factory.identity.commissioner_id, sql)
        self.assertIn(factory.identity.owner_id, sql)
        self.assertIn(factory.identity.team_ids[0], sql)

    def test_psql_imports_exported_passwords_before_begin_and_substitution_is_valid(self):
        sql, _ = build_fixture_sql("gate3-psql-variable-test")
        self.assertLess(sql.index("\\getenv gate3_commissioner_password GATE3_COMMISSIONER_PASSWORD"), sql.index("begin;"))
        self.assertLess(sql.index("\\getenv gate3_owner_password GATE3_OWNER_PASSWORD"), sql.index("begin;"))
        self.assertIn("\\if :{?gate3_commissioner_password}", sql)
        self.assertIn("\\if :{?gate3_owner_password}", sql)
        self.assertEqual(sql.count("\\quit 3"), 2)
        rendered = substitute_psql_literal(sql, "gate3_commissioner_password", "commissioner's-local-password")
        rendered = substitute_psql_literal(rendered, "gate3_owner_password", "owner's-local-password")
        self.assertIn("extensions.crypt('commissioner''s-local-password',extensions.gen_salt('bf'))", rendered)
        self.assertIn("extensions.crypt('owner''s-local-password',extensions.gen_salt('bf'))", rendered)
        self.assertNotRegex(rendered, r"extensions\.crypt\(:")

    def test_plaintext_passwords_stay_out_of_generated_sql_and_repo(self):
        sql, _ = build_fixture_sql("gate3-secret-test")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.env"
            values = write_credentials(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            content = path.read_text()
            self.assertIn("export GATE3_COMMISSIONER_PASSWORD=", content)
            self.assertIn("export GATE3_OWNER_PASSWORD=", content)
            for key in ("GATE3_COMMISSIONER_PASSWORD", "GATE3_OWNER_PASSWORD"):
                self.assertNotIn(values[key], sql)
                self.assertGreaterEqual(len(values[key]), 24)

    def test_write_surface_is_domain_only_plus_auth_identities(self):
        sql, _ = build_fixture_sql("gate3-surface-test")
        writes = set(re.findall(
            r"\b(?:insert\s+into|update|delete\s+from)\s+((?:auth|public)\.[a-z0-9_]+)",
            sql,
            re.IGNORECASE,
        ))
        self.assertIn("auth.users", writes)
        self.assertIn("auth.identities", writes)
        self.assertNotIn("public.rollover_executions", writes)
        self.assertNotIn("public.prepared_team_caps", writes)
        self.assertFalse(any("publication" in table for table in writes))


if __name__ == "__main__":
    unittest.main()
