"""Streamlit-side helpers that connect the GM Assistant page to League AI."""
from __future__ import annotations

from typing import Any

import streamlit as st

from auth import auth_client
from league_ai.client import ClaudeClient
from league_ai.config import load_config
from league_ai.context_pack import Asker
from league_ai.nfl_data import data_freshness
from league_ai.service import AnswerResult, LeagueDataSources, answer, build_pack, load_league_rules
from league_ai.tools.nfl import build_registry
from league_ai.tools.history import history_tools, pack_history_summary
from league_ai.memory import Memory
from league_ai.learning import Ledger, learning_pack_lines
from league_ai.routing import effort_for, topic as question_topic
from league_ai.usage import record as record_usage, row_from_trace
from league_ai.tools.nfl import NflData
from services.team_roster_state import load_team_state
from league_ai.tools import ToolRegistry

MODE_KEY = "gm_assistant_mode"
MEMORY_EPOCH_KEY = "league_ai_memory_epoch"
CONVERSATION_KEY = "league_ai_conversation_id"
LEAGUE_AI = "League AI"
GM_BRAIN = "GM Brain (legacy)"


def league_ai_available() -> tuple[bool, str]:
    cfg = load_config()
    if not cfg.enabled:
        return False, "League AI is turned off (LEAGUE_AI_ENABLED)."
    if not cfg.api_key_present:
        return False, "League AI needs ANTHROPIC_API_KEY in .env (local) or Streamlit secrets (cloud)."
    return True, f"League AI ready · {cfg.model}"


def _league_row(client: Any, league_id: str) -> dict:
    try:
        rows = client.table("leagues").select("id, name, sleeper_league_id").eq("id", league_id).limit(1).execute().data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _standings_loader(sleeper_league_id: str | None):
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


@st.cache_data(ttl=300, show_spinner=False)
def _cached_state(league_id: str, season: int, user_id: str, epoch: int) -> dict:
    return load_team_state(auth_client(), league_id, season)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_rules(league_id: str, user_id: str, epoch: int) -> dict:
    return load_league_rules(auth_client(), league_id)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_pack(league_id: str, season: int, league_team_id: str, user_id: str, epoch: int, sleeper_league_id: str | None, league_name: str, owner_name: str, team_name: str, role: str, memory_epoch: int = 0) -> str:
    client = auth_client()
    state = _cached_state(league_id, season, user_id, epoch)
    memory = Memory(client, league_id=league_id, user_id=user_id, league_team_id=league_team_id, owner_name=owner_name)
    sources = LeagueDataSources(
        client=client,
        league_id=league_id,
        season=season,
        league_name=league_name,
        sleeper_league_id=sleeper_league_id,
        league_rules=_cached_rules(league_id, user_id, epoch),
        state_loader=lambda: state,
        standings_loader=_standings_loader(sleeper_league_id),
        history_loader=lambda: pack_history_summary(client, league_id),
        private_memory_loader=memory.private_notes,
        league_notebook_loader=memory.league_notes,
        learning_loader=lambda: learning_pack_lines(client, league_id, user_id, season),
        data_freshness={"league state": "live from the app database", "standings": "live from Sleeper", **data_freshness(client)},
    )
    asker = Asker(user_id=user_id, league_id=league_id, league_team_id=league_team_id, owner_name=owner_name, team_name=team_name, role=role)
    return build_pack(sources, asker)


TOOL_STATUS = {
    "get_player_profile": "Pulling up the player profile",
    "get_player_stats": "Checking the stats",
    "compare_players": "Comparing players",
    "get_injury_report": "Checking the injury report",
    "get_prospects": "Scouting the prospects",
    "get_market_values": "Checking market values",
    "search_players": "Searching the player pool",
    "simulate_trade": "Running the trade through the cap",
    "get_league_history": "Digging through league history",
    "get_waiver_prices": "Looking up what pickups cost here",
    "get_trade_tendencies": "Checking who trades with whom",
    "get_matchup_history": "Checking matchup history",
    "remember": "Making a note",
    "forget": "Updating my notes",
    "log_prediction": "Logging this call so I can grade it after the games",
    "get_my_track_record": "Checking how my past calls graded",
}


