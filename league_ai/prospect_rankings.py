"""Prospect Power Rankings: a weekly GM article ranking the next rookie class (Tuesdays, .github/workflows/prospect-rankings.yml).

    python -m league_ai.prospect_rankings              # write this week's edition
    python -m league_ai.prospect_rankings --dry-run    # build the candidate list, call nothing, save nothing

How an edition is made:
1. Candidates come from our own college table (nfl_prospects, filled from cfbfastR-data by the NFL data sync): every
   QB/RB/WR/TE with real production this college season, plus anyone on last week's board.
2. One Claude Sonnet 5.5 call reads this week's big boards and mock drafts with web search and ranks the top 20 for a
   superflex dynasty rookie draft, writing 3-4 sentences on each.
3. Every ranked player is matched back to our table: the stat line, school, size and headshot come from our data, never
   the model, and anyone we can't match is dropped. Movement (up, down, new) is measured against last week's edition.
4. The edition is saved to prospect_rankings and shared by every league (it holds no league data).

Cost: about $0.30-0.60 a week (Sonnet 5.5 tokens plus up to 8 web searches at $0.01).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Any

from league_ai import usage

MODEL = os.getenv("PROSPECT_MODEL", "claude-sonnet-5-5")
EFFORT = os.getenv("PROSPECT_EFFORT", "medium")
POSITIONS = ("QB", "RB", "WR", "TE")
BOARD_SIZE = 20
PER_POSITION = 30          # candidates sent per position (by production)
MAX_SEARCHES = 8
HEADSHOT = "https://a.espncdn.com/i/headshots/college-football/players/full/{id}.png"
COLUMNS = ("cfbd_athlete_id, season, name, search_name, position, college, conference, class_year, draft_eligible, height, weight, "
           "usage_overall, pass_att, pass_yds, pass_td, pass_int, rush_att, rush_yds, rush_td, receptions, rec_yds, rec_td, scrimmage_yds")

SYSTEM = """You are the GM of a superflex dynasty fantasy football league writing the weekly Prospect Power Rankings: the top
20 offensive prospects (QB, RB, WR, TE) for next spring's NFL draft, ranked for a superflex rookie draft.

Use web search to read this week's dynasty rookie big boards and the newest NFL mock drafts (a few searches are enough).
Rank for fantasy value in superflex: NFL draft capital, college production and role, age and size, and what the boards
say. Quarterbacks gain value in superflex. Only rank players eligible for the coming NFL draft.

Every player you rank must come from the candidate list you are given; copy his name and athlete id exactly. Use only
facts from the candidate list or from pages you read: never invent stats, quotes, injuries or mock slots.

Each writeup is 3 or 4 sentences, plain and confident, for a fantasy manager on a phone: how he is used, what stands out
in his production, where the boards and mocks have him, and his fantasy outlook. No hype words, no emoji.

Reply with only a JSON object, no other text:
{"headline": "<at most 90 characters>",
 "dek": "<one sentence on this week's story>",
 "lede": "<2-3 sentences opening the article>",
 "players": [{"rank": 1, "athlete_id": "<id from the list>", "name": "<name>", "role": "<2-4 words, e.g. Alpha WR1>",
              "tags": ["<0-2 of: Rising, Falling, Injury, Consensus No. 1>"], "mock": "<e.g. Pick 12 · Jets, or Not in mocks>",
              "boards": "<short, e.g. DN 3 · DS 5, or empty>", "text": "<3-4 sentences>"}],
 "sources": [{"title": "<site · article (date)>", "url": "<url you read>"}]}"""


def _n(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", (name or "").lower()))


def production(r: dict[str, Any]) -> float:
    """One number to sort a position's candidates by: yards with touchdowns worth 20."""
    if r.get("position") == "QB":
        return _n(r.get("pass_yds")) + 20 * _n(r.get("pass_td")) - 25 * _n(r.get("pass_int")) + _n(r.get("rush_yds")) + 20 * _n(r.get("rush_td"))
    return _n(r.get("scrimmage_yds")) + 20 * (_n(r.get("rush_td")) + _n(r.get("rec_td")))


