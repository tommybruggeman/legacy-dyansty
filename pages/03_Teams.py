# pages/03_Teams.py
# ============================================================
# Legacy Dynasty — Team Dashboard
# ROW 1: Team / Record / PF / PPG / Cap (all on same row)
# ROW 2: Roster | (Trade Block + Draft Picks) | Activity Feed
# ============================================================

from __future__ import annotations
import os, sys, re
from pathlib import Path
from typing import Tuple, Optional
from datetime import datetime
import json
import urllib.parse  # <-- NEW
import html as html_lib

import requests
import pandas as pd
import streamlit as st
from components.sidebar_nav import render_nav
from auth import require_login, current_user
from services.config import configured_value
from services.standings_lookup import (
    build_sleeper_team_identities,
    resolve_team_standings_row,
    standings_for_team_cards,
)
from services.app_context import build_standings_from_sleeper
from services.team_roster_state import (
    CanonicalTeamStateError,
    cap_adjustment_display_rows,
    calculate_team_financials,
    load_team_state,
    merge_activity,
    roster_designation,
    state_activity,
    state_cap_adjustments,
    state_roster,
)

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Teams",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded"
)

render_nav()
require_login()
# ---------- paths / env ----------
PAGES_DIR = Path(__file__).resolve().parent
ROOT_DIR = PAGES_DIR.parent
sys.path.append(str(ROOT_DIR))
sys.path.append(str(ROOT_DIR / "lib"))

DATA_DIR = ROOT_DIR / "data"
CSV_CURRENT = DATA_DIR / "Current_Standings.csv"   # same file used by Season Standings

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

def _load_env() -> Tuple[str, str, str]:
    here = PAGES_DIR
    root, cwd = here.parent, Path.cwd()

    for p in [
        here / "fantasy_env", here / ".env",
        cwd / "fantasy_env", cwd / ".env",
        root / "fantasy_env", root / ".env",
        cwd / "pages" / "fantasy_env", cwd / "pages" / ".env",
    ]:
        if _load_kv(p):
            break

    return (
        configured_value("SUPABASE_URL"),
        (
            configured_value("SUPABASE_ANON_KEY")
            or configured_value("SUPABASE_KEY")
        ),
        configured_value("SLEEPER_LEAGUE_ID"),
    )

SUPABASE_URL, SUPABASE_KEY, SLEEPER_LEAGUE_ID = _load_env()

# ---------- minimal supabase client ----------
class _Resp:
    def __init__(self, data): self.data = data

class _Table:
    def __init__(self, base, headers, name):
        self.base, self.h, self.name = base.rstrip("/"), headers, name
        self._select, self._order, self._filters, self._in_filters, self._limit = "*", None, [], [], None

    def select(self, cols="*"):
        self._select = cols; return self

    def order(self, col, desc=False):
        self._order = (col, desc); return self

    def eq(self, col, val):
        self._filters.append((col, val)); return self

    def in_(self, col, values):
        self._in_filters.append((col, list(values))); return self

    def limit(self, n:int):
        self._limit = n; return self

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
        r = requests.get(url, headers=self.h, params=params, timeout=25)
        if r.status_code == 404: return _Resp([])
        r.raise_for_status(); return _Resp(r.json())

class SB:
    def __init__(self, url, key, access_token=None):
        self.url = url.rstrip("/")
        token = access_token or key
        self.h = {
            "apikey": key,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def table(self, name):
        return _Table(self.url, self.h, name)

    def rpc(self, name, params):
        class _Rpc:
            def execute(inner_self):
                response = requests.post(f"{self.url}/rest/v1/rpc/{name}", headers=self.h, json=params, timeout=25)
                response.raise_for_status()
                return _Resp(response.json())
        return _Rpc()

access_token = st.session_state.get("sb_access_token")
sb = SB(SUPABASE_URL, SUPABASE_KEY, access_token) if (SUPABASE_URL and SUPABASE_KEY) else None

# ---------- admin + REST helpers (NEW) ----------
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()  # same key you use on Transactions page

def _rest_request(method: str, table: str, params: dict | None = None, json_body=None):
    """
    Tiny helper around Supabase REST for write operations.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not configured")

    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=minimal",
    }

    r = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=25)
    r.raise_for_status()
    return r

ROSTER_TABLE_NAME = "rosters_admin"
ROSTER_PK_CANDIDATES = ["id"]  # our new primary key

# For writes: map the "pretty" column names in roster_df
# to the actual rosters_admin table column names.
ROSTER_WRITE_COL_MAP = {
    "pos": "position",
    "player": "player_name",
    "years": "contract_years",
    "salary": "contract_salary",
}

# ---------- CSS ----------
st.markdown(
    """
<style>
/* =========================
   Brand tokens
========================= */
:root{
  --bg: #061311;
  --panel: #101E1D;
  --panel-2: #0C1917;                 /* subtle depth */
  --gold: #E2BC5B;
  --gold-soft: rgba(226,188,91,.35);
  --text: #FFF5E7;
  --muted: #9DA89C;
  --rule: rgba(202,167,74,.08);
  --col-gap: 12px;

  /* depth + rings */
  --shadow-amb: 0 2px 10px rgba(0,0,0,.28), 0 1px 2px rgba(0,0,0,.28);
  --shadow-lift: 0 6px 22px rgba(0,0,0,.36), 0 2px 6px rgba(0,0,0,.28);
  --ring-focus: 0 0 0 1px rgba(226,188,91,.45), 0 0 0 3px rgba(226,188,91,.18);
}

@import url('https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: 'Lato', sans-serif !important;
  background: var(--bg);
  color: var(--text);
}
[data-testid="stToolbar"], [data-testid="stHeader"] h1, header, [data-testid="stAppHeader"] { display: none !important; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 64px; }

/* =========================
   Cards & Panels (depth + accent)
========================= */
.cardish,
.panel,
.stat-card{
  position: relative;
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--gold-soft);
  border-radius: 16px;
  box-shadow: var(--shadow-amb);
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease, background .18s ease;
  box-sizing: border-box;
  padding: 14px 16px !important;      /* uniform inner padding everywhere */
}

/* subtle gold accent line */
.cardish::before,
.panel::before,
.stat-card::before{
  content:"";
  position:absolute; left:6px; right:6px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(90deg, rgba(226,188,91,.0) 0%, rgba(226,188,91,.35) 22%, rgba(226,188,91,.6) 50%, rgba(226,188,91,.35) 78%, rgba(226,188,91,.0) 100%);
  pointer-events:none;
}

/* lift on hover */
.cardish:hover,
.panel:hover,
.stat-card:hover{
  box-shadow: var(--shadow-lift);
  transform: translateY(-2px);
  border-color: rgba(226,188,91,.48);
}

/* headers inside panels */
.panel h3{
  margin:0 0 8px 0;
  font-size:.95rem;
  font-weight:800;
  letter-spacing:.01em;
  color: var(--text);
  text-shadow: 0 0 12px rgba(226,188,91,.06);
}

/* trade block emphasis */
.panel.trade-block strong { color: var(--gold); }

/* =========================
   Row 1 layout (equal heights)
========================= */
.row1{ margin-bottom:16px; }
.row1.row1-cards .cardish,
.row1.row1-cards .stat-card{
  min-height:88px;
  display:flex; flex-direction:column; justify-content:center;
}

/* column gaps (Streamlit columns) */
.block-container .stColumns{ gap: var(--col-gap) !important; }

/* =========================
   Typography hierarchy
========================= */
.label{ font-size:.72rem; letter-spacing:.04em; text-transform:uppercase; color: var(--muted); }

/* stat cards */
.stat-title{ font-size:.7rem; color: var(--muted); margin-bottom:4px !important; text-transform:uppercase; letter-spacing:.03em; }
.stat-value{
  font-size:1.28rem; font-weight:900; line-height:1.12; margin-top:2px !important;
  color: var(--gold);
  text-shadow: 0 0 8px rgba(226,188,91,.18);
}
.stat-sub{ font-size:.72rem; color: rgba(255,245,231,.65); margin-top:2px; }

/* =========================
   Roster table
========================= */
.roster-panel{
  max-height:460px; min-height:420px;
  overflow-y:auto;
  border-radius:16px;
  padding:16px 16px 8px !important;
  box-shadow: var(--shadow-amb);
}
.roster-panel h3{ margin:0 0 10px 0; font-size:1rem; font-weight:800; }
.roster-row{
  display:flex; justify-content:space-between; align-items:center;
  gap:8px; padding:7px 0 6px; border-bottom:1px solid var(--rule);
  transition: background .15s ease;
}
.roster-row:hover{ background: rgba(226,188,91,.04); }
.pos-pill{
  background: rgba(226,188,91,.12);
  border:1px solid rgba(226,188,91,.45);
  border-radius:999px; font-size:.65rem; min-width:34px; text-align:center; text-transform:uppercase; padding:2px 10px;
}
.player-name{ font-size:.9rem; font-weight:600; }

/* =========================
   Middle & right columns
========================= */
.middle-stack{ height:100%; display:flex; flex-direction:column; gap:12px; }
.panel.grow{ flex:1; }

/* Right column panel matches roster height and stretches feed */
.panel.right-panel{
  min-height:420px;
  display:flex; flex-direction:column;
}
.activity-panel{ max-height:300px; overflow-y:auto; padding: 6px 6px 4px !important; }
.activity-item{
  border-bottom:1px solid var(--rule);
  padding:5px 0 6px;
  font-size:.78rem;
  transition: background .15s ease;
}
.activity-item:hover{ background: rgba(226,188,91,.04); }
.activity-item small{ display:block; opacity:.55; font-size:.6rem; }
.activity-empty{ font-size:.72rem; opacity:.65; margin-bottom:6px; }

