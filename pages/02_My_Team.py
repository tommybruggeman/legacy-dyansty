# pages/02_My_Team.py
# ============================================================
# Legacy Dynasty — My Team Portal
# Owner homepage after login
# - Auto-detects signed-in user's league/team
# - Shows roster, cap, standings snapshot, picks, activity
# - Lets owner add/remove players from trade block
# ============================================================

from __future__ import annotations

import time
import html
import os
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

PAGE_START = time.perf_counter()

def tick(label):
    print(
        f"[MY TEAM] {label}: {time.perf_counter() - PAGE_START:.2f}s",
        flush=True
    )

import requests
import pandas as pd
import streamlit as st

from components.sidebar_nav import render_nav
from auth import auth_client, require_login, current_user
from services.lineup_editor import render_lineup_editor
from services.win_odds_view import win_odds_text
from services.my_team_context import resolve_my_team
from services.standings_lookup import (
    build_sleeper_team_identities,
    resolve_team_standings_row,
    standings_for_team_cards,
)
from services.app_context import build_standings_from_sleeper
from services.offseason_transactions import (
    OffseasonTransactionService,
    load_taxi_eligibility_provenance,
    taxi_eligible_player_names,
)
from services.team_roster_state import (
    CanonicalTeamStateError,
    cap_adjustment_display_rows,
    calculate_team_financials,
    invalidate_team_state_session_cache,
    load_team_state,
    merge_activity,
    roster_designation,
    state_activity,
    state_cap_adjustments,
    state_roster,
)

# ---------- page ----------
ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — My Team",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()
tick("page start")
require_login()

# ---------- paths / env ----------
PAGES_DIR = Path(__file__).resolve().parent
ROOT_DIR = PAGES_DIR.parent
sys.path.append(str(ROOT_DIR))
sys.path.append(str(ROOT_DIR / "lib"))

DATA_DIR = ROOT_DIR / "data"


def _load_kv(path: Path) -> bool:
    if not path.exists():
        return False

    for raw in path.read_text().splitlines():
        if "=" not in raw or raw.strip().startswith("#"):
            continue
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and os.getenv(k) is None:
            os.environ[k] = v

    return True


def _load_env() -> Tuple[str, str, str]:
    for p in [
        PAGES_DIR / "fantasy_env",
        PAGES_DIR / ".env",
        ROOT_DIR / "fantasy_env",
        ROOT_DIR / ".env",
        Path.cwd() / "fantasy_env",
        Path.cwd() / ".env",
    ]:
        if _load_kv(p):
            break

    return (
        os.getenv("SUPABASE_URL", "").strip(),
        (
            os.getenv("SUPABASE_KEY", "").strip()
            or os.getenv("SUPABASE_ANON_KEY", "").strip()
        ),
        os.getenv("SLEEPER_LEAGUE_ID", "").strip(),
    )


SUPABASE_URL, SUPABASE_KEY, SLEEPER_LEAGUE_ID = _load_env()


# ---------- minimal Supabase REST ----------
class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, base: str, headers: dict, name: str):
        self.base = base.rstrip("/")
        self.headers = headers
        self.name = name
        self._select = "*"
        self._filters = []
        self._in_filters = []
        self._order = None
        self._limit = None

    def select(self, cols="*"):
        self._select = cols
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, values):
        self._in_filters.append((col, list(values)))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        params = {"select": self._select}

        for c, v in self._filters:
            params[c] = f"eq.{v}"

        for c, values in self._in_filters:
            params[c] = f"in.({','.join(str(v) for v in values)})"

        if self._order:
            params["order"] = f"{self._order[0]}.{'desc' if self._order[1] else 'asc'}"

        if self._limit:
            params["limit"] = self._limit

        r = requests.get(url, headers=self.headers, params=params, timeout=25)

        if r.status_code == 404:
            return _Resp([])

        r.raise_for_status()
        return _Resp(r.json())


class SB:
    def __init__(self, url: str, key: str, access_token: Optional[str] = None):
        self.url = url.rstrip("/")
        token = access_token or key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def table(self, name: str):
        return _Table(self.url, self.headers, name)

    def rpc(self, name: str, params: dict):
        class _Rpc:
            def execute(inner_self):
                response = requests.post(
                    f"{self.url}/rest/v1/rpc/{name}", headers=self.headers,
                    json=params, timeout=25,
                )
                response.raise_for_status()
                return _Resp(response.json())
        return _Rpc()


access_token = st.session_state.get("sb_access_token")
sb = SB(SUPABASE_URL, SUPABASE_KEY, access_token) if SUPABASE_URL and SUPABASE_KEY else None


def rest_request(method: str, table: str, params: dict | None = None, json_body=None):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Missing Supabase credentials.")

    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }

    r = requests.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=25,
    )
    r.raise_for_status()

    try:
        return r.json()
    except Exception:
        return []

def log_team_activity(action: str, player_name: str, note: str = ""):
    payload = {
        "league_id": league_id,
        "owner_name": owner_name,
        "team_name": team_name,
        "player_name": player_name,
        "action": action,
        "note": note,
    }

    try:
        rest_request(
            "POST",
            "team_activity",
            json_body=payload,
        )

        load_team_activity.clear()

    except Exception as e:
        print(
            f"[TEAM ACTIVITY LOG FAILED] {action} - {player_name}: {e}",
            flush=True,
        )

        st.warning(
            f"Activity log failed: {e}"
        )

