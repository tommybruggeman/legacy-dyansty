"""Move cards the GM proposes and the owner confirms (api.gm_moves)."""
from __future__ import annotations

import pytest

from api import gm_moves
from league_ai.service import _history_messages

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]
POS = {"q": "QB", "r1": "RB", "r2": "RB", "w1": "WR", "w2": "WR", "t": "TE", "fx": "RB", "w3": "WR", "t2": "TE", "taxi": "WR", "q2": "QB"}
NAMES = {"q": "Josh Allen", "r1": "Bijan Robinson", "r2": "Kyren Williams", "w1": "Jaylen Waddle", "w2": "Michael Pittman Jr.",
         "t": "Sam LaPorta", "fx": "James Cook", "w3": "Rome Odunze", "t2": "Tucker Kraft", "taxi": "Rookie Guy", "q2": "Backup QB"}
STARTERS = ["q", "r1", "r2", "w1", "w2", "t", "fx"]


def apply(changes, locked=(), starters=STARTERS):
    return gm_moves.apply_lineup(SLOTS, list(starters), changes, roster=POS.keys(), pos=POS, inactive={"taxi"}, locked=locked, names=NAMES)


def test_straight_swap_in_the_same_slot():
    assert apply([{"start": "w3", "bench": "w2"}]) == ["q", "r1", "r2", "w1", "w3", "t", "fx"]


def test_swap_slides_a_flex_player_over_when_needed():
    # a WR benching a RB: the WR takes the FLEX and the FLEX back moves into the RB slot
    assert apply([{"start": "w3", "bench": "r2"}]) == ["q", "r1", "fx", "w1", "w2", "t", "w3"]


def test_refuses_taxi_locked_and_bad_fits():
    with pytest.raises(gm_moves.MoveError, match="taxi or IR"):
        apply([{"start": "taxi", "bench": "w2"}])
    with pytest.raises(gm_moves.MoveError, match="locked"):
        apply([{"start": "w3", "bench": "w2"}], locked={"w2"})
    with pytest.raises(gm_moves.MoveError, match="doesn't fit"):
        apply([{"start": "q2", "bench": "w2"}])
    with pytest.raises(gm_moves.MoveError, match="already starting"):
        apply([{"start": "w1", "bench": "w2"}])


def test_fills_an_empty_slot():
    assert apply([{"start": "t2"}], starters=["q", "r1", "r2", "w1", "w2", None, "fx"])[5] == "t2"


def test_pick_one_by_name_part_and_ambiguity():
    assert gm_moves.pick_one("waddle", NAMES, "player") == "w1"
    assert gm_moves.pick_one("Michael Pittman Jr", NAMES, "player") == "w2"
    with pytest.raises(gm_moves.MoveError, match="more than one"):
        gm_moves.pick_one("Williams", {"a": "Kyren Williams", "b": "Javonte Williams"}, "player")
    with pytest.raises(gm_moves.MoveError, match="no player"):
        gm_moves.pick_one("Nobody", NAMES, "player")


STATE = {
    "roster": [
        {"league_team_id": "me", "contract_agreement_id": "c1", "sleeper_player_id": "w1", "player_name": "Jaylen Waddle"},
        {"league_team_id": "dy", "contract_agreement_id": "c2", "sleeper_player_id": "n1", "player_name": "Malik Nabers"},
    ],
    "draft_picks": [
        {"stable_pick_id": "p1", "current_owner_league_team_id": "me", "original_league_team_id": "me", "draft_year": 2027, "round_number": 1},
        {"stable_pick_id": "p2", "current_owner_league_team_id": "me", "original_league_team_id": "me", "draft_year": 2027, "round_number": 2},
    ],
}
TEAMS = [{"id": "me", "owner_name": "Tommy B"}, {"id": "dy", "owner_name": "Dylan R"}]


