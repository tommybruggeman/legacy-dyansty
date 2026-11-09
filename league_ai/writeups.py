"""Weekly player write-ups: a short recap paragraph for every NFL QB/RB/WR/TE who mattered this week.

Runs on Tuesday after the stats lock (.github/workflows/player-writeups.yml):

    python -m league_ai.writeups              # newest week with stats
    python -m league_ai.writeups --week 9     # a given week
    python -m league_ai.writeups --dry-run    # build the facts, call nothing, save nothing

Everything the model sees comes from our own tables (nfl_player_stats, nfl_games, nfl_injuries), never a
league's rosters or owners, so one write-up per player per week is shared by every league and every viewer.
Each one is written once and saved to player_writeups; a rerun skips players already written.
Cost: Claude Haiku 5.5 at low effort, roughly $0.0004 per player.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from statistics import mean
from typing import Any, Iterable

from league_ai.nfl_data import team_code

MODEL = os.getenv("WRITEUP_MODEL", "claude-haiku-5-5")
EFFORT = os.getenv("WRITEUP_EFFORT", "low")
POSITIONS = ("QB", "RB", "WR", "TE")
MIN_SNAP_PCT = 25          # played a real role this week...
MIN_SEASON_GAMES = 3       # ...or is an established player who missed it
MAX_PLAYERS = 450          # hard stop on spend: about $0.20 a week at most
PRICE_IN, PRICE_OUT = 0.10, 0.50  # $ per million tokens (Haiku 5.5, prompts under 100K)
STAT_COLUMNS = ("gsis_id, sleeper_id, player_name, position, team, opponent, is_home, week, completions, attempts, passing_yards, "
                "passing_tds, interceptions, carries, rushing_yards, rushing_tds, targets, receptions, receiving_yards, receiving_tds, "
                "target_share, offense_pct, fantasy_points_ppr")

SYSTEM = """You write the weekly recap shown at the top of a fantasy football player page.

