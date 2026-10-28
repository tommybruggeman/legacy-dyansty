from api import player


def _games():
    return [
        {"week": 1, "home_team": "DEN", "away_team": "SEA", "home_score": 20, "away_score": 17},
        {"week": 2, "home_team": "LA", "away_team": "DEN", "home_score": 10, "away_score": 24},
        {"week": 4, "home_team": "DEN", "away_team": "KC", "home_score": None, "away_score": None, "gameday": "2026-10-04", "gametime": "16:25", "roof": "outdoors"},
        {"week": 3, "home_team": "DEN", "away_team": "DAL", "home_score": 3, "away_score": 9},
        {"week": 3, "home_team": "SEA", "away_team": "LA", "home_score": 3, "away_score": 9},
        {"week": 1, "home_team": "KC", "away_team": "LA", "home_score": 3, "away_score": 9},
        {"week": 2, "home_team": "KC", "away_team": "SEA", "home_score": 3, "away_score": 9},
    ]


def test_game_log_marks_started_bench_out_and_free_agent():
    league_weeks = [
        {"week": 1, "points": 8.4, "started": False},
        {"week": 2, "points": 21.3, "started": True},
        {"week": 3, "points": None, "started": False},
    ]
    nfl = [
        {"week": 1, "opponent": "SEA", "is_home": True, "carries": 7, "rushing_yards": 31, "receptions": 3, "receiving_yards": 23, "offense_pct": 0.34},
        {"week": 2, "opponent": "LA", "is_home": False, "carries": 18, "rushing_yards": 131, "rushing_tds": 2, "offense_pct": 0.72},
    ]
    log = player.game_log(league_weeks, nfl, player.schedule_by_week(_games(), "DEN"), None, "RB")
    assert [g["week"] for g in log] == [3, 2, 1]
    assert log[0]["status"] == "out" and log[0]["opp"] == "vs DAL" and log[0]["points"] is None
    assert log[1]["status"] == "started" and log[1]["opp"] == "at LAR"
    assert log[1]["line"]["rush_yds"] == 131 and log[1]["line"]["snap_pct"] == 72
    assert log[2]["status"] == "bench"

    free = player.game_log([{"week": 1, "points": None, "started": False}], nfl, {}, None, "RB")
    assert free[0]["status"] == "free_agent"


def test_bye_week():
    log = player.game_log([{"week": 5, "points": None, "started": False}], [], {}, 5, "RB")
    assert log[0]["status"] == "bye" and log[0]["opp"] == ""


def test_season_tiles_by_position():
    rows = [{"week": 1, "carries": 10, "rushing_yards": 50, "receptions": 2, "rushing_tds": 1, "offense_pct": 0.5},
            {"week": 2, "carries": 10, "rushing_yards": 42, "receptions": 3, "receiving_tds": 1, "offense_pct": 0.7}]
    rb = player.season_tiles(rows, "RB")
    assert [t["value"] for t in rb["tiles"]] == ["92", "4.6", "5", "2"]
    assert rb["snap_pct"] == 60 and rb["games"] == 2
    wr = player.season_tiles(rows, "WR")
    assert wr["tiles"][0]["label"] == "catches"
    assert player.season_tiles([], "RB") is None


def test_league_ranks_by_position():
    weeks = [[{"players_points": {"a": 10, "b": 20, "c": 5}}], [{"players_points": {"a": 15, "b": 1}}]]
    ranks = player.league_ranks(weeks, {"a": "RB", "b": "RB", "c": "WR"})
    assert ranks["a"]["rank"] == 1 and ranks["b"]["rank"] == 2 and ranks["a"]["of"] == 2
    assert ranks["c"] == {"rank": 1, "of": 1, "pos": "WR", "total": 5.0}


def test_contract_history_titles_and_tags():
    rows = [
        {"event_type": "signed", "source": "sleeper_waiver", "acquisition_type": "sleeper_waiver", "effective_at": "2026-10-14T12:00:00Z",
         "new_values": {"salary": 3, "years": 2}, "contract_status": "active", "league_team_id": "t1", "owner_name": "Tommy"},
        {"event_type": "released", "effective_at": "2026-09-20T12:00:00Z", "previous_values": {"salary": 3},
         "contract_status": "released", "league_team_id": "t2", "owner_name": "Dylan", "contract_type": "rookie"},
        {"event_type": "signed", "acquisition_type": "rookie_draft", "effective_at": "2026-05-02T12:00:00Z",
         "new_values": {"salary": 3, "years": 3}, "contract_status": "released", "league_team_id": "t3", "owner_name": "Nando", "contract_type": "rookie"},
        {"event_type": "signed", "acquisition_type": "sleeper_free_agent", "effective_at": "2026-07-02T12:00:00Z",
         "new_values": {"salary": 1, "years": 1}, "contract_status": "expired", "league_team_id": "t3", "owner_name": "Nando"},
    ]
    out = player.contract_history(rows, {"t1": 2})
    assert out[0]["title"] == "Mid-season pickup" and out[0]["tag"] == "2 YRS LEFT" and out[0]["date"] == "Oct 2026"
    assert out[1]["title"] == "Released" and out[1]["tag"] == "ENDED" and out[1]["salary"] == 3.0
    assert out[2]["title"] == "Rookie draft" and out[2]["tag"] == "ROOKIE DEAL" and out[2]["years"] == 3
    assert out[3]["title"] == "Free agent pickup"


def test_next_game_with_defense_rank_and_bye():
    dvp = {("KC", "RB"): {"rank": 29, "teams": 32, "ppg_allowed": 28.1, "factor": 1.2}}
    game = player.next_game(_games(), 2026, "DEN", "RB", dvp, None)
    assert game["week"] == 4 and game["opp"] == "KC" and game["home"] is True
    assert game["vs_pos"]["text"] == "4th easiest" and game["kickoff"].startswith("2026-10-04T20:25")
    assert player.next_game(_games(), 2026, "DEN", "RB", dvp, 4) == {"week": 4, "bye": True}


def test_health_summary():
    h = player.health({"injury_status": ""}, None,
                      [{"season": 2026, "week": 8, "practice_status": "Full Participation in Practice", "report_status": None}],
                      [{"injury": "Hamstring", "last_game_season": 2026, "games_missed": 1, "returned": True}], 2026)
    assert h["healthy"] and h["status"] == "Healthy"
    assert h["this_week"]["practice"] == "Full practice"
    assert h["missed"] == [{"injury": "hamstring", "season": 2026, "games": 1, "back": True}]


def test_active_counts_as_healthy_and_injury_estimate():
    h = player.health({"injury_status": "Active"}, None, [], [], 2026)
    assert h["healthy"] and h["status"] == "Healthy" and h["estimate"] is None
    eps = [{"injury": "hamstring", "position": "WR", "games_missed": g, "returned": True, "pre_snap_pct": 0.8} for g in (1, 2, 2, 3, 5, 1, 2, 4)]
    est = player.injury_estimate(eps, "WR", "hamstring")
    assert est["median_games"] == 2 and est["text"].startswith("Hamstring: usually 2 games out")
    hurt = player.health({"injury_status": "Questionable"}, None, [], [], 2026, est)
    assert not hurt["healthy"] and hurt["estimate"]["median_games"] == 2