class LiveAnswer:
    """Streams an answer into the chat bubble: a status line for each tool, text as it arrives."""

    def __init__(self) -> None:
        self.status = st.empty()
        self.body = st.empty()
        self.text = ""
        self.status.caption("Thinking…")

    def __call__(self, kind: str, payload: Any) -> None:
        if kind == "reset":
            self.text = ""  # text before a lookup is replaced by the next round's answer
        elif kind == "round" and self.text:
            self.text += "\n\n"
        elif kind == "tool":
            self.status.caption(TOOL_STATUS.get(str(payload), "Checking the data") + "…")
            self.body.empty()
        elif kind == "text":
            self.text += str(payload or "")
            self.status.empty()
            self.body.markdown(self.text.replace("$", "\\$") + " ▌")

    def finish(self) -> None:
        self.status.empty()
        self.body.empty()


def run_league_ai(*, request_context: Any, team: dict, question: str, history: list[dict[str, str]], tools: ToolRegistry | None = None, on_event: Any = None, week_context: str | None = None) -> AnswerResult:
    client = auth_client()
    league_id = request_context.league_id
    league = _league_row(client, league_id)
    owner_name = request_context.owner_name or team.get("owner_name") or team.get("team_name") or "Owner"
    team_name = request_context.team_name or team.get("team_name") or owner_name
    role = request_context.role or team.get("role") or "owner"
    epoch = int(st.session_state.get("team_state_cache_epoch", 0))
    memory_epoch = int(st.session_state.get(MEMORY_EPOCH_KEY, 0))
    pack = _cached_pack(
        league_id, int(request_context.current_season), request_context.league_team_id, request_context.user_id, epoch,
        league.get("sleeper_league_id"), league.get("name") or "the league", owner_name, team_name, role, memory_epoch,
    )
    memory = Memory(client, league_id=league_id, user_id=request_context.user_id, league_team_id=request_context.league_team_id, owner_name=owner_name)
    asker = Asker(user_id=request_context.user_id, league_id=league_id, league_team_id=request_context.league_team_id, owner_name=owner_name, team_name=team_name, role=role)
    sources = LeagueDataSources(client=client, league_id=league_id, season=int(request_context.current_season))
    if tools is None:
        state = _cached_state(league_id, int(request_context.current_season), request_context.user_id, epoch)
        rules = _cached_rules(league_id, request_context.user_id, epoch)
        nfl = NflData(client, league_state=state)
        ledger = Ledger(client, league_id=league_id, user_id=request_context.user_id, league_team_id=request_context.league_team_id,
                        season=int(request_context.current_season), week=_cached_nfl_week(),
                        resolve_player=lambda name: next(iter(nfl.find_players(name, limit=1)), None),
                        conversation_id=lambda: st.session_state.get(CONVERSATION_KEY))
        tools = build_registry(client, league_state=state, salary_cap=rules.get("salary_cap", 225), dead_cap_pct=float(rules.get("default_dead_cap_pct", 50) or 50),
                               extra=[*history_tools(client, league_id), *memory.tools(), *ledger.tools()])
    result = answer(question=question, history=history, asker=asker, sources=sources, tools=tools, client=ClaudeClient(), pack=pack, on_event=on_event,
                    week_context=week_context, turn_effort=effort_for(question))
    if any(name in ("remember", "forget") for name in result.trace.tool_calls):
        st.session_state[MEMORY_EPOCH_KEY] = memory_epoch + 1  # rebuild the pack next turn so new notes show
    if result.ok:
        full = [*history, {"role": "user", "content": question}, {"role": "assistant", "content": result.text}]
        st.session_state[CONVERSATION_KEY] = memory.save_conversation(st.session_state.get(CONVERSATION_KEY), full)
    record_usage(client, row_from_trace(result.trace, league_id=league_id, user_id=request_context.user_id, feature="chat",
                                        topic=question_topic(question), conversation_id=st.session_state.get(CONVERSATION_KEY)))
    return result


def resume_conversation(request_context: Any) -> list[dict[str, str]] | None:
    """The owner's most recent saved League AI conversation, if any (used when the page has no history yet)."""
    try:
        memory = Memory(auth_client(), league_id=request_context.league_id, user_id=request_context.user_id, league_team_id=request_context.league_team_id, owner_name=request_context.owner_name or "")
        latest = memory.latest_conversation()
    except Exception:
        return None
    if not latest or not latest.get("messages"):
        return None
    st.session_state[CONVERSATION_KEY] = latest["id"]
    return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in latest["messages"]]


def start_new_conversation() -> None:
    st.session_state.pop(CONVERSATION_KEY, None)


def list_conversations(request_context: Any) -> list[dict[str, Any]]:
    memory = Memory(auth_client(), league_id=request_context.league_id, user_id=request_context.user_id, league_team_id=request_context.league_team_id, owner_name=request_context.owner_name or "")
    return memory.list_conversations()