Write one paragraph of 3 or 4 sentences, at most 85 words, in plain, confident language a fantasy manager skims on a phone.
Lead with what happened this week, then the trend that matters for his fantasy value (role, snaps, targets or carries,
efficiency), then what to watch next. Use only the facts given. Never invent quotes, injuries, depth chart moves, coaches'
comments or numbers that are not in the facts. Round sensibly. No headings, lists, emoji or hype words.
If he did not play, say why when the facts say it, and what his return outlook is."""


def _n(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _snap(r: dict[str, Any]) -> int | None:
    pct = r.get("offense_pct")
    if pct is None:
        return None
    pct = _n(pct)
    return int(round(pct * 100 if pct <= 1 else pct))


def _line(r: dict[str, Any], pos: str) -> str:
    """'17 car, 57 rush yds, 1 rush TD; 4/5 rec, 31 yds' in plain words."""
    parts = []
    if pos == "QB" and _n(r.get("attempts")):
        parts.append(f"{int(_n(r.get('completions')))}/{int(_n(r.get('attempts')))} passing, {int(_n(r.get('passing_yards')))} yds, "
                     f"{int(_n(r.get('passing_tds')))} TD, {int(_n(r.get('interceptions')))} INT")
    if _n(r.get("carries")):
        parts.append(f"{int(_n(r.get('carries')))} carries, {int(_n(r.get('rushing_yards')))} rush yds, {int(_n(r.get('rushing_tds')))} rush TD")
    if _n(r.get("targets")) or _n(r.get("receptions")):
        parts.append(f"{int(_n(r.get('receptions')))} catches on {int(_n(r.get('targets')))} targets, {int(_n(r.get('receiving_yards')))} rec yds, "
                     f"{int(_n(r.get('receiving_tds')))} rec TD")
    return "; ".join(parts) or "no touches"


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    return round(mean(_n(r.get(key)) for r in rows), 1) if rows else 0.0


def week_ranks(rows: Iterable[dict[str, Any]], week: int) -> dict[str, int]:
    """gsis_id -> rank at his position by PPR points that week (1 = best)."""
    by_pos: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if int(r.get("week") or 0) == week and r.get("position") in POSITIONS:
            by_pos.setdefault(r["position"], []).append(r)
    out = {}
    for group in by_pos.values():
        group.sort(key=lambda r: _n(r.get("fantasy_points_ppr")), reverse=True)
        for i, r in enumerate(group, start=1):
            out[str(r["gsis_id"])] = i
    return out


def game_result(games: dict[tuple[int, str], dict[str, Any]], week: int, team: str) -> str | None:
    g = games.get((week, team))
    if not g or g.get("home_score") is None:
        return None
    home = team_code(g.get("home_team")) == team
    us, them = (g["home_score"], g["away_score"]) if home else (g["away_score"], g["home_score"])
    opp = team_code(g.get("away_team") if home else g.get("home_team"))
    res = "won" if us > them else "lost" if us < them else "tied"
    return f"{'vs' if home else 'at'} {opp}, {res} {us}-{them}"


def player_facts(rows: list[dict[str, Any]], week: int, *, rank: int | None, games: dict, injury: dict[str, Any] | None) -> dict[str, Any] | None:
    """The fact sheet for one player (all his REG rows this season), or None when he isn't worth a write-up this week."""
    rows = sorted(rows, key=lambda r: int(r["week"]))
    this = next((r for r in rows if int(r["week"]) == week), None)
    before = [r for r in rows if int(r["week"]) < week]
    last = rows[-1]
    pos = str(last.get("position") or "")
    team = team_code(last.get("team"))
    if pos not in POSITIONS:
        return None
    snap = _snap(this) if this else None
    if this is None and len(before) < MIN_SEASON_GAMES:
        return None
    if this is not None and (snap or 0) < MIN_SNAP_PCT and len(before) < MIN_SEASON_GAMES:
        return None
    season_snaps = [s for s in (_snap(r) for r in rows) if s is not None]
    recent = [s for s in (_snap(r) for r in rows[-3:]) if s is not None]
    facts: dict[str, Any] = {
        "player": last.get("player_name"), "position": pos, "team": team, "week": week,
        "games_this_season": len(rows),
        "this_week": None,
        "season_per_game": {
            "ppr_points": _avg(rows, "fantasy_points_ppr"),
            **({"pass_yds": _avg(rows, "passing_yards"), "pass_td": _avg(rows, "passing_tds")} if pos == "QB" else {}),
            "carries": _avg(rows, "carries"), "rush_yds": _avg(rows, "rushing_yards"),
            "targets": _avg(rows, "targets"), "catches": _avg(rows, "receptions"), "rec_yds": _avg(rows, "receiving_yards"),
        },
        "snap_share_season_avg": round(mean(season_snaps)) if season_snaps else None,
        "snap_share_last_3_games": recent or None,
    }
    if this is not None:
        ts = this.get("target_share")
        facts["this_week"] = {
            "game": game_result(games, week, team_code(this.get("team"))),
            "box_score": _line(this, pos),
            "ppr_points": round(_n(this.get("fantasy_points_ppr")), 1),
            "rank_at_position_this_week": rank,
            "snap_share": snap,
            "target_share": f"{round(_n(ts) * 100)}%" if ts is not None and _n(ts) else None,
        }
    else:
        facts["this_week"] = {"did_not_play": True, "team_game": game_result(games, week, team)}
    if injury and str(injury.get("status") or "").lower() not in ("", "active", "healthy"):
        facts["injury_report"] = {k: injury.get(k) for k in ("status", "injury_type", "detail", "short_comment", "return_date") if injury.get(k)}
    return facts


def prompt(facts: dict[str, Any]) -> str:
    import json
    return "Facts (JSON):\n" + json.dumps(facts, default=str) + "\n\nWrite the recap."


# ---- loaders -----------------------------------------------------------------------------------------

