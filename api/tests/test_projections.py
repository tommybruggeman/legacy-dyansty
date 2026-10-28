from api import cache, projections, sleeper

SCORING = {"rush_yd": 0.1, "rush_td": 6, "rec": 0.5, "rec_yd": 0.1, "pass_yd": 0.04}


def teardown_function():
    cache.clear()
    sleeper.clear()


def test_score_uses_league_rules():
    assert projections.score({"rush_yd": 100, "rush_td": 1, "rec": 4, "rec_yd": 30, "fum": 1}, SCORING) == 21.0
    assert projections.score(None, SCORING) == 0.0


def test_ranks_by_position_skip_players_who_did_not_play():
    lines = {
        "a": {"stats": {"rush_yd": 50, "gp": 1}, "pos": "RB"},
        "b": {"stats": {"rush_yd": 120, "gp": 1}, "pos": "RB"},
        "c": {"stats": {"pass_yd": 300, "gp": 1}, "pos": "QB"},
        "d": {"stats": {}, "pos": "RB"},
    }
    r = projections.ranks(lines, SCORING)
    assert r["b"]["label"] == "RB1" and r["a"]["label"] == "RB2" and r["c"]["label"] == "QB1"
    assert "d" not in r


def test_rows_reads_both_sleeper_shapes():
    rows = projections._rows([{"player_id": "1", "stats": {"rec": 3}, "player": {"position": "WR", "team": "LA"}}])
    assert rows["1"] == {"stats": {"rec": 3}, "pos": "WR", "team": "LAR"}
    assert projections._rows({"2": {"rec": 1}})["2"]["stats"] == {"rec": 1}


def test_week_projections_score_and_mark_byes(monkeypatch):
    monkeypatch.setattr(sleeper, "get_url", lambda url, ttl=0: [{"player_id": "1", "stats": {"rush_yd": 80, "rush_td": 0.5}, "player": {"position": "RB", "team": "DEN"}},
                                                                {"player_id": "2", "stats": {"rec": 6, "rec_yd": 70}, "player": {"position": "WR", "team": "BUF"}}])
    monkeypatch.setattr(sleeper, "get", lambda path, ttl=0: {"scoring_settings": SCORING})
    monkeypatch.setattr(projections, "season_games", lambda client, season: [{"week": 9, "home_team": "DEN", "away_team": "KC"}])
    monkeypatch.setattr(projections, "defense_vs_position", lambda client, season: {})
    monkeypatch.setattr(projections, "add_weather", lambda games, season, week: games)
    out = projections.week_projections(None, "S", 2026, 9, ["1", "2"], {"1": "RB", "2": "WR"}, {"1": "DEN", "2": "BUF"})
    assert out["1"] == 11.0  # 8 rush + 3 for half a TD, average matchup
    assert out["2"] == 0.0  # BUF has no game this week: bye


def test_breakdown_lists_scoring_stats_biggest_first():
    from api import projections
    rows = projections.breakdown({"rush_yd": 87, "rush_td": 1, "rec": 3, "rec_yd": 22, "fum_lost": 1, "rush_att": 15},
                                 {"rush_yd": 0.1, "rush_td": 6, "rec": 0.5, "rec_yd": 0.1, "fum_lost": -2, "pass_yd": 0.04})
    assert rows[0] == {"label": "rush yds", "value": 87, "points": 8.7}
    assert [r["label"] for r in rows] == ["rush yds", "rush TD", "rec yds", "fumbles lost", "catches"]
    assert projections.breakdown(None, {"rush_yd": 0.1}) == []
