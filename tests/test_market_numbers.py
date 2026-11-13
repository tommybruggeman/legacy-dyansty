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
