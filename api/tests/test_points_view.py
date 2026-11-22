import pandas as pd

from api.league import add_points_view
from api.teams import points_view


def test_points_view_ranks_and_weeks():
    weeks = pd.DataFrame([
        {"team": "1", "week": 1, "score": 120.0, "opp_score": 100.0},
        {"team": "2", "week": 1, "score": 100.0, "opp_score": 120.0},
        {"team": "3", "week": 1, "score": 90.0, "opp_score": 80.0},
        {"team": "1", "week": 2, "score": 80.0, "opp_score": 130.0},
        {"team": "2", "week": 2, "score": 130.0, "opp_score": 80.0},
        {"team": "3", "week": 2, "score": 110.0, "opp_score": 70.0},
    ])
    rows = [
        {"roster_id": 1, "pf": 200.0, "pa": 230.0, "pf_per_game": 100.0, "pa_per_game": 115.0, "top5": 2, "games": 2},
        {"roster_id": 2, "pf": 230.0, "pa": 200.0, "pf_per_game": 115.0, "pa_per_game": 100.0, "top5": 2, "games": 2},
        {"roster_id": 3, "pf": 200.0, "pa": 150.0, "pf_per_game": 100.0, "pa_per_game": 75.0, "top5": 1, "games": 2},
    ]
    add_points_view(rows, weeks)
    one = points_view(rows[0])
    assert one["ranks"]["pf"] == 2 and one["ranks"]["pa"] == 1  # tie on PF shares 2nd; most PA is 1st
    assert rows[2]["points_ranks"]["pf"] == 2
    assert one["best_week"] == {"week": 1, "score": 120.0} and one["worst_week"] == {"week": 2, "score": 80.0}
    # week 1 avg 103.3 (team 1 above), week 2 avg 106.7 (team 1 below)
    assert (one["vs_avg_wins"], one["vs_avg_losses"]) == (1, 1)
    assert points_view(None) is None and points_view({"pf": 1}) is None


class _Q:
    def __init__(self, rows, rpc_ok=True): self.rows, self.rpc_ok, self.calls = rows, rpc_ok, []
    def rpc(self, name, args):
        self.calls.append((name, args))
        if not self.rpc_ok:
            raise RuntimeError("missing function")
        return self
    def table(self, name): assert name == "rookie_draft_board_assignments"; return self
    def select(self, *_): return self
    def execute(self): return type("R", (), {"data": self.rows})()


def test_rookie_deals_reads_every_draft_through_the_rpc():
    from api.teams import rookie_deals
    rows = [
        {"player_id": "golden", "rookie_deal": True, "expected_end": 2026, "option_salary": 25, "option_consumed": False, "taxi_seasons": 0},
        {"player_id": "mendoza", "rookie_deal": True, "expected_end": 2028, "option_salary": 25, "option_consumed": False, "taxi_seasons": 1},
    ]
    q = _Q(rows)
    deals = rookie_deals(q, "L")
    assert q.calls == [("read_rookie_deals_authenticated", {"p_league_id": "L"})]
    assert deals["mendoza"]["expected_end"] == 2028 and deals["golden"]["option_salary"] == 25.0


def test_rookie_deals_falls_back_to_the_board():
    from api.teams import rookie_deals
    q = _Q([{"player_id": "golden", "option_consumed": False, "one_time_option_salary": 25, "rookie_contract_provenance": True}], rpc_ok=False)
    assert rookie_deals(q, "L")["golden"] == {"rookie_deal": True, "expected_end": None, "option_salary": 25.0, "option_consumed": False, "taxi_seasons": 0}


def test_apply_rookie_deal():
    from api.teams import apply_rookie_deal
    deal = lambda **k: {"rookie_deal": True, "expected_end": 2026, "option_salary": 25.0, "option_consumed": False, **k}
    golden = {"player_id": "golden", "years_left": 1, "total_years": 2}
    apply_rookie_deal(golden, deal(), 2026)
    assert golden["is_rookie"] and golden["rookie_option"] and golden["option_salary"] == 25.0 and golden["years_left"] == 1
    # taxi in 2026: contract row still ends 2027, rookie deal really runs through 2028
    mendoza = {"player_id": "mendoza", "years_left": 2, "total_years": 2}
    apply_rookie_deal(mendoza, deal(expected_end=2028), 2026)
    assert (mendoza["years_left"], mendoza["total_years"]) == (3, 3)
    # re-signed (not the rookie deal any more): no R tag, no option
    beck = {"player_id": "beck", "years_left": 1, "is_rookie": False}
    apply_rookie_deal(beck, deal(rookie_deal=False), 2026)
    assert not beck["is_rookie"] and not beck["rookie_option"]
    used = {"player_id": "judkins", "years_left": 1}
    apply_rookie_deal(used, deal(option_consumed=True), 2026)
    assert used["is_rookie"] and not used["rookie_option"]