def stat_line(r: dict[str, Any]) -> str:
    """'2,410 pass yds · 21 TD · 6 INT · 310 rush yds' from our table (season so far)."""
    def f(v: Any) -> str:
        return f"{int(_n(v)):,}"
    parts = []
    if r.get("position") == "QB" and _n(r.get("pass_att")):
        parts += [f"{f(r.get('pass_yds'))} pass yds", f"{f(r.get('pass_td'))} TD", f"{f(r.get('pass_int'))} INT"]
        if _n(r.get("rush_yds")) >= 100:
            parts.append(f"{f(r.get('rush_yds'))} rush yds")
        return " · ".join(parts)
    if _n(r.get("rush_att")) >= 20:
        parts += [f"{f(r.get('rush_att'))} car", f"{f(r.get('rush_yds'))} rush yds", f"{f(r.get('rush_td'))} rush TD"]
    if _n(r.get("receptions")):
        parts += [f"{f(r.get('receptions'))} rec", f"{f(r.get('rec_yds'))} rec yds", f"{f(r.get('rec_td'))} rec TD"]
    return " · ".join(parts) or "No stats in our college feed yet"


def size_line(r: dict[str, Any]) -> str:
    h, w, yr = _n(r.get("height")), _n(r.get("weight")), int(_n(r.get("class_year")))
    out = []
    if h:
        out.append(f"{int(h) // 12}'{int(h) % 12}\"")
    if w:
        out.append(str(int(w)))
    if yr:
        out.append({1: "Fr", 2: "So", 3: "Jr", 4: "Sr"}.get(yr, "Sr"))
    return " · ".join(out)


# ---- data -------------------------------------------------------------------------------------------

def latest_season(client: Any) -> int | None:
    top = client.table("nfl_prospects").select("season").order("season", desc=True).limit(1).execute().data
    return int(top[0]["season"]) if top else None


