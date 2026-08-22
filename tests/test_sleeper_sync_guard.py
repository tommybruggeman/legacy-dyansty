from types import SimpleNamespace
import unittest

from services.sleeper_sync_guard import SleeperSyncGuardError, require_active_season_sync_authority


class Result:
    def __init__(self, data, count=None): self.data, self.count = data, count


class Query:
    def __init__(self, rows): self.rows, self.filters = rows, {}
    def select(self, *_args, **kwargs): self.head = bool(kwargs.get("head")); return self
    def eq(self, key, value): self.filters[key] = value; return self
    def order(self, *_args): return self
    def range(self, start, end): self.start, self.end = start, end; return self
    def execute(self):
        rows = [row for row in self.rows if all(str(row.get(k)) == str(v) for k, v in self.filters.items())]
        return Result([] if getattr(self, "head", False) else rows[getattr(self, "start", 0):getattr(self, "end", len(rows) - 1) + 1], len(rows))


class Client:
    def __init__(self, rows): self.rows = rows
    def table(self, name): return Query(self.rows.get(name, []))


class SleeperSyncGuardTests(unittest.TestCase):
    def rows(self):
        return {"league_seasons": [{"id":"season-2026","league_id":"league-1","season":2026,
            "status":"active","is_active":True,"sleeper_league_id":"sleeper-2026"}],
            "rollover_execution_locks": []}

    def test_exact_active_season_and_sleeper_identity_pass(self):
        authority = require_active_season_sync_authority(Client(self.rows()), league_id="league-1",
            expected_season=2026, sleeper_league_id="sleeper-2026")
        self.assertEqual(authority.league_season_id, "season-2026")

    def test_stale_season_wrong_sleeper_and_cutover_fail_closed(self):
        for season, sleeper in ((2025,"sleeper-2026"),(2026,"sleeper-2025")):
            with self.assertRaises(SleeperSyncGuardError):
                require_active_season_sync_authority(Client(self.rows()), league_id="league-1",
                    expected_season=season, sleeper_league_id=sleeper)
        rows = self.rows(); rows["rollover_execution_locks"] = [{"id":"lock-1","league_id":"league-1",
            "status":"active","lock_type":"cutover"}]
        with self.assertRaisesRegex(SleeperSyncGuardError, "cutover"):
            require_active_season_sync_authority(Client(rows), league_id="league-1",
                expected_season=2026, sleeper_league_id="sleeper-2026")


if __name__ == "__main__": unittest.main()
