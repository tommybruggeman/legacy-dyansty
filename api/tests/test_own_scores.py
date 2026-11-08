from api import own_league, sleeper

SCORING = {"pass_yd": 0.04, "rec": 1.0, "rec_yd": 0.1}


def rows():
    return [{"roster_id": 1, "matchup_id": 1, "starters": ["a", "b"], "players": ["a", "b", "c"],
             "starters_points": [9.9, 9.9], "players_points": {"a": 9.9, "b": 9.9, "c": 1.0}, "points": 19.8}]


def test_every_week_is_scored_from_our_lines_and_gaps_keep_sleeper():
    lines = {1: {"a": {"stats": {"pass_yd": 250}, "pos": "QB"}}, 2: {}}
    out = own_league.own_scores(2026, 3, SCORING, rows_for=lambda w: rows() if w != 3 else [],
                                lines_for=lambda w: lines.get(w, {}))
    assert list(out) == ["matchups/1"]  # week 2 has no lines, week 3 no rows: both stay Sleeper's
    row = out["matchups/1"][0]
    assert row["starters_points"] == [10.0, 9.9]  # a from our line, b keeps Sleeper's number
    assert row["points"] == 19.9


def test_a_failing_week_is_skipped_and_no_scoring_means_nothing():
    def boom(w):
        raise RuntimeError
    assert own_league.own_scores(2026, 2, SCORING, rows_for=boom, lines_for=lambda w: {}) == {}
    assert own_league.own_scores(2026, 2, {}, rows_for=lambda w: rows(), lines_for=lambda w: {"a": {}}) == {}


def test_put_many_swaps_the_whole_set():
    sleeper._overrides.clear()
    sleeper.put_many("league/9/", {"matchups/1": [1], "matchups/2": [2]})
    sleeper.put_many("league/9/", {"matchups/2": [3]})
    assert sleeper.get("league/9/matchups/2") == [3]
    assert f"{sleeper.BASE}/league/9/matchups/1" not in sleeper._overrides
    sleeper._overrides.clear()
