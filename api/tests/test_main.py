from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import main


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
