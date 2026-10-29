# pages/06_Trades.py
# ------------------------------------------------------------
# Trades
# Every trade in the league, newest first: what each side got,
# cap change, points since and today's market value. Same feed
# as the app's Market > Trades tab (api/trade_feed.py).
# ------------------------------------------------------------

import os
import sys
from pathlib import Path

import streamlit as st

from auth import require_login, _sb
from components.sidebar_nav import render_nav
from season_engine import SeasonResolver
from services.app_context import get_user_id, last_scored_week, resolve_my_team

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Trades",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()

PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

from api import trade_feed  # noqa: E402
from services.trades_view import page_html  # noqa: E402

require_login("home.py")

access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")
league_id = st.session_state.get("active_league_id") or st.session_state.get("import_league_id")

if not access:
    st.error("No access token. Please sign in again.")
    st.stop()

if not league_id:
    st.error("No league selected. Go back to League Setup.")
    st.stop()

sb = _sb(access)
try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass

season = SeasonResolver(sb).get_active_season(league_id)
sleeper_id = "".join(ch for ch in str(season.sleeper_league_id or "") if ch.isdigit())
if not sleeper_id:
    st.error("This league does not have a Sleeper league connected yet.")
    st.stop()

st.markdown("<style>html,body,[data-testid='stAppViewContainer']{background:#061311;color:#FFF5E7}</style>", unsafe_allow_html=True)


@st.cache_data(ttl=600, show_spinner="Loading trades…")
def _feed(league_id: str, sleeper_id: str, current: tuple[int, int], _client) -> dict:
    rows = _client.table("league_teams").select("id,owner_name").eq("league_id", league_id).execute().data or []
    team_ids = {r["owner_name"]: str(r["id"]) for r in rows if r.get("owner_name")}
    return trade_feed.trades(_client, league_id, sleeper_id, current, team_ids)


try:
    feed = _feed(str(league_id), sleeper_id, (int(season.season), last_scored_week(sleeper_id)), sb)
except Exception as exc:
    st.error(f"Could not load trades: {exc}")
    st.stop()

try:
    me = (resolve_my_team(get_user_id(), league_id) or {}).get("owner_name")
except Exception:
    me = None

options = ["All"] + (["Mine"] if me else []) + [str(s) for s in feed["seasons"]]
choice = st.segmented_control("Show", options, default="All", label_visibility="collapsed") or "All"
shown = [t for t in feed["trades"]
         if choice == "All" or (choice == "Mine" and me in t["owners"]) or str(t["season"]) == choice]

st.markdown(page_html(shown, me, len(feed["trades"])), unsafe_allow_html=True)