def load_prospects(client: Any, season: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pos in POSITIONS:
        rows += (client.table("nfl_prospects").select(COLUMNS).eq("season", season).eq("position", pos)
                 .gte("class_year", 2).limit(2000).execute().data or [])
    return rows


def last_edition(client: Any) -> dict[str, Any] | None:
    try:
        rows = client.table("prospect_rankings").select("*").order("edition_date", desc=True).limit(1).execute().data
    except Exception:
        return None
    return rows[0] if rows else None


def candidates(rows: list[dict[str, Any]], keep_ids: set[str]) -> list[dict[str, Any]]:
    """The top producers at each position, plus anyone on last week's board."""
    out: list[dict[str, Any]] = []
    for pos in POSITIONS:
        at = sorted((r for r in rows if r.get("position") == pos), key=production, reverse=True)
        out += at[:PER_POSITION]
    have = {str(r["cfbd_athlete_id"]) for r in out}
    out += [r for r in rows if str(r["cfbd_athlete_id"]) in keep_ids and str(r["cfbd_athlete_id"]) not in have]
    return out


def candidate_text(cands: list[dict[str, Any]]) -> str:
    lines = []
    for r in cands:
        lines.append(f"{r['cfbd_athlete_id']} | {r['name']} | {r['position']} | {r.get('college') or ''} | {size_line(r)} | {stat_line(r)}")
    return "\n".join(lines)


def prompt(cands: list[dict[str, Any]], prev: dict[str, Any] | None, today: date, season: int) -> str:
    prev_lines = ""
    if prev:
        prev_lines = "\n\nLast week's board (rank, name):\n" + "\n".join(
            f"{p['rank']}. {p['name']}" for p in (prev.get("data") or {}).get("players", []))
    return (f"Today is {today.isoformat()}. College season {season}; the draft is in spring {season + 1}.\n\n"
            f"Candidates (athlete id | name | pos | school | size and class | season stats from our feed; TD counts can run low):\n"
            f"{candidate_text(cands)}{prev_lines}\n\nWrite this week's Prospect Power Rankings.")


# ---- writing ----------------------------------------------------------------------------------------

def _json_from(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def write(ai: Any, user_prompt: str) -> tuple[dict[str, Any] | None, dict[str, int], int]:
    """(parsed article or None, summed token counts, latency ms). Web search can pause a long turn; resume it."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "web_searches": 0}
    started = time.monotonic()
    text = ""
    for _ in range(4):
        with ai.messages.stream(
            model=MODEL, max_tokens=32000, system=SYSTEM, output_config={"effort": EFFORT},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}],
            messages=messages,
        ) as stream:
            resp = stream.get_final_message()
        for k, v in usage.response_usage(resp).items():
            totals[k] = totals.get(k, 0) + v
        if resp.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
            continue
        if resp.stop_reason in ("end_turn", "stop_sequence"):
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        break
    return _json_from(text), totals, int((time.monotonic() - started) * 1000)


# ---- assembling -------------------------------------------------------------------------------------

def assemble(raw: dict[str, Any], rows: list[dict[str, Any]], prev: dict[str, Any] | None) -> dict[str, Any]:
    """Match the model's board back to our table; our data supplies every fact shown beside the writeup."""
    by_id = {str(r["cfbd_athlete_id"]): r for r in rows}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_name.setdefault(_norm(r["name"]), []).append(r)
    prev_rank = {str(p.get("athlete_id")): int(p["rank"]) for p in ((prev or {}).get("data") or {}).get("players", [])}
    players, seen = [], set()
    for p in sorted(raw.get("players") or [], key=lambda x: _n(x.get("rank")) or 99):
        r = by_id.get(str(p.get("athlete_id") or ""))
        if r is None:
            named = by_name.get(_norm(str(p.get("name") or "")), [])
            r = named[0] if len(named) == 1 else None
        if r is None or str(r["cfbd_athlete_id"]) in seen or not str(p.get("text") or "").strip():
            continue
        aid = str(r["cfbd_athlete_id"])
        seen.add(aid)
        rank = len(players) + 1
        was = prev_rank.get(aid)
        move = "new" if was is None else "up" if was > rank else "down" if was < rank else "same"
        tags = [t for t in (p.get("tags") or []) if t in ("Rising", "Falling", "Injury", "Consensus No. 1")][:2]
        players.append({
            "rank": rank, "athlete_id": aid, "name": r["name"], "pos": r["position"], "school": r.get("college") or "",
            "size": size_line(r), "line": stat_line(r), "role": str(p.get("role") or "")[:40], "tags": tags,
            "mock": str(p.get("mock") or "")[:40], "boards": str(p.get("boards") or "")[:40], "text": str(p["text"]).strip(),
            "photo": HEADSHOT.format(id=aid), "move": move, "was": was,
        })
        if len(players) == BOARD_SIZE:
            break
    sources = [{"title": str(s.get("title") or "")[:120], "url": str(s.get("url") or "")} for s in (raw.get("sources") or [])
               if str(s.get("url") or "").startswith("https://")][:8]
    return {"headline": str(raw.get("headline") or "Prospect Power Rankings")[:110], "dek": str(raw.get("dek") or ""),
            "lede": str(raw.get("lede") or ""), "players": players, "sources": sources}


def run(client: Any, ai: Any, *, dry_run: bool = False, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    season = latest_season(client)
    if season is None:
        return {"skipped": "no college prospects loaded"}
    rows = load_prospects(client, season)
    prev = last_edition(client)
    keep = {str(p.get("athlete_id")) for p in ((prev or {}).get("data") or {}).get("players", [])}
    cands = candidates(rows, keep)
    try:  # no table yet (migration not run): stop before spending anything
        client.table("prospect_rankings").select("edition_date").limit(1).execute()
    except Exception:
        return {"skipped": "run supabase/migrations/20261227_prospect_rankings.sql first"}
    if dry_run or ai is None:
        return {"season": season, "candidates": len(cands), "previous": (prev or {}).get("edition_date")}
    raw, tokens, latency = write(ai, prompt(cands, prev, today, season))
    row = usage.usage_row(league_id=None, user_id=None, feature="prospect_rankings", model=MODEL, effort=EFFORT, latency_ms=latency,
                          ok=raw is not None, **tokens)
    usage.record(client, row)
    if raw is None:
        return {"season": season, "error": "no article came back", "cost_usd": row["cost_usd"]}
    data = assemble(raw, rows, prev)
    if len(data["players"]) < 10:
        return {"season": season, "error": f"only {len(data['players'])} players matched our table", "cost_usd": row["cost_usd"]}
    number = int((prev or {}).get("number") or 0) + (0 if (prev or {}).get("edition_date") == today.isoformat() else 1)
    client.table("prospect_rankings").upsert({"edition_date": today.isoformat(), "season": season, "number": max(number, 1), "data": data,
                                              "model": MODEL, "cost_usd": row["cost_usd"]}, on_conflict="edition_date").execute()
    return {"season": season, "players": len(data["players"]), "cost_usd": row["cost_usd"], "searches": tokens.get("web_searches")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    ai = None
    if not args.dry_run:
        import anthropic
        from league_ai.config import api_key

        ai = anthropic.Anthropic(api_key=api_key() or None, max_retries=3)
    print(run(client, ai, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
