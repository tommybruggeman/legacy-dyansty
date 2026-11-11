"""The Assistant GM without Streamlit: pack, tools, Front Office data and one chat turn.

The website (league_ai.page_glue) wraps these in st.cache_data; the iOS app's
API (api/front_office.py) wraps them in its own small cache. Both callers pass
the asker's authenticated Supabase client, so database rules still apply.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from dataclasses import replace

from league_ai import fact_check
from league_ai.client import ClaudeClient
from league_ai.config import load_config
from league_ai.context_pack import Asker
from league_ai.front_office import (
    build_tiles, current_opponent, data_brief, head_to_head, nfl_week, power_movement, power_rankings, season_table, snapshot_power,
)
from league_ai.learning import Ledger, learning_pack_lines
from league_ai.memory import Memory
from league_ai.nfl_data import add_weather, bye_weeks, current_week_from_games, data_freshness, defense_vs_position, season_games, week_matchups
from league_ai.roster_moves import rules_from
from league_ai.routing import effort_for, topic as question_topic
from league_ai.service import AnswerResult, LeagueDataSources, answer, build_pack
from league_ai.settings_store import apply_sleeper_scoring, fetch_sleeper_scoring, load_settings
from league_ai.team_context import team_context
from league_ai.tools import ToolRegistry
from league_ai.tools.history import history_tools, pack_history_summary
from league_ai.tools.nfl import NflData, build_registry
from league_ai.usage import record as record_usage, row_from_trace, usage_row


# What the chat shows while a tool runs, keyed by tool name.
TOOL_STATUS = {
    "get_player_profile": "Pulling up the player profile",
    "get_player_stats": "Checking the stats",
    "compare_players": "Comparing players",
    "get_injury_report": "Checking the injury report",
    "get_prospects": "Scouting the prospects",
    "get_market_values": "Checking market values",
    "search_players": "Searching the player pool",
    "simulate_trade": "Running the trade through the cap",
    "evaluate_trade": "Weighing the trade from both sides",
    "get_league_history": "Digging through league history",
    "get_waiver_prices": "Looking up what pickups cost here",
    "get_trade_tendencies": "Checking who trades with whom",
    "get_matchup_history": "Checking matchup history",
    "remember": "Making a note",
    "forget": "Updating my notes",
    "log_prediction": "Logging this call so I can grade it after the games",
    "get_my_track_record": "Checking how my past calls graded",
    "find_trade_targets": "Scanning every roster for trades that work for both sides",
    "plan_roster_moves": "Checking cuts, taxi, IR and rookie options under your league's rules",
    "get_owner_trade_profile": "Checking how that owner trades",
    "record_trade_response": "Noting how they answered",
    "get_schedule_outlook": "Grading the rest of the schedule",
    "get_return_outlook": "Comparing his return with past players who had the same injury",
    "get_position_room": "Checking how his team splits the work",
    "get_nfl_moves": "Checking the latest NFL trades and signings",
    "get_playoff_odds": "Simulating the rest of the season",
    "get_pickup_odds": "Simulating the season with him on your roster",
    "get_matchup_odds": "Working out your odds to win this week",
}


@dataclass(frozen=True)
class Owner:
    """Who is asking, as the pack and tools need it."""
    user_id: str
    league_id: str
    league_team_id: str
    season: int
    owner_name: str
    team_name: str
    role: str = "owner"

    def asker(self) -> Asker:
        return Asker(user_id=self.user_id, league_id=self.league_id, league_team_id=self.league_team_id,
                     owner_name=self.owner_name, team_name=self.team_name, role=self.role)

    def memory(self, client: Any) -> Memory:
        return Memory(client, league_id=self.league_id, user_id=self.user_id, league_team_id=self.league_team_id, owner_name=self.owner_name)


def league_row(client: Any, league_id: str) -> dict:
    try:
        rows = client.table("leagues").select("id, name, sleeper_league_id").eq("id", league_id).limit(1).execute().data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def standings_loader(sleeper_league_id: str | None) -> Callable[[], list[dict[str, Any]]] | None:
    if not sleeper_league_id:
        return None

    def load() -> list[dict[str, Any]]:
        from services.app_context import build_standings_from_sleeper

        df = build_standings_from_sleeper(sleeper_league_id)
        if df is None or df.empty:
            return []
        df = df.sort_values(["Standing Points", "PF"], ascending=[False, False]).reset_index(drop=True)
        df["rank"] = df.index + 1
        return df.to_dict("records")

    return load


def standings(client: Any, league_id: str, season: int, sleeper_league_id: str | None) -> list[dict]:
    table = season_table(client, league_id, season)
    if table:
        return table
    loader = standings_loader(sleeper_league_id)
    try:
        return loader() if loader else []
    except Exception as exc:
        print(f"FRONT_OFFICE_STANDINGS_ERROR {type(exc).__name__}: {exc}", flush=True)
        return []


def roster_positions(client: Any, league_id: str, sleeper_league_id: str | None) -> list[str]:
    s = load_settings(client, league_id)
    if not s.roster_positions and sleeper_league_id:
        try:
            apply_sleeper_scoring(s, fetch_sleeper_scoring(sleeper_league_id))
        except Exception:
            pass
    return list(s.roster_positions or [])


def current_week(client: Any, season: int) -> int:
    """This NFL week from our own schedule (nfl_games): the earliest week with an unplayed game, the last week once the
    regular season is over. Sleeper's state endpoint only when the schedule isn't loaded."""
    games = season_games(client, int(season)) if client is not None else []
    if games:
        week = current_week_from_games(games)
        return int(week) if week else max(int(g["week"]) for g in games if g.get("week") is not None)
    return nfl_week()


def playoff_start(sleeper_league_id: str | None, client: Any = None, league_id: str | None = None) -> int | None:
    """First fantasy playoff week: our own playoff format (Settings > League AI), Sleeper's league settings only for a
    caller that has no league to read it from."""
    if client is not None and league_id:
        try:
            return int(load_settings(client, league_id).playoff_week_start or 15)
        except Exception:
            pass
    if not sleeper_league_id:
        return None
    try:
        value = (fetch_sleeper_scoring(sleeper_league_id).get("league_settings") or {}).get("playoff_week_start")
        return int(value) if value else None
    except Exception:
        return None


def season_mode(sleeper_league_id: str | None, week: int, playoff_week: int | None, client: Any = None, season: int | None = None) -> tuple[bool, float]:
    """(offseason?, share of the regular season still to play). The offseason starts once the league's final game is
    over and runs until the next season starts. Ours: every regular-season NFL game is final in our schedule (the
    fantasy playoffs end by the last regular-season week). Sleeper's league status only when our schedule isn't loaded."""
    games = season_games(client, int(season)) if client is not None and season else []
    if games:
        offseason = current_week_from_games(games) is None
        end = (playoff_week or 15) - 1
        return offseason, (0.0 if offseason else max(0.0, min(1.0, (end - week + 1) / max(end, 1))))
    status = None
    if sleeper_league_id:
        try:
            status = fetch_sleeper_scoring(sleeper_league_id).get("status")
        except Exception:
            status = None
    offseason = status is not None and status != "in_season"
    end = (playoff_week or 15) - 1
    return offseason, (0.0 if offseason else max(0.0, min(1.0, (end - week + 1) / max(end, 1))))


def build_owner_pack(client: Any, owner: Owner, *, state: Mapping[str, Any], rules: Mapping[str, Any], league: Mapping[str, Any]) -> str:
    """The league pack for one owner: rules, state, standings, history, their notes and the lessons learned."""
    memory = owner.memory(client)
    sources = LeagueDataSources(
        client=client,
        league_id=owner.league_id,
        season=owner.season,
        league_name=league.get("name") or "the league",
        sleeper_league_id=league.get("sleeper_league_id"),
        league_rules=rules,
        state_loader=lambda: state,
        standings_loader=lambda: standings(client, owner.league_id, owner.season, league.get("sleeper_league_id")),
        history_loader=lambda: pack_history_summary(client, owner.league_id),
        private_memory_loader=memory.private_notes,
        league_notebook_loader=memory.league_notes,
        learning_loader=lambda: learning_pack_lines(client, owner.league_id, owner.user_id, owner.season),
        data_freshness={"league state": "live from the app database", "standings": "the app's own standings (our league rules)", **data_freshness(client)},
    )
    return build_pack(sources, owner.asker())


def _week_points(client: Any, league_id: str, season: int, week: int, players: list[Any]) -> dict[str, float]:
    """Sleeper id -> this week's calibrated projection for every rostered player ({} when it can't be made: the
    Front Office then falls back to recent form)."""
    from api.projections import week_projections

    try:
        rows = client.table("leagues").select("sleeper_league_id").eq("id", league_id).limit(1).execute().data or []
        sid = str((rows[0] if rows else {}).get("sleeper_league_id") or "")
        if not sid:
            return {}
        ids = [p.sleeper_id for p in players if p.sleeper_id]
        return week_projections(client, sid, int(season), int(week), ids, {p.sleeper_id: p.position for p in players},
                                {p.sleeper_id: p.nfl_team or "" for p in players})
    except Exception as exc:
        print(f"FO_WEEK_POINTS_ERROR {type(exc).__name__}", flush=True)
        return {}


def power_rows(client: Any, league_id: str, season: int, *, state: Mapping[str, Any], positions: list[str], actual_ppg: Mapping[str, float],
               week: int, byes: Mapping[str, int]) -> list[dict]:
    """Power rankings as plain dicts (cache friendly), with this week's lineup, matchups and byes."""
    games = add_weather(week_matchups(season_games(client, season), week), season, week) if week else {}
    ctx = team_context(client, season, week) if week else None
    if ctx:
        ctx.annotate(games)
    rankings = power_rankings(client, state, season, list(positions), dict(actual_ppg), byes=dict(byes), week=week, games=games, dvp=defense_vs_position(client, season),
                              situational=ctx.situational if ctx else None,
                              week_points=(lambda players: _week_points(client, league_id, season, week, players)) if week else None)
    snapshot_power(client, league_id, rankings)

    def bye_label(p: Any) -> str:
        return f"{p.position} {p.name} ({p.nfl_team})"

    def player_row(p: Any, slot: str | None = None) -> dict:
        # pts: this week's matchup-adjusted expectation; sd: how much he swings week to week (None = too few games)
        return {"id": p.sleeper_id, "name": p.name, "pos": p.position, "slot": slot, "matchup": p.matchup, "injury": p.injury,
                "pts": round(p.score("week_now"), 2), "sd": p.week_sd if p.week_sd is not None else p.spread}

    def week_label(p: Any) -> str:
        return f"{p.position} {p.name} ({p.score('week_now'):.1f}" + (f", {p.matchup})" if p.matchup else ")")

    return [{"team_id": tp.team_id, "owner": tp.owner, "now_rank": tp.now_rank, "now_score": tp.now_score, "dynasty_rank": tp.dynasty_rank, "dynasty_score": tp.dynasty_score,
             "weakest_slot": tp.weakest_slot, "weakest_note": tp.weakest_note, "strongest_slot": tp.strongest_slot,
             "starters": [f"{p.position} {p.name} ({p.now:.1f})" for p in tp.starters],
             "week_starters": [week_label(p) for p in tp.week_starters], "week_bench": [week_label(p) for p in tp.week_bench],
             "team_notes": ctx.team_lines([p.nfl_team for p in tp.week_starters + tp.week_bench], games) if ctx else [],
             "on_bye": [bye_label(p) for p in tp.on_bye], "on_bye_names": [p.name for p in tp.on_bye], "bye_next": [bye_label(p) for p in tp.bye_next],
             "week_lineup": [player_row(p, slot) for slot, p in zip(tp.week_slots, tp.week_starters)],
             "week_bench_detail": [player_row(p) for p in tp.week_bench],
             "on_bye_ids": [p.sleeper_id for p in tp.on_bye]} for tp in rankings]


