from types import SimpleNamespace

from api import box_score, home, league, nfl_live, projections, teams


class _T:
    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=[])


class _C:
    def table(self, name):
        return _T()


def test_game_adds_projections_and_projected_totals(monkeypatch):
    rows = [{"matchup_id": 3, "roster_id": 1, "starters": ["10", "20"], "starters_points": [20.0, 0.0], "points": 20.0},
            {"matchup_id": 3, "roster_id": 2, "starters": ["30", "0"], "starters_points": [12.5, 0.0], "points": 12.5}]
    monkeypatch.setattr(teams.sleeper, "get", lambda path, ttl=60: rows)
    monkeypatch.setattr(league, "last_scored_week", lambda sid: 7)
    monkeypatch.setattr(teams, "player_info", lambda client, ids: {"10": {"full_name": "A", "position": "QB", "team": "BUF"},
                                                                    "20": {"full_name": "B", "position": "RB", "team": "KC"},
                                                                    "30": {"full_name": "C", "position": "WR", "team": "MIA"}})
    monkeypatch.setattr(projections, "week_projections", lambda *a, **k: {"10": 22.0, "20": 14.0, "30": 15.0})
    monkeypatch.setattr(projections, "week_ranks", lambda *a, **k: {"10": {"label": "QB2"}})
    monkeypatch.setattr(projections, "week_lines", lambda *a, **k: {})
    monkeypatch.setattr(projections, "league_scoring", lambda sid: {})
    monkeypatch.setattr(nfl_live, "week_games", lambda season, week, final: {})
    monkeypatch.setattr(home, "player_status", lambda team, starts, now: "playing" if team == "BUF" else "upcoming")
    ctx = {"season": 2026, "sleeper_id": "S", "names": {1: "Tommy", 2: "Grady"}, "slots": ["QB", "RB", "BN"], "sleeper_roster_id": 1}
    g = box_score.game(_C(), ctx, 8, 3, [{"roster_id": 1, "team_id": "t1"}])
    me, them = g["teams"]
    assert me["team_id"] == "t1" and g["my_roster_id"] == 1 and g["is_final"] is False
    assert me["players"][0]["proj"] == 22.0 and me["players"][0]["rank"] == "QB2"
    assert me["projected"] == 36.0          # max(20 real, 22 proj) while playing + 14 projected
    assert them["projected"] == 15.0
    assert box_score.game(_C(), ctx, 8, 99) is None


def test_game_adds_live_game_state_and_stat_lines(monkeypatch):
    rows = [{"matchup_id": 3, "roster_id": 1, "starters": ["10"], "starters_points": [19.9], "points": 19.9},
            {"matchup_id": 3, "roster_id": 2, "starters": ["30"], "starters_points": [4.0], "points": 4.0}]
    monkeypatch.setattr(teams.sleeper, "get", lambda path, ttl=60: rows)
    monkeypatch.setattr(league, "last_scored_week", lambda sid: 7)
    monkeypatch.setattr(teams, "player_info", lambda client, ids: {"10": {"full_name": "A", "position": "QB", "team": "BUF"},
                                                                    "30": {"full_name": "C", "position": "WR", "team": "MIA"}})
    monkeypatch.setattr(projections, "week_projections", lambda *a, **k: {"10": 28.0, "30": 15.0})
    monkeypatch.setattr(projections, "week_ranks", lambda *a, **k: {})
    monkeypatch.setattr(projections, "week_lines", lambda *a, **k: {
        "10": {"stats": {"pass_cmp": 18, "pass_att": 26, "pass_yd": 221, "pass_td": 2, "rush_yd": 31}},
        "30": {"stats": {"rec": 2, "rec_tgt": 3, "rec_yd": 19, "fum_lost": 1}}})
    monkeypatch.setattr(projections, "league_scoring", lambda sid: {})
    monkeypatch.setattr(home, "player_status", lambda team, starts, now: "waiting")  # kickoff guess is wrong: ESPN says live
    live = nfl_live.parse_scoreboard(_board())
    monkeypatch.setattr(nfl_live, "week_games", lambda season, week, final: live)
    ctx = {"season": 2026, "sleeper_id": "S", "names": {1: "Tommy", 2: "Nick"}, "slots": ["QB"], "sleeper_roster_id": 1}
    g = box_score.game(_C(), ctx, 8, 3)
    qb = g["teams"][0]["players"][0]
    wr = g["teams"][1]["players"][0]
    assert qb["status"] == "playing" and qb["game"]["score"] == 24 and qb["game"]["possession"] is True and qb["game"]["red_zone"] is True
    assert [x["text"] for x in qb["line"]] == ["18/26", "221 YD", "2 TD", "0 INT", "31 RUSH"]
    assert wr["game"]["possession"] is False and wr["game"]["down"] == ""
    assert [x["text"] for x in wr["line"]] == ["2/3 REC", "19 YD", "1 FUM"] and wr["line"][-1]["tone"] == "bad"
    assert sum(g["win_chance"]) == 100 and g["win_chance"][0] > 50


def _board():
    return {"events": [{"status": {"period": 3, "displayClock": "8:42", "type": {"state": "in", "shortDetail": "8:42 - 3rd"}},
                        "competitions": [{"competitors": [
                            {"homeAway": "home", "score": "24", "team": {"id": "2", "abbreviation": "BUF"}},
                            {"homeAway": "away", "score": "13", "team": {"id": "15", "abbreviation": "MIA"}}],
                            "situation": {"possession": "2", "isRedZone": True, "shortDownDistanceText": "1st & 10", "possessionText": "MIA 18", "yardLine": 82}}]},
                       {"status": {"period": 0, "displayClock": "0:00", "type": {"state": "pre"}},
                        "competitions": [{"competitors": [
                            {"homeAway": "home", "score": "0", "team": {"id": "6", "abbreviation": "DAL"}},
                            {"homeAway": "away", "score": "0", "team": {"id": "28", "abbreviation": "WSH"}}]}]}]}


def test_scoreboard_maps_espn_codes_and_stat_lines_by_position():
    games = nfl_live.parse_scoreboard(_board())
    assert games["WAS"]["opp"] == "DAL" and games["WAS"]["home"] is False and games["WAS"]["state"] == "pre"
    assert games["MIA"]["opp_score"] == 24 and games["MIA"]["possession"] is False
    rb = nfl_live.stat_line({"rush_att": 17, "rush_yd": 88, "rush_td": 1, "rec": 3, "rec_yd": 19, "fum_lost": 1}, "RB")
    assert [x["text"] for x in rb] == ["17 CAR", "88 YD", "1 TD", "3 REC 19 YD", "1 FUM"]
    assert nfl_live.stat_line({}, "WR") == [] and nfl_live.stat_line({"rec": 1}, "K") == []
    assert nfl_live.win_chance([{"score": 130}, {"score": 120}], True) == [100, 0]
