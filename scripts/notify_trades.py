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
import time
from pathlib import Path
from typing import Any, Mapping

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from pipeline.league_history.build import OwnerMap  # noqa: E402

SLEEPER = "https://api.sleeper.app/v1"
EXPO = "https://exp.host/--/api/v2/push/send"
ACTIVITY_URL = "/league?view=Activity&at={at}"


def _get(url: str) -> Any:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def short(name: str) -> str:
    parts = str(name or "").split()
    return f"{parts[0]} {parts[1][0]}." if len(parts) > 1 else str(name or "")


def trade_message(tx: Mapping[str, Any], owners: OwnerMap, names: Mapping[str, str]) -> tuple[str, str]:
    """('Trade completed', 'Grady M. gets Josh Allen · Tommy B. gets Justin Jefferson, 2027 R1')."""
    gets: dict[str, list[str]] = {}
    for rid in tx.get("roster_ids") or []:
        gets.setdefault(owners.name(rid), [])
    for pid, rid in (tx.get("adds") or {}).items():
        gets.setdefault(owners.name(rid), []).append(names.get(str(pid)) or "a player")
    for pk in tx.get("draft_picks") or []:
        gets.setdefault(owners.name(pk.get("owner_id")), []).append(f"{pk.get('season')} R{pk.get('round')}")
    for b in tx.get("waiver_budget") or []:
        gets.setdefault(owners.name(b.get("receiver")), []).append(f"${b.get('amount')} FAAB")
    body = " · ".join(f"{short(o)} gets {', '.join(items) or 'nothing'}" for o, items in gets.items())
    return "Trade completed", body


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


def run_league(client: Any, league: Mapping[str, Any], week: int) -> dict[str, int]:
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
    teams = client.table("league_teams").select("owner_name, sleeper_roster_id, sleeper_user_id").eq("league_id", lid).execute().data or []
    owners = OwnerMap(_get(f"{SLEEPER}/league/{sid}/users") or [], _get(f"{SLEEPER}/league/{sid}/rosters") or [], teams)
    ids = sorted({str(p) for t in new for p in (t.get("adds") or {})})
    names = {str(r["sleeper_id"]): r.get("full_name") for r in (client.table("nfl_players").select("sleeper_id, full_name").in_("sleeper_id", ids).execute().data or [])} if ids else {}
    tokens = [r["token"] for r in client.table("push_tokens").select("token").eq("league_id", lid).execute().data or []]
    sent = 0
    for t in sorted(new, key=lambda t: t.get("status_updated") or 0):
        title, body = trade_message(t, owners, names)
        messages = [{"to": tok, "title": title, "body": body, "sound": "default",
                     "data": {"url": ACTIVITY_URL.format(at=int(time.time() * 1000))}} for tok in tokens]
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
            if str((_get(f"{SLEEPER}/league/{league['sleeper_league_id']}") or {}).get("season")) != season:
                continue  # last season's league: nothing new happens there
            res = run_league(client, league, week)
            print(f"[push] league {n}: {res['new']} new trades, {res['sent']} alerts sent", flush=True)
        except Exception as exc:
            print(f"[push] league {n}: failed ({type(exc).__name__})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
