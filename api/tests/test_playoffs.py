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
    # Loser bracket: week 15 7v10 and 8v9, week 16 5 v 8 and 6 v 7 (and 10 v 9 starts the 9th place game), week 17 5th/7th.
    week16 = [tuple(t.get("roster_id") for t in g["teams"]) for g in view(16, 16, 15)["losers"]]
    assert week16 == [(5, 8), (6, 7), (10, 9)]
    week17 = [(g["label"], tuple(t.get("roster_id") for t in g["teams"])) for g in v["losers"]]
    assert week17 == [("5th place", (5, 6)), ("7th place", (8, 7)), ("9th place", (10, 9))]
    # The week-15 losers play one two-week 9th place game (weeks 16-17), scored over both weeks.
    ninth = next(g for g in v["losers"] if g["label"] == "9th place")
    assert ninth["weeks"] == [16, 17] and all(len(t["legs"]) == 2 for t in ninth["teams"])
    assert next(g for g in view(16, 16, 15)["losers"] if g["teams"][0].get("roster_id") == 10)["label"] == "9th place"


def test_format_is_ours_from_settings():
    one_week = playoffs.rules_of({"playoff_teams": 4, "playoff_week_start": 15, "playoff_round_weeks": 1, "loser_bracket": False})
    assert playoffs.last_week(one_week) == 16 and playoffs.last_week(RULES) == 18
    v = view(16, 9, 8, rules=one_week)
    assert v["round_name"] == "Championship" and v["legs"] == 1 and v["losers"] == [] and v["byes"] == []


def test_two_week_final_ends_week_17():
    # Legacy (Tommy, 2026-11-18): semis week 15, title game and 3rd place weeks 16-17, loser bracket weeks 15-17.
    final_two = playoffs.rules_of({"playoff_teams": 4, "playoff_week_start": 15, "playoff_round_weeks": 3, "loser_bracket": True})
    cfg = playoffs.settings_of(final_two, league_size=10)
    assert cfg["weeks"] == {1: [15], 2: [16, 17]} and cfg["last_week"] == 17
    semis = view(15, 15, 14, rules=final_two)
    assert semis["round_name"] == "Semifinal" and semis["legs"] == 1
    title = view(17, 17, 16, rules=final_two)
    assert title["round_name"] == "Championship" and title["legs"] == 2 and title["leg"] == 2
    assert playoffs.round_type(final_two) == 1 and playoffs.ROUND_FORMATS[3] == "Two-week final only"


def test_settings_object_defaults():
    from league_ai.settings_store import LeagueAISettings
    r = playoffs.rules_of(LeagueAISettings(league_id="x", playoff_teams=4))
    assert r == {"playoff_teams": 4, "playoff_week_start": 15, "playoff_round_weeks": 2, "loser_bracket": True}


def test_legacy_cards_for_old_app_builds():
    cards = playoffs.legacy_games(view(15, 15, 14))
    assert len(cards) == 4 and all(len(c["teams"]) == 2 for c in cards)


def test_loser_bracket_for_teams_5_to_10_runs_three_one_week_rounds():
    assert own_league.loser_weeks(15, 6) == {1: [15], 2: [16], 3: [17]}
    assert own_league.loser_layout(3) == []
    places = sorted(g[4] for g in own_league.loser_layout(6) if g[4])
    assert places == [1, 3, 5]  # 5th, 7th and 9th place games


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


def test_over_cap_team_loses_its_playoff_round():
    # Week 15 semis: seed 1 outscores seed 4 over both weeks, but started week 15 over the cap.
    weeks = {1: [15, 16], 2: [17, 18]}
    points = {(15, 1): 120, (16, 1): 120, (15, 4): 100, (16, 4): 100, (15, 2): 110, (16, 2): 110, (15, 3): 90, (16, 3): 90}
    games = own_league.build_bracket([1, 2, 3, 4], points, lambda r: r == 1, weeks, auto_loss={(15, 1)})
    semi = next(g for g in games if {g["t1"], g["t2"]} == {1, 4})
    assert semi["w"] == 4 and semi["l"] == 1


def test_mark_auto_losses_flags_only_that_team():
    rows = [{"roster_id": 1, "points": 120, "matchup_id": 1}, {"roster_id": 2, "points": 90, "matchup_id": 1}]
    out = own_league.mark_auto_losses(rows, {1})
    assert out[0]["auto_loss"] is True and "auto_loss" not in out[1] and "auto_loss" not in rows[0]


def test_consolation_games_say_what_they_decide():
    week15 = {tuple(t.get("roster_id") for t in g["teams"]): g["stakes"] for g in view(15, 15, 14)["losers"]}
    assert week15[(7, 10)] == {"win": "Win: play for 5th–8th", "lose": "Lose: 9th place game"}
    week16 = view(16, 16, 15)["losers"]
    assert week16[0]["stakes"] == {"win": "Win: 5th place game", "lose": "Lose: 7th place game"}
    assert next(g for g in week16 if g["label"] == "9th place")["stakes"] is None


def test_projections_add_banked_weeks_and_byes():
    proj = {rid: {"projected": 100.0, "kickoff": 90.0} for rid in range(1, 11)}
    v = view(18, 18, 17)  # week 2 of the two-week final (weeks 17-18 in this fixture)
    playoffs.add_projections(v, proj)
    t = next(g for g in v["games"] if g["place"] == 1)["teams"][0]
    assert t["proj"] == round(t["legs"][0] + 100.0, 1)  # week 17 points banked + week 18 projection
    v16 = view(16, 16, 15)
    playoffs.add_projections(v16, proj)
    ninth = next(g for g in v16["losers"] if g["label"] == "9th place")
    assert all(t["proj"] == 100.0 for t in ninth["teams"])


def test_bye_card_names_my_bye_partner_and_next_game():
    from api import home
    v = view(15, 15, 14, me=5)
    playoffs.add_projections(v, {rid: {"projected": 110.0, "kickoff": 100.0} for rid in range(1, 11)})
    bye = home.playoff_bye(v, 5)
    assert bye["me"]["roster_id"] == 5 and bye["them"]["roster_id"] == 6 and bye["me"]["proj"] == 110.0
    assert bye["next_week"] == 16 and bye["next_vs"] is None
    assert bye["feeder"] == {"kind": "w", "names": ["Owner 8", "Owner 9"]}  # 5 plays the 8/9 winner
    assert home.playoff_bye(v, 2) is None


def test_champion_once_the_title_game_is_final():
    from api import home
    assert home.champion(view(17, 17, 16), 4) is None
    done = view(18, 18, 18)
    champ = home.champion(done, 4)
    assert champ and champ["is_me"] == (champ["roster_id"] == 4) and champ["opponent"]["roster_id"] != champ["roster_id"]
