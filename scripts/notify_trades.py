"""Push an alert to every owner's phone when a trade completes. Runs after each Sleeper sync (every 15 minutes).

For each league, reads this week's and last week's Sleeper transactions, and for every completed trade not yet
announced (push_sent), sends one Expo push to every phone registered in that league (push_tokens), then records it.
The first run for a league only records the trades already there (and a 'seeded' marker), so nobody gets a burst of old trades.
Free: Expo's push service has no charge. Prints counts only (the repo and its Actions logs are public).

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import os
import sys
from typing import Any, Mapping

import requests


SLEEPER = "https://api.sleeper.app/v1"
EXPO = "https://exp.host/--/api/v2/push/send"
ACTIVITY_URL = "/activity?focus={tid}"


def _get(url: str) -> Any:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def trade_message(league_name: str | None) -> str:
    """'A trade was completed in Legacy Dynasty'; tapping it opens Activity on that trade."""
    return f"A trade was completed in {league_name or 'your league'}"


def send(messages: list[dict[str, Any]]) -> list[str]:
    """Send in batches of 100; returns the tokens Expo says are no longer registered."""
    dead = []
    for i in range(0, len(messages), 100):
        batch = messages[i:i + 100]
        try:
            resp = requests.post(EXPO, json=batch, timeout=30, headers={"Accept": "application/json"})
            tickets = (resp.json() or {}).get("data") or []
        except Exception as exc:
            print(f"[push] send failed: {type(exc).__name__}", flush=True)
            continue
        for msg, ticket in zip(batch, tickets):
            if (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                dead.append(msg["to"])
    return dead


def run_league(client: Any, league: Mapping[str, Any], week: int, league_name: str | None = None) -> dict[str, int]:
    lid, sid = str(league["id"]), str(league["sleeper_league_id"])
    sent_keys = {r["key"] for r in client.table("push_sent").select("key").eq("league_id", lid).execute().data or []}
    first_run = "seeded" not in sent_keys
    txs = []
    for w in sorted({max(week - 1, 1), max(week, 1)}):
        txs += _get(f"{SLEEPER}/league/{sid}/transactions/{w}") or []
    trades = [t for t in txs if t.get("type") == "trade" and t.get("status") == "complete"]
    new = [t for t in trades if f"trade:{t.get('transaction_id')}" not in sent_keys]
    if first_run:  # start quietly: remember what's there, announce only what comes next
        keys = ["seeded"] + [f"trade:{t['transaction_id']}" for t in new]
        client.table("push_sent").upsert([{"league_id": lid, "key": k} for k in keys], on_conflict="league_id,key").execute()
        return {"new": len(new), "sent": 0}
    if not new:
        return {"new": 0, "sent": 0}
    tokens = [r["token"] for r in client.table("push_tokens").select("token").eq("league_id", lid).execute().data or []]
    sent = 0
    for t in sorted(new, key=lambda t: t.get("status_updated") or 0):
        messages = [{"to": tok, "body": trade_message(league_name), "sound": "default",
                     "data": {"url": ACTIVITY_URL.format(tid=t["transaction_id"])}} for tok in tokens]
        dead = send(messages) if messages else []
        if dead:
            client.table("push_tokens").delete().in_("token", dead).execute()
        client.table("push_sent").upsert({"league_id": lid, "key": f"trade:{t['transaction_id']}"}, on_conflict="league_id,key").execute()
        sent += len(messages) - len(dead)
    return {"new": len(new), "sent": sent}


def main() -> int:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    try:
        client.table("push_tokens").select("token").limit(1).execute()
    except Exception:
        print("[push] push tables not set up yet; skipping", flush=True)
        return 0
    state = _get(f"{SLEEPER}/state/nfl") or {}
    week = int(state.get("week") or 1)
    season = str(state.get("season") or "")
    for n, league in enumerate(client.table("leagues").select("id, sleeper_league_id").execute().data or [], start=1):
        if not league.get("sleeper_league_id"):
            continue
        try:
            info = _get(f"{SLEEPER}/league/{league['sleeper_league_id']}") or {}
            if str(info.get("season")) != season:
                continue  # last season's league: nothing new happens there
            res = run_league(client, league, week, info.get("name"))
            print(f"[push] league {n}: {res['new']} new trades, {res['sent']} alerts sent", flush=True)
        except Exception as exc:
            print(f"[push] league {n}: failed ({type(exc).__name__})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
