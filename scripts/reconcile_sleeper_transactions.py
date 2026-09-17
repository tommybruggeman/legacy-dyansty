"""Prove that every Sleeper transaction is accounted for in the database.

Usage:  python scripts/reconcile_sleeper_transactions.py [--since "Sep 11"]

Reads only. For each transaction Sleeper reports, this works out the canonical
writes it should have produced (using the same mapping the sync itself uses),
then checks contract_events for those exact idempotency keys. Nothing is
inferred from the sync's own run summary -- the summary counts replays as new
work, so it cannot be used to answer "did this land?".

Every transaction ends up in exactly one bucket:

    OK       every expected write is in the database
    MISSING  at least one expected write is absent
    NOOP     Sleeper recorded nothing to apply (failed claim, empty move)

Exit code is 1 when anything is MISSING, so this can gate a deploy.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import requests
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from services.sleeper_transaction_adapter import (  # noqa: E402
    AcquireIntent, ReleaseIntent, SkippedTransaction, TradeIntent, map_transaction,
)
from services.sleeper_pipeline import effective_ms  # noqa: E402

MT = timezone(timedelta(hours=-6))
SLEEPER_BASE = "https://api.sleeper.app/v1"
WEEKS = range(1, 5)


def when(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(int(ms) / 1000, MT).strftime("%b %d %H:%M")


def parse_since(value: str | None) -> int:
    """Accept 'Sep 11', '2026-09-11', or raw epoch ms."""
    if not value:
        return 0
    value = value.strip()
    if value.isdigit():
        return int(value)
    for fmt in ("%b %d", "%Y-%m-%d", "%b %d %Y"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.year == 1900:
            parsed = parsed.replace(year=datetime.now(MT).year)
        return int(parsed.replace(tzinfo=MT).timestamp() * 1000)
    raise SystemExit(f"Could not read --since value: {value!r}")


def build_read_client():
    from supabase import create_client

    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)) if not v]
    if missing:
        raise SystemExit("Missing from .env: " + ", ".join(missing))
    return create_client(url, key)


def player_ids(transaction) -> set[str]:
    ids = set()
    for field in ("adds", "drops"):
        value = transaction.get(field)
        if isinstance(value, dict):
            ids.update(str(k) for k in value)
    return ids


def expected_keys(transaction, names) -> tuple[list[tuple[str, str]], str | None]:
    """(label, idempotency_key) pairs this transaction should have written."""
    pairs: list[tuple[str, str]] = []
    noop_reason: str | None = None
    for intent in map_transaction(transaction):
        if isinstance(intent, SkippedTransaction):
            noop_reason = intent.reason
        elif isinstance(intent, ReleaseIntent):
            pairs.append((f"drop  {names.get(intent.player_id, intent.player_id):<22} "
                          f"from r{intent.roster_id}", intent.idempotency_key))
        elif isinstance(intent, AcquireIntent):
            pairs.append((f"add   {names.get(intent.player_id, intent.player_id):<22} "
                          f"to   r{intent.roster_id} (${intent.salary})",
                          intent.idempotency_key))
        elif isinstance(intent, TradeIntent):
            pairs.append((f"trade rosters {', '.join(str(r) for r in intent.roster_ids)}",
                          intent.idempotency_key))
    return pairs, noop_reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="only report transactions at or after this date")
    parser.add_argument("--all", action="store_true", help="list OK rows too, not just problems")
    parser.add_argument("--player", help="only transactions touching this player, whole season")
    args = parser.parse_args()
    since = parse_since(args.since)

    client = build_read_client()

    sync_rows = (client.table("league_sleeper_sync").select("*")
                 .eq("sync_enabled", True).execute().data or [])
    if not sync_rows:
        raise SystemExit("No league has the Sleeper sync enabled.")

    failures = 0
    for config in sync_rows:
        league_id = str(config["league_id"])
        league = (client.table("leagues").select("name,sleeper_league_id")
                  .eq("id", league_id).execute().data or [{}])[0]
        sleeper_id = "".join(c for c in str(league.get("sleeper_league_id") or "") if c.isdigit())
        print(f"\n=== {league.get('name') or league_id} ===")
        print(f"watermark: {when(config.get('watermark_created_ms'))} "
              f"({config.get('watermark_created_ms')})  last run: {config.get('last_run_detail')}")

        transactions = []
        for week in WEEKS:
            data = requests.get(
                f"{SLEEPER_BASE}/league/{sleeper_id}/transactions/{week}", timeout=30,
            ).json() or []
            transactions.extend(data)
        transactions.sort(key=effective_ms)

        applied = set()
        page = 0
        while True:
            rows = (client.table("contract_events").select("idempotency_key")
                    .eq("league_id", league_id).like("idempotency_key", "sleeper:%")
                    .range(page * 1000, page * 1000 + 999).execute().data or [])
            applied.update(str(r["idempotency_key"]) for r in rows if r.get("idempotency_key"))
            if len(rows) < 1000:
                break
            page += 1

        wanted = sorted({pid for tx in transactions for pid in player_ids(tx)})
        names: dict[str, str] = {}
        for start in range(0, len(wanted), 100):
            chunk = wanted[start:start + 100]
            for row in (client.table("sleeper_players")
                        .select("sleeper_player_id,full_name")
                        .in_("sleeper_player_id", chunk).execute().data or []):
                if row.get("full_name"):
                    names[str(row["sleeper_player_id"])] = str(row["full_name"])

        counts = {"OK": 0, "MISSING": 0, "NOOP": 0}
        lines: list[str] = []
        matched_keys: set[str] = set()

        for transaction in transactions:
            effective = effective_ms(transaction)
            if args.player:
                # A player trace ignores --since: the question is always
                # "everything Sleeper ever did with him", not a date window.
                touched = any(
                    args.player.lower() in names.get(pid, pid).lower()
                    for pid in player_ids(transaction)
                )
                if not touched:
                    continue
            elif effective < since:
                continue
            tx_id = str(transaction.get("transaction_id") or "")
            pairs, noop_reason = expected_keys(transaction, names)
            matched_keys.update(key for _, key in pairs)

            if not pairs:
                counts["NOOP"] += 1
                if args.all:
                    lines.append(f"NOOP     {when(effective):<14} {tx_id}  {noop_reason}")
                continue

            present = [key in applied for _, key in pairs]
            verdict = "OK" if all(present) else "MISSING"
            counts[verdict] += 1
            if verdict == "MISSING" or args.all:
                lines.append(f"{verdict:<8} {when(effective):<14} {tx_id}  "
                             f"{transaction.get('type')}")
                for (label, key), found in zip(pairs, present):
                    lines.append(f"           {'  in db' if found else 'NOT IN DB'}  {label}")

        for line in lines:
            print(line)

        orphans = sorted(applied - matched_keys)
        if orphans and not args.player:
            print(f"\n{len(orphans)} write(s) in the database with no matching Sleeper "
                  f"transaction in weeks {WEEKS.start}-{WEEKS.stop - 1}:")
            for key in orphans[:20]:
                print(f"           {key}")

        print(f"\n{counts['OK']} accounted for, {counts['MISSING']} missing, "
              f"{counts['NOOP']} nothing to apply"
              + (f", touching {args.player}" if args.player
                 else f", at or after {when(since)}" if since else ""))
        failures += counts["MISSING"]

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
