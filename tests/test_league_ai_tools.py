import json
import unittest

from league_ai.tools.nfl import NflData, build_registry, normalize_name, tool_compare_players, tool_get_market_values, tool_get_player_profile, tool_get_player_stats, tool_search_players, tool_simulate_trade
from tests.fixtures.league_ai_state import CHASE, LEAGUE_ID, TOMMY, fixture_state


PLAYERS = [
    {"sleeper_id": "p1", "full_name": "Josh Allen", "search_name": "josh allen", "position": "QB", "team": "BUF", "age": 30.4, "years_exp": 8, "depth_chart_order": 1, "depth_chart_position": "QB", "injury_status": None, "status": "Active", "college": "Wyoming", "draft_year": 2018, "draft_round": 1, "draft_ovr": 7, "gsis_id": "g1", "search_rank": 5, "refreshed_at": "2026-09-29T09:00:00"},
    {"sleeper_id": "p2", "full_name": "Bijan Robinson", "search_name": "bijan robinson", "position": "RB", "team": "ATL", "age": 24.6, "years_exp": 3, "depth_chart_order": 1, "depth_chart_position": "RB", "injury_status": "Questionable", "injury_body_part": "Ankle", "status": "Active", "college": "Texas", "draft_year": 2023, "draft_round": 1, "draft_ovr": 8, "gsis_id": "g2", "search_rank": 3, "refreshed_at": "2026-09-29T09:00:00"},
    {"sleeper_id": "p99", "full_name": "Free Agent Back", "search_name": "free agent back", "position": "RB", "team": "SEA", "age": 23.0, "years_exp": 1, "depth_chart_order": 2, "status": "Active", "gsis_id": "g99", "search_rank": 200, "refreshed_at": "2026-09-29T09:00:00"},
]
STATS = [
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2026, "week": 3, "season_type": "REG", "team": "ATL", "opponent": "CAR", "is_home": True, "carries": 20, "rushing_yards": 110, "rushing_tds": 1, "targets": 6, "receptions": 5, "receiving_yards": 40, "receiving_tds": 0, "fantasy_points": 20, "fantasy_points_ppr": 25, "offense_snaps": 55, "offense_pct": 0.8, "target_share": 0.15},
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2026, "week": 2, "season_type": "REG", "team": "ATL", "opponent": "TB", "is_home": False, "carries": 18, "rushing_yards": 70, "rushing_tds": 0, "targets": 4, "receptions": 4, "receiving_yards": 30, "receiving_tds": 0, "fantasy_points": 10, "fantasy_points_ppr": 14, "offense_snaps": 50, "offense_pct": 0.7, "target_share": 0.1},
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2025, "week": 1, "season_type": "REG", "team": "ATL", "opponent": "PIT", "is_home": True, "carries": 22, "rushing_yards": 120, "rushing_tds": 2, "targets": 3, "receptions": 3, "receiving_yards": 20, "receiving_tds": 0, "fantasy_points": 26, "fantasy_points_ppr": 29, "offense_snaps": 60, "offense_pct": 0.9, "target_share": 0.08},
]
VALUES = [
    {"fantasypros_id": "1", "scrape_date": "2026-09-25", "sleeper_id": "p2", "player_name": "Bijan Robinson", "position": "RB", "team": "ATL", "age": 24.6, "value_1qb": 9648, "value_2qb": 8301, "ecr_1qb": 3.6, "ecr_2qb": 10, "ecr_pos": 1.4},
    {"fantasypros_id": "1", "scrape_date": "2026-08-25", "sleeper_id": "p2", "player_name": "Bijan Robinson", "position": "RB", "team": "ATL", "age": 24.5, "value_1qb": 9400, "value_2qb": 8100, "ecr_1qb": 4, "ecr_2qb": 11, "ecr_pos": 2},
    {"fantasypros_id": "2", "scrape_date": "2026-09-25", "sleeper_id": "p1", "player_name": "Josh Allen", "position": "QB", "team": "BUF", "age": 30.4, "value_1qb": 6000, "value_2qb": 9800, "ecr_1qb": 20, "ecr_2qb": 2, "ecr_pos": 1},
]
PICKS = [
    {"pick_label": "2027 Pick 1.02", "draft_year": 2027, "round": 1, "slot": "1.02", "value_1qb": 5600, "value_2qb": 6000, "scrape_date": "2026-09-25"},
    {"pick_label": "2027 2nd", "draft_year": 2027, "round": 2, "slot": None, "value_1qb": 1300, "value_2qb": 1400, "scrape_date": "2026-09-25"},
]
LOG = [{"loader": "sleeper_players", "finished_at": "2026-09-29T09:05:00+00:00", "ok": True, "rows_written": 3313}]


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self._order = []
        self._limit = None

    def select(self, *_a, **k): self._count = k.get('count'); return self
    def eq(self, col, val): self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]; return self
    def ilike(self, col, pat):
        needle = pat.strip("%").lower()
        self.rows = [r for r in self.rows if needle in str(r.get(col) or "").lower()]; return self
    def in_(self, col, vals): self.rows = [r for r in self.rows if r.get(col) in vals]; return self
    def gt(self, col, val): self.rows = [r for r in self.rows if r.get(col) is not None and r[col] > val]; return self
    def lte(self, col, val): self.rows = [r for r in self.rows if r.get(col) is not None and r[col] <= val]; return self
    def gte(self, col, val): self.rows = [r for r in self.rows if r.get(col) is not None and r[col] >= val]; return self
    def order(self, col, desc=False): self._order.append((col, desc)); return self
    def limit(self, n): self._limit = n; return self
    def execute(self):
        rows = list(self.rows)
        for col, desc in reversed(self._order):
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        class R: pass
        res = R(); res.data = [dict(r) for r in rows]; res.count = len(self.rows) if getattr(self, '_count', None) else None; return res


