"""Front Office for the app: the Assistant GM chat, past chats, this week's brief and the report card.

It runs the same Assistant GM as the website (league_ai.runtime), with the
Anthropic key held only by this server. Slow, shared reads are cached per
server instance; anything about one owner is keyed by their user id.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from league_ai import runtime
from league_ai.config import load_config
from league_ai.front_office import load_brief, merge_brief, rotating_prompts, week_context
from league_ai.learning import Ledger
from league_ai.service import load_league_rules
from services.team_roster_state import load_team_state

# Questions per owner per day. A normal answer costs about 10 cents, so this
# caps a runaway day near $3 per owner. Counted per server instance.
DAILY_QUESTIONS = 30
LEAGUE_TZ = ZoneInfo("America/Denver")

_cache: dict[Any, tuple[float, Any]] = {}
_asked: dict[tuple[str, str], int] = {}


def _cached(key: Any, seconds: int, load: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = load()
    _cache[key] = (now + seconds, value)
    return value


def clear() -> None:
    _cache.clear()
    _asked.clear()


def ai_ready() -> bool:
    return load_config().ready


def owner_from(ctx: dict[str, Any], user: Any) -> runtime.Owner:
    owner_name = ctx.get("owner_name") or ctx.get("team_name") or "Owner"
    return runtime.Owner(user_id=str(user.id), league_id=str(ctx["league_id"]), league_team_id=str(ctx["team_id"]), season=int(ctx["season"]),
                         owner_name=owner_name, team_name=ctx.get("team_name") or owner_name, role=ctx.get("role") or "owner")


def take_question(user_id: str, today: date | None = None) -> bool:
    """Count one question against today's limit; False once it's used up."""
    day = (today or datetime.now(LEAGUE_TZ).date()).isoformat()
    key = (user_id, day)
    if _asked.get(key, 0) >= DAILY_QUESTIONS:
        return False
    _asked[key] = _asked.get(key, 0) + 1
    return True


class Session:
    """One owner's view of the league for a request, with the slow reads cached."""

    def __init__(self, client: Any, owner: runtime.Owner, sleeper_league_id: str | None):
        self.client = client
        self.owner = owner
        self.league = {**runtime.league_row(client, owner.league_id), "sleeper_league_id": sleeper_league_id}
        self.sleeper_id = sleeper_league_id
        o = owner
        # League-wide reads are the same for every owner; RLS lets every member read them.
        self.state = lambda: _cached(("state", o.league_id, o.season), 300, lambda: load_team_state(client, o.league_id, o.season))
        self.rules = lambda: _cached(("rules", o.league_id), 300, lambda: load_league_rules(client, o.league_id))
        self.positions = lambda: _cached(("positions", o.league_id), 1800, lambda: runtime.roster_positions(client, o.league_id, sleeper_league_id))
        self.week = lambda: _cached("week", 3600, runtime.nfl_week)
        self.playoff_start = lambda: _cached(("playoffs", sleeper_league_id), 12 * 3600, lambda: runtime.playoff_start(sleeper_league_id))

    def pack(self) -> str:
        o = self.owner
        return _cached(("pack", o.user_id, o.season), 300,
                       lambda: runtime.build_owner_pack(self.client, o, state=self.state(), rules=self.rules(), league=self.league))

    def forget_pack(self) -> None:
        _cache.pop(("pack", self.owner.user_id, self.owner.season), None)

    def loaders(self) -> runtime.Loaders:
        o, client, sid = self.owner, self.client, self.sleeper_id
        byes = lambda: _cached(("byes", o.season), 12 * 3600, lambda: runtime.bye_weeks(client, o.season))  # noqa: E731
        return runtime.Loaders(
            state=self.state,
            rules=self.rules,
            positions=self.positions,
            standings=lambda: _cached(("standings", o.league_id, o.season), 600, lambda: runtime.standings(client, o.league_id, o.season, sid)),
            week=self.week,
            byes=byes,
            power=lambda actual_ppg, week: _cached(("power", o.league_id, o.season, week), 6 * 3600, lambda: runtime.power_rows(
                client, o.league_id, o.season, state=self.state(), positions=self.positions(), actual_ppg=dict(actual_ppg), week=week, byes=byes())),
            opponent=lambda week, roster_to_owner, my_roster_id: _cached(("opponent", sid, week, my_roster_id), 1800,
                                                                         lambda: runtime.current_opponent(sid, week, dict(roster_to_owner), my_roster_id)),
            playoff_start=self.playoff_start,
        )

    def front_office(self) -> dict[str, Any]:
        o = self.owner
        return _cached(("fo", o.user_id, o.season), 600, lambda: runtime.front_office_data(self.client, o, self.league, self.loaders()))


