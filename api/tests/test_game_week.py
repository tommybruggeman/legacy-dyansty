from datetime import date

from api import game_week, league


def _games(week, days, scores=True):
    teams = [("BUF", "MIA"), ("KC", "DEN"), ("LA", "SF")]
    return [{"week": week, "gameday": d, "home_team": h, "away_team": a,
             "home_score": 20 if scores else None, "away_score": 17 if scores else None}
            for (h, a), d in zip(teams, days)]


def test_week_flips_when_last_game_ends():
    sched = _games(11, ["2026-11-15"] * 3) + _games(12, ["2026-11-19", "2026-11-22", "2026-11-23"], scores=False) \
        + _games(13, ["2026-11-29"] * 3, scores=False)
    today = date(2026, 11, 24)
    # Monday night still on: week 12 is current, 11 is the newest final week.
    over = {12: {"BUF", "MIA", "KC", "DEN"}}
    assert game_week.decide(sched, over, set(), today) == {"current": 12, "last_final": 11, "final": [11]}
    # The watcher saw Monday night end (ESPN codes LA -> LAR normalize on both sides): week 12 is final.
    over[12] |= {"LAR", "SF"}
    assert game_week.decide(sched, over, set(), today) == {"current": 13, "last_final": 12, "final": [11, 12]}


def test_nflverse_scores_or_saved_official_lines_also_close_a_week():
    sched = _games(12, ["2026-11-23"] * 3, scores=False) + _games(13, ["2026-11-29"] * 3, scores=False)
    assert game_week.decide(sched, {}, {12}, date(2026, 11, 24))["current"] == 13
    sched = _games(12, ["2026-11-23"] * 3) + _games(13, ["2026-11-29"] * 3, scores=False)
    assert game_week.decide(sched, {}, set(), date(2026, 11, 24))["last_final"] == 12


def test_stuck_game_stops_holding_the_week_after_three_days():
    sched = _games(12, ["2026-11-23"] * 3, scores=False) + _games(13, ["2026-11-29"] * 3, scores=False)
    assert game_week.decide(sched, {}, set(), date(2026, 11, 26))["current"] == 12
    assert game_week.decide(sched, {}, set(), date(2026, 11, 27))["current"] == 13


def test_season_over_and_no_schedule():
    sched = _games(18, ["2027-01-10"] * 3)
    assert game_week.decide(sched, {}, set(), date(2027, 1, 12)) == {"current": 18, "last_final": 18, "final": [18]}
    assert game_week.decide([], {}, set(), date(2027, 1, 12)) is None
    assert game_week.season_of(date(2027, 1, 12)) == 2026


def test_league_weeks_use_ours_and_fall_back_to_sleeper(monkeypatch):
    monkeypatch.setattr(game_week, "state", lambda *a, **k: {"current": 13, "last_final": 12, "final": []})
    assert league.current_week() == 13 and league.last_scored_week("S") == 12
    monkeypatch.setattr(game_week, "state", lambda *a, **k: None)
    monkeypatch.setattr(league.sleeper, "get", lambda path, ttl=0: {"state/nfl": {"week": 9}, "league/S": {"settings": {"last_scored_leg": 7}}}.get(path))
    assert league.current_week() == 9 and league.last_scored_week("S") == 7
