import unittest
from types import SimpleNamespace

from league_ai import writeups as w


def row(week, snap=0.4, **kw):
    base = {"gsis_id": "00-1", "sleeper_id": "111", "player_name": "Test Back", "position": "RB", "team": "DEN",
            "opponent": "KC", "week": week, "carries": 10, "rushing_yards": 40, "rushing_tds": 0, "targets": 5,
            "receptions": 4, "receiving_yards": 30, "receiving_tds": 0, "offense_pct": snap, "fantasy_points_ppr": 11.0}
    base.update(kw)
    return base


class Query:
    def __init__(self, db, table):
        self.db, self.table_name, self.filters, self._range = db, table, {}, None

    def select(self, *_a, **_k): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def in_(self, *_a): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a): return self
    def range(self, a, b): self._range = (a, b); return self

    def delete(self):
        self.db.deleted.append(self.table_name)
        return self

    def upsert(self, rows, **_k):
        self.db.saved.extend(rows)
        return self

    def execute(self):
        data = self.db.tables.get(self.table_name, [])
        if self._range:
            data = data[self._range[0]:self._range[1] + 1]
        return SimpleNamespace(data=data)


class FakeDB:
    def __init__(self, tables): self.tables, self.saved, self.deleted = tables, [], []
    def table(self, name): return Query(self, name)


class FakeAI:
    def __init__(self): self.calls = 0
    @property
    def messages(self): return self

    def create(self, **kw):
        self.calls += 1
        assert kw["model"] == w.MODEL and kw["output_config"] == {"effort": w.EFFORT}
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=900, output_tokens=150),
                               content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="He ran well.")])


class WriteupsTest(unittest.TestCase):
    def test_facts_for_player_who_played(self):
        rows = [row(1, 0.35), row(2, 0.38), row(3, 0.45, fantasy_points_ppr=20.0, rushing_tds=1)]
        games = {(3, "DEN"): {"home_team": "DEN", "away_team": "KC", "home_score": 24, "away_score": 20}}
        f = w.player_facts(rows, 3, rank=7, games=games, injury=None)
        self.assertEqual(f["this_week"]["game"], "vs KC, won 24-20")
        self.assertEqual(f["this_week"]["snap_share"], 45)
        self.assertEqual(f["snap_share_last_3_games"], [35, 38, 45])
        self.assertIn("1 rush TD", f["this_week"]["box_score"])
        self.assertIn("4 catches on 5 targets", f["this_week"]["box_score"])

    def test_injured_regular_gets_writeup_bit_player_does_not(self):
        regular = [row(1), row(2), row(3)]
        f = w.player_facts(regular, 4, rank=None, games={}, injury={"status": "Out", "injury_type": "Knee"})
        self.assertTrue(f["this_week"]["did_not_play"])
        self.assertEqual(f["injury_report"]["status"], "Out")
        self.assertIsNone(w.player_facts([row(4, 0.10)], 4, rank=None, games={}, injury=None))

    def test_run_writes_once_and_skips_saved(self):
        stats = [row(1), row(2), row(3)]
        db = FakeDB({"nfl_player_stats": stats, "nfl_games": [], "nfl_injuries": [], "player_writeups": []})
        ai = FakeAI()
        out = w.run(db, ai, 2026, None)
        self.assertEqual((out["week"], out["written"], ai.calls), (3, 1, 1))
        self.assertEqual(db.saved[0]["text"], "He ran well.")
        self.assertEqual(db.saved[0]["sleeper_id"], "111")
        db.tables["player_writeups"] = [{"gsis_id": "00-1"}]
        self.assertEqual(w.run(db, ai, 2026, 3)["written"], 0)
        self.assertEqual(ai.calls, 1)

    def test_week_not_loaded_for_his_team_is_not_did_not_play(self):
        kc = lambda wk: row(wk, gsis_id="00-2", sleeper_id="222", team="KC")
        stats = [row(1), row(2), row(3), kc(1), kc(2), kc(3), kc(4)]  # DEN week 4 not loaded yet
        db = FakeDB({"nfl_player_stats": stats, "nfl_games": [], "nfl_injuries": [], "player_writeups": []})
        self.assertEqual(w.run(db, None, 2026, 4, dry_run=True)["players"], 1)  # only the KC player
        # once a DEN teammate's week-4 row is in, the missing DEN regular really didn't play
        stats.append(row(4, gsis_id="00-3", team="DEN", offense_pct=0.1))
        self.assertEqual(w.run(db, None, 2026, 4, dry_run=True)["players"], 2)

    def test_nothing_loaded_writes_nothing(self):
        db = FakeDB({"nfl_player_stats": [row(1), row(2), row(3)], "nfl_games": [], "nfl_injuries": [], "player_writeups": []})
        ai = FakeAI()
        out = w.run(db, ai, 2026, 4)
        self.assertEqual((out["written"], out["note"], ai.calls), (0, "week not loaded yet", 0))

    def test_redo_clears_the_week_first(self):
        db = FakeDB({"nfl_player_stats": [row(1), row(2), row(3)], "nfl_games": [], "nfl_injuries": [], "player_writeups": []})
        w.run(db, FakeAI(), 2026, 3, redo=True)
        self.assertEqual(db.deleted, ["player_writeups"])

    def test_dry_run_calls_nothing(self):
        db = FakeDB({"nfl_player_stats": [row(1), row(2), row(3)], "nfl_games": [], "nfl_injuries": []})
        out = w.run(db, None, 2026, None, dry_run=True)
        self.assertEqual((out["players"], out["written"]), (1, 0))

    def test_chips(self):
        f = w.player_facts([row(1, 0.30), row(2, 0.32), row(3, 0.50)], 3, rank=4, games={}, injury=None)
        self.assertEqual(w.chips(f), ["RB4 this week", "Snaps ▲ 50%", "5.0 tgt/g"])


if __name__ == "__main__":
    unittest.main()
