from __future__ import annotations

import unittest

from services.sleeper_sync import refresh_sleeper_players


class FakeResponse:
    data = []


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def upsert(self, rows, *, on_conflict):
        self.pending = rows
        self.on_conflict = on_conflict
        return self

    def execute(self):
        by_id = {
            row["sleeper_player_id"]: row
            for row in self.rows
        }
        for row in self.pending:
            by_id[row["sleeper_player_id"]] = row
        self.rows[:] = by_id.values()
        return FakeResponse()


class FakeClient:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.table_name = None
        self.table_writer = FakeTable(self.rows)

    def table(self, name):
        self.table_name = name
        return self.table_writer


class SleeperSyncTest(unittest.TestCase):
    def test_refresh_projects_current_metadata_for_idempotent_upsert(self):
        client = FakeClient([{
            "sleeper_player_id": "12504",
            "full_name": "Kaleb Johnson",
            "position": "WR",
            "team": "PIT",
            "status": "Active",
            "is_active": True,
            "search_name": "kaleb johnson",
        }])
        count = refresh_sleeper_players(client, players={
            "12504": {
                "full_name": "Kaleb Johnson",
                "position": "RB",
                "team": "GB",
                "status": "Active",
                "active": True,
                "league_team_id": "must-not-copy",
                "contract_id": "must-not-copy",
            },
        })

        self.assertEqual(count, 1)
        self.assertEqual(client.table_name, "sleeper_players")
        self.assertEqual(client.table_writer.on_conflict, "sleeper_player_id")
        self.assertEqual(len(client.rows), 1)
        self.assertEqual(client.rows[0], {
            "sleeper_player_id": "12504",
            "full_name": "Kaleb Johnson",
            "position": "RB",
            "team": "GB",
            "status": "Active",
            "is_active": True,
            "search_name": "kaleb johnson",
        })

    def test_refresh_preserves_null_team_and_inactive_state(self):
        client = FakeClient()
        refresh_sleeper_players(client, players={
            "retired": {
                "full_name": "Retired Player",
                "position": "WR",
                "team": None,
                "status": "Retired",
                "active": False,
            },
        })

        self.assertIsNone(client.rows[0]["team"])
        self.assertFalse(client.rows[0]["is_active"])
        self.assertEqual(client.rows[0]["status"], "Retired")


if __name__ == "__main__":
    unittest.main()