# ---------- CSS ----------
st.markdown(
    """
<style>
:root{
  --bg:#061311;
  --panel:#101E1D;
  --panel2:#0C1917;
  --gold:#E2BC5B;
  --goldSoft:rgba(226,188,91,.36);
  --text:#FFF5E7;
  --muted:#9DA89C;
  --rule:rgba(202,167,74,.12);
  --shadow:0 4px 18px rgba(0,0,0,.32);

  /* ======================================================
     EASY FONT SIZE CONTROLS
     Change ONLY these numbers when you want to experiment.
     1.00 = current size, 1.10 = 10% larger, .90 = 10% smaller.
     Desktop/tablet and phone can be tuned independently.
     ====================================================== */
  --my-team-desktop-font-scale: 1.00;
  --my-team-phone-font-scale: 1.00;
}

@media (min-width:769px){
  html { font-size: calc(16px * var(--my-team-desktop-font-scale)); }
}

html, body, [data-testid="stAppViewContainer"]{
  background:var(--bg);
  color:var(--text);
}

.block-container{
  padding-top:58px;
}

.hero{
  background:
    radial-gradient(100% 160% at 10% 0%, rgba(226,188,91,.18), transparent 45%),
    linear-gradient(135deg, #10201E 0%, #081513 100%);
  border:1px solid var(--goldSoft);
  border-radius:22px;
  padding:22px 24px;
  box-shadow:var(--shadow);
  margin-bottom:16px;
}

.hero-kicker{
  color:var(--muted);
  text-transform:uppercase;
  font-size:.72rem;
  letter-spacing:.08em;
  font-weight:800;
}

.hero-title{
  color:var(--text);
  font-size:2rem;
  font-weight:900;
  margin-top:2px;
  line-height:1.1;
}

.hero-sub{
  color:rgba(255,245,231,.72);
  font-size:.88rem;
  margin-top:6px;
}

.card{
  position:relative;
  background:radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel2) 100%);
  border:1px solid var(--goldSoft);
  border-radius:18px;
  box-shadow:var(--shadow);
  padding:15px 16px;
  box-sizing:border-box;
}

.card::before{
  content:"";
  position:absolute;
  top:7px;
  left:8px;
  right:8px;
  height:2px;
  border-radius:999px;
  background:linear-gradient(90deg, transparent, rgba(226,188,91,.62), transparent);
}

.card h3{
  margin:0 0 10px 0;
  font-size:.98rem;
  font-weight:900;
}

.metric-title{
  color:var(--muted);
  font-size:.7rem;
  text-transform:uppercase;
  letter-spacing:.05em;
}

.metric-value{
  color:var(--gold);
  font-size:1.4rem;
  font-weight:950;
  line-height:1.1;
  margin-top:4px;
}

.metric-sub{
  color:rgba(255,245,231,.62);
  font-size:.72rem;
  margin-top:3px;
}

.roster-panel{
  /* Tall enough for 12 players before the roster scrolls. */
  max-height:636px;
  overflow-y:auto;
}

.roster-header,
.roster-row{
  display:grid;
  grid-template-columns:52px minmax(0, 1fr) 70px 78px;
  gap:8px;
  align-items:center;
}

.roster-header{
  color:rgba(255,245,231,.6);
  font-size:.65rem;
  text-transform:uppercase;
  letter-spacing:.04em;
  border-bottom:1px solid var(--rule);
  padding-bottom:7px;
}

.roster-row{
  padding:8px 0;
  border-bottom:1px solid var(--rule);
}

.pos-pill{
  display:inline-flex;
  justify-content:center;
  min-width:34px;
  padding:2px 8px;
  border-radius:999px;
  background:rgba(226,188,91,.12);
  border:1px solid rgba(226,188,91,.38);
  color:var(--text);
  font-size:.64rem;
  font-weight:900;
}

.roster-badge{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-width:20px;
  height:20px;
  margin-left:6px;
  padding:0 6px;
  border-radius:999px;
  border:1px solid rgba(226,188,91,.55);
  color:#E2BC5B;
  font-size:.62rem;
  font-weight:900;
  cursor:default;
}

.roster-badge-action{
  cursor:pointer;
}

.player-name{
  font-size:.88rem;
  font-weight:750;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}

.small-cell{
  font-size:.74rem;
  text-align:right;
  color:rgba(255,245,231,.78);
}

.activity-item{
  padding:8px 0;
  border-bottom:1px solid var(--rule);
  font-size:.78rem;
}

.activity-item small{
  display:block;
  color:rgba(255,245,231,.48);
  font-size:.62rem;
  margin-top:2px;
}

.pick-item,
.trade-item{
  padding:7px 0;
  border-bottom:1px solid var(--rule);
  font-size:.78rem;
}

.empty{
  font-size:.75rem;
  color:rgba(255,245,231,.58);
}

[data-testid="stButton"] button{
  border-radius:999px !important;
  border:1px solid rgba(226,188,91,.55) !important;
  background:rgba(226,188,91,.12) !important;
  color:#FFF5E7 !important;
  font-weight:800 !important;
}

[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSelectbox"] [data-baseweb="select"]{
  background:rgba(255,255,255,.035) !important;
  border:1px solid rgba(226,188,91,.25) !important;
  color:#FFF5E7 !important;
  border-radius:12px !important;
}

.cap-hover-card {
  position: relative;
  cursor: help;
  overflow: visible;
  z-index: 10;
}

.cap-tooltip {
  display: none;
  position: absolute;
  top: 100%;
  margin-top: 8px;
  right: 0;
  width: 220px;
  background: #101E1D;
  border: 1px solid rgba(226,188,91,.45);
  border-radius: 14px;
  padding: 12px;
  box-shadow: 0 12px 28px rgba(0,0,0,.45);
  z-index: 9999;
}

.cap-hover-card:hover .cap-tooltip {
  display: block;
}

.cap-tooltip div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: .72rem;
  padding: 4px 0;
  color: #FFF5E7;
}

.cap-tooltip hr {
  border: 0;
  border-top: 1px solid rgba(226,188,91,.25);
  margin: 6px 0;
}


/* ==========================================================
   PHONE-ONLY RESPONSIVE LAYOUT
   Desktop layout remains unchanged.
   ========================================================== */

.mobile-metrics-grid,
.mobile-roster {
  display: none;
}

/* ==========================================================
   ACTION CARDS (desktop + phone)
   Trade Block, Taxi Squad and IR share the roster card's gold
   border, gradient and heading size. Taxi and IR sit side by side.
   ========================================================== */

.st-key-my_team_trade_block,
.st-key-my_team_taxi,
.st-key-my_team_ir,
.st-key-my_team_activity {
  border:1px solid var(--goldSoft) !important;
  border-radius:18px !important;
  background:radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel2) 100%) !important;
  box-shadow:var(--shadow) !important;
  padding:15px 16px !important;
  gap:.6rem !important;
}

/* Stretch Taxi and IR to the same height. */
.st-key-my_team_designations [data-testid="stColumn"] [data-testid="stLayoutWrapper"],
.st-key-my_team_designations [data-testid="stColumn"] [data-testid="stVerticalBlock"] {
  height:100% !important;
}

.st-key-my_team_trade_block h3,
.st-key-my_team_taxi h3,
.st-key-my_team_ir h3,
.st-key-my_team_activity h3 {
  font-size:.98rem !important;
  font-weight:900 !important;
  line-height:1.2 !important;
  margin:0 !important;
  padding:0 !important;
}

.st-key-my_team_trade_block h4 {
  font-size:.82rem !important;
  font-weight:800 !important;
  color:var(--muted) !important;
  text-transform:uppercase;
  letter-spacing:.05em;
  margin:0 0 .4rem 0 !important;
  padding:0 !important;
}

.st-key-my_team_trade_block hr {
  margin:.3rem 0 !important;
  border-color:var(--rule) !important;
}

.st-key-my_team_trade_block p,
.st-key-my_team_taxi p,
.st-key-my_team_ir p {
  font-size:.86rem !important;
  overflow:hidden;
  text-overflow:ellipsis;
}

.st-key-my_team_trade_block [data-testid="stSelectbox"] label,
.st-key-my_team_taxi [data-testid="stSelectbox"] label,
.st-key-my_team_ir [data-testid="stSelectbox"] label {
  font-size:.72rem !important;
}

.st-key-my_team_taxi [data-testid="stCaptionContainer"],
.st-key-my_team_ir [data-testid="stCaptionContainer"] {
  margin:0 !important;
  font-size:.72rem !important;
}

.st-key-my_team_designations {
  margin-top:4px;
}

/* Recent Team Activity: same tag colors as the Teams page. */
.activity-row {
  display:flex;
  align-items:center;
  gap:10px;
  padding:8px 0;
  border-bottom:1px solid rgba(226,188,91,.075);
}

.activity-tag {
  flex:0 0 auto;
  min-width:46px;
  box-sizing:border-box;
  text-align:center;
  white-space:nowrap;
  font-size:.6rem;
  font-weight:900;
  border-radius:6px;
  padding:3px 7px;
  background:rgba(255,245,231,.08);
  color:rgba(255,245,231,.75);
}

.activity-tag.sign { background:rgba(110,200,140,.15); color:#8fdcaa; }
.activity-tag.drop { background:rgba(230,110,100,.15); color:#ee9a90; }
.activity-tag.trade { background:rgba(226,188,91,.15); color:var(--gold); }

.activity-player {
  flex:1;
  min-width:0;
  font-size:.8rem;
  font-weight:700;
  color:var(--text);
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}

.activity-date {
  flex:0 0 auto;
  font-size:.66rem;
  color:rgba(255,245,231,.44);
  white-space:nowrap;
}

.activity-more summary {
  list-style:none;
  cursor:pointer;
  color:var(--gold);
  font-size:.8rem;
  font-weight:900;
  text-align:center;
  padding:10px 0 2px;
}

.activity-more summary::-webkit-details-marker { display:none; }
.activity-more .close-label { display:none; }
.activity-more[open] .open-label { display:none; }
.activity-more[open] .close-label { display:inline; }
.activity-more[open] { display:flex; flex-direction:column; }
.activity-more[open] summary { order:2; }

.st-key-my_team_designations [data-testid="stHorizontalBlock"] {
  display:grid !important;
  grid-template-columns:minmax(0,1fr) minmax(0,1fr) !important;
  gap:12px !important;
  align-items:stretch !important;
}

.st-key-my_team_designations [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
.st-key-my_team_designations [data-testid="stHorizontalBlock"] > [data-testid="column"] {
  width:100% !important;
  min-width:0 !important;
  max-width:100% !important;
}

.st-key-my_team_designations [data-testid="stElementContainer"]:has(> [data-testid="stButton"]),
.st-key-my_team_designations [data-testid="stButton"],
.st-key-my_team_designations [data-testid="stButton"] button {
  width:100% !important;
}

@media (max-width: 768px) {
  html { font-size: calc(16px * var(--my-team-phone-font-scale)); }

  .block-container {
    padding-top: 28px !important;
    padding-left: 14px !important;
    padding-right: 14px !important;
    max-width: 100% !important;
  }

  .hero {
    border-radius: 20px;
    padding: 18px;
    margin-bottom: 12px;
  }

  .hero-kicker {
    font-size: .66rem;
  }

  .hero-title {
    font-size: 1.75rem;
    line-height: 1.08;
  }

  .hero-sub {
    font-size: .82rem;
    line-height: 1.45;
  }

  /* Hide the existing five-column desktop metric row on phones only. */
  [data-testid="stHorizontalBlock"]:has(.desktop-metric-card) {
    display: none !important;
  }

  .mobile-metrics-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 9px;
    margin-bottom: 12px;
  }

  .mobile-metrics-grid .card {
    min-width: 0;
    min-height: 94px;
    border-radius: 16px;
    padding: 13px;
  }

  .mobile-metrics-grid .metric-title {
    font-size: .64rem;
  }

  .mobile-metrics-grid .metric-value {
    font-size: 1.25rem;
  }

  .mobile-metrics-grid .metric-sub {
    font-size: .68rem;
  }

  /* Desktop roster remains unchanged above 768px. */
  .desktop-roster {
    display: none !important;
  }

  .mobile-roster {
    display: block;
  }

  .mobile-roster .roster-panel {
    max-height: none;
    overflow: visible;
  }

  .mobile-roster .roster-header,
  .mobile-roster .roster-row {
    grid-template-columns: 48px minmax(0, 1fr) 46px 66px;
    gap: 5px;
  }

  .mobile-roster .roster-header {
    font-size: .60rem;
  }

  .mobile-roster .roster-row {
    padding: 9px 0;
  }

  .mobile-roster .player-name {
    font-size: .82rem;
  }

  .mobile-roster .small-cell {
    font-size: .70rem;
  }

  .mobile-roster .pos-pill {
    min-width: 32px;
    padding: 2px 6px;
    font-size: .59rem;
  }

  .mobile-roster .roster-badge {
    min-width: 18px;
    height: 18px;
    margin-left: 4px;
    padding: 0 5px;
  }

  .mobile-roster-details {
    margin-top: 0;
    display:flex;
    flex-direction:column;
  }

  .mobile-roster-details summary {
    order:1;
    list-style:none;
    cursor:pointer;
    color:var(--gold);
    font-size:.82rem;
    font-weight:900;
    text-align:center;
    min-height:44px;
    padding:13px 0 0;
  }

  .mobile-roster-details summary::-webkit-details-marker { display:none; }
  .mobile-roster-details .close-label { display:none; }
  .mobile-roster-details[open] .expanded-roster { order:1; }
  .mobile-roster-details[open] summary {
    order:2;
    border-top:1px solid var(--rule);
    margin-top:4px;
  }
  .mobile-roster-details[open] .open-label { display:none; }
  .mobile-roster-details[open] .close-label { display:inline; }

  .mobile-roster-details .expanded-roster {
    margin-top:0;
    padding-top:0;
  }

  /* ======================================================
     MY TEAM PHONE LAYOUT
     Roster runs full width, then Taxi Squad and IR side by side
     below it. Trade Block is desktop only.
     Streamlit 1.4x+ names columns stColumn; older builds used column.
     ====================================================== */

  .st-key-my_team_main_layout [data-testid="stHorizontalBlock"] {
    display:block !important;
  }

  .st-key-my_team_main_layout [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-my_team_main_layout [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    width:100% !important;
    min-width:0 !important;
    max-width:100% !important;
  }

  .st-key-my_team_trade_block {
    display:none !important;
  }

  .st-key-my_team_designations {
    margin-top:14px !important;
  }

  /* Keep Taxi + IR side by side even though the main layout stacks. */
  .st-key-my_team_main_layout .st-key-my_team_designations [data-testid="stHorizontalBlock"] {
    display:grid !important;
    gap:10px !important;
  }

  .st-key-my_team_taxi,
  .st-key-my_team_ir {
    padding:14px 12px !important;
  }

  .st-key-my_team_taxi h3,
  .st-key-my_team_ir h3,
  .st-key-my_team_activity h3 {
    font-size:1.05rem !important;
    line-height:1.2 !important;
    margin:0 !important;
    padding:0 !important;
  }

  .st-key-my_team_activity {
    margin-top:14px !important;
  }

  /* Mobile controls: larger touch targets without changing desktop controls. */
  [data-testid="stButton"] button {
    min-height: 44px;
  }

  /* 16px or larger stops iPhone Safari from zooming in when a field is tapped. */
  [data-testid="stTextInput"] input,
  [data-testid="stTextArea"] textarea,
  [data-testid="stSelectbox"] [data-baseweb="select"],
  [data-testid="stSelectbox"] input {
    min-height: 46px;
    font-size: 16px !important;
  }

  /* Hover-only cap tooltip is not useful on touch screens.
     The phone Cap Used card opens a tap-to-show breakdown instead. */
  .cap-tooltip {
    display: none !important;
  }

  .mobile-cap-details summary {
    list-style:none;
    cursor:pointer;
  }

  .mobile-cap-details summary::-webkit-details-marker { display:none; }

  .mobile-cap-details .cap-tap-hint {
    color:var(--gold);
    font-size:.64rem;
    font-weight:800;
    margin-top:4px;
  }

  .mobile-cap-details[open] .cap-tap-hint { display:none; }
  .mobile-cap-details[open] { grid-column:1 / -1; }

  .mobile-cap-breakdown {
    margin-top:8px;
    padding-top:6px;
    border-top:1px solid rgba(226,188,91,.25);
  }

  .mobile-cap-breakdown div {
    display:flex;
    justify-content:space-between;
    gap:8px;
    font-size:.68rem;
    padding:2px 0;
    color:#FFF5E7;
  }

  .mobile-cap-breakdown hr {
    border:0;
    border-top:1px solid rgba(226,188,91,.25);
    margin:4px 0;
  }

  .activity-item {
    font-size:.82rem;
    padding:10px 0;
  }

  .activity-item small {
    font-size:.68rem;
  }
}
@media (max-width: 390px) {
  .block-container {
    padding-left: 10px !important;
    padding-right: 10px !important;
  }

  .hero {
    padding: 16px;
  }

  .hero-title {
    font-size: 1.55rem;
  }

  .mobile-metrics-grid {
    gap: 7px;
  }

  .mobile-metrics-grid .card {
    padding: 11px;
  }

  .mobile-metrics-grid .metric-value {
    font-size: 1.15rem;
  }

  .mobile-roster .roster-header,
  .mobile-roster .roster-row {
    grid-template-columns: 42px minmax(0, 1fr) 40px 60px;
  }
}
</style>
""",
    unsafe_allow_html=True,
)


