# Legacy App/lib/loaders.py
from __future__ import annotations
from collections import defaultdict
from typing import List
import requests
import pandas as pd

# IMPORTANT: absolute import (no leading dot)
from owners import display_name_for


def _get(url: str):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def _fetch_league_meta(league_id: str):
    """Return dict: roster_id -> {username, display_name}"""
    users = _get(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = _get(f"https://api.sleeper.app/v1/league/{league_id}/rosters")
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        uid = r.get("owner_id")
        if uid and uid in user_by_id:
            u = user_by_id[uid]
            username = u.get("username") or "Unknown"
            display = (
                u.get("display_name")
                or (u.get("metadata") or {}).get("team_name")
                or username
            )
        else:
            username = display = "Unknown"
        out[r["roster_id"]] = {"username": username, "display_name": display}
    return out

def _week_df_from_sleeper(league_id: str, week: int) -> pd.DataFrame:
    """
    Columns: week | matchup_id | roster_id | points | opponent_roster_id | owner_name
    One row per team-game for the given week.
    """
    data = _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}")
    if not isinstance(data, list) or not data:
        return pd.DataFrame(
            columns=["week","matchup_id","roster_id","points","opponent_roster_id","owner_name"]
        )

    roster_map = _fetch_league_meta(league_id)
    groups = defaultdict(list)
    for m in data:
        groups[(week, m.get("matchup_id"))].append(m)

    rows = []
    for (w, mid), entries in groups.items():
        for m in entries:
            rid = m["roster_id"]
            opp = None
            for z in entries:
                if z["roster_id"] != rid:
                    opp = z["roster_id"]
                    break

            meta = roster_map.get(rid, {})
            username = meta.get("username", "Unknown")
            disp = meta.get("display_name", username)
            owner_name = display_name_for(username) if username != "Unknown" else display_name_for(disp)

            rows.append({
                "week": int(w),
                "matchup_id": mid if mid is not None else -1,
                "roster_id": rid,
                "points": float(m.get("points", 0.0)),
                "opponent_roster_id": opp,
                "owner_name": owner_name,
            })
    return pd.DataFrame(rows)

def all_weeks_df_from_sleeper(league_id: str, weeks: List[int]) -> pd.DataFrame:
    frames = [_week_df_from_sleeper(league_id, w) for w in weeks]
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

# Optional DB loader (stub)
def all_weeks_df_from_db(db_conn_or_engine, season: int, weeks: List[int]) -> pd.DataFrame:
    raise NotImplementedError("Implement DB loader when ready.")
