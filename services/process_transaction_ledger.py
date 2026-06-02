from __future__ import annotations

import json
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
            os.environ.setdefault(
                k.strip(),
                v.split("#", 1)[0].strip().strip('"').strip("'"),
            )


def env_required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise SystemExit(f"Missing required env var: {name}")
    return val


def headers(prefer: str = "return=representation"):
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_KEY", "").strip()
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


def get_json(url: str, h: dict, params: dict):
    r = requests.get(url, headers=h, params=params, timeout=25)
    r.raise_for_status()
    return r.json() or []


def patch_json(url: str, h: dict, params: dict, payload: dict):
    r = requests.patch(url, headers=h, params=params, data=json.dumps(payload), timeout=25)
    if not r.ok:
        print("PATCH failed:", r.status_code, r.text)
        r.raise_for_status()
    return r.json() if r.text else None


def post_json(url: str, h: dict, payload: dict):
    r = requests.post(url, headers=h, data=json.dumps(payload), timeout=25)
    if not r.ok:
        print("POST failed:", r.status_code, r.text)
        r.raise_for_status()
    return r.json() if r.text else None


def get_team_for_ledger_row(supabase_url: str, h: dict, row: dict) -> dict | None:
    rows = get_json(
        f"{supabase_url}/rest/v1/league_teams",
        h,
        {
            "select": "id,owner_name,team_name,sleeper_roster_id,sleeper_team_name",
            "league_id": f"eq.{row['league_id']}",
            "sleeper_roster_id": f"eq.{row['roster_id']}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def get_contract_by_player(supabase_url: str, h: dict, league_id: str, sleeper_player_id: str):
    rows = get_json(
        f"{supabase_url}/rest/v1/contracts",
        h,
        {
            "select": "*",
            "league_id": f"eq.{league_id}",
            "sleeper_player_id": f"eq.{sleeper_player_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def mark_processed(supabase_url: str, h: dict, ledger_id: str):
    patch_json(
        f"{supabase_url}/rest/v1/transaction_ledger",
        h,
        {"id": f"eq.{ledger_id}"},
        {
            "processed": True,
            "processed_at": "now()",
            "processing_error": None,
        },
    )


def mark_error(supabase_url: str, h: dict, ledger_id: str, error: str):
    patch_json(
        f"{supabase_url}/rest/v1/transaction_ledger",
        h,
        {"id": f"eq.{ledger_id}"},
        {
            "processing_error": error[:1000],
        },
    )


def process_drop(supabase_url: str, h: dict, row: dict) -> str:
    dropped_player_id = row.get("dropped_player_id")

    if not dropped_player_id:
        return "no_drop"

    team = get_team_for_ledger_row(supabase_url, h, row)
    if not team:
        raise RuntimeError(f"No team mapping for roster_id={row.get('roster_id')}")

    contract = get_contract_by_player(
        supabase_url,
        h,
        row["league_id"],
        dropped_player_id,
    )

    if not contract:
        patch_json(
            f"{supabase_url}/rest/v1/transaction_ledger",
            h,
            {"id": f"eq.{row['id']}"},
            {
                "processed": True,
                "needs_review": True,
                "review_reason": f"Drop skipped: no contract found for sleeper_player_id={dropped_player_id}",
                "processing_error": None,
            },
        )
        return f"review_needed no contract found for dropped sleeper_player_id={dropped_player_id}"

    salary = float(contract.get("salary") or 0)
    years_left = int(contract.get("contract_years_left") or 1)

    if salary > 1:
        dead_cap_amount = round(salary * 0.5 * years_left, 2)

        post_json(
            f"{supabase_url}/rest/v1/dead_cap_ledger",
            h,
            {
                "league_id": row["league_id"],
                "league_season_id": row["league_season_id"],
                "team_id": team["id"],
                "sleeper_player_id": dropped_player_id,
                "player_name": contract.get("player_name"),
                "original_salary": salary,
                "dead_cap_amount": dead_cap_amount,
                "remaining_years": years_left,
                "source_transaction_id": row["id"],
                "active": True,
            },
        )

    patch_json(
        f"{supabase_url}/rest/v1/contracts",
        h,
        {"id": f"eq.{contract['id']}"},
        {
            "owner_id": None,
            "owner_name": None,
        },
    )

    return f"dropped {contract.get('player_name')} salary={salary} years_left={years_left}"


def main():
    load_env()

    supabase_url = env_required("SUPABASE_URL").rstrip("/")
    h = headers()

    rows = get_json(
        f"{supabase_url}/rest/v1/transaction_ledger",
        h,
        {
            "select": "*",
            "processed": "eq.false",
            "dropped_player_id": "not.is.null",
            "order": "nfl_week.asc,created_at.asc",
            "limit": "10",
        },
    )

    print(f"Drop ledger rows to process: {len(rows)}")

    for row in rows:
        try:
            result = process_drop(supabase_url, h, row)
            mark_processed(supabase_url, h, row["id"])
            print(f"✅ {row['id']} {result}")

        except Exception as e:
            mark_error(supabase_url, h, row["id"], str(e))
            print(f"❌ {row['id']} {e}")


if __name__ == "__main__":
    main()
