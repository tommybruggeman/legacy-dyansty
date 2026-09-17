"""Print every Sleeper transaction with no summarising, for reconciliation.

Usage:  python scripts/inspect_sleeper_transactions.py [since_epoch_ms]

Shows created and status_updated side by side, because Sleeper's app displays
the processing time while the sync filters on creation time -- a difference
that decides whether a transaction is picked up.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import requests

LEAGUE = "".join(c for c in os.getenv("SLEEPER_LEAGUE_ID", "1361083066197491712") if c.isdigit())
MT = timezone(timedelta(hours=-6))
SINCE = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def when(ms):
    if not ms:
        return "-"
    return datetime.fromtimestamp(int(ms) / 1000, MT).strftime("%b %d %H:%M")


rows = []
for week in range(1, 4):
    data = requests.get(
        f"https://api.sleeper.app/v1/league/{LEAGUE}/transactions/{week}", timeout=30,
    ).json() or []
    for tx in data:
        rows.append((week, tx))

rows.sort(key=lambda r: int(r[1].get("created") or 0))

print(f"{'wk':<3} {'created (MT)':<14} {'processed (MT)':<14} {'type':<11} "
      f"{'status':<9} {'bid':<4} {'sync?':<7} detail")
shown = 0
for week, tx in rows:
    created = int(tx.get("created") or 0)
    if created < SINCE:
        continue
    shown += 1
    settings = tx.get("settings") or {}
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    detail = []
    if adds:
        detail.append("add " + ", ".join(f"{p}->r{r}" for p, r in adds.items()))
    if drops:
        detail.append("drop " + ", ".join(f"{p}<-r{r}" for p, r in drops.items()))
    print(f"{week:<3} {when(created):<14} {when(tx.get('status_updated')):<14} "
          f"{str(tx.get('type')):<11} {str(tx.get('status')):<9} "
          f"{str(settings.get('waiver_bid')):<4} "
          f"{('yes' if str(tx.get('status'))=='complete' else 'skip'):<7} "
          f"{tx.get('transaction_id')} {' | '.join(detail)}")

print(f"\n{shown} transaction(s) at or after {when(SINCE)}; {len(rows)} total across weeks 1-3")