# ---------- helpers ----------
_NUM_RE = re.compile(r"[^0-9.\-()]")


def clean_num(s) -> str:
    s = ("" if s is None else str(s)).strip()
    if not s:
        return ""
    neg = s.startswith("(") and s.endswith(")")
    s = _NUM_RE.sub("", s).replace("(", "").replace(")", "")
    return "-" + s if neg else s


def as_float(x) -> float:
    try:
        return float(clean_num(x))
    except Exception:
        return 0.0


def ordinal(n: int) -> str:
    if n <= 0:
        return "—"
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1:'st', 2:'nd', 3:'rd'}.get(n % 10, 'th') }"


def get_user_id() -> Optional[str]:
    user = current_user()

    if isinstance(user, dict):
        return user.get("id") or user.get("user_id")

    return getattr(user, "id", None)

def ensure_active_league_from_user() -> Optional[str]:
    if st.session_state.get("active_league_id"):
        return st.session_state["active_league_id"]

    user_id = get_user_id()

    if not sb or not user_id:
        return None

    rows = (
        sb.table("league_memberships")
        .select("league_id, role")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )

    if len(rows) != 1:
        return None

    st.session_state["active_league_id"] = rows[0].get("league_id")
    st.session_state["role"] = rows[0].get("role")

    return st.session_state["active_league_id"]

