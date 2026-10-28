"""Every trade idea the finder shows, and whether anything like it happened. No model call.

Real trades are rare, so the trade engine learns slowly from them alone. Each time find_trade_targets answers, the ideas
it showed (partner, who you'd get, and the open / close / walk-away packages) are saved to league_ai_experiments as
'trade_ideas_v1'. The daily learning job then checks the league's completed trades: an idea 'happened' when a completed
trade with that partner moved one of the players you'd get and one of the players you'd send, and the rung it landed
closest to says whether the ladder was priced right. After NOT_MADE_AFTER_DAYS with nothing like it, it's 'not_made'.
The weekly report counts them. Trades made in the app land in the same history table, so this keeps working off Sleeper.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

EXPERIMENT = "trade_ideas_v1"
NOT_MADE_AFTER_DAYS = 30
RUNGS = ("open_with", "likely_close", "walk_away_after")


def _name(label: Any) -> str:
    """'Puka Nacua (WR4, 21.3 ppg, $30 x 2yr)' -> 'Puka Nacua'."""
    return str(label or "").split(" (")[0].strip()


def compact(out: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ideas in one find_trade_targets answer, as names only."""
    rows = []
    for idea in out.get("ideas") or ([out] if out.get("fair_deal_found") else []):
        gets = idea.get("you_get") or ([idea["target"]] if idea.get("target") else [])
        row = {"partner": idea.get("partner"), "gets": [_name(g) for g in gets]}
        for rung in RUNGS:
            if idea.get(rung):
                row[rung] = [_name(p) for p in idea[rung]]
        if idea.get("their_share"):
            row["their_share"] = dict(idea["their_share"])   # numbers per rung, for fitting an acceptance model later
        if row["partner"] and row["gets"] and row.get("open_with"):
            rows.append(row)
    return rows


def log(client: Any, *, league_id: str, user_id: str | None, conversation_id: str | None, question: str | None, out: Mapping[str, Any]) -> bool:
    ideas = compact(out)
    if not ideas or client is None or os.environ.get("LEAGUE_AI_IDEA_LOG") == "off":   # off in the practice test and stress runs
        return False
    try:
        client.table("league_ai_experiments").insert({
            "league_id": league_id, "user_id": user_id, "experiment": EXPERIMENT, "conversation_id": conversation_id,
            "question": (question or "")[:2000], "result": {"ideas": ideas, "goal": out.get("goal"), "status": "open"}, "cost_usd": 0,
        }).execute()
        return True
    except Exception as exc:
        print(f"IDEA_LOG_ERROR {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return False


def _ts(v: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def trades_by_id(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Completed trades from league_history_events: trade id -> {owners, players (casefolded names), occurred_at}."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        parts = str(r.get("event_id") or "").split(":")
        tid = parts[1] if len(parts) > 1 else str(r.get("event_id"))
        t = out.setdefault(tid, {"owners": set(), "players": set(), "occurred_at": r.get("occurred_at")})
        for o in [r.get("owner_name"), *(r.get("counterparty_names") or [])]:
            if o:
                t["owners"].add(str(o).casefold())
        if r.get("player_name"):
            t["players"].add(str(r["player_name"]).casefold())
    return out


def match(idea: Mapping[str, Any], asked: datetime, trades: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """The completed trade this idea turned into, if any, and the rung whose package overlaps it most."""
    gets = {g.casefold() for g in idea.get("gets") or []}
    sends = {p.casefold() for rung in RUNGS for p in idea.get(rung) or []}
    partner = str(idea.get("partner") or "").casefold()
    for tid, t in trades.items():
        when = _ts(t.get("occurred_at"))
        if not when or when < asked - timedelta(days=1):
            continue
        if partner and t["owners"] and partner not in t["owners"]:
            continue
        if t["players"] & gets and t["players"] & sends:
            overlap = {rung: len({p.casefold() for p in idea.get(rung) or []} & t["players"]) / max(1, len(idea.get(rung) or []))
                       for rung in RUNGS if idea.get(rung)}
            return {"trade": tid, "closest_rung": max(overlap, key=overlap.get), "occurred_at": t.get("occurred_at")}
    return None


def grade(client: Any, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    """Settle logged ideas against completed trades (service role, daily learning job)."""
    now = now or datetime.now(timezone.utc)
    rows = (client.table("league_ai_experiments").select("id, league_id, created_at, result").eq("experiment", EXPERIMENT)
            .gte("created_at", (now - timedelta(days=150)).isoformat()).limit(5000).execute().data or [])
    out = {"happened": 0, "not_made": 0, "open": 0}
    trades: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        res = dict(r.get("result") or {})
        if res.get("status") != "open":
            continue
        lid = str(r["league_id"])
        if lid not in trades:
            ev = (client.table("league_history_events").select("event_id, owner_name, counterparty_names, player_name, occurred_at").eq("league_id", lid)
                  .eq("kind", "trade").gte("occurred_at", (now - timedelta(days=160)).isoformat()).limit(5000).execute().data or [])
            trades[lid] = trades_by_id(ev)
        asked = _ts(r.get("created_at")) or now
        hits = [h for h in (match(i, asked, trades[lid]) for i in res.get("ideas") or []) if h]
        if hits:
            res.update(status="happened", match=hits[0])
        elif now - asked >= timedelta(days=NOT_MADE_AFTER_DAYS):
            res["status"] = "not_made"
        else:
            out["open"] += 1
            continue
        out[res["status"]] += 1
        if not dry_run:
            client.table("league_ai_experiments").update({"result": res}).eq("id", r["id"]).execute()
    return out


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    status = [str((r.get("result") or {}).get("status") or "open") for r in rows]
    rungs = [((r.get("result") or {}).get("match") or {}).get("closest_rung") for r in rows]
    return {"answers_logged": len(rows), "ideas_shown": sum(len((r.get("result") or {}).get("ideas") or []) for r in rows),
            "happened": status.count("happened"), "not_made": status.count("not_made"), "still_open": status.count("open"),
            "closest_rung": {k: rungs.count(k) for k in RUNGS if rungs.count(k)}}


def load(client: Any, league_id: str) -> list[dict[str, Any]]:
    try:
        return (client.table("league_ai_experiments").select("created_at, result").eq("league_id", league_id).eq("experiment", EXPERIMENT)
                .order("created_at").limit(2000).execute().data or [])
    except Exception:
        return []
