import unittest
from datetime import date

from pipeline.nfl_data.common import normalize_name, to_float, to_int
from pipeline.nfl_data.crosswalk import Crosswalk
from pipeline.nfl_data.market_values import ecr_to_value, parse_pick_label, read_csv as read_values, transform_picks, transform_values
from pipeline.nfl_data.nflverse_stats import read_csv as read_stats, transform_stats
from pipeline.nfl_data.run import current_nfl_season, default_seasons
from pipeline.nfl_data.sleeper_players import transform_players

CROSSWALK_CSV = """mfl_id,sportradar_id,fantasypros_id,gsis_id,pff_id,sleeper_id,nfl_id,espn_id,yahoo_id,fleaflicker_id,cbs_id,pfr_id,cfbref_id,rotowire_id,rotoworld_id,ktc_id,stats_id,stats_global_id,fantasy_data_id,swish_id,name,merge_name,position,team,birthdate,age,draft_year,draft_round,draft_pick,draft_ovr,twitter_username,height,weight,college,db_season
13593,x,19788,00-0036900,1,9509,1,1,NA,NA,1,ChasJa00,jamarr-chase-1,1,NA,1,1,0,1,NA,Ja'Marr Chase,jamarr chase,WR,CIN,2000-03-01,26.5,2021,1,5,5,NA,72,201,LSU,2026
15281,y,23133,00-0039139,2,9226,2,2,NA,NA,2,RobiBi01,bijan-robinson-1,2,NA,2,2,0,2,NA,Bijan Robinson,bijan robinson,RB,ATL,2002-01-30,24.6,2023,1,8,8,NA,71,215,Texas,2026
17462,z,28013,00-0041562,3,13269,3,3,NA,NA,3,MendFe00,fernando-mendoza-1,3,NA,4,4,0,4,NA,Fernando Mendoza,fernando mendoza,QB,LVR,2003-10-01,23,2026,1,1,1,NA,77,225,Indiana,2026
"""

STATS_CSV = """player_id,player_name,player_display_name,position,position_group,headshot_url,season,week,season_type,game_id,team,opponent_team,completions,attempts,passing_yards,passing_tds,passing_interceptions,sacks_suffered,passing_epa,carries,rushing_yards,rushing_tds,rushing_first_downs,rushing_epa,receptions,targets,receiving_yards,receiving_tds,receiving_air_yards,receiving_yards_after_catch,receiving_first_downs,receiving_epa,target_share,air_yards_share,wopr,fumbles_lost_total,fantasy_points,fantasy_points_ppr
00-0036900,J.Chase,Ja'Marr Chase,WR,WR,,2026,1,REG,2026_01_CIN_CLE,CIN,CLE,0,0,0,0,0,0,,0,0,0,0,,8,11,110,1,140,30,5,4.2,0.31,0.4,0.62,0,17.0,25.0
00-0039139,B.Robinson,Bijan Robinson,RB,RB,,2026,1,REG,2026_01_TB_ATL,ATL,TB,0,0,0,0,0,0,,20,95,1,6,2.1,4,5,30,0,10,25,2,0.8,0.12,0.05,0.2,0,18.5,22.5
00-0000001,K.Banks,Kelvin Banks,T,OL,,2026,1,REG,2026_01_ARI_NO,NO,ARI,0,0,0,0,0,0,,0,0,0,0,,0,0,0,0,0,0,0,,,,,0,0,0
"""

SNAPS_CSV = """game_id,pfr_game_id,season,game_type,week,player,pfr_player_id,position,team,opponent,offense_snaps,offense_pct,defense_snaps,defense_pct,st_snaps,st_pct
2026_01_CIN_CLE,x,2026,REG,1,Ja'Marr Chase,ChasJa00,WR,CIN,CLE,61,0.92,0,0,0,0
2026_01_TB_ATL,y,2026,REG,1,Bijan Robinson,RobiBi01,RB,ATL,TB,55,0.78,0,0,2,0.1
"""

VALUES_CSV = '''"player","pos","team","age","draft_year","ecr_1qb","ecr_2qb","ecr_pos","value_1qb","value_2qb","scrape_date","fp_id"
"Ja'Marr Chase","WR","CIN",26.5,2021,1.1,6.1,1,10232,9098,"2026-09-04","19788"
"Bijan Robinson","RB","ATL",24.6,2023,3.6,10,1.4,9648,8301,"2026-09-04","23133"
"Mystery Man","QB","FA",30,2018,250,260,40,50,40,"2026-09-04","99999"
'''

