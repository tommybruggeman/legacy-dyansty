"""Prove that every Sleeper transaction is accounted for in the database.

Usage:
    python scripts/reconcile_sleeper_transactions.py [--since "Sep 11"] [--all]
    python scripts/reconcile_sleeper_transactions.py --player delp

Reads only. For each transaction Sleeper reports, this works out the canonical
writes it should have produced (using the same mapping the sync itself uses),
then checks the database two ways:

  1. did the sync write it -- is the exact idempotency key in contract_events
  2. if not, is the end state right anyway -- is the player under a live
     contract to the team that added him, and off the team that dropped him

The second check is what separates a real gap from a move the commissioner
entered by hand before the sync went live. Without it every pre-sync
transaction looks broken.

    OK       the sync wrote it
    BY HAND  no sync write, but the rosters already say what Sleeper says
    MISSING  no sync write and the end state disagrees -- this one is real
    NOOP     Sleeper recorded nothing to apply (failed claim, empty move)

Nothing is read from the sync's own run summary. Exit code is 1 when anything
is MISSING, so this can gate a deploy.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
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
LIVE_STATUSES = ("active", "scheduled")


@dataclass(frozen=True)
class Leg:
    """One canonical write a transaction should have produced."""
    label: str
    key: str
    player_id: str | None = None
    roster_id: int | None = None
    # True when the player should end up owned by roster_id, False when he
    # should end up not owned by it, None when there is nothing to check.
    owned_after: bool | None = None
    # Traded FAAB is logged by hand as a cap_adjustments pair rather than
    # applied by the sync, so it is checked against that table instead.
    cap_tx_id: str | None = None
    cap_to_roster: int | None = None
    cap_amount: int | None = None


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


def legs_for(transaction, names) -> tuple[list[Leg], str | None]:
    """Every write this transaction should have produced, plus a noop reason."""
    legs: list[Leg] = []
    noop_reason: str | None = None

    def name(pid):
        return names.get(str(pid), str(pid))

    for intent in map_transaction(transaction):
        if isinstance(intent, SkippedTransaction):
            noop_reason = intent.reason
        elif isinstance(intent, ReleaseIntent):
            legs.append(Leg(
                f"drop  {name(intent.player_id):<22} from r{intent.roster_id}",
                intent.idempotency_key, str(intent.player_id), intent.roster_id, False,
            ))
        elif isinstance(intent, AcquireIntent):
            legs.append(Leg(
                f"add   {name(intent.player_id):<22} to   r{intent.roster_id} "
                f"(${intent.salary})",
                intent.idempotency_key, str(intent.player_id), intent.roster_id, True,
            ))
        elif isinstance(intent, TradeIntent):
            for move in intent.player_moves:
                legs.append(Leg(
                    f"trade {name(move.player_id):<22} "
                    f"r{move.from_roster_id} -> r{move.to_roster_id}",
                    intent.idempotency_key, str(move.player_id),
                    move.to_roster_id, True if move.to_roster_id else None,
                ))
            for pick in intent.pick_moves:
                legs.append(Leg(
                    f"trade {str(pick.season) + ' rd' + str(pick.round_number):<22} "
                    f"r{pick.from_roster_id} -> r{pick.to_roster_id}",
                    intent.idempotency_key,
                ))
            for faab in intent.faab_moves:
                legs.append(Leg(
                    f"trade {'$' + str(faab.amount) + ' FAAB':<22} "
                    f"r{faab.from_roster_id} -> r{faab.to_roster_id}",
                    intent.idempotency_key,
                    cap_tx_id=intent.transaction_id,
                    cap_to_roster=faab.to_roster_id,
                    cap_amount=faab.amount,
                ))
            if not legs:
                noop_reason = "trade moved nothing"
    return legs, noop_reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="only report transactions at or after this date")
    parser.add_argument("--all", action="store_true", help="list clean rows too, not just problems")
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

        # What the sync wrote.
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

        # Who currently owns whom, by Sleeper roster id.
        roster_of_team = {
            str(row["id"]): row.get("sleeper_roster_id")
            for row in (client.table("league_teams").select("id,sleeper_roster_id")
                        .eq("league_id", league_id).execute().data or [])
        }
        owner_rosters: dict[str, set[int]] = {}
        page = 0
        while True:
            rows = (client.table("contract_agreements")
                    .select("player_id,league_team_id,status,superseded_by_contract_id")
                    .eq("league_id", league_id).in_("status", list(LIVE_STATUSES))
                    .is_("superseded_by_contract_id", "null")
                    .range(page * 1000, page * 1000 + 999).execute().data or [])
            for row in rows:
                roster = roster_of_team.get(str(row.get("league_team_id") or ""))
                if roster is not None:
                    owner_rosters.setdefault(str(row["player_id"]), set()).add(int(roster))
            if len(rows) < 1000:
                break
            page += 1

        # Traded FAAB logged by hand: a cap_adjustments pair whose note names
        # the Sleeper transaction it came from.
        owner_by_roster = {
            int(row["sleeper_roster_id"]): str(row.get("owner_name") or row.get("team_name") or "")
            for row in (client.table("league_teams")
                        .select("sleeper_roster_id,owner_name,team_name")
                        .eq("league_id", league_id).execute().data or [])
            if row.get("sleeper_roster_id") is not None
        }
        cap_moves = [
            (str(row.get("owner_name") or ""), str(row.get("amount") or "0"),
             str(row.get("note") or ""))
            for row in (client.table("cap_adjustments")
                        .select("owner_name,amount,note,adjustment_type")
                        .eq("league_id", league_id)
                        .eq("adjustment_type", "trade_carryover").execute().data or [])
        ]

        names: dict[str, str] = {}
        wanted = sorted({pid for tx in transactions for pid in player_ids(tx)})
        for start in range(0, len(wanted), 100):
            for row in (client.table("sleeper_players")
                        .select("sleeper_player_id,full_name")
                        .in_("sleeper_player_id", wanted[start:start + 100])
                        .execute().data or []):
                if row.get("full_name"):
                    names[str(row["sleeper_player_id"])] = str(row["full_name"])

        def end_state_ok(leg: Leg) -> bool | None:
            if leg.cap_tx_id:
                owner = owner_by_roster.get(int(leg.cap_to_roster or 0), "")
                return any(
                    leg.cap_tx_id in note and row_owner == owner
                    and float(amount) == float(leg.cap_amount or 0)
                    for row_owner, amount, note in cap_moves
                )
            if leg.owned_after is None or leg.player_id is None or leg.roster_id is None:
                return None
            owns = leg.roster_id in owner_rosters.get(leg.player_id, set())
            return owns is leg.owned_after

        counts = {"OK": 0, "BY HAND": 0, "MISSING": 0, "NOOP": 0}
        lines: list[str] = []
        matched_keys: set[str] = set()

        for transaction in transactions:
            effective = effective_ms(transaction)
            if args.player:
                # A player trace ignores --since: the question is always
                # "everything Sleeper ever did with him", not a date window.
                if not any(args.player.lower() in names.get(pid, pid).lower()
                           for pid in player_ids(transaction)):
                    continue
            elif effective < since:
                continue

            tx_id = str(transaction.get("transaction_id") or "")
            legs, noop_reason = legs_for(transaction, names)
            matched_keys.update(leg.key for leg in legs)

            if not legs:
                counts["NOOP"] += 1
                if args.all:
                    lines.append(f"NOOP     {when(effective):<14} {tx_id}  {noop_reason}")
                continue

            marks = []
            for leg in legs:
                if leg.key in applied:
                    marks.append(("  in db", True))
                else:
                    settled = end_state_ok(leg)
                    if settled is True:
                        marks.append(("by hand", True))
                    elif settled is False:
                        marks.append(("NOT DONE", False))
                    else:
                        marks.append(("NOT IN DB", False))

            if all(leg.key in applied for leg in legs):
                verdict = "OK"
            elif all(ok for _, ok in marks):
                verdict = "BY HAND"
            else:
                verdict = "MISSING"
            counts[verdict] += 1

            if verdict == "MISSING" or args.all:
                lines.append(f"{verdict:<8} {when(effective):<14} {tx_id}  "
                             f"{transaction.get('type')}")
                for leg, (mark, _) in zip(legs, marks):
                    lines.append(f"           {mark:<9}  {leg.label}")

        for line in lines:
            print(line)

        orphans = sorted(applied - matched_keys)
        if orphans and not args.player:
            print(f"\n{len(orphans)} write(s) in the database with no matching Sleeper "
                  f"transaction in weeks {WEEKS.start}-{WEEKS.stop - 1}:")
            for key in orphans[:20]:
                print(f"           {key}")

        scope = (f", touching {args.player}" if args.player
                 else f", at or after {when(since)}" if since else "")
        print(f"\n{counts['OK']} synced, {counts['BY HAND']} already right by hand, "
              f"{counts['MISSING']} missing, {counts['NOOP']} nothing to apply{scope}")
        failures += counts["MISSING"]

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
