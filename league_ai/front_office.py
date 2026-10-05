"""Front Office data: tiles, power rankings, this week's matchup, and the weekly brief."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping

import requests

from league_ai.client import ClaudeClient
from league_ai.config import LeagueAIConfig, load_config
from league_ai.context_pack import team_summaries
from league_ai.power import PlayerStrength, TeamPower, compute_power, player_strength
from league_ai.prompt import SYSTEM_PROMPT
from league_ai.tools import ToolRegistry
from services.team_roster_state import state_roster

SLEEPER = "https://api.sleeper.app/v1"
DEFAULT_BRIEF_MODEL = "claude-haiku-4-5-20251001"


def brief_config() -> LeagueAIConfig:
    """Fast model, no tools, modest output for the weekly brief. Override with LEAGUE_AI_BRIEF_MODEL."""
    import os

    base = load_config()
    model = os.getenv("LEAGUE_AI_BRIEF_MODEL", "").strip() or DEFAULT_BRIEF_MODEL
    return LeagueAIConfig(enabled=base.enabled, api_key_present=base.api_key_present, model=model, max_output_tokens=1500, max_tool_calls=0, timeout_seconds=45)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _chunks(items: list[str], n: int = 150):
    for i in range(0, len(items), n):
        yield items[i : i + n]


# ---- NFL week / opponent -------------------------------------------------------

def nfl_week(get_json: Callable[[str], Any] | None = None) -> int:
    get_json = get_json or (lambda url: requests.get(url, timeout=15).json())
    try:
        st = get_json(f"{SLEEPER}/state/nfl") or {}
        return int(st.get("week") or st.get("display_week") or 1)
    except Exception:
        return 1


def current_opponent(sleeper_league_id: str | None, week: int, roster_to_owner: Mapping[int, str], my_roster_id: int | None, get_json: Callable[[str], Any] | None = None) -> dict[str, Any] | None:
    if not sleeper_league_id or my_roster_id is None:
        return None
    get_json = get_json or (lambda url: requests.get(url, timeout=15).json())
    try:
        rows = get_json(f"{SLEEPER}/league/{sleeper_league_id}/matchups/{week}") or []
    except Exception:
        return None
    mine = next((r for r in rows if int(r.get("roster_id") or -1) == int(my_roster_id)), None)
    if not mine:
        return None
    opp = next((r for r in rows if r.get("matchup_id") == mine.get("matchup_id") and int(r.get("roster_id") or -1) != int(my_roster_id)), None)
    if not opp:
        return None
    rid = int(opp["roster_id"])
    return {"roster_id": rid, "owner_name": roster_to_owner.get(rid, f"Roster {rid}"), "week": week}


def head_to_head(client: Any, league_id: str, me: str, them: str) -> dict[str, Any]:
    try:
        rows = client.table("league_matchups").select("season, week, won, points, opponent_points").eq("league_id", league_id).eq("owner_name", me).eq("opponent_name", them).order("season", desc=True).order("week", desc=True).limit(100).execute().data or []
    except Exception:
        rows = []
    wins = sum(1 for r in rows if r.get("won"))
    last = rows[0] if rows else None
    return {"games": len(rows), "wins": wins, "losses": len(rows) - wins,
            "last": (f"{last['season']} wk {last['week']}: {'W' if last.get('won') else 'L'} {_num(last.get('points')):.0f}-{_num(last.get('opponent_points')):.0f}" if last else None)}


def season_table(client: Any, league_id: str, season: int) -> list[dict[str, Any]]:
    """Regular-season standings from league_matchups (already synced): rank by standing points, then PF."""
    try:
        rows = client.table("league_matchups").select("owner_name, points, opponent_points, won, standing_points, is_playoff").eq("league_id", league_id).eq("season", season).limit(2000).execute().data or []
    except Exception:
        return []
    agg: dict[str, dict[str, float]] = defaultdict(lambda: {"w": 0, "l": 0, "pf": 0.0, "pa": 0.0, "sp": 0.0, "g": 0})
    for r in rows:
        if r.get("is_playoff"):
            continue
        a = agg[r["owner_name"]]
        a["g"] += 1; a["w"] += 1 if r.get("won") else 0; a["l"] += 0 if r.get("won") else 1
        a["pf"] += _num(r.get("points")); a["pa"] += _num(r.get("opponent_points")); a["sp"] += _num(r.get("standing_points"))
    table = sorted(agg.items(), key=lambda x: (-x[1]["sp"], -x[1]["pf"]))
    return [{"Team": name, "Wins": int(a["w"]), "Losses": int(a["l"]), "PF": round(a["pf"], 1), "PA": round(a["pa"], 1), "Standing Points": int(a["sp"]),
             "PF Per Game": round(a["pf"] / a["g"], 1) if a["g"] else 0.0, "Games": int(a["g"]), "rank": i + 1} for i, (name, a) in enumerate(table)]


def team_record(standings: list[dict[str, Any]], owner: str) -> dict[str, Any] | None:
    for row in standings:
        if str(row.get("Team") or row.get("owner_name") or "").casefold() == owner.casefold():
            return {"record": f"{int(row.get('Wins', 0))}-{int(row.get('Losses', 0))}", "rank": int(row.get("rank") or 0), "pf": _num(row.get("PF")), "ppg": _num(row.get("PF Per Game")), "standing_points": int(row.get("Standing Points", 0))}
    return None


# ---- player strengths from nfl_* tables ------------------------------------------

def build_player_strengths(client: Any, state: Mapping[str, Any], season: int) -> list[PlayerStrength]:
    roster = state_roster(state)
    ids = sorted({str(r.get("sleeper_player_id") or r.get("player_id") or "") for r in roster} - {""})
    stats: dict[str, list[dict[str, Any]]] = defaultdict(list)
    market: dict[str, float] = {}
    ages: dict[str, float] = {}
    injuries: dict[str, str] = {}
    try:
        latest = client.table("nfl_market_values").select("scrape_date").order("scrape_date", desc=True).limit(1).execute().data or []
        latest_date = latest[0]["scrape_date"] if latest else None
    except Exception:
        latest_date = None
    for chunk in _chunks(ids):
        try:
            for r in client.table("nfl_player_stats").select("sleeper_id, week, fantasy_points_ppr").eq("season", season).eq("season_type", "REG").in_("sleeper_id", chunk).execute().data or []:
                stats[str(r["sleeper_id"])].append(r)
        except Exception:
            pass
        if latest_date:
            try:
                for r in client.table("nfl_market_values").select("sleeper_id, ecr_pos").eq("scrape_date", latest_date).in_("sleeper_id", chunk).execute().data or []:
                    if r.get("ecr_pos") is not None:
                        market[str(r["sleeper_id"])] = float(r["ecr_pos"])
            except Exception:
                pass
        try:
            for r in client.table("nfl_players").select("sleeper_id, age, injury_status").in_("sleeper_id", chunk).execute().data or []:
                if r.get("age") is not None:
                    ages[str(r["sleeper_id"])] = float(r["age"])
                if r.get("injury_status"):
                    injuries[str(r["sleeper_id"])] = str(r["injury_status"])
        except Exception:
            pass
        try:
            for r in client.table("nfl_injuries").select("sleeper_id, status").in_("sleeper_id", chunk).execute().data or []:
                if r.get("status"):
                    injuries[str(r["sleeper_id"])] = str(r["status"])
        except Exception:
            pass
    out: list[PlayerStrength] = []
    for row in roster:
        sid = str(row.get("sleeper_player_id") or row.get("player_id") or "")
        weeks = sorted(stats.get(sid, []), key=lambda r: int(r.get("week") or 0))
        pts = [_num(r.get("fantasy_points_ppr")) for r in weeks]
        season_ppg = round(sum(pts) / len(pts), 2) if pts else None
        recent = pts[-4:]
        recent_ppg = round(sum(recent) / len(recent), 2) if recent else None
        out.append(player_strength(row, recent_ppg=recent_ppg, season_ppg=season_ppg, pos_rank=market.get(sid), age=ages.get(sid), injury_status=injuries.get(sid)))
    return out


def power_rankings(client: Any, state: Mapping[str, Any], season: int, roster_positions: list[str], actual_ppg: Mapping[str, float] | None = None) -> list[TeamPower]:
    players = build_player_strengths(client, state, season)
    return compute_power(players, state.get("teams", []), roster_positions, actual_ppg)


def snapshot_power(client: Any, league_id: str, rankings: list[TeamPower]) -> None:
    """Store today's ranking once (any member's client may write it); ignore failures."""
    today = date.today().isoformat()
    try:
        existing = client.table("league_power_rankings").select("league_team_id").eq("league_id", league_id).eq("computed_on", today).limit(1).execute().data or []
        if existing:
            return
        rows = [{"league_id": league_id, "computed_on": today, "league_team_id": tp.team_id, "owner_name": tp.owner, "now_rank": tp.now_rank, "now_score": tp.now_score,
                 "dynasty_rank": tp.dynasty_rank, "dynasty_score": tp.dynasty_score, "weakest_slot": tp.weakest_slot, "strongest_slot": tp.strongest_slot} for tp in rankings]
        client.table("league_power_rankings").insert(rows).execute()
    except Exception:
        pass


def power_movement(client: Any, league_id: str, team_id: str, days: int = 7) -> int | None:
    """now_rank change vs the snapshot ~days ago (positive = moved up)."""
    try:
        rows = client.table("league_power_rankings").select("computed_on, now_rank").eq("league_id", league_id).eq("league_team_id", team_id).order("computed_on", desc=True).limit(30).execute().data or []
    except Exception:
        return None
    if len(rows) < 2:
        return None
    today = rows[0]
    older = next((r for r in rows if (date.fromisoformat(str(today["computed_on"])) - date.fromisoformat(str(r["computed_on"]))).days >= days), rows[-1])
    if older is today or older.get("now_rank") is None or today.get("now_rank") is None:
        return None
    return int(older["now_rank"]) - int(today["now_rank"])


# ---- tiles -----------------------------------------------------------------------

def next_year_space(state: Mapping[str, Any], team_id: str, salary_cap: float) -> float:
    committed = sum(_num(r.get("cap_hit")) for r in state_roster(state) if str(r.get("league_team_id")) == team_id and int(r.get("contract_years_left") or 1) >= 2)
    return round(salary_cap - committed, 2)


def build_tiles(*, state: Mapping[str, Any], team_id: str, owner: str, salary_cap: float, standings: list[dict[str, Any]], rankings: list[TeamPower], opponent: dict[str, Any] | None, h2h: dict[str, Any] | None, movement: int | None) -> list[dict[str, Any]]:
    fin = next((s for s in team_summaries(state, salary_cap) if s.league_team_id == team_id), None)
    rec = team_record(standings, owner)
    mine = next((tp for tp in rankings if tp.team_id == team_id), None)
    tiles = []
    tiles.append({"value": rec["record"] if rec else "0-0", "label": f"Record · {_ordinal(rec['rank'])} of {len(standings)}" if rec and rec.get("rank") else "Record", "tone": "good" if rec and rec.get("rank", 99) <= 3 else None})
    if mine and mine.weakest_slot:
        tiles.append({"value": mine.weakest_slot, "label": f"Weakest spot · {mine.weakest_note}", "tone": "warn" if mine.weakest_note and mine.weakest_note.startswith("-") else None})
    else:
        tiles.append({"value": "—", "label": "Weakest spot · needs NFL data", "tone": None})
    if opponent:
        h = h2h or {}
        sub = f"Head to head {h.get('wins', 0)}-{h.get('losses', 0)}" if h.get("games") else "First meeting on record"
        tiles.append({"value": f"vs {opponent['owner_name'].split(' ')[-1]}", "label": f"Week {opponent['week']} · {sub}", "tone": None})
    else:
        tiles.append({"value": "—", "label": "No matchup this week", "tone": None})
    space = float(fin.cap_space) if fin else 0.0
    tiles.append({"value": f"${space:g}", "label": "Cap space", "tone": "warn" if space < 10 else ("good" if space >= 30 else None)})
    nxt = next_year_space(state, team_id, salary_cap)
    tiles.append({"value": f"${nxt:g}", "label": "Next-year cap space", "tone": "good" if nxt >= salary_cap * 0.35 else ("warn" if nxt < salary_cap * 0.15 else None)})
    if mine:
        mv = "" if movement is None or movement == 0 else (f" · up {movement}" if movement > 0 else f" · down {-movement}")
        tiles.append({"value": f"#{mine.now_rank}", "label": f"Power · dynasty #{mine.dynasty_rank}{mv}", "tone": "good" if mine.now_rank <= 3 else None})
    else:
        tiles.append({"value": "—", "label": "Power ranking", "tone": None})
    return tiles


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ---- weekly brief ------------------------------------------------------------------

BRIEF_INSTRUCTIONS = """Write this owner's weekly brief for the Front Office page from the FACTS below and the league state. Answer ONLY with a JSON object, no prose before or after, exactly this shape:
{
 "injuries": {"items": [{"player": "name", "status": "Out · ankle · est. Oct 4"}], "call": "one sentence: who to start / what changes"},
 "matchup": {"headline": "vs Owner Name", "lines": ["their record and ppg vs yours", "head to head", "standings stakes"], "stakes": "one sentence"}
}
Rules: injuries only for this owner's roster (max 5, worst first; empty list if none). Every line specific, no hedging. No markdown. Do not call tools; everything you need is below.

