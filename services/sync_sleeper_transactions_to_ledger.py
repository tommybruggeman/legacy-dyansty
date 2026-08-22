from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import date

import requests
from supabase import create_client

from season_engine import SeasonResolver


GO_LIVE_DATE = date(2026, 7, 4)


def load_env():
    for p in [
        Path("../.env"),
        Path("../fantasy_env"),
        Path("../pages/.env"),
        Path("../pages/fantasy_env"),
        Path(".env"),
        Path("fantasy_env"),
    ]:
        if not p.exists():
            continue

        for line in p.read_text().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue

            k, v = line.split("=", 1)
            os.environ.setdefault(
                k.strip(),
                v.split("#", 1)[0].strip().strip('"').strip("'"),
            )


def env_required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise SystemExit(f"Missing required env var: {name}")
    return val


def supabase_headers(prefer: str = "return=representation") -> dict:
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )

    if not key:
        raise SystemExit("Missing SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY")

    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": prefer,
    }


def get_active_league_season(supabase_url: str, headers: dict, sleeper_league_id: str) -> dict:
    client = create_client(supabase_url, headers["apikey"])
    leagues = client.table("leagues").select("id").eq("sleeper_league_id", sleeper_league_id).execute().data or []
    if len(leagues) != 1:
        raise SystemExit(f"Expected one Legacy league for Sleeper league {sleeper_league_id}; found {len(leagues)}.")
    season = SeasonResolver(client).get_active_season(str(leagues[0]["id"]))
    if season.sleeper_league_id != sleeper_league_id:
        raise SystemExit("Connected Sleeper league is not the authoritative active-season Sleeper league.")
    return {"id": season.id, "league_id": season.league_id, "season": season.season, "sleeper_league_id": season.sleeper_league_id, "is_active": season.is_active}


def get_table_columns(supabase_url: str, headers: dict, table_name: str) -> set[str]:
    url = f"{supabase_url}/rest/v1/{table_name}"
    params = {"select": "*", "limit": "1"}

    r = requests.get(url, headers=headers, params=params, timeout=25)
    r.raise_for_status()

    rows = r.json() or []
    if not rows:
        return set()

    return set(rows[0].keys())


def get_team_map(supabase_url: str, headers: dict, league_id: str) -> dict[int, dict]:
    url = f"{supabase_url}/rest/v1/league_teams"
    params = {
        "select": "id,owner_name,team_name,sleeper_roster_id,sleeper_team_name",
        "league_id": f"eq.{league_id}",
    }

    r = requests.get(url, headers=headers, params=params, timeout=25)
    r.raise_for_status()

    rows = r.json() or []

    team_map = {}
    unmapped = []

    for row in rows:
        roster_id = row.get("sleeper_roster_id")

        if roster_id is None:
            unmapped.append(row.get("owner_name") or row.get("team_name") or row.get("id"))
            continue

        team_map[int(roster_id)] = row

    if unmapped:
        raise SystemExit(f"Cannot sync. Unmapped teams found: {unmapped}")

    if not team_map:
        raise SystemExit("Cannot sync. No league_teams mappings found.")

    return team_map


def fetch_sleeper_transactions(sleeper_league_id: str, week: int) -> list[dict]:
    url = f"https://api.sleeper.app/v1/league/{sleeper_league_id}/transactions/{week}"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json() or []


def fetch_existing_keys(supabase_url: str, headers: dict, league_season_id: str) -> set[tuple]:
    url = f"{supabase_url}/rest/v1/transaction_ledger"
    params = {
        "select": "sleeper_transaction_id,roster_id,added_player_id,dropped_player_id",
        "league_season_id": f"eq.{league_season_id}",
    }

    r = requests.get(url, headers=headers, params=params, timeout=25)
    r.raise_for_status()

    rows = r.json() or []

    return {
        (
            str(row.get("sleeper_transaction_id")),
            str(row.get("roster_id")),
            str(row.get("added_player_id")),
            str(row.get("dropped_player_id")),
        )
        for row in rows
    }


