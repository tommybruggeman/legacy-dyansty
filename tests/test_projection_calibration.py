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
