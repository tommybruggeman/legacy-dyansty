from datetime import datetime, timedelta, timezone

from api import trade_offers as to

ME, THEM = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
STATE = {
    "roster": [
        {"league_team_id": ME, "contract_agreement_id": "c1", "sleeper_player_id": "100", "contract_cap_hit": 18, "contract_years_left": 3},
        {"league_team_id": THEM, "contract_agreement_id": "c2", "sleeper_player_id": "200", "cap_hit": 31, "contract_years_left": 1},
        {"league_team_id": THEM, "contract_agreement_id": None, "sleeper_player_id": "300"},
    ],
    "draft_picks": [
        {"stable_pick_id": "p1", "current_owner_league_team_id": ME, "original_league_team_id": THEM, "draft_year": 2027, "round_number": 1, "asset_status": "tradable"},
        {"stable_pick_id": "p2", "current_owner_league_team_id": ME, "original_league_team_id": ME, "draft_year": 2026, "round_number": 2, "asset_status": "consumed"},
    ],
}
INFO = {"100": {"full_name": "Justin Jefferson", "position": "WR", "team": "MIN"}, "200": {"full_name": "A.J. Brown", "position": "WR", "team": "PHI"}}
OWNERS = {ME: "Tommy Bruggeman", THEM: "Grady M"}


def test_side_lists_contracts_and_tradable_picks():
    price = to.pick_pricer({(2027, 1): 3000.0})
    mine = to.side(STATE, ME, INFO, {"100": 17.2}, {"100": 7000.0}, price, OWNERS)
    assert [p["name"] for p in mine["players"]] == ["Justin Jefferson"]
    assert mine["players"][0] == {"player_id": "100", "contract_id": "c1", "name": "Justin Jefferson", "pos": "WR", "nfl_team": "MIN",
                                  "salary": 18.0, "years": 3, "ppg": 17.2, "value": 7000}
    assert mine["picks"] == [{"pick_id": "p1", "label": "2027 R1 (Grady's)", "year": 2027, "round": 1, "value": 3000}]
    theirs = to.side(STATE, THEM, INFO, {}, {}, price, OWNERS)
    assert [p["player_id"] for p in theirs["players"]] == ["200"]  # no contract row = not tradable here


def test_pick_pricer_falls_back_to_round_average():
    price = to.pick_pricer({(2027, 1): 3000.0, (2028, 1): 2000.0})
    assert price(2029, 1) == 2500.0 and price(2027, 3) == 0.0


def test_grade_and_read():
    assert to.grade(100, 100)["lean"] == "fair"
    g = to.grade(46, 54)
    assert g == {"share": 0.54, "lean": "fair", "pct": 8}
    g = to.grade(40, 60)
    assert g["lean"] == "you" and g["pct"] == 20
    assert "20% in your favor" in to.read_text(g, "Grady M")
    assert "Grady's favor" in to.read_text(to.grade(60, 40), "Grady M")
    assert to.grade(0, 0)["share"] is None


def test_offer_items_marks_directions():
    items = to.offer_items(ME, THEM, [{"contract_id": "c1", "player_id": "100"}], [{"pick_id": "p9", "label": "2027 R1"}])
    assert items[0] == {"from_league_team_id": ME, "to_league_team_id": THEM, "contract_id": "c1", "player_id": "100", "name": None}
    assert items[1]["draft_pick_id"] == "p9" and items[1]["from_league_team_id"] == THEM


def test_center_groups_offers_and_results():
    now = datetime(2027, 9, 10, tzinfo=timezone.utc)
    rows = [
        {"id": "o1", "status": "pending", "from_league_team_id": THEM, "to_league_team_id": ME, "created_at": "2027-09-09",
         "players": [{"contract_id": "c2", "player_id": "200", "from_league_team_id": THEM, "to_league_team_id": ME},
                     {"contract_id": "c1", "player_id": "100", "from_league_team_id": ME, "to_league_team_id": THEM}], "picks": []},
        {"id": "o2", "status": "pending", "from_league_team_id": ME, "to_league_team_id": THEM, "created_at": "2027-09-08", "players": [], "picks": []},
        {"id": "o3", "status": "declined", "from_league_team_id": ME, "to_league_team_id": THEM, "decided_at": (now - timedelta(days=1)).isoformat(), "players": [], "picks": []},
        {"id": "o4", "status": "declined", "from_league_team_id": THEM, "to_league_team_id": ME, "decided_at": now.isoformat(), "players": [], "picks": []},
        {"id": "o5", "status": "accepted", "from_league_team_id": ME, "to_league_team_id": THEM, "decided_at": (now - timedelta(days=9)).isoformat(), "players": [], "picks": []},
    ]
    c = to.center(rows, ME, OWNERS, INFO, now=now)
    assert c["pending"] == 2
    assert [o["id"] for o in c["needs_answer"]] == ["o1"] and [o["id"] for o in c["sent"]] == ["o2"]
    o1 = c["needs_answer"][0]
    assert o1["partner"] == "Grady M" and [p["name"] for p in o1["you_get"]] == ["A.J. Brown"] and [p["name"] for p in o1["you_give"]] == ["Justin Jefferson"]
    assert [r["id"] for r in c["results"]] == ["o3"]  # o4 you declined yourself; o5 is older than a week
    assert to.result_line(c["results"][0]) == "Grady M declined your trade"


def test_friendly_errors():
    assert "already played" in to.friendly(Exception("ERROR: offer_player_locked"))
    assert to.friendly(Exception("boom")) == "The trade wasn't saved. Try again."
