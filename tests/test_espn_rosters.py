import unittest

from pipeline.nfl_data import espn_rosters as er
from pipeline.nfl_data.crosswalk import Crosswalk

CROSSWALK_CSV = """sleeper_id,espn_id,gsis_id,fantasypros_id,pfr_id,name,position,team
100,3121,00-1,1,a,Kirk Cousins,QB,LV
200,4000,00-2,2,b,Justin Jefferson,WR,MIN
"""

ROSTER_MIN = {"team": {"abbreviation": "MIN"}, "athletes": [
    {"position": "offense", "items": [
        {"id": "3121", "fullName": "Kirk Cousins", "position": {"abbreviation": "QB"}},
        {"id": "4000", "fullName": "Justin Jefferson", "position": {"abbreviation": "WR"}},
        {"id": "9", "fullName": "Big Lineman", "position": {"abbreviation": "OT"}},
        {"id": "77", "fullName": "New Guy", "position": {"abbreviation": "RB"}},
    ]}]}
ROSTER_WSH = {"team": {"abbreviation": "WSH"}, "athletes": [{"position": "offense", "items": [
    {"id": "55", "fullName": "Same Name", "position": {"abbreviation": "WR"}}]}]}


class EspnRosterTests(unittest.TestCase):
    def setUp(self):
        self.cw = Crosswalk.from_csv(CROSSWALK_CSV)
        self.current = {
            "100": {"sleeper_id": "100", "full_name": "Kirk Cousins", "position": "QB", "team": "LV", "depth_chart_order": 1},
            "200": {"sleeper_id": "200", "full_name": "Justin Jefferson", "position": "WR", "team": "MIN", "depth_chart_order": 1},
            "300": {"sleeper_id": "300", "full_name": "New Guy", "position": "RB", "team": None},
            "400": {"sleeper_id": "400", "full_name": "Same Name", "position": "WR", "team": "WAS"},
        }

    def test_roster_parse_keeps_fantasy_positions_and_aliases(self):
        players = er.roster_players(ROSTER_MIN) + er.roster_players(ROSTER_WSH)
        self.assertEqual({p["name"] for p in players}, {"Kirk Cousins", "Justin Jefferson", "New Guy", "Same Name"})
        self.assertEqual(er.roster_players(ROSTER_WSH)[0]["team"], "WAS")

    def test_trade_and_signing_detected_unchanged_skipped(self):
        players = er.roster_players(ROSTER_MIN) + er.roster_players(ROSTER_WSH)
        teams = er.espn_teams_by_sleeper(players, self.current, self.cw)
        updates, moves = er.espn_changes(self.current, teams)
        by = {u["sleeper_id"]: u for u in updates}
        self.assertEqual(set(by), {"100", "300"})
        self.assertEqual(by["100"]["team"], "MIN")
        self.assertIsNone(by["100"]["depth_chart_order"])
        m = {x["sleeper_id"]: x for x in moves}
        self.assertEqual((m["100"]["from_team"], m["100"]["to_team"]), ("LV", "MIN"))
        self.assertEqual(m["300"]["from_team"], "FA")

    def test_player_on_two_rosters_is_skipped(self):
        dup = {"team": {"abbreviation": "LV"}, "athletes": [{"items": [{"id": "3121", "fullName": "Kirk Cousins", "position": {"abbreviation": "QB"}}]}]}
        teams = er.espn_teams_by_sleeper(er.roster_players(ROSTER_MIN) + er.roster_players(dup), self.current, self.cw)
        self.assertNotIn("100", teams)

    def test_stale_sleeper_file_does_not_undo_espn_move(self):
        current = dict(self.current)
        current["100"] = {**current["100"], "team": "MIN", "depth_chart_order": None}
        recent = [{"sleeper_id": "100", "from_team": "LV", "to_team": "MIN", "source": "espn_roster", "seen_at": "2026-11-10T21:29:00+00:00"}]
        rows = [{"sleeper_id": "100", "team": "LV", "depth_chart_order": 1, "depth_chart_position": "QB"},
                {"sleeper_id": "200", "team": "DAL", "depth_chart_order": 1}]
        moves = er.sleeper_changes(current, rows, recent)
        self.assertEqual(rows[0]["team"], "MIN")
        self.assertIsNone(rows[0]["depth_chart_order"])
        self.assertEqual([(m["sleeper_id"], m["from_team"], m["to_team"]) for m in moves], [("200", "MIN", "DAL")])

    def test_sleeper_catching_up_is_not_a_new_move(self):
        current = dict(self.current)
        current["100"] = {**current["100"], "team": "MIN"}
        rows = [{"sleeper_id": "100", "team": "MIN", "depth_chart_order": 2}]
        self.assertEqual(er.sleeper_changes(current, rows, []), [])


if __name__ == "__main__":
    unittest.main()