PICKS_CSV = '''"player","pos","ecr_1qb","ecr_2qb","ecr_high_1qb","ecr_high_2qb","ecr_low_1qb","ecr_low_2qb","scrape_date","pick"
"2027 Pick 1.02","PICK",27,24.055,5.45,15.1,48.55,50.92,"2026-08-28",2
"2028 Early 1st","PICK",40,38,1,1,1,1,"2026-08-28",NA
"2028 2nd","PICK",90,88,1,1,1,1,"2026-08-28",NA
'''

SLEEPER_PLAYERS = {
    "9509": {"player_id": "9509", "full_name": "Ja'Marr Chase", "first_name": "Ja'Marr", "last_name": "Chase", "position": "WR", "team": "CIN",
             "age": 26, "birth_date": "2000-03-01", "years_exp": 5, "depth_chart_order": 1, "depth_chart_position": "LWR", "injury_status": None,
             "status": "Active", "active": True, "college": "LSU", "height": "72", "weight": "201", "search_rank": 3},
    "1234": {"player_id": "1234", "full_name": "Old Guard", "position": "G", "team": "DAL", "active": True, "status": "Active"},
    "5678": {"player_id": "5678", "full_name": "Retired Back", "position": "RB", "team": None, "active": False, "status": "Inactive"},
    "DET": {"player_id": "DET", "full_name": None, "first_name": "Detroit", "last_name": "Lions", "position": "DEF", "team": "DET", "active": True},
}


class CommonTests(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("Ja'Marr Chase"), "jamarr chase")
        self.assertEqual(normalize_name("Kenneth Walker III"), "kenneth walker")
        self.assertEqual(normalize_name("Odell Beckham Jr."), "odell beckham")

    def test_na_handling(self):
        self.assertIsNone(to_int("NA"))
        self.assertIsNone(to_float(float("nan")))
        self.assertEqual(to_int("5.0"), 5)

    def test_seasons(self):
        self.assertEqual(current_nfl_season(date(2026, 9, 29)), 2026)
        self.assertEqual(current_nfl_season(date(2027, 3, 1)), 2026)
        self.assertEqual(default_seasons(date(2026, 9, 29)), [2023, 2024, 2025, 2026])


class CrosswalkTests(unittest.TestCase):
    def setUp(self):
        self.cw = Crosswalk.from_csv(CROSSWALK_CSV)

    def test_id_joins(self):
        self.assertEqual(self.cw.sleeper_for_gsis("00-0036900"), "9509")
        self.assertEqual(self.cw.sleeper_for_fantasypros("23133"), "9226")
        self.assertEqual(self.cw.sleeper_for_fantasypros("23133.0"), "9226")
        self.assertEqual(self.cw.gsis_for_pfr("ChasJa00"), "00-0036900")
        self.assertEqual(self.cw.record_for_sleeper("13269")["college"], "Indiana")

    def test_name_fallback(self):
        self.assertEqual(self.cw.sleeper_for_name("Ja'Marr Chase", "WR"), "9509")
        self.assertIsNone(self.cw.sleeper_for_name("Nobody Here"))


class PlayersTests(unittest.TestCase):
    def test_transform_keeps_fantasy_positions_and_enriches(self):
        rows = transform_players(SLEEPER_PLAYERS, Crosswalk.from_csv(CROSSWALK_CSV))
        by_id = {r["sleeper_id"]: r for r in rows}
        self.assertIn("9509", by_id)
        self.assertNotIn("1234", by_id)  # guard
        self.assertNotIn("5678", by_id)  # retired, no team
        self.assertIn("DET", by_id)
        chase = by_id["9509"]
        self.assertEqual(chase["gsis_id"], "00-0036900")
        self.assertEqual(chase["draft_year"], 2021)
        self.assertEqual(chase["draft_ovr"], 5)
        self.assertEqual(chase["search_name"], "jamarr chase")
        self.assertEqual(chase["weight"], 201)
        self.assertEqual(by_id["DET"]["full_name"], "Detroit Lions")