def load_conversation(request_context: Any, conversation_id: str) -> list[dict[str, str]] | None:
    memory = Memory(auth_client(), league_id=request_context.league_id, user_id=request_context.user_id, league_team_id=request_context.league_team_id, owner_name=request_context.owner_name or "")
    row = memory.get_conversation(conversation_id)
    if not row or not row.get("messages"):
        return None
    st.session_state[CONVERSATION_KEY] = row["id"]
    msgs = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in row["messages"]]
    return [m for m in msgs if not str(m.get("content", "")).startswith("Hi, I'm **Coach Condor**")]


# ---- presentation -----------------------------------------------------------

QUICK_PROMPTS = [
    "Am I a contender this year? Be honest.",
    "Which contract is hurting me most?",
    "Injury check on my starters this week.",
    "Find me a realistic trade for a WR upgrade.",
    "Who should I target on waivers, and at what price?",
]


def league_ai_opening(owner_name: str | None, league_name: str | None = None, ready: bool = True) -> str:
    first = (owner_name or "").split(" ")[0] or "there"
    if not ready:
        return f"Hey {first}. League AI isn't configured on this deployment yet, so I'm running in the legacy GM Brain mode for now."
    return (
        f"Hey {first}, I'm League AI, your co-GM. I've got every contract, cap number, pick and rule in "
        f"{league_name or 'the league'}, every trade, pickup and matchup going back seasons, plus live NFL stats, "
        "injuries and market values. Ask me anything about your team, another team, a player, a trade, or the draft."
    )


LEAGUE_AI_CSS = """
<style>
.lai-header { padding: 1.1rem 0 0.4rem 0; display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; }
.lai-header h1 { margin: 0; letter-spacing: .01em; }
.lai-badge { display: inline-block; padding: .2rem .6rem; border-radius: 999px; border: 1px solid rgba(226,188,91,.55);
             background: rgba(226,188,91,.10); color: #E2BC5B; font-size: .78rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.lai-subtitle { opacity: .74; margin: .1rem 0 .9rem 0; }
.lai-status { opacity: .6; font-size: .82rem; margin-bottom: .6rem; }
[data-testid="stChatMessageContent"] { font-size: 1.04rem; line-height: 1.6; }
[data-testid="stChatMessageContent"] table { font-size: .95rem; }
[data-testid="stChatMessageContent"] th { color: #E2BC5B; }
.st-key-lai_prompts button { border-radius: 999px !important; border: 1px solid rgba(226,188,91,.4) !important;
             background: rgba(255,255,255,.03) !important; font-size: .86rem !important; padding: .35rem .8rem !important; white-space: nowrap; }
.st-key-lai_prompts button:hover { border-color: #E2BC5B !important; background: rgba(226,188,91,.12) !important; }
</style>
"""


