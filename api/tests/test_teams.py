import pytest

from api import sleeper, teams

T1, T2 = "t1", "t2"

STATE = {
    "season": 2026,
    "roster": [
        {"league_team_id": T1, "sleeper_player_id": "10", "player_name": "Josh Allen", "pos": "QB", "cap_hit": 45, "contract_years_left": 3},
        {"league_team_id": T1, "sleeper_player_id": "11", "player_name": "Bijan Robinson", "pos": "RB", "cap_hit": 32.5, "contract_years_left": 2},
        {"league_team_id": T1, "sleeper_player_id": "12", "player_name": "Rome Odunze", "pos": "WR", "cap_hit": 2.67, "contract_cap_hit": 4, "contract_years_left": 2, "roster_designation": "taxi"},
        {"league_team_id": T1, "sleeper_player_id": "13", "player_name": "Davante Adams", "pos": "WR", "cap_hit": 14, "contract_years_left": 1, "roster_designation": "ir"},
        {"league_team_id": T1, "sleeper_player_id": "14", "player_name": "Tank Dell", "pos": "WR", "cap_hit": 4, "contract_years_left": 1},
        {"league_team_id": T2, "sleeper_player_id": "20", "player_name": "Brock Bowers", "pos": "TE", "cap_hit": 9, "contract_years_left": 2},
    ],
    "dead_cap": [{"league_team_id": T1, "player_name": "Zack Moss", "amount": 3, "adjustment_type": "dropped_player_charge", "season": 2026}],
    "cap_adjustments": [{"league_team_id": T1, "amount": -7, "adjustment_type": "ir_adjustment", "season": 2026}],
    "activity": [
        {"league_team_id": T1, "event_type": "signed", "player_name": "Tank Dell", "effective_at": "2026-10-20"},
        {"league_team_id": T2, "event_type": "released", "player_name": "Someone", "effective_at": "2026-10-19"},
    ],
    "draft_picks": [
        {"draft_year": 2027, "round_number": 1, "original_league_team_id": T1, "current_owner_league_team_id": T1, "original_owner_name": "Tommy", "current_owner_name": "Tommy"},
        {"draft_year": 2027, "round_number": 2, "original_league_team_id": T2, "current_owner_league_team_id": T1, "original_owner_name": "Grady", "current_owner_name": "Tommy"},
        {"draft_year": 2027, "round_number": 3, "original_league_team_id": T1, "current_owner_league_team_id": T2, "original_owner_name": "Tommy", "current_owner_name": "Grady"},
    ],
}
NEXT = {"season": 2027, "roster": [{"league_team_id": T1, "cap_hit": 77.5}], "dead_cap": [], "cap_adjustments": []}

SLEEPER = {
    "league/S/rosters": [{"roster_id": 1, "starters": ["10", "11", "14"]}, {"roster_id": 2, "starters": ["20"], "taxi": ["21"]}],
    "league/S/matchups/1": [
        {"roster_id": 1, "matchup_id": 1, "points": 120, "starters": ["10", "11", "14"], "starters_points": [30, 20, 10], "players_points": {"10": 30, "11": 20, "14": 10, "12": 0},
         "players": ["10", "11", "14", "12"]},
        {"roster_id": 2, "matchup_id": 1, "points": 100, "starters": ["20"], "starters_points": [100], "players_points": {"20": 100}, "players": ["20", "21"]},
    ],
    "league/S/matchups/2": [
        {"roster_id": 1, "matchup_id": 1, "points": 80, "players_points": {"10": 20, "11": 10}},
        {"roster_id": 2, "matchup_id": 1, "points": 90, "players_points": {"20": 90}},
    ],
}


@pytest.fixture(autouse=True)
def fake_sleeper(monkeypatch):
    sleeper.clear()
    monkeypatch.setattr(sleeper, "fetch", lambda url: SLEEPER[url.removeprefix(sleeper.BASE + "/")])
    yield
    sleeper.clear()


def test_cap_by_year_uses_cap_hits_dead_cap_and_ir_credit():
    years = teams.cap_by_year([STATE, NEXT], 225, T1)
    # 45 + 32.5 + 2.67 + 14 + 4 = 98.17 salary, + 3 dead cap, - 7 IR credit
    assert years[0] == {"season": 2026, "used": 94.17, "space": 130.83, "dead_cap": 3.0}
    assert years[1]["season"] == 2027 and years[1]["space"] == 147.5