class FakeClient:
    TABLES = {"nfl_players": PLAYERS, "nfl_player_stats": STATS, "nfl_market_values": VALUES, "nfl_pick_values": PICKS, "nfl_data_sync_log": LOG}
    def table(self, name): return FakeQuery(self.TABLES[name])


def _data():
    return NflData(FakeClient(), league_state=fixture_state(), salary_cap=225, dead_cap_pct=50)


class ProfileTests(unittest.TestCase):
    def test_profile_joins_nfl_league_and_market(self):
        out = tool_get_player_profile(_data(), "Bijan Robinson")
        self.assertEqual(out["nfl_team"], "ATL")
        self.assertEqual(out["injury_status"], "Questionable")
        self.assertEqual(out["draft"], "2023 round 1 pick 8")
        self.assertEqual(out["league_contract"]["owner"], "Tommy Bruggeman")
        self.assertEqual(out["league_contract"]["cap_hit"], 45.0)
        self.assertEqual(out["league_contract"]["if_dropped"]["dead_cap_total"], 67.5)  # 22.5 x 3 seasons
        self.assertEqual(out["league_contract"]["if_dropped"]["cap_saved_this_season"], 22.5)
        self.assertEqual(out["market"]["positional_rank"], "RB1")
        self.assertEqual(out["market"]["positional_tier"], "elite RB1")
        self.assertEqual(out["market"]["overall_rank_superflex"], 2)  # Allen 9800 > Bijan 8301
        self.assertIn("since 2026-08-25", out["market"]["trend"])

    def test_profile_unrostered_and_unknown(self):
        out = tool_get_player_profile(_data(), "Free Agent Back")
        self.assertEqual(out["league_contract"], "not rostered in this league (free agent)")
        self.assertIn("error", tool_get_player_profile(_data(), "Nobody Real"))

    def test_normalize(self):
        self.assertEqual(normalize_name("Ja'Marr Chase Jr."), "jamarr chase")


