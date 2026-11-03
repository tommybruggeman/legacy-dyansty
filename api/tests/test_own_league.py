from api import own_league, sleeper

SCORING = {"rec": 1.0, "rec_yd": 0.1, "pass_td": 4.0}
SLOTS = ["QB", "WR"]


def rosters():
    return [{"roster_id": r, "players": [f"q{r}", f"w{r}", f"x{r}"], "taxi": [f"x{r}"], "reserve": []} for r in (1, 2, 3, 4, 5)]


def lines(week):
    # roster r's WR catches r passes for 10*r yards (week 16: 6-r)
    out = {}
    for r in (1, 2, 3, 4, 5):
        n = r if week < 16 else 6 - r
        out[f"w{r}"] = {"stats": {"rec": n, "rec_yd": 10 * n}, "pos": "WR"}
        out[f"q{r}"] = {"stats": {"pass_td": 1}, "pos": "QB"}
        out[f"x{r}"] = {"stats": {"rec": 50}, "pos": "WR"}  # on taxi: never starts
    return out


def run(current, saved=(), **extra):
    return own_league.build(
        season=2026, own_from=15, current_week=current,
        league={"settings": {"playoff_week_start": 15, "playoff_teams": 4, "playoff_round_type": 0}},
        rosters=rosters(), team_of_roster={r: f"t{r}" for r in (1, 2, 3, 4, 5)}, saved=list(saved), scoring=SCORING, slots=SLOTS,
        sleeper_rows=lambda w: [{"roster_id": r, "matchup_id": 9, "starters": [f"q{r}", f"w{r}"]} for r in (1, 2, 3, 4, 5)],
        lines_for=lines, standings_for=lambda own: [1, 2, 3, 4, 5], **extra,
    )


def test_round_weeks():
    assert own_league.round_weeks(15, 2, 0) == {1: [15], 2: [16]}
    assert own_league.round_weeks(15, 2, 1) == {1: [15], 2: [16, 17]}
    assert own_league.round_weeks(14, 2, 2) == {1: [14, 15], 2: [16, 17]}


def test_semifinal_week_pairs_seeds_and_scores():
    res = run(15)
    rows = {r["roster_id"]: r for r in res["weeks"][15]}
    assert rows[1]["matchup_id"] == rows[4]["matchup_id"] == 1
    assert rows[2]["matchup_id"] == rows[3]["matchup_id"] == 2
    assert rows[5]["matchup_id"] is None  # missed the playoffs
    assert rows[4]["starters"] == ["q4", "w4"] and rows[4]["points"] == 4 + 4 + 4.0
    semis = [g for g in res["bracket"] if g["r"] == 1]
    assert all(g["w"] is None for g in semis)  # week 15 still in progress


def test_final_uses_semifinal_winners_and_app_lineups():
    # roster 1 benches its WR in week 16 (an empty slot)
    saved = [{"league_team_id": "t1", "week": 16, "slots": [{"slot": "QB", "player_id": "q1"}, {"slot": "WR", "player_id": None}]}]
    res = run(17, saved)
    final = next(g for g in res["bracket"] if g.get("p") == 1)
    third = next(g for g in res["bracket"] if g.get("p") == 3)
    assert (final["t1"], final["t2"]) == (4, 3)  # week 15: 4 beat 1, 3 beat 2
    assert final["w"] == 3  # week 16 flips the order: roster 3 catches 3 passes, roster 4 only 2
    rows16 = {r["roster_id"]: r for r in res["weeks"][16]}
    assert rows16[1]["starters"] == ["q1", "0"] and rows16[1]["points"] == 4.0
    assert {third["t1"], third["t2"]} == {1, 2}


def test_overrides_replace_and_drop():
    sleeper.put("league/L/matchups/15", [{"roster_id": 1}])
    assert sleeper.get("league/L/matchups/15") == [{"roster_id": 1}]
    sleeper.drop("league/L/")
    assert "league/L/matchups/15" not in str(list(sleeper._overrides))


def test_commissioner_team_fix_changes_the_winner():
    # week 15 without fixes: roster 4 (12 pts) beats roster 1 (6 pts); +10 for roster 1 flips it
    res = run(16, fixes=[{"week": 15, "league_team_id": "t1", "player_id": "", "points": 10}])
    rows = {r["roster_id"]: r for r in res["weeks"][15]}
    assert rows[1]["points"] == 16.0 and rows[1]["adjustment"] == 10.0
    final = next(g for g in res["bracket"] if g.get("p") == 1)
    assert 1 in (final["t1"], final["t2"])
    assert {r["roster_id"]: r["points"] for r in res["unfixed"][15]}[1] == 6.0  # what finalizing saves


def test_commissioner_player_fix_sets_points():
    res = run(15, fixes=[{"week": 15, "league_team_id": "t4", "player_id": "w4", "points": 0}])
    row = next(r for r in res["weeks"][15] if r["roster_id"] == 4)
    assert row["players_points"]["w4"] == 0 and row["starters_points"][1] == 0 and row["points"] == 4.0
    assert row["fixed_players"] == ["w4"]


def test_finalized_week_serves_saved_rows():
    frozen = [dict(r, points=99.0 if r["roster_id"] == 5 else r["points"]) for r in run(15)["weeks"][15]]
    res = run(16, finals={15: frozen})
    assert {r["roster_id"]: r["points"] for r in res["weeks"][15]}[5] == 99.0
    # fixes still apply on top of a finalized week
    res = run(16, finals={15: frozen}, fixes=[{"week": 15, "league_team_id": "t5", "player_id": "", "points": -9}])
    assert {r["roster_id"]: r["points"] for r in res["weeks"][15]}[5] == 14 - 9  # recomputed from the saved starters (4 + 5 + 5)
