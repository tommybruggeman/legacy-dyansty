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


def test_rookie_option_ids_reads_every_draft():
    from api.teams import rookie_option_ids

    class Q:
        def __init__(self, rows): self.rows = rows
        def table(self, name): assert name == "rookie_draft_board_assignments"; return self
        def select(self, *_): return self
        def execute(self): return type("R", (), {"data": self.rows})()

    rows = [
        {"player_id": "golden", "option_consumed": False, "rookie_contract_provenance": True},  # 2025 draft, old league
        {"player_id": "coleman", "option_consumed": False, "rookie_contract_provenance": True},  # 2026 draft
        {"player_id": "used", "option_consumed": True, "rookie_contract_provenance": True},
    ]
    assert rookie_option_ids(Q(rows)) == {"golden", "coleman"}
