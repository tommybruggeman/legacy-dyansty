from types import SimpleNamespace

from api import market

STATE = {"roster": [
    {"league_team_id": "t1", "sleeper_player_id": "1", "player_name": "Derrick Henry", "pos": "RB", "owner_name": "Nick", "cap_hit": 31, "contract_years_left": 1},
    {"league_team_id": "t2", "sleeper_player_id": "2", "player_name": "Davante Adams", "pos": "WR", "owner_name": "Tommy", "cap_hit": 7, "contract_cap_hit": 14, "contract_years_left": 1},
    {"league_team_id": "t2", "sleeper_player_id": "3", "player_name": "Josh Allen", "pos": "QB", "owner_name": "Tommy", "cap_hit": 45, "contract_years_left": 3},
]}
POOL = [
    {"sleeper_id": "1", "full_name": "Derrick Henry", "position": "RB"},
    {"sleeper_id": "7", "full_name": "Tyjae Spears", "position": "RB", "team": "TEN"},
    {"sleeper_id": "8", "full_name": "Bryce Young", "position": "QB", "team": "CAR", "years_exp": 0},
    {"sleeper_id": "9", "full_name": "Nobody", "position": "WR"},
]
PPG = SimpleNamespace(active_season=2026, last_season=2025, active_season_started=True,
                      current_ppg={"7": 9.8, "8": 16.1}, last_ppg={"7": 7.0}, ranking_ppg={"7": 9.8, "8": 16.1})


def test_free_agents_skip_signed_players_and_rank_by_ppg():
    result = market.free_agents(POOL, STATE, PPG)
    assert [p["name"] for p in result["players"]] == ["Bryce Young", "Tyjae Spears", "Nobody"]
    assert result["players"][0]["rookie"] and result["players"][1]["last_ppg"] == 7.0
    assert market.free_agents(POOL, STATE, PPG, limit=1)["players"][0]["name"] == "Bryce Young"


def test_expiring_lists_last_year_contracts_biggest_first():
    rows = market.expiring(STATE, "t2")
    assert [(r["name"], r["salary"], r["is_mine"]) for r in rows] == [("Derrick Henry", 31.0, False), ("Davante Adams", 14.0, True)]


def test_expiring_in_a_later_season():
    assert [r["name"] for r in market.expiring(STATE, "t2", years_out=2)] == ["Josh Allen"]
    assert market.expiring(STATE, "t2", years_out=1) == []


def test_cache_keeps_one_copy():
    market.clear()
    calls = []
    load = lambda: calls.append(1) or "x"  # noqa: E731
    assert market._cached("k", load) == "x" and market._cached("k", load) == "x"
    assert len(calls) == 1
    market.clear()


def test_free_agents_skip_players_on_a_sleeper_roster_without_a_contract():
    from types import SimpleNamespace
    from api import market as m
    pool = [{"sleeper_id": "1", "full_name": "Omar Cooper Jr.", "position": "WR"}, {"sleeper_id": "2", "full_name": "Real FA", "position": "WR"}]
    ppg = SimpleNamespace(current_ppg={}, last_ppg={}, ranking_ppg={}, active_season=2026, last_season=2025, active_season_started=True)
    out = m.free_agents(pool, {"roster": []}, ppg, rostered={"1": 6})
    assert [p["name"] for p in out["players"]] == ["Real FA"]


GAMES = SimpleNamespace(active_season=2026, last_season=2025, active_season_started=True,
                        current_ppg={"7": 9.8, "8": 16.1}, last_ppg={}, ranking_ppg={"7": 9.8, "8": 16.1, "1": 20.0},
                        ranking_games={"7": 8, "8": 3, "1": 7})


def test_free_agents_rank_by_season_points_and_keep_ppg():
    rows = market.free_agents(POOL, STATE, GAMES)["players"]
    assert [(p["name"], p["points"]) for p in rows] == [("Tyjae Spears", 78.4), ("Bryce Young", 48.3), ("Nobody", None)]
    assert rows[1]["rank_ppg"] == 16.1


def test_expiring_adds_nfl_team_and_points():
    rows = market.expiring(STATE, "t2", pool=[{"sleeper_id": "1", "team": "TEN"}], ppg=GAMES)
    assert (rows[0]["name"], rows[0]["nfl_team"], rows[0]["points"]) == ("Derrick Henry", "TEN", 140.0)
    assert rows[1]["nfl_team"] == "FA" and rows[1]["points"] is None


def test_targets_show_owner_or_free_agent_in_saved_order():
    rows = market.targets(["8", "2", "missing"], STATE, "t2", POOL, GAMES)
    assert [(t["name"], t["owner_name"], t["is_mine"]) for t in rows] == [("Bryce Young", None, False), ("Davante Adams", "Tommy", True)]
    assert rows[0]["salary"] is None and rows[1]["salary"] == 14.0 and rows[0]["nfl_team"] == "CAR"


