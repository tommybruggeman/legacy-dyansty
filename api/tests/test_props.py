from api import cache, props


def _item(athlete: str, type_id: str, value: float) -> dict:
    return {
        "athlete": {"$ref": f"http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/athletes/{athlete}?lang=en"},
        "type": {"id": type_id},
        "current": {"target": {"value": value}},
    }


def test_lines_by_athlete_keeps_one_line_per_prop():
    items = [_item("1", props.REC, 38.5), _item("1", props.REC, 38.5), _item("2", props.PASS, 251.5), {"type": {"id": "98"}}]
    assert props.lines_by_athlete(items) == {"1": {props.REC: 38.5}, "2": {props.PASS: 251.5}}


def test_pick_line_by_position():
    assert props.pick_line({props.REC: 38.5}, "TE") == {"label": "VEGAS REC YDS", "line": 38.5}
    assert props.pick_line({props.RUSH: 55.5, props.RUSH_REC: 78.5}, "RB") == {"label": "VEGAS RUSH+REC YDS", "line": 78.5}
    assert props.pick_line({props.RUSH: 55.5}, "RB") == {"label": "VEGAS RUSH YDS", "line": 55.5}
    assert props.pick_line({props.REC: 38.5}, "QB") is None
    assert props.pick_line({props.REC: 38.5}, "K") is None


def test_event_for_team_maps_espn_codes():
    board = {"events": [{"id": "9", "competitions": [{"competitors": [{"team": {"abbreviation": "WSH"}}, {"team": {"abbreviation": "DAL"}}]}]}]}
    assert props.event_for_team(board, "WAS") == "9"
    assert props.event_for_team(board, "PIT") is None


def test_yards_line_end_to_end(monkeypatch):
    cache._cache.clear()
    monkeypatch.setattr(props, "espn_ids", lambda: {"5850": "4361411"})
    calls = []

    def fake(url, params=None):
        calls.append(url)
        if url == props.SCOREBOARD:
            return {"events": [{"id": "77", "competitions": [{"competitors": [{"team": {"abbreviation": "PIT"}}]}]}]}
        return {"items": [_item("4361411", props.REC, 31.5)], "pageCount": 1}

    monkeypatch.setattr(props, "fetch", fake)
    assert props.yards_line("5850", "TE", "PIT", 2026, 9) == {"label": "VEGAS REC YDS", "line": 31.5}
    assert props.yards_line("5850", "TE", "PIT", 2026, 9) == {"label": "VEGAS REC YDS", "line": 31.5}
    assert len(calls) == 2  # cached the second time
    assert props.yards_line("999", "TE", "PIT", 2026, 9) is None


def test_game_without_lines_yet_is_empty(monkeypatch):
    import requests

    cache._cache.clear()
    resp = requests.Response()
    resp.status_code = 404

    def fake(url, params=None):
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(props, "fetch", fake)
    assert props.game_lines("1") == {}