/* =========================
   Scrollbar (subtle)
========================= */
.roster-panel::-webkit-scrollbar,
.activity-panel::-webkit-scrollbar{ width:8px; }
.roster-panel::-webkit-scrollbar-thumb,
.activity-panel::-webkit-scrollbar-thumb{
  background: rgba(226,188,91,.22);
  border-radius:8px;
}
.roster-panel::-webkit-scrollbar-track,
.activity-panel::-webkit-scrollbar-track{
  background: rgba(255,255,255,.03);
  border-radius:8px;
}

/* =========================
   Responsive
========================= */
@media (max-width:1100px){
  .row1.row1-cards .cardish,
  .row1.row1-cards .stat-card{ min-height:84px; }
}
/* Style the real Streamlit selectbox as the card */
/* === TEAM BOX — final polish (only this widget) ===================== */
.row1 [data-testid="stSelectbox"]{
  position: relative;
  overflow: hidden;                        /* clip accent line corners */
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--gold-soft);
  border-radius: 16px;
  box-shadow: var(--shadow-amb);
  padding: 14px 16px;                      /* same padding as other cards */
  min-height: 88px;                        /* same height as stat cards */
  display: flex;
  flex-direction: column;
  justify-content: center;
  margin-bottom: 0 !important;             /* remove default widget gap */
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease, background .18s ease;
}
.row1 [data-testid="stSelectbox"]:hover{
  box-shadow: var(--shadow-lift);
  transform: translateY(-2px);
  border-color: rgba(226,188,91,.48);
}
/* subtle gold accent line on top, matching other cards */
.row1 [data-testid="stSelectbox"]::before{
  content:"";
  position:absolute; left:6px; right:6px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(90deg, rgba(226,188,91,.0) 0%, rgba(226,188,91,.35) 22%, rgba(226,188,91,.6) 50%, rgba(226,188,91,.35) 78%, rgba(226,188,91,.0) 100%);
  pointer-events:none;
}

/* Label styling to match headers */
.row1 [data-testid="stSelectbox"] > label{
  margin: 0 0 6px 0 !important;
  font-size:.72rem !important;
  letter-spacing:.04em;
  text-transform:uppercase;
  color: var(--muted) !important;
}

/* Inner BaseWeb select chrome to match inputs across the app */
.row1 [data-testid="stSelectbox"] [data-baseweb="select"]{
  background: rgba(255,255,255,.02) !important;
  border: 1px solid rgba(226,188,91,.25) !important;
  border-radius: 12px !important;
  box-shadow: none !important;
  padding: 4px 10px !important;            /* a tad more breathing room */
  min-height: 38px;
  color: var(--text) !important;
}
.row1 [data-testid="stSelectbox"] [data-baseweb="select"]:focus-within{
  box-shadow: var(--ring-focus) !important;
  border-color: rgba(226,188,91,.55) !important;
}

/* Ensure the displayed value is readable & aligned with brand */
.row1 [data-testid="stSelectbox"] [data-baseweb="select"] div[role="combobox"],
.row1 [data-testid="stSelectbox"] [data-baseweb="select"] div[aria-haspopup="listbox"]{
  color: var(--text) !important;
  font-weight: 700;                         /* mirror stat emphasis */
}

/* Tweak the chevron to be subtle, not neon */
.row1 [data-testid="stSelectbox"] [data-baseweb="select"] svg{
  opacity:.75; filter: drop-shadow(0 0 0 rgba(0,0,0,0));
}

/* =========================
   Trade CTA button
========================= */
.trade-cta{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  padding:4px 10px;
  border-radius:999px;
  border:1px solid rgba(226,188,91,.55);
  background: radial-gradient(120% 140% at 20% 0%, rgba(226,188,91,.18) 0%, rgba(6,19,17,1) 80%);
  color: #FFF5E7;
  font-size:.7rem;
  font-weight:700;
  letter-spacing:.04em;
  text-transform:uppercase;
  text-decoration:none;
  box-shadow: var(--shadow-amb);
  transition: background .18s ease, box-shadow .18s ease, transform .18s ease, border-color .18s ease;
}
.trade-cta:hover{
  background: radial-gradient(120% 140% at 20% 0%, rgba(226,188,91,.28) 0%, rgba(6,19,17,1) 80%);
  box-shadow: var(--shadow-lift);
  border-color: rgba(226,188,91,.85);
  transform: translateY(-1px);
}
.trade-cta span{
  margin-left:4px;
  font-size:.65rem;
}
/* Style the Streamlit button used for the trade CTA */
.trade-cta-container button{
  padding:4px 10px;
  border-radius:999px !important;
  border:1px solid rgba(226,188,91,.55) !important;
  background: radial-gradient(120% 140% at 20% 0%, rgba(226,188,91,.18) 0%, rgba(6,19,17,1) 80%) !important;
  color:#FFF5E7 !important;
  font-size:.7rem !important;
  font-weight:700 !important;
  letter-spacing:.04em !important;
  text-transform:uppercase !important;
  box-shadow: var(--shadow-amb) !important;
}
.trade-cta-container button:hover{
  background: radial-gradient(120% 140% at 20% 0%, rgba(226,188,91,.28) 0%, rgba(6,19,17,1) 80%) !important;
  box-shadow: var(--shadow-lift) !important;
  border-color: rgba(226,188,91,.85) !important;
}

/* =========================
   Loading screen
========================= */
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
    box-shadow: 0 0 24px rgba(226,188,91,.18);
}
.legacy-loader-text {
    margin-top: 22px;
    font-size: 1.35rem;
    font-weight: 800;
    color: #F5EBD7;
    letter-spacing: .04em;
    text-transform: uppercase;
}
@keyframes legacy-spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

