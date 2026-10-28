import pandas as pd

from pipeline.nfl_data.prospects import build_prospects
from pipeline.nfl_data.prospects_free import season_totals, to_cfbd_inputs

NA = None


def _play(team, **kw):
    row = {"game_id": 1, "play_id": kw.pop("play_id"), "team": team, "conference": "SEC", "yards_to_goal": kw.pop("ytg", 50),
           "reception_player_id": NA, "reception_yds": NA, "completion_player_id": NA, "completion_yds": NA,
           "rush_player_id": NA, "rush_yds": NA, "interception_thrown_player_id": NA, "touchdown_player_id": NA,
           "incompletion_player_id": NA, "target_player_id": NA}
    row.update(kw)
    return row


def test_season_totals_and_prospect_rows():
    plays = pd.DataFrame([
        _play("Ole Miss", play_id=1, completion_player_id=10, completion_yds=20, reception_player_id=20, reception_yds=20, target_player_id=20),
        _play("Ole Miss", play_id=2, ytg=15, completion_player_id=10, completion_yds=15, reception_player_id=20, reception_yds=15, target_player_id=20),
        _play("Ole Miss", play_id=3, incompletion_player_id=10, target_player_id=21),
        _play("Ole Miss", play_id=4, rush_player_id=30, rush_yds=7),
        _play("Ole Miss", play_id=5, ytg=3, rush_player_id=30, rush_yds=3),
        _play("Ole Miss", play_id=6, interception_thrown_player_id=10),
    ])
    tot = season_totals(plays).set_index("pid")
    assert tot.loc["10", "pass_att"] == 4 and tot.loc["10", "pass_yds"] == 35 and tot.loc["10", "pass_td"] == 1 and tot.loc["10", "pass_int"] == 1
    assert tot.loc["20", "receptions"] == 2 and tot.loc["20", "rec_yds"] == 35 and tot.loc["20", "rec_td"] == 1 and tot.loc["20", "targets"] == 2
    assert tot.loc["30", "rush_att"] == 2 and tot.loc["30", "rush_td"] == 1
    assert abs(tot.loc["30", "usage_rush"] - 1.0) < 1e-9

    roster = pd.DataFrame([
        {"athlete_id": 10, "first_name": "Trinidad", "last_name": "Chambliss", "team": "Ole Miss", "position": "QB", "year": 4, "height": 73, "weight": 200},
        {"athlete_id": 20, "first_name": "Wide", "last_name": "Out", "team": "Ole Miss", "position": "WR", "year": 3, "height": 72, "weight": 190},
        {"athlete_id": 30, "first_name": "Run", "last_name": "Ner", "team": "Ole Miss", "position": "RB", "year": 2, "height": 70, "weight": 210},
    ])
    draft = pd.DataFrame([{"season": 2026, "round": 1, "pick": 3, "team": "NYJ", "pfr_player_name": "Trinidad Chambliss", "position": "QB"}])
    rows = {r["name"]: r for r in build_prospects(2025, source="cfbfastr", **to_cfbd_inputs(tot.reset_index(), roster, draft, 2025))}
    qb = rows["Trinidad Chambliss"]
    assert qb["pass_yds"] == 35 and qb["nfl_draft_round"] == 1 and qb["source"] == "cfbfastr" and qb["draft_eligible"] is True
    assert rows["Run Ner"]["rush_td"] == 1 and rows["Run Ner"]["draft_eligible"] is False
