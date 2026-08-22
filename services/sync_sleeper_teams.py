from __future__ import annotations

import json
import os
from pathlib import Path

import requests
from supabase import create_client

from season_engine import SeasonResolver


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


def supabase_headers():
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
        "Prefer": "return=representation",
    }


def get_league_season(supabase_url: str, headers: dict, sleeper_league_id: str) -> dict:
    client = create_client(supabase_url, headers["apikey"])
    leagues = client.table("leagues").select("id").eq("sleeper_league_id", sleeper_league_id).execute().data or []
    if len(leagues) != 1:
        raise SystemExit(f"Expected one Legacy league for Sleeper league {sleeper_league_id}; found {len(leagues)}.")
    season = SeasonResolver(client).get_active_season(str(leagues[0]["id"]))
    if season.sleeper_league_id != sleeper_league_id:
        raise SystemExit("Connected Sleeper league is not the authoritative active-season Sleeper league.")
    return {"id": season.id, "league_id": season.league_id, "season": season.season, "sleeper_league_id": season.sleeper_league_id, "is_active": season.is_active}


def fetch_sleeper_users(sleeper_league_id: str) -> list[dict]:
    url = f"https://api.sleeper.app/v1/league/{sleeper_league_id}/users"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json() or []


def fetch_sleeper_rosters(sleeper_league_id: str) -> list[dict]:
    url = f"https://api.sleeper.app/v1/league/{sleeper_league_id}/rosters"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json() or []


def upsert_team(
    supabase_url: str,
    headers: dict,
    league_id: str,
    team_name: str,
    sleeper_roster_id: int,
    sleeper_owner_id: str | None,
) -> dict:
    url = f"{supabase_url}/rest/v1/teams"
    params = {
        "on_conflict": "league_id,sleeper_roster_id",
    }

    payload = {
        "league_id": league_id,
        "team_name": team_name,
        "sleeper_roster_id": sleeper_roster_id,
        "sleeper_owner_id": sleeper_owner_id,
    }

    r = requests.post(
        url,
        headers=headers | {"Prefer": "resolution=merge-duplicates,return=representation"},
        params=params,
        data=json.dumps(payload),
        timeout=25,
    )

    if not r.ok:
        print("Team upsert failed")
        print(r.status_code)
        print(r.text)
        r.raise_for_status()

    rows = r.json() or []
    return rows[0]


def upsert_team_roster_map(
    supabase_url: str,
    headers: dict,
    league_season_id: str,
    roster_id: int,
    team_id: str,
    owner_display_name: str,
) -> None:
    url = f"{supabase_url}/rest/v1/team_roster_map"
    params = {
        "on_conflict": "league_season_id,roster_id",
    }

    payload = {
        "league_season_id": league_season_id,
        "roster_id": roster_id,
        "team_id": team_id,
        "owner_display_name": owner_display_name,
    }

    r = requests.post(
        url,
        headers=headers | {"Prefer": "resolution=merge-duplicates,return=minimal"},
        params=params,
        data=json.dumps(payload),
        timeout=25,
    )

    if not r.ok:
        print("Roster map upsert failed")
        print(r.status_code)
        print(r.text)
        r.raise_for_status()


def main():
    load_env()

    supabase_url = env_required("SUPABASE_URL").rstrip("/")
    sleeper_league_id = "".join(
        ch for ch in env_required("SLEEPER_LEAGUE_ID") if ch.isdigit()
    )

    headers = supabase_headers()

    league_season = get_league_season(
        supabase_url,
        headers,
        sleeper_league_id,
    )

    league_id = league_season["league_id"]
    league_season_id = league_season["id"]

    users = fetch_sleeper_users(sleeper_league_id)
    rosters = fetch_sleeper_rosters(sleeper_league_id)

    user_by_id = {u.get("user_id"): u for u in users}

    print("Syncing Sleeper teams...")
    print("League ID:", league_id)
    print("League season ID:", league_season_id)
    print("Sleeper rosters:", len(rosters))

    count = 0

    for roster in rosters:
        roster_id = roster.get("roster_id")
        owner_id = roster.get("owner_id")

        user = user_by_id.get(owner_id, {}) if owner_id else {}

        team_name = (
            (user.get("metadata") or {}).get("team_name")
            or user.get("display_name")
            or user.get("username")
            or f"Roster {roster_id}"
        )

        team = upsert_team(
            supabase_url=supabase_url,
            headers=headers,
            league_id=league_id,
            team_name=team_name,
            sleeper_roster_id=roster_id,
            sleeper_owner_id=owner_id,
        )

        upsert_team_roster_map(
            supabase_url=supabase_url,
            headers=headers,
            league_season_id=league_season_id,
            roster_id=roster_id,
            team_id=team["id"],
            owner_display_name=team_name,
        )

        count += 1
        print(f"Mapped roster {roster_id} → {team_name}")

    print(f"\nDone. Synced {count} teams.")


if __name__ == "__main__":
    main()