@dataclass
class Loaders:
    """The slow, shareable reads behind the Front Office. Each caller supplies its own caching."""
    state: Callable[[], Mapping[str, Any]]
    rules: Callable[[], Mapping[str, Any]]
    positions: Callable[[], list[str]]
    standings: Callable[[], list[dict]]
    week: Callable[[], int]
    byes: Callable[[], Mapping[str, int]]
    power: Callable[[tuple[tuple[str, float], ...], int], list[dict]]
    opponent: Callable[[int, tuple[tuple[int, str], ...], int | None], dict | None]
    playoff_start: Callable[[], int | None]


def front_office_data(client: Any, owner: Owner, league: Mapping[str, Any], load: Loaders) -> dict[str, Any]:
    """Everything the Front Office header needs: tiles, rankings, opponent, week."""
    league_id, season = owner.league_id, owner.season
    state = load.state()
    rules = load.rules()
    table = load.standings()
    actual_ppg = tuple((str(r.get("Team")), float(r.get("PF Per Game") or 0)) for r in table if r.get("Games", 1))
    week = load.week()
    rankings_raw = load.power(actual_ppg, week)
    rankings = [SimpleNamespace(**r) for r in rankings_raw]
    try:
        teams = client.table("league_teams").select("id, owner_name, sleeper_roster_id").eq("league_id", league_id).execute().data or []
    except Exception:
        teams = []
    roster_to_owner = tuple((int(t["sleeper_roster_id"]), str(t["owner_name"])) for t in teams if t.get("sleeper_roster_id") is not None)
    my_roster_id = next((int(t["sleeper_roster_id"]) for t in teams if str(t["id"]) == str(owner.league_team_id) and t.get("sleeper_roster_id") is not None), None)
    opponent = load.opponent(week, roster_to_owner, my_roster_id)
    h2h = head_to_head(client, league_id, owner.owner_name, opponent["owner_name"]) if opponent else None
    movement = power_movement(client, league_id, owner.league_team_id)
    salary_cap = float(rules.get("salary_cap", 225) or 225)
    tiles = build_tiles(state=state, team_id=owner.league_team_id, owner=owner.owner_name, salary_cap=salary_cap,
                        standings=table, rankings=rankings, opponent=opponent, h2h=h2h, movement=movement)
    mine = next((r for r in rankings_raw if r["team_id"] == owner.league_team_id), None)
    instant = data_brief(client, state=state, team_id=owner.league_team_id, owner=owner.owner_name, opponent=opponent, h2h=h2h, standings=table)
    if mine and mine.get("on_bye"):
        instant["byes"] = list(mine["on_bye"])
        # bye players lead the "Injuries & lineup" card: they have to come out of the lineup too
        items = instant["injuries"]["items"]
        instant["injuries"]["items"] = ([{"player": n, "status": f"Bye · week {week}"} for n in mine.get("on_bye_names") or []] + items)[:5]
    bye_teams = sorted(t for t, w in load.byes().items() if w == week)
    return {"tiles": tiles, "data_brief": instant, "week": week, "power_movement": movement, "my_roster_id": my_roster_id, "roster_to_owner": dict(roster_to_owner), "bye_teams": bye_teams, "playoff_start_week": load.playoff_start(), "opponent": opponent, "h2h": h2h,
            "standings": table, "owner_name": owner.owner_name, "league_name": league.get("name") or "the league",
            "season": season, "rankings": rankings_raw, "weakest_slot": mine["weakest_slot"] if mine else None, "salary_cap": salary_cap}


