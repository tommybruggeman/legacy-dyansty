import unittest

from league_ai import matchup_odds as mo
from league_ai.trade_value import team_windows
from league_ai.power import PlayerStrength


def _p(pid, pos, pts, sd, slot=None):
    return {"id": pid, "name": f"P{pid}", "pos": pos, "slot": slot, "pts": pts, "sd": sd}


class WinOddsTests(unittest.TestCase):
    def test_even_and_favoured(self):
        self.assertAlmostEqual(mo.win_prob(100, 400, 100, 400), 0.5)
        self.assertGreater(mo.win_prob(115, 400, 100, 400), 0.7)
        self.assertLess(mo.win_prob(115, 400, 100, 400, scale=1.5), mo.win_prob(115, 400, 100, 400))

    def test_week_odds_pairs_up(self):
        rankings = [{"owner": "A", "week_lineup": [_p("1", "WR", 20, 5)]}, {"owner": "B", "week_lineup": [_p("2", "WR", 15, 5)]}]
        out = mo.week_odds(rankings, [("A", "B")])
        self.assertAlmostEqual(out["A"]["win"] + out["B"]["win"], 1.0)
        self.assertEqual(out["A"]["opp"], "B")
        self.assertGreater(out["A"]["win"], 0.5)


class RiskSwapTests(unittest.TestCase):
    def test_underdog_takes_the_boom_bust_option(self):
        starters = [_p("s", "WR", 12, 3, "WR"), _p("q", "QB", 20, 6, "QB")]
        bench = [_p("b", "WR", 11, 12)]
        swaps, before, after = mo.risk_swaps(starters, bench, opp_mu=45, opp_var=60)
        self.assertEqual(swaps[0]["in"]["id"], "b")
        self.assertIn("underdog", swaps[0]["why"])
        self.assertGreater(after, before)

    def test_favourite_takes_the_steady_option_and_never_a_much_worse_player(self):
        starters = [_p("s", "WR", 12, 12, "FLEX"), _p("q", "QB", 30, 6, "QB")]
        swaps, _, _ = mo.risk_swaps(starters, [_p("b", "TE", 11, 2)], opp_mu=30, opp_var=60)
        self.assertEqual(swaps[0]["in"]["id"], "b")
        self.assertIn("favourite", swaps[0]["why"])
        swaps, _, _ = mo.risk_swaps(starters, [_p("c", "WR", 6, 15)], opp_mu=60, opp_var=60)
        self.assertEqual(swaps, [])  # 6 points worse: risk doesn't overrule the projection

    def test_wrong_position_and_injured_never_swap(self):
        starters = [_p("s", "QB", 12, 3, "QB")]
        self.assertEqual(mo.risk_swaps(starters, [_p("b", "WR", 12, 15)], 40, 50)[0], [])
        hurt = {**_p("c", "QB", 12, 15), "injury": "Out"}
        self.assertEqual(mo.risk_swaps(starters, [hurt], 40, 50)[0], [])


class GradingTests(unittest.TestCase):
    def _games(self, n, overconfident):
        history, results = [], []
        for i in range(n):
            # the saved odds think A is a big favourite; in truth it's near a coin flip when overconfident
            history.append({"season": 2026, "week": i + 1, "owner_name": "A", "win_pct": 90, "mu": 120, "var": 100, "opp_mu": 100, "opp_var": 100})
            won = (i % 2 == 0) if overconfident else (i % 10 != 0)
            results.append({"season": 2026, "week": i + 1, "owner_name": "a", "won": won})
        return history, results

    def test_scale_widens_when_odds_were_overconfident(self):
        history, results = self._games(40, overconfident=True)
        rep = mo.grade_report(history, results)
        self.assertEqual(rep["weekly_games"], 40)
        self.assertGreater(rep["tuned_scale"], 1.2)

    def test_scale_stays_put_without_enough_games(self):
        history, results = self._games(10, overconfident=True)
        self.assertEqual(mo.grade_report(history, results)["tuned_scale"], 1.0)

    def test_history_rows_and_final_playoffs(self):
        rows = mo.history_rows("L", 2026, 8, {"A": {"win": 0.6, "mu": 110, "var": 90, "opp": "B", "opp_mu": 100, "opp_var": 80}}, {"A": 0.7, "B": 0.3})
        self.assertEqual({r["owner_name"]: r["win_pct"] for r in rows}, {"A": 60.0, "B": None})
        results = [{"season": 2025, "week": 1, "owner_name": n, "standing_points": sp, "points": 100, "is_playoff": False}
                   for n, sp in zip("ABCDEF", [6, 5, 4, 3, 2, 1])] + [{"season": 2025, "week": 15, "owner_name": "A", "is_playoff": True, "points": 0}]
        final = mo.final_playoff_teams(results)
        self.assertTrue(final[(2025, "a")])
        self.assertFalse(final[(2025, "f")])


class WindowTests(unittest.TestCase):
    def test_playoff_odds_drive_the_window(self):
        players = [PlayerStrength(sleeper_id="1", name="x", position="WR", team_id="t1", owner="Al", now=10, dynasty=10, available=True)]
        teams = [{"league_team_id": "t1", "owner_name": "Al"}, {"league_team_id": "t2", "owner_name": "Bo"}]
        ranks = {"t1": 2, "t2": 1}
        w = team_windows(players, teams, ranks, ranks, playoff_odds={"Al": 0.8, "Bo": 0.05})
        self.assertEqual(w, {"t1": "contending", "t2": "rebuilding"})


if __name__ == "__main__":
    unittest.main()
