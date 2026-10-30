from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import cache, main


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}

    def select(self, *_):
        return self

    def limit(self, *_):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        rows = [r for r in self.rows if all(r.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return FakeQuery(self.tables.get(name, []))


def _app_with(tables):
    user = SimpleNamespace(id="u1")
    main.app.dependency_overrides[main.owner_client] = lambda: (FakeClient(tables), user)
    return TestClient(main.app)


def teardown_function():
    main.app.dependency_overrides.clear()
    cache.clear()


def test_health():
    assert TestClient(main.app).get("/health").json() == {"status": "ok"}


def test_requires_sign_in():
    response = TestClient(main.app).get("/me")
    assert response.status_code == 401


def test_me_resolves_team_by_id():
    client = _app_with({
        "league_memberships": [{"user_id": "u1", "league_id": "L", "league_team_id": "t1", "role": "owner"}],
        "league_teams": [
            {"id": "t0", "league_id": "L", "owner_name": "Dylan", "team_name": "Other"},
            {"id": "t1", "league_id": "L", "owner_name": "Tommy", "team_name": "Legacy"},
        ],
    })
    assert client.get("/me").json() == {
        "league_id": "L", "team_id": "t1", "team_name": "Legacy", "owner_name": "Tommy", "role": "owner",
        "sleeper_roster_id": None,
    }


def test_me_falls_back_to_owner_name():
    client = _app_with({
        "league_memberships": [{"user_id": "u1", "league_id": "L", "owner_name": "Grady", "role": "owner"}],
        "league_teams": [{"id": "t2", "league_id": "L", "owner_name": "Grady", "team_name": "G Money"}],
    })
    assert client.get("/me").json()["team_id"] == "t2"


def test_me_without_membership_is_404():
    client = _app_with({"league_memberships": []})
    assert client.get("/me").status_code == 404


def test_matchup_cards_get_projected_totals(monkeypatch):
    from api import box_score, league, teams
    rows = [{"matchup_id": 1, "roster_id": 1, "starters": ["10"], "players_points": {"10": 5.0}},
            {"matchup_id": 1, "roster_id": 2, "starters": ["20"], "players_points": {"20": 0.0}}]
    monkeypatch.setattr(main, "_season_context", lambda client, user: {"season": 2026, "sleeper_id": "S"})
    monkeypatch.setattr(league.sleeper, "get", lambda path, ttl=60: rows)
    monkeypatch.setattr(teams, "player_info", lambda client, ids: {})

    def fake(client, ctx, week, final, result, info):
        for side in result["teams"]:
            side["projected"] = 10.0 + side["roster_id"]
    monkeypatch.setattr(box_score, "add_projections", fake)
    result = {"week": 8, "games": [{"teams": [{"roster_id": 1}, {"roster_id": 2}]}]}
    main._add_card_projections(None, None, result)
    assert [t["projected"] for t in result["games"][0]["teams"]] == [11.0, 12.0]


def test_matchup_cards_get_live_counts_and_win_odds(monkeypatch):
    from api import box_score, league, teams
    rows = [{"matchup_id": 1, "roster_id": 1, "starters": ["10", "11"], "players_points": {"10": 5.0}},
            {"matchup_id": 1, "roster_id": 2, "starters": ["20", "21"], "players_points": {"20": 0.0}}]
    monkeypatch.setattr(main, "_season_context", lambda client, user: {"season": 2026, "sleeper_id": "S"})
    monkeypatch.setattr(league.sleeper, "get", lambda path, ttl=60: rows)
    monkeypatch.setattr(teams, "player_info", lambda client, ids: {})

    def fake(client, ctx, week, final, result, info):
        for side in result["teams"]:
            a, b = side["players"]
            a.update(status="playing", proj=12.0, live_proj=10.0)
            b.update(status="waiting", proj=8.0, live_proj=8.0, kickoff=f"2026-11-01T1{side['roster_id']}:00:00-07:00")
            side["projected"] = 18.0 + side["roster_id"]
    monkeypatch.setattr(box_score, "add_projections", fake)
    result = {"week": 8, "games": [{"teams": [{"roster_id": 1, "score": 5.0}, {"roster_id": 2, "score": 0.0}]}]}
    main._add_card_projections(None, None, result)
    game = result["games"][0]
    assert [(t["playing"], t["to_play"]) for t in game["teams"]] == [(1, 1), (1, 1)]
    assert sum(game["win_chance"]) == 100 and game["win_chance"][0] < 50
    assert game["next_kickoff"] == "2026-11-01T11:00:00-07:00"


def test_matchup_cards_get_owner_pictures_and_records(monkeypatch):
    from api import league
    monkeypatch.setattr(main.cache, "cached", lambda key, seconds, load: load())
    monkeypatch.setattr(league.sleeper, "get", lambda path, ttl=60: [{"user_id": "u1", "avatar": "abc"}] if path.endswith("users") else [{"roster_id": 1, "owner_id": "u1"}])
    monkeypatch.setattr(league, "standings", lambda sid, names, me: {"rows": [{"roster_id": 1, "wins": 5, "losses": 2, "ties": 0}, {"roster_id": 2, "wins": 3, "losses": 3, "ties": 1}]})
    result = {"games": [{"teams": [{"roster_id": 1}, {"roster_id": 2}]}]}
    main._add_card_owners("S", {}, 1, result)
    a, b = result["games"][0]["teams"]
    assert a["avatar"].endswith("/abc") and a["record"] == "5-2"
    assert b["avatar"] is None and b["record"] == "3-3-1"
