from api import own_league, playoffs

RULES = playoffs.rules_of({"playoff_teams": 4, "playoff_week_start": 15, "playoff_round_weeks": 2, "loser_bracket": True})
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


def view(week, current, scored, me=2, rules=RULES):
    return playoffs.build(week=week, rules=rules, standings=STANDINGS, names=NAMES, rows_for=rows,
                          last_scored=scored, current_week=current, my_roster_id=me)


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


def test_format_is_ours_from_settings():
    one_week = playoffs.rules_of({"playoff_teams": 4, "playoff_week_start": 15, "playoff_round_weeks": 1, "loser_bracket": False})
    assert playoffs.last_week(one_week) == 16 and playoffs.last_week(RULES) == 18
    v = view(16, 9, 8, rules=one_week)
    assert v["round_name"] == "Championship" and v["legs"] == 1 and v["losers"] == [] and v["byes"] == []


def test_settings_object_defaults():
    from league_ai.settings_store import LeagueAISettings
    r = playoffs.rules_of(LeagueAISettings(league_id="x", playoff_teams=4))
    assert r == {"playoff_teams": 4, "playoff_week_start": 15, "playoff_round_weeks": 2, "loser_bracket": True}


def test_legacy_cards_for_old_app_builds():
    cards = playoffs.legacy_games(view(15, 15, 14))
    assert len(cards) == 4 and all(len(c["teams"]) == 2 for c in cards)


def test_own_league_loser_layout_is_trimmed_to_playoff_rounds():
    assert max(g[0] for g in own_league.loser_layout(6, 2)) == 2
    assert own_league.loser_layout(3, 2) == []


def test_home_bracket_and_my_game_from_our_view():
    from api import box_score, home
    v = view(16, 16, 15)  # semis, week 2 of 2: week 15 is banked
    rounds = home.bracket_from_view(v)
    assert [(r["label"], r["weeks"], len(r["games"])) for r in rounds] == [("Semifinal", [15, 16], 2), ("Championship", [17, 18], 1)]
    semi = next(g for g in rounds[0]["games"] if g["a"]["roster_id"] == 1)
    assert semi["a"]["score"] == 99 + 50 and semi["placeholders"] == [None, None]
    final = rounds[1]["games"][0]
    assert final["a"] is None and final["placeholders"] == ["Winner of 1 v 4", "Winner of 2 v 3"]
    g = home.playoff_game(v)
    assert (g["leg"], g["legs"], g["label"]) == (2, 2, "Semifinal")
    assert (g["me"]["roster_id"], g["me"]["carry"], g["them"]["roster_id"], g["them"]["carry"]) == (2, 98, 3, 97)
    box = {"is_final": False, "teams": [{"roster_id": 2, "score": 10, "projected": 90, "players": []}, {"roster_id": 3, "score": 20, "projected": 95, "players": []}]}
    box_score.add_round(box, {2: 98, 3: 97})
    assert box["round"]["projected"] == [188, 192] and box["round"]["score"] == [108, 117]
