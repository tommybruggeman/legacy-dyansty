from datetime import datetime, timezone

from api import lineups, teams

SLOTS = ["QB", "RB", "FLEX", "DEF"]


def test_starter_slots_and_fits():
    assert lineups.starter_slots(["QB", "RB", "FLEX", "BN", "BN", "TAXI", "IR"]) == ["QB", "RB", "FLEX"]
    assert lineups.fits("FLEX", "TE") and not lineups.fits("FLEX", "QB")
    assert lineups.fits("SUPER_FLEX", "QB") and lineups.fits("RB", "RB") and not lineups.fits("RB", "WR")
    assert lineups.label("SUPER_FLEX") == "OP"


def test_effective_prefers_saved_then_sleeper_and_drops_gone_players():
    saved = [{"week": 14, "slots": [{"slot": "QB", "player_id": "q1"}, {"slot": "RB", "player_id": "r9"},
                                    {"slot": "FLEX", "player_id": "w1"}, {"slot": "DEF", "player_id": None}]}]
    active = {"q1", "w1", "r1", "BUF"}
    assert lineups.effective(SLOTS, saved, ["x"], active) == ["q1", None, "w1", None]
    assert lineups.effective(SLOTS, [], ["q1", "r1"], active) == ["q1", "r1", None, None]
    # a saved row for a different slot list is ignored
    assert lineups.effective(["QB"], saved, ["q1"], active) == ["q1"]


def test_locks_from_kickoffs():
    games = [{"gameday": "2026-12-17", "gametime": "20:15", "home_team": "DAL", "away_team": "NYG"}]
    starts = lineups.kickoffs(games)
    assert starts["DAL"] == datetime(2026, 12, 18, 1, 15, tzinfo=timezone.utc)
    before, after = datetime(2026, 12, 18, 1, 0, tzinfo=timezone.utc), datetime(2026, 12, 18, 2, 0, tzinfo=timezone.utc)
    assert not lineups.is_locked("p", "DAL", starts, before)
    assert lineups.is_locked("p", "DAL", starts, after)
    assert lineups.is_locked("NYG", None, starts, after)  # a team defense
    assert not lineups.is_locked("p", "KC", starts, after)


def test_roster_rows_shows_empty_app_slots():
    state = {"roster": [
        {"league_team_id": "t", "sleeper_player_id": "q1", "player_name": "Q", "pos": "QB", "cap_hit": 5},
        {"league_team_id": "t", "sleeper_player_id": "r1", "player_name": "R", "pos": "RB", "cap_hit": 3},
    ]}
    out = teams.roster_rows(state, "t", {"starters": ["r1"], "app_lineup": [("QB", "q1"), ("RB", None)]}, ["QB", "RB"], {}, {})
    assert [p["slot"] for p in out["starters"]] == ["QB", "RB"]
    assert out["starters"][1]["empty"] and out["starters"][1]["name"] == "Empty"
    assert [p["player_id"] for p in out["bench"]] == ["r1"]


def test_view():
    v = lineups.view(SLOTS, ["q1", None, "w1", None], 15, {"q1"}, True)
    assert v["slots"][0]["locked"] and not v["slots"][2]["locked"]
    assert v["fits"]["FLEX"] == ["RB", "TE", "WR"]