/* =========================================================
   TEAMS DASHBOARD — one card layout for phone and desktop.
   Base rules below are the phone design; the desktop block at the
   end of this stylesheet widens it into three columns.
   ========================================================= */

  .st-key-teams_mobile_switch,
  .st-key-teams_mobile_trade_action {
    margin:0 !important;
  }

  .st-key-teams_mobile_switch [data-testid="stVerticalBlock"],
  .st-key-teams_mobile_trade_action [data-testid="stVerticalBlock"] {
    gap:0 !important;
  }

  /* Style blocks and other empty elements sit in the page's main column;
     drop the column gap so they leave no blank space. Spacing is set on
     the rows themselves. */
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .teams-mobile-page-head) {
    gap:0 !important;
  }

  [data-testid="stMarkdownContainer"]:has(> .teams-block),
  .st-key-teams_mobile_switch [data-testid="stMarkdownContainer"] {
    margin-bottom:0 !important;
  }

  .teams-mobile-page-head {
    position:relative;
    min-height:44px;
    display:flex;
    align-items:center;
    justify-content:center;
    margin:0 0 12px 0;
  }

  .teams-mobile-page-title {
    color:var(--text);
    font-size:1.22rem;
    line-height:1;
    font-weight:900;
    letter-spacing:.01em;
    margin:0;
  }

  /* Team switcher: the whole card is the tap target for the team sheet. */
  .st-key-teams_mobile_switch {
    position:relative;
  }

  .teams-mobile-switch {
    display:flex;
    align-items:center;
    gap:12px;
    min-height:66px;
    padding:10px 14px;
    border:1px solid rgba(226,188,91,.55);
    border-radius:16px;
    background:linear-gradient(135deg,rgba(24,54,45,.96),rgba(16,39,34,.96));
    box-shadow:0 7px 22px rgba(0,0,0,.27), inset 0 1px 0 rgba(255,255,255,.025);
  }

  .teams-mobile-switch-text {
    flex:1;
    min-width:0;
  }

  .teams-mobile-switch-text small {
    display:block;
    color:rgba(255,245,231,.58);
    font-size:.62rem;
    letter-spacing:.1em;
    text-transform:uppercase;
  }

  .teams-mobile-switch-text strong {
    display:block;
    color:var(--text);
    font-size:1.05rem;
    font-weight:900;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }

  .teams-mobile-switch-pill {
    flex:0 0 auto;
    color:var(--gold);
    font-size:.78rem;
    font-weight:900;
    border:1px solid rgba(226,188,91,.42);
    border-radius:999px;
    padding:6px 11px;
  }

  /* Lay the (invisible) button over the whole card. Position its element
     container, not stButton: some Streamlit versions make the element
     container position:relative, which would trap the overlay. */
  .st-key-teams_mobile_switch [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
    position:absolute !important;
    inset:0;
    width:auto !important;
    z-index:2;
  }

  .st-key-teams_mobile_switch [data-testid="stButton"] {
    position:static !important;
    width:100% !important;
    height:100% !important;
  }

  .st-key-teams_mobile_switch [data-testid="stButton"] button {
    width:100% !important;
    height:100% !important;
    opacity:0 !important;
  }

  .teams-mobile-flow {
    display:flex !important;
    flex-direction:column;
    gap:10px;
    width:100%;
  }

  .teams-mobile-card {
    position:relative;
    overflow:hidden;
    width:100%;
    box-sizing:border-box;
    background:
      radial-gradient(120% 145% at 13% 0%,rgba(226,188,91,.10),transparent 43%),
      linear-gradient(145deg,#10201E 0%,#081513 100%);
    border:1px solid rgba(226,188,91,.43);
    border-radius:18px;
    box-shadow:0 7px 22px rgba(0,0,0,.27), inset 0 1px 0 rgba(255,255,255,.018);
    padding:14px 15px;
  }

  .teams-mobile-card::before {
    content:"";
    position:absolute;
    left:10px;
    right:10px;
    top:6px;
    height:2px;
    border-radius:99px;
    background:linear-gradient(90deg,transparent 0%,rgba(226,188,91,.14) 16%,rgba(226,188,91,.74) 50%,rgba(226,188,91,.14) 84%,transparent 100%);
    box-shadow:0 0 12px rgba(226,188,91,.14);
    pointer-events:none;
  }

  /* Team summary */
  .teams-mobile-avatar {
    width:44px;
    height:44px;
    flex:0 0 44px;
    border-radius:50%;
    display:flex;
    align-items:center;
    justify-content:center;
    color:var(--gold);
    font-size:.94rem;
    font-weight:900;
    background:radial-gradient(circle at 35% 25%,rgba(226,188,91,.18),rgba(8,21,19,.82));
    border:1px solid rgba(226,188,91,.62);
    box-shadow:0 0 16px rgba(226,188,91,.10);
  }



  .teams-mobile-stats {
    display:grid !important;
    grid-template-columns:repeat(4,minmax(0,1fr));
    gap:8px;
    width:100%;
  }

  .teams-mobile-summary { padding:12px; }
  .teams-mobile-summary::before { display:none; }

  .teams-mobile-stat {
    min-width:0;
    text-align:center;
    padding:10px 4px 9px;
    background:rgba(3,13,12,.35);
    border:1px solid rgba(226,188,91,.14);
    border-radius:12px;
  }


  .teams-mobile-stat-label {
    color:rgba(255,245,231,.57);
    text-transform:uppercase;
    font-size:.58rem;
    letter-spacing:.06em;
    white-space:nowrap;
  }

  .teams-mobile-stat-value {
    color:var(--text);
    font-size:1.02rem;
    font-weight:900;
    line-height:1.1;
    margin-top:4px;
    white-space:nowrap;
  }

  .teams-mobile-stat-value.gold { color:var(--gold); }

  .teams-mobile-stat-sub {
    color:rgba(255,245,231,.48);
    font-size:.6rem;
    line-height:1.05;
    margin-top:3px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }

  /* Section headings */
  .teams-mobile-section-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:8px;
    margin:2px 0 9px;
  }

  .teams-mobile-heading-left {
    display:flex;
    align-items:center;
    gap:8px;
    min-width:0;
  }

  .teams-mobile-section-icon {
    color:var(--gold);
    font-size:.95rem;
    line-height:1;
  }

  .teams-mobile-title {
    color:var(--text);
    font-size:.96rem;
    font-weight:900;
    line-height:1.1;
    margin:0;
  }

  .teams-mobile-count,
  .teams-mobile-link {
    color:var(--gold);
    font-size:.70rem;
    font-weight:800;
    white-space:nowrap;
  }

  .teams-mobile-empty {
    color:rgba(255,245,231,.62);
    font-size:.73rem;
    line-height:1.4;
  }

  /* Roster */
  .teams-mobile-roster-row {
    display:grid;
    grid-template-columns:42px minmax(0,1fr) auto;
    gap:8px;
    align-items:center;
    min-height:48px;
    padding:6px 0;
    border-top:1px solid rgba(226,188,91,.09);
  }

  .teams-mobile-roster-money {
    text-align:right;
    line-height:1.15;
    white-space:nowrap;
  }

  .teams-mobile-roster-money strong {
    display:block;
    color:var(--text);
    font-size:.86rem;
    font-weight:900;
  }

  .teams-mobile-roster-money span {
    color:rgba(255,245,231,.55);
    font-size:.66rem;
  }

  .teams-mobile-pos {
    display:inline-flex;
    align-items:center;
    justify-content:center;
    min-width:29px;
    max-width:34px;
    padding:3px 5px;
    border-radius:999px;
    background:rgba(226,188,91,.10);
    border:1px solid rgba(226,188,91,.46);
    color:var(--text);
    font-size:.57rem;
    font-weight:850;
  }

  .teams-mobile-roster-name {
    min-width:0;
    color:var(--text);
    font-size:.88rem;
    font-weight:800;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }


  .teams-mobile-roster-details {
    display:flex;
    flex-direction:column;
  }

  .teams-mobile-roster-details summary {
    order:1;
    list-style:none;
    cursor:pointer;
    color:var(--gold);
    text-align:center;
    font-size:.73rem;
    font-weight:900;
    min-height:38px;
    padding:11px 0 0;
  }

  .teams-mobile-roster-details summary::-webkit-details-marker { display:none; }
  .teams-mobile-roster-details .close-label { display:none; }
  .teams-mobile-roster-details[open] .expanded-roster { order:1; }
  .teams-mobile-roster-details[open] summary {
    order:2;
    border-top:1px solid rgba(226,188,91,.09);
    margin-top:3px;
  }
  .teams-mobile-roster-details[open] .open-label { display:none; }
  .teams-mobile-roster-details[open] .close-label { display:inline; }

  /* The two half-width cards from the rendered concept. */
  .teams-mobile-pair {
    display:grid !important;
    grid-template-columns:minmax(0,1fr) minmax(0,1fr) !important;
    gap:9px !important;
    width:100%;
  }

  .teams-mobile-pair .teams-mobile-card {
    min-width:0;
    min-height:142px;
    padding:13px;
  }

  .teams-mobile-mini-row {
    padding:6px 0;
    border-bottom:1px solid rgba(226,188,91,.08);
    color:rgba(255,245,231,.80);
    font-size:.69rem;
    line-height:1.3;
  }

  .teams-mobile-pick-year {
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:6px;
    padding:6px 0;
    border-bottom:1px solid rgba(226,188,91,.08);
    color:var(--text);
    font-size:.70rem;
  }

  .teams-mobile-pick-count {
    color:var(--gold);
    font-weight:900;
    white-space:nowrap;
  }

  .teams-mobile-picks-details summary {
    list-style:none;
    cursor:pointer;
    color:var(--gold);
    font-size:.68rem;
    font-weight:850;
    text-align:right;
    padding-top:8px;
  }

  .teams-mobile-picks-details summary::-webkit-details-marker { display:none; }

  .teams-mobile-picks-full {
    border-top:1px solid rgba(226,188,91,.08);
    margin-top:5px;
    padding-top:4px;
  }

  .teams-mobile-pick-line {
    color:rgba(255,245,231,.72);
    font-size:.62rem;
    line-height:1.35;
    padding:3px 0;
  }

  /* Trade button sits directly under the half-width cards. */
  .st-key-teams_mobile_trade_action {
    margin-top:10px !important;
    margin-bottom:10px !important;
  }

  .st-key-teams_mobile_trade_action [data-testid="stButton"] button {
    width:100% !important;
    min-height:43px !important;
    border-radius:11px !important;
    border:1px solid rgba(226,188,91,.28) !important;
    background:linear-gradient(135deg,rgba(31,62,49,.82),rgba(19,43,36,.82)) !important;
    color:var(--text) !important;
    font-size:.80rem !important;
    font-weight:750 !important;
    text-transform:none !important;
    letter-spacing:0 !important;
  }

  /* Activity / dead cap */
  .teams-mobile-activity-row,
  .teams-mobile-dead-row {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
    padding:7px 0;
    border-bottom:1px solid rgba(226,188,91,.075);
  }

  .teams-mobile-activity-row:last-child,
  .teams-mobile-dead-row:last-child { border-bottom:0; }

  .teams-mobile-activity-main,
  .teams-mobile-dead-main {
    min-width:0;
    color:var(--text);
    font-size:.73rem;
    line-height:1.25;
  }

  .teams-mobile-date {
    flex:0 0 auto;
    color:rgba(255,245,231,.44);
    font-size:.61rem;
    white-space:nowrap;
  }

  .teams-mobile-dead-amount { color:rgba(255,245,231,.82); }

.st-key-teams_mobile_trade_action {
  border:1px solid rgba(226,188,91,.22) !important;
  border-radius:15px !important;
  background:rgba(8,21,19,.72) !important;
  padding:9px !important;
}

.st-key-teams_top,
.st-key-teams_body {
  margin-bottom:10px !important;
}

/* Draft pick rows: season, one tag per pick, count. */
.teams-pick-season { font-weight:900; }

.teams-pick-chips {
  display:flex;
  flex-wrap:wrap;
  gap:4px;
  flex:1;
  min-width:0;
}

.teams-pick-chip {
  font-size:.66rem;
  font-weight:800;
  border:1px solid rgba(255,245,231,.18);
  border-radius:999px;
  padding:2px 7px;
  white-space:nowrap;
}

.teams-pick-chip.via {
  color:var(--gold);
  border-color:rgba(226,188,91,.6);
}

.teams-pick-chip em {
  font-style:normal;
  font-weight:600;
  margin-left:4px;
  color:rgba(255,245,231,.6);
}

/* Activity tags */
.teams-activity-tag {
  flex:0 0 46px;
  text-align:center;
  font-size:.6rem;
  font-weight:900;
  border-radius:6px;
  padding:3px 0;
  background:rgba(255,245,231,.08);
  color:rgba(255,245,231,.75);
}

