"""The test copy of the league: Sleeper reads through the mirror id, and the app's rosters on top of Sleeper's."""
from api import own_league, sleeper


def test_mirror_id_round_trip_has_no_digits():
    mid = sleeper.mirror_id("1361083066197491712")
    assert mid.startswith("mirror-") and not any(c.isdigit() for c in mid)
    assert sleeper.real_id(mid) == "1361083066197491712"
    assert sleeper.is_mirror(mid) and not sleeper.is_mirror("1361083066197491712")


def test_mirror_reads_real_league_with_names_hidden(monkeypatch):
    calls = []

    def fake(url):
        calls.append(url)
        if url.endswith("/users"):
            return [{"user_id": "u1", "display_name": "RealName", "avatar": "x", "metadata": {"team_name": "Real"}}]
        if url.endswith("/transactions/3"):
            return [{"type": "trade"}]
        return {"league_id": "123", "name": "Legacy Dynasty", "settings": {"waiver_budget": 100}}

    monkeypatch.setattr(sleeper, "fetch", fake)
    sleeper.clear()
    mid = sleeper.mirror_id("123")
    users = sleeper.get(f"league/{mid}/users")
    assert users[0]["display_name"] == "Owner 1" and users[0]["user_id"] == "u1" and users[0]["metadata"] == {}
    info = sleeper.get(f"league/{mid}")
    assert info["name"] == "Legacy Test" and info["league_id"] == mid and info["settings"]["waiver_budget"] == 100
    assert sleeper.get(f"league/{mid}/transactions/3") == []
    assert all("/league/123" in u for u in calls)
    sleeper.clear()


def test_overrides_for_the_mirror_never_touch_the_real_league():
    mid = sleeper.mirror_id("555")
    sleeper.put(f"league/{mid}/matchups/1", [{"roster_id": 1}])
    assert f"{sleeper.BASE}/league/555/matchups/1" not in sleeper._overrides
    sleeper.drop(f"league/{mid}/")


def test_merge_rosters_takes_the_apps_players():
    rows = [{"roster_id": 1, "owner_id": "u1", "players": ["a", "b"], "starters": ["a", "b"], "taxi": [], "reserve": [],
             "settings": {"wins": 5}},
            {"roster_id": 2, "owner_id": "u2", "players": ["c"], "starters": ["c"], "taxi": [], "reserve": []}]
    app = [{"roster_id": 1, "players": ["a", "c"], "taxi": [], "reserve": []},
           {"roster_id": 2, "players": ["b"], "taxi": ["b"], "reserve": []}]
    out = {r["roster_id"]: r for r in own_league.merge_rosters(rows, app)}
    assert out[1]["players"] == ["a", "c"] and out[1]["starters"] == ["a", "0"] and out[1]["settings"] == {"wins": 5}
    assert out[2]["players"] == ["b"] and out[2]["taxi"] == ["b"] and out[2]["starters"] == ["0"] and out[2]["owner_id"] == "u2"
