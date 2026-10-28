import unittest

from league_ai.position_signals import Signals


def _qb(week, sid, opp, att, sacks, epa, rush_yd=0):
    return {"sleeper_id": sid, "week": week, "position": "QB", "opponent": opp, "attempts": att, "sacks": sacks, "passing_epa": epa,
            "rushing_yards": rush_yd, "fantasy_points_ppr": 15}


class SignalsTest(unittest.TestCase):
    def test_defense_sack_rate_and_rush(self):
        rows = [_qb(w, "q1", "AAA", 30, 6, -5, 40) for w in (1, 2, 3)] + [_qb(w, "q2", "BBB", 30, 0, 5) for w in (1, 2, 3)]
        s = Signals(rows)
        self.assertLess(s.qb_pressure("AAA"), 1.0)
        self.assertGreater(s.qb_pressure("BBB"), 1.0)
        self.assertGreater(s.qb_pass_d("BBB"), s.qb_pass_d("AAA"))
        self.assertAlmostEqual(s.qb_rush("q1"), 4.0)
        self.assertEqual(s.qb_pressure("ZZZ"), 1.0)

    def test_role_trend_and_form(self):
        rows = [{"sleeper_id": "t", "week": w, "position": "TE", "target_share": sh, "fantasy_points_ppr": p}
                for w, sh, p in ((1, 0.10, 5), (2, 0.10, 5), (3, 0.20, 10), (4, 0.20, 10))]
        s = Signals(rows)
        self.assertAlmostEqual(s.role_trend("t"), 1.15)   # capped
        self.assertGreater(s.form("t"), 1.0)
        self.assertEqual(s.role_trend("nobody"), 1.0)
        self.assertIsNotNone(s.spread("t"))


if __name__ == "__main__":
    unittest.main()
