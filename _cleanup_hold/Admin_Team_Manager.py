# -*- coding: utf-8 -*-
# Legacy App/pages/team_view.py — Fantasy GM Team Dashboard (Sign + Drop with transaction logging)

import os, sys, re, json
from pathlib import Path
import requests
import streamlit as st

st.set_page_config(page_title="Fantasy GM — Team Dashboard", layout="wide")

# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

# ---------- Minimal .env loader ----------
def _load_kv_file(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and os.getenv(k) is None:
                os.environ[k] = v
        return True
    except Exception:
        return False

def load_env():
    here = Path(__file__).resolve()
    root = here.parents[1]; cwd = Path.cwd()
    for p in [here.with_name("fantasy_env"), here.with_name(".env"),
              cwd/"fantasy_env", cwd/".env", root/"fantasy_env", root/".env",
              cwd/"pages"/"fantasy_env", cwd/"pages"/".env"]:
        if _load_kv_file(p): break
    return (
        (os.getenv("SUPABASE_URL") or "").strip(),
        (os.getenv("SUPABASE_KEY") or "").strip(),
        (os.getenv("SLEEPER_LEAGUE_ID") or "").strip(),
    )

SUPABASE_URL, SUPABASE_KEY, SLEEPER_LEAGUE_ID = load_env()
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing Supabase credentials. Add SUPABASE_URL and SUPABASE_KEY to pages/fantasy_env (or .env).")
    st.stop()

# ---------- Minimal Supabase REST client (GET, POST/insert, RPC) ----------
class _SBResponse:
    def __init__(self, data): self.data = data

class _SBTable:
    def __init__(self, base_url, headers, name):
        self.base_url = base_url.rstrip("/")
        self.headers  = headers
        self.name     = name
        self._select  = "*"
        self._filters = []
        self._method  = "GET"
        self._payload = None

    def select(self, cols="*"):
        self._method = "GET"; self._select = cols; return self

    def eq(self, col, value):
        self._filters.append((col, value)); return self

    def insert(self, rows):
        self._method = "POST"; self._payload = rows; return self

    def execute(self):
        url = f"{self.base_url}/rest/v1/{self.name}"
        if self._method == "GET":
            params = {"select": self._select}
            for col, val in self._filters: params[col] = f"eq.{val}"
            r = requests.get(url, headers=self.headers, params=params, timeout=20)
        elif self._method == "POST":
            hdrs = dict(self.headers); hdrs["Prefer"] = "return=representation"
            r = requests.post(url, headers=hdrs, json=self._payload, timeout=20)
        else:
            raise RuntimeError("Unsupported method")
        r.raise_for_status()
        data = r.json() if r.content else None
        return _SBResponse(data)

class MiniSupabase:
    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        }
    def table(self, name): return _SBTable(self.url, self.headers, name)
    def rpc(self, fn_name, params=None):
        url = f"{self.url}/rest/v1/rpc/{fn_name}"
        r = requests.post(url, headers=self.headers, json=params or {}, timeout=20)
        r.raise_for_status()
        data = r.json() if r.content else None
        return _SBResponse(data)

sb = MiniSupabase(SUPABASE_URL, SUPABASE_KEY)

# ---------- UI ----------
st.title("🏈 Fantasy GM — Team Dashboard")

# Teams dropdown
try:
    response = sb.table("teams").select("team_name").execute()
    team_names = [t["team_name"] for t in (response.data or [])]
except Exception as e:
    st.error(f"Could not fetch teams: {e}")
    team_names = []

team_choice = st.selectbox("Select Team:", team_names) if team_names else None
if not team_names:
    st.warning("No teams found.")

# ---------- Helpers ----------
def load_roster(team_name: str):
    try:
        roster_resp = sb.table("roster").select("*").eq("owner_team_name", team_name).execute()
        return roster_resp.data or []
    except Exception as e:
        st.error(f"Error fetching roster for {team_name}: {e}")
        return []

