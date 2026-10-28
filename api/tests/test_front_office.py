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
    def fake_ask(session, question, conversation_id, on_event=None):
        on_event("tool", "find_trade_targets")
        on_event("text", "Three fits.")
        return {"ok": True, "text": "Three fits.", "conversation_id": "c1"}

    monkeypatch.setattr(front_office, "ask", fake_ask)
    import json
    events = [json.loads(line) for line in front_office.ask_stream(None, "Find me a trade", None)]
    assert [e["e"] for e in events] == ["status", "status", "text", "done"]
    assert events[1]["text"].startswith("Scanning every roster") and events[-1]["conversation_id"] == "c1"