def report_card(client: Any, owner: runtime.Owner, week: int) -> dict[str, Any]:
    ledger = Ledger(client, league_id=owner.league_id, user_id=owner.user_id, league_team_id=owner.league_team_id, season=owner.season, week=week)
    rec = ledger.track_record(limit=6)
    try:
        lessons = (client.table("league_ai_lessons").select("lesson, evidence_count").eq("league_id", owner.league_id).eq("active", True)
                   .order("evidence_count", desc=True).limit(6).execute().data or [])
    except Exception:
        lessons = []
    return {
        "record": rec.get("record") or {},
        "open_calls": int(rec.get("open_calls") or 0),
        "recent": [{"when": r["when"], "call": r["call"], "right": r["result"] == "right", "points": r["points"], "why": r.get("why")} for r in rec.get("recent") or []],
        "lessons": [{"lesson": l["lesson"], "seen": int(l.get("evidence_count") or 1)} for l in lessons],
    }


def greeting(owner_name: str, now: datetime | None = None) -> str:
    hour = (now or datetime.now(LEAGUE_TZ)).hour
    part = "Morning" if 4 <= hour < 12 else "Afternoon" if hour < 17 else "Evening"
    return f"{part}, {(owner_name or '').split(' ')[0] or 'there'}."


def home(session: Session) -> dict[str, Any]:
    """The Front Office screen before you ask anything: tiles, this week's brief, starters and the report card."""
    o = session.owner
    fo = session.front_office()
    week = int(fo.get("week") or 0)
    saved = load_brief(session.client, o.league_id, o.user_id, o.season, week)
    return {
        "greeting": greeting(o.owner_name),
        "ai_ready": ai_ready(),
        "week": week,
        "season": o.season,
        "tiles": fo.get("tiles") or [],
        "brief": merge_brief(fo.get("data_brief") or {}, saved),
        "prompts": rotating_prompts(week=week),
        "report_card": report_card(session.client, o, week),
        "sleeper_league_id": session.sleeper_id,
    }


def ask(session: Session, question: str, conversation_id: str | None, on_event: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
    """One chat turn. Returns the answer, or a friendly error the app can show."""
    o, client = session.owner, session.client
    memory = o.memory(client)
    history: list[dict[str, str]] = []
    if conversation_id:
        row = memory.get_conversation(conversation_id)
        if row is None:
            conversation_id = None
        else:
            history = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in row.get("messages") or []]
    try:
        fo = session.front_office()
    except Exception as exc:
        print(f"APP_FRONT_OFFICE_ERROR {type(exc).__name__}: {exc}", flush=True)
        fo = None
    tools = runtime.chat_tools(client, o, state=session.state(), rules=session.rules(), league=session.league, week=session.week(),
                               positions=session.positions, playoff_week=session.playoff_start,
                               question=lambda: question, conversation_id=lambda: conversation_id)
    turn = runtime.chat_turn(client, o, question=question, history=history, conversation_id=conversation_id, pack=session.pack(), tools=tools,
                             on_event=on_event, week_context=week_context(fo, o.owner_name))
    if turn.notes_changed:
        session.forget_pack()  # new notes show up in the next answer
    result = turn.result
    print(result.trace_line(), flush=True)
    return {"ok": result.ok, "text": result.text if result.ok else result.human_error, "conversation_id": turn.conversation_id}


def stream_event(kind: str, payload: Any) -> dict[str, Any] | None:
    """A model event as the app sees it: a status line for each lookup, text as it arrives."""
    if kind == "tool":
        return {"e": "status", "text": runtime.TOOL_STATUS.get(str(payload), "Checking the data")}
    if kind == "text":
        return {"e": "text", "d": str(payload or "")} if payload else None
    if kind == "reset":
        return {"e": "reset"}  # text before a lookup is replaced by the next round's answer
    if kind == "round":
        return {"e": "round"}
    return None


def ask_stream(session: Session, question: str, conversation_id: str | None) -> Iterator[str]:
    """The same turn as ask(), as newline-delimited JSON events ending with a 'done' event."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def on_event(kind: str, payload: Any) -> None:
        event = stream_event(kind, payload)
        if event:
            events.put(event)

    def run() -> None:
        try:
            events.put({"e": "done", **ask(session, question, conversation_id, on_event)})
        except Exception as exc:
            print(f"APP_CHAT_ERROR {type(exc).__name__}: {exc}", flush=True)
            events.put({"e": "done", "ok": False, "text": "Something went wrong. Try again.", "conversation_id": conversation_id})
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()
    yield json.dumps({"e": "status", "text": "Reading your roster"}) + "\n"
    while (event := events.get()) is not None:
        yield json.dumps(event) + "\n"


def conversations(client: Any, owner: runtime.Owner) -> list[dict[str, Any]]:
    return [{"id": str(r["id"]), "title": r.get("title") or "Conversation", "updated_at": r.get("updated_at")}
            for r in owner.memory(client).list_conversations()]


def conversation(client: Any, owner: runtime.Owner, conversation_id: str) -> dict[str, Any] | None:
    row = owner.memory(client).get_conversation(conversation_id)
    if not row:
        return None
    messages = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in row.get("messages") or []]
    # The website's old opening line isn't part of the conversation.
    messages = [m for m in messages if not str(m["content"]).startswith("Hi, I'm **Coach Condor**")]
    return {"id": str(row["id"]), "title": row.get("title") or "Conversation", "messages": messages}