def log_transaction(kind, team_name, player_name, pos, nfl_team, salary=None, years=None, notes=None):
    try:
        payload = [{
            "type": kind.upper(),
            "team": team_name,
            "player": player_name,
            "pos": pos,
            "nfl_team": nfl_team,
            "salary": salary,
            "years": years,
            "notes": notes or "",
        }]
        sb.table("transactions").insert(payload).execute()
    except Exception:
        pass  # keep UI clean

# ---------- Team summary + Drop UI ----------
if team_choice:
    st.subheader(f"📊 Team — {team_choice}")
    players = load_roster(team_choice)
    if players:
        st.dataframe(players, use_container_width=True)

        # Drop panel
        st.markdown("#### Drop a Player")
        drop_labels = [f"{r.get('full_name') or r.get('player')} ({r.get('pos') or r.get('position') or '-'}) [{r.get('sleeper_id')}]" for r in players]
        drop_pick = st.selectbox("Choose player to drop", drop_labels, key="drop_pick")
        drop_confirm = st.checkbox("I confirm I want to drop this player", key="drop_confirm")
        drop_notes = st.text_input("Notes (optional)", "", key="drop_notes")

        if st.button("Drop Player", key="drop_btn"):
            if not drop_confirm:
                st.warning("Please confirm the drop.")
            else:
                m = re.search(r"\[(.+?)\]$", drop_pick)
                sleeper_id = m.group(1) if m else None
                try:
                    res = sb.rpc("drop_player", {"p_team": team_choice, "p_sleeper_id": sleeper_id}).execute()
                    # Best-effort details for logging (from returned row or selected row)
                    dropped = None
                    if isinstance(res.data, list) and res.data:
                        dropped = res.data[0]
                    else:
                        dropped = next((r for r in players if str(r.get("sleeper_id")) == str(sleeper_id)), None)

                    if dropped:
                        log_transaction(
                            "DROP",
                            team_choice,
                            dropped.get("full_name") or dropped.get("player") or "",
                            dropped.get("pos") or dropped.get("position") or "",
                            dropped.get("nfl_team") or dropped.get("team") or "",
                            notes=drop_notes or "Dropped via app",
                        )

                    st.success("✅ Player dropped.")
                    st.dataframe(load_roster(team_choice), use_container_width=True)
                except Exception as e:
                    st.error(f"Drop failed: {e}")
    else:
        st.info("No players assigned.")

st.markdown("---")

# ---------- Free Agents (Search + Sign) ----------
st.header("🕵️ Free Agents")
col1, col2, col3 = st.columns([1, 2, 1])
with col1:
    fa_pos = st.selectbox("Position", ["", "QB", "RB", "WR", "TE", "K", "DEF"])
with col2:
    fa_query = st.text_input("Name contains", "")
with col3:
    fa_limit = st.number_input("Limit", 1, 200, 50)

