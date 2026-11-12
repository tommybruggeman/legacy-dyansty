from types import SimpleNamespace

from league_ai.tools import nfl


def test_compare_players_adds_if_he_plays_for_a_player_in_doubt(monkeypatch):
    players = {"Deebo Samuel": {"sleeper_id": "1", "full_name": "Deebo Samuel", "position": "WR", "team": "SF"},
               "Blake Corum": {"sleeper_id": "2", "full_name": "Blake Corum", "position": "RB", "team": "LAR"}}
    calls = []

    def week_projection(found, active=False):
        calls.append(active)
        return {"1": 13.5} if active else {"1": 9.2, "2": 11.9}

    data = SimpleNamespace(find_players=lambda n, limit=1: [players[n]], week_projection=week_projection,
                           stats_summary=lambda *a, **k: {"seasons": {}})
    chance = {"1": {"chance_to_play": "54%", "verdict": "questionable"}}
    monkeypatch.setattr(nfl, "_profile_dict", lambda d, p: {"chance_to_play_this_week": chance[p["sleeper_id"]]} if p["sleeper_id"] in chance else {})
    monkeypatch.setattr(nfl, "tool_get_schedule_outlook", lambda d, names: {})
    out = nfl.tool_compare_players(data, ["Deebo Samuel", "Blake Corum"])
    assert out["Deebo Samuel"]["if_he_plays"]["projection"] == 13.5
    assert out["Deebo Samuel"]["if_he_plays"]["chance_to_play"] == "54%"
    assert "if_he_plays" not in out["Blake Corum"]
    assert calls == [False, True]


def test_play_probability_reads_the_percent():
    assert nfl._play_probability({"chance_to_play_this_week": {"chance_to_play": "54%"}}) == 0.54
    assert nfl._play_probability({}) is None
