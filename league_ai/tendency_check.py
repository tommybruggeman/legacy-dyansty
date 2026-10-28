"""Does each owner's activity predict what they trade for? Free check, prints counts and rates only (the repo is public).

For every completed trade, the habits are rebuilt from activity before that trade only, then we ask how often the
owner received a position they had been spending FAAB on, against how often that position shows up in trades at all.
A lift above 1 means the habits say something the trade engine can use when it ranks ideas.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Any

from league_ai.tendencies import lineup_habits, market_habits


def _rows(client: Any, league_id: str) -> list[dict[str, Any]]:
    rows, start = [], 0
    while True:
        page = (client.table("league_history_events").select("kind, season, week, owner_name, player_id, position, faab_bid, details, event_id")
                .eq("league_id", league_id).order("event_id").range(start, start + 999).execute().data or [])
        rows += page
        if len(page) < 1000:
            return rows
        start += 1000


def check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    key = lambda r: (int(r.get("season") or 0), int(r.get("week") or 0))
    lineups = [r for r in rows if r.get("kind") == "lineup"]
    market = [r for r in rows if r.get("kind") in ("waiver", "add", "drop", "draft_pick")]
    trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "trade" and r.get("position") and r.get("position") != "PICK":
            trades[str(r.get("event_id") or "").split(":")[1] if ":" in str(r.get("event_id")) else str(r.get("event_id"))].append(r)
    base = Counter(r["position"] for legs in trades.values() for r in legs)
    total = sum(base.values()) or 1
    hits = expected = graded = 0.0
    for legs in trades.values():
        when = key(legs[0])
        before = market_habits([r for r in market if key(r) < when])
        for r in legs:
            fav = list((before.get(str(r.get("owner_name"))) or {}).get("faab_by_position") or {})
            if not fav:
                continue
            graded += 1
            hits += r["position"] == fav[0]
            expected += base[fav[0]] / total
    habits = lineup_habits(lineups)
    shares = [h["hot_hand_share"] for h in habits.values() if "hot_hand_share" in h]
    return {"lineup_events": len(lineups), "seasons_with_lineups": sorted({int(r["season"]) for r in lineups}),
            "owners_with_a_start_sit_read": len(shares), "hot_hand_shares": sorted(shares),
            "lineup_vs_best": sorted(round(h["lineup_vs_best"], 2) for h in habits.values() if "lineup_vs_best" in h),
            "trade_legs_graded": int(graded),
            "got_their_top_faab_position": round(hits / graded, 3) if graded else None,
            "base_rate": round(expected / graded, 3) if graded else None,
            "lift": round(hits / expected, 2) if expected else None}


def main() -> None:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for n, lg in enumerate(client.table("leagues").select("id").execute().data or [], start=1):
        print(f"league {n}: {check(_rows(client, lg['id']))}")


if __name__ == "__main__":
    main()
