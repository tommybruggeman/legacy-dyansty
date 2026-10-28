import unittest

from league_ai.wr_matchup import WrModel


def row(g, week, team, opp, targets, yards, tds, air, pos="WR"):
    return {"gsis_id": g, "week": week, "team": team, "opponent": opp, "position": pos, "targets": targets, "receptions": targets * 0.65,
            "receiving_yards": yards, "receiving_tds": tds, "receiving_air_yards": air}


class WrModelTest(unittest.TestCase):
    def setUp(self):
        rows = []
        for w in range(1, 6):
            # deep threat d1 and slot s1 on AAA; defense BBB gets torched deep, CCC shuts deep balls down
            rows += [row("d1", w, "AAA", "XXX", 7, 90, 0.6, 110), row("s1", w, "AAA", "XXX", 8, 70, 0.4, 45), row("t1", w, "AAA", "XXX", 5, 40, 0.2, 30, "TE")]
            rows += [row(f"bd{w}", w, "YYY", "BBB", 8, 160, 1.5, 120), row(f"bs{w}", w, "YYY", "BBB", 8, 60, 0.3, 40)]
            rows += [row(f"cd{w}", w, "ZZZ", "CCC", 8, 30, 0, 120), row(f"cs{w}", w, "ZZZ", "CCC", 8, 65, 0.4, 40)]
        self.m = WrModel(rows)

    def test_profile_has_share_yards_td_and_style(self):
        p = self.m.profile("d1")
        self.assertEqual(p["style"], "deep threat")
        self.assertAlmostEqual(p["target_share"], 0.35, places=2)
        self.assertEqual(p["yards_per_game"], 90.0)
        self.assertEqual(self.m.profile("s1")["style"], "underneath")

    def test_deep_threat_gains_vs_a_defense_beaten_deep_and_loses_vs_one_that_stops_it(self):
        up, why = self.m.multiplier("d1", "BBB")
        down, _ = self.m.multiplier("d1", "CCC")
        self.assertGreater(up, 1.0)
        self.assertLess(down, 1.0)
        self.assertIn("effect", why)
        # the slot receiver barely moves against the deep-ball defense
        slot, _ = self.m.multiplier("s1", "CCC")
        self.assertGreater(slot, down)

    def test_teammate_out_lifts_his_share(self):
        base, _ = self.m.multiplier("s1", "XXX")
        lifted, why = self.m.multiplier("s1", "XXX", out_teammates=["d1"])
        self.assertGreater(lifted, base)
        self.assertGreater(why["teammates_out_share"], 0.3)
        self.assertLessEqual(lifted, 1.2)

    def test_out_teammates_found_from_missing_lines(self):
        week_rows = [row("s1", 6, "AAA", "XXX", 8, 70, 0, 45)]
        self.assertEqual(sorted(self.m.out_teammates("s1", week_rows)), ["d1", "t1"])


if __name__ == "__main__":
    unittest.main()