def test_summaries_follow_standings_order_and_lean():
    team_rows = [
        {"team_id": T1, "owner_name": "Tommy", "team_name": "Ballers", "roster_id": 1},
        {"team_id": T2, "owner_name": "Grady", "team_name": "G Money", "roster_id": 2},
    ]
    standings = [{"roster_id": 2, "rank": 1, "wins": 2}, {"roster_id": 1, "rank": 2, "wins": 0, "is_me": True}]
    rows = teams.summaries(team_rows, [STATE], 225, standings, {"grady": "rebuilding"})
    assert [r["owner_name"] for r in rows] == ["Grady", "Tommy"]
    assert rows[0]["lean"] == "rebuilding" and rows[1]["is_me"]


def test_roster_splits_starters_bench_taxi_ir():
    ppg = teams.player_ppg("S", 2)
    roster = teams.roster_rows(STATE, T1, {"starters": ["10", "11", "14"]}, ["QB", "RB", "FLEX", "BN"], {}, ppg)
    assert [(p["name"], p["slot"]) for p in roster["starters"]] == [("Josh Allen", "QB"), ("Bijan Robinson", "RB"), ("Tank Dell", "FLX")]
    assert [p["name"] for p in roster["taxi"]] == ["Rome Odunze"]
    assert [p["name"] for p in roster["ir"]] == ["Davante Adams"]
    assert roster["bench"] == [] and roster["count"] == 5
    # Zero-point weeks don't count as games.
    assert ppg["10"] == 25.0 and "12" not in ppg


def test_picks_show_acquired_and_traded_away():
    picks = teams.picks_view(STATE, T1)
    assert [(p["year"], p["round"], p["own"]) for p in picks["owned"]] == [(2027, 1, True), (2027, 2, False)]
    assert picks["owned"][1]["original_owner"] == "Grady"
    assert [(p["round"], p["current_owner"]) for p in picks["traded_away"]] == [(3, "Grady")]


def test_picks_keep_tradable_and_drop_used_ones():
    # Supabase marks live picks "tradable"; drafted ones become consumed or historical.
    rows = [
        {**STATE["draft_picks"][0], "asset_status": "tradable"},
        {**STATE["draft_picks"][1], "asset_status": "consumed"},
        {**STATE["draft_picks"][0], "draft_year": 2026, "asset_status": "historical"},
    ]
    picks = teams.picks_view({**STATE, "draft_picks": rows}, T1)
    assert [(p["year"], p["round"]) for p in picks["owned"]] == [(2027, 1)]


def test_activity_and_contracts_only_for_this_team():
    assert teams.activity_view(STATE, T1) == [{"kind": "SIGN", "player": "Tank Dell", "at": "2026-10-20"}]
    roster = teams.roster_rows(STATE, T1, {}, [], {}, {})
    players = roster["bench"] + roster["taxi"] + roster["ir"]
    view = teams.contracts_view(players, STATE, T1)
    assert view["biggest"][0]["name"] == "Josh Allen" and view["biggest"][0]["through"] == 2028
    assert {p["name"] for p in view["expiring"]} == {"Davante Adams", "Tank Dell"}
    assert view["dead_cap"] == [{"player": "Zack Moss", "amount": 3.0, "player_id": None, "source": "drop", "years_left": 1, "through": 2026, "more": False}]


def test_dead_cap_counts_the_seasons_still_charged():
    moss = STATE["dead_cap"][0]
    later = [{"season": 2027, "dead_cap": [{**moss, "season": 2027}], "cap_adjustments": []},
             {"season": 2028, "dead_cap": [], "cap_adjustments": []}]
    view = teams.contracts_view([], STATE, T1, later)
    assert view["dead_cap"][0] | {} == {"player": "Zack Moss", "amount": 3.0, "player_id": None, "source": "drop", "years_left": 2, "through": 2027, "more": False}
    later[1]["dead_cap"] = [{**moss, "season": 2028}]
    assert teams.contracts_view([], STATE, T1, later)["dead_cap"][0]["more"] is True


def test_head_to_head_and_box_score():
    h2h = teams.head_to_head("S", 2, 1, 2)
    assert (h2h["wins"], h2h["losses"]) == (1, 1) and h2h["last"] == {"week": 2, "me": 80.0, "them": 90.0}
    assert teams.head_to_head("S", 2, 1, 1) is None

    box = teams.box_score("S", 1, 1, {1: "Tommy", 2: "Grady"}, ["QB", "RB", "FLEX"], {"10": {"full_name": "Josh Allen"}})
    tommy = box["teams"][0]
    assert tommy["name"] == "Tommy" and tommy["score"] == 120.0
    assert [(p["name"], p["slot"], p["points"]) for p in tommy["players"]][0] == ("Josh Allen", "QB", 30.0)
    assert tommy["players"][2]["slot"] == "FLX"
    assert [(p["player_id"], p["slot"], p["points"]) for p in tommy["bench"]] == [("12", "BN", 0.0)]
    assert box["teams"][1]["bench"] == []  # taxi player 21 can't play


