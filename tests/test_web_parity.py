"""The website shows the same numbers as the app: box score markup, saved odds, Front Office number tiles."""
from __future__ import annotations

from league_ai import matchup_odds
from league_ai.front_office import mobile_status
from services.matchups_view import box_score_html


class _Q:
    def __init__(self, rows):
        self.rows, self.filters = rows, {}

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.rows = [r for r in self.rows if r.get(k, v) == v]
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _):
        return _Q(list(self._rows))


def test_saved_week_returns_latest_week_by_owner():
    rows = [{"league_id": "L", "season": 2026, "week": 7, "owner_name": "A", "playoff_pct": 40.0, "win_pct": 55.0, "opp_owner": "B"},
            {"league_id": "L", "season": 2026, "week": 8, "owner_name": "A", "playoff_pct": 45.0, "win_pct": 60.0, "opp_owner": "C"},
            {"league_id": "L", "season": 2026, "week": 8, "owner_name": "C", "playoff_pct": 20.0, "win_pct": 40.0, "opp_owner": "A"}]
    week, by_owner = matchup_odds.saved_week(_DB(rows), "L", 2026)
    assert week == 8
    assert by_owner["A"]["win_pct"] == 60.0 and set(by_owner) == {"A", "C"}
    assert matchup_odds.saved_week(_DB(rows), "L", 2026, 7)[1]["A"]["opp_owner"] == "B"
    assert matchup_odds.saved_week(_DB([]), "L", 2026) == (None, {})


def test_box_score_html_shows_points_projection_and_rank():
    game = {"teams": [
        {"name": "Tommy", "score": 101.2, "projected": 110.4, "players": [
            {"slot": "QB", "name": "Josh Allen", "pos": "QB", "team": "BUF", "points": 24.5, "proj": 22.0, "rank": "QB3", "status": "final"},
            {"slot": "RB", "name": "<b>X</b>", "pos": "RB", "team": None, "points": 0.0, "proj": None, "rank": None, "status": "upcoming"}]},
        {"name": "Grady", "score": 90.0, "projected": None, "players": []}]}
    out = box_score_html(game)
    assert "Josh Allen" in out and "proj 22.0" in out and "QB3" in out and "proj 110.4" in out
    assert "&lt;b&gt;X&lt;/b&gt;" in out and "<b>X</b>" not in out
    assert "Grady" in out


def test_mobile_status_includes_playoff_odds_tile():
    tiles = [{"label": "Record · 2nd of 10", "value": "5-2"}, {"label": "Playoff odds · +4 this week", "value": "62%"}]
    assert mobile_status(8, tiles) == "Week 8 · 5-2, 2nd of 10 · 62% playoff odds"