class StatsTests(unittest.TestCase):
    def test_transform_stats_joins_snaps_and_ids(self):
        cw = Crosswalk.from_csv(CROSSWALK_CSV)
        rows = transform_stats(read_stats(STATS_CSV), read_stats(SNAPS_CSV), cw)
        self.assertEqual(len(rows), 2)  # OL row dropped
        chase = next(r for r in rows if r["gsis_id"] == "00-0036900")
        self.assertEqual(chase["sleeper_id"], "9509")
        self.assertEqual(chase["targets"], 11)
        self.assertEqual(chase["receiving_yac"], 30)
        self.assertEqual(chase["offense_snaps"], 61)
        self.assertAlmostEqual(chase["offense_pct"], 0.92)
        self.assertFalse(chase["is_home"])  # 2026_01_CIN_CLE: CIN away
        bijan = next(r for r in rows if r["gsis_id"] == "00-0039139")
        self.assertTrue(bijan["is_home"])
        self.assertEqual(bijan["fantasy_points_ppr"], 22.5)

    def test_stats_without_snaps(self):
        rows = transform_stats(read_stats(STATS_CSV), None, Crosswalk.from_csv(CROSSWALK_CSV))
        self.assertIsNone(rows[0]["offense_snaps"])


class ValuesTests(unittest.TestCase):
    def test_values_join_by_fp_id_then_name(self):
        rows = transform_values(read_values(VALUES_CSV), Crosswalk.from_csv(CROSSWALK_CSV))
        by_name = {r["player_name"]: r for r in rows}
        self.assertEqual(by_name["Ja'Marr Chase"]["sleeper_id"], "9509")
        self.assertEqual(by_name["Ja'Marr Chase"]["value_1qb"], 10232)
        self.assertEqual(by_name["Bijan Robinson"]["ecr_pos"], 1.4)
        self.assertIsNone(by_name["Mystery Man"]["sleeper_id"])
        self.assertEqual(by_name["Mystery Man"]["scrape_date"], "2026-09-04")

    def test_pick_labels_and_values(self):
        self.assertEqual(parse_pick_label("2027 Pick 1.02"), (2027, 1, "1.02"))
        self.assertEqual(parse_pick_label("2028 Early 1st"), (2028, 1, "early"))
        self.assertEqual(parse_pick_label("2028 2nd"), (2028, 2, None))
        rows = transform_picks(read_values(PICKS_CSV))
        first = rows[0]
        self.assertEqual((first["draft_year"], first["round"], first["slot"]), (2027, 1, "1.02"))
        self.assertEqual(first["value_1qb"], ecr_to_value(27))
        self.assertGreater(first["value_1qb"], rows[2]["value_1qb"])


if __name__ == "__main__":
    unittest.main()


ESPN_PAYLOAD = {
    "injuries": [
        {"displayName": "Atlanta Falcons", "abbreviation": "ATL", "injuries": [
            {"id": "1", "status": "Out", "date": "2026-09-25T03:06Z", "shortComment": "Robinson (ankle) did not practice Thursday.",
             "longComment": "Robinson rolled his ankle in Week 3 and is expected back for Week 5.",
             "athlete": {"id": "4430807", "displayName": "Bijan Robinson", "position": {"abbreviation": "RB"}},
             "details": {"type": "Ankle", "location": "Leg", "detail": "Sprain", "side": "Left", "returnDate": "2026-10-11", "fantasyStatus": {"description": "OUT"}}},
            {"id": "2", "status": "Questionable", "athlete": {"id": "999", "displayName": "Some Lineman", "position": {"abbreviation": "G"}}, "details": {}},
        ]},
    ]
}

NFLVERSE_INJ = """season,season_type,game_type,team,week,gsis_id,position,full_name,first_name,last_name,report_primary_injury,report_secondary_injury,report_status,practice_primary_injury,practice_secondary_injury,practice_status
2026,REG,REG,ATL,3,00-0039139,RB,Bijan Robinson,Bijan,Robinson,Ankle,,Out,Ankle,,Did Not Participate In Practice
2026,REG,REG,ATL,2,00-0039139,RB,Bijan Robinson,Bijan,Robinson,,,,Ankle,,Limited Participation in Practice
2026,POST,POST,ATL,1,00-0039139,RB,Bijan Robinson,Bijan,Robinson,,,,,,Full Participation in Practice
2026,REG,REG,ARI,1,00-0034381,LB,Josh Sweat,Josh,Sweat,,,,Knee,,Full Participation in Practice
"""


