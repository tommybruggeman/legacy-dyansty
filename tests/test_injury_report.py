import unittest

from api.player import injury_report


def log(*rows):
    return [{"week": w, "status": s, "opp": o} for w, s, o in rows]


SCHEDULE = {w: {"opp": "X", "home": True} for w in range(1, 19) if w != 10}  # bye week 10


class InjuryReportTest(unittest.TestCase):
    def test_ir_player_hurt_then_missed_two(self):
        h = {"healthy": False, "status": "IR", "estimate": {"p25": 1, "p75": 4, "median_games": 3}}
        game_log = log((9, "out", "@CAR"), (8, "out", "KC"), (7, "started", "@ARI"), (6, "started", "SEA"))
        espn = {"side": "Right", "detail": "Sprain", "short_comment": "Making progress.", "reported_at": "2026-11-05T18:00:00+00:00"}
        r = injury_report(h, game_log, SCHEDULE, espn)
        self.assertEqual(r["hurt"], {"week": 7, "opp": "@ARI"})
        self.assertEqual(r["missed_weeks"], [8, 9])
        # Four games from week 8 with a week 10 bye: 8, 9, 11, 12 -> back week 13 at the earliest.
        self.assertEqual(r["eligible_week"], 13)
        # Usual 1-4 games would be weeks 9-13, but IR keeps him out until 13.
        self.assertEqual(r["estimate"], {"weeks": [13, 13]})
        self.assertEqual(r["detail"], "Right Sprain")
        self.assertEqual(r["news"], {"text": "Making progress.", "date": "2026-11-05"})

    def test_questionable_player_still_playing(self):
        h = {"healthy": False, "status": "Questionable", "estimate": None}
        r = injury_report(h, log((9, "started", "@CAR"), (8, "bench", "KC")), SCHEDULE, None)
        self.assertEqual(r["hurt"], {"week": 9, "opp": "@CAR"})
        self.assertEqual(r["missed_weeks"], [])
        self.assertIsNone(r["eligible_week"])
        self.assertIsNone(r["estimate"])
        self.assertIsNone(r["news"])

    def test_past_usual_time(self):
        h = {"healthy": False, "status": "Out", "estimate": {"p25": 1, "p75": 2, "median_games": 1}}
        r = injury_report(h, log((9, "out", ""), (8, "out", ""), (7, "out", ""), (6, "started", "")), SCHEDULE, None)
        self.assertEqual(r["estimate"], {"past_usual": True})

    def test_healthy_has_no_report(self):
        self.assertIsNone(injury_report({"healthy": True}, [], SCHEDULE, None))


if __name__ == "__main__":
    unittest.main()


class RankedStatsTest(unittest.TestCase):
    def test_ranks_per_game_and_total(self):
        from api.player import ranked_stats
        mine = [{"week": w, "rushing_yards": 10, "receptions": 5, "receiving_yards": 40, "rushing_tds": 0, "receiving_tds": 0} for w in (1, 2)]
        pool = [
            {"sleeper_id": "a", "games": 4, "totals": {"rush_yds": 400, "rec": 8, "rec_yds": 60, "td": 4}},
            {"sleeper_id": "b", "games": 4, "totals": {"rush_yds": 30, "rec": 12, "rec_yds": 100, "td": 0}},
            {"sleeper_id": "me", "games": 2, "totals": {"rush_yds": 0, "rec": 0, "rec_yds": 0, "td": 0}},
        ]
        r = ranked_stats(mine, "RB", pool, "me")
        self.assertEqual(r["pool"], 3)
        rec = next(s for s in r["stats"] if s["key"] == "rec")
        self.assertEqual((rec["per_game"], rec["total"]), (5.0, 10))
        self.assertEqual(rec["rank_per_game"], 1)  # 5/g beats 2/g and 3/g
        self.assertEqual(rec["rank_total"], 2)     # 10 total trails b's 12
        rush = next(s for s in r["stats"] if s["key"] == "rush_yds")
        self.assertEqual(rush["rank_per_game"], 2)  # a 100/g, me 10/g, b 7.5/g

    def test_qb_interceptions_lower_is_better(self):
        from api.player import ranked_stats
        mine = [{"week": 1, "interceptions": 0, "passing_yards": 200}]
        pool = [{"sleeper_id": "x", "games": 3, "totals": {"pass_yds": 900, "pass_td": 6, "ints": 3, "rush_yds": 20}}]
        ints = next(s for s in ranked_stats(mine, "QB", pool, "me")["stats"] if s["key"] == "ints")
        self.assertEqual(ints["rank_per_game"], 1)

    def test_kicker_has_no_card(self):
        from api.player import ranked_stats
        self.assertIsNone(ranked_stats([{"week": 1}], "K", [], "me"))