def resolve_my_team_uncached(user_id: str) -> dict | None:
    league_id = ensure_active_league_from_user()

    if not sb or not league_id or not user_id:
        return None

    return resolve_my_team(sb, user_id=user_id, league_id=league_id)

def get_cached_my_team():
    user_id = get_user_id()

    if not user_id:
        return None

    cached = st.session_state.get("my_team_context")
    active_league = st.session_state.get("active_league_id")
    if (cached and cached.get("_user_id") == user_id
            and cached.get("league_id") == active_league):
        return cached

    my_team = resolve_my_team_uncached(user_id)

    if my_team:
        my_team["_user_id"] = user_id
        st.session_state["my_team_context"] = my_team
        st.session_state["active_league_id"] = my_team["league_id"]
        st.session_state["role"] = my_team.get("role")

    return my_team

# ---------- data loaders ----------
def load_canonical_team_state(league_id: str, season: int, team_id: str, cache_epoch: int) -> dict:
    return load_team_state(sb, league_id, season, team_id)

@st.cache_data(ttl=300, show_spinner=False)
def load_caps(league_id: str, context_generation: int) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        from services.publication_context import published_cap_rows
        rows = published_cap_rows(sb, league_id)
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_league_rules(league_id: str) -> dict:
    if not sb or not league_id:
        return {}

    try:
        rows = (
            sb.table("league_rules")
            .select("*")
            .eq("league_id", league_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        return rows[0] if rows else {}

    except Exception:
        return {}

@st.cache_data(ttl=300, show_spinner=False)
def load_draft_picks() -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("draft_picks")
            .select("*")
            .order("season")
            .order("round")
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def load_transactions(limit: int = 300) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("transactions_enriched")
            .select("*")
            .order("ts", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def load_team_activity(limit: int = 100) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("team_activity")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

        return pd.DataFrame(rows)

    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def load_trade_block(league_id: str, owner_name: str) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("trade_block")
            .select("*")
            .eq("league_id", league_id)
            .eq("owner", owner_name)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
@st.cache_data(ttl=300, show_spinner=False)
def load_cached_standings(league_id: str) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("league_standings")
            .select("*")
            .eq("league_id", league_id)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

# ---------- live standings ----------
def compute_live_standings() -> pd.DataFrame:
    if not SLEEPER_LEAGUE_ID:
        return pd.DataFrame()

    try:
        rid_to_name = roster_id_to_name(SLEEPER_LEAGUE_ID)
    except Exception:
        return pd.DataFrame()

    latest = current_nfl_week()
    rows = []

    for wk in range(1, latest + 1):
        try:
            matchups = get_json(
                f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/matchups/{wk}"
            ) or []
        except Exception:
            continue

        by_mid = {}
        for m in matchups:
            mid = m.get("matchup_id")
            if mid is not None:
                by_mid.setdefault(mid, []).append(m)

        week_rows = []

        for pair in by_mid.values():
            if len(pair) < 2:
                continue

            a, b = pair[0], pair[1]
            pa = as_float(a.get("points"))
            pb = as_float(b.get("points"))

            sa = as_float(a.get("starters_points"))
            sbp = as_float(b.get("starters_points"))

            if pa < 10 <= sa:
                pa = sa
            if pb < 10 <= sbp:
                pb = sbp

            na = rid_to_name.get(a.get("roster_id"), f"Roster {a.get('roster_id')}")
            nb = rid_to_name.get(b.get("roster_id"), f"Roster {b.get('roster_id')}")

            week_rows.append({"owner": na, "score": pa, "opp": pb, "win": 1 if pa > pb else 0})
            week_rows.append({"owner": nb, "score": pb, "opp": pa, "win": 1 if pb > pa else 0})

        if not week_rows:
            continue

        wdf = pd.DataFrame(week_rows)

        if wdf["score"].abs().sum() == 0:
            continue

        wdf = wdf.sort_values("score", ascending=False).reset_index(drop=True)
        wdf["top5"] = 0
        wdf.loc[: min(4, len(wdf) - 1), "top5"] = 1
        wdf["standing_points"] = (2 * wdf["win"] + wdf["top5"]).astype(int)

        rows.append(wdf)

    if not rows:
        return pd.DataFrame()

    big = pd.concat(rows, ignore_index=True)

    out = big.groupby("owner", as_index=False).agg(
        wins=("win", "sum"),
        games=("score", "count"),
        pf=("score", "sum"),
        pa=("opp", "sum"),
        standing_points=("standing_points", "sum"),
    )

    out["losses"] = out["games"] - out["wins"]
    out["ppg"] = out["pf"] / out["games"]
    out = out.sort_values(["standing_points", "pf"], ascending=[False, False]).reset_index(drop=True)
    out["rank"] = out.index + 1

    return out


@st.cache_data(ttl=300, show_spinner=False)
def load_sleeper_team_identities(league_id: str) -> dict[int, dict[str, object]]:
    if not league_id:
        return {}
    try:
        users = requests.get(
            f"https://api.sleeper.app/v1/league/{league_id}/users", timeout=25
        ).json()
        rosters = requests.get(
            f"https://api.sleeper.app/v1/league/{league_id}/rosters", timeout=25
        ).json()
        return build_sleeper_team_identities(users or [], rosters or [])
    except Exception:
        return {}

# ---------- load current owner ----------
my_team = get_cached_my_team()
tick("after resolve_my_team")

if not my_team:
    st.error(
        "No team is assigned to this login yet. The commissioner needs to connect this user to a league team."
    )

    with st.expander("Debug info"):
        st.write("Current user:", current_user())
        st.write("active_league_id:", st.session_state.get("active_league_id"))

    st.stop()

spinner_placeholder = st.empty()

spinner_placeholder.markdown(
    """
<style>
.legacy-loader-wrap {
    height: 70vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
}
.legacy-loader {
    width: 70px;
    height: 70px;
    border: 6px solid rgba(226,188,91,.25);
    border-top: 6px solid #E2BC5B;
    border-radius: 50%;
    animation: legacy-spin 1s linear infinite;
}
.legacy-loader-text {
    margin-top: 22px;
    font-size: 1.35rem;
    font-weight: 800;
    color: #F5EBD7;
}
@keyframes legacy-spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}
</style>

<div class="legacy-loader-wrap">
    <div class="legacy-loader"></div>
    <div class="legacy-loader-text">Loading Your Team...</div>
</div>
""",
    unsafe_allow_html=True,
)

if not my_team:
    st.error("No team is assigned to this login yet. The commissioner needs to connect this user to a league team.")
    st.stop()

league_id = my_team["league_id"]
team_name = my_team["team_name"] or "My Team"
owner_name = my_team["owner_name"] or team_name
role = my_team.get("role") or "owner"

league_rules = load_league_rules(league_id)
salary_cap = float(league_rules.get("salary_cap", 225))

from season_engine import SeasonResolver

active_league_season = SeasonResolver(auth_client()).get_active_season(league_id)
active_season = active_league_season.season
active_league_season_id = str(active_league_season.id)

try:
    canonical_state = load_canonical_team_state(
        league_id, active_season, str(my_team.get("team_id") or ""),
        int(st.session_state.get("team_state_cache_epoch", 0)),
    )
except CanonicalTeamStateError as exc:
    spinner_placeholder.empty()
    st.error("Canonical team state could not be loaded. Roster and cap totals were not rendered.")
    st.stop()
roster_df = pd.DataFrame(state_roster(canonical_state))
tick("after load_roster")

from services.publication_context import publication_generation
caps_df = load_caps(league_id, publication_generation(sb, league_id))
tick("after load_caps")

picks_df = load_draft_picks()
tick("after load_draft_picks")

tx_df = load_transactions()
tick("after load_transactions")

team_activity_df = load_team_activity()
tick("after load_team_activity")

canonical_activity_df = pd.DataFrame(state_activity(canonical_state, 300))
legacy_activity_rows = pd.concat([tx_df, team_activity_df], ignore_index=True).to_dict("records")
tx_df = pd.DataFrame(merge_activity(canonical_activity_df.to_dict("records"), legacy_activity_rows))
team_activity_df = pd.DataFrame()

trade_df = load_trade_block(league_id, owner_name)
tick("after load_trade_block")

cap_adj_df = pd.DataFrame(state_cap_adjustments(canonical_state))
tick("after load_cap_adjustments")

stand_df = standings_for_team_cards(build_standings_from_sleeper(SLEEPER_LEAGUE_ID))
# Streamlit Cloud keeps imported services modules loaded across redeploys,
# so an older standings_for_team_cards can still be running. Fill Top 5 and
# rank here too (Standings page order) so the cards never fall back to "—".
if not stand_df.empty:
    if "top5" not in stand_df.columns and "Top 5" in stand_df.columns:
        stand_df = stand_df.rename(columns={"Top 5": "top5"})
    if "rank" not in stand_df.columns:
        rank_order = [c for c in ("standing_points", "pf", "wins", "top5") if c in stand_df.columns]
        if rank_order:
            ranked = stand_df.sort_values(rank_order, ascending=False, kind="mergesort")
            stand_df.loc[ranked.index, "rank"] = range(1, len(ranked) + 1)
            stand_df["rank"] = stand_df["rank"].astype(int)
tick("after load_live_standings")

spinner_placeholder.empty()

my_roster = pd.DataFrame()
if not roster_df.empty:
    my_roster = roster_df[
        roster_df["league_team_id"].astype(str).eq(str(my_team.get("team_id")))
    ].copy()

# ---------- metrics ----------
record_txt = "0 – 0"
standing_txt = "—"
standing_points = 0
ppg = 0.0
top5_count = 0

row = resolve_team_standings_row(
    stand_df,
    sleeper_roster_id=my_team.get("sleeper_roster_id"),
    sleeper_owner_id=my_team.get("sleeper_owner_id"),
    identities_by_roster_id=load_sleeper_team_identities(SLEEPER_LEAGUE_ID),
)
if not row.empty:
    r = row.iloc[0]
    record_txt = f"{int(r.get('wins', 0))} – {int(r.get('losses', 0))}"
    standing_points = int(r.get("standing_points", 0) or 0)
    ppg = float(r.get("ppg", 0) or 0)
    standing_txt = ordinal(int(r.get("rank", 0) or 0))
    top5_count = int(r.get("top5", 0) or 0)

place_txt = f"{standing_txt} place" if standing_txt != "—" else "—"
# This week's win odds (same saved number the app shows), in place of the PPG caption when there is one
ppg_sub = win_odds_text(league_id, active_season, owner_name) or "Avg / game"
top5_txt = f"{top5_count} Top 5" + ("" if top5_count == 1 else "s")


active_salary = 0.0

if not my_roster.empty and "salary" in my_roster.columns:
    active_salary = (
        pd.to_numeric(my_roster["salary"], errors="coerce")
        .fillna(0)
        .sum()
    )

trade_carryover = 0.0
drop_charge = 0.0
taxi_adjustment = 0.0
ir_adjustment = 0.0
manual_adjustment = 0.0

if not cap_adj_df.empty:
    trade_carryover = cap_adj_df.loc[
        cap_adj_df["adjustment_type"] == "trade_carryover",
        "amount"
    ].sum()

    drop_charge = cap_adj_df.loc[
        cap_adj_df["adjustment_type"] == "dropped_player_charge",
        "amount"
    ].sum()

    taxi_adjustment = cap_adj_df.loc[
        cap_adj_df["adjustment_type"] == "taxi_adjustment",
        "amount"
    ].sum()

    ir_adjustment = cap_adj_df.loc[
        cap_adj_df["adjustment_type"] == "ir_adjustment",
        "amount"
    ].sum()

    manual_adjustment = cap_adj_df.loc[
        cap_adj_df["adjustment_type"] == "manual_adjustment",
        "amount"
    ].sum()

financials = calculate_team_financials(
    roster_df.to_dict("records"),
    cap_adj_df.to_dict("records"),
    salary_cap=salary_cap,
    league_team_id=my_team.get("team_id"),
    owner_name=owner_name,
)
active_salary = float(financials["active_salary"])
cap_used = float(financials["cap_used"])
cap_space = float(financials["cap_space"])

cap_txt = f"${cap_used:.1f}"
roster_count = len(my_roster)


# ---------- hero ----------
st.markdown(
    f"""
<div class="hero">
  <div class="hero-kicker">Owner Portal</div>
  <div class="hero-title">{team_name}</div>
  <div class="hero-sub">
    Manage your roster, trade block, draft picks, cap, and recent team activity.
  </div>
</div>
""",
    unsafe_allow_html=True,
)


# ---------- top metrics ----------
# Desktop: keep the existing Streamlit metric row layout.
m1, m2, m3, m4 = st.columns([1, 1, 1, 1])

metrics = [
    ("Record", record_txt, place_txt),
    ("Standing Points", standing_points, top5_txt),
    ("PPG", f"{ppg:.1f}", html.escape(ppg_sub)),
    ("Cap Used", cap_txt, f"${cap_space:.1f} space"),
]

dead_cap_tooltip_html = "".join(
    f"<div><strong>{label}</strong><span>{amount or ''}</span></div>"
    for label, amount in cap_adjustment_display_rows(cap_adj_df.to_dict("records"))
)

for col, (title, value, sub) in zip([m1, m2, m3, m4], metrics):
    with col:
        if title == "Cap Used":
            st.markdown(
                f"""
<div class="card cap-hover-card desktop-metric-card">
  <div class="metric-title">{title}</div>
  <div class="metric-value">{value}</div>
  <div class="metric-sub">{sub}</div>

  <div class="cap-tooltip">
    <div><strong>Active:</strong><span>${active_salary:.2f}</span></div>
    <div><strong>Trades:</strong><span>${trade_carryover:.2f}</span></div>
    {dead_cap_tooltip_html}
    <div><strong>Taxi:</strong><span>${taxi_adjustment:.2f}</span></div>
    <div><strong>IR:</strong><span>${ir_adjustment:.2f}</span></div>
    <hr>
    <div><strong>Limit:</strong><span>${salary_cap:.2f}</span></div>
    <div><strong>Total:</strong><span>${cap_used:.2f}</span></div>
    <div><strong>Space:</strong><span>${cap_space:.2f}</span></div>
  </div>
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
<div class="card desktop-metric-card">
  <div class="metric-title">{title}</div>
  <div class="metric-value">{value}</div>
  <div class="metric-sub">{sub}</div>
</div>
""",
                unsafe_allow_html=True,
            )

# Phone only: compact 2 x 2 grid. Cap Used opens a tap-to-show breakdown,
# since the desktop hover tooltip can't be reached on a touch screen.
st.markdown(
    f"""
<div class="mobile-metrics-grid">
  <div class="card">
    <div class="metric-title">Record</div>
    <div class="metric-value">{record_txt}</div>
    <div class="metric-sub">{place_txt}</div>
  </div>

  <div class="card">
    <div class="metric-title">Standing Points</div>
    <div class="metric-value">{standing_points}</div>
    <div class="metric-sub">{top5_txt}</div>
  </div>

  <div class="card">
    <div class="metric-title">PPG</div>
    <div class="metric-value">{ppg:.1f}</div>
    <div class="metric-sub">{html.escape(ppg_sub)}</div>
  </div>

  <details class="card mobile-cap-details">
    <summary>
      <div class="metric-title">Cap Used</div>
      <div class="metric-value">{cap_txt}</div>
      <div class="metric-sub">${cap_space:.1f} space</div>
      <div class="cap-tap-hint">Tap for breakdown</div>
    </summary>
    <div class="mobile-cap-breakdown">
      <div><strong>Active</strong><span>${active_salary:.2f}</span></div>
      <div><strong>Trades</strong><span>${trade_carryover:.2f}</span></div>
      {dead_cap_tooltip_html}
      <div><strong>Taxi</strong><span>${taxi_adjustment:.2f}</span></div>
      <div><strong>IR</strong><span>${ir_adjustment:.2f}</span></div>
      <hr>
      <div><strong>Limit</strong><span>${salary_cap:.2f}</span></div>
      <div><strong>Total</strong><span>${cap_used:.2f}</span></div>
      <div><strong>Space</strong><span>${cap_space:.2f}</span></div>
    </div>
  </details>

</div>
""",
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# ---------- main layout ----------
# Desktop keeps the existing two-column layout.
# Phone CSS stacks Roster first, then the action area below it.
with st.container(key="my_team_main_layout"):
    left, right = st.columns([1.45, 1])


# ---------- roster ----------
with left:
    # Build roster row HTML once so desktop and phone use identical roster data.
    roster_rows = []

    if not my_roster.empty:
        my_roster = my_roster.sort_values(["pos", "player"])

        for _, r in my_roster.iterrows():
            pos = r.get("pos") or "—"
            player = r.get("player") or "Unknown"
            years = r.get("years") or "—"
            salary = r.get("salary") or "—"

            badges = []

            if bool(r.get("is_rookie")):
                badges.append(
                    '<span class="roster-badge" title="Rookie">R</span>'
                )

            designation = roster_designation(r)
            if designation == "taxi":
                badges.append(
                    '<span class="roster-badge roster-badge-action" title="Taxi Squad">T</span>'
                )
            elif designation == "ir":
                badges.append(
                    '<span class="roster-badge roster-badge-action" title="Injured Reserve">IR</span>'
                )

            badges_html = "".join(badges)

            roster_rows.append(
                f"""
<div class="roster-row">
  <div><span class="pos-pill">{pos}</span></div>
  <div class="player-name">{player}{badges_html}</div>
  <div class="small-cell">{years}</div>
  <div class="small-cell">${salary}</div>
</div>
"""
            )

    # DESKTOP / TABLET: preserve the existing full roster card exactly.
    desktop_html = [
        '<div class="desktop-roster">',
        '<div class="card roster-panel">',
        "<h3>My Roster</h3>",
        '<div class="roster-header">',
        "<div>Pos</div><div>Player</div><div style='text-align:right;'>Years</div><div style='text-align:right;'>Salary</div>",
        "</div>",
    ]

    if not roster_rows:
        desktop_html.append(
            '<div class="empty" style="padding-top:10px;">No roster data found for this team.</div>'
        )
    else:
        desktop_html.extend(roster_rows)

    desktop_html.extend(["</div>", "</div>"])

    # PHONE ONLY: show five players first, with the rest behind View Full Roster.
    mobile_html = [
        '<div class="mobile-roster">',
        '<div class="card roster-panel">',
        "<h3>My Roster</h3>",
        '<div class="roster-header">',
        "<div>Pos</div><div>Player</div><div style='text-align:right;'>Years</div><div style='text-align:right;'>Salary</div>",
        "</div>",
    ]

    if not roster_rows:
        mobile_html.append(
            '<div class="empty" style="padding-top:10px;">No roster data found for this team.</div>'
        )
    else:
        mobile_html.extend(roster_rows[:5])

        if len(roster_rows) > 5:
            mobile_html.append(
                f"""
<details class="mobile-roster-details">
  <summary>
    <span class="open-label">View Full Team ›</span>
    <span class="close-label">Close Full Team ↑</span>
  </summary>
  <div class="expanded-roster">
    {''.join(roster_rows[5:])}
  </div>
</details>
"""
            )

    mobile_html.extend(["</div>", "</div>"])

    st.markdown(
        "".join(desktop_html) + "".join(mobile_html),
        unsafe_allow_html=True,
    )

# ---------- right actions ----------
# Desktop: Trade Block on top, Taxi Squad and IR as two cards below it.
# Phone: this column drops below the roster and Trade Block is hidden.
with right:
    action_trade_col = st.container()
    with st.container(key="my_team_designations"):
        taxi_col, ir_col = st.columns(2)
    activity_area = st.container()

with action_trade_col:
    # This week's lineup, once lineups are set in the app (playoffs on); hidden until then.
    render_lineup_editor(sb, league_id=str(league_id), season=int(active_season), team_id=str(my_team.get("team_id")),
                         sleeper_league_id=active_league_season.sleeper_league_id or SLEEPER_LEAGUE_ID,
                         roster=my_roster.to_dict("records") if not my_roster.empty else [])
    with st.container(border=True, key="my_team_trade_block"):
        st.markdown("### Trade Block")

        if trade_df.empty:
            st.markdown('<div class="empty">No players currently on your trade block.</div>', unsafe_allow_html=True)
        else:
            for _, r in trade_df.iterrows():
                tb_id = r.get("id")
                pname = r.get("player_name") or "Player"
                st.markdown(
                    f"<div class='trade-item'><strong>{pname}</strong></div>",
                    unsafe_allow_html=True
                )

                if tb_id and st.button(f"Remove {pname}", key=f"remove_tb_{tb_id}"):
                    rest_request("DELETE", "trade_block", params={"id": f"eq.{tb_id}"})

                    load_trade_block.clear()
                    log_team_activity("trade_block_remove", pname)

                    st.toast(f"{pname} removed from trade block")
                    st.rerun()
        st.markdown("---")
        st.markdown("#### Add Player to Trade Block")

        roster_names = sorted(my_roster["player"].dropna().astype(str).unique().tolist()) if not my_roster.empty else []

        selected_trade_block_player = st.selectbox(
            "Player",
            options=roster_names if roster_names else ["No roster players found"],
            disabled=not bool(roster_names),
            key="trade_block_player",
            label_visibility="collapsed",
        )

        if st.button("Add to Trade Block", disabled=not bool(roster_names)):
            payload = {
                "league_id": league_id,
                "owner": owner_name,
                "player_name": selected_trade_block_player,
            }

            rest_request(
                "POST",
                "trade_block",
                json_body=payload,
            )

            load_trade_block.clear()
            log_team_activity("trade_block_add", selected_trade_block_player)

            st.toast(f"{selected_trade_block_player} added to trade block")
            st.rerun()

# ---------- taxi / IR helpers ----------
# Used by the Taxi Squad and IR cards on both desktop and phone.
DESIGNATION_ADJUSTMENT_TYPES = {
    "Taxi Squad": "taxi_adjustment",
    "Injured Reserve": "ir_adjustment",
}

current_designations = pd.DataFrame()

if not cap_adj_df.empty:
    current_designations = cap_adj_df[
        cap_adj_df["adjustment_type"]
        .astype(str)
        .isin(["taxi_adjustment", "ir_adjustment"])
    ]

# Taxi candidates come from rookie draft provenance; load it once per run.
draft_assignments, taxi_provenance_error = load_taxi_eligibility_provenance(
    auth_client(), league_id,
)
taxi_roster = my_roster.rename(columns={"sleeper_id": "sleeper_player_id"}).to_dict(orient="records")
taxi_options = list(taxi_eligible_player_names(
    taxi_roster,
    draft_assignments,
    league_team_id=my_team["team_id"],
    draft_year=active_season,
)) if not my_roster.empty else []

# IR candidates: Taxi is locked for the season, so a Taxi player
# can never move to IR, and the current IR player is already there.
designated_names = set()
if not current_designations.empty:
    designated_names = set(
        current_designations["player_name"].dropna().astype(str)
    )
ir_options = sorted(
    str(r.get("player"))
    for r in my_roster.to_dict(orient="records")
    if r.get("player")
    and roster_designation(r) is None
    and str(r.get("player")) not in designated_names
) if not my_roster.empty else []


def designation_holders(designation: str) -> list[str]:
    # Read from the canonical roster so Taxi (which has no removable
    # designation row) shows too.
    held_designation = "taxi" if designation == "Taxi Squad" else "ir"
    return sorted(
        str(r.get("player"))
        for r in my_roster.to_dict(orient="records")
        if r.get("player") and roster_designation(r) == held_designation
    ) if not my_roster.empty else []


def designation_cap_adjustment(designation: str, salary: float) -> float:
    if designation == "Taxi Squad":
        # Canonical Taxi rule:
        # player carries 50% of the normal annual charge while on Taxi.
        taxi_charge = round(salary * 0.50, 2)
        adjustment_amount = round(taxi_charge - salary, 2)
    else:
        adjustment_amount = round(-(salary / 2), 2)
    return adjustment_amount


def remove_ir_designation(designation_id, label: str, player_name: str, remove_action: str):
    # Delete as the signed-in manager so cap_adjustments
    # RLS checks team authority. An RLS miss deletes zero
    # rows without raising, so confirm a row came back.
    try:
        deleted = (
            auth_client()
            .table("cap_adjustments")
            .delete()
            .eq("id", designation_id)
            .eq("adjustment_type", "ir_adjustment")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        deleted = []
        st.error(f"{label} removal failed: {exc}")
    else:
        if not deleted:
            st.error(
                f"{player_name} was not removed from {label}. "
                "You can only change your own team's designations."
            )

    if deleted:
        invalidate_team_state_session_cache(st.session_state)

        log_team_activity(
            remove_action,
            player_name,
        )

        st.toast(f"{player_name} removed from {label}")
        st.rerun()


def apply_designation(designation: str, player_name: str, player_row, salary: float):
    adjustment_type = DESIGNATION_ADJUSTMENT_TYPES[designation]
    existing_designation = pd.DataFrame()

    if not cap_adj_df.empty:
        existing_designation = cap_adj_df[
            cap_adj_df["adjustment_type"]
            .astype(str)
            .eq(adjustment_type)
        ]

    if not existing_designation.empty:
        # One player per designation per team (the IR RPC also
        # rejects a second IR row). Swapping is remove, then add.
        current_name = (
            existing_designation["player_name"].dropna().astype(str).iloc[0]
            if "player_name" in existing_designation.columns
            and existing_designation["player_name"].notna().any()
            else "the current player"
        )
        st.warning(
            f"{team_name} already has {current_name} on {designation}. "
            f"Remove {current_name} above, then designate a new player."
        )
        return

    sleeper_player_id = str(
        player_row.get("sleeper_id")
        or player_row.get("sleeper_player_id")
        or player_row.get("player_id")
        or ""
    ).strip()

    short_label = "Taxi" if designation == "Taxi Squad" else "IR"

    if not sleeper_player_id:
        st.error(
            f"Canonical player ID is missing. {short_label} assignment was not written."
        )
        return

    try:
        service = OffseasonTransactionService(
            auth_client(),
            league_id,
        )
        assignment = dict(
            player_id=sleeper_player_id,
            league_team_id=str(my_team["team_id"]),
            league_season_id=active_league_season_id,
            normal_annual_charge=salary,
        )
        if designation == "Taxi Squad":
            service.assign_rookie_taxi(**assignment)
        else:
            service.assign_injured_reserve(**assignment)

        invalidate_team_state_session_cache(st.session_state)

        log_team_activity(
            designation.lower(),
            player_name,
        )

        st.success(
            f"{player_name} added to {designation}"
        )

        st.rerun()

    except Exception as exc:
        st.error(
            f"{short_label} assignment failed: {exc}"
        )


def render_designation_form(designation: str, player_options: list[str], key_prefix: str):
    selected_player = st.selectbox(
        "Player",
        player_options,
        key=f"{key_prefix}_player",
    )

    if not selected_player:
        return

    player_row = my_roster[
        my_roster["player"] == selected_player
    ].iloc[0]

    salary = float(player_row.get("salary") or 0)
    adjustment_amount = designation_cap_adjustment(designation, salary)

    st.caption(
        f"Cap Adjustment: ${adjustment_amount:.2f}"
    )

    if st.button(
        "Apply",
        key=f"{key_prefix}_apply_designation",
    ):
        apply_designation(designation, selected_player, player_row, salary)


# ---------- taxi / IR cards ----------
with taxi_col:
    with st.container(border=True, key="my_team_taxi"):
        st.markdown("### Taxi Squad")
        if taxi_provenance_error:
            st.warning(taxi_provenance_error)

        taxi_holders = designation_holders("Taxi Squad")
        if taxi_holders:
            for holder in taxi_holders:
                st.markdown(f"**{holder}**")
            st.caption("Locked for the season")
        else:
            st.caption("Open")
            render_designation_form("Taxi Squad", taxi_options, "taxi")

with ir_col:
    with st.container(border=True, key="my_team_ir"):
        st.markdown("### IR")

        ir_rows = current_designations
        if not ir_rows.empty:
            ir_rows = ir_rows[ir_rows["adjustment_type"].astype(str).eq("ir_adjustment")]

        if not ir_rows.empty:
            for _, d in ir_rows.iterrows():
                designation_id = d.get("id")
                player_name = d.get("player_name") or "Player"
                st.markdown(f"**{player_name}**")
                if designation_id and st.button(
                    "Remove",
                    key=f"remove_designation_{designation_id}",
                ):
                    remove_ir_designation(designation_id, "Injured Reserve", player_name, "ir_removed")
        else:
            ir_holders = designation_holders("Injured Reserve")
            if ir_holders:
                for holder in ir_holders:
                    st.markdown(f"**{holder}**")
            else:
                st.caption("Open")
                render_designation_form("Injured Reserve", ir_options, "ir")
# ---------- activity ----------
# Desktop: below Taxi Squad / IR in the right column. Phone: below them too.
# Tags match the Teams page (Sign green, Drop red, Trade gold).
ACTIVITY_VISIBLE_ROWS = 6
ACTIVITY_MAX_ROWS = 20


def activity_row_html(action: str, player: str, ts: str) -> str:
    tag = action.lower() if action in ("Sign", "Drop", "Trade") else "other"
    return (
        '<div class="activity-row">'
        f'<span class="activity-tag {tag}">{html.escape(action)}</span>'
        f'<div class="activity-player">{html.escape(player)}</div>'
        f'<div class="activity-date">{html.escape(ts)}</div>'
        "</div>"
    )


with activity_area:
    with st.container(border=True, key="my_team_activity"):
        st.markdown("### Recent Team Activity")

        activity_parts = []

        if not tx_df.empty:
            mask = pd.Series(False, index=tx_df.index)

            for c in [
                "owner_name", "team_name", "from_owner_name", "to_owner_name",
                "from_team_name", "to_team_name",
            ]:
                if c in tx_df.columns:
                    mask = mask | tx_df[c].astype(str).str.strip().str.lower().eq(str(owner_name).strip().lower())
                    mask = mask | tx_df[c].astype(str).str.strip().str.lower().eq(str(team_name).strip().lower())

            tx_activity = tx_df[mask].copy()
            activity_parts.append(tx_activity)

        if not team_activity_df.empty:
            app_mask = (
                team_activity_df["owner_name"].astype(str).str.strip().str.lower().eq(str(owner_name).strip().lower())
                | team_activity_df["team_name"].astype(str).str.strip().str.lower().eq(str(team_name).strip().lower())
            )

            app_activity = team_activity_df[app_mask].copy()
            app_activity["tx_type"] = app_activity["action"]
            app_activity["acquisition"] = app_activity["action"]
            app_activity["ts"] = app_activity["created_at"]

            activity_parts.append(app_activity)

        activity = (
            pd.concat(activity_parts, ignore_index=True)
            if activity_parts
            else pd.DataFrame()
        )

        if not activity.empty:
            activity["_sort_ts"] = pd.to_datetime(
                activity.get("ts"),
                errors="coerce",
                utc=True,
            )
            activity = activity.sort_values("_sort_ts", ascending=False)

        if activity.empty:
            st.markdown('<div class="empty">No activity for your team yet.</div>', unsafe_allow_html=True)
        else:
            activity_rows = []

            for _, r in activity.head(ACTIVITY_MAX_ROWS).iterrows():
                ts_raw = r.get("ts") or r.get("created_at") or ""
                try:
                    ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    ts = f"{ts_dt:%b} {ts_dt.day}"
                except Exception:
                    ts = str(ts_raw)

                acq = str(r.get("acquisition") or "").lower()
                tx_type = str(r.get("tx_type") or "").lower()

                if acq in ["added", "add", "waiver"]:
                    action = "Sign"

                elif acq in ["dropped", "drop"]:
                    action = "Drop"

                elif acq == "traded" or tx_type == "trade":
                    action = "Trade"

                elif tx_type == "trade_block_add":
                    action = "Trade Block Add"

                elif tx_type == "trade_block_remove":
                    action = "Trade Block Remove"

                elif tx_type == "taxi squad":
                    action = "Taxi Squad"

                elif tx_type == "injured reserve":
                    action = "Injured Reserve"

                elif tx_type == "taxi_removed":
                    action = "Taxi Removed"

                elif tx_type == "ir_removed":
                    action = "IR Removed"

                else:
                    action = "Transaction"

                player = r.get("player_name") or "Player"
                activity_rows.append(activity_row_html(action, str(player), ts))

            # First rows show in the card; the rest open with Load More
            # (a plain <details>, so no rerun and no extra queries).
            feed = activity_rows[:ACTIVITY_VISIBLE_ROWS]
            if len(activity_rows) > ACTIVITY_VISIBLE_ROWS:
                feed.append(
                    '<details class="activity-more">'
                    '<summary><span class="open-label">Load More ›</span>'
                    '<span class="close-label">Show Less ↑</span></summary>'
                    + "".join(activity_rows[ACTIVITY_VISIBLE_ROWS:])
                    + "</details>"
                )

            st.markdown(
                f'<div class="activity-feed">{"".join(feed)}</div>',
                unsafe_allow_html=True,
            )