class InjuryLoaderTests(unittest.TestCase):
    def test_espn_transform_keeps_fantasy_positions_and_joins(self):
        from pipeline.nfl_data.injuries import transform_espn
        cw = Crosswalk.from_csv(CROSSWALK_CSV.replace("15281,y,23133,00-0039139,2,9226,2,2,", "15281,y,23133,00-0039139,2,9226,2,4430807,"))
        rows = transform_espn(ESPN_PAYLOAD, cw)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["sleeper_id"], "9226")
        self.assertEqual(r["gsis_id"], "00-0039139")
        self.assertEqual(r["return_date"], "2026-10-11")
        self.assertEqual(r["team"], "ATL")
        self.assertEqual(r["status"], "Out")
        self.assertEqual(r["search_name"], "bijan robinson")

    def test_espn_name_fallback_when_no_espn_id_in_crosswalk(self):
        from pipeline.nfl_data.injuries import transform_espn
        rows = transform_espn(ESPN_PAYLOAD, Crosswalk.from_csv(CROSSWALK_CSV))
        self.assertEqual(rows[0]["sleeper_id"], "9226")

    def test_nflverse_practice_reports(self):
        from pipeline.nfl_data.injuries import read_csv, transform_nflverse
        rows = transform_nflverse(read_csv(NFLVERSE_INJ), Crosswalk.from_csv(CROSSWALK_CSV))
        self.assertEqual(len(rows), 2)  # LB and POST rows dropped
        self.assertEqual(rows[0]["report_status"], "Out")
        self.assertEqual(rows[0]["sleeper_id"], "9226")
        self.assertEqual(rows[1]["practice_status"], "Limited Participation in Practice")


class DedupeTests(unittest.TestCase):
    def test_dedupe_keeps_last_per_key(self):
        from pipeline.nfl_data.common import dedupe
        rows = [{"a": 1, "b": 1, "v": "x"}, {"a": 1, "b": 1, "v": "y"}, {"a": 2, "b": 1, "v": "z"}]
        out = dedupe(rows, "a, b")
        self.assertEqual([r["v"] for r in out], ["y", "z"])


class ProspectBuildTests(unittest.TestCase):
    def test_build_prospects_merges_sources(self):
        from pipeline.nfl_data.prospects import build_prospects
        rows = build_prospects(
            2026,
            season_stats=[
                {"playerId": "a1", "player": "Star Back", "position": "RB", "team": "Ohio State", "conference": "Big Ten", "category": "rushing", "statType": "YDS", "stat": 1200},
                {"playerId": "a1", "player": "Star Back", "position": "RB", "team": "Ohio State", "conference": "Big Ten", "category": "rushing", "statType": "CAR", "stat": 200},
                {"playerId": "a1", "player": "Star Back", "position": "RB", "team": "Ohio State", "conference": "Big Ten", "category": "receiving", "statType": "YDS", "stat": 300},
                {"playerId": "a2", "player": "Some Guard", "position": "OL", "team": "Ohio State", "category": "rushing", "statType": "YDS", "stat": 5},
            ],
            usage=[{"id": "a1", "name": "Star Back", "position": "RB", "team": "Ohio State", "conference": "Big Ten", "usage": {"overall": 0.31, "pass": 0.1, "rush": 0.6}}],
            roster=[{"id": "a1", "firstName": "Star", "lastName": "Back", "team": "Ohio State", "position": "RB", "year": 3, "height": 71, "weight": 212, "recruitIds": [55]}],
            recruits=[{"id": 55, "athleteId": None, "name": "Star Back", "stars": 5, "rating": 0.99, "ranking": 4, "year": 2024}],
            draft_picks=[],
        )
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["scrimmage_yds"], 1500)
        self.assertEqual(r["rush_att"], 200)
        self.assertEqual(r["usage_overall"], 0.31)
        self.assertEqual(r["recruit_stars"], 5)
        self.assertTrue(r["draft_eligible"])
        self.assertEqual(r["search_name"], "star back")
