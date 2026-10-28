from datetime import date, datetime, timezone

from api import home


def test_phase_follows_the_calendar():
    sunday, tuesday = date(2026, 10, 25), date(2026, 10, 27)
    assert home.phase(sunday, "in_season", 8, 15) == "game_day"
    assert home.phase(tuesday, "in_season", 8, 15) == "recap"
    assert home.phase(tuesday, "in_season", 15, 15) == "playoffs"
    assert home.phase(date(2027, 2, 10), "complete", 18, 15) == "decisions"
    assert home.phase(date(2027, 6, 30), "complete", 1, 15) == "decisions"
    assert home.phase(date(2027, 7, 1), "pre_draft", 1, 15) == "draft_prep"
    assert home.phase(date(2027, 9, 1), "pre_draft", 1, 15) == "draft_prep"


def test_my_game_and_bye():
    games = [{"matchup_id": 1, "teams": [{"roster_id": 2, "score": 90}, {"roster_id": 5, "score": 96.4}]}]
    game = home.my_game(games, 5)
    assert game["me"]["score"] == 96.4 and game["them"]["roster_id"] == 2
    assert home.my_game(games, 9) is None


def test_standings_table_always_includes_me():
    rows = [{"rank": i, "roster_id": i, "name": f"T{i}", "wins": 9 - i, "losses": i, "standing_points": 20 - i, "is_me": i == 9} for i in range(1, 11)]
    table = home.standings_table(rows, {9: "team-9"}, {9: "pic"}, 4, show=5)
    assert [r["rank"] for r in table] == [1, 2, 3, 4, 9]
    assert table[3]["in"] and not table[4]["in"]
    assert table[4]["is_me"] and table[4]["team_id"] == "team-9" and table[4]["avatar"] == "pic"


def test_bracket_keeps_title_path_with_scores():
    matches = [
        {"r": 1, "m": 1, "t1": 1, "t2": 4, "w": 1, "l": 4},
        {"r": 1, "m": 2, "t1": 2, "t2": 3, "w": None},
        {"r": 2, "m": 3, "t1": 1, "t2": None, "t1_from": {"w": 1}, "t2_from": {"w": 2}, "p": 1},
        {"r": 2, "m": 4, "t1": 4, "t2": None, "p": 3},
    ]
    rounds = home.bracket(matches, {1: "Kevin", 2: "Nick", 3: "Tommy", 4: "Mekel"}, {(1, 1): 102.4, (1, 4): 94.0}, 15)
    assert [(r["label"], r["week"], len(r["games"])) for r in rounds] == [("Semifinal", 15, 2), ("Final", 16, 1)]
    assert rounds[0]["games"][0]["a"] == {"roster_id": 1, "name": "Kevin", "score": 102.4}
    assert rounds[1]["games"][0]["b"] is None


ROSTER = [
    {"league_team_id": "t1", "sleeper_player_id": "1", "player_name": "Rome Odunze", "pos": "WR", "cap_hit": 4, "contract_years_left": 1, "is_rookie": True},
    {"league_team_id": "t1", "sleeper_player_id": "2", "player_name": "Ray Davis", "pos": "RB", "cap_hit": 2, "contract_years_left": 1, "is_rookie": True},
    {"league_team_id": "t1", "sleeper_player_id": "3", "player_name": "Josh Allen", "pos": "QB", "cap_hit": 45, "contract_years_left": 3},
    {"league_team_id": "t1", "sleeper_player_id": "4", "player_name": "Tank Dell", "pos": "WR", "cap_hit": 4, "contract_years_left": 1},
    {"league_team_id": "t2", "sleeper_player_id": "5", "player_name": "Other", "pos": "TE", "cap_hit": 9, "contract_years_left": 1, "is_rookie": True},
]


