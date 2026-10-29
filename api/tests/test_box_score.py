from types import SimpleNamespace

from api import box_score, home, league, projections, teams


class _T:
    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=[])


class _C:
    def table(self, name):
        return _T()


def test_game_adds_projections_and_projected_totals(monkeypatch):
    rows = [{"matchup_id": 3, "roster_id": 1, "starters": ["10", "20"], "starters_points": [20.0, 0.0], "points": 20.0},
            {"matchup_id": 3, "roster_id": 2, "starters": ["30", "0"], "starters_points": [12.5, 0.0], "points": 12.5}]
    monkeypatch.setattr(teams.sleeper, "get", lambda path, ttl=60: rows)
    monkeypatch.setattr(league, "last_scored_week", lambda sid: 7)
    monkeypatch.setattr(teams, "player_info", lambda client, ids: {"10": {"full_name": "A", "position": "QB", "team": "BUF"},
                                                                    "20": {"full_name": "B", "position": "RB", "team": "KC"},
                                                                    "30": {"full_name": "C", "position": "WR", "team": "MIA"}})
    monkeypatch.setattr(projections, "week_projections", lambda *a, **k: {"10": 22.0, "20": 14.0, "30": 15.0})
    monkeypatch.setattr(projections, "week_ranks", lambda *a, **k: {"10": {"label": "QB2"}})
    monkeypatch.setattr(projections, "week_lines", lambda *a, **k: {})
    monkeypatch.setattr(projections, "league_scoring", lambda sid: {})
    monkeypatch.setattr(home, "player_status", lambda team, starts, now: "playing" if team == "BUF" else "upcoming")
    ctx = {"season": 2026, "sleeper_id": "S", "names": {1: "Tommy", 2: "Grady"}, "slots": ["QB", "RB", "BN"], "sleeper_roster_id": 1}
    g = box_score.game(_C(), ctx, 8, 3, [{"roster_id": 1, "team_id": "t1"}])
    me, them = g["teams"]
    assert me["team_id"] == "t1" and g["my_roster_id"] == 1 and g["is_final"] is False
    assert me["players"][0]["proj"] == 22.0 and me["players"][0]["rank"] == "QB2"
    assert me["projected"] == 36.0          # max(20 real, 22 proj) while playing + 14 projected
    assert them["projected"] == 15.0
    assert box_score.game(_C(), ctx, 8, 99) is None
