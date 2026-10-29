"""GM's Risers, graded. No model call.

Home shows GM's Risers from Thursday to Saturday: the players whose projection this week beats their season average by
the most (api.home.risers). The first time a league's list is built each week it is saved to league_ai_experiments as
'gm_risers_v2' (player, season average, projection). Once the week is scored the daily learning job settles it: a riser
'hit' when he outscored his season average, and the mean of (actual - average) says how much the list really rose.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

EXPERIMENT = "gm_risers_v2"   # v1 (week 9 only) compared league-scoring projections with full-PPR averages
LOGGED = 10   # the top of the list is what owners see


def question(season: int, week: int) -> str:
    return f"risers {season} week {week}"


def log(client: Any, *, league_id: str, sleeper_id: str, season: int, week: int, rows: Iterable[Mapping[str, Any]]) -> bool:
    """Save this week's list once per owner (row security only lets an owner see their own rows, so a league
    can hold one copy per owner; grade() keeps the first). False when already saved, empty or switched off."""
    picks = [{"player_id": r["player_id"], "avg": r["avg"], "proj": r["proj"]} for r in list(rows)[:LOGGED]]
    if not picks or client is None or os.environ.get("LEAGUE_AI_IDEA_LOG") == "off":
        return False
    try:
        seen = (client.table("league_ai_experiments").select("id").eq("league_id", league_id).eq("experiment", EXPERIMENT)
                .eq("question", question(season, week)).limit(1).execute().data or [])
        if seen:
            return False
        client.table("league_ai_experiments").insert({
            "league_id": league_id, "experiment": EXPERIMENT, "conversation_id": None,
            "question": question(season, week), "cost_usd": 0,
            "result": {"season": season, "week": week, "sleeper_id": sleeper_id, "players": picks, "status": "open"},
        }).execute()
        return True
    except Exception as exc:
        print(f"RISERS_LOG_ERROR {type(exc).__name__}", flush=True)
        return False


def settle(picks: Iterable[Mapping[str, Any]], actual: Mapping[str, float]) -> dict[str, Any]:
    """Hits (beat his season average) and the mean real rise, for players who have a scored line."""
    graded = [(p, actual[p["player_id"]]) for p in picks if p["player_id"] in actual]
    if not graded:
        return {"graded": 0, "hits": 0, "mean_rise": None, "mean_projected_rise": None}
    return {
        "graded": len(graded),
        "hits": sum(pts > p["avg"] for p, pts in graded),
        "mean_rise": round(sum(pts - p["avg"] for p, pts in graded) / len(graded), 2),
        "mean_projected_rise": round(sum(p["proj"] - p["avg"] for p, _ in graded) / len(graded), 2),
    }


def grade(client: Any, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    """Settle every open list whose week has been scored (service role, daily learning job)."""
    from api import league, projections

    now = now or datetime.now(timezone.utc)
    rows = (client.table("league_ai_experiments").select("id, league_id, question, result").eq("experiment", EXPERIMENT)
            .gte("created_at", (now - timedelta(days=150)).isoformat()).order("created_at").limit(2000).execute().data or [])
    out = {"graded": 0, "open": 0}
    kept: set[tuple[str, str]] = set()
    for r in rows:
        res = dict(r.get("result") or {})
        key = (str(r.get("league_id")), str(r.get("question")))
        first = key not in kept
        kept.add(key)
        if res.get("status") != "open":
            continue
        if not first:  # another owner's copy of the same list
            if not dry_run:
                client.table("league_ai_experiments").update({"result": {**res, "status": "duplicate"}}).eq("id", r["id"]).execute()
            continue
        sid, season, week = str(res.get("sleeper_id") or ""), int(res.get("season") or 0), int(res.get("week") or 0)
        if not sid or league.last_scored_week(sid) < week:
            out["open"] += 1
            continue
        scoring = projections.league_scoring(sid)
        lines = projections.week_lines("stats", season, week, 86400)
        actual = {pid: projections.score(line["stats"], scoring) for pid, line in lines.items() if line.get("stats")}
        res.update(status="graded", **settle(res.get("players") or [], actual))
        out["graded"] += 1
        if not dry_run:
            client.table("league_ai_experiments").update({"result": res}).eq("id", r["id"]).execute()
    return out