def chat_tools(client: Any, owner: Owner, *, state: Mapping[str, Any], rules: Mapping[str, Any], league: Mapping[str, Any], week: int,
               positions: Callable[[], list[str]], playoff_week: Callable[[], int | None],
               question: Callable[[], str], conversation_id: Callable[[], str | None], odds: Callable[[], Any] | None = None,
               extra: list[Any] | None = None) -> ToolRegistry:
    """Every tool the Assistant GM can call for this owner."""
    import threading

    from league_ai import idea_log
    from league_ai.front_office import build_player_strengths, free_agent_strength
    from league_ai.tools.history import LeagueHistory
    from league_ai.trade_finder import load_league, trade_finder_tools

    nfl = NflData(client, league_state=state)
    ledger = Ledger(client, league_id=owner.league_id, user_id=owner.user_id, league_team_id=owner.league_team_id,
                    season=owner.season, week=week,
                    resolve_player=lambda name: next(iter(nfl.find_players(name, limit=1)), None),
                    conversation_id=conversation_id, question=question,
                    owner_of=lambda sid, name: (nfl.league_contract(sid, name) or {}).get("owner"))
    finder = trade_finder_tools(
        lambda: load_league(client, state, owner.season, positions(), rules.get("salary_cap", 225), owner.league_team_id, build_player_strengths,
                            league_id=owner.league_id, playoff_start=playoff_week() or 15),
        LeagueHistory(client, owner.league_id),
        outcomes=ledger.trade_outcomes, current_season=owner.season, odds=odds,
        free_agent=lambda name: next((free_agent_strength(client, r, owner.season, owner.league_team_id, owner.league_id) for r in nfl.find_players(name, limit=1)), None),
        roster_rules=lambda: (rules_from(rules, load_settings(client, owner.league_id)),
                              *season_mode(league.get("sleeper_league_id"), week, playoff_week(), client, owner.season)),
        log_ideas=lambda out: threading.Thread(target=idea_log.log, daemon=True, kwargs={
            "client": client, "league_id": owner.league_id, "user_id": owner.user_id, "conversation_id": conversation_id(),
            "question": question(), "out": out}).start(),
    )

    def week_projection(players: list[Mapping[str, Any]]) -> dict[str, float]:
        from api.projections import week_projections

        sid = league.get("sleeper_league_id")
        if not sid or not week:
            return {}
        ids = [str(p.get("sleeper_id")) for p in players if p.get("sleeper_id")]
        return week_projections(client, str(sid), int(owner.season), int(week), ids,
                                {str(p.get("sleeper_id")): str(p.get("position") or "") for p in players},
                                {str(p.get("sleeper_id")): str(p.get("team") or "") for p in players})

    return build_registry(client, league_state=state, salary_cap=rules.get("salary_cap", 225), dead_cap_pct=float(rules.get("default_dead_cap_pct", 50) or 50),
                          week_projection=week_projection,
                          extra=[*history_tools(client, owner.league_id), *owner.memory(client).tools(), *ledger.tools(), *finder, *(extra or [])])


