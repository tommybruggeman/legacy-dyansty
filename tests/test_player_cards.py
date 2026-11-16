import unittest

from api import player_cards as pc


class PlayCallTest(unittest.TestCase):
    rates = {("pos:WR", "Questionable", "Limited"): {"listings": 400, "rate": 0.78}}

    def test_questionable_limited(self):
        h = {"healthy": False, "status": "Questionable", "detail": "Ankle",
             "this_week": {"week": 11, "game_status": "Questionable", "days": [{"day": "Wed", "practice": "DNP"}, {"day": "Fri", "practice": "Limited"}]}}
        out = pc.play_call(h, "WR", {"short_comment": "He is expected to play Sunday."}, self.rates, "2026-11-22")
        self.assertGreaterEqual(out["pct"], 70)
        self.assertEqual(out["official"], "Questionable")
        self.assertEqual(out["reported"], "Expected to play")
        self.assertEqual(out["ours"], "Start him")
        self.assertIn("78%", out["why"])

    def test_healthy_and_ir_have_no_call(self):
        self.assertIsNone(pc.play_call({"healthy": True}, "WR", None, {}, None))
        self.assertIsNone(pc.play_call({"healthy": False, "status": "IR", "report": {"on_ir": True}}, "WR", None, {}, None))


class RoleTest(unittest.TestCase):
    def test_chips_and_rows(self):
        rows = [
            {"sleeper_id": "1", "player_name": "Mike Evans", "position": "WR", "week": 1, "targets": 10, "offense_pct": 0.9},
            {"sleeper_id": "2", "player_name": "Chris Godwin", "position": "WR", "week": 1, "targets": 6, "offense_pct": 0.8},
            {"sleeper_id": "3", "player_name": "Cade Otton", "position": "TE", "week": 1, "targets": 4, "offense_pct": 0.7},
            {"sleeper_id": "4", "player_name": "Bucky Irving", "position": "RB", "week": 1, "targets": 5, "carries": 15},
        ]
        out = pc.role_on_team(rows, "1", "WR", [{"sleeper_id": "2", "full_name": "Chris Godwin", "position": "WR"}])
        self.assertEqual([c["slot"] for c in out["chips"]], ["WR1", "WR2", "TE1"])
        self.assertTrue(out["chips"][0]["me"])
        self.assertEqual(out["chips"][0]["share"], 40)
        self.assertEqual(out["rows"][-1], {"label": "Teammates out", "value": "Godwin (WR)"})


class RestOfSeasonTest(unittest.TestCase):
    def test_tones_and_playoffs(self):
        games = [{"week": w, "home_team": "TB", "away_team": opp} for w, opp in ((11, "NO"), (15, "ATL"), (16, "CAR"))]
        games += [{"week": 15, "home_team": "DAL", "away_team": "NYG"}]
        dvp = {("NO", "WR"): {"rank": 30, "teams": 32, "factor": 1.2}, ("ATL", "WR"): {"rank": 2, "teams": 32, "factor": 0.8},
               ("CAR", "WR"): {"rank": 16, "teams": 32, "factor": 1.0}, ("NYG", "WR"): {"rank": 31, "teams": 32, "factor": 1.3},
               ("DAL", "WR"): {"rank": 20, "teams": 32, "factor": 1.05}}
        out = pc.rest_of_season(games, "TB", "WR", dvp, 11, (15, 16))
        self.assertEqual([(w["opp"], w["tone"], w["playoff"]) for w in out["weeks"]], [("NO", "easy", False), ("ATL", "tough", True), ("CAR", "avg", True)])
        self.assertEqual(out["playoff_rank"], 3)  # TB 0.9 vs DAL 1.3, NYG 1.05: hardest of the 3 rated
        self.assertEqual(out["teams"], 3)


class PastSeasonsTest(unittest.TestCase):
    def test_lines(self):
        rows = [{"season": 2025, "receptions": 5, "receiving_yards": 60, "receiving_tds": 1, "fantasy_points_ppr": 17},
                {"season": 2025, "receptions": 3, "receiving_yards": 40, "receiving_tds": 0, "fantasy_points_ppr": 7},
                {"season": 2026, "receptions": 6, "receiving_yards": 90, "receiving_tds": 0, "fantasy_points_ppr": 15}]
        out = pc.past_seasons(rows, "WR", {2025: 9})
        self.assertEqual(out["seasons"][0]["season"], 2026)
        self.assertEqual(out["seasons"][1], {"season": 2025, "games": 2, "rec": 8, "yds": 100, "td": 1, "ppg": 12.0, "finish": 9})


if __name__ == "__main__":
    unittest.main()