def test_rookie_decisions_name_the_player_and_choice():
    decisions = [
        {"id": "d1", "player_id": "1", "decision_status": "waiting_for_owner"},
        {"id": "d2", "player_id": "2", "decision_status": "decline_submitted", "owner_choice": "decline"},
    ]
    rows = home.rookie_decisions(decisions, ROSTER, {"1": 16.3})
    assert [(r["name"], r["open"], r["choice"]) for r in rows] == [("Rome Odunze", True, None), ("Ray Davis", False, "walk")]
    assert rows[0]["ppg"] == 16.3 and rows[0]["salary"] == 4.0


def test_rookie_deals_ending_before_the_window_opens():
    rows = home.rookie_deals_ending(ROSTER, "t1", {})
    assert [r["name"] for r in rows] == ["Rome Odunze", "Ray Davis"] and not rows[0]["open"]


def test_draft_prep_pieces():
    books = home.coming_off_books(ROSTER, "t1")
    assert books["total"] == 10.0 and books["players"][0]["name"] in {"Rome Odunze", "Tank Dell"}
    summaries = [{"owner_name": "A", "cap_by_year": [{"space": 10}, {"space": 80}]}, {"owner_name": "B", "cap_by_year": [{"space": 50}, {"space": 20}]}]
    assert [m["name"] for m in home.who_has_money(summaries)] == ["A", "B"]
    assert [m["name"] for m in home.who_has_money(summaries, season_index=0)] == ["B", "A"]
    picks = home.upcoming_picks([{"year": 2028, "round": 1}, {"year": 2027, "round": 2}, {"year": 2027, "round": 1}])
    assert picks["year"] == 2027 and len(picks["picks"]) == 2


def test_avatars_prefer_team_picture():
    users = [{"user_id": "u1", "avatar": "abc", "metadata": {}}, {"user_id": "u2", "avatar": "def", "metadata": {"avatar": "https://x/team.png"}}, {"user_id": "u3"}]
    rosters = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"}, {"roster_id": 3, "owner_id": "u3"}]
    assert home.avatars(users, rosters) == {1: "https://sleepercdn.com/avatars/thumbs/abc", 2: "https://x/team.png", 3: None}


def test_lineup_status_from_kickoff():
    # Sunday 1:00 PM Eastern is 17:00 UTC in late October (EDT).
    starts = home.kickoffs([{"gameday": "2026-10-25", "gametime": "13:00", "home_team": "BUF", "away_team": "LA"},
                            {"gameday": "2026-10-26", "gametime": "20:15", "home_team": "DET", "away_team": "MIN"}])
    assert starts["LAR"] == datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
    now = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)
    row = {"starters": ["1", "2", "0", "3", "DET"], "starters_points": [12.5, 0, 0, 0, 0]}
    info = {"1": {"full_name": "Josh Allen", "position": "QB", "team": "BUF"},
            "2": {"full_name": "Amon-Ra St. Brown", "position": "WR", "team": "DET", "injury_status": "Questionable"},
            "3": {"full_name": "Bye Guy", "position": "TE", "team": "KC"}}
    players = home.lineup(row, info, starts, now)
    assert [p["status"] for p in players] == ["playing", "waiting", "empty", "bye", "waiting"]
    assert players[0]["points"] == 12.5 and players[4]["pos"] == "DEF"
    issues = home.lineup_issues(players)
    assert [(p["name"], p["issue"]) for p in issues] == [("Amon-Ra St. Brown", "Questionable"), ("Empty slot", "Empty slot"), ("Bye Guy", "On bye")]
    later = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)
    assert home.lineup(row, info, starts, later)[0]["status"] == "final"


def test_standing_points_earned():
    win_top5 = {"me": {"score": 130, "top5": True}, "them": {"score": 110}}
    loss = {"me": {"score": 90, "top5": False}, "them": {"score": 110}}
    assert home.standing_points_earned(win_top5) == 3
    assert home.standing_points_earned(loss) == 0
    assert home.standing_points_earned(None) == 0
