from api import own_league, playoffs

LEAGUE = {"settings": {"playoff_week_start": 15, "playoff_teams": 4, "playoff_round_type": 2, "num_teams": 10}}
STANDINGS = [{"roster_id": r, "wins": 14 - r, "losses": r, "ties": 0} for r in range(1, 11)]
NAMES = {r: f"Owner {r}" for r in range(1, 11)}


def rows(week):
    # Sleeper's made-up pairings (1 v 2, 3 v 4, ...) and points: lower roster ids score more, except week 16 flips 1 and 4.
    out = []
    for r in range(1, 11):
        pts = 100 - r
        if week == 16 and r in (1, 4):
            pts = {1: 50, 4: 150}[r]
        out.append({"roster_id": r, "matchup_id": (r + 1) // 2, "points": pts})
    return out


def view(week, current, scored, winners=None, losers=None, me=2):
    return playoffs.build(week=week, league=LEAGUE, standings=STANDINGS, names=NAMES, rows_for=rows,
                          winners=winners or [], losers=losers or [], last_scored=scored, current_week=current, my_roster_id=me)


def test_regular_season_week_is_not_a_playoff_week():
    assert view(14, 14, 13) is None


def test_projected_semis_use_our_seeds_not_sleeper_pairings():
    v = view(15, 9, 8)
    assert v["projected"] and v["round_name"] == "Semifinal" and (v["leg"], v["legs"]) == (1, 2)
    pairs = [tuple(t["roster_id"] for t in g["teams"]) for g in v["games"]]
    assert pairs == [(2, 3), (1, 4)]  # my game first
    assert all(g["state"] == "pre" and g["matchup_id"] is None for g in v["games"])
    assert [tuple(t.get("roster_id") for t in g["teams"]) for g in v["losers"]] == [(7, 10), (8, 9)]
    assert [b["roster_id"] for b in v["byes"]] == [5, 6]
    final = next(g for g in v["tree"] if g["place"] == 1)
    assert final["teams"][0]["placeholder"] == "Winner of 1 v 4" and final["weeks"] == [17, 18]


def test_two_week_rounds_add_up_and_name_the_winner():
    v = view(17, 17, 16)
    semis = {g["m"]: g for g in v["tree"] if g["round"] == 1}
    assert semis[1]["state"] == "final" and semis[1]["winner"] == 4  # 99+50 vs 96+150
    assert [t["legs"] for t in semis[1]["teams"]] == [[99, 50], [96, 150]]
    final = next(g for g in v["games"] if g["place"] == 1)
    assert [t["roster_id"] for t in final["teams"]] == [4, 2] and final["label"] == "Championship"
    assert v["games"][0] is final  # the title game always comes first
    assert final["state"] == "live"
    loser_r2 = [tuple(t.get("roster_id") for t in g["teams"]) for g in v["losers"]]
    assert loser_r2 == [(5, 8), (6, 7)]


def test_sleeper_brackets_win_once_filled():
    winners = [{"r": 1, "m": 1, "t1": 1, "t2": 3}, {"r": 1, "m": 2, "t1": 2, "t2": 4},
               {"r": 2, "m": 3, "t1": None, "t2": None, "t1_from": {"w": 1}, "t2_from": {"w": 2}, "p": 1}]
    v = view(15, 15, 14, winners=winners)
    assert {tuple(t["roster_id"] for t in g["teams"]) for g in v["games"]} == {(1, 3), (2, 4)}
    # both teams share Sleeper's matchup id 1 in week 15 only for 1 v 2, so 1 v 3 has no box score link
    assert all(g["matchup_id"] is None for g in v["games"])


def test_legacy_cards_for_old_app_builds():
    cards = playoffs.legacy_games(view(15, 15, 14))
    assert len(cards) == 4 and all(len(c["teams"]) == 2 for c in cards)


def test_own_league_loser_layout_is_trimmed_to_playoff_rounds():
    assert max(g[0] for g in own_league.loser_layout(6, 2)) == 2
    assert own_league.loser_layout(3, 2) == []
