from types import SimpleNamespace

import pytest

from api import league, sleeper


def _matchup(roster_id, matchup_id, points):
    return {"roster_id": roster_id, "matchup_id": matchup_id, "points": points}


SLEEPER = {
    "league/S": {"settings": {"last_scored_leg": 2}},
    "state/nfl": {"week": 3},
    "league/S/users": [{"user_id": "u1", "display_name": "sleeper1"}, {"user_id": "u9", "display_name": "sleeper9"}],
    "league/S/rosters": [
        {"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u9"},
        {"roster_id": 3, "owner_id": "u3"}, {"roster_id": 4, "owner_id": "u4"},
    ],
    "league/S/matchups/1": [_matchup(1, 1, 120), _matchup(2, 1, 100), _matchup(3, 2, 90), _matchup(4, 2, 95)],
    "league/S/matchups/2": [_matchup(1, 1, 80), _matchup(3, 1, 130), _matchup(2, 2, 110), _matchup(4, 2, 70)],
    "league/S/matchups/3": [_matchup(1, 1, 40.5), _matchup(4, 1, 12), _matchup(2, 2, 0), _matchup(3, 2, 0)],
}


@pytest.fixture(autouse=True)
def fake_sleeper(monkeypatch):
    sleeper.clear()
    monkeypatch.setattr(sleeper, "fetch", lambda url: SLEEPER[url.removeprefix(sleeper.BASE + "/")])
    yield
    sleeper.clear()


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def test_roster_names_prefer_owner_name_then_sleeper_name():
    client = SimpleNamespace(table=lambda _: FakeTable([
        {"sleeper_roster_id": 1, "owner_name": "Tommy Bruggeman"},
        {"sleeper_roster_id": "3", "owner_name": None, "team_name": "Grady's Team"},
    ]))
    names = league.roster_names(client, "L", "S")
    assert names == {1: "Tommy Bruggeman", 2: "sleeper9", 3: "Grady's Team", 4: "Roster 4"}


def test_standings_only_count_finished_weeks():
    names = {1: "Tommy", 2: "Dylan", 3: "Grady", 4: "Nando"}
    result = league.standings("S", names, my_roster_id=1)

    assert result["weeks_played"] == 2
    assert result["playoff_teams"] == 4
    by_name = {row["name"]: row for row in result["rows"]}
    # 4 teams, so every team is a weekly top 5: 2 per win + 1 per top 5.
    assert by_name["Tommy"]["wins"] == 1 and by_name["Tommy"]["standing_points"] == 4
    assert by_name["Grady"]["pf"] == 220
    assert by_name["Tommy"]["is_me"] and not by_name["Dylan"]["is_me"]
    assert [row["rank"] for row in result["rows"]] == [1, 2, 3, 4]
    assert all(row["prev_rank"] is not None for row in result["rows"])


def test_matchups_marks_live_week_and_names_teams():
    names = {1: "Tommy", 2: "Dylan", 3: "Grady", 4: "Nando"}
    result = league.matchups("S", names, week=3, my_roster_id=1)

    assert result["week"] == 3 and result["is_final"] is False
    first = result["games"][0]["teams"]
    assert [t["name"] for t in first] == ["Tommy", "Nando"]
    assert result["summary"]["high"]["name"] == "Tommy"


def test_matchups_final_week():
    result = league.matchups("S", {}, week=1, my_roster_id=None)
    assert result["is_final"] is True
    assert len(result["games"]) == 2


def test_sleeper_owners_include_taxi_and_ir(monkeypatch):
    from api import league as lg
    monkeypatch.setattr(lg.sleeper, "get", lambda path, ttl=300: [{"roster_id": 6, "players": ["1", "2"], "taxi": ["3"], "reserve": None}])
    assert lg.sleeper_owners("S") == {"1": 6, "2": 6, "3": 6}
