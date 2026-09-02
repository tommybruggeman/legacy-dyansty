# scripts/sync_transactions.py
import os, time, math, json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Iterable, Tuple

import requests
from dotenv import load_dotenv

# Allow running from project root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# Reuse your Supabase client setup
from supabase import create_client, Client
from services.sleeper_sync_guard import require_active_season_sync_authority

ENV_PATH = Path(__file__).parents[1] / "pages" / "fantasy_env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()  # fallback

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SLEEPER_LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID", "")
CANONICAL_LEAGUE_ID = os.getenv("LEGACY_LEAGUE_ID", "")
SYNC_SEASON = int(os.getenv("LEGACY_SYNC_SEASON", "0") or 0)

assert SUPABASE_URL and SUPABASE_KEY, "Missing Supabase env."
assert SLEEPER_LEAGUE_ID, "Missing SLEEPER_LEAGUE_ID in env."

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SLEEPER_BASE = "https://api.sleeper.app/v1"

def current_nfl_week() -> int:
    # Sleeper state endpoint
    r = requests.get(f"{SLEEPER_BASE}/state/nfl", timeout=20)
    r.raise_for_status()
    data = r.json()
    # 'week' is current NFL week (pre/post might vary). Coerce to int.
    return int(data.get("week", 1) or 1)

def fetch_transactions_for_week(league_id: str, week: int) -> List[dict]:
    url = f"{SLEEPER_BASE}/league/{league_id}/transactions/{week}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json() or []

def get_owner_team_map() -> Dict[str, str]:
    # owners table maps Sleeper username -> team_name
    # Build quick dict for both username and display_name keys in Sleeper
    res = sb.table("owners").select("sleeper_username, team_name").execute()
    mapping = {row["sleeper_username"]: row["team_name"] for row in res.data or []}
    return mapping

def get_roster_team_by_roster_id(roster_id_to_owner: Dict[int, str], owners_map: Dict[str, str]) -> Dict[int, str]:
    """
    Translate Sleeper 'roster_id' -> our teams.team_name via owners_map.
    We'll need the league rosters endpoint to map roster_id -> owner username.
    """
    return {rid: owners_map.get(owner_username) for rid, owner_username in roster_id_to_owner.items()}

def fetch_roster_id_to_owner_username(league_id: str) -> Dict[int, str]:
    # /league/<league_id>/rosters gives roster objects including owner_id
    r = requests.get(f"{SLEEPER_BASE}/league/{league_id}/rosters", timeout=30)
    r.raise_for_status()
    rosters = r.json() or []
    # We also need users to resolve owner_id -> username/display_name
    u = requests.get(f"{SLEEPER_BASE}/league/{league_id}/users", timeout=30)
    u.raise_for_status()
    users = {x["user_id"]: x for x in (u.json() or [])}

    mapping = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        user = users.get(owner_id, {})
        # prefer username; fallback to display_name
        uname = user.get("display_name") or user.get("username") or ""
        mapping[int(r["roster_id"])] = uname
    return mapping

