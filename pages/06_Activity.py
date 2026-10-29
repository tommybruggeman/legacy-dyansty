# pages/06_Activity.py
# ------------------------------------------------------------
# Activity
# Everything that happens in the league in one place, newest
# first: trades (what each side got, cap change, points since,
# market value now), pickups, drops and the draft. Same feed as
# the app's League > Activity tab (api/activity.py).
# ------------------------------------------------------------

import os
import sys
from pathlib import Path

import streamlit as st

from auth import require_login, _sb
from components.sidebar_nav import render_nav
from season_engine import SeasonResolver
from services.app_context import current_nfl_week, get_user_id, last_scored_week, resolve_my_team

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Activity",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()

PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

from api import activity, teams  # noqa: E402
from services.activity_view import page_html  # noqa: E402

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


@st.cache_data(ttl=120, show_spinner="Loading league activity…")
def _feed(league_id: str, sleeper_id: str, current: tuple[int, int], week: int, _client) -> dict:
    try:
        states = teams.seasons_state(_client, league_id, current[0])
    except Exception:
        states = []
    contracts = {str(r.get("sleeper_player_id")): {"salary": r.get("contract_cap_hit") or r.get("cap_hit"), "years": r.get("contract_years_left")}
                 for r in (states[0].get("roster", []) if states else []) if r.get("sleeper_player_id")}
    return activity.feed(_client, league_id, sleeper_id, current, teams.league_teams(_client, league_id), contracts, week=week,
                         dead_cap=activity.dead_cap_index(states))


try:
    feed = _feed(str(league_id), sleeper_id, (int(season.season), last_scored_week(sleeper_id)), current_nfl_week(), sb)
except Exception as exc:
    st.error(f"Could not load league activity: {exc}")
    st.stop()

try:
    me = (resolve_my_team(get_user_id(), league_id) or {}).get("owner_name")
except Exception:
    me = None

KINDS = {"Trades": "trade", "Adds & drops": "move", "Draft": "draft"}
options = ["All", "Trades", "Adds & drops", "Draft"] + (["Mine"] if me else [])
choice = st.segmented_control("Show", options, default="All", label_visibility="collapsed") or "All"
shown = [i for i in feed["items"]
         if choice == "All" or i["kind"] == KINDS.get(choice) or (choice == "Mine" and me in i.get("owners", []))]

st.markdown(page_html(shown[:200], me, len(feed["items"])), unsafe_allow_html=True)