if st.button("Search Free Agents"):
    try:
        resp = sb.rpc(
            "search_free_agents",
            {"p_pos": fa_pos or None, "p_name": fa_query or None, "p_limit": fa_limit},
        ).execute()
        fa_list = resp.data or []
        for r in fa_list:
            r["position"] = r.get("position") or r.get("pos")
            r["nfl_team"] = r.get("nfl_team") or r.get("team")
        st.dataframe(fa_list, use_container_width=True)

        if team_choice and fa_list:
            st.markdown("#### Sign a Player")
            labels = [f"{r['full_name']} ({r['position']}) [{r['sleeper_id']}]" for r in fa_list]
            sel    = st.selectbox("Choose player", labels, key="fa_pick")
            salary = st.number_input("Salary", 0, 500, 1, key="fa_salary")
            years  = st.number_input("Years", 1, 5, 1, key="fa_years")
            slot   = st.selectbox("Slot", ["ACTIVE", "IR", "TAXI"], key="fa_slot")

            if st.button("Sign", key="fa_sign_btn"):
                m = re.search(r"\[(.+?)\]$", sel)
                sleeper_id = m.group(1) if m else None
                pos_for_rpc = None if slot == "ACTIVE" else slot

                try:
                    out = sb.rpc(
                        "sign_free_agent",
                        {
                            "p_team": team_choice,
                            "p_sleeper_id": sleeper_id,
                            "p_salary": salary,
                            "p_years": years,
                            "p_pos": pos_for_rpc,
                            "p_nfl_team": None,
                        },
                    ).execute()

                    st.success("✅ Signed!" if (out.data is not None) else "✅ Done")

                    chosen = next((r for r in fa_list if str(r.get("sleeper_id")) == str(sleeper_id)), None)
                    if chosen:
                        log_transaction(
                            "SIGN",
                            team_choice,
                            chosen.get("full_name") or chosen.get("player") or "",
                            chosen.get("position") or chosen.get("pos") or "",
                            chosen.get("nfl_team") or chosen.get("team") or "",
                            salary=salary,
                            years=years,
                            notes="Signed via app",
                        )

                    st.dataframe(load_roster(team_choice), use_container_width=True)

                except Exception as e:
                    st.error(f"Signing failed: {e}")

        elif not team_choice:
            st.info("Select a team above to enable signing.")
    except Exception as e:
        st.error(f"Free-agent search failed: {e}")

st.markdown("---")

# ---------- Weekly Points (Sleeper) ----------
st.header("🗓️ Weekly Points")
left, right = st.columns([1, 1])
with left:
    wp_season = st.number_input("Season", min_value=2018, max_value=2100, value=2025, step=1)
with right:
    wp_week   = st.number_input("Week",   min_value=1,    max_value=25,   value=8,    step=1)

def _sleeper(url: str):
    r = requests.get(url, timeout=20); r.raise_for_status(); return r.json()

def _as_number(x):
    if isinstance(x, (int, float)): return float(x)
    if isinstance(x, list): return float(sum(v or 0 for v in x))
    try: return float(x)
    except Exception: return 0.0

def fetch_sleeper_week_points(league_id: str, week: int):
    users   = _sleeper(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = _sleeper(f"https://api.sleeper.app/v1/league/{league_id}/rosters")
    user_by_id  = {u["user_id"]: (u.get("display_name") or u.get("username") or f"user_{u['user_id']}") for u in users}
    roster_name = {r.get("roster_id"): user_by_id.get(r.get("owner_id"), f"Roster {r.get('roster_id')}") for r in rosters}
    matchups = _sleeper(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}") or []
    out = []
    for row in matchups:
        rid = row.get("roster_id")
        raw_points   = _as_number(row.get("points", 0))
        raw_starters = _as_number(row.get("starters_points", 0))
        weekly_points = raw_points if raw_points >= 10 or raw_starters <= 10 else raw_starters
        out.append({
            "team_name": roster_name.get(rid, f"Roster {rid}"),
            "roster_id": rid,
            "matchup_id": row.get("matchup_id"),
            "raw_points": round(raw_points, 2),
            "raw_starters_points": round(raw_starters, 2),
            "weekly_points": round(weekly_points, 2),
        })
    out.sort(key=lambda x: (x["matchup_id"], -x["weekly_points"]))
    return out

if SLEEPER_LEAGUE_ID:
    if st.button("Show Team Points"):
        try:
            rows = fetch_sleeper_week_points(SLEEPER_LEAGUE_ID, int(wp_week))
            if rows:
                st.dataframe([{"season": wp_season, "week": wp_week, **d} for d in rows],
                             use_container_width=True)
            else:
                st.info("No matchup data for that week.")
        except Exception as e:
            st.error(f"Failed to fetch Sleeper points: {e}")
else:
    st.info("Add SLEEPER_LEAGUE_ID to fantasy_env to enable weekly points.")

st.markdown("---")
st.caption("Powered by Supabase + Streamlit | Fantasy GM")
