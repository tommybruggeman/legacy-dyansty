from __future__ import annotations

import os
from pathlib import Path

import requests


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
            os.environ.setdefault(k.strip(), v.split("#", 1)[0].strip().strip('"').strip("'"))


def env_required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise SystemExit(f"Missing required env var: {name}")
    return val


def headers():
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_KEY", "").strip()
    if not key:
        raise SystemExit("Missing SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY")

    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def get_json(url: str, h: dict, params: dict):
    r = requests.get(url, headers=h, params=params, timeout=25)
    r.raise_for_status()
    return r.json() or []


def main():
    load_env()

    supabase_url = env_required("SUPABASE_URL").rstrip("/")
    h = headers()

    ledger_rows = get_json(
        f"{supabase_url}/rest/v1/transaction_ledger",
        h,
        {
            "select": "*",
            "processed": "eq.false",
            "order": "nfl_week.asc,created_at.asc",
            "limit": "25",
        },
    )

    print(f"Unprocessed ledger rows found: {len(ledger_rows)}")

    for row in ledger_rows:
        roster_id = row["roster_id"]
        league_season_id = row["league_season_id"]
        added_player_id = row.get("added_player_id")
        dropped_player_id = row.get("dropped_player_id")

        team_map = get_json(
            f"{supabase_url}/rest/v1/league_teams",
            h,
            {
                "select": "id,owner_name,team_name,sleeper_roster_id,sleeper_team_name",
                "league_id": f"eq.{row['league_id']}",
                "sleeper_roster_id": f"eq.{roster_id}",
                "limit": "1",
            },
        )

        team = team_map[0] if team_map else None

        added_contract = []
        dropped_contract = []

        if added_player_id:
            added_contract = get_json(
                f"{supabase_url}/rest/v1/contracts",
                h,
                {
                    "select": "*",
                    "league_id": f"eq.{row['league_id']}",
                    "sleeper_player_id": f"eq.{added_player_id}",
                    "limit": "1",
                },
            )

        if dropped_player_id:
            dropped_contract = get_json(
                f"{supabase_url}/rest/v1/contracts",
                h,
                {
                    "select": "*",
                    "league_id": f"eq.{row['league_id']}",
                    "sleeper_player_id": f"eq.{dropped_player_id}",
                    "limit": "1",
                },
            )

        print("\n==============================")
        print(f"Ledger ID: {row['id']}")
        print(f"Week: {row['nfl_week']}")
        print(f"Type: {row['transaction_type']}")
        print(f"Roster ID: {roster_id}")
        team_label = (
            team.get("owner_name")
            or team.get("team_name")
            or team.get("sleeper_team_name")
            if team
            else "NOT FOUND"
        )
        print(f"Team: {team_label}")
        print(f"Waiver bid: {row.get('waiver_bid') or 0}")

        if added_player_id:
            print(f"ADD Sleeper ID: {added_player_id}")
            print(f"ADD Contract Found: {bool(added_contract)}")
            if added_contract:
                c = added_contract[0]
                print(f"ADD Player: {c.get('player_name')} | Salary: {c.get('salary')}")

        if dropped_player_id:
            print(f"DROP Sleeper ID: {dropped_player_id}")
            print(f"DROP Contract Found: {bool(dropped_contract)}")
            if dropped_contract:
                c = dropped_contract[0]
                print(f"DROP Player: {c.get('player_name')} | Salary: {c.get('salary')}")

        if not team:
            print("❌ Cannot process: missing team mapping")
        elif added_player_id and not added_contract:
            print("⚠️ Added player has no contract yet")
        elif dropped_player_id and not dropped_contract:
            print("⚠️ Dropped player has no contract match")
        else:
            print("✅ Dry-run route looks processable")


if __name__ == "__main__":
    main()
