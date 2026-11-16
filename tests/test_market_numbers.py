from datetime import datetime, timedelta, timezone

from api import market


def test_position_ranks_by_season_points():
    ranks = market.position_ranks({"a": 100.0, "b": 150.0, "c": 90.0, "k": 5.0}, {"a": "WR", "b": "WR", "c": "RB", "k": "DEF"})
    assert ranks == {"b": "WR1", "a": "WR2", "c": "RB1"}


def test_defense_ranks_toughest_first_in_league_points():
    games = [(1, "WR", "NO", 30.0), (1, "WR", "CLE", 10.0), (2, "WR", "NO", 20.0), (2, "WR", "CLE", 12.0), (1, "K", "NO", 9.0)]
    opp = {("NO", 1): "CLE", ("CLE", 1): "NO", ("NO", 2): "CLE", ("CLE", 2): "NO"}
    ranks = market.defense_ranks(games, lambda t, w: opp.get((t, w)))
    assert ranks[("NO", "WR")] == {"rank": 1, "teams": 2}   # NO allowed 11/game: toughest
    assert ranks[("CLE", "WR")] == {"rank": 2, "teams": 2}  # CLE allowed 25/game
    assert ("NO", "K") not in ranks


def test_game_state_prefers_live_scoreboard_then_final_then_clock():
    now = datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc)
    early = {"bye": False, "kickoff": (now - timedelta(hours=6)).isoformat(), "final": False}
    later = {"bye": False, "kickoff": (now + timedelta(hours=1)).isoformat(), "final": False}
    playing = {"bye": False, "kickoff": (now - timedelta(hours=1)).isoformat(), "final": False}
    assert market.game_state(early, {"state": "in"}, now, 3.5) == "in"        # ESPN wins
    assert market.game_state(early, None, now, 3.5) == "post"                  # finished by the clock before nflverse has it
    assert market.game_state({**later, "final": True}, None, now, 3.5) == "post"
    assert market.game_state(later, None, now, 3.5) == "pre"
    assert market.game_state(playing, None, now, 3.5) == "in"
    assert market.game_state({"bye": True}, None, now, 3.5) is None
    assert market.game_state(None, None, now, 3.5) is None


def test_week_and_game_text_follow_the_game_state():
    from api import market_week
    pre = {"proj": 8.54, "next": {"week": 10, "bye": False, "opp": "CLE", "home": True, "kickoff": "2026-11-08T18:00:00+00:00", "opp_rank": 30, "teams": 32}}
    live = {"proj": 8.5, "week_pts": 11.8, "next": {"week": 10, "bye": False, "opp": "ARI", "home": True, "state": "in", "detail": "Q4 2:11", "score": 20, "opp_score": 17}}
    done = {"proj": 8.5, "week_pts": 14.2, "next": {"week": 10, "bye": False, "opp": "CLE", "home": True, "state": "post", "score": 24, "opp_score": 17}}
    assert market_week.week_text(pre) == "8.5"
    assert market_week.game_text(pre) == "Sun 1:00 PM ET · vs CLE · soft D"
    assert market_week.week_text(live) == "● 11.8"
    assert market_week.game_text(live) == "Q4 2:11 · vs ARI · 20-17"
    assert market_week.week_text(done) == "14.2"
    assert market_week.game_text(done) == "Final · W 24-17 vs CLE"
    assert market_week.week_text({"next": {"bye": True}}) == "BYE"


def test_player_numbers_last_three_games_and_final_game_counts():
    games = [(1, 10.0), (2, 20.0), (4, 6.0), (6, 4.0)]   # week 3 bye, week 5 hurt
    n = market.player_numbers(games, 7, bye_week=3, now_pts=14.0, now_state="post")
    assert n["games"] == 5 and n["points"] == 54.0 and n["avg"] == 10.8
    assert n["last3"] == 8.0 and n["last3_games"] == 3   # weeks 4, 6, 7: real games, not calendar weeks
    assert n["trend"] == -2.8
    assert [b["week"] for b in n["bars"]] == [2, 3, 4, 5, 6, 7]
    assert n["bars"][1] == {"week": 3, "pts": None, "bye": True}
    assert n["bars"][3]["pts"] is None and n["bars"][-1]["pts"] == 14.0


def test_player_numbers_live_game_is_a_bar_but_not_in_the_average():
    n = market.player_numbers([(1, 10.0)], 2, now_pts=5.5, now_state="in")
    assert n["games"] == 1 and n["avg"] == 10.0
    assert n["bars"][-1] == {"week": 2, "pts": 5.5, "live": True}
    assert market.player_numbers([], 2)["points"] is None


def test_sorts_use_the_numbers_on_screen():
    rows = [
        {"name": "A", "points": 80.0, "rank_ppg": 10.0, "last3": 9.1, "age": 28, "salary": None, "proj": 9.0, "next": {"state": "pre"}},
        {"name": "B", "points": 81.6, "rank_ppg": 10.2, "last3": 9.5, "age": 27, "salary": 4, "week_pts": 0.0, "next": {"state": "post"}},
        {"name": "C", "points": 76.5, "rank_ppg": 8.5, "last3": 10.0, "age": 27, "salary": 2, "week_pts": 17.1, "next": {"state": "post"}},
        {"name": "D", "points": None, "rank_ppg": None, "last3": None, "age": 22, "salary": None, "next": {"bye": True}},
    ]
    names = lambda s: [r["name"] for r in market.sort_rows(rows, s)]  # noqa: E731
    assert names("Points") == ["B", "A", "C", "D"]
    assert names("PPG") == ["B", "A", "C", "D"]
    assert names("Last 3 games") == ["C", "B", "A", "D"]
    assert names("This week") == ["C", "A", "B", "D"]
    assert names("Value per $") == ["C", "B", "A", "D"]
    assert names("Youngest")[0] == "D"
