import unittest

from pipeline.league_history.build import OwnerMap, best_lineup_points, build_lineup_events
from league_ai.tendencies import describe, lineup_habits, market_habits


def lineup(owner, week, starters, bench, season=2026):
    pts = lambda xs: [{"id": i, "pos": "WR", "pts": p} for i, p in xs]
    s, b = pts(starters), pts(bench)
    return {"kind": "lineup", "season": season, "week": week, "owner_name": owner,
            "details": {"starters": s, "bench": b, "points": sum(x["pts"] for x in s), "best": sum(sorted([x["pts"] for x in s + b])[-len(s):])}}


class LineupTest(unittest.TestCase):
    def test_best_lineup_fills_flex_after_fixed_slots(self):
        pts = {"q": 20, "r1": 15, "r2": 10, "w": 12, "t": 3}
        pos = {"q": "QB", "r1": "RB", "r2": "RB", "w": "WR", "t": "TE"}
        self.assertEqual(best_lineup_points(pts, pos, ["QB", "RB", "WR", "FLEX"]), 57.0)

    def test_lineup_events_from_sleeper_matchups(self):
        owners = OwnerMap([{"user_id": "u1", "display_name": "a"}], [{"roster_id": 1, "owner_id": "u1"}], [])
        ms = [{"roster_id": 1, "matchup_id": 1, "starters": ["a", "b"], "players": ["a", "b", "c"], "players_points": {"a": 10, "b": 2, "c": 9}}]
        rows = build_lineup_events("L", 2026, 3, ms, owners, {"a": {"position": "QB"}, "b": {"position": "WR"}, "c": {"position": "WR"}}, ["QB", "WR", "BN"])
        self.assertEqual(rows[0]["kind"], "lineup")
        self.assertEqual((rows[0]["details"]["points"], rows[0]["details"]["best"]), (12.0, 19.0))

    def test_hot_hand_owner_is_spotted(self):
        # X scored big early then cooled; Y started slow and got hot. The owner keeps starting whoever is hotter.
        rows = []
        x = [30, 30, 30] + [2] * 11
        y = [2, 2, 2] + [12] * 11
        for wk in range(14):
            hot_y = wk >= 5
            rows.append(lineup("Ann", wk + 1, [("Y", y[wk])] if hot_y else [("X", x[wk])], [("X", x[wk])] if hot_y else [("Y", y[wk])]))
        out = lineup_habits(rows, 2026)
        self.assertEqual(out["Ann"]["start_sit_lean"], "starts the hot hand more than most")


class MarketTest(unittest.TestCase):
    def test_faab_timing_positions_and_patience(self):
        ev = [{"kind": "waiver", "season": 2026, "week": w, "owner_name": "Bo", "player_id": f"p{w}", "position": "RB", "faab_bid": 10} for w in (1, 2, 3, 8)]
        ev += [{"kind": "drop", "season": 2026, "week": w + 1, "owner_name": "Bo", "player_id": f"p{w}", "position": "RB"} for w in (1, 2, 3)]
        out = market_habits(ev, 2026)["Bo"]
        self.assertEqual((out["faab_per_season"], out["share_spent_by_week_4"], out["patience"]), (40.0, 0.75, "quick to cut"))
        self.assertIn("mostly on RBs", describe(out))


if __name__ == "__main__":
    unittest.main()
