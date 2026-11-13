from league_ai import projection_calibration as pc
from scoring.engine import player_points


def _week(season, week, rows, lineups=None, snapshot=None):
    return {"season": season, "week": week, "rows": rows, "lineups": lineups or [], "snapshot": snapshot}


def _row(pid, pos, proj, actual):
    return {"id": pid, "pos": pos, "raw": proj, "shown": proj, "actual": actual}


def test_factor_leans_on_last_season_until_this_one_has_weight():
    assert pc.factor(0, 0, carry=0.8) == 0.8
    assert round(pc.factor(800, 1000, carry=1.0, prior=300), 3) == round(1100 / 1300, 3)
    assert pc.factor(0, 1000, carry=1.0) == pc.FACTOR_RANGE[0]   # clamped


def test_replay_learns_only_from_finished_weeks_and_carries_over_seasons():
    weeks = [_week(2025, w, [_row("a", "RB", 20, 16), _row("b", "WR", 10, 10)]) for w in range(1, 11)]
    weeks.append(_week(2026, 1, [_row("a", "RB", 20, 16), _row("b", "WR", 10, 10)], lineups=[["a", "b"]], snapshot={"a": 17.0}))
    out = pc.replay(weeks)
    assert out["season"] == 2026 and out["through_week"] == 1
    assert out["last_season_ratio"]["RB"] == 0.8 and out["last_season_ratio"]["WR"] == 1.0
    w1 = out["by_week"][0]
    # week 1 of 2026 was projected with last season's RB ratio already in: 20 x 0.8 = 16, a perfect call
    assert w1["shown"]["bias"] == -2.0 and w1["calibrated"]["mae"] == 0.0
    assert w1["team_shown"]["bias"] == -4.0 and w1["team_calibrated"]["bias"] == 0.0
    assert w1["as_shown"]["bias"] == -1.0
    assert out["factors"]["RB"] == 0.8 and out["factors"]["WR"] == 1.0


def test_replay_ignores_players_not_really_projected_and_unknown_scores():
    out = pc.replay([_week(2026, 1, [_row("a", "RB", 2, 10), {**_row("b", "RB", 15, 0), "actual": None}])])
    assert out["by_week"] == [] and out["factors"]["RB"] == 1.0


def test_projected_kicker_long_field_goals_count_in_split_buckets():
    rules = {"fgm_40_49": 4, "fgm_50_59": 5, "fgm_60p": 6, "fgmiss": -1, "xpm": 1}
    assert player_points({"fgm_40_49": 0.5, "fgm_50p": 0.4, "xpm": 2.5}, rules, "K") == round(2 + 2 + 2.5, 2)
    # only fgm and the short buckets: the rest of the makes are 50+; misses from attempts
    assert player_points({"fgm": 2.0, "fga": 2.4, "fgm_40_49": 1.5, "xpm": 2}, rules, "K") == round(6 + 2.5 - 0.4 + 2, 2)
    # a real line with its own buckets is scored as given
    assert player_points({"fgm": 2, "fgm_40_49": 1, "fgm_50_59": 1, "fgmiss": 1}, rules, "K") == 8.0


def test_front_office_week_number_comes_from_the_projection_when_there_is_one():
    from league_ai.front_office import use_projections
    from league_ai.power import PlayerStrength

    a = PlayerStrength("1", "A", "RB", "t", "o", now=14.0, dynasty=0, available=True, week_now=15.0, spread=6.0)
    b = PlayerStrength("2", "B", "WR", "t", "o", now=9.0, dynasty=0, available=True, week_now=9.5)
    use_projections([a, b], {"1": 16.0})
    assert a.week_now == 16.0 and a.week_sd == round(2 * 4 + 0.4, 2)
    assert b.week_now == 9.5 and b.week_sd is None   # no projection: keeps the form number


def test_line_learns_that_big_projections_run_high_and_keeps_the_order():
    line = pc.Line()
    for x in range(4, 25):
        line.add(float(x), 2.0 + 0.75 * x)
    a, b = line.fit(pc.Line(line.s))   # last season said the same
    assert abs(a - 2.0) < 0.01 and abs(b - 0.75) < 0.01
    assert pc.line_points(20, (a, b)) < 20 and pc.line_points(4, (a, b)) > 4
    assert abs(pc.line_points(1.5, (a, b)) - 1.5 * (a + 3 * b) / 3) < 1e-9   # under 3 pts: scaled, no jump
    xs = [pc.line_points(x / 2, (a, b)) for x in range(0, 60)]
    assert xs == sorted(xs)   # higher projection, higher number
    assert pc.Line().fit() == (0.0, 1.0)   # nothing learned yet: projections as they are


def test_replay_saves_lines_and_uses_them_for_skill_positions():
    weeks = [_week(2025, w, [_row("a", "RB", 20, 14), _row("b", "RB", 6, 6), _row("k", "K", 8, 8)]) for w in range(1, 11)]
    out = pc.replay(weeks)
    a, b = out["lines"]["RB"]
    assert b < 1 and pc.line_points(20, (a, b)) < 20
    assert pc.calibrate(20, "RB", out["factors"], out["lines"]) == pc.line_points(20, out["lines"]["RB"])
    assert pc.calibrate(8, "K", {"K": 1.1}, out["lines"]) == 8 * 1.1   # kickers keep the position factor


def test_played_lines_leave_out_players_who_never_got_in():
    rows = lambda w: [{**_row("a", "WR", 15, 15), "played": True}, {**_row("b", "WR", 10, 10), "played": True},
                      {**_row("c", "WR", 15, 0), "played": False}]   # a scratch: 0 points
    out = pc.replay([_week(2026, w, rows(w)) for w in range(1, 11)])
    all_line, played = out["lines"]["WR"], out["lines_played"]["WR"]
    assert pc.line_points(15, played) > pc.line_points(15, all_line)
    assert abs(pc.line_points(15, played) - 15) < 1.0


def test_pregame_snapshot_skips_when_no_kickoff_is_near(monkeypatch):
    from datetime import datetime, timezone

    import league_ai.nfl_data as nd

    games = [{"week": 9, "gameday": "2026-11-08", "gametime": "13:00", "home_team": "BUF", "away_team": "MIA", "home_score": None}]
    monkeypatch.setattr(nd, "season_games", lambda client, season: games)
    called = []
    monkeypatch.setattr(pc, "snapshot", lambda *a: called.append(a) or 5)

    class Q:
        def table(self, *_):
            return self
        def select(self, *_):
            return self
        def execute(self):
            return type("R", (), {"data": [{"id": "L", "sleeper_league_id": "S"}]})()

    assert pc.pregame_snapshots(Q(), datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)) == {}   # 1 PM ET is 6 h away
    assert pc.pregame_snapshots(Q(), datetime(2026, 11, 8, 17, 30, tzinfo=timezone.utc)) == {"league 1": 5}
    assert len(called) == 1