def test_strategy_question_names_each_target():
    saved = [{"name": "Bijan Robinson", "owner_name": "Nick"}, {"name": "Tyjae Spears", "owner_name": None}]
    q = market.strategy_question(saved, 2026)
    assert "Bijan Robinson (Nick) and Tyjae Spears (free agent)" in q
    assert market.strategy_question([], 2026) == "Plan my offseason after the 2026 season."


TEAMS = [
    {"team_id": "t1", "owner_name": "Nick", "team_name": "Nick's", "roster_id": 1},
    {"team_id": "t2", "owner_name": "Tommy", "team_name": "Tommy's", "roster_id": 2},
]
ROSTERS = [
    {"roster_id": 1, "players": ["1", "7"], "taxi": ["7"]},
    {"roster_id": 2, "players": ["2", "3"], "reserve": ["3"]},
]


def test_all_players_lists_rostered_and_free_agents_with_owners():
    result = market.all_players(POOL, STATE, PPG, TEAMS, ROSTERS, "t2")
    rows = {p["player_id"]: p for p in result["players"]}
    assert set(rows) == {"1", "2", "3", "7", "8", "9"}
    # On a Sleeper taxi squad without a contract row: rostered to Nick, no salary.
    assert (rows["7"]["owner_name"], rows["7"]["designation"], rows["7"]["salary"]) == ("Nick", "taxi", None)
    assert (rows["3"]["is_mine"], rows["3"]["designation"], rows["3"]["salary"], rows["3"]["years_left"]) == (True, "ir", 45.0, 3)
    assert rows["8"]["team_id"] is None and rows["8"]["rookie"]
    assert [p["name"] for p in result["players"]][:2] == ["Bryce Young", "Tyjae Spears"]
    assert [o["owner_name"] for o in result["owners"]] == ["Tommy", "Nick"]


def test_all_players_caps_free_agents_only():
    result = market.all_players(POOL, STATE, PPG, TEAMS, ROSTERS, "t2", fa_limit=1)
    assert sorted(p["player_id"] for p in result["players"]) == ["1", "2", "3", "7", "8"]


def test_filter_players_like_the_app():
    rows = market.all_players(POOL, STATE, PPG, TEAMS, ROSTERS, "t2")["players"]
    names = lambda **kw: [p["name"] for p in market.filter_players(rows, **kw)]  # noqa: E731
    assert names(status="Free agents") == ["Bryce Young", "Nobody"]
    assert names(status="Rostered", owner="t2") == ["Davante Adams", "Josh Allen"]
    # A search ignores the status row and looks through everyone.
    assert names(search="tyjae", status="Free agents") == ["Tyjae Spears"]
    assert names(contract="Taxi") == ["Tyjae Spears"]
    assert names(salary="Under $20") == ["Davante Adams"]
    assert names(salary="$20+") == ["Derrick Henry", "Josh Allen"]
    assert market.name_matches("A.J. Brown", "aj brown") and market.name_matches("Christian McCaffrey", "mc")


def test_filter_players_contract_ends_and_saved_free_agents():
    rows = market.all_players(POOL, STATE, PPG, TEAMS, ROSTERS, "t2")["players"]
    ends = lambda year: [p["name"] for p in market.filter_players(rows, ends=year, season=2026)]  # noqa: E731
    assert ends(2026) == ["Davante Adams", "Derrick Henry"]
    assert ends(2028) == ["Josh Allen"]
    assert ends(2027) == []
    assert [p["name"] for p in market.filter_players(rows, status="Rostered", sort="Salary")][:2] == ["Josh Allen", "Derrick Henry"]
    # A saved free agent past the cut still comes back so the Saved chip finds him.
    kept = market.all_players(POOL, STATE, PPG, TEAMS, ROSTERS, "t2", fa_limit=1, keep_ids=["9"])["players"]
    assert "9" in {p["player_id"] for p in kept}


def test_all_players_without_a_kicker_slot():
    pool = POOL + [{"sleeper_id": "11", "full_name": "Kicky", "position": "K"}]
    with_k = market.all_players(pool, STATE, PPG, TEAMS, ROSTERS, "t2")
    without = market.all_players(pool, STATE, PPG, TEAMS, ROSTERS, "t2", kickers=False)
    assert "K" in with_k["positions"] and "11" in {p["player_id"] for p in with_k["players"]}
    assert without["positions"] == ["QB", "RB", "WR", "TE"] and "11" not in {p["player_id"] for p in without["players"]}