@dataclass
class ChatTurn:
    result: AnswerResult
    conversation_id: str | None
    notes_changed: bool


def chat_turn(client: Any, owner: Owner, *, question: str, history: list[dict[str, str]], conversation_id: str | None, pack: str,
              tools: ToolRegistry, on_event: Any = None, week_context: str | None = None, inbox: Any = None) -> ChatTurn:
    """One question and answer: run Claude, save the conversation, log the cost.
    inbox(final) hands over anything the owner typed while the answer was being worked on (see ClaudeClient.run)."""
    added: list[str] = []

    def take(final: bool) -> list[str]:
        got = list(inbox(final) or [])
        added.extend(got)
        return got

    memory = owner.memory(client)
    from league_ai import owner_prefs  # answer length and contend/rebuild from the owner's Settings
    week_context = owner_prefs.with_prefs(week_context, owner_prefs.load(client, owner.user_id))
    topic = question_topic(question)
    sources = LeagueDataSources(client=client, league_id=owner.league_id, season=owner.season)
    model = pick_model(topic, conversation_id or question)
    result = answer(question=question, history=history, asker=owner.asker(), sources=sources, tools=tools,
                    client=ClaudeClient(replace(load_config(), model=model) if model else None), pack=pack, on_event=on_event,
                    week_context=week_context, turn_effort=effort_for(question), prior_facts=_prior_facts(memory, conversation_id) if history else None,
                    **({"inbox": take} if inbox else {}))
    if result.ok and fact_check.enabled():
        checked = fact_check.check(result.text, pack=result.pack or pack, evidence=result.evidence, week_context=week_context)
        if checked.ran:
            record_usage(client, usage_row(league_id=owner.league_id, user_id=owner.user_id, feature="fact_check", model=fact_check.MODEL,
                                           topic="fixed" if checked.fixed else ("flagged" if checked.problems else "ok"),
                                           conversation_id=conversation_id, latency_ms=checked.latency_ms, **checked.usage))
        if checked.fixed:
            # the owner watched the first draft stream in; swap it for the corrected one
            result.text = checked.text
            _emit_replace(on_event, checked.text)
            print(f"LEAGUE_AI_FACT_FIX problems={len(checked.problems)}", flush=True)
    if result.ok:
        full = [*history, {"role": "user", "content": question}, *({"role": "user", "content": a} for a in added),
                {"role": "assistant", "content": result.text, "facts": facts_digest(result.evidence)}]
        conversation_id = memory.save_conversation(conversation_id, full)
    record_usage(client, row_from_trace(result.trace, league_id=owner.league_id, user_id=owner.user_id, feature="chat",
                                        topic=topic, conversation_id=conversation_id))
    notes_changed = any(name in ("remember", "forget") for name in result.trace.tool_calls)
    return ChatTurn(result, conversation_id, notes_changed)