.teams-activity-tag.sign { background:rgba(110,200,140,.15); color:#8fdcaa; }
.teams-activity-tag.drop { background:rgba(230,110,100,.15); color:#ee9a90; }
.teams-activity-tag.trade { background:rgba(226,188,91,.15); color:var(--gold); }

.teams-mobile-activity-main { flex:1; font-weight:700; }

.teams-mobile-activity-main small {
  display:block;
  color:rgba(255,245,231,.55);
  font-weight:500;
  font-size:.66rem;
  margin-top:2px;
}

.teams-mobile-dead-amount { font-weight:900; white-space:nowrap; }
.teams-mobile-stat-label .long { display:none; }

/* Phone: one column, title on the menu-button row. */
@media (max-width:768px) {
  .block-container {
    padding-top:10px !important;
    padding-left:14px !important;
    padding-right:14px !important;
    padding-bottom:32px !important;
    max-width:100% !important;
  }

  .st-key-teams_top [data-testid="stHorizontalBlock"],
  .st-key-teams_body [data-testid="stHorizontalBlock"] {
    flex-direction:column !important;
    gap:10px !important;
  }

  .st-key-teams_top [data-testid="stColumn"],
  .st-key-teams_body [data-testid="stColumn"] {
    width:100% !important;
    flex:1 1 100% !important;
    min-width:100% !important;
  }

  .st-key-teams_top [data-testid="stColumn"] [data-testid="stVerticalBlock"],
  .st-key-teams_body [data-testid="stColumn"] [data-testid="stVerticalBlock"] {
    gap:10px !important;
  }

  /* Half-width card: season and count on one line, the pick tags below. */
  .teams-mobile-pick-year { flex-wrap:wrap; }
  .teams-pick-chips { order:3; flex-basis:100%; }
}

/* Desktop: wider cards in three columns, the phone design scaled up. */
@media (min-width:769px) {
  .block-container {
    max-width:1320px !important;
    padding-top:3.2rem !important;
  }

  .teams-mobile-page-head {
    justify-content:flex-start;
    margin-bottom:16px;
  }

  .teams-mobile-page-title { font-size:1.75rem; }

  .st-key-teams_top,
  .st-key-teams_body {
    margin-bottom:14px !important;
  }

  .st-key-teams_top [data-testid="stHorizontalBlock"],
  .st-key-teams_body [data-testid="stHorizontalBlock"] {
    gap:14px !important;
  }

  .st-key-teams_body [data-testid="stColumn"] [data-testid="stVerticalBlock"] {
    gap:14px !important;
  }

  .teams-mobile-switch {
    min-height:96px;
    padding:14px 18px;
    border-radius:18px;
  }

  .teams-mobile-switch .teams-mobile-avatar {
    width:52px;
    height:52px;
    flex-basis:52px;
    font-size:1.05rem;
  }

  .teams-mobile-switch-text strong { font-size:1.35rem; }

  .teams-mobile-summary {
    background:none !important;
    border:0 !important;
    box-shadow:none !important;
    padding:0 !important;
  }

  .teams-mobile-stats { gap:14px; }

  .teams-mobile-stat {
    min-height:96px;
    display:flex;
    flex-direction:column;
    justify-content:center;
    border:1px solid rgba(226,188,91,.3);
    border-radius:18px;
    background:linear-gradient(145deg,#10201E 0%,#081513 100%);
  }

  .teams-mobile-stat-label { font-size:.7rem; }
  .teams-mobile-stat-label .long { display:inline; }
  .teams-mobile-stat-label .short { display:none; }
  .teams-mobile-stat-value { font-size:1.6rem; }
  .teams-mobile-stat-sub { font-size:.76rem; }

  .teams-mobile-card { padding:16px 18px; }
  .teams-mobile-title { font-size:1.08rem; }
  .teams-mobile-count { font-size:.78rem; }

  .teams-mobile-pair {
    grid-template-columns:minmax(0,1fr) !important;
    gap:14px !important;
  }

  .teams-mobile-pair .teams-mobile-card { min-height:0; }

  .teams-mobile-flow { gap:14px; }

  .teams-mobile-roster-name { font-size:.95rem; }
  .teams-mobile-roster-money strong { font-size:.95rem; }

  .teams-mobile-mini-row,
  .teams-mobile-empty,
  .teams-mobile-pick-year,
  .teams-mobile-activity-main,
  .teams-mobile-dead-main { font-size:.86rem; }

  .teams-mobile-date { font-size:.74rem; }
  .teams-pick-chip { font-size:.74rem; }
}

/* Team sheet (opened from the phone team switcher). */
[data-testid="stDialog"]:has(.teams-sheet-note) {
  background:rgba(0,0,0,.62) !important;
}

[data-testid="stDialog"]:has(.teams-sheet-note) > div {
  background:#0c1c1a !important;
  border:1px solid rgba(226,188,91,.45) !important;
  color:var(--text) !important;
  box-shadow:0 -12px 30px rgba(0,0,0,.5) !important;
}

[data-testid="stDialog"]:has(.teams-sheet-note) h2 {
  padding:20px 18px 6px !important;
}

[data-testid="stDialog"]:has(.teams-sheet-note) h2 p {
  color:var(--text) !important;
  font-size:1.05rem !important;
  font-weight:900 !important;
}

[data-testid="stDialog"]:has(.teams-sheet-note) section > div {
  padding:6px 14px 22px !important;
}

[data-testid="stDialog"] [data-testid="stVerticalBlock"]:has(> [data-testid="stLayoutWrapper"] .teams-sheet-row) {
  gap:7px !important;
}

[data-testid="stDialog"] .st-key-teams_mobile_team_search input {
  background:rgba(255,245,231,.05) !important;
  border-radius:12px !important;
  color:var(--text) !important;
  min-height:42px;
}

[data-testid="stDialog"] .st-key-teams_mobile_team_search [data-testid="stTextInputRootElement"] {
  border:1px solid rgba(255,245,231,.16) !important;
  border-radius:12px !important;
  background:transparent !important;
}

.teams-sheet-note {
  color:var(--gold);
  font-size:.74rem;
  font-weight:800;
  text-align:right;
  margin:-2px 2px 0;
}

[class*="st-key-teams_sheet_row_"] {
  position:relative;
  gap:0 !important;
}

[data-testid="stDialog"] [data-testid="stMarkdownContainer"]:has(.teams-sheet-row),
[data-testid="stDialog"] [data-testid="stMarkdownContainer"]:has(.teams-sheet-note) {
  margin-bottom:0 !important;
}

[class*="st-key-teams_sheet_row_"] [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
  position:absolute !important;
  inset:0;
  width:auto !important;
  z-index:2;
}

[class*="st-key-teams_sheet_row_"] [data-testid="stButton"] {
  position:static !important;
  width:100% !important;
  height:100% !important;
}

[class*="st-key-teams_sheet_row_"] [data-testid="stButton"] button {
  width:100% !important;
  height:100% !important;
  opacity:0 !important;
}

.teams-sheet-row {
  display:flex;
  align-items:center;
  gap:11px;
  min-height:56px;
  padding:8px 10px;
  border:1px solid rgba(226,188,91,.12);
  border-radius:13px;
  background:rgba(16,32,30,.65);
}

.teams-sheet-row.current {
  background:rgba(226,188,91,.11);
  border-color:rgba(226,188,91,.55);
}

.teams-sheet-rank {
  width:30px;
  flex:0 0 30px;
  text-align:center;
  color:rgba(255,245,231,.55);
  font-size:.74rem;
  font-weight:900;
}

.teams-sheet-row.current .teams-sheet-rank { color:var(--gold); }

.teams-sheet-avatar {
  width:36px;
  height:36px;
  flex:0 0 36px;
  border-radius:50%;
  display:flex;
  align-items:center;
  justify-content:center;
  color:var(--gold);
  font-size:.76rem;
  font-weight:900;
  background:radial-gradient(circle at 35% 25%,rgba(226,188,91,.18),rgba(8,21,19,.82));
  border:1px solid rgba(226,188,91,.62);
}

.teams-sheet-name {
  flex:1;
  min-width:0;
  color:var(--text);
  font-size:.94rem;
  font-weight:800;
  line-height:1.2;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}

.teams-sheet-name small {
  display:block;
  color:rgba(255,245,231,.55);
  font-size:.7rem;
  font-weight:600;
}

.teams-sheet-record {
  color:var(--text);
  font-size:.9rem;
  font-weight:900;
}

.teams-sheet-check {
  color:var(--gold);
  font-weight:900;
}

@media (max-width:768px) {
  /* Slide the team sheet up from the bottom of the screen. */
  [data-testid="stDialog"]:has(.teams-sheet-note) {
    align-items:flex-end !important;
    padding:0 !important;
  }

  [data-testid="stDialog"]:has(.teams-sheet-note) > div {
    width:100% !important;
    max-width:100% !important;
    margin:0 !important;
    max-height:84vh;
    overflow-y:auto !important;
    border-radius:22px 22px 0 0 !important;
    border-bottom:0 !important;
  }

  [data-testid="stDialog"]:has(.teams-sheet-note) > div::before {
    content:"";
    position:absolute;
    top:9px;
    left:50%;
    width:42px;
    height:5px;
    margin-left:-21px;
    border-radius:99px;
    background:rgba(255,245,231,.25);
  }
}

@media (max-width:390px) {
  .block-container {
    padding-left:10px !important;
    padding-right:10px !important;
  }

  .teams-mobile-card { padding:13px; border-radius:17px; }
  .teams-mobile-pair { gap:7px !important; }
  .teams-mobile-stats { gap:6px; }
  .teams-mobile-stat-value { font-size:.94rem; }
  .teams-mobile-roster-name { font-size:.84rem; }
}

</style>
""",
    unsafe_allow_html=True,
)

# ---------- helpers ----------
_NUM_RE = re.compile(r"[^0-9.\-()]")

def _clean_num(s: str) -> str:
    s = ("" if s is None else str(s)).strip()
    if not s: return ""
    neg = s.startswith("(") and s.endswith(")")
    s = _NUM_RE.sub("", s).replace("(", "").replace(")", "")
    return "-" + s if neg else s

def _as_int(x) -> int:
    try: return int(float(_clean_num(x)))
    except: return 0

def _as_float(x) -> float:
    try: return float(_clean_num(x))
    except: return 0.0

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())

def _ordinal(n: int) -> str:
    if n <= 0: return "—"
    if 10 <= (n % 100) <= 20: return f"{n}th"
    return f"{n}{ {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th') }"

def _ensure_numeric(df: pd.DataFrame, col: str, default=0.0) -> pd.Series:
    return pd.to_numeric(df.get(col, default), errors="coerce").fillna(default)

def _find_col(df: pd.DataFrame, keys: list[str]) -> Optional[str]:
    if df.empty: return None
    idx = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in df.columns}
    for k in keys:
        if k in idx: return idx[k]
    return None

def _get_json(url: str):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

def show_loading_screen(placeholder, text: str = "Loading Team Dashboard..."):
    placeholder.markdown(
        f"""
        <div class="legacy-loader-wrap">
            <div class="legacy-loader"></div>
            <div class="legacy-loader-text">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------- standings loaders (live from Sleeper weekly matchups) ----------
def _current_nfl_week(max_week: int = 25) -> int:
    """
    Resolve current NFL week from Sleeper state, with a fallback that scans
    backwards for the latest week that has any matchups.
    """
    if not SLEEPER_LEAGUE_ID:
        return 1
    try:
        state = _get_json("https://api.sleeper.app/v1/state/nfl")
        wk = int(state.get("week") or 1)
        if wk <= 0:
            raise ValueError("preseason or unknown week")
        return max(1, min(max_week, wk))
    except Exception:
        for wk in range(max_week, 0, -1):
            try:
                rows = _get_json(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/matchups/{wk}") or []
                if rows:
                    return wk
            except Exception:
                continue
        return 1

def _roster_id_to_name(league_id: str) -> dict[int, str]:
    """
    Map Sleeper roster_id -> OWNER HANDLE (username), not display name.

    This makes all downstream standings keyed by handle so we can match
    cleanly against owners_df['handle'] (e.g. MekelS, RollPals, etc.).
    """
    users   = _get_json(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = _get_json(f"https://api.sleeper.app/v1/league/{league_id}/rosters")

    # Prefer username (handle); fall back to display_name if needed
    uid_to_handle: dict[str, str] = {}
    for u in users:
        uid     = u.get("user_id")
        handle  = (u.get("username") or "").strip()
        display = (u.get("display_name") or "").strip()
        val = handle or display or ""
        if uid and val:
            uid_to_handle[uid] = val

    rid_to_handle: dict[int, str] = {}
    for r in rosters:
        rid = r.get("roster_id")
        own = r.get("owner_id")
        if rid is None:
            continue
        name = uid_to_handle.get(own)
        if not name:
            name = f"Roster {rid}"
        rid_to_handle[rid] = name

    return rid_to_handle

def _fetch_week_pairs(league_id: str, week: int):
    """
    Pull raw Sleeper matchups for a week and pair entries with same matchup_id.
    Each pair is (teamA_row, teamB_row).
    """
    rows = _get_json(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}") or []
    by_mid: dict[int, list] = {}
    for r in rows:
        mid = r.get("matchup_id")
        if mid is None:
            continue
        by_mid.setdefault(mid, []).append(r)

    pairs: list[tuple[dict, dict]] = []
    for arr in by_mid.values():
        if len(arr) >= 2:
            pairs.append((arr[0], arr[1]))
    return pairs

def _compute_week_df(league_id: str, week: int) -> pd.DataFrame:
    """
    One week of rows:
      Team, Score, OppScore, Win, Rank, Top5_wk, StandingPoints_wk
    """
    rid_to_name = _roster_id_to_name(league_id)
    pairs = _fetch_week_pairs(league_id, week)

    wk_rows: list[dict] = []
    for a, b in pairs:
        ra, rb = a.get("roster_id"), b.get("roster_id")
        na, nb = rid_to_name.get(ra, f"Roster {ra}"), rid_to_name.get(rb, f"Roster {rb}")

        pa = _as_float(a.get("points"))
        pb = _as_float(b.get("points"))
        sa = _as_float(a.get("starters_points"))
        sb = _as_float(b.get("starters_points"))

        if pa < 10.0 <= sa:
            pa = sa
        if pb < 10.0 <= sb:
            pb = sb

        wk_rows.append({"Team": na, "Score": pa, "OppScore": pb, "Win": 1 if pa > pb else 0})
        wk_rows.append({"Team": nb, "Score": pb, "OppScore": pa, "Win": 1 if pb > pa else 0})

    if not wk_rows:
        return pd.DataFrame()

    df_w = pd.DataFrame(wk_rows)

    if df_w["Score"].abs().sum() == 0 and df_w["OppScore"].abs().sum() == 0:
        return pd.DataFrame()

    df_w = df_w.sort_values(["Score", "Team"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
    df_w["Rank"] = df_w.index + 1

    top_n = min(5, len(df_w))
    df_w["Top5_wk"] = 0
    df_w.loc[: top_n - 1, "Top5_wk"] = 1

    df_w["StandingPoints_wk"] = (2 * df_w["Win"] + df_w["Top5_wk"]).astype(int)
    return df_w

@st.cache_data(ttl=300, show_spinner=False)
def load_live_standings() -> pd.DataFrame:
    """
    Use the exact live standings snapshot builder used by the Standings page.
    """
    standings = standings_for_team_cards(
        build_standings_from_sleeper(SLEEPER_LEAGUE_ID)
    )
    if standings.empty:
        return standings
    standings = standings.sort_values(
        ["standing_points", "pf"], ascending=[False, False]
    ).reset_index(drop=True)
    standings["rank"] = standings.index + 1
    return standings

@st.cache_data(ttl=300, show_spinner=False)
def load_sleeper_team_identities(league_id: str) -> dict[int, dict[str, object]]:
    if not league_id:
        return {}
    try:
        users = _get_json(f"https://api.sleeper.app/v1/league/{league_id}/users") or []
        rosters = _get_json(f"https://api.sleeper.app/v1/league/{league_id}/rosters") or []
        return build_sleeper_team_identities(users, rosters)
    except Exception:
        return {}
def ensure_active_league_from_user() -> Optional[str]:
    if st.session_state.get("active_league_id"):
        return st.session_state["active_league_id"]

    user = current_user()
    user_id = None

    if isinstance(user, dict):
        user_id = user.get("id") or user.get("user_id")
    else:
        user_id = getattr(user, "id", None)

    if not sb or not user_id:
        st.error(f"Debug: Could not resolve signed-in user_id. user={user}")
        return None

    try:
        rows = (
            sb.table("league_memberships")
            .select("league_id, role")
            .eq("user_id", user_id)
            .execute()
            .data
            or []
        )

        if not rows:
            st.error(f"Debug: No league_memberships found for user_id={user_id}")
            return None

        st.session_state["active_league_id"] = rows[0]["league_id"]
        st.session_state["role"] = rows[0].get("role")

        return rows[0]["league_id"]

    except Exception as e:
        st.error(f"Debug: League lookup failed: {e}")
        return None
# ---------- roster / caps / transactions loaders ----------
@st.cache_data(ttl=300, show_spinner=False)
def load_owners_df() -> pd.DataFrame:

    league_id = st.session_state.get("active_league_id") or st.session_state.get("import_league_id")

    if not sb or not league_id:

        return pd.DataFrame(columns=["handle", "name", "sleeper_roster_id", "sleeper_owner_id"])

    try:
        rows = (
            sb.table("league_teams")
            .select("owner_name, team_name, sleeper_roster_id, sleeper_user_id")
            .eq("league_id", league_id)
            .order("owner_name")
            .execute()
            .data
            or []
        )

        df = pd.DataFrame(rows)

        if df.empty:
            return pd.DataFrame(columns=["handle", "name", "sleeper_roster_id", "sleeper_owner_id"])

        df["name"] = df["team_name"].fillna(df["owner_name"])
        df["handle"] = df["owner_name"]
        df["sleeper_owner_id"] = df["sleeper_user_id"]

        return df[["handle", "name", "sleeper_roster_id", "sleeper_owner_id"]].drop_duplicates()

    except Exception as e:
        st.exception(e)
        return pd.DataFrame(columns=["handle", "name", "sleeper_roster_id", "sleeper_owner_id"])

def load_canonical_team_state_current(season: int, cache_epoch: int) -> dict:
    league_id = st.session_state.get("active_league_id") or st.session_state.get("import_league_id")
    return load_team_state(sb, league_id, season)

# A cash trade carries no player name, so deduping on the player alone treats
# every cash leg a team has in a season as the same row and keeps one. Amount
# and counterparty are what make two cash legs different.
DEAD_CAP_IDENTITY = [
    "owner_name",
    "player_name",
    "adjustment_type",
    "season",
    "amount",
    "counterparty_owner",
]


def _dead_cap_identity(df):
    """The dedupe key, limited to columns this frame actually has."""
    return [column for column in DEAD_CAP_IDENTITY if column in df.columns]


@st.cache_data(ttl=300, show_spinner=False)
def load_transactions(limit: int = 200) -> pd.DataFrame:
    """
    Load recent league transactions from the enriched view that the
    Admin Transactions page uses (transactions_enriched).
    """
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("transactions_enriched")
            .select("*")
            .order("ts", desc=True)   # newest first
            .limit(limit)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def load_team_activity(limit: int = 200) -> pd.DataFrame:
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
        df = pd.DataFrame(rows)
        if not df.empty:
            df["_source"] = "team_activity"
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def load_caps(league_id: str, context_generation: int) -> pd.DataFrame:
    try:
        if sb:
            from services.publication_context import published_cap_rows
            rows = published_cap_rows(sb, league_id)
            return pd.DataFrame(rows)
    except Exception:
        pass
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

def resolve_cap_for_owner(
    roster_df: pd.DataFrame,
    cap_adj_df: pd.DataFrame,
    sel_name: str,
    sel_handle: Optional[str],
    salary_cap: float,
    season: int,
) -> tuple[str, str]:
    financials = calculate_team_financials(
        roster_df.to_dict("records"),
        cap_adj_df.to_dict("records"),
        salary_cap=salary_cap,
        owner_name=sel_name,
    )
    return f"${float(financials['cap_used']):.1f}", f"${float(financials['cap_space']):.1f} space"

def draft_picks_from_state(state: dict) -> pd.DataFrame:
    """
    Normalize canonical draft-pick ownership already returned by
    read_canonical_team_state_authenticated.
    """
    rows = state.get("draft_picks", []) if isinstance(state, dict) else []

    normalized = []
    for row in rows:
        original_id = str(row.get("original_league_team_id") or "").strip()
        current_id = str(row.get("current_owner_league_team_id") or "").strip()

        original_name = (
            str(row.get("original_team_name") or "").strip()
            or str(row.get("original_owner_name") or "").strip()
            or original_id
            or "Unknown"
        )

        current_name = (
            str(row.get("current_team_name") or "").strip()
            or str(row.get("current_owner_name") or "").strip()
            or current_id
            or "Unknown"
        )

        note = (
            "Original pick"
            if original_id == current_id
            else f"Pick originally owned by {original_name}"
        )

        normalized.append(
            {
                "stable_pick_id": row.get("stable_pick_id"),
                "season": row.get("draft_year"),
                "round": row.get("round_number"),
                "current_owner": current_name,
                "original_team": original_name,
                "note": note,
                "asset_status": row.get("asset_status"),
            }
        )

    return pd.DataFrame(
        normalized,
        columns=[
            "stable_pick_id",
            "season",
            "round",
            "current_owner",
            "original_team",
            "note",
            "asset_status",
        ],
    )

def get_roster_for_owner(
    all_roster: pd.DataFrame,
    sel_handle: str | None,
    sel_name: str,
) -> pd.DataFrame:
    """
    Return the subset of roster rows for the selected owner.

    1) Prefer handle-based matches (owner / sleeper_username / owner_handle / etc.)
    2) Fall back to name-based matches, including Mekel's alias ("MekelS").
    """
    if all_roster.empty:
        return all_roster

    # Map lowercase column names -> real column names
    cols = {c.lower(): c for c in all_roster.columns}

    # 1) Handle-based match (most reliable)
    handle_candidates = [
        "owner",
        "owner_handle",
        "sleeper_username",
        "sleeper_handle",
        "team_owner_handle",
        "user_handle",
    ]
    if sel_handle:
        h = str(sel_handle).strip().lower()
        for key in handle_candidates:
            if key in cols:
                col_name = cols[key]
                col_vals = (
                    all_roster[col_name]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                )
                sub = all_roster[col_vals == h]
                if not sub.empty:
                    return sub

    # 2) Name-based match (display names / owner names)
    name_candidates = [
        "owner_name",
        "owner_display_name",  # Mekel's roster uses this
        "team_owner",
        "manager_name",
        "display_name",
        "owner_team_name",
    ]

    # Special aliases for Mekel so "Mekel Sanches" can match "MekelS"
    alias_names: list[str] = []
    if sel_name == "Mekel Sanches":
        alias_names = ["MekelS", "Mekel S", "Mekel"]

    for key in name_candidates:
        if key in cols:
            col_name = cols[key]
            col_vals = all_roster[col_name].astype(str).str.strip()

            # exact match on display name
            sub = all_roster[col_vals == sel_name]
            if not sub.empty:
                return sub

            # alias match (for Mekel)
            if alias_names:
                sub = all_roster[col_vals.isin(alias_names)]
                if not sub.empty:
                    return sub

    # No match found
    return pd.DataFrame(columns=all_roster.columns)
# ---------- load all ----------
spinner_placeholder = st.empty()
show_loading_screen(spinner_placeholder, "Loading Team Dashboard...")

active_league_id = ensure_active_league_from_user()

# Canonical league context for the Teams page.
# Keep the legacy variable name available for downstream loaders.
league_id = active_league_id

league_rules = load_league_rules(active_league_id)
salary_cap = float(league_rules.get("salary_cap", 225))

owners_df = load_owners_df()

# REMOVE the "None" owner row + any empties
if not owners_df.empty and "name" in owners_df.columns:
    owners_df = owners_df[
        owners_df["name"].notna()
        & (owners_df["name"].astype(str).str.strip().str.lower() != "none")
    ]

if owners_df.empty:
    st.error("No teams found for your account. Make sure your user is assigned to a league.")
    st.stop()

from services.publication_context import publication_generation
caps_df   = load_caps(league_id, publication_generation(sb, league_id))
from season_engine import SeasonResolver

active_season = SeasonResolver(sb).get_active_season(league_id).season
try:
    canonical_state = load_canonical_team_state_current(active_season, int(st.session_state.get("team_state_cache_epoch", 0)))
except CanonicalTeamStateError:
    spinner_placeholder.empty()
    st.error("Canonical team state could not be loaded. Roster and cap totals were not rendered.")
    st.stop()
roster_df = pd.DataFrame(state_roster(canonical_state))
tx_enriched_df = load_transactions(limit=500)
activity_df = load_team_activity(limit=500)
canonical_activity_df = pd.DataFrame(state_activity(canonical_state, 500))
tx_df = pd.DataFrame(merge_activity(
    canonical_activity_df.to_dict("records"),
    pd.concat([tx_enriched_df, activity_df], ignore_index=True).to_dict("records"),
))
cap_adj_df = pd.DataFrame(state_cap_adjustments(canonical_state))
stand_df = load_live_standings()

spinner_placeholder.empty()

# Try to find a primary-key-ish column for roster edits
ROSTER_PK_COL = None
if not roster_df.empty:
    for c in ROSTER_PK_CANDIDATES:
        if c in roster_df.columns:
            ROSTER_PK_COL = c
            break

def roster_insert(payload: dict):
    return _rest_request("POST", ROSTER_TABLE_NAME, json_body=payload)

def roster_update(pk_val, payload: dict):
    if not ROSTER_PK_COL:
        raise RuntimeError("No primary key column configured for roster edits.")
    params = {ROSTER_PK_COL: f"eq.{pk_val}"}
    return _rest_request("PATCH", ROSTER_TABLE_NAME, params=params, json_body=payload)

def roster_delete(pk_val):
    if not ROSTER_PK_COL:
        raise RuntimeError("No primary key column configured for roster edits.")
    params = {ROSTER_PK_COL: f"eq.{pk_val}"}
    return _rest_request("DELETE", ROSTER_TABLE_NAME, params=params)

# ---------- determine if current session is admin (NEW) ----------
is_admin = False
if ADMIN_KEY:
    # cache success in session so you don't keep retyping
    if st.session_state.get("teams_admin_ok"):
        is_admin = True
    else:
        entered = st.sidebar.text_input("Admin key (Teams)", type="password", key="teams_admin_key")
        if entered and entered == ADMIN_KEY:
            st.session_state["teams_admin_ok"] = True
            is_admin = True
        elif entered:
            st.sidebar.warning("Invalid admin key for Teams page.")
else:
    # no ADMIN_KEY set => everything is effectively admin
    is_admin = True


# ---------- selection ----------
team_names = sorted(owners_df["name"])

# Try to read ?team=... from the URL (set by Season Standings links)
param_team = None
try:
    # Newer Streamlit
    qp = st.query_params
    raw = qp.get("team")
    if isinstance(raw, list):
        param_team = raw[0] if raw else None
    else:
        param_team = raw
except Exception:
    try:
        # Fallback for older Streamlit
        qp = st.experimental_get_query_params()
        raw = qp.get("team")
        param_team = raw[0] if isinstance(raw, list) and raw else None
    except Exception:
        param_team = None

# If the query param matches a known display name, use it once per URL value
if (
    param_team
    and param_team in team_names
    and st.session_state.get("_teams_applied_param_team") != param_team
):
    st.session_state["team_name"] = param_team
    st.session_state["_teams_applied_param_team"] = param_team
# Otherwise fall back to first team on first load
elif "team_name" not in st.session_state:
    st.session_state["team_name"] = team_names[0]


# ---------- selected team ----------
# The team switcher (built below) writes team_name; there is no dropdown.
if st.session_state["team_name"] not in team_names:
    st.session_state["team_name"] = team_names[0]
sel_name = st.session_state["team_name"]
selected_team = owners_df[owners_df["name"].eq(sel_name)].iloc[0]
sel_handle = selected_team.get("handle")

# --- compute stats from standings (live) ---
record_txt = "0 – 0"
standing_txt = "—"
sp_val = 0          # Standing Points
ppg_val = 0.0

if not stand_df.empty:
    row = resolve_team_standings_row(
        stand_df,
        sleeper_roster_id=selected_team.get("sleeper_roster_id"),
        sleeper_owner_id=selected_team.get("sleeper_owner_id"),
        identities_by_roster_id=load_sleeper_team_identities(SLEEPER_LEAGUE_ID),
    )
    if not row.empty:
        r = row.iloc[0]
        w  = int(r.get("wins", 0) or 0)
        l  = int(r.get("losses", 0) or 0)
        record_txt = f"{w} – {l}"

        rank = int(r.get("rank", 0) or 0)
        standing_txt = _ordinal(rank)
        sp_val = int(r.get("standing_points", 0) or 0)

        ppg_val = float(r.get("ppg", 0.0) or 0.0)

# Cap (resolve after roster load)
cap_txt, cap_sub = resolve_cap_for_owner(
    roster_df,
    cap_adj_df,
    sel_name,
    sel_handle,
    salary_cap,
    active_season,
)

# ---------- trade block + draft picks data ----------
trade_rows = []
if sb and sel_handle:
    try:
        trade_rows = (
            sb.table("trade_block")
            .select("*")
            .eq("owner", sel_handle)
            .execute()
            .data
            or []
        )
    except Exception:
        trade_rows = []

picks_df = draft_picks_from_state(canonical_state)


def _sort_recent(df: pd.DataFrame) -> pd.DataFrame:
    for c in ["executed_at", "ts", "created_at"]:
        if c in df.columns:
            return df.sort_values(c, ascending=False)
    return df


# ============================================================
# TEAM DASHBOARD (one layout for phone and desktop)
# ============================================================

def _mobile_escape(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def _mobile_initials(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "T"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# Mobile roster data.
mobile_roster = get_roster_for_owner(
    roster_df,
    sel_handle,
    sel_name,
).copy()

if mobile_roster.empty and "owner_team_name" in roster_df.columns:
    mobile_roster = roster_df[
        roster_df["owner_team_name"]
        .astype(str)
        .str.strip()
        .eq(sel_name)
    ].copy()

if not mobile_roster.empty:
    mobile_roster = mobile_roster.sort_values(["pos", "player"])

mobile_roster_rows: list[str] = []

for _, r in mobile_roster.iterrows():
    mobile_pos = _mobile_escape(r.get("pos") or "—")
    mobile_name = _mobile_escape(r.get("player") or "Unknown")
    mobile_years = _mobile_escape(r.get("years") or "—")
    mobile_years_label = "yr" if str(r.get("years")).strip() == "1" else "yrs"
    mobile_salary = _mobile_escape(r.get("salary") or "—")
    mobile_designation = roster_designation(r)
    mobile_badge = (
        '<span class="teams-mobile-pos" title="Taxi Squad">T</span>'
        if mobile_designation == "taxi" else
        '<span class="teams-mobile-pos" title="Injured Reserve">IR</span>'
        if mobile_designation == "ir" else ""
    )

    mobile_roster_rows.append(
        '<div class="teams-mobile-roster-row">'
        f'<div><span class="teams-mobile-pos">{mobile_pos}</span></div>'
        f'<div class="teams-mobile-roster-name">{mobile_name}{mobile_badge}</div>'
        '<div class="teams-mobile-roster-money">'
        f'<strong>${mobile_salary}</strong>'
        f'<span>{mobile_years} {mobile_years_label}</span>'
        '</div>'
        '</div>'
    )

# Draft-pick summary: one row per season, one tag per pick.
mobile_pick_years: list[tuple[object, list[str]]] = []
mobile_pick_total = 0

if not picks_df.empty:
    mobile_picks = picks_df[
        (picks_df["current_owner"] == sel_name)
        & picks_df["season"].isin([active_season, active_season + 1, active_season + 2])
    ].copy()

    if not mobile_picks.empty:
        mobile_picks = mobile_picks.sort_values(["season", "round"])
        mobile_pick_total = len(mobile_picks)

        for season, season_rows in mobile_picks.groupby("season", sort=True):
            chips: list[str] = []
            for _, row in season_rows.iterrows():
                rnd = _mobile_escape(row.get("round", "—"))
                orig = str(row.get("original_team") or "").strip()
                note = _mobile_escape(row.get("note") or "")
                if orig and orig != sel_name:
                    via = _mobile_escape(orig.split()[0])
                    chips.append(
                        f'<span class="teams-pick-chip via" title="{note}">'
                        f'R{rnd}<em>via {via}</em></span>'
                    )
                else:
                    chips.append(f'<span class="teams-pick-chip" title="{note}">R{rnd}</span>')
            mobile_pick_years.append((season, chips))

# Activity summary.
def _mobile_norm_val(x) -> str:
    return str(x or "").strip().lower()


def _mobile_col_eq_any(
    df: pd.DataFrame,
    col: str,
    values: list[str],
) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)

    clean_values = [_mobile_norm_val(v) for v in values if v]

    return (
        df[col]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(clean_values)
        .fillna(False)
    )


mobile_match_values = [sel_name]
if sel_handle:
    mobile_match_values.append(sel_handle)

if tx_df.empty:
    mobile_activity = pd.DataFrame()
else:
    mobile_possible_cols = [
        "owner_name", "team_name", "owner", "owner_handle",
        "from_owner_name", "to_owner_name", "from_team_name", "to_team_name",
        "from_owner", "to_owner", "from_owner_handle", "to_owner_handle",
        "added_to_owner_name", "dropped_from_owner_name", "added_to_owner",
        "dropped_from_owner", "source_owner_name", "target_owner_name",
        "source_owner", "target_owner",
    ]

    mobile_mask = pd.Series(False, index=tx_df.index)
    for column in mobile_possible_cols:
        mobile_mask = mobile_mask | _mobile_col_eq_any(
            tx_df,
            column,
            mobile_match_values,
        )

    mobile_activity = _sort_recent(tx_df[mobile_mask].copy())

mobile_activity_lines: list[tuple[str, str, str, str]] = []

for _, r in mobile_activity.head(20).iterrows():
    ts_iso = r.get("created_at") or r.get("ts") or r.get("executed_at")

    try:
        ts_dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
        ts = f"{ts_dt:%b} {ts_dt.day}"
    except Exception:
        ts = str(ts_iso or "")

    raw_type = str(
        r.get("action")
        or r.get("tx_type")
        or r.get("transaction_type")
        or r.get("action_type")
        or r.get("type")
        or ""
    ).strip().lower()

    acq = str(r.get("acquisition") or "").strip().lower()

    action_map = {
        "taxi squad": "Taxi Squad",
        "taxi": "Taxi Squad",
        "taxi_removed": "Taxi Removed",
        "taxi removed": "Taxi Removed",
        "ir": "Injured Reserve",
        "injured reserve": "Injured Reserve",
        "ir_removed": "IR Removed",
        "ir removed": "IR Removed",
        "trade_block_add": "Trade Block Add",
        "trade block add": "Trade Block Add",
        "trade_block_remove": "Trade Block Remove",
        "trade block remove": "Trade Block Remove",
        "free_agent": "Sign",
        "free agent": "Sign",
        "waiver": "Sign",
        "add": "Sign",
        "drop": "Drop",
        "dropped": "Drop",
        "trade": "Trade",
    }

    action = action_map.get(raw_type)
    if not action:
        if acq in ("added", "add", "waiver", "free_agent", "free agent"):
            action = "Sign"
        elif acq in ("dropped", "drop"):
            action = "Drop"
        elif acq == "traded":
            action = "Trade"
        else:
            action = "Transaction"

    player = (
        r.get("player_name")
        or r.get("player")
        or r.get("player_display_name")
        or r.get("add_player_name")
        or r.get("drop_player_name")
        or ""
    )

    if not player or str(player).strip().isdigit():
        continue

    # The note carries trade details (what moved both ways).
    note = str(r.get("note") or "").strip()
    if note.lower() == "nan":
        note = ""
    mobile_activity_lines.append((action, str(player), ts, note))

# Dead cap summary.
mobile_dead_cap = pd.DataFrame()

if not cap_adj_df.empty:
    mobile_dead_cap = cap_adj_df[
        (
            cap_adj_df["owner_name"]
            .astype(str)
            .str.strip()
            == sel_name
        )
        & (
            cap_adj_df["adjustment_type"]
            .astype(str)
            .str.strip()
            .isin(["dropped_player_charge", "trade_carryover"])
        )
    ].copy()

if not mobile_dead_cap.empty:
    mobile_dead_cap = (
        mobile_dead_cap
        .sort_values(["season", "player_name"])
        .drop_duplicates(subset=_dead_cap_identity(mobile_dead_cap))
    )

# Page title.
st.markdown(
    '<div class="teams-block teams-mobile-page-head">'
    '<div class="teams-mobile-page-title">Teams</div>'
    '</div>',
    unsafe_allow_html=True,
)

# Team switcher: a card that opens a sheet listing every team in standings order.
mobile_identities = load_sleeper_team_identities(SLEEPER_LEAGUE_ID)
mobile_team_options: list[dict] = []

for _, owner in owners_df.iterrows():
    owner_name = str(owner.get("name") or "").strip()
    if not owner_name:
        continue

    option = {"name": owner_name, "rank": 0, "record": "—"}
    if not stand_df.empty:
        owner_row = resolve_team_standings_row(
            stand_df,
            sleeper_roster_id=owner.get("sleeper_roster_id"),
            sleeper_owner_id=owner.get("sleeper_owner_id"),
            identities_by_roster_id=mobile_identities,
        )
        if not owner_row.empty:
            o = owner_row.iloc[0]
            option["rank"] = int(o.get("rank", 0) or 0)
            option["record"] = f"{int(o.get('wins', 0) or 0)}-{int(o.get('losses', 0) or 0)}"

    _, option["cap_space"] = resolve_cap_for_owner(
        roster_df,
        cap_adj_df,
        owner_name,
        owner.get("handle"),
        salary_cap,
        active_season,
    )
    mobile_team_options.append(option)

mobile_team_options.sort(key=lambda t: (t["rank"] or 999, t["name"]))


def _pick_mobile_team(name: str):
    st.session_state["team_name"] = name
    st.session_state["teams_mobile_team_search"] = ""
    st.session_state["_teams_mobile_picked"] = True


@st.dialog("Pick a team")
def _mobile_team_sheet():
    # A pick reruns only this dialog; rerun the whole page to show the team.
    if st.session_state.pop("_teams_mobile_picked", False):
        st.rerun()

    query = st.text_input(
        "Search owners",
        key="teams_mobile_team_search",
        placeholder="Search owners",
        label_visibility="collapsed",
    ).strip().lower()
    st.markdown(
        '<div class="teams-sheet-note">In standings order</div>',
        unsafe_allow_html=True,
    )

    for i, team in enumerate(mobile_team_options):
        if query and query not in team["name"].lower():
            continue
        is_current = team["name"] == sel_name
        place = _ordinal(team["rank"]) if team["rank"] else "—"
        check = '<span class="teams-sheet-check">✓</span>' if is_current else ""
        with st.container(key=f"teams_sheet_row_{i}"):
            st.markdown(
                f'<div class="teams-sheet-row{" current" if is_current else ""}">'
                f'<div class="teams-sheet-rank">{_mobile_escape(place)}</div>'
                f'<div class="teams-sheet-avatar">{_mobile_escape(_mobile_initials(team["name"]))}</div>'
                '<div class="teams-sheet-name">'
                f'{_mobile_escape(team["name"])}'
                f'<small>{_mobile_escape(team["cap_space"])}</small>'
                '</div>'
                f'<div class="teams-sheet-record">{_mobile_escape(team["record"])}</div>'
                f'{check}'
                '</div>',
                unsafe_allow_html=True,
            )
            st.button(
                team["name"],
                key=f"teams_sheet_pick_{i}",
                on_click=_pick_mobile_team,
                args=(team["name"],),
                use_container_width=True,
            )



mobile_record = record_txt.replace(" – ", "-")

# Top row: team switcher + four stat boxes. On phone the columns stack.
with st.container(key="teams_top"):
    top_switch, top_stats = st.columns([1, 2.1], gap="small")

with top_switch:
    with st.container(key="teams_mobile_switch"):
        st.markdown(
            '<div class="teams-mobile-switch">'
            f'<div class="teams-mobile-avatar">{_mobile_escape(_mobile_initials(sel_name))}</div>'
            '<div class="teams-mobile-switch-text">'
            '<small>Viewing</small>'
            f'<strong>{_mobile_escape(sel_name)}</strong>'
            '</div>'
            '<div class="teams-mobile-switch-pill">Switch ▾</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("Switch team", key="teams_mobile_switch_btn", use_container_width=True):
            _mobile_team_sheet()

with top_stats:
    st.markdown(
        ''.join([
            '<section class="teams-block teams-mobile-card teams-mobile-summary">',
            '<div class="teams-mobile-stats">',
            '<div class="teams-mobile-stat">',
            '<div class="teams-mobile-stat-label">Record</div>',
            f'<div class="teams-mobile-stat-value gold">{_mobile_escape(mobile_record)}</div>',
            f'<div class="teams-mobile-stat-sub">{_mobile_escape(standing_txt)}</div>',
            '</div>',
            '<div class="teams-mobile-stat">',
            '<div class="teams-mobile-stat-label"><span class="long">Standing Points</span><span class="short">SP</span></div>',
            f'<div class="teams-mobile-stat-value">{sp_val}</div>',
            f'<div class="teams-mobile-stat-sub">{_mobile_escape(standing_txt)}</div>',
            '</div>',
            '<div class="teams-mobile-stat">',
            '<div class="teams-mobile-stat-label">PPG</div>',
            f'<div class="teams-mobile-stat-value">{ppg_val:.1f}</div>',
            '<div class="teams-mobile-stat-sub">Avg/game</div>',
            '</div>',
            '<div class="teams-mobile-stat">',
            '<div class="teams-mobile-stat-label">Cap</div>',
            f'<div class="teams-mobile-stat-value gold">{_mobile_escape(cap_txt)}</div>',
            f'<div class="teams-mobile-stat-sub">{_mobile_escape(cap_sub)}</div>',
            '</div>',
            '</div>',
            '</section>',
        ]),
        unsafe_allow_html=True,
    )


def _mobile_section_head(title: str, icon: str = "", right: str = "") -> str:
    icon_html = f'<span class="teams-mobile-section-icon">{icon}</span>' if icon else ""
    right_html = f'<div class="teams-mobile-count">{right}</div>' if right else ""
    return (
        '<div class="teams-mobile-section-head">'
        '<div class="teams-mobile-heading-left">'
        f'{icon_html}<div class="teams-mobile-title">{title}</div>'
        '</div>'
        f'{right_html}'
        '</div>'
    )


# Roster card.
roster_html: list[str] = [
    '<section class="teams-block teams-mobile-card">',
    _mobile_section_head("Roster", "♟", f"{len(mobile_roster)} Players"),
]
if not mobile_roster_rows:
    roster_html.append('<div class="teams-mobile-empty">No roster data for this team.</div>')
else:
    roster_html.extend(mobile_roster_rows[:8])
    if len(mobile_roster_rows) > 8:
        roster_html.extend([
            '<details class="teams-mobile-roster-details">',
            '<summary>',
            '<span class="open-label">View Full Roster ›</span>',
            '<span class="close-label">Close Full Roster ↑</span>',
            '</summary>',
            '<div class="expanded-roster">',
            ''.join(mobile_roster_rows[8:]),
            '</div>',
            '</details>',
        ])
roster_html.append('</section>')

# Trade block + draft picks: side by side on phone, stacked on desktop.
trade_body: list[str] = []
if trade_rows:
    for tb in trade_rows:
        trade_body.append(
            '<div class="teams-mobile-mini-row">'
            + _mobile_escape(tb.get("player_name") or "Player")
            + '</div>'
        )
else:
    trade_body.append('<div class="teams-mobile-empty">No players on the trade block.</div>')

pick_body: list[str] = []
if mobile_pick_years:
    for season, chips in mobile_pick_years:
        count = len(chips)
        pick_body.append(
            '<div class="teams-mobile-pick-year">'
            f'<span class="teams-pick-season">{_mobile_escape(season)}</span>'
            f'<span class="teams-pick-chips">{"".join(chips)}</span>'
            f'<span class="teams-mobile-pick-count">{count} {"Pick" if count == 1 else "Picks"}</span>'
            '</div>'
        )
else:
    pick_body.append('<div class="teams-mobile-empty">No draft picks data.</div>')

pair_html = ''.join([
    '<div class="teams-block teams-mobile-pair">',
    '<section class="teams-mobile-card">',
    _mobile_section_head("Trade Block", "⇄"),
    ''.join(trade_body),
    '</section>',
    '<section class="teams-mobile-card">',
    _mobile_section_head(
        "Draft Picks",
        "▣",
        f"{mobile_pick_total} {'Pick' if mobile_pick_total == 1 else 'Picks'}" if mobile_pick_total else "",
    ),
    ''.join(pick_body),
    '</section>',
    '</div>',
])

# Activity feed: six rows, the rest behind "View all".
def _activity_row(action: str, player: str, ts: str, note: str) -> str:
    tag = action.lower() if action in ("Sign", "Drop", "Trade") else "other"
    note_html = f'<small>{_mobile_escape(note)}</small>' if note else ""
    return (
        '<div class="teams-mobile-activity-row">'
        f'<span class="teams-activity-tag {tag}">{_mobile_escape(action)}</span>'
        f'<div class="teams-mobile-activity-main">{_mobile_escape(player)}{note_html}</div>'
        f'<div class="teams-mobile-date">{_mobile_escape(ts)}</div>'
        '</div>'
    )


activity_rows_html: list[str] = []
if mobile_activity_lines:
    activity_rows_html.extend(_activity_row(*line) for line in mobile_activity_lines[:6])
    if len(mobile_activity_lines) > 6:
        activity_rows_html.extend([
            '<details class="teams-mobile-roster-details">',
            '<summary><span class="open-label">View All ›</span>'
            '<span class="close-label">Show Less ↑</span></summary>',
            '<div class="expanded-roster">',
            ''.join(_activity_row(*line) for line in mobile_activity_lines[6:]),
            '</div>',
            '</details>',
        ])
else:
    activity_rows_html.append('<div class="teams-mobile-empty">No activity for this team yet.</div>')

dead_rows_html: list[str] = []
dead_total = 0.0
if mobile_dead_cap.empty:
    dead_rows_html.append('<div class="teams-mobile-empty">No dead cap charges.</div>')
else:
    for raw_label, amount in cap_adjustment_display_rows(mobile_dead_cap.to_dict("records")):
        if amount:
            dead_total += float(amount.lstrip("$"))
        dead_rows_html.append(
            '<div class="teams-mobile-dead-row">'
            f'<div class="teams-mobile-dead-main">{_mobile_escape(raw_label)}</div>'
            f'<div class="teams-mobile-dead-amount">{_mobile_escape(amount or "")}</div>'
            '</div>'
        )

side_html = ''.join([
    '<div class="teams-block teams-mobile-flow">',
    '<section class="teams-mobile-card">',
    _mobile_section_head("Activity Feed", "●"),
    ''.join(activity_rows_html),
    '</section>',
    '<section class="teams-mobile-card">',
    _mobile_section_head("Dead Cap", "", f"${dead_total:.2f} total" if dead_total else ""),
    ''.join(dead_rows_html),
    '</section>',
    '</div>',
])

# Body: roster | trade block, picks and the trade button | activity and dead cap.
with st.container(key="teams_body"):
    body_roster, body_mid, body_side = st.columns([1.25, 1, 1], gap="small")

with body_roster:
    st.markdown(''.join(roster_html), unsafe_allow_html=True)

with body_mid:
    st.markdown(pair_html, unsafe_allow_html=True)
    with st.container(border=True, key="teams_mobile_trade_action"):
        if st.button(
            "🔁 Send Trade Offer",
            key="mobile_trade_cta",
            use_container_width=True,
        ):
            st.session_state["trade_from_team"] = sel_name
            st.switch_page("pages/_82_Trades.py")

with body_side:
    st.markdown(side_html, unsafe_allow_html=True)