def normalize_transaction(
    tx: dict,
    league_id: str,
    league_season_id: str,
    season: int,
    sleeper_league_id: str,
    team_map: dict[int, dict],
) -> list[dict]:

    if tx.get("status") != "complete":
        return []

    tx_id = str(tx.get("transaction_id") or "")
    tx_type = str(tx.get("type") or "")
    week = tx.get("leg")

    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    roster_ids = tx.get("roster_ids") or []
    settings = tx.get("settings") or {}

    waiver_bid = settings.get("waiver_bid") or 0

    all_rosters = set(roster_ids)
    all_rosters.update(adds.values())
    all_rosters.update(drops.values())

    rows = []

    for roster_id_raw in sorted(all_rosters):
        roster_id = int(roster_id_raw)

        if roster_id not in team_map:
            rows.append({
                "league_id": league_id,
                "league_season_id": league_season_id,
                "season": season,
                "sleeper_league_id": sleeper_league_id,
                "sleeper_transaction_id": tx_id,
                "nfl_week": week,
                "transaction_type": tx_type,
                "transaction_status": tx.get("status"),
                "roster_id": roster_id,
                "added_player_id": None,
                "dropped_player_id": None,
                "waiver_bid": waiver_bid,
                "processed": False,
                "needs_review": True,
                "review_reason": f"No league_teams mapping found for roster_id {roster_id}",
                "raw_transaction": tx,
            })
            continue

        mapped_team = team_map[roster_id]

        added_players = [pid for pid, rid in adds.items() if int(rid) == roster_id]
        dropped_players = [pid for pid, rid in drops.items() if int(rid) == roster_id]

        max_len = max(len(added_players), len(dropped_players), 1)

        for i in range(max_len):
            added = added_players[i] if i < len(added_players) else None
            dropped = dropped_players[i] if i < len(dropped_players) else None

            rows.append({
                "league_id": league_id,
                "league_season_id": league_season_id,
                "season": season,
                "sleeper_league_id": sleeper_league_id,
                "sleeper_transaction_id": tx_id,
                "nfl_week": week,
                "transaction_type": tx_type,
                "transaction_status": tx.get("status"),
                "roster_id": roster_id,

                # New useful mapping fields
                "league_team_id": mapped_team.get("id"),
                "owner_name": mapped_team.get("owner_name"),
                "sleeper_team_name": mapped_team.get("sleeper_team_name"),

                "added_player_id": str(added) if added is not None else None,
                "dropped_player_id": str(dropped) if dropped is not None else None,
                "waiver_bid": waiver_bid,
                "processed": False,
                "needs_review": False,
                "review_reason": None,
                "raw_transaction": tx,
            })

    return rows


def filter_to_existing_columns(row: dict, allowed_columns: set[str]) -> dict:
    if not allowed_columns:
        return row

    return {
        k: v
        for k, v in row.items()
        if k in allowed_columns
    }


def insert_ledger_rows(
    supabase_url: str,
    headers: dict,
    rows: list[dict],
    existing_keys: set[tuple],
    ledger_columns: set[str],
) -> tuple[int, int]:

    inserted = 0
    skipped = 0

    url = f"{supabase_url}/rest/v1/transaction_ledger"

    for row in rows:
        key = (
            str(row.get("sleeper_transaction_id")),
            str(row.get("roster_id")),
            str(row.get("added_player_id")),
            str(row.get("dropped_player_id")),
        )

        if key in existing_keys:
            skipped += 1
            continue

        clean_row = filter_to_existing_columns(row, ledger_columns)

        r = requests.post(
            url,
            headers=headers,
            data=json.dumps(clean_row),
            timeout=25,
        )

        if r.status_code in (200, 201, 204):
            inserted += 1
            existing_keys.add(key)
        else:
            print("\nInsert failed:")
            print("status:", r.status_code)
            print("body:", r.text)
            r.raise_for_status()

    return inserted, skipped


def main():
    load_env()

    supabase_url = env_required("SUPABASE_URL").rstrip("/")
    sleeper_league_id = "".join(ch for ch in env_required("SLEEPER_LEAGUE_ID") if ch.isdigit())

    headers = supabase_headers("return=representation")

    league_season = get_active_league_season(
        supabase_url,
        headers,
        sleeper_league_id,
    )

    league_id = league_season["league_id"]
    league_season_id = league_season["id"]
    season = league_season["season"]

    print("\nUsing league:")
    print("league_id:", league_id)
    print("league_season_id:", league_season_id)
    print("season:", season)

    team_map = get_team_map(supabase_url, headers, league_id)
    print(f"Loaded {len(team_map)} mapped teams from league_teams.")

    existing_keys = fetch_existing_keys(supabase_url, headers, league_season_id)
    print(f"Loaded {len(existing_keys)} existing ledger keys.")

    ledger_columns = get_table_columns(supabase_url, headers, "transaction_ledger")
    print(f"Detected {len(ledger_columns)} transaction_ledger columns.")

    total_rows = 0
    total_inserted = 0
    total_skipped = 0

    for week in range(1, 19):
        transactions = fetch_sleeper_transactions(sleeper_league_id, week)

        week_rows = []

        for tx in transactions:
            week_rows.extend(
                normalize_transaction(
                    tx=tx,
                    league_id=league_id,
                    league_season_id=league_season_id,
                    season=season,
                    sleeper_league_id=sleeper_league_id,
                    team_map=team_map,
                )
            )

        if not week_rows:
            continue

        inserted, skipped = insert_ledger_rows(
            supabase_url=supabase_url,
            headers=headers,
            rows=week_rows,
            existing_keys=existing_keys,
            ledger_columns=ledger_columns,
        )

        total_rows += len(week_rows)
        total_inserted += inserted
        total_skipped += skipped

        print(
            f"Week {week}: "
            f"normalized={len(week_rows)} "
            f"inserted={inserted} "
            f"skipped_duplicates={skipped}"
        )

    print("\nDone.")
    print("Total normalized:", total_rows)
    print("Total inserted:", total_inserted)
    print("Total skipped duplicates:", total_skipped)


if __name__ == "__main__":
    main()
