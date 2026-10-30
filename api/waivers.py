"""Pickups and drops in the app: FAAB bids, Sunday free agency, drops with dead cap.

The rules live in the database (supabase/migrations/20261213_app_waivers.sql): bids run Wednesday and Sunday at
3:00 AM Eastern (highest bid wins, a tie goes to the earlier bid, salary = bid with a $1 minimum, one season);
Sunday after the run any free agent can be added at $1; a drop charges dead cap for every season still owed at the
league's rate ($1 contracts free); nobody moves once his game has kicked off. This module turns rows into what the
app shows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RESULT_DAYS = 7

ERRORS = {
    "transactions_not_enabled": "Pickups in the app turn on for the playoffs.",
    "not_a_team_owner": "Your account isn't linked to a team in this league.",
    "bid_invalid": "Bids are whole dollars, $0 or more.",
    "bid_over_budget": "That's more FAAB than you have left.",
    "player_taken": "He's already on a roster.",
    "player_on_waivers": "Someone has a bid in on him. Bid too, and it runs Wednesday at 3 AM.",
    "player_locked": "His game has already started this week.",
    "drop_not_yours": "That player isn't on your roster.",
    "roster_full": "Your roster is full. Pick someone to drop.",
    "free_agency_closed": "Instant adds are open Sundays after the 3 AM run. Place a bid instead.",
    "claim_not_open": "That bid was already processed.",
    "not_your_claim": "That bid isn't yours.",
    "dead_cap_rule_missing": "The league's dead cap rule isn't set. Ask the commissioner.",
}


def friendly(exc: Exception) -> str:
    text = str(exc)
    for code, message in ERRORS.items():
        if code in text:
            return message
    return "That move wasn't saved. Try again."


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def next_run(now: datetime) -> datetime:
    """The next Wednesday or Sunday 3:00 AM Eastern after `now` (same rule as next_waiver_run_private)."""
    local = now.astimezone(ET)
    for days in range(0, 8):
        day = (local + timedelta(days=days)).date()
        at = datetime(day.year, day.month, day.day, 3, 0, tzinfo=ET)
        if at > local and at.isoweekday() in (3, 7):
            return at.astimezone(timezone.utc)
    return (local + timedelta(days=7)).astimezone(timezone.utc)


def free_agency_open(now: datetime) -> bool:
    local = now.astimezone(ET)
    return local.isoweekday() == 7 and local.hour >= 3


def run_label(at: datetime) -> str:
    local = at.astimezone(ET)
    return f"{local.strftime('%A')} 3 AM"


def dead_cap(seasons: Iterable[Mapping[str, Any]], season: int, pct: float) -> dict[str, Any]:
    """What dropping a contract costs: {this_season, total, through}. $1 seasons are free."""
    owed = []
    for s in seasons:
        if int(s.get("season") or 0) < season or str(s.get("obligation_status")) not in {"active", "scheduled"}:
            continue
        basis = float(s.get("cap_hit") if s.get("cap_hit") is not None else s.get("salary") or 0)
        owed.append((int(s["season"]), 0.0 if basis <= 1 else round(basis * pct / 100, 2)))
    charged = [(yr, amt) for yr, amt in owed if amt > 0]
    return {
        "this_season": next((amt for yr, amt in owed if yr == season), 0.0),
        "total": round(sum(amt for _, amt in owed), 2),
        "through": max((yr for yr, _ in charged), default=None),
    }


def dead_cap_text(cost: Mapping[str, Any], season: int) -> str:
    """'No dead cap', '$3 dead cap this season', or '$3 dead cap through 2028' (per season)."""
    if not cost.get("total"):
        return "No dead cap"
    amount = f"${cost['this_season']:g}" if cost.get("this_season") else f"${cost['total']:g}"
    if cost.get("through") and cost["through"] > season:
        return f"{amount} dead cap through {cost['through']}"
    return f"{amount} dead cap this season"


def bids_view(rows: Iterable[Mapping[str, Any]], me: str, owner_of: Mapping[str, str], info: Mapping[str, Mapping[str, Any]],
              now: datetime | None = None) -> dict[str, Any]:
    """My open bids, plus results from the last week: wins, and losses with who outbid me and every bid on him."""
    now = now or datetime.now(timezone.utc)
    rows = list(rows)

    def player(pid: Any) -> dict[str, Any]:
        meta = info.get(str(pid)) or {}
        return {"player_id": str(pid), "name": meta.get("full_name") or "Player", "pos": meta.get("position") or "",
                "nfl_team": meta.get("team") or "FA"}

    open_bids, results = [], []
    for r in sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True):
        if str(r.get("league_team_id")) != me:
            continue
        if r.get("status") == "pending":
            at = _when(r.get("process_at"))
            open_bids.append({"id": str(r["id"]), "player": player(r["player_id"]), "bid": float(r.get("bid") or 0),
                              "drop": player(r["drop_player_id"]) if r.get("drop_player_id") else None,
                              "runs": run_label(at) if at else None, "process_at": r.get("process_at")})
            continue
        decided = _when(r.get("decided_at"))
        if not decided or now - decided > timedelta(days=RESULT_DAYS) or r.get("status") not in {"won", "lost", "failed"}:
            continue
        others = sorted(
            ({"owner": owner_of.get(str(o.get("league_team_id")), "Another team"), "bid": float(o.get("bid") or 0),
              "won": o.get("status") == "won", "mine": str(o.get("league_team_id")) == me}
             for o in rows if o.get("player_id") == r.get("player_id") and o.get("process_at") == r.get("process_at")
             and o.get("status") in {"won", "lost"}),
            key=lambda o: -o["bid"])
        p, bid = player(r["player_id"]), float(r.get("bid") or 0)
        if r.get("status") == "won":
            line = f"You won {p['name']} for ${bid:g}"
        elif r.get("status") == "lost" and r.get("winning_bid") is not None:
            winner = owner_of.get(str(r.get("winning_team_id")), "Another team")
            line = f"{winner} outbid you for {p['name']}: ${float(r['winning_bid']):g} to your ${bid:g}"
        elif r.get("status") == "lost":
            line = f"{p['name']} was signed before the bids ran"
        else:
            line = f"Your ${bid:g} bid for {p['name']} didn't go through: {r.get('note') or 'not processed'}"
        results.append({"kind": "bid", "id": str(r["id"]), "status": r.get("status"), "line": line, "player": p, "bid": bid,
                        "bids": others, "decided_at": r.get("decided_at")})
    return {"bids": open_bids, "results": results}
