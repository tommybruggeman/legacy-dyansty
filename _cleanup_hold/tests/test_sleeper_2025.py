# test_sleeper_points.py
import os
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

DOTENV_PATH = Path(__file__).with_name(".env")
if DOTENV_PATH.exists():
    load_dotenv(DOTENV_PATH)

LEAGUE_ID = (os.getenv("SLEEPER_LEAGUE_ID") or "").strip()

def _get(url: str):
    r = requests.get(url, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def get_state():
    return _get("https://api.sleeper.app/v1/state/nfl") or {}

def get_league_meta(league_id: str):
    return _get(f"https://api.sleeper.app/v1/league/{league_id}")

def get_users(league_id: str):
    return _get(f"https://api.sleeper.app/v1/league/{league_id}/users") or []

def get_rosters(league_id: str):
    return _get(f"https://api.sleeper.app/v1/league/{league_id}/rosters") or []

def get_matchups(league_id: str, week: int):
    return _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}") or []

def last_completed_week(state: dict) -> int:
    wk = state.get("week")
    # If week is 1 or None, just return 1
    return wk - 1 if isinstance(wk, int) and wk > 1 else (wk or 1)

def summarize_week(league_id: str, week: int):
    """Return list of rows: (matchup_id, owner_name, roster_id, points, starters_sum)."""
    users = get_users(league_id)
    rosters = get_rosters(league_id)
    user_name = {u["user_id"]: (u.get("display_name") or u.get("username") or "unknown") for u in users}
    rid_to_owner = {
        r["roster_id"]: user_name.get(r.get("owner_id")) or f"Roster {r['roster_id']}"
        for r in rosters
    }

    m = get_matchups(league_id, week)
    rows = []
    for row in m:
        rid = row.get("roster_id")
        owner = rid_to_owner.get(rid, f"Roster {rid}")
        pts = float(row.get("points", 0.0))
        starters_points = row.get("starters_points") or []
        rows.append((row.get("matchup_id"), owner, rid, pts, round(sum(starters_points), 2)))
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows

def main():
    parser = argparse.ArgumentParser(description="Fetch Sleeper weekly team points.")
    parser.add_argument("--week", type=int, help="Force a specific week (e.g., 7)")
    args = parser.parse_args()

    if not LEAGUE_ID:
        print("❌ Missing SLEEPER_LEAGUE_ID in .env")
        return

    meta = get_league_meta(LEAGUE_ID)
    if not meta:
        print("❌ League ID not found. Double-check the ID.")
        return

    state = get_state()
    print(f"✅ League: '{meta.get('name')}'  season={meta.get('season')}")
    print(f"🏈 NFL state: season={state.get('season')} week={state.get('week')} status={state.get('season_type')}")

    # Decide the week
    target_week = args.week if args.week else last_completed_week(state)
    print(f"\n📆 Fetching Week {target_week}")

    rows = summarize_week(LEAGUE_ID, target_week)

    if not rows:
        print("⚠️ No matchups returned for that week.")
        return

    # If all points are zero, warn (likely a future/unplayed week or scoring not posted yet)
    all_zero = all(r[3] == 0 for r in rows)
    print(f"\n{'matchup':<8} {'owner':<18} {'rid':<4} {'points':>8} {'starters_sum':>14}")
    for mid, owner, rid, pts, ssum in rows:
        print(f"{str(mid):<8} {owner:<18} {str(rid):<4} {pts:>8.2f} {ssum:>14.2f}")

    if all_zero:
        print("\n⚠️ All team totals are 0. This usually means you asked for a week that hasn't been played")
        print("   (or scoring isn’t posted yet). Try a completed week (e.g., --week 7).")
    else:
        print("\n✅ Non-zero scores found. These should match Sleeper’s official totals for that week.")

if __name__ == "__main__":
    main()