def render_league_ai_header(status_text: str, league_name: str | None = None) -> None:
    st.markdown(LEAGUE_AI_CSS, unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="lai-header"><h1>League AI</h1><span class="lai-badge">co-GM</span></div>
        <p class="lai-subtitle">Contracts, cap, trades, injuries, history and the draft for {league_name or 'your league'}. Ask anything.</p>
        <div class="lai-status">{status_text}</div>
        """,
        unsafe_allow_html=True,
    )


def render_quick_prompts() -> str | None:
    """Prompt chips in a row; returns the clicked prompt, if any."""
    with st.container(key="lai_prompts"):
        cols = st.columns(len(QUICK_PROMPTS))
        for col, text in zip(cols, QUICK_PROMPTS):
            with col:
                if st.button(text, key=f"lai_prompt_{abs(hash(text))}", use_container_width=True):
                    return text
    return None


# ---- Front Office ---------------------------------------------------------------

from league_ai.front_office import (  # noqa: E402
    mobile_status,
    brief_fingerprint,
    brief_facts, data_brief, merge_brief, rotating_prompts, build_tiles, current_opponent, generate_brief, head_to_head, load_brief, nfl_week, power_movement, power_rankings, save_brief, season_table, snapshot_power,
)
from league_ai.settings_store import apply_sleeper_scoring, fetch_sleeper_scoring, load_settings  # noqa: E402


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_nfl_week() -> int:
    return nfl_week()


@st.cache_data(ttl=600, show_spinner=False)
def _cached_standings(league_id: str, season: int, user_id: str, sleeper_league_id: str | None) -> list[dict]:
    table = season_table(auth_client(), league_id, season)
    if table:
        return table
    loader = _standings_loader(sleeper_league_id)
    try:
        return loader() if loader else []
    except Exception as exc:
        print(f"FRONT_OFFICE_STANDINGS_ERROR {type(exc).__name__}: {exc}", flush=True)
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_roster_positions(league_id: str, sleeper_league_id: str | None, user_id: str) -> list[str]:
    s = load_settings(auth_client(), league_id)
    if not s.roster_positions and sleeper_league_id:
        try:
            apply_sleeper_scoring(s, fetch_sleeper_scoring(sleeper_league_id))
        except Exception:
            pass
    return list(s.roster_positions or [])


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _cached_power(league_id: str, season: int, user_id: str, epoch: int, positions: tuple[str, ...], actual_ppg: tuple[tuple[str, float], ...] = ()) -> list[dict]:
    client = auth_client()
    state = _cached_state(league_id, season, user_id, epoch)
    rankings = power_rankings(client, state, season, list(positions), dict(actual_ppg))
    snapshot_power(client, league_id, rankings)
    return [{"team_id": tp.team_id, "owner": tp.owner, "now_rank": tp.now_rank, "now_score": tp.now_score, "dynasty_rank": tp.dynasty_rank, "dynasty_score": tp.dynasty_score,
             "weakest_slot": tp.weakest_slot, "weakest_note": tp.weakest_note, "strongest_slot": tp.strongest_slot,
             "starters": [f"{p.position} {p.name} ({p.now:.1f})" for p in tp.starters]} for tp in rankings]


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_opponent(sleeper_league_id: str | None, week: int, roster_to_owner: tuple[tuple[int, str], ...], my_roster_id: int | None) -> dict | None:
    return current_opponent(sleeper_league_id, week, dict(roster_to_owner), my_roster_id)


def front_office_data(*, request_context: Any, team: dict) -> dict[str, Any]:
    """Everything the Front Office header needs: tiles, rankings, opponent, week."""
    from types import SimpleNamespace

    client = auth_client()
    league_id = request_context.league_id
    season = int(request_context.current_season)
    epoch = int(st.session_state.get("team_state_cache_epoch", 0))
    league = _league_row(client, league_id)
    sleeper_id = league.get("sleeper_league_id")
    owner_name = request_context.owner_name or team.get("owner_name") or team.get("team_name") or "Owner"
    state = _cached_state(league_id, season, request_context.user_id, epoch)
    rules = _cached_rules(league_id, request_context.user_id, epoch)
    positions = tuple(_cached_roster_positions(league_id, sleeper_id, request_context.user_id))
    standings = _cached_standings(league_id, season, request_context.user_id, sleeper_id)
    actual_ppg = tuple((str(r.get("Team")), float(r.get("PF Per Game") or 0)) for r in standings if r.get("Games", 1))
    rankings_raw = _cached_power(league_id, season, request_context.user_id, epoch, positions, actual_ppg)
    rankings = [SimpleNamespace(**r) for r in rankings_raw]
    week = _cached_nfl_week()
    # roster id -> owner from league_teams
    try:
        teams = client.table("league_teams").select("id, owner_name, sleeper_roster_id").eq("league_id", league_id).execute().data or []
    except Exception:
        teams = []
    roster_to_owner = tuple((int(t["sleeper_roster_id"]), str(t["owner_name"])) for t in teams if t.get("sleeper_roster_id") is not None)
    my_roster_id = next((int(t["sleeper_roster_id"]) for t in teams if str(t["id"]) == str(request_context.league_team_id) and t.get("sleeper_roster_id") is not None), None)
    opponent = _cached_opponent(sleeper_id, week, roster_to_owner, my_roster_id)
    h2h = head_to_head(client, league_id, owner_name, opponent["owner_name"]) if opponent else None
    movement = power_movement(client, league_id, request_context.league_team_id)
    tiles = build_tiles(state=state, team_id=request_context.league_team_id, owner=owner_name, salary_cap=float(rules.get("salary_cap", 225) or 225),
                        standings=standings, rankings=rankings, opponent=opponent, h2h=h2h, movement=movement)
    mine = next((r for r in rankings_raw if r["team_id"] == request_context.league_team_id), None)
    instant = data_brief(client, state=state, team_id=request_context.league_team_id, owner=owner_name, opponent=opponent, h2h=h2h, standings=standings)
    return {"tiles": tiles, "data_brief": instant, "week": week, "opponent": opponent, "h2h": h2h, "standings": standings, "owner_name": owner_name, "league_name": league.get("name") or "the league",
            "season": season, "rankings": rankings_raw, "weakest_slot": mine["weakest_slot"] if mine else None, "salary_cap": float(rules.get("salary_cap", 225) or 225)}


def weekly_brief(*, request_context: Any, team: dict, fo: dict, force: bool = False) -> dict | None:
    client = auth_client()
    league_id = request_context.league_id
    season = int(request_context.current_season)
    week = int(fo.get("week") or 0)
    fingerprint = brief_fingerprint(fo.get("data_brief"))
    if not force:
        cached = load_brief(client, league_id, request_context.user_id, season, week)
        if cached and cached.get("_fingerprint") in (None, fingerprint) and not (cached.get("_fingerprint") is None and _brief_older_than(cached, hours=24)):
            return cached
    cfg = load_config()
    if not cfg.ready:
        return None
    epoch = int(st.session_state.get("team_state_cache_epoch", 0))
    league = _league_row(client, league_id)
    owner_name = request_context.owner_name or team.get("owner_name") or "Owner"
    team_name = request_context.team_name or team.get("team_name") or owner_name
    role = request_context.role or team.get("role") or "owner"
    pack = _cached_pack(league_id, season, request_context.league_team_id, request_context.user_id, epoch, league.get("sleeper_league_id"), league.get("name") or "the league",
                        owner_name, team_name, role, int(st.session_state.get(MEMORY_EPOCH_KEY, 0)))
    state = _cached_state(league_id, season, request_context.user_id, epoch)
    opponent = fo.get("opponent")
    facts = brief_facts(client, state=state, team_id=request_context.league_team_id, owner=owner_name, opponent=opponent, h2h=fo.get("h2h"), standings=fo.get("standings") or [],
                        weakest_slot=fo.get("weakest_slot"), salary_cap=float(fo.get("salary_cap") or 225))
    sections = generate_brief(pack=pack, tools=None, owner=owner_name, week=week, opponent=opponent["owner_name"] if opponent else None, facts=facts,
                              on_trace=lambda trace: record_usage(client, row_from_trace(trace, league_id=league_id, user_id=request_context.user_id, feature="brief")))
    if sections:
        sections["_fingerprint"] = fingerprint  # regenerate when injuries or the opponent change
        save_brief(client, league_id, request_context.user_id, request_context.league_team_id, season, week, sections)
    return sections


def _brief_older_than(sections: dict, hours: int) -> bool:
    """Briefs saved before fingerprints existed: refresh once they're a day old."""
    from datetime import datetime, timedelta, timezone

    try:
        made = datetime.fromisoformat(str(sections.get("_generated_at")).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - made > timedelta(hours=hours)
    except Exception:
        return False


FRONT_OFFICE_CSS = """
<style>
.fo-hdr { display:flex; align-items:flex-end; gap:14px; flex-wrap:wrap; padding: .6rem 0 .2rem; }
.fo-hdr h1 { margin:0; font-size:2.2rem; }
.fo-badge { display:inline-block; padding:.2rem .6rem; border-radius:999px; border:1px solid rgba(226,188,91,.55); background:rgba(226,188,91,.10);
            color:#E2BC5B; font-size:.72rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; margin-bottom:.45rem; }
.fo-who { margin-left:auto; text-align:right; }
.fo-who .n { font-weight:800; }
.fo-who .m { opacity:.55; font-size:.8rem; }
.fo-tiles { display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:14px 0 18px; }
@media (max-width: 900px) { .fo-tiles { grid-template-columns:repeat(3,1fr);} .fo-brief { grid-template-columns:1fr !important; } }
.fo-tile { border:1px solid rgba(226,188,91,.22); border-radius:14px; background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.015)); padding:13px 15px; }
.fo-tile .v { font-size:1.5rem; font-weight:800; color:#F5EBD7; line-height:1.1; }
.fo-tile .l { font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; opacity:.6; margin-top:4px; }
.fo-tile.warn { border-color:rgba(201,69,59,.55);} .fo-tile.warn .v { color:#F3A79F; }
.fo-tile.good .v { color:#9BE0AC; }
.fo-sec { display:flex; align-items:baseline; gap:10px; margin:4px 0 8px; }
.fo-label { font-size:.72rem; letter-spacing:.12em; text-transform:uppercase; color:#E2BC5B; font-weight:800; }
.fo-note { opacity:.5; font-size:.78rem; }
.fo-brief { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.fo-card { border:1px solid rgba(226,188,91,.3); border-radius:14px; background:rgba(255,255,255,.025); padding:14px 16px; min-height: 150px; }
.fo-card .fo-label { display:block; margin-bottom:8px; }
.fo-row { display:flex; justify-content:space-between; align-items:center; gap:10px; margin:5px 0; font-size:.95rem; }
.fo-row .nm { font-weight:600; }
.fo-tag { display:inline-block; font-size:.72rem; padding:2px 7px; border-radius:6px; background:rgba(201,69,59,.2); color:#F3A79F; white-space:nowrap; }
.fo-tag.ok { background:rgba(94,170,110,.2); color:#9BE0AC; }
.fo-call { margin-top:9px; padding-top:8px; border-top:1px solid rgba(255,255,255,.07); font-size:.9rem; opacity:.92; }
.fo-call b { color:#E2BC5B; }
.fo-contracts { opacity:.75; font-size:.88rem; margin:8px 0 4px; }
[data-testid="stChatMessageContent"] { font-size:1.03rem; line-height:1.58; }
[data-testid="stChatMessageContent"] th { color:#E2BC5B; }
.st-key-lai_prompts button { border-radius:999px !important; border:1px solid rgba(226,188,91,.4) !important; background:rgba(255,255,255,.03) !important;
             font-size:.85rem !important; padding:.3rem .8rem !important; white-space:nowrap; }
.st-key-lai_prompts button:hover { border-color:#E2BC5B !important; background:rgba(226,188,91,.12) !important; }
/* Chip buttons look like pills everywhere */
[data-testid="stHorizontalBlock"]:has(.fo-chips-marker) button { border-radius:999px !important; border:1px solid rgba(226,188,91,.4) !important;
             background:rgba(255,255,255,.03) !important; font-size:.85rem !important; padding:.3rem .8rem !important; white-space:nowrap; }
[data-testid="stHorizontalBlock"]:has(.fo-chips-marker) button:hover { border-color:#E2BC5B !important; background:rgba(226,188,91,.12) !important; }
[data-testid="stElementContainer"]:has(.fo-chips-marker), [data-testid="stElementContainer"]:has(.fo-footer-marker) { display:none !important; }
/* ☰ popover: plain glyph, no box, no caret */
[data-testid="stHorizontalBlock"]:has(.fo-hdr) [data-testid="stPopover"] button { border:none !important; background:transparent !important; font-size:1.5rem !important; padding:0 .2rem !important; color:#F5EBD7 !important; min-height:0 !important; }
[data-testid="stHorizontalBlock"]:has(.fo-hdr) [data-testid="stPopover"] button svg { display:none !important; }
[data-testid="stHorizontalBlock"]:has(.fo-hdr) [data-testid="stPopover"] { display:flex; justify-content:flex-end; }
.fo-mobile { display:none; }
/* Desktop: no ☰, header spans the row */
@media (min-width: 641px) {
  [data-testid="stHorizontalBlock"]:has(.fo-hdr) > [data-testid="stColumn"]:last-child { display:none !important; }
  [data-testid="stHorizontalBlock"]:has(.fo-hdr) > [data-testid="stColumn"]:first-child { flex:1 1 100% !important; width:100% !important; }
}
/* ---- Phone layout: Option A, nothing but the conversation ---- */
@media (max-width: 640px) {
  .block-container { padding-top:1.2rem !important; padding-bottom:9.5rem !important; }
  .fo-tiles, .fo-sec, .fo-brief, .fo-card, .fo-who { display:none !important; }
  .fo-hdr { padding:0; gap:10px; align-items:center; }
  .fo-hdr h1 { font-size:1.75rem; }
  .fo-badge { font-size:.62rem; padding:.18rem .55rem; margin:0; }
  [data-testid="stHorizontalBlock"]:has(.fo-hdr) { flex-direction:row !important; flex-wrap:nowrap !important; align-items:center !important; border-bottom:1px solid rgba(226,188,91,.16); padding-bottom:.5rem; }
  [data-testid="stHorizontalBlock"]:has(.fo-hdr) > [data-testid="stColumn"]:first-child { flex:1 1 auto !important; width:auto !important; min-width:0 !important; }
  [data-testid="stHorizontalBlock"]:has(.fo-hdr) > [data-testid="stColumn"]:last-child { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
  .fo-mobile { display:block; text-align:center; opacity:.55; font-size:.8rem; margin:.2rem 0 .2rem; }
  /* desktop footer hidden on phones (☰ has the same controls) */
  [data-testid="stHorizontalBlock"]:has(.fo-footer-marker) { display:none !important; }
  /* starter chips pinned just above the input, scrolling sideways */
  [data-testid="stHorizontalBlock"]:has(.fo-chips-marker) { position:fixed; left:0; right:0; bottom:6.4rem; z-index:100000; margin:0; padding:.4rem 1rem .5rem;
             flex-direction:row !important; flex-wrap:nowrap !important; overflow-x:auto; gap:.45rem !important; scrollbar-width:none;
             background:#0B1615; }
  [data-testid="stHorizontalBlock"]:has(.fo-chips-marker)::-webkit-scrollbar { display:none; }
  [data-testid="stHorizontalBlock"]:has(.fo-chips-marker) [data-testid="stColumn"] { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
  [data-testid="stHorizontalBlock"]:has(.fo-chips-marker) button { font-size:.82rem !important; padding:.3rem .8rem !important; }
  /* rounded pill input */
  [data-testid="stBottom"] > div { padding-top:.4rem !important; }
  [data-testid="stChatInput"], [data-testid="stChatInput"] > div, [data-testid="stChatInput"] [data-baseweb="textarea"], [data-testid="stChatInput"] [data-baseweb="base-input"] {
             border-radius:26px !important; }
  [data-testid="stChatInput"] { border:1px solid rgba(226,188,91,.55) !important; background:rgba(255,255,255,.03) !important; overflow:hidden; }
  [data-testid="stChatInput"] [data-baseweb="textarea"], [data-testid="stChatInput"] [data-baseweb="base-input"], [data-testid="stChatInput"] textarea { border:none !important; background:transparent !important; box-shadow:none !important; }
  [data-testid="stChatInput"] textarea { padding-left:.9rem !important; }
  [data-testid="stChatInputSubmitButton"] { border-radius:50% !important; }
  [data-testid="stChatMessage"] { padding:.4rem 0; }
  [data-testid="stChatMessageContent"] { font-size:.95rem; line-height:1.5; }
}
</style>
"""

FO_PROMPTS = ["Am I a contender?", "Worst contract on my team", "Injury check on my starters", "Find me a WR trade", "Waiver targets and prices", "Plan my 2027 draft"]




def _esc(text: Any) -> str:
    import html

    return html.escape(str(text or ""))


def render_front_office_header(owner_name: str, week: int, freshness: str) -> None:
    st.markdown(FRONT_OFFICE_CSS, unsafe_allow_html=True)
    st.markdown(
        f'<div class="fo-hdr"><h1>Front Office</h1><span class="fo-badge">Assistant GM</span>'
        f'<div class="fo-who"><div class="n">{_esc(owner_name)}</div><div class="m">Week {week} · {_esc(freshness)}</div></div></div>',
        unsafe_allow_html=True,
    )


def _svg_avatar(text: str, bg: str, fg: str) -> str:
    from urllib.parse import quote

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
           f'<rect width="64" height="64" rx="14" fill="{bg}"/>'
           f'<text x="32" y="41" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="26" font-weight="800" fill="{fg}">{text}</text></svg>')
    return "data:image/svg+xml;utf8," + quote(svg)


GM_AVATAR = _svg_avatar("GM", "#E2BC5B", "#0B1615")


def owner_avatar(owner_name: str | None) -> str:
    initial = ((owner_name or "").strip()[:1] or "?").upper()
    return _svg_avatar(initial, "#C9453B", "#FFFFFF")


def render_mobile_status(week: int, tiles: list[dict[str, Any]] | None) -> None:
    line = mobile_status(week, tiles)
    if line:
        st.markdown(f'<div class="fo-mobile">{_esc(line)}</div>', unsafe_allow_html=True)


def render_tiles(tiles: list[dict[str, Any]]) -> None:
    cells = "".join(f'<div class="fo-tile {t.get("tone") or ""}"><div class="v">{_esc(t["value"])}</div><div class="l">{_esc(t["label"])}</div></div>' for t in tiles)
    st.markdown(f'<div class="fo-tiles">{cells}</div>', unsafe_allow_html=True)


def _tag_class(status: str) -> str:
    s = (status or "").lower()
    return "ok" if any(w in s for w in ("playing", "trending in", "cleared", "full", "healthy", "active")) else ""


def render_brief(brief: dict[str, Any] | None, *, generating: bool = False) -> None:
    st.markdown('<div class="fo-sec"><span class="fo-label">Weekly brief</span><span class="fo-note">Your injuries and this week\'s matchup, refreshed each week</span></div>', unsafe_allow_html=True)
    if brief is None:
        msg = "Writing this week's brief…" if generating else "The weekly brief needs the Assistant GM to be configured."
        st.markdown(f'<div class="fo-card"><span class="fo-note">{_esc(msg)}</span></div>', unsafe_allow_html=True)
        return
    inj = brief.get("injuries") or {}
    mu = brief.get("matchup") or {}
    inj_rows = "".join(f'<div class="fo-row"><span class="nm">{_esc(i.get("player"))}</span><span class="fo-tag {_tag_class(i.get("status"))}">{_esc(i.get("status"))}</span></div>' for i in (inj.get("items") or [])[:5]) or '<div class="fo-row"><span class="fo-note">No injuries on your roster.</span></div>'
    mu_rows = "".join(f'<div class="fo-row"><span>{_esc(l)}</span></div>' for l in (mu.get("lines") or [])[:4])
    call = f'<div class="fo-call"><b>Call:</b> {_esc(inj.get("call"))}</div>' if inj.get("call") else ""
    stakes = f'<div class="fo-call"><b>Stakes:</b> {_esc(mu.get("stakes"))}</div>' if mu.get("stakes") else ""
    headline = f'<div style="font-size:1.2rem;font-weight:800;margin:2px 0 4px">{_esc(mu.get("headline"))}</div>'
    html_out = (
        '<div class="fo-brief">'
        f'<div class="fo-card"><span class="fo-label">✚ Injuries &amp; lineup</span>{inj_rows}{call}</div>'
        f'<div class="fo-card"><span class="fo-label">⚔ This week\'s matchup</span>{headline}{mu_rows}{stakes}</div>'
        '</div>'
    )
    st.markdown(html_out, unsafe_allow_html=True)


def render_report_card(request_context: Any) -> None:
    """'How my calls have graded': this owner's record, recent graded calls with the diagnosis, and the playbook."""
    client = auth_client()
    ledger = Ledger(client, league_id=request_context.league_id, user_id=request_context.user_id, league_team_id=request_context.league_team_id,
                    season=int(request_context.current_season), week=_cached_nfl_week())
    rec = ledger.track_record(limit=6)
    try:
        lessons = (client.table("league_ai_lessons").select("lesson, evidence_count").eq("league_id", request_context.league_id).eq("active", True)
                   .order("evidence_count", desc=True).limit(6).execute().data or [])
    except Exception:
        lessons = []
    record = rec.get("record") or {}
    label = "Assistant GM report card" + (" · " + ", ".join(f"{k.replace('_', '/')} {v}" for k, v in record.items()) if record else "")
    with st.expander(label, expanded=False):
        if not record and not rec.get("open_calls"):
            st.caption("No graded calls yet. Ask who to start, who to add, or whether to take a trade; I log the call and grade it after the games.")
        elif not record:
            st.caption(f"{rec['open_calls']} call(s) waiting on games to finish. Grades land the morning after the last game of the week.")
        for r in rec.get("recent") or []:
            mark = "✅" if r["result"] == "right" else "❌"
            st.markdown(f"{mark} **{_esc(r['when'])}** · {_esc(r['call'])} ({_esc(r['points'])})" + (f"  \n<span class='fo-note'>{_esc(r['why'])}</span>" if r.get("why") else ""),
                        unsafe_allow_html=True)
        if lessons:
            st.markdown("**What I've learned in this league**")
            for l in lessons:
                st.markdown(f"- {_esc(l['lesson'])}" + (f" <span class='fo-note'>(seen {int(l.get('evidence_count') or 1)}x)</span>" if (l.get("evidence_count") or 1) > 1 else ""),
                            unsafe_allow_html=True)


def render_fo_prompts(week: int | None = None) -> str | None:
    prompts = rotating_prompts(week=week)
    with st.container(key="lai_prompts"):
        cols = st.columns(len(prompts))
        clicked = None
        for i, (col, text) in enumerate(zip(cols, prompts)):
            with col:
                if i == 0:
                    st.markdown('<span class="fo-chips-marker"></span>', unsafe_allow_html=True)
                if st.button(text, key=f"fo_prompt_{abs(hash(text))}", use_container_width=True):
                    clicked = text
    return clicked


def front_office_opening(owner_name: str | None, ready: bool = True) -> str:
    first = (owner_name or "").split(" ")[0] or "there"
    if not ready:
        return f"Hey {first}. The Assistant GM isn't configured on this deployment yet."
    return f"{_greeting()}, {first}. What are we working on?"


def _greeting(now: Any = None) -> str:
    """Time-of-day greeting in league time (Mountain)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hour = (now or datetime.now(ZoneInfo("America/Denver"))).hour
    return "Morning" if 4 <= hour < 12 else "Afternoon" if hour < 17 else "Evening"