def test_ir_rows_only_for_this_team():
    state = {"cap_adjustments": [
        {"id": "a", "league_team_id": T1, "adjustment_type": "ir_adjustment"},
        {"id": "b", "league_team_id": T2, "adjustment_type": "ir_adjustment"},
        {"id": "c", "league_team_id": T1, "adjustment_type": "trade_carryover"},
    ]}
    assert [r["id"] for r in teams.ir_rows(state, T1)] == ["a"]


def test_player_detail_has_contract_and_recent_weeks():
    weeks = teams.weekly_points("S", 2, "10")
    assert weeks == [{"week": 1, "points": 30.0, "started": True}, {"week": 2, "points": 20.0, "started": False}]
    detail = teams.player_detail("10", STATE, {"team": "BUF", "age": 30}, weeks, T1)
    assert detail["name"] == "Josh Allen" and detail["nfl_team"] == "BUF" and detail["ppg"] == 25.0
    assert detail["contract"]["salary"] == 45.0 and detail["contract"]["through"] == 2028
    assert detail["is_mine"]
    assert teams.player_detail("999", STATE, {}, [], T1) is None
    free_agent = teams.player_detail("999", STATE, {"full_name": "Tyjae Spears", "position": "RB"}, [], T1)
    assert free_agent["contract"] is None and free_agent["name"] == "Tyjae Spears"


def test_designated_players_never_show_as_starters():
    roster = teams.roster_rows(STATE, T1, {"starters": ["10", "13"]}, ["QB", "WR"], {}, {})
    assert [p["name"] for p in roster["starters"]] == ["Josh Allen"]
    assert [p["name"] for p in roster["ir"]] == ["Davante Adams"]


def test_value_board_ranks_by_points_per_dollar_and_grades_against_the_league():
    from api import teams as t
    state = {"roster": [{"sleeper_player_id": str(i), "contract_cap_hit": 10} for i in range(9)]}
    ppg = {str(i): float(i + 1) for i in range(9)}  # 0.1 .. 0.9 PPG per dollar
    cuts = t.league_value_cuts(state, ppg)
    players = [
        {"player_id": "a", "contract_salary": 2, "years_left": 1, "ppg": 10.0},
        {"player_id": "b", "contract_salary": 40, "years_left": 2, "ppg": 8.0},
        {"player_id": "c", "contract_salary": 10, "years_left": 2, "ppg": 5.0},
        {"player_id": "d", "contract_salary": 12, "years_left": 2, "ppg": None, "designation": "taxi"},
    ]
    board = t.value_board(players, cuts, 2026)
    assert [p["player_id"] for p in board] == ["a", "c", "b", "d"]
    assert [p["grade"] for p in board] == ["bargain", "fair", "overpaid", "stash"]
    assert board[1]["per_dollar"] == 0.5 and board[1]["through"] == 2027


def test_moves_count_completed_transactions_and_trades(monkeypatch):
    from api import teams as t
    weeks = {0: [{"transaction_id": "1", "status": "complete", "type": "trade", "roster_ids": [3, 5]}],
             1: [{"transaction_id": "2", "status": "complete", "type": "waiver", "roster_ids": [3]},
                 {"transaction_id": "3", "status": "failed", "type": "waiver", "roster_ids": [3]},
                 {"transaction_id": "4", "status": "complete", "type": "free_agent", "roster_ids": [7]}]}
    monkeypatch.setattr(t.sleeper, "get", lambda path, ttl=0: weeks.get(int(path.rsplit("/", 1)[1]), []))
    assert t.moves("L", 3, 1) == {"moves": 2, "trades": 1}
    assert t.moves("L", None, 1) == {"moves": 0, "trades": 0}


def test_faab_left_from_sleeper(monkeypatch):
    from api import teams as t
    monkeypatch.setattr(t.sleeper, "get", lambda path, ttl=0: {"settings": {"waiver_budget": 100}})
    assert t.faab("L", {"settings": {"waiver_budget_used": 58}}) == {"left": 42.0, "budget": 100.0}
    assert t.faab("L", {}) is None