def normalize_tx_rows(tx: dict,
                      week: int,
                      rid_to_team: Dict[int, str]) -> List[dict]:
    """
    Break a Sleeper transaction into 1+ normalized rows for our 'transactions' table.
    Handles adds/drops/trades. We create one row per player move.
    """
    rows = []
    tx_id   = str(tx.get("transaction_id"))
    tx_type = tx.get("type") or "unknown"
    ts      = tx.get("status_updated") or tx.get("created")
    executed = datetime.fromtimestamp(ts/1000, tz=timezone.utc) if isinstance(ts, (int, float)) else datetime.now(timezone.utc)
    waiver_bid = tx.get("waiver_bid")

    # Maps of player_id -> roster_id for adds/drops
    adds  = tx.get("adds")  or {}  # {player_id: roster_id}
    drops = tx.get("drops") or {}  # {player_id: roster_id}

    # Optional: player metadata block might be elsewhere; we’ll fetch names via helper
    # but Sleeper transactions rarely include full names, so we’ll defer to players table lookup
    def player_name_from_id(pid: str) -> str:
        # try players table
        res = sb.table("players").select("full_name").eq("sleeper_id", pid).limit(1).execute()
        if res.data:
            return res.data[0]["full_name"]
        return pid  # fallback

    # Adds (FA or waiver)
    for pid, rid in adds.items():
        to_team = rid_to_team.get(int(rid))
        rows.append({
            "sleeper_tx_id": tx_id,
            "tx_type": "waiver" if tx_type == "waiver" else ("free_agent" if tx_type == "free_agent" else tx_type),
            "nfl_week": week,
            "executed_at": executed.isoformat(),
            "player_name": player_name_from_id(pid),
            "sleeper_player": pid,
            "from_team": None,
            "to_team": to_team,
            "waiver_bid": waiver_bid,
            "raw": tx
        })

    # Drops
    for pid, rid in drops.items():
        from_team = rid_to_team.get(int(rid))
        rows.append({
            "sleeper_tx_id": tx_id,
            "tx_type": "drop",
            "nfl_week": week,
            "executed_at": executed.isoformat(),
            "player_name": player_name_from_id(pid),
            "sleeper_player": pid,
            "from_team": from_team,
            "to_team": None,
            "waiver_bid": None,
            "raw": tx
        })

    # Trades: Sleeper sets type 'trade' and includes adds/drops to represent the swap.
    # The above loops already capture both sides of the swap as add/drop rows with from/to teams.
    # To make it explicit, we keep tx_type=trade for any adds occurring in a trade.
    if tx_type == "trade":
        for r in rows:
            if r["tx_type"] in ("waiver", "free_agent"):
                r["tx_type"] = "trade"

    return rows

def upsert_transactions(rows: List[dict]) -> None:
    if not rows:
        return
    # Upsert by composite unique constraint
    # Supabase python client supports upsert with on_conflict
    sb.table("transactions").upsert(
        rows, on_conflict="sleeper_tx_id,player_name,tx_type,from_team,to_team"
    ).execute()

def apply_to_roster(rows: List[dict]) -> None:
    """
    Apply each transaction to the roster table so the Team View stays current.
    Assumes roster.player = players.full_name
    """
    for r in rows:
        player = r["player_name"]
        from_team = r["from_team"]
        to_team   = r["to_team"]
        tx_type   = r["tx_type"]

        if tx_type in ("waiver","free_agent","trade") and to_team:
            # ensure a single row: upsert -> set owner_team_name = to_team
            # delete any existing ownership first (safety)
            sb.table("roster").delete().eq("player", player).execute()
            sb.table("roster").insert({
                "player": player,
                "owner_team_name": to_team
            }).execute()

        elif tx_type == "drop" and from_team:
            # remove ownership
            sb.table("roster").delete().eq("player", player).execute()

def sync_week(week: int) -> int:
    owners_map = get_owner_team_map()
    rid_to_uname = fetch_roster_id_to_owner_username(SLEEPER_LEAGUE_ID)
    rid_to_team  = {rid: owners_map.get(uname) for rid, uname in rid_to_uname.items()}

    txs = fetch_transactions_for_week(SLEEPER_LEAGUE_ID, week)
    total_rows = 0
    for tx in txs:
        rows = normalize_tx_rows(tx, week, rid_to_team)
        if not rows:
            continue
        upsert_transactions(rows)
        apply_to_roster(rows)
        total_rows += len(rows)
    return total_rows

def sync_recent(backfill_weeks: int = 2) -> None:
    require_active_season_sync_authority(sb, league_id=CANONICAL_LEAGUE_ID,
                                         expected_season=SYNC_SEASON,
                                         sleeper_league_id=SLEEPER_LEAGUE_ID)
    wk = current_nfl_week()
    # backfill current and previous week (covers Wednesday/Sunday runs)
    start = max(1, wk - backfill_weeks + 1)
    total = 0
    for w in range(start, wk + 1):
        total += sync_week(w)
    print(f"Synced {total} transaction rows across weeks {start}-{wk}")

if __name__ == "__main__":
    # By default sync recent weeks (fast). For first run you can backfill more.
    backfill = int(os.getenv("TX_BACKFILL_WEEKS", "2"))
    sync_recent(backfill)