Owner: {owner}. NFL week {week}. Opponent this week: {opponent}.
FACTS:
{facts}"""


def brief_facts(client: Any, *, state: Mapping[str, Any], team_id: str, owner: str, opponent: dict[str, Any] | None, h2h: dict[str, Any] | None, standings: list[dict[str, Any]], weakest_slot: str | None, salary_cap: float) -> str:
    """Pre-gathered facts so the brief needs no tool calls: roster injuries, opponent line, free agents at the weak spot, cap."""
    roster = [r for r in state_roster(state) if str(r.get("league_team_id")) == team_id]
    ids = [str(r.get("sleeper_player_id") or r.get("player_id") or "") for r in roster]
    lines: list[str] = []
    try:
        inj = client.table("nfl_injuries").select("sleeper_id, player_name, status, injury_type, side, return_date, short_comment").in_("sleeper_id", ids).execute().data or []
    except Exception:
        inj = []
    try:
        sl = client.table("nfl_players").select("sleeper_id, full_name, injury_status, injury_body_part").in_("sleeper_id", ids).execute().data or []
    except Exception:
        sl = []
    seen = set()
    for r in inj:
        seen.add(r["sleeper_id"])
        lines.append(f"- INJURY {r.get('player_name')}: {r.get('status')} · {r.get('side') or ''} {r.get('injury_type') or ''} · est. return {r.get('return_date') or 'unknown'} · {r.get('short_comment') or ''}")
    for r in sl:
        if r.get("injury_status") and r["sleeper_id"] not in seen:
            lines.append(f"- INJURY {r.get('full_name')}: {r.get('injury_status')} ({r.get('injury_body_part') or 'unspecified'}), Sleeper only")
    if not any(l.startswith("- INJURY") for l in lines):
        lines.append("- INJURY none reported on this roster")
    me = team_record(standings, owner)
    if me:
        lines.append(f"- ME {owner}: {me['record']}, rank {me['rank']}, {me['ppg']} ppg, {me['standing_points']} standing points")
    if opponent:
        opp = team_record(standings, opponent["owner_name"])
        if opp:
            lines.append(f"- OPPONENT {opponent['owner_name']}: {opp['record']}, rank {opp['rank']}, {opp['ppg']} ppg, {opp['standing_points']} standing points")
        if h2h and h2h.get("games"):
            lines.append(f"- HEAD TO HEAD vs {opponent['owner_name']}: {h2h['wins']}-{h2h['losses']} all time; last {h2h.get('last')}")
    fin = next((x for x in team_summaries(state, salary_cap) if x.league_team_id == team_id), None)
    if fin:
        lines.append(f"- CAP space ${float(fin.cap_space):g}, dead cap ${float(fin.dead_cap):g}, roster {fin.roster_count} (IR {fin.ir_count}, taxi {fin.taxi_count})")
    expiring = [f"{r.get('player_name')} (${_num(r.get('cap_hit')):g})" for r in roster if int(r.get('contract_years_left') or 1) <= 1 and _num(r.get('cap_hit')) >= 8]
    if expiring:
        lines.append("- EXPIRING after this season (>= $8): " + ", ".join(expiring[:8]))
    if weakest_slot:
        pos = {"OP": "QB", "FLEX": "RB", "DST": "DEF"}.get(weakest_slot, weakest_slot)
        rostered = {str(r.get("sleeper_player_id") or r.get("player_id")) for r in state_roster(state)}
        try:
            fa = client.table("nfl_players").select("sleeper_id, full_name, team, depth_chart_order, injury_status, search_rank").eq("position", pos).order("search_rank").limit(80).execute().data or []
        except Exception:
            fa = []
        picks = [f"{r['full_name']} ({r.get('team')}, depth {r.get('depth_chart_order') or '?'}{', ' + r['injury_status'] if r.get('injury_status') else ''})" for r in fa if str(r["sleeper_id"]) not in rostered][:6]
        if picks:
            lines.append(f"- FREE AGENTS at weakest spot {weakest_slot} ({pos}), by relevance: " + "; ".join(picks))
    return "\n".join(lines)


def brief_fingerprint(data: Mapping[str, Any] | None) -> str:
    """Short hash of the facts the brief's verdicts depend on (injury list + opponent).

    A cached brief whose fingerprint differs is stale: news broke since it was written.
    """
    import hashlib

    data = data or {}
    items = [f"{i.get('player')}|{i.get('status')}" for i in (data.get("injuries") or {}).get("items") or []]
    headline = (data.get("matchup") or {}).get("headline") or ""
    return hashlib.sha256(json.dumps({"inj": sorted(items), "vs": headline}, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def brief_key(season: int, week: int) -> tuple[int, int]:
    return season, week


def load_brief(client: Any, league_id: str, user_id: str, season: int, week: int) -> dict[str, Any] | None:
    try:
        rows = client.table("league_ai_briefs").select("sections, generated_at").eq("league_id", league_id).eq("user_id", user_id).eq("season", season).eq("week", week).limit(1).execute().data or []
    except Exception:
        return None
    if not rows:
        return None
    sections = rows[0]["sections"]
    if isinstance(sections, str):
        try:
            sections = json.loads(sections)
        except Exception:
            return None
    sections["_generated_at"] = rows[0].get("generated_at")
    return sections


def save_brief(client: Any, league_id: str, user_id: str, team_id: str | None, season: int, week: int, sections: dict[str, Any]) -> None:
    try:
        client.table("league_ai_briefs").upsert({"league_id": league_id, "user_id": user_id, "league_team_id": team_id, "season": season, "week": week, "sections": sections, "generated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="league_id,user_id,season,week").execute()
    except Exception:
        pass


def parse_brief(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict) or "injuries" not in data or "matchup" not in data:
        return None
    return data


def generate_brief(*, pack: str, tools: ToolRegistry | None, owner: str, week: int, opponent: str | None, facts: str = "", client: ClaudeClient | None = None,
                   on_trace: Callable[[Any], None] | None = None) -> dict[str, Any] | None:
    system = [{"type": "text", "text": SYSTEM_PROMPT}, {"type": "text", "text": pack, "cache_control": {"type": "ephemeral"}}]
    question = BRIEF_INSTRUCTIONS.replace("{owner}", owner).replace("{week}", str(week)).replace("{opponent}", opponent or "no game this week").replace("{facts}", facts or "(none)")
    runner = client or ClaudeClient(brief_config())
    result = runner.run(system=system, messages=[{"role": "user", "content": question}], tools=None, max_tool_calls=0)
    if on_trace is not None:
        try:
            on_trace(result.trace)
        except Exception:
            pass
    if not result.ok:
        return None
    return parse_brief(result.text)


def rotating_prompts(weekday: int | None = None, week: int | None = None) -> list[str]:
    """Four starters that follow the fantasy week: waivers early, lineup late, trades near the deadline."""
    weekday = date.today().weekday() if weekday is None else weekday  # Mon=0
    always = ["Am I a contender?", "Worst contract on my team"]
    if weekday in (1, 2):      # Tue/Wed: waivers just ran
        timely = ["Waiver targets and prices", "Who should I pick up this week?"]
    elif weekday in (3, 4, 5):  # Thu-Sat: lineup decisions
        timely = ["Injury check on my starters", "Who do I start this week?"]
    else:                        # Sun/Mon: results and trades
        timely = ["Find me a trade that helps now", "How did my week go?"]
    if week and 8 <= week <= 11:
        timely[0] = "Find me a deadline trade"
    return always + timely


def data_brief(client: Any, *, state: Mapping[str, Any], team_id: str, owner: str, opponent: dict[str, Any] | None, h2h: dict[str, Any] | None, standings: list[dict[str, Any]]) -> dict[str, Any]:
    """Instant, model-free brief: injury list and matchup facts straight from the data. Verdict lines stay empty until the cached model brief fills them."""
    roster = [r for r in state_roster(state) if str(r.get("league_team_id")) == team_id]
    ids = [str(r.get("sleeper_player_id") or r.get("player_id") or "") for r in roster]
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    rank = {"out": 0, "injured reserve": 0, "ir": 0, "pup": 0, "doubtful": 1, "questionable": 2}
    try:
        for r in client.table("nfl_injuries").select("sleeper_id, player_name, status, injury_type, side, return_date").in_("sleeper_id", ids).execute().data or []:
            seen.add(str(r["sleeper_id"]))
            bits = [r.get("status") or "", " ".join(x for x in [r.get("side"), r.get("injury_type")] if x).strip().lower()]
            if r.get("return_date"):
                bits.append(f"est. {_short_date(r['return_date'])}")
            items.append({"player": r.get("player_name") or "", "status": " · ".join(b for b in bits if b), "_rank": rank.get(str(r.get("status") or "").lower(), 3)})
    except Exception:
        pass
    try:
        for r in client.table("nfl_players").select("sleeper_id, full_name, injury_status, injury_body_part").in_("sleeper_id", ids).execute().data or []:
            if r.get("injury_status") and str(r["sleeper_id"]) not in seen:
                items.append({"player": r.get("full_name") or "", "status": f"{r['injury_status']} · {(r.get('injury_body_part') or '').lower()}".strip(" ·"), "_rank": rank.get(str(r["injury_status"]).lower(), 3)})
    except Exception:
        pass
    for r in roster:
        if r.get("roster_designation") == "ir" and not any(i["player"] == r.get("player_name") for i in items):
            items.append({"player": r.get("player_name") or "", "status": "on your IR", "_rank": 0})
    items.sort(key=lambda i: i.pop("_rank"))
    me = team_record(standings, owner)
    lines: list[str] = []
    headline = "No game this week"
    if opponent:
        headline = f"vs {opponent['owner_name']}"
        opp = team_record(standings, opponent["owner_name"])
        if opp and me:
            lines.append(f"{opponent['owner_name'].split(' ')[-1]} {opp['record']} at {opp['ppg']} ppg vs your {me['record']} at {me['ppg']} ppg")
        if h2h and h2h.get("games"):
            lines.append(f"Head to head: you're {h2h['wins']}-{h2h['losses']}" + (f" (last: {h2h['last']})" if h2h.get("last") else ""))
        if me and opp:
            lines.append(f"Standing points: you {me['standing_points']} ({_ordinal(me['rank'])}), {opponent['owner_name'].split(' ')[-1]} {opp['standing_points']} ({_ordinal(opp['rank'])})")
    return {"injuries": {"items": items[:5], "call": ""}, "matchup": {"headline": headline, "lines": lines, "stakes": ""}, "_source": "data"}


def _short_date(value: Any) -> str:
    try:
        d = date.fromisoformat(str(value)[:10])
        return d.strftime("%b %-d")
    except Exception:
        return str(value)


def merge_brief(data: dict[str, Any], model: dict[str, Any] | None) -> dict[str, Any]:
    """Data facts stay authoritative; the model brief contributes only the verdict sentences."""
    out = {"injuries": dict(data.get("injuries") or {}), "matchup": dict(data.get("matchup") or {})}
    if model:
        out["injuries"]["call"] = (model.get("injuries") or {}).get("call") or ""
        out["matchup"]["stakes"] = (model.get("matchup") or {}).get("stakes") or ""
        if not out["matchup"].get("lines"):
            out["matchup"]["lines"] = (model.get("matchup") or {}).get("lines") or []
    return out


def mobile_status(week: int, tiles: list[dict[str, Any]] | None) -> str:
    """One grey line for phones: 'Week 4 · 3-0, 2nd of 10 · $7 cap · vs Burruel'."""
    record = cap = opp = ""
    for t in tiles or []:
        label, value = str(t.get("label") or ""), str(t.get("value") or "")
        if label.startswith("Record"):
            rank = label.split("·", 1)[1].strip() if "·" in label else ""
            record = f"{value}, {rank}" if rank else value
        elif label == "Cap space":
            cap = f"{value} cap"
        elif value.startswith("vs "):
            opp = value
    parts = [f"Week {week}"] if week else []
    return " · ".join(p for p in parts + [record, cap, opp] if p)


def week_context(fo: Mapping[str, Any] | None, owner: str, today: date | None = None) -> str:
    """'This week' block for the chat: date, NFL week, opponent, injuries, power ranks and starters.

    Built from the Front Office data the page already loaded, so it costs no extra queries.
    """
    if not fo:
        return ""
    today = today or date.today()
    lines = [f"## This week (as of {today.strftime('%a %b %-d, %Y')})", f"- NFL week {fo.get('week') or '?'} of the {fo.get('season') or ''} season."]
    brief = fo.get("data_brief") or {}
    mu = brief.get("matchup") or {}
    if fo.get("opponent"):
        lines.append(f"- {owner}'s matchup this week: {mu.get('headline') or 'vs ' + str(fo['opponent'].get('owner_name'))}." + (" " + "; ".join(mu.get("lines") or []) + "." if mu.get("lines") else ""))
    else:
        lines.append(f"- {owner} has no matchup this week.")
    items = (brief.get("injuries") or {}).get("items") or []
    lines.append("- Injuries on " + owner + "'s roster: " + ("; ".join(f"{i.get('player')} ({i.get('status')})" for i in items) if items else "none reported") + ".")
    rankings = fo.get("rankings") or []
    if rankings:
        ordered = sorted(rankings, key=lambda r: r.get("now_rank") or 99)
        lines.append("- Power rankings now (app's lineup-strength model, not standings): " + ", ".join(f"#{r.get('now_rank')} {r.get('owner')}" for r in ordered) + ".")
        mine = next((r for r in rankings if r.get("owner") == owner), None)
        if mine:
            lines.append(f"- {owner}: power #{mine.get('now_rank')} now, dynasty #{mine.get('dynasty_rank')}; strongest slot {mine.get('strongest_slot') or '?'}, weakest slot {mine.get('weakest_slot') or '?'}" + (f" ({mine.get('weakest_note')})" if mine.get("weakest_note") else "") + ".")
            if mine.get("starters"):
                lines.append(f"- {owner}'s best lineup by the app's strength model (position, player, strength score blending recent and season PPG with market tier): " + ", ".join(mine["starters"]) + ".")
        opp_name = (fo.get("opponent") or {}).get("owner_name")
        opp = next((r for r in rankings if opp_name and r.get("owner") == opp_name), None)
        if opp and opp.get("starters"):
            lines.append(f"- Opponent {opp_name}'s best lineup by the model: " + ", ".join(opp["starters"]) + ".")
    return "\n".join(lines)
