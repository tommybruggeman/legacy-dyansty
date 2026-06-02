# pages/02_League_Overview.py
# League-wide overview: weekly points for every team + current rosters
# Prefers DB; falls back to Sleeper if tables/views are empty.

import os, sys
from pathlib import Path
from typing import Dict, List
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Legacy Dynasty — League Overview", page_icon="🧭", layout="wide")

# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR); sys.path.append(os.path.join(ROOT_DIR, "lib"))

# ---------- Minimal env loader ----------
def _load_kv(path: Path) -> bool:
    if not path.exists(): return False
    for raw in path.read_text().splitlines():
        if "=" not in raw or raw.strip().startswith("#"): continue
        k, v = raw.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and os.getenv(k) is None: os.environ[k] = v
    return True

def _load_env():
    here = Path(__file__).resolve()
    root, cwd = here.parents[1], Path.cwd()
    for p in [here.with_name("fantasy_env"), here.with_name(".env"),
              cwd/"fantasy_env", cwd/".env", root/"fantasy_env", root/".env",
              cwd/"pages"/"fantasy_env", cwd/"pages"/".env"]:
        if _load_kv(p): break
    return (os.getenv("SUPABASE_URL","").strip(),
            os.getenv("SUPABASE_KEY","").strip(),
            os.getenv("SLEEPER_LEAGUE_ID","").strip())

SUPABASE_URL, SUPABASE_KEY, SLEEPER_LEAGUE_ID = _load_env()

# ---------- Minimal Supabase REST client ----------
class _Resp:
    def __init__(self, data): self.data = data
class _Table:
    def __init__(self, base, headers, name):
        self.base, self.h, self.name = base.rstrip("/"), headers, name
        self._select, self._order, self._filters = "*", None, []
    def select(self, cols="*"): self._select=cols; return self
    def order(self, col, desc=False): self._order=(col,desc); return self
    def eq(self, col, val): self._filters.append((col,val)); return self
    def execute(self):
        import requests
        url=f"{self.base}/rest/v1/{self.name}"
        params={"select": self._select}
        for c,v in self._filters: params[c]=f"eq.{v}"
        if self._order: params["order"]=f"{self._order[0]}.{'desc' if self._order[1] else 'asc'}"
        r=requests.get(url, headers=self.h, params=params, timeout=25)
        if r.status_code==404: return _Resp([])
        r.raise_for_status(); return _Resp(r.json())
class SB:
    def __init__(self, url, key):
        self.url=url.rstrip("/")
        self.h={"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json","Accept":"application/json"}
    def table(self, name): return _Table(self.url, self.h, name)

