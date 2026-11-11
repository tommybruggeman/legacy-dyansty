from types import SimpleNamespace

from league_ai import power, team_lineups
from league_ai.front_office import week_context

SLOTS = ["QB", "RB", "WR", "FLEX"]
INFO = {
    "qb1": {"position": "QB", "team": "BUF"}, "qb2": {"position": "QB", "team": "TEN"},
    "rb1": {"position": "RB", "team": "NO"}, "rb2": {"position": "RB", "team": "LAC", "injury_status": "Out"},
    "wr1": {"position": "WR", "team": "MIN"}, "wr2": {"position": "WR", "team": "TB"}, "wr3": {"position": "WR", "team": "SEA"},
}
NAMES = {"qb1": "Josh Allen", "qb2": "Fernando Mendoza", "rb1": "Audric Estime", "rb2": "Omarion Hampton",
         "wr1": "Justin Jefferson", "wr2": "Mike Evans", "wr3": "DK Metcalf"}
PROJ = {"qb1": 22.0, "qb2": 11.3, "rb1": 10.4, "rb2": 7.9, "wr1": 18.0, "wr2": 3.0, "wr3": 10.9}


def lineup(starters, **kw):
    args = dict(slots=SLOTS, starters=starters, roster=list(INFO), names=NAMES, info=INFO, proj=PROJ, bye_teams=set(),
                locked=set(), designation={"qb2": "taxi"}, bench_size=8)
    args.update(kw)
    return team_lineups.team_lineup(**args)


def test_set_lineup_flags_problems_and_never_offers_taxi():
    out = lineup(["qb1", "rb2", "wr1", None])
    assert [s["slot"] for s in out["starters"]] == ["QB", "RB", "WR", "FLX"]
    assert out["starters"][1]["flags"] == "Out"
    assert out["problems"] == ["Omarion Hampton (RB) is Out", "FLX slot is empty"]
    bench = [b["player"] for b in out["bench"]]
    assert "Fernando Mendoza" not in bench and out["cant_play_taxi_ir"] == ["Fernando Mendoza (taxi)"]
    swaps = {s["slot"]: s["start"] for s in out["better_bench_options"]}
    assert swaps == {"RB": "Audric Estime", "FLX": "DK Metcalf"}
    assert out["projected_total"] == 40.0  # Hampton is Out, so he counts 0


def test_locked_and_bye_players_are_not_swapped_in():
    out = lineup(["qb1", "rb1", "wr1", "wr2"], locked={"wr3"}, bye_teams={"NO"})
    assert out["starters"][1]["flags"] == "bye"
    assert out["better_bench_options"] == []  # Metcalf's game started; no other playable RB on the bench


def test_taxi_and_ir_players_never_make_the_power_lineup():
    def strength(pid, pos, ppg, designation=None):
        return power.player_strength({"sleeper_player_id": pid, "player_name": pid, "pos": pos, "league_team_id": "T",
                                      "roster_designation": designation},
                                     recent_ppg=ppg, season_ppg=ppg, pos_rank=None, age=None, injury_status=None)
    players = [strength("starter", "QB", 15), strength("rookie", "QB", 20, "taxi"), strength("hurt", "QB", 25, "ir")]
    assert not players[1].available and players[1].designation == "taxi" and players[2].designation == "ir"
    lineup_, bench = power.fill_lineup(players, ["QB"])
    assert [p.sleeper_id for _, p in lineup_] == ["starter"] and bench == []
    team = power.compute_power(players, [{"league_team_id": "T", "owner_name": "Tommy"}], ["QB"])[0]
    assert [p.sleeper_id for p in team.inactive] == ["rookie", "hurt"]


def test_week_context_names_taxi_players_and_points_to_set_lineups():
    fo = {"week": 10, "rankings": [{"owner": "Tommy", "now_rank": 3, "cant_play": ["QB Fernando Mendoza (taxi)"]}]}
    text = week_context(fo, "Tommy")
    assert "TAXI or IR" in text and "Fernando Mendoza (taxi)" in text and "get_lineups" in text