class StatsTests(unittest.TestCase):
    def test_stats_seasons_recent_and_split(self):
        out = tool_get_player_stats(_data(), "Bijan", split="home_away")
        s26 = out["seasons"]["2026"]
        self.assertEqual(s26["totals"]["games"], 2)
        self.assertEqual(s26["totals"]["rushing_yards"], 180)
        self.assertEqual(s26["totals"]["ppg_ppr"], 19.5)
        self.assertEqual(s26["home"]["games"], 1)
        self.assertEqual(s26["away"]["games"], 1)
        self.assertEqual(out["seasons"]["2025"]["totals"]["rushing_tds"], 2)
        self.assertEqual(out["recent"]["weeks"], [3, 2])
        self.assertEqual(out["game_log_latest"][0]["week"], 3)

    def test_stats_missing(self):
        out = tool_get_player_stats(_data(), "Josh Allen")
        self.assertIn("note", out)


class MarketAndSearchTests(unittest.TestCase):
    def test_market_table_latest_only_with_league_status(self):
        out = tool_get_market_values(_data(), position="RB", include_picks=True)
        self.assertEqual(out["as_of"], "2026-09-25")
        self.assertEqual(len(out["players"]), 1)
        self.assertIn("Tommy Bruggeman", out["players"][0]["league_status"])
        self.assertEqual(out["players"][0]["tier"], "elite RB1")
        self.assertEqual(out["picks"][0]["pick"], "2027 Pick 1.02")
        self.assertIn("dynasty asset overall", out["picks"][0]["worth_about"])

    def test_superflex_ordering(self):
        out = tool_get_market_values(_data(), superflex=True)
        self.assertEqual(out["players"][0]["player"], "Josh Allen")
        self.assertEqual(out["players"][0]["positional_rank"], "QB1")

    def test_search_free_agents(self):
        out = tool_search_players(_data(), position="RB", league_free_agents_only=True)
        names = [p["full_name"] for p in out["players"]]
        self.assertEqual(names, ["Free Agent Back"])

    def test_compare(self):
        out = tool_compare_players(_data(), ["Bijan Robinson", "Josh Allen"])
        self.assertIn("2026", out["Bijan Robinson"]["stats"])
        self.assertEqual(out["Josh Allen"]["league_contract"]["cap_hit"], 60.0)


class TradeSimTests(unittest.TestCase):
    def test_two_team_trade_cap_math(self):
        out = tool_simulate_trade(_data(), [
            {"team": "Tommy Bruggeman", "sends_players": ["Bijan Robinson"], "sends_picks": ["2027 R1"]},
            {"team": "Chase's Chumps", "sends_players": ["CeeDee Lamb"], "sends_cap": 5},
        ])
        self.assertEqual(out["errors"], [])
        tommy = out["teams"]["Tommy Bruggeman"]
        chase = out["teams"]["Chase Seyforth"]
        self.assertEqual(tommy["cap_space_before"], 91.5)
        self.assertEqual(tommy["cap_space_after"], 91.5 + 45 - 50 + 5)
        self.assertEqual(chase["cap_space_after"], chase["cap_space_before"] + 50 - 45 - 5)
        self.assertIn("pick 2027 R1", tommy["sends"])
        self.assertFalse(tommy["over_cap"])

    def test_trade_errors(self):
        out = tool_simulate_trade(_data(), [{"team": "Tommy Bruggeman", "sends_players": ["CeeDee Lamb"]}, {"team": "Nobody"}])
        self.assertTrue(any("not on" in e for e in out["errors"]))
        self.assertTrue(any("Unknown team" in e for e in out["errors"]))


class RegistryTests(unittest.TestCase):
    def test_registry_runs_tools_through_json(self):
        reg = build_registry(FakeClient(), league_state=fixture_state())
        self.assertEqual(len(reg), 8)
        out = json.loads(reg.call("get_player_profile", {"name": "Josh Allen"}))
        self.assertEqual(out["nfl_team"], "BUF")
        self.assertIn("error", json.loads(reg.call("get_player_stats", {})))  # bad args reported, not raised


if __name__ == "__main__":
    unittest.main()


