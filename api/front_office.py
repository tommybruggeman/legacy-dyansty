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
from league_ai import matchup_odds, odds_trial, playoff_odds
from league_ai.front_office import lineup_moves, load_brief, merge_brief, rotating_prompts, week_context
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

    def odds_model(self, fo: dict[str, Any] | None = None) -> playoff_odds.OddsModel | None:
        """Results so far, the rest of the schedule and each team's expected points, shared by the page and the GM."""
        o, sid = self.owner, self.sleeper_id
        fo = fo or self.front_office()
        week, start = int(fo.get("week") or 0), int(fo.get("playoff_start_week") or 15)
        if not sid or not week or not fo.get("rankings"):
            return None
        means = {str(r["owner"]): float(r.get("now_score") or 0) for r in fo["rankings"]}
        owners = dict(fo.get("roster_to_owner") or {})

        def build() -> playoff_odds.OddsModel:
            schedule = lambda weeks: _cached(("schedule", sid, weeks.start, weeks.stop), 12 * 3600,  # noqa: E731
                                             lambda: playoff_odds.sleeper_schedule(sid, weeks, owners))
            return playoff_odds.OddsModel(playoff_odds.load_rows(self.client, o.league_id, o.season), means, week, start, schedule)
        return _cached(("odds_model", o.league_id, o.season, week), 1800, build)

    def odds_summary(self, fo: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Playoff odds for every team; also saves this week's odds (playoff and matchup) so they can be graded later."""
        o = self.owner
        fo = fo or self.front_office()
        try:
            model = self.odds_model(fo)
            if not model:
                return None

            def build() -> dict[str, Any]:
                out = model.summary()
                week = self.week_view(fo)
                matchup_odds.save_history(self.client, matchup_odds.history_rows(o.league_id, o.season, int(fo.get("week") or 0),
                                                                                  week.get("wins") or {}, model.odds()))
                return out
            return _cached(("odds", o.league_id, o.season, fo.get("week")), 1800, build)
        except Exception as exc:
            print(f"APP_PLAYOFF_ODDS_ERROR {type(exc).__name__}: {exc}", flush=True)
            return None

    def week_view(self, fo: dict[str, Any]) -> dict[str, Any]:
        """This week's win odds for every game, with the spread tuned on the league's graded history."""
        o, sid, week = self.owner, self.sleeper_id, int(fo.get("week") or 0)
        model = self.odds_model(fo)
        if not model or not sid or week >= model.playoff_start:
            return {}

        def build() -> dict[str, Any]:
            games = playoff_odds.sleeper_schedule(sid, range(week, week + 1), dict(fo.get("roster_to_owner") or {}))[0]
            results = [{**r, "season": o.season} for r in model.rows]
            scale = matchup_odds.fit_scale(matchup_odds.graded_games(matchup_odds.load_history(self.client, o.league_id, o.season), results))
            return {"wins": matchup_odds.week_odds(fo.get("rankings") or [], [(model.name(a), model.name(b)) for a, b in games], scale), "scale": scale}
        return _cached(("week_view", o.league_id, o.season, week), 1800, build)

    def my_week(self, fo: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """(my ranking row, my win odds row) for this week."""
        o = self.owner
        mine = next((r for r in fo.get("rankings") or [] if str(r.get("team_id")) == o.league_team_id), None)
        try:
            wins = self.week_view(fo).get("wins") or {}
        except Exception as exc:
            print(f"APP_WEEK_ODDS_ERROR {type(exc).__name__}: {exc}", flush=True)
            wins = {}
        return mine, next((v for k, v in wins.items() if k.casefold() == o.owner_name.casefold()), None)

    def moves(self, fo: dict[str, Any]) -> list[dict[str, str]]:
        """This week's lineup changes: the app's best lineup (with risk-aware swaps for this matchup) against the starters set in Sleeper now."""
        o, sid, week, rid = self.owner, self.sleeper_id, fo.get("week"), fo.get("my_roster_id")
        mine, win = self.my_week(fo)
        if not sid or not week or rid is None or not mine or "week_lineup" not in mine:
            return []
        best = [dict(p) for p in mine["week_lineup"]]
        if win:
            swaps, _, _ = matchup_odds.risk_swaps(best, mine.get("week_bench_detail") or [], win["opp_mu"], win["opp_var"],
                                                  scale=(self.week_view(fo) or {}).get("scale", 1.0))
            for sw in swaps:
                best = [{**sw["in"], "slot": sw["slot"], "note": sw["why"]} if p["id"] == sw["out"]["id"] else p for p in best]
        from api import sleeper
        try:
            rows = sleeper.get(f"league/{sid}/matchups/{week}", ttl=300) or []
        except Exception:
            return []
        current = next((r.get("starters") for r in rows if r.get("roster_id") == rid), None)
        from services.team_roster_state import state_roster
        names = {str(r.get("sleeper_player_id") or r.get("player_id") or ""): str(r.get("player_name") or "") for r in state_roster(self.state())}
        return lineup_moves(best, current, on_bye=mine.get("on_bye_ids") or [], names=names)


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


def _gm_odds(session: Session, fo: dict[str, Any]) -> playoff_odds.OddsModel | None:
    model = session.odds_model(fo)
    if model is not None:
        try:
            model.week_wins = session.week_view(fo).get("wins") or {}
        except Exception:
            pass
    return model


def matchup_tool(session: Session, fo: dict[str, Any]) -> Any:
    from league_ai.tools import ToolSpec

    def run() -> dict[str, Any]:
        mine, win = session.my_week(fo)
        if not mine or not win:
            return {"error": "no matchup odds this week (bye, playoffs not set, or lineups not loaded)"}
        swaps, before, after = matchup_odds.risk_swaps(mine["week_lineup"], mine.get("week_bench_detail") or [], win["opp_mu"], win["opp_var"],
                                                       scale=session.week_view(fo).get("scale", 1.0))
        return {"opponent": win["opp"], "win_chance": f"{round(100 * before)}%", "your_expected_points": win["mu"], "their_expected_points": win["opp_mu"],
                "risk_swaps": [{"start": sw["in"]["name"], "over": sw["out"]["name"], "slot": sw["slot"], "why": sw["why"],
                                "win_chance_gain": f"+{round(100 * sw['gain'])}"} for sw in swaps],
                "win_chance_with_swaps": f"{round(100 * after)}%",
                "how_to_use": "Win chance compares both best lineups (expected points and how much each starter swings week to week). A risk swap is a close "
                              "call where the steadier player (as the favourite) or the boom-or-bust one (as the underdog) wins more often, even with a "
                              "slightly lower average. Mention it only for close calls; never bench a clearly better player for variance."}
    return ToolSpec("get_matchup_odds", "This owner's chance to win this week's matchup and any risk-aware start/sit swaps (steadier players as the favourite, "
                    "boom-or-bust as the underdog). Use for start/sit and 'will I win this week' questions.", {"type": "object", "properties": {}}, run)


def _pct_word(note: str | None) -> str:
    """'-35% vs league median' -> '-35%'; anything without a percentage -> ''."""
    first = (note or "").split(" ")[0]
    return first if first.endswith("%") else ""


def _my_odds(odds: dict[str, Any] | None, owner: str) -> dict[str, Any] | None:
    name = next((t for t in (odds or {}).get("odds", {}) if t.casefold() == owner.casefold()), None)
    if not odds or name is None:
        return None
    return {"pct": odds["odds"][name], "change": odds["change"].get(name), "weeks_left": odds["weeks_left"], "spots": odds["spots"]}


def home(session: Session) -> dict[str, Any]:
    """The Front Office screen before you ask anything: tiles, this week's brief, starters and the report card."""
    o = session.owner
    fo = session.front_office()
    week = int(fo.get("week") or 0)
    saved = load_brief(session.client, o.league_id, o.user_id, o.season, week)
    mine, win = session.my_week(fo)
    odds = session.odds_summary(fo)
    try:
        moves = session.moves(fo)
    except Exception as exc:
        print(f"APP_MOVES_ERROR {type(exc).__name__}: {exc}", flush=True)
        moves = []
    return {
        "greeting": greeting(o.owner_name),
        "ai_ready": ai_ready(),
        "week": week,
        "season": o.season,
        "tiles": fo.get("tiles") or [],
        # The three tiles on the Front Office screen: weakest spot, playoff odds, power rank.
        "header": {
            "weakest": {"slot": mine.get("weakest_slot"), "note": _pct_word(mine.get("weakest_note"))} if mine and mine.get("weakest_slot") else None,
            "odds": _my_odds(odds, o.owner_name),
            "power": {"rank": mine.get("now_rank"), "move": fo.get("power_movement")} if mine else None,
        },
        "moves": moves,
        "win": {"pct": round(100 * win["win"]), "opponent": win["opp"]} if win else None,
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
    state = session.state()
    if session.sleeper_id:
        # Players on a Sleeper roster without a contract row yet (fresh rookie picks) aren't free agents.
        try:
            from api import league as league_api
            names = league_api.roster_names(client, o.league_id, session.sleeper_id)
            state = {**state, "sleeper_rostered": {pid: names.get(rid, f"Roster {rid}") for pid, rid in league_api.sleeper_owners(session.sleeper_id).items()}}
        except Exception:
            pass
    tools = runtime.chat_tools(client, o, state=state, rules=session.rules(), league=session.league, week=session.week(),
                               positions=session.positions, playoff_week=session.playoff_start,
                               question=lambda: question, conversation_id=lambda: conversation_id,
                               odds=(lambda: _gm_odds(session, fo)) if fo else None, extra=[matchup_tool(session, fo)] if fo else None)
    seen = odds_trial.watch(tools)
    turn = runtime.chat_turn(client, o, question=question, history=history, conversation_id=conversation_id, pack=session.pack(), tools=tools,
                             on_event=on_event, week_context=week_context(fo, o.owner_name))
    if turn.result.ok:
        odds_trial.maybe_run(client, league_id=o.league_id, user_id=o.user_id, conversation_id=turn.conversation_id, question=question,
                             answer=turn.result.text, seen=seen, sdk_factory=lambda: runtime.ClaudeClient()._client())
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
