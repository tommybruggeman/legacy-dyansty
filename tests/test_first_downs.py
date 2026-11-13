from league_ai.first_downs import DEFAULT, FirstDowns, adjust_with_card


def test_rates_lean_on_position_then_on_the_player():
    fd = FirstDowns()
    assert fd.rates("x", "WR") == DEFAULT["WR"]
    for _ in range(20):
        fd.add("a", "WR", {"rec": 5, "rec_fd": 1})       # 0.2 first downs per catch
    r_a, _ = fd.rates("a", "WR")
    r_new, _ = fd.rates("new", "WR")
    assert r_a < r_new < DEFAULT["WR"][0]                # the player moves most, the position some


def test_adjust_replaces_projected_first_downs():
    fd = FirstDowns()
    out = fd.adjust("a", "RB", {"rec": 4, "rush_att": 15, "rec_fd": 9, "rush_fd": 9, "rush_yd": 70})
    assert out["rec_fd"] == round(4 * DEFAULT["RB"][0], 3) and out["rush_fd"] == round(15 * DEFAULT["RB"][1], 3)
    assert out["rush_yd"] == 70
    assert fd.adjust("k", "K", {"fgm_30_39": 1}) == {"fgm_30_39": 1}


def test_card_round_trip():
    fd = FirstDowns()
    fd.add("a", "TE", {"rec": 6, "rec_fd": 3})
    card = fd.to_card()
    assert set(card["pos"]) == set(DEFAULT) and "a" in card["players"]
    out = adjust_with_card(card, "a", "TE", {"rec": 2, "rec_fd": 5})
    assert out["rec_fd"] == round(2 * card["players"]["a"][0], 3)
    assert adjust_with_card({}, "a", "TE", {"rec_fd": 5}) == {"rec_fd": 5}