sb = SB(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

# ---------- Helpers ----------
def _get_json(url: str):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

def load_owners_df() -> pd.DataFrame:
    """Return DataFrame[handle, name]. Prefer owners; fallback to owner_map."""
    # owners
    try:
        if sb:
            rows = sb.table("owners").select("*").execute().data or []
            if rows:
                df = pd.DataFrame(rows)
                if {"sleeper_username","display_name"}.issubset(df.columns):
                    out = df.rename(columns={"sleeper_username":"handle","display_name":"name"})
                    out["handle"] = out["handle"].astype(str).str.strip()
                    out["name"]   = out["name"].astype(str).str.strip()
                    return out[["handle","name"]].dropna().drop_duplicates()
    except Exception:
        pass
    # owner_map
    try:
        if sb:
            rows = sb.table("owner_map").select("*").execute().data or []
            if rows:
                df = pd.DataFrame(rows)
                handle_col = "handle" if "handle" in df.columns else ("team_name" if "team_name" in df.columns else None)
                name_col   = "full_name" if "full_name" in df.columns else ("display_name" if "display_name" in df.columns else None)
                if handle_col and name_col:
                    out = df.rename(columns={handle_col:"handle", name_col:"name"})
                    out["handle"] = out["handle"].astype(str).str.strip()
                    out["name"]   = out["name"].astype(str).str.strip()
                    return out[["handle","name"]].dropna().drop_duplicates()
    except Exception:
        pass
    # Fallback: pull users from Sleeper when nothing in DB
    if SLEEPER_LEAGUE_ID:
        try:
            users = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/users")
            df = pd.DataFrame([{
                "handle": (u.get("username") or "").strip(),
                "name":   (u.get("display_name") or u.get("username") or "").strip()
            } for u in users])
            return df.dropna().drop_duplicates()
        except Exception:
            pass
    return pd.DataFrame(columns=["handle","name"])

# ---- Weekly points: DB or Sleeper fallback for the WHOLE league
def weekly_points_all(owners: pd.DataFrame) -> pd.DataFrame:
    # Try DB first
    try:
        if sb:
            rows = sb.table("weekly_scores").select("*").execute().data or []
            if rows:
                df = pd.DataFrame(rows)
                # weekly_scores(owner=handle)
                return df
    except Exception:
        pass
    # Fallback to Sleeper
    if not SLEEPER_LEAGUE_ID:
        return pd.DataFrame()
    try:
        users   = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/users")
        rosters = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/rosters")
    except Exception:
        return pd.DataFrame()

    id_to_handle = {u["user_id"]: (u.get("username") or "").strip() for u in users}
    id_to_name   = {u["user_id"]: (u.get("display_name") or u.get("username") or "").strip() for u in users}
    rid_to_handle = {r["roster_id"]: id_to_handle.get(r["owner_id"], "") for r in rosters}
    rid_to_name   = {r["roster_id"]: id_to_name.get(r["owner_id"], f"Roster {r['roster_id']}") for r in rosters}

    rows = []
    for wk in range(1, 19):
        try:
            mats = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/matchups/{wk}") or []
        except Exception:
            break
        if not mats: break
        by_mid = {}
        for m in mats:
            mid = m.get("matchup_id")
            if mid is None: continue
            by_mid.setdefault(mid, []).append(m)
        for mid, games in by_mid.items():
            for g in games:
                rid = g.get("roster_id")
                owner_handle = rid_to_handle.get(rid, "")
                owner_name   = rid_to_name.get(rid, f"Roster {rid}")
                pts = float(g.get("points") or 0.0)
                opp_handle = ""; opp_name = None
                for h in games:
                    if h is not g:
                        rid2 = h.get("roster_id")
                        opp_handle = rid_to_handle.get(rid2, "")
                        opp_name   = rid_to_name.get(rid2, f"Roster {rid2}")
                        break
                rows.append({
                    "week": wk,
                    "owner": owner_handle if owner_handle else owner_name,  # prefer handle, fallback to name
                    "owner_display": owner_name,
                    "points": pts,
                    "opponent": opp_handle if opp_handle else (opp_name or None),
                    "matchup_id": str(mid)
                })
    return pd.DataFrame(rows)

# ---- Current rosters for the WHOLE league: DB or Sleeper fallback
def league_rosters_all(owners: pd.DataFrame) -> pd.DataFrame:
    # DB path: v_current_roster(owner=handle, player_sleeper_id)
    try:
        if sb:
            rows = sb.table("v_current_roster").select("*").execute().data or []
            if rows:
                return pd.DataFrame(rows)
    except Exception:
        pass
    # Fallback to Sleeper /rosters
    if not SLEEPER_LEAGUE_ID:
        return pd.DataFrame()
    try:
        rosters = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/rosters")
    except Exception:
        return pd.DataFrame()

    id_to_handle = dict(zip(owners["name"], owners["handle"]))  # not used here but kept for parity
    rows = []
    for r in rosters:
        owner_handle = r.get("owner_id")  # this is a user_id, not username
        # Need username mapping:
        # pull users to map user_id -> username
    try:
        users = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/users")
    except Exception:
        users = []
    uid_to_username = {u["user_id"]: (u.get("username") or "").strip() for u in users}

    rows = []
    for r in rosters:
        handle = uid_to_username.get(r.get("owner_id"), "")
        players = r.get("players") or []
        for pid in players:
            rows.append({"owner": handle, "player_sleeper_id": pid})
    return pd.DataFrame(rows)

# ---------- Load everything ----------
owners = load_owners_df()
if owners.empty:
    st.error("No owners found (DB and Sleeper both empty). Add `owners` rows or set SLEEPER_LEAGUE_ID.")
    st.stop()

# Weekly points (whole league)
wp = weekly_points_all(owners)
# Rosters (whole league)
rost = league_rosters_all(owners)

# ---------- UI ----------
st.title("🧭 League Overview")

tab_pts, tab_rosters = st.tabs(["Weekly Points", "Rosters"])

with tab_pts:
    if wp.empty:
        st.info("No weekly points found yet (DB empty and Sleeper fallback returned nothing).")
    else:
        # Normalize owner label: prefer display name match via owners map
        handle_to_name = dict(zip(owners["handle"], owners["name"]))
        wp["Team"] = wp["owner"].map(handle_to_name).fillna(wp.get("owner_display", wp["owner"]))

        # Pivot weeks across columns
        pivot = wp.pivot_table(index="Team", columns="week", values="points", aggfunc="sum").fillna(0.0)
        # Summary stats
        pivot["Total"] = pivot.sum(axis=1)
        pivot["PPG"]   = (pivot.drop(columns=["Total"]).sum(axis=1) / pivot.drop(columns=["Total"]).astype(bool).sum(axis=1)).round(2)
        # Last 3 weeks avg
        last3 = wp.groupby("Team").apply(lambda df: df.sort_values("week").tail(3)["points"].mean()).round(2)
        pivot["Last3"] = pivot.index.map(last3.to_dict()).fillna(0.0)

        # Sort by Total desc, then PPG
        pivot = pivot.sort_values(by=["Total","PPG"], ascending=[False, False])

        # Reorder columns: Total, PPG, Last3, weeks 1..18
        week_cols = [c for c in range(1,19) if c in pivot.columns]
        ordered = ["Total","PPG","Last3"] + week_cols
        # Put summary first, then weeks
        pivot = pivot[ordered]

        st.dataframe(pivot, use_container_width=True)

with tab_rosters:
    if rost.empty:
        st.info("No roster data yet (DB empty and Sleeper fallback returned nothing).")
    else:
        # Map handle -> display name
        handle_to_name = dict(zip(owners["handle"], owners["name"]))
        rost["Team"] = rost["owner"].map(handle_to_name).fillna(rost["owner"])
        # Group by team; show simple list of player ids for now
        teams = sorted(rost["Team"].dropna().unique().tolist())
        cols = st.columns(2)
        for i, team in enumerate(teams):
            with cols[i % 2]:
                with st.expander(team, expanded=False):
                    mine = rost[rost["Team"] == team].copy()
                    mine = mine.sort_values("player_sleeper_id")
                    # Show as a narrow table
                    st.dataframe(mine[["player_sleeper_id"]].rename(columns={"player_sleeper_id":"Player ID"}),
                                 hide_index=True, use_container_width=True)
        st.caption("Player names can be added later (Sleeper players mapping is large; we’ll wire it when we finalize).")
