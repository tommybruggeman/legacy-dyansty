from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260720_assistant_identity_memory_scope.sql"
sys.path.insert(0, str(ROOT))

auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

verify_script = importlib.import_module("scripts.verify_assistant_scope")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None
        self.columns = "*"

    def select(self, columns="*"):
        self.columns = columns
        if self.client.fail_column_probe and self.table_name == "gm_user_memory" and "league_team_id" in columns:
            raise Exception("column does not exist")
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]

        if self.limit_value is not None:
            rows = rows[: self.limit_value]

        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, columns="*"):
        return FakeQuery(self.client, self.table_name).select(columns)


class FakeClient:
    def __init__(self):
        self.fail_column_probe = False
        self.rows = {
            "league_teams": [
                {
                    "id": "team-1",
                    "league_id": "league-1",
                    "team_name": "Same Name",
                },
                {
                    "id": "team-2",
                    "league_id": "league-2",
                    "team_name": "Same Name",
                },
            ],
            "gm_user_memory": [
                {
                    "user_id": "user-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Same Name",
                },
                {
                    "user_id": "legacy-user",
                    "league_id": None,
                    "league_team_id": None,
                    "team_name": "Legacy",
                },
            ],
            "league_brain": [
                {
                    "league_id": "league-1",
                    "league_key": "league-1",
                    "team_count": 1,
                },
                {
                    "league_id": None,
                    "league_key": "default",
                },
            ],
            "team_brain": [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Same Name",
                },
                {
                    "league_id": "league-2",
                    "league_team_id": "team-2",
                    "team_name": "Same Name",
                },
                {
                    "league_id": None,
                    "league_team_id": None,
                    "team_name": "Legacy",
                },
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class AssistantScopeVerificationTest(unittest.TestCase):
    def test_verification_passes_with_warnings_for_legacy_rows(self):
        report = verify_script.verify_assistant_scope(
            league_id="league-1",
            league_team_id="team-1",
            user_id="user-1",
            sb=FakeClient(),
        )

        self.assertTrue(report.ok)
        self.assertTrue(any("Legacy unscoped" in warning for warning in report.warnings))
        self.assertTrue(any("Same team name exists outside this league" in detail for detail in report.details))

    def test_duplicate_memory_scope_fails(self):
        client = FakeClient()
        client.rows["gm_user_memory"].append(dict(client.rows["gm_user_memory"][0]))

        report = verify_script.verify_assistant_scope(
            league_id="league-1",
            league_team_id="team-1",
            user_id="user-1",
            sb=client,
        )

        self.assertFalse(report.ok)
        self.assertTrue(any("Duplicate modern gm_user_memory" in failure for failure in report.failures))

    def test_wrong_team_league_fails(self):
        report = verify_script.verify_assistant_scope(
            league_id="league-1",
            league_team_id="team-2",
            user_id="user-1",
            sb=FakeClient(),
        )

        self.assertFalse(report.ok)
        self.assertTrue(any("does not belong" in failure for failure in report.failures))

    def test_missing_scope_columns_fail(self):
        client = FakeClient()
        client.fail_column_probe = True

        report = verify_script.verify_assistant_scope(
            league_id="league-1",
            league_team_id="team-1",
            user_id="user-1",
            sb=client,
        )

        self.assertFalse(report.ok)
        self.assertTrue(any("missing one or more required scope columns" in failure for failure in report.failures))

    def test_main_returns_nonzero_for_unsafe_state(self):
        original_service_client = verify_script.service_client
        client = FakeClient()
        client.rows["team_brain"].append(dict(client.rows["team_brain"][0]))

        try:
            verify_script.service_client = lambda: client
            exit_code = verify_script.main([
                "--league-id",
                "league-1",
                "--league-team-id",
                "team-1",
                "--user-id",
                "user-1",
            ])
        finally:
            verify_script.service_client = original_service_client

        self.assertEqual(exit_code, 1)

    def test_stage_three_migration_is_idempotent_and_adds_membership_column(self):
        sql = MIGRATION.read_text()

        self.assertIn("alter table if exists public.league_memberships", sql)
        self.assertIn("add column if not exists league_team_id uuid", sql)
        self.assertIn("create unique index if not exists gm_user_memory_user_league_team_uidx", sql)
        self.assertIn("create unique index if not exists team_brain_league_team_uidx", sql)
        self.assertNotIn("drop table", sql.lower())
        self.assertNotIn("delete from", sql.lower())
        self.assertNotIn("update public.", sql.lower())


if __name__ == "__main__":
    unittest.main()