def load_stats(client: Any, season: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, 30000, 1000):
        chunk = (client.table("nfl_player_stats").select(STAT_COLUMNS).eq("season", season).eq("season_type", "REG")
                 .in_("position", list(POSITIONS)).order("gsis_id").order("week").range(start, start + 999).execute().data or [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
    return rows


def load_games(client: Any, season: int) -> dict[tuple[int, str], dict[str, Any]]:
    rows = client.table("nfl_games").select("week,home_team,away_team,home_score,away_score").eq("season", season).limit(400).execute().data or []
    out = {}
    for g in rows:
        for t in (g.get("home_team"), g.get("away_team")):
            out[(int(g["week"]), team_code(t))] = g
    return out


def load_injuries(client: Any) -> dict[str, dict[str, Any]]:
    rows = client.table("nfl_injuries").select("gsis_id,status,injury_type,detail,short_comment,return_date").limit(3000).execute().data or []
    return {str(r["gsis_id"]): r for r in rows if r.get("gsis_id")}


def already_written(client: Any, season: int, week: int) -> set[str]:
    rows = client.table("player_writeups").select("gsis_id").eq("season", season).eq("week", week).limit(2000).execute().data or []
    return {str(r["gsis_id"]) for r in rows}


# ---- writing -----------------------------------------------------------------------------------------

def write_one(ai: Any, facts: dict[str, Any]) -> tuple[str | None, int, int]:
    """(text or None, input tokens, output tokens). Haiku 5.5 thinks by default; low effort keeps it short."""
    resp = ai.messages.create(
        model=MODEL, max_tokens=1500, system=SYSTEM, output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": prompt(facts)}],
    )
    usage = getattr(resp, "usage", None)
    tin, tout = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
    if getattr(resp, "stop_reason", None) not in ("end_turn", "stop_sequence"):
        return None, tin, tout
    text = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return (text or None), tin, tout


def chips(facts: dict[str, Any]) -> list[str]:
    """Up to four short tags under the paragraph, built from the facts (no AI)."""
    out = []
    inj = facts.get("injury_report") or {}
    if inj.get("status"):
        out.append(f"{inj['status']}{' · ' + str(inj['injury_type']).lower() if inj.get('injury_type') else ''}")
    tw = facts.get("this_week") or {}
    if tw.get("rank_at_position_this_week"):
        out.append(f"{facts['position']}{tw['rank_at_position_this_week']} this week")
    last3, avg = facts.get("snap_share_last_3_games"), facts.get("snap_share_season_avg")
    if last3 and avg is not None and len(last3) >= 2:
        trend = "▲" if last3[-1] >= avg + 5 else "▼" if last3[-1] <= avg - 5 else "·"
        out.append(f"Snaps {trend} {last3[-1]}%")
    tg = (facts.get("season_per_game") or {}).get("targets")
    if facts["position"] in ("WR", "TE", "RB") and tg:
        out.append(f"{tg} tgt/g")
    return out[:4]


def run(client: Any, ai: Any, season: int, week: int | None, dry_run: bool = False, workers: int = 6) -> dict[str, Any]:
    rows = load_stats(client, season)
    if not rows:
        return {"week": week, "players": 0, "written": 0, "note": "no stats"}
    week = week or max(int(r["week"]) for r in rows)
    games, injuries = load_games(client, season), load_injuries(client)
    ranks = week_ranks(rows, week)
    by_player: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if int(r["week"]) <= week:
            by_player.setdefault(str(r["gsis_id"]), []).append(r)
    done = set() if dry_run else already_written(client, season, week)
    todo = []
    for gsis, prs in by_player.items():
        if gsis in done:
            continue
        f = player_facts(prs, week, rank=ranks.get(gsis), games=games, injury=injuries.get(gsis))
        if f:
            todo.append((gsis, str(prs[-1].get("sleeper_id") or ""), f))
    todo.sort(key=lambda t: -_n((t[2].get("season_per_game") or {}).get("ppr_points")))
    todo = todo[:MAX_PLAYERS]
    if dry_run or ai is None:
        return {"week": week, "players": len(todo), "written": 0, "skipped_existing": len(done), "dry_run": True}

    def one(item: tuple[str, str, dict[str, Any]]) -> tuple[dict[str, Any] | None, int, int]:
        gsis, sleeper, f = item
        try:
            text, tin, tout = write_one(ai, f)
        except Exception as exc:  # one bad call never stops the run; only the error type is printed
            print(f"write failed: {type(exc).__name__}", file=sys.stderr)
            return None, 0, 0
        if not text:
            return None, tin, tout
        return {"season": season, "week": week, "gsis_id": gsis, "sleeper_id": sleeper or None, "position": f["position"],
                "text": text, "chips": chips(f), "model": MODEL}, tin, tout

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, todo))
    saved = [r for r, _, _ in results if r]
    tin = sum(t for _, t, _ in results)
    tout = sum(t for _, _, t in results)
    for start in range(0, len(saved), 200):
        client.table("player_writeups").upsert(saved[start:start + 200], on_conflict="season,week,gsis_id").execute()
    cost = round(tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT, 3)
    return {"week": week, "players": len(todo), "written": len(saved), "skipped_existing": len(done),
            "input_tokens": tin, "output_tokens": tout, "cost_usd": cost}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    season = args.season
    if season is None:
        top = client.table("nfl_player_stats").select("season").eq("season_type", "REG").order("season", desc=True).limit(1).execute().data
        season = int(top[0]["season"]) if top else None
    if season is None:
        print("no NFL stats loaded")
        return 0
    ai = None
    if not args.dry_run:
        import anthropic
        from league_ai.config import api_key

        ai = anthropic.Anthropic(api_key=api_key() or None, max_retries=3)
    summary = run(client, ai, season, args.week, dry_run=args.dry_run)
    print({"season": season, **summary})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
