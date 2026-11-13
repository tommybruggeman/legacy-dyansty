from datetime import date, datetime
from types import SimpleNamespace

from api import front_office
from league_ai import runtime

OWNER = runtime.Owner(user_id="u1", league_id="L", league_team_id="t1", season=2026, owner_name="Tommy Bruggeman", team_name="Ballers")


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return FakeTable(self.tables.get(name, []))


def test_daily_question_limit_resets_each_day():
    front_office.clear()
    day = date(2026, 10, 28)
    assert all(front_office.take_question("u1", day) for _ in range(front_office.DAILY_QUESTIONS))
    assert not front_office.take_question("u1", day)
    assert front_office.take_question("u2", day)
    assert front_office.take_question("u1", date(2026, 10, 29))


def test_greeting_uses_first_name_and_time_of_day():
    assert front_office.greeting("Tommy Bruggeman", datetime(2026, 10, 28, 9)) == "Morning, Tommy."
    assert front_office.greeting("", datetime(2026, 10, 28, 20)) == "Evening, there."


def test_owner_from_context():
    owner = front_office.owner_from({"league_id": "L", "team_id": "t1", "season": 2026, "owner_name": "Tommy", "team_name": None, "role": None}, SimpleNamespace(id="u1"))
    assert owner.team_name == "Tommy" and owner.role == "owner" and owner.league_team_id == "t1"


def test_report_card_shapes_graded_calls_and_lessons():
    client = FakeClient({
        "league_ai_predictions": [
            {"kind": "start_sit", "season": 2026, "week": 7, "pick": [{"name": "Bijan Robinson"}], "over": [{"name": "Kyle Monangai"}], "correct": True,
             "pick_points": 21.4, "over_points": 9.0, "status": "graded", "diagnosis": "Volume held up."},
            {"kind": "start_sit", "season": 2026, "week": 8, "pick": [{"name": "A"}], "over": [{"name": "B"}], "status": "open"},
        ],
        "league_ai_lessons": [{"lesson": "Trust the snap share.", "evidence_count": 3}],
    })
    card = front_office.report_card(client, OWNER, 8)
    assert card["open_calls"] == 1
    assert card["recent"][0]["call"] == "start Bijan Robinson over Kyle Monangai" and card["recent"][0]["right"] and card["recent"][0]["points"] == "21.4 vs 9.0"
    assert card["lessons"] == [{"lesson": "Trust the snap share.", "seen": 3}]


def test_conversation_drops_the_old_opening_line():
    client = FakeClient({"league_ai_conversations": [{"id": "c1", "title": "Am I a contender?", "messages": [
        {"role": "assistant", "content": "Hi, I'm **Coach Condor** ..."},
        {"role": "user", "content": "Am I a contender?"},
        {"role": "assistant", "content": "Yes."},
    ]}]})
    convo = front_office.conversation(client, OWNER, "c1")
    assert [m["content"] for m in convo["messages"]] == ["Am I a contender?", "Yes."]
    assert front_office.conversation(FakeClient({}), OWNER, "nope") is None


def test_stream_events_name_the_lookup_and_pass_text_through():
    assert front_office.stream_event("tool", "get_injury_report") == {"e": "status", "text": "Checking the injury report"}
    assert front_office.stream_event("tool", "something_new") == {"e": "status", "text": "Checking the data"}
    assert front_office.stream_event("text", "Yes") == {"e": "text", "d": "Yes"}
    assert front_office.stream_event("text", "") is None
    assert front_office.stream_event("reset", None) == {"e": "reset"}


def test_ask_stream_ends_with_done(monkeypatch):
    def fake_ask(session, question, conversation_id, on_event=None, inbox=None):
        on_event("tool", "find_trade_targets")
        on_event("text", "Three fits.")
        return {"ok": True, "text": "Three fits.", "conversation_id": "c1"}

    monkeypatch.setattr(front_office, "ask", fake_ask)
    import json
    events = [json.loads(line) for line in front_office.ask_stream(None, "Find me a trade", None)]
    assert [e["e"] for e in events] == ["status", "status", "text", "done"]
    assert events[1]["text"].startswith("Scanning every roster") and events[-1]["conversation_id"] == "c1"


def test_gm_tools_gives_the_website_the_same_odds_and_matchup_tools(monkeypatch):
    seen = {}

    def fake_chat_tools(client, owner, **kw):
        seen.update(kw)
        return object()

    monkeypatch.setattr(front_office.runtime, "chat_tools", fake_chat_tools)
    monkeypatch.setattr(front_office.odds_trial, "watch", lambda tools: "watched")
    session = SimpleNamespace(owner=SimpleNamespace(league_id="L"), client=None, sleeper_id=None, rules=lambda: {}, league={}, week=lambda: 8,
                              positions=lambda: [], playoff_start=lambda: 15, state=lambda: {"roster": ["from session"]})
    tools, watched = front_office.gm_tools(session, {"week": 8}, question=lambda: "q", conversation_id=lambda: None, state={"roster": ["from website"]})
    assert watched == "watched"
    assert seen["state"] == {"roster": ["from website"]}
    assert callable(seen["odds"]) and [t.name for t in seen["extra"]] == ["get_matchup_odds", "get_lineups"]
    front_office.gm_tools(session, None, question=lambda: "q", conversation_id=lambda: None)
    assert seen["odds"] is None and seen["extra"] is None and seen["state"] == {"roster": ["from session"]}


