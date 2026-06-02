# -*- coding: utf-8 -*-
# Legacy App/pages/09_Draft_Picks.py — Draft Picks Manager (view/filter/edit)

import os, sys, requests
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Fantasy GM — Draft Picks", layout="wide")

# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

# ---------- Minimal env loader ----------
def _load_kv(path: Path) -> bool:
    if not path.exists():
        return False
    for raw in path.read_text().splitlines():
        if "=" not in raw or raw.strip().startswith("#"):
            continue
        k, v = raw.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and os.getenv(k) is None:
            os.environ[k] = v
    return True

def _load_env():
    here = Path(__file__).resolve()
    root, cwd = here.parents[1], Path.cwd()
    for p in [
        here.with_name("fantasy_env"), here.with_name(".env"),
        cwd / "fantasy_env", cwd / ".env",
        root / "fantasy_env", root / ".env",
        cwd / "pages" / "fantasy_env", cwd / "pages" / ".env"
    ]:
        if _load_kv(p):
            break
    return (os.getenv("SUPABASE_URL", "").strip(), os.getenv("SUPABASE_KEY", "").strip())

SUPABASE_URL, SUPABASE_KEY = _load_env()
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing Supabase credentials. Add SUPABASE_URL and SUPABASE_KEY to fantasy_env.")
    st.stop()

# ---------- Minimal Supabase REST client ----------
class _SBResponse:
    def __init__(self, data):
        self.data = data

class _SBTable:
    def __init__(self, base, headers, name):
        self.base = base.rstrip("/")
        self.h = headers
        self.name = name
        self._sel = "*"
        self._filters = []
        self._order = None
        self._method = "GET"
        self._payload = None

    def select(self, cols="*"):
        self._sel = cols
        return self

    def eq(self, col, val):
        self._filters.append((col, "eq", val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def insert(self, rows):
        self._method = "POST"
        self._payload = rows
        return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        if self._method == "GET":
            params = {"select": self._sel}
            for c, op, v in self._filters:
                params[c] = f"{op}.{v}"
            if self._order:
                params["order"] = f"{self._order[0]}.{'desc' if self._order[1] else 'asc'}"
            r = requests.get(url, headers=self.h, params=params, timeout=20)
        elif self._method == "POST":
            hdrs = dict(self.h)
            hdrs["Prefer"] = "return=representation"
            r = requests.post(url, headers=hdrs, json=self._payload, timeout=20)
        else:
            raise RuntimeError("Unsupported method")
        r.raise_for_status()
        return _SBResponse(r.json() if r.content else None)

class SB:
    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.h = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def table(self, name):
        return _SBTable(self.url, self.h, name)

sb = SB(SUPABASE_URL, SUPABASE_KEY)

# ---------- Access context ----------
role = st.session_state.get("role")
active_team = (
    st.session_state.get("active_team_name")
    or st.session_state.get("team_name")
)

is_commissioner = role in ["commissioner", "host", "admin"]

def patch_table(name: str, query: str, payload: dict):
    """Generic helper for updating rows."""
    url = f"{sb.url}/rest/v1/{name}?{query}"
    hdrs = dict(sb.h)
    hdrs["Prefer"] = "return=representation"
    r = requests.patch(url, headers=hdrs, json=payload, timeout=20)
    r.raise_for_status()
    return r.json() if r.content else None

# ---------- Helpers ----------
def fetch_teams():
    try:
        rows = sb.table("teams").select("team_name").order("team_name").execute().data or []
        return [r["team_name"] for r in rows if r.get("team_name")]
    except Exception:
        return []

def fetch_picks(season=None, owner=None):
    t = sb.table("draft_picks").select("*")
    if season:
        t = t.eq("season", season)
    if owner and owner != "All Teams":
        t = t.eq("current_owner", owner)
    t = t.order("round")
    try:
        return t.execute().data or []
    except Exception as e:
        st.error(f"Failed to load picks: {e}")
        return []

# ---------- UI ----------
st.title("🏈 Draft Picks")

all_teams = fetch_teams()

if is_commissioner:
    teams = ["All Teams"] + all_teams
else:
    teams = [active_team] if active_team else []

col1, col2 = st.columns([1, 2])
with col1:
    season = st.number_input("Season", min_value=2024, max_value=2100, value=2027, step=1)
with col2:
    owner = st.selectbox("Filter by Owner", teams)

rows = fetch_picks(season=season, owner=owner)
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No picks found for that filter.")

if is_commissioner:
    st.markdown("---")
    st.subheader("Commissioner Tool — Change Pick Owner")

    c1, c2, c3 = st.columns([1, 1, 1])

    with c1:
        edit_round = st.number_input(
            "Round",
            min_value=1,
            max_value=10,
            value=1,
            step=1,
        )

    with c2:
        edit_original = st.selectbox(
            "Original Team",
            [t for t in all_teams],
        )

    with c3:
        new_owner = st.selectbox(
            "New Owner",
            [t for t in all_teams],
        )

    if st.button("Update Owner"):
        try:
            patch_table(
                "draft_picks",
                f"season=eq.{season}&round=eq.{edit_round}&original_team=eq.{edit_original}",
                {"current_owner": new_owner},
            )

            st.success("✅ Updated.")

            rows = fetch_picks(season=season, owner=owner)
            st.dataframe(rows, use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Update failed: {e}")

    st.caption(
        "Commissioner repair tool for correcting pick ownership."
    )