INJURIES = [
    {"espn_id": "4430807", "sleeper_id": "p2", "gsis_id": "g2", "player_name": "Bijan Robinson", "search_name": "bijan robinson", "position": "RB", "team": "ATL",
     "status": "Out", "injury_type": "Ankle", "location": "Leg", "detail": "Sprain", "side": "Left", "return_date": "2026-10-11",
     "short_comment": "Robinson (ankle) did not practice Thursday.", "long_comment": "Expected back Week 5.", "reported_at": "2026-09-25T03:06Z", "refreshed_at": "2026-09-29T09:00:00"},
]
PRACTICE = [
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2026, "week": 3, "report_injury": "Ankle", "report_status": "Out", "practice_injury": "Ankle", "practice_status": "Did Not Participate In Practice"},
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2026, "week": 2, "report_injury": None, "report_status": None, "practice_injury": "Ankle", "practice_status": "Limited Participation in Practice"},
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2024, "week": 9, "report_injury": "Hamstring", "report_status": "Out", "practice_injury": "Hamstring", "practice_status": "Did Not Participate In Practice"},
    {"gsis_id": "g2", "sleeper_id": "p2", "season": 2024, "week": 10, "report_injury": "Hamstring", "report_status": "Questionable", "practice_injury": "Hamstring", "practice_status": "Limited Participation in Practice"},
]
FakeClient.TABLES["nfl_injuries"] = INJURIES
FakeClient.TABLES["nfl_practice_reports"] = PRACTICE


class InjuryToolTests(unittest.TestCase):
    def test_injury_report(self):
        from league_ai.tools.nfl import tool_get_injury_report
        out = tool_get_injury_report(_data(), "Bijan Robinson")
        self.assertEqual(out["current"]["estimated_return"], "2026-10-11")
        self.assertEqual(out["current"]["injury"], "Left Ankle (Sprain)")
        self.assertEqual(out["history"]["seasons"]["2026"]["weeks_out"], 1)
        self.assertEqual(out["history"]["seasons"]["2024"]["injuries_reported"], ["Hamstring"])
        self.assertEqual(out["history"]["this_season_weekly"][0]["week"], 3)

    def test_profile_carries_injury_report(self):
        out = tool_get_player_profile(_data(), "Bijan Robinson")
        self.assertEqual(out["injury_report"]["estimated_return"], "2026-10-11")

    def test_registry_has_injury_tool(self):
        reg = build_registry(FakeClient(), league_state=fixture_state())
        self.assertIn("get_injury_report", reg.tools)
        self.assertEqual(len(reg), 8)


PROSPECTS = [
    {"cfbd_athlete_id": "a1", "season": 2026, "name": "Star Back", "search_name": "star back", "position": "RB", "college": "Ohio State", "conference": "Big Ten",
     "class_year": 3, "draft_eligible": True, "height": 71, "weight": 212, "recruit_stars": 5, "recruit_rating": 0.99, "recruit_rank": 4, "recruit_year": 2024,
     "usage_overall": 0.31, "rush_att": 200, "rush_yds": 1200, "rush_td": 14, "receptions": 30, "rec_yds": 300, "rec_td": 2, "scrimmage_yds": 1500},
    {"cfbd_athlete_id": "a3", "season": 2026, "name": "Young Frosh", "search_name": "young frosh", "position": "RB", "college": "Texas", "class_year": 1, "draft_eligible": False,
     "rush_yds": 900, "scrimmage_yds": 900},
]
FakeClient.TABLES["nfl_prospects"] = PROSPECTS


class ProspectToolTests(unittest.TestCase):
    def test_prospect_class_list_excludes_ineligible(self):
        from league_ai.tools.nfl import tool_get_prospects
        out = tool_get_prospects(_data(), draft_year=2027, position="RB")
        self.assertEqual(out["draft_class"], 2027)
        names = [p["name"] for p in out["prospects"]]
        self.assertEqual(names, ["Star Back"])
        self.assertIn("200 car for 1200 yds, 14 TD", out["prospects"][0]["season_line"])
        self.assertEqual(out["prospects"][0]["recruiting"], "5-star recruit (2024), #4 overall")
        self.assertEqual(out["picks_market"][0]["pick"], "2027 Pick 1.02")

    def test_prospect_lookup_by_name(self):
        from league_ai.tools.nfl import tool_get_prospects
        out = tool_get_prospects(_data(), name="star back")
        self.assertEqual(out["matches"][0]["class"], "Jr")