HARD_TOPICS = ("trade", "strategy")
FACTS_CHARS = 1500   # per lookup in the facts note a follow-up gets
FACTS_TOTAL = 8000


def pick_model(topic: str, key: str) -> str | None:
    """The model for this question: LEAGUE_AI_HARD_MODEL (e.g. Opus) for trade and strategy questions, for a
    LEAGUE_AI_HARD_SHARE of conversations (0 = off, 1 = all), picked by a stable hash so a conversation keeps its model
    (and its prompt cache). None = the default model."""
    import hashlib
    import os

    hard = (os.getenv("LEAGUE_AI_HARD_MODEL") or "").strip()
    try:
        share = max(0.0, min(1.0, float(os.getenv("LEAGUE_AI_HARD_SHARE") or 0)))
    except ValueError:
        share = 0.0
    if not hard or share <= 0 or topic not in HARD_TOPICS:
        return None
    bucket = int(hashlib.sha256(str(key).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return hard if bucket < share else None


def facts_digest(evidence: list[Mapping[str, Any]] | None) -> str:
    """A short note of what the answer's lookups returned, saved with the answer for the next question."""
    import json

    parts, total = [], 0
    for e in evidence or []:
        block = f"{e.get('tool')}({json.dumps(e.get('input') or {}, sort_keys=True, default=str)[:200]}): {str(e.get('result') or '')[:FACTS_CHARS]}"
        if total + len(block) > FACTS_TOTAL:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def _prior_facts(memory: Memory, conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    row = memory.get_conversation(conversation_id) or {}
    last = next((m for m in reversed(row.get("messages") or []) if m.get("role") == "assistant"), None)
    return (last or {}).get("facts") or None


def _emit_replace(on_event: Any, text: str) -> None:
    if on_event is None:
        return
    try:
        on_event("reset", None)
        on_event("text", text)
    except Exception:
        pass


__all__ = [
    "ChatTurn", "Loaders", "TOOL_STATUS", "Owner", "build_owner_pack", "bye_weeks", "chat_tools", "chat_turn", "current_opponent", "front_office_data",
    "current_week", "league_row", "nfl_week", "playoff_start", "power_rows", "roster_positions", "standings", "standings_loader",
]
