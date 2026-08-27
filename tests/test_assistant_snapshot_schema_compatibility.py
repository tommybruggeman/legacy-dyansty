from __future__ import annotations

import sys
import types
import unittest


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        self.client.selects.append((self.table_name, list(self.filters)))
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]
        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)


class FakeClient:
    def __init__(self):
        self.selects = []
        self.rows = {
            "player_intelligence": [{"sleeper_id": "p1", "player_name": "Global Player"}],
            "league_intelligence": [{"owner_team_name": "Owner One", "overall_rank": 1}],
            "team_intelligence": [
                {"league_id": "league-1", "owner_team_name": "Owner One"},
                {"league_id": "league-2", "owner_team_name": "Other Owner"},
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class SnapshotSchemaCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.original_auth_module = sys.modules.get("auth")
        auth_stub = types.ModuleType("auth")
        auth_stub.current_user = lambda: None
        auth_stub.service_client = lambda: self.client
        sys.modules["auth"] = auth_stub

    def tearDown(self):
        if self.original_auth_module is None:
            sys.modules.pop("auth", None)
        else:
            sys.modules["auth"] = self.original_auth_module

    def test_global_snapshot_tables_do_not_receive_league_filter(self):
        from gm_assistant import data

        players = data.load_snapshot_table("player_intelligence", "league-1")
        league = data.load_snapshot_table("league_intelligence", "league-1")

        self.assertEqual(len(players), 1)
        self.assertEqual(len(league), 1)
        self.assertIn(("player_intelligence", []), self.client.selects)
        self.assertIn(("league_intelligence", []), self.client.selects)

    def test_scoped_snapshot_tables_keep_league_filter(self):
        from gm_assistant import data

        teams = data.load_snapshot_table("team_intelligence", "league-1")

        self.assertEqual(len(teams), 1)
        self.assertEqual(teams.iloc[0]["owner_team_name"], "Owner One")
        self.assertIn(("team_intelligence", [("league_id", "league-1")]), self.client.selects)


if __name__ == "__main__":
    unittest.main()
