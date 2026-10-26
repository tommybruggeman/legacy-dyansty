from services.draft_board import build_slots, draft_order, on_the_clock, short_names
from services.fa_auction import auction_rules, equivalent_bid, is_valid_bid

TEAMS = [
    {"id": "t1", "owner_name": "Tommy Bruggeman", "sleeper_roster_id": 1},
    {"id": "t5", "owner_name": "Grady Graham", "sleeper_roster_id": 5},
    {"id": "t9", "owner_name": "Kevin Wells", "sleeper_roster_id": 9},
]
STANDINGS = [
    {"sleeper_roster_id": 1, "Standing Points": 20, "PF": 1500, "Wins": 8, "Top 5": 4},
    {"sleeper_roster_id": 5, "Standing Points": 12, "PF": 1300, "Wins": 5, "Top 5": 2},
    {"sleeper_roster_id": 9, "Standing Points": 12, "PF": 1200, "Wins": 6, "Top 5": 2},
]


def terms(rnd, pick):
    return (10.0 - pick, 2, 25.0)


def test_order_is_worst_first_with_points_for_tiebreak():
    assert draft_order(STANDINGS, TEAMS) == ["t9", "t5", "t1"]


def test_team_missing_from_standings_goes_last():
    assert draft_order(STANDINGS[:2], TEAMS) == ["t5", "t1", "t9"]


def test_traded_pick_shows_new_owner_and_original():
    assets = [{"draft_year": 2027, "round_number": 1,
               "original_league_team_id": "t1", "current_owner_league_team_id": "t5"}]
    slots = build_slots(draft_year=2027, order=["t9", "t5", "t1"], teams=TEAMS,
                        pick_assets=assets, made_picks=[], players={}, terms=terms)
    first_round = [s for s in slots if s.round == 1]
    assert [s.owner_name for s in first_round] == ["Kevin", "Grady", "Grady"]
    assert first_round[2].traded and first_round[2].original_name == "Tommy"
    assert first_round[2].label == "1.03" and first_round[2].salary == 7.0
    # Other years' assets do not move this year's picks.
    assert slots[5].owner_name == "Tommy"


def test_on_the_clock_skips_made_picks():
    made = [{"draft_year": 2027, "draft_round": 1, "round_pick": 1,
             "player_id": "p1", "original_league_team_id": "t9"}]
    slots = build_slots(draft_year=2027, order=["t9", "t5", "t1"], teams=TEAMS,
                        pick_assets=[], made_picks=made,
                        players={"p1": {"player": "Jeremiah Smith", "position": "WR"}},
                        terms=terms)
    assert slots[0].player_name == "Jeremiah Smith"
    assert on_the_clock(slots).label == "1.02"
    assert len(slots) == 9 and slots[-1].overall == 9


def test_short_names_keep_full_name_on_clash():
    names = short_names([{"id": "a", "owner_name": "Chase One"}, {"id": "b", "owner_name": "Chase Two"},
                         {"id": "c", "owner_name": "Nick Salafia"}])
    assert names == {"a": "Chase One", "b": "Chase Two", "c": "Nick"}


def test_auction_math_matches_calculator():
    rules = auction_rules({})
    assert equivalent_bid(rules, 14, 2, 2) == 14
    assert equivalent_bid(rules, 14, 2, 1) == 16
    assert equivalent_bid(rules, 14, 2, 3) == 13
    assert equivalent_bid(rules, 14, 2, 4) is None
    assert is_valid_bid(rules, 14, 2)
    assert not is_valid_bid(rules, 10, 3)
    assert not is_valid_bid(rules, 0, 1)


def test_auction_rules_read_league_settings():
    rules = auction_rules({"min_3_year_bid": 15, "year_discount_pct": 20, "max_contract_years": 3})
    assert rules["minimums"][3] == 15 and rules["max_years"] == 3
    assert not is_valid_bid(rules, 30, 4)
    assert equivalent_bid(rules, 20, 3, 2) == 24