def test_build_offer_resolves_players_picks_and_cap():
    them, body, lines = gm_moves.build_offer(STATE, TEAMS, "me", to_owner="dylan", give=["Waddle"], get=["Nabers"], give_picks=["2027 2nd"],
                                             get_picks=[], give_cap=3, get_cap=0)
    assert them == "dy"
    assert [p.get("contract_id") or p.get("pick_id") for p in body["give"]] == ["c1", "p2"]
    assert body["get"][0]["contract_id"] == "c2" and body["give_cap"] == 3
    assert lines[0] == "You send: Jaylen Waddle, 2027 R2, $3 cap space" and lines[1] == "You get: Malik Nabers"


def test_build_offer_needs_both_sides():
    with pytest.raises(gm_moves.MoveError, match="each side"):
        gm_moves.build_offer(STATE, TEAMS, "me", to_owner="Dylan", give=["Waddle"], get=[], give_picks=[], get_picks=[], give_cap=0, get_cap=0)


def _data():
    return {"week": 12, "slots": SLOTS, "teams": [{"team_id": "me", "roster": list(POS), "starters": list(STARTERS)}],
            "names": NAMES, "info": {p: {"position": v} for p, v in POS.items()}, "designation": {"taxi": "taxi"}, "locked": [],
            "proj": {"w3": 14.2, "w2": 11.8}}


def test_lineup_tool_makes_a_card_and_replaces_a_repeat():
    tray = gm_moves.Tray()
    tool = gm_moves.lineup_tool(_data, "me", tray)
    out = tool.fn(changes=[{"start": "Odunze", "bench": "Pittman"}])
    assert out["card"]["title"] == "Set your Week 12 lineup" and out["card"]["status"] == "pending"
    assert out["card"]["lines"] == ["Start Rome Odunze at WR, 14.2 proj", "Bench Michael Pittman Jr., 11.8 proj"]
    tool.fn(changes=[{"start": "Odunze", "bench": "Pittman"}])
    assert len(tray.moves) == 1
    assert "error" in tool.fn(changes=[{"start": "Rookie Guy", "bench": "Pittman"}])


class FakeRPC:
    def __init__(self, fail: str | None = None):
        self.calls, self.fail = [], fail

    def rpc(self, name, args):
        self.calls.append((name, args))
        fail = self.fail

        class Run:
            def execute(self_inner):
                if fail:
                    raise RuntimeError(fail)
                return type("R", (), {"data": {}})()
        return Run()


def test_settle_lineup_saves_through_the_same_rpc():
    tray = gm_moves.Tray()
    gm_moves.lineup_tool(_data, "me", tray).fn(changes=[{"start": "Odunze", "bench": "Pittman"}])
    client = FakeRPC()
    done = gm_moves.settle(client, league_id="L", season=2026, team_id="me", move=tray.moves[0], action="confirm", lineup_data=_data)
    assert done["status"] == "done"
    name, args = client.calls[0]
    assert name == "set_lineup_authenticated" and args["p_request"]["week"] == 12
    assert [s["player_id"] for s in args["p_request"]["slots"]][4] == "w3"
    with pytest.raises(gm_moves.MoveError, match="already handled"):
        gm_moves.settle(client, league_id="L", season=2026, team_id="me", move=done, action="confirm", lineup_data=_data)


def test_settle_reports_the_leagues_refusal():
    move = gm_moves._new("bid", "bid:x", "Place a waiver bid", ["Bid $5 on X"], {"type": "bid", "player_id": "x", "bid": 5, "drop_player_id": None})
    done = gm_moves.settle(FakeRPC("bid_over_budget"), league_id="L", season=2026, team_id="me", move=move, action="confirm", lineup_data=_data)
    assert done["status"] == "failed" and done["result"] == "That's more FAAB than you have left."
    assert gm_moves.settle(FakeRPC(), league_id="L", season=2026, team_id="me", move=move, action="dismiss", lineup_data=_data)["status"] == "dismissed"


def test_past_cards_go_back_to_the_gm():
    history = [{"role": "user", "content": "Pittman or Odunze?"},
               {"role": "assistant", "content": "Odunze.", "moves": [{"title": "Set your Week 12 lineup", "lines": ["Start Rome Odunze at WR"], "status": "done", "result": "Lineup saved."}]}]
    text = _history_messages(history)[1]["content"]
    assert "[Move cards under this answer]" in text and "confirmed and done" in text