def test_resume_replays_the_answer_after_the_phone_drops_off(monkeypatch):
    import json
    import threading
    from types import SimpleNamespace

    gate = threading.Event()

    def fake_ask(session, question, conversation_id, on_event=None, inbox=None):
        on_event("tool", "find_trade_targets")
        gate.wait(5)  # the phone has gone away; the answer keeps going
        on_event("text", "Three fits.")
        return {"ok": True, "text": "Three fits.", "conversation_id": "c9"}

    monkeypatch.setattr(front_office, "ask", fake_ask)
    session = SimpleNamespace(owner=SimpleNamespace(user_id="u-resume"))
    first = front_office.ask_stream(session, "Find me a trade", None)
    assert json.loads(next(first))["e"] == "status"  # then the app closes
    gate.set()
    events = [json.loads(line) for line in front_office.resume_stream(session)]
    assert events[0] == {"e": "job", "question": "Find me a trade", "conversation_id": None}
    assert events[-1]["e"] == "done" and events[-1]["text"] == "Three fits." and events[-1]["conversation_id"] == "c9"


def test_resume_with_nothing_running_says_none():
    import json
    from types import SimpleNamespace

    session = SimpleNamespace(owner=SimpleNamespace(user_id="u-never-asked"))
    assert [json.loads(line) for line in front_office.resume_stream(session)] == [{"e": "none"}]


def test_typing_more_mid_answer_goes_into_that_answer(monkeypatch):
    import json
    import threading
    from types import SimpleNamespace

    gate = threading.Event()
    seen = {}

    def fake_ask(session, question, conversation_id, on_event=None, inbox=None):
        gate.wait(5)
        seen["added"] = inbox(True)
        seen["after"] = inbox(True)  # nothing more: the inbox closes
        return {"ok": True, "text": "Done.", "conversation_id": "c1"}

    monkeypatch.setattr(front_office, "ask", fake_ask)
    session = SimpleNamespace(owner=SimpleNamespace(user_id="u-tell"))
    stream = front_office.ask_stream(session, "Start Kelce?", None)
    assert front_office.tell(session, "His ankle is hurt") is True
    gate.set()
    events = [json.loads(line) for line in stream]
    assert {"e": "added", "text": "His ankle is hurt"} in events and events[-1]["e"] == "done"
    assert seen == {"added": ["His ankle is hurt"], "after": []}
    assert front_office.tell(session, "Too late") is False  # finished: the app sends it as a new question


def test_get_lineups_tool_shows_every_set_lineup(monkeypatch):
    from api import projections, sleeper
    front_office.clear()
    teams = [{"id": "t1", "owner_name": "Tommy", "sleeper_roster_id": 1}, {"id": "t2", "owner_name": "Grady", "sleeper_roster_id": 2}]
    players = [{"sleeper_id": "qb1", "full_name": "Josh Allen", "position": "QB", "team": "BUF"},
               {"sleeper_id": "qb2", "full_name": "Fernando Mendoza", "position": "QB", "team": "TEN"},
               {"sleeper_id": "qb3", "full_name": "Other QB", "position": "QB", "team": "KC"}]
    roster = [{"league_team_id": "t1", "sleeper_player_id": "qb1", "player_name": "Josh Allen"},
              {"league_team_id": "t1", "sleeper_player_id": "qb2", "player_name": "Fernando Mendoza", "roster_designation": "taxi"},
              {"league_team_id": "t2", "sleeper_player_id": "qb3", "player_name": "Other QB"}]
    tables = {"league_teams": teams, "nfl_players": players, "league_app_transactions": [{"enabled": False}]}
    session = SimpleNamespace(owner=SimpleNamespace(league_id="L", season=2026), client=FakeClient(tables), sleeper_id="S", positions=lambda: ["QB", "BN", "TAXI"])
    monkeypatch.setattr(sleeper, "get", lambda path, ttl=0: [{"roster_id": 1, "matchup_id": 4, "starters": ["qb2"]},
                                                              {"roster_id": 2, "matchup_id": 4, "starters": ["qb3"]}])
    monkeypatch.setattr(projections, "week_projections", lambda *a, **k: {"qb1": 22.0, "qb2": 11.3, "qb3": 15.0})
    tool = front_office.lineups_tool(session, {"week": 10, "bye_teams": []}, {"roster": roster})
    out = tool.fn()
    assert [t["owner"] for t in out["teams"]] == ["Tommy", "Grady"] and out["teams"][0]["opponent"] == "Grady"
    mine = tool.fn(owner="tom")["teams"]
    assert len(mine) == 1
    starter = mine[0]["starters"][0]
    assert starter["player"] == "Fernando Mendoza" and "taxi" in starter["flags"]
    assert mine[0]["better_bench_options"][0]["start"] == "Josh Allen"
    assert mine[0]["cant_play_taxi_ir"] == ["Fernando Mendoza (taxi)"]

    # Once the league runs its own weeks the app's saved lineup wins over Sleeper's.
    front_office.clear()
    tables["league_app_transactions"] = [{"enabled": True, "own_from_week": 10}]
    tables["league_lineups"] = [{"league_team_id": "t1", "week": 10, "slots": [{"slot": "QB", "player_id": "qb1"}]}]
    out = front_office.lineups_tool(session, {"week": 10, "bye_teams": []}, {"roster": roster}).fn(owner="Tommy")
    assert out["teams"][0]["starters"][0]["player"] == "Josh Allen" and out["teams"][0]["better_bench_options"] == []
