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
