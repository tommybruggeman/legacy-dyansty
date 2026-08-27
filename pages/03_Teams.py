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
from services.team_roster_state import (
    CanonicalTeamStateError,
    calculate_team_financials,
    dead_cap_display_rows,
    load_team_state,
    merge_activity,
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
   TEAMS — IPHONE ONLY
   Matches the compact rendered mobile concept.
   Desktop / tablet styles above remain unchanged.
   ========================================================= */

.teams-mobile-only,
.teams-desktop-row1-marker,
.teams-desktop-row2-marker,
.st-key-teams_mobile_picker,
.st-key-teams_mobile_trade_action {
  display:none !important;
}

@media (max-width:768px) {
  .block-container {
    padding-top:22px !important;
    padding-left:14px !important;
    padding-right:14px !important;
    padding-bottom:32px !important;
    max-width:100% !important;
  }

  /* Desktop Teams UI is still rendered for desktop, but removed on phone. */
  [data-testid="stHorizontalBlock"]:has(.teams-desktop-row1-marker),
  [data-testid="stHorizontalBlock"]:has(.teams-desktop-row2-marker) {
    display:none !important;
  }

  .teams-mobile-only,
  .st-key-teams_mobile_picker,
  .st-key-teams_mobile_trade_action {
    display:block !important;
  }

  /* Tighten Streamlit's vertical spacing only for the phone version. */
  .st-key-teams_mobile_picker,
  .st-key-teams_mobile_trade_action {
    margin:0 !important;
  }

  .st-key-teams_mobile_picker [data-testid="stVerticalBlock"],
  .st-key-teams_mobile_trade_action [data-testid="stVerticalBlock"] {
    gap:0 !important;
  }

  .teams-mobile-page-head {
    position:relative;
    min-height:42px;
    display:flex;
    align-items:center;
    justify-content:center;
    margin:0 0 10px 0;
  }

  .teams-mobile-page-title {
    color:var(--text);
    font-size:1.22rem;
    line-height:1;
    font-weight:900;
    letter-spacing:.01em;
    margin:0;
  }

  /* Team picker: same compact field as the mockup. */
  .st-key-teams_mobile_picker {
    margin-bottom:12px !important;
  }

  .st-key-teams_mobile_picker [data-testid="stSelectbox"] > label {
    color:rgba(255,245,231,.68) !important;
    font-size:.78rem !important;
    margin:0 0 5px 0 !important;
  }

  .st-key-teams_mobile_picker [data-baseweb="select"] > div {
    min-height:50px !important;
    background:linear-gradient(135deg,rgba(24,54,45,.96),rgba(16,39,34,.96)) !important;
    border:1px solid rgba(226,188,91,.38) !important;
    border-radius:14px !important;
    color:var(--text) !important;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.025) !important;
  }

  .st-key-teams_mobile_picker [data-baseweb="select"] div[role="combobox"],
  .st-key-teams_mobile_picker [data-baseweb="select"] div[aria-haspopup="listbox"] {
    color:var(--text) !important;
    font-size:.95rem !important;
    font-weight:700 !important;
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
  .teams-mobile-teamline {
    display:flex;
    align-items:center;
    gap:11px;
    padding:3px 0 11px;
  }

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

  .teams-mobile-team-name {
    color:var(--text);
    font-size:1.12rem;
    font-weight:900;
    line-height:1.1;
  }

  .teams-mobile-team-sub {
    color:rgba(255,245,231,.58);
    font-size:.69rem;
    margin-top:3px;
  }

  .teams-mobile-stats {
    display:grid !important;
    grid-template-columns:repeat(5,minmax(0,1fr));
    width:100%;
    border-top:1px solid rgba(226,188,91,.09);
    background:rgba(3,13,12,.17);
    border-radius:0 0 12px 12px;
  }

  .teams-mobile-stat {
    min-width:0;
    text-align:center;
    padding:10px 3px 8px;
    border-right:1px solid rgba(226,188,91,.09);
  }

  .teams-mobile-stat:last-child { border-right:0; }

  .teams-mobile-stat-label {
    color:rgba(255,245,231,.57);
    text-transform:uppercase;
    font-size:.52rem;
    letter-spacing:.015em;
    white-space:nowrap;
  }

  .teams-mobile-stat-value {
    color:var(--text);
    font-size:.90rem;
    font-weight:900;
    line-height:1.1;
    margin-top:4px;
    white-space:nowrap;
  }

  .teams-mobile-stat-value.gold { color:var(--gold); }

  .teams-mobile-stat-sub {
    color:rgba(255,245,231,.48);
    font-size:.55rem;
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
  .teams-mobile-roster-header,
  .teams-mobile-roster-row {
    display:grid;
    grid-template-columns:42px minmax(0,1fr) 42px 58px;
    gap:5px;
    align-items:center;
  }

  .teams-mobile-roster-header {
    color:rgba(255,245,231,.52);
    text-transform:uppercase;
    font-size:.54rem;
    padding:1px 0 6px;
    border-bottom:1px solid rgba(226,188,91,.10);
  }

  .teams-mobile-roster-row {
    min-height:39px;
    padding:5px 0;
    border-bottom:1px solid rgba(226,188,91,.075);
  }

  .teams-mobile-roster-row:last-child { border-bottom:0; }

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
    font-size:.78rem;
    font-weight:800;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
  }

  .teams-mobile-roster-cell {
    color:rgba(255,245,231,.78);
    font-size:.68rem;
    text-align:right;
    white-space:nowrap;
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
    margin-top:0 !important;
    margin-bottom:0 !important;
  }

  .st-key-teams_mobile_trade_action [data-testid="stVerticalBlockBorderWrapper"] {
    border:1px solid rgba(226,188,91,.22) !important;
    border-radius:15px !important;
    background:rgba(8,21,19,.72) !important;
    padding:9px !important;
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
    align-items:flex-start;
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
}

@media (max-width:390px) {
  .block-container {
    padding-left:10px !important;
    padding-right:10px !important;
  }

  .teams-mobile-card { padding:13px; border-radius:17px; }
  .teams-mobile-pair { gap:7px !important; }
  .teams-mobile-stat-label { font-size:.49rem; }
  .teams-mobile-stat-value { font-size:.83rem; }
  .teams-mobile-stat-sub { font-size:.51rem; }
  .teams-mobile-roster-name { font-size:.74rem; }
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
    Build full-season standings purely from Sleeper weekly matchups,
    normalized for this Teams page.
    """
    if not SLEEPER_LEAGUE_ID:
        return pd.DataFrame()

    latest_wk = _current_nfl_week(max_week=25)

    frames: list[pd.DataFrame] = []
    for wk in range(1, latest_wk + 1):
        df_w = _compute_week_df(SLEEPER_LEAGUE_ID, wk)
        if not df_w.empty:
            frames.append(df_w)

    if not frames:
        return pd.DataFrame()

    big = pd.concat(frames, ignore_index=True)

    agg = big.groupby("Team", as_index=False).agg(
        Standing_Points=("StandingPoints_wk", "sum"),
        PF=("Score", "sum"),
        PA=("OppScore", "sum"),
        Wins=("Win", "sum"),
        Games=("Score", "count"),
    )

    agg["Losses"] = (agg["Games"] - agg["Wins"]).clip(lower=0)
    agg["PPG"]    = (agg["PF"] / agg["Games"]).round(1)

    agg = agg.sort_values(["Standing_Points", "PF"], ascending=[False, False]).reset_index(drop=True)
    agg["Rank"] = agg.index + 1

    agg = agg.rename(columns={
        "Team": "owner_name",
        "Wins": "wins",
        "Losses": "losses",
        "PF": "pf",
        "PPG": "ppg",
        "Rank": "rank",
        "Standing_Points": "standing_points",
    })

    return agg[["owner_name", "wins", "losses", "pf", "ppg", "rank", "standing_points"]]

def match_standings_row(stand_df: pd.DataFrame, sel_name: str, sel_handle: Optional[str]) -> pd.DataFrame:
    """
    Match the selected team to a row in stand_df.

    Priority:
      1) Exact handle match (sel_handle) against owner_name / owner / handle / username columns.
      2) Exact display-name match (sel_name) against owner_name.
      3) Contains + fuzzy match on owner_name vs sel_name.
    """
    if stand_df.empty:
        return pd.DataFrame()

    from difflib import SequenceMatcher

    def _norm_local(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())

    work = stand_df.copy()

    # --- 1) Try handle-based match first ---
    if sel_handle:
        handle_cols = []
        for c in work.columns:
            lc = c.lower()
            if lc in {"owner_name", "owner", "handle", "sleeper_username", "sleeper_handle", "username", "user_name"}:
                handle_cols.append(c)

        for c in handle_cols:
            col_vals = work[c].astype(str)
            mask = col_vals.str.strip().str.casefold() == sel_handle.strip().casefold()
            hit = work[mask]
            if not hit.empty:
                return hit.iloc[:1]

    # --- 2) Exact display-name match on owner_name ---
    if "owner_name" in work.columns:
        hit = work[work["owner_name"].astype(str).str.strip() == sel_name]
        if not hit.empty:
            return hit.iloc[:1]

        # contains
        mask = work["owner_name"].astype(str).str.contains(re.escape(sel_name), case=False, na=False)
        hit = work[mask]
        if not hit.empty:
            return hit.iloc[:1]

        # --- 3) Fuzzy match on owner_name vs sel_name ---
        best_score, best_idx = 0.0, -1
        target = _norm_local(sel_name)
        for i, val in work["owner_name"].astype(str).items():
            s = SequenceMatcher(None, target, _norm_local(val)).ratio()
            if s > best_score:
                best_score, best_idx = s, i

        if best_score >= 0.82 and best_idx >= 0:
            return work.loc[[best_idx]]

    # No match
    return pd.DataFrame()
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

        return pd.DataFrame(columns=["handle", "name"])

    try:
        rows = (
            sb.table("league_teams")
            .select("owner_name, team_name")
            .eq("league_id", league_id)
            .order("owner_name")
            .execute()
            .data
            or []
        )

        df = pd.DataFrame(rows)

        if df.empty:
            return pd.DataFrame(columns=["handle", "name"])

        df["name"] = df["team_name"].fillna(df["owner_name"])
        df["handle"] = df["owner_name"]

        return df[["handle", "name"]].dropna().drop_duplicates()

    except Exception as e:
        st.exception(e)
        return pd.DataFrame(columns=["handle", "name"])

@st.cache_data(ttl=300, show_spinner=False)
def load_canonical_team_state_current(season: int, cache_epoch: int) -> dict:
    league_id = st.session_state.get("active_league_id") or st.session_state.get("import_league_id")
    return load_team_state(sb, league_id, season)

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

@st.cache_data(ttl=300, show_spinner=False)
def load_draft_picks() -> pd.DataFrame:
    try:
        if sb:
            rows = (
                sb.table("draft_picks")
                .select("*").order("season", desc=False).order("round", desc=False)
                .execute().data or []
            )
            return pd.DataFrame(rows)
    except Exception:
        pass
    return pd.DataFrame()

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


# --- Normalize handles so owners_df.handle matches stand_df.owner_name ---
# This fixes cases like Nando: owners_df has "Nandio", Sleeper standings use "nandorio".
if not stand_df.empty:
    from difflib import SequenceMatcher

    stand_handles = stand_df["owner_name"].astype(str).tolist()

    corrected = {}
    for raw_h in owners_df["handle"].astype(str):
        h = raw_h.strip()

        # Exact match: keep as-is
        if h in stand_handles:
            corrected[h] = h
            continue

        # Fuzzy match handle -> any standings handle
        best_score, best_handle = 0.0, None
        for s in stand_handles:
            score = SequenceMatcher(None, h.lower(), s.lower()).ratio()
            if score > best_score:
                best_score, best_handle = score, s

        # "Nandio" vs "nandorio" is ~0.85, so 0.75 catches it safely
        if best_score >= 0.75 and best_handle:
            corrected[h] = best_handle

    # Apply corrections back into owners_df.handle
    owners_df["handle"] = (
        owners_df["handle"]
        .astype(str)
        .map(lambda h: corrected.get(h.strip(), h.strip()))
    )

# Now that handles are corrected to match stand_df, rebuild the maps
owner_map = dict(zip(owners_df["handle"], owners_df["name"]))
name_to_handle = {n: h for h, n in zip(owners_df["handle"], owners_df["name"])}

# Attach Sleeper handles from owners_df to standings so we can match by handle,
# not just by display name. This helps for Nando / Mekel where names may differ.
if not stand_df.empty and not owners_df.empty:
    try:
        tmp = owners_df.rename(columns={"name": "owner_name"})
        # left join: keep all standings rows, add "handle" when we find a match
        stand_df = stand_df.merge(
            tmp[["owner_name", "handle"]],
            on="owner_name",
            how="left",
        )
    except Exception:
        # if merge fails for any reason, just keep the original standings
        pass

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

# If the query param matches a known display name, use it
if param_team and param_team in team_names:
    st.session_state["team_name"] = param_team
# Otherwise fall back to first team on first load
elif "team_name" not in st.session_state:
    st.session_state["team_name"] = team_names[0]

# ---------- ROW 1 ----------
st.markdown('<div class="row1">', unsafe_allow_html=True)
tc, c_record, c_pf, c_ppg, c_cap = st.columns([2.6, 1, 1, 1, 1])

with tc:
    st.markdown(
        '<div class="teams-desktop-row1-marker"></div>',
        unsafe_allow_html=True,
    )

    # Single real widget only (no extra HTML wrapper)
    sel_name = st.selectbox(
        "Team",
        options=team_names,
        index=team_names.index(st.session_state["team_name"]),
        key="owner_picker",
    )
    st.session_state["team_name"] = sel_name
    sel_handle = name_to_handle.get(sel_name)

# --- compute stats from standings (live) ---
record_txt = "0 – 0"
standing_txt = "—"
sp_val = 0          # Standing Points
ppg_val = 0.0

if not stand_df.empty:
    row = match_standings_row(stand_df, sel_name, sel_handle)
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

# Record
with c_record:
    st.markdown(
        f"""
        <div class="stat-card">
          <div class="stat-title">Record</div>
          <div class="stat-value">{record_txt}</div>
          <div class="stat-sub">{standing_txt}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Standing Points (with rank)
with c_pf:
    st.markdown(
        f"""
        <div class="stat-card">
          <div class="stat-title">Standing Points</div>
          <div class="stat-value">{sp_val}</div>
          <div class="stat-sub">{standing_txt}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# PPG
with c_ppg:
    st.markdown(
        f"""
        <div class="stat-card">
          <div class="stat-title">PPG</div>
          <div class="stat-value">{ppg_val:.1f}</div>
          <div class="stat-sub">Avg / game</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Cap
with c_cap:
    st.markdown(
        f"""
        <div class="stat-card">
          <div class="stat-title">Cap</div>
          <div class="stat-value">{cap_txt}</div>
          <div class="stat-sub">{cap_sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('</div>', unsafe_allow_html=True)

# ---------- ROW 2 ----------
left_col, mid_col, right_col = st.columns([1.2, 0.6, 0.7])

# 1) ROSTER
with left_col:
    st.markdown(
        '<div class="teams-desktop-row2-marker"></div>',
        unsafe_allow_html=True,
    )

    mine = pd.DataFrame()
    if not roster_df.empty:
        # use the rosters_enriched data already loaded
        mine = get_roster_for_owner(roster_df, sel_handle, sel_name).copy()

        # fallback by display name if needed
        if mine.empty and "owner_team_name" in roster_df.columns:
            mine = roster_df[
                roster_df["owner_team_name"]
                .astype(str)
                .str.strip()
                .eq(sel_name)
            ].copy()

        # 2) If that somehow fails and we have an owner_team_name column,
        #    fall back to display-name match.
        if mine.empty and "owner_team_name" in roster_df.columns:
            mine = roster_df[
                roster_df["owner_team_name"]
                .astype(str)
                .str.strip()
                .eq(sel_name)
            ].copy()

    html = [
        '<div class="roster-panel" style="max-height:460px; overflow-y:auto;">',
        '<h3>Roster</h3>',
        (
            '<div style="display:flex;justify-content:space-between;gap:8px;'
            'padding:4px 0 6px;border-bottom:1px solid rgba(202,167,74,.15);'
            'font-size:.68rem;text-transform:uppercase;opacity:.65;">'
            '<span style="width:42px;">Pos</span>'
            '<span style="flex:1;">Player</span>'
            '<span style="width:70px;text-align:right;">Years</span>'
            '<span style="width:80px;text-align:right;">Salary</span>'
            '</div>'
        )
    ]

    if mine.empty:
        html.append(
            '<p style="font-size:.7rem; opacity:.6; margin-top:6px;">'
            'No roster data for this team.</p>'
        )
    else:
        mine = mine.sort_values(["pos", "player"])
        for _, r in mine.iterrows():
            pos = r.get("pos", "—")
            name = r.get("player", "Unknown")
            yrs = r.get("years", "") or "—"
            sal = r.get("salary", "") or "—"
            html.append(
                (
                    '<div class="roster-row" style="gap:0;">'
                    f'<div style="width:42px;"><span class="pos-pill">{pos}</span></div>'
                    f'<div style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"><span class="player-name">{name}</span></div>'
                    f'<div style="width:70px;text-align:right;font-size:.7rem;">{yrs}</div>'
                    f'<div style="width:80px;text-align:right;font-size:.7rem;">${sal}</div>'
                    '</div>'
                )
            )

    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)

    # ---------- Admin inline editor for this roster (NEW) ----------
    if False and is_admin and not mine.empty:
        st.markdown("#### Admin — Edit Roster")
        if not ROSTER_PK_COL:
            st.info(
                "Cannot save changes yet: `rosters_enriched` has no obvious primary key "
                "(tried id / row_id / roster_id). Add one or adjust ROSTER_PK_CANDIDATES."
            )
        else:
            st.caption(
                "Edit Pos / Player / Years / Salary, mark **Drop** to remove. "
                "Add a new blank row at the bottom to add a player to this team."
            )

            # Build editable view: PK + editable fields
            editable_cols: list[str] = []
            for c in ["pos", "player", "years", "salary"]:
                if c in mine.columns:
                    editable_cols.append(c)

            admin_df = mine[[ROSTER_PK_COL] + editable_cols].copy()
            admin_df.rename(columns={ROSTER_PK_COL: "_pk"}, inplace=True)
            admin_df["Drop"] = False  # admin-only flag

            edited = st.data_editor(
                admin_df,
                num_rows="dynamic",
                key=f"teams_admin_editor_{sel_name}",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "_pk": st.column_config.Column("ID", disabled=True),
                },
            )

            if st.button("💾 Save roster changes", key=f"save_roster_{sel_name}"):
                errors: list[str] = []
                updates = inserts = deletes = 0

                for _, row in edited.iterrows():
                    pk = row.get("_pk")
                    # Normalise pk value
                    if pd.isna(pk):
                        pk = None

                    drop_flag = bool(row.get("Drop") or False)

                    # Build payload using DB column names
                    payload: dict[str, object] = {}
                    for col in editable_cols:
                        db_col = ROSTER_WRITE_COL_MAP.get(col, col)
                        val = row.get(col)
                        payload[db_col] = val if val not in ("", None) else None

                    try:
                        if pk is None:
                            # New row => INSERT, attach this team name
                            if any(v is not None for v in payload.values()):
                                payload["owner_name"] = sel_name
                                payload.setdefault("team_name", sel_name)
                                roster_insert(payload)
                                inserts += 1
                        else:
                            if drop_flag:
                                # Delete this row entirely
                                roster_delete(pk)
                                deletes += 1
                            else:
                                # Update existing row
                                roster_update(pk, payload)
                                updates += 1

                    except Exception as e:
                        errors.append(str(e))

                if errors:
                    st.error(
                        "Some roster updates failed:\n\n"
                        + "\n".join(f"- {msg}" for msg in errors)
                    )
                else:
                    st.success(
                        f"Roster saved "
                        f"(updated: {updates}, added: {inserts}, dropped: {deletes}). "
                        "Reloading…"
                    )
                    st.experimental_rerun()

# 2) MIDDLE STACK (trade + picks)
with mid_col:
    st.markdown('<div class="middle-stack">', unsafe_allow_html=True)

    # --- Trade Block ---
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

    with st.container(border=True):
        st.markdown("### Trade Block")

        if st.button("🔁 Send Trade Offer", key="trade_cta"):
            st.session_state["trade_from_team"] = sel_name
            st.switch_page("pages/_82_Trades.py")

        if trade_rows:
            for tb in trade_rows:
                pname = tb.get("player_name") or "Player"
                st.markdown(f"• **{pname}**")
        else:
            st.caption("No players on the trade block.")

    # --- Draft Picks panel ---
    picks_df = load_draft_picks() if 'picks_df' not in globals() else picks_df
    picks_html = ['<div class="panel"><h3>Draft Picks</h3>']
    if picks_df.empty:
        picks_html.append('<p style="font-size:.7rem;opacity:.65;">No draft picks data.</p>')
    else:
        minep = picks_df[picks_df["current_owner"] == sel_name].copy()
        if minep.empty:
            picks_html.append('<p style="font-size:.7rem;opacity:.65;">No draft picks data.</p>')
        else:
            minep = minep.sort_values(["season", "round"])
            picks_html.append('<ul style="margin:4px 0 0 16px; padding:0; list-style:disc;">')
            for _, row in minep.iterrows():
                season = row.get("season", "—")
                rnd    = row.get("round", "—")
                orig   = row.get("original_team", "")
                note   = row.get("note") or ""
                if orig and orig != sel_name:
                    line = f"{season} — Round {rnd} (via {orig})"
                else:
                    line = f"{season} — Round {rnd}"
                if note:
                    line += f" — {note}"
                picks_html.append(f"<li style='margin-bottom:2px;font-size:.75rem;'>{line}</li>")
            picks_html.append("</ul>")
    picks_html.append("</div>")
    st.markdown("".join(picks_html), unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)  # close .middle-stack

def _sort_recent(df: pd.DataFrame) -> pd.DataFrame:
    for c in ["executed_at", "ts", "created_at"]:
        if c in df.columns:
            return df.sort_values(c, ascending=False)
    return df

# 3) ACTIVITY FEED
with right_col:
    def _norm_val(x) -> str:
        return str(x or "").strip().lower()

    def _col_eq_any(df: pd.DataFrame, col: str, values: list[str]) -> pd.Series:
        if col not in df.columns:
            return pd.Series(False, index=df.index)

        clean_values = [_norm_val(v) for v in values if v]
        return df[col].astype(str).str.strip().str.lower().isin(clean_values).fillna(False)

    team_match_values = [sel_name]
    if sel_handle:
        team_match_values.append(sel_handle)

    if tx_df.empty:
        mine = pd.DataFrame()
    else:
        possible_cols = [
            "owner_name",
            "team_name",
            "owner",
            "owner_handle",
            "from_owner_name",
            "to_owner_name",
            "from_team_name",
            "to_team_name",
            "from_owner",
            "to_owner",
            "from_owner_handle",
            "to_owner_handle",
            "added_to_owner_name",
            "dropped_from_owner_name",
            "added_to_owner",
            "dropped_from_owner",
            "source_owner_name",
            "target_owner_name",
            "source_owner",
            "target_owner",
        ]

        mask = pd.Series(False, index=tx_df.index)
        for c in possible_cols:
            mask = mask | _col_eq_any(tx_df, c, team_match_values)

        mine = tx_df[mask].copy()
        mine = _sort_recent(mine)

    parts = []
    parts.append('<div class="panel"><h3>Activity Feed</h3><div class="activity-panel">')

    if mine.empty:
        parts.append('<div class="activity-empty">No activity for this team yet.</div>')
    else:
        for _, r in mine.iterrows():
            ts_iso = r.get("created_at") or r.get("ts") or r.get("executed_at")
            try:
                ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00")).strftime("%Y-%m-%d")
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

            parts.append(
                f'<div class="activity-item">'
                f'<strong>{action}</strong> · {player}'
                f'<small>{ts}</small>'
                f'</div>'
            )

    parts.append("</div></div>")
    st.markdown("".join(parts), unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    dead_cap = pd.DataFrame()

    if not cap_adj_df.empty:
        dead_cap = cap_adj_df[
            (cap_adj_df["owner_name"].astype(str).str.strip() == sel_name)
            & (
                cap_adj_df["adjustment_type"]
                .astype(str)
                .str.strip()
                .eq("dropped_player_charge")
            )
        ].copy()

    dead_parts = []
    dead_parts.append('<div class="panel"><h3>Dead Cap</h3><div class="activity-panel">')

    if dead_cap.empty:
        dead_parts.append('<div class="activity-empty">No dead cap charges.</div>')
    else:
        dead_cap = (
            dead_cap
            .sort_values(["season", "player_name"])
            .drop_duplicates(
                subset=[
                    "owner_name",
                    "player_name",
                    "adjustment_type",
                    "season",
                ]
            )
        )

        for player, amount in dead_cap_display_rows(dead_cap.to_dict("records")):
            dead_parts.append(
                f'<div class="activity-item">'
                f'<strong>{player}</strong> · ${amount:.2f}'
                f'</div>'
            )

    dead_parts.append("</div></div>")
    st.markdown("".join(dead_parts), unsafe_allow_html=True)

# ============================================================
# IPHONE-ONLY TEAM DASHBOARD
# Desktop / tablet layout above is intentionally unchanged.
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
    mobile_salary = _mobile_escape(r.get("salary") or "—")

    mobile_roster_rows.append(
        '<div class="teams-mobile-roster-row">'
        f'<div><span class="teams-mobile-pos">{mobile_pos}</span></div>'
        f'<div class="teams-mobile-roster-name">{mobile_name}</div>'
        f'<div class="teams-mobile-roster-cell">{mobile_years}</div>'
        f'<div class="teams-mobile-roster-cell">${mobile_salary}</div>'
        '</div>'
    )

# Draft-pick summary.
mobile_pick_counts: list[tuple[object, int]] = []
mobile_pick_lines: list[str] = []

if not picks_df.empty:
    mobile_picks = picks_df[picks_df["current_owner"] == sel_name].copy()

    if not mobile_picks.empty:
        mobile_picks = mobile_picks.sort_values(["season", "round"])

        for season, season_rows in mobile_picks.groupby("season", sort=True):
            mobile_pick_counts.append((season, len(season_rows)))

        for _, row in mobile_picks.iterrows():
            season = row.get("season", "—")
            rnd = row.get("round", "—")
            orig = row.get("original_team", "")
            note = row.get("note") or ""

            if orig and orig != sel_name:
                line = f"{season} — Round {rnd} (via {orig})"
            else:
                line = f"{season} — Round {rnd}"

            if note:
                line += f" — {note}"

            mobile_pick_lines.append(_mobile_escape(line))

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

mobile_activity_lines: list[tuple[str, str, str]] = []

for _, r in mobile_activity.head(6).iterrows():
    ts_iso = r.get("created_at") or r.get("ts") or r.get("executed_at")

    try:
        ts = datetime.fromisoformat(
            str(ts_iso).replace("Z", "+00:00")
        ).strftime("%Y-%m-%d")
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

    mobile_activity_lines.append((action, str(player), ts))

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
            .eq("dropped_player_charge")
        )
    ].copy()

if not mobile_dead_cap.empty:
    mobile_dead_cap = (
        mobile_dead_cap
        .sort_values(["season", "player_name"])
        .drop_duplicates(
            subset=["owner_name", "player_name", "adjustment_type", "season"]
        )
    )

# Phone page title.
st.markdown(
    '<div class="teams-mobile-only teams-mobile-page-head">'
    '<div class="teams-mobile-page-title">Teams</div>'
    '</div>',
    unsafe_allow_html=True,
)

# Phone team picker. The desktop picker remains untouched and hidden only on phone.
with st.container(key="teams_mobile_picker"):
    mobile_selected_team = st.selectbox(
        "Team",
        options=team_names,
        index=team_names.index(sel_name),
        key="mobile_owner_picker",
    )

    if mobile_selected_team != sel_name:
        st.session_state["team_name"] = mobile_selected_team

# Build the compact top portion in ONE markdown block so Streamlit cannot
# insert large gaps between the summary, roster, and side-by-side cards.
mobile_top_html: list[str] = ['<div class="teams-mobile-only teams-mobile-flow">']

# Summary card.
initials = _mobile_escape(_mobile_initials(sel_name))
name_html = _mobile_escape(sel_name)

mobile_top_html.extend([
    '<section class="teams-mobile-card">',
    '<div class="teams-mobile-teamline">',
    f'<div class="teams-mobile-avatar">{initials}</div>',
    '<div>',
    f'<div class="teams-mobile-team-name">{name_html}</div>',
    '<div class="teams-mobile-team-sub">Team Dashboard</div>',
    '</div>',
    '</div>',
    '<div class="teams-mobile-stats">',
    '<div class="teams-mobile-stat">',
    '<div class="teams-mobile-stat-label">Record</div>',
    f'<div class="teams-mobile-stat-value gold">{_mobile_escape(record_txt)}</div>',
    f'<div class="teams-mobile-stat-sub">{_mobile_escape(standing_txt)}</div>',
    '</div>',
    '<div class="teams-mobile-stat">',
    '<div class="teams-mobile-stat-label">SP</div>',
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
    '<div class="teams-mobile-stat">',
    '<div class="teams-mobile-stat-label">Rank</div>',
    f'<div class="teams-mobile-stat-value">{_mobile_escape(standing_txt)}</div>',
    '<div class="teams-mobile-stat-sub">League</div>',
    '</div>',
    '</div>',
    '</section>',
])

# Roster card.
mobile_top_html.extend([
    '<section class="teams-mobile-card">',
    '<div class="teams-mobile-section-head">',
    '<div class="teams-mobile-heading-left">',
    '<span class="teams-mobile-section-icon">♟</span>',
    '<div class="teams-mobile-title">Roster</div>',
    '</div>',
    f'<div class="teams-mobile-count">{len(mobile_roster)} Players</div>',
    '</div>',
    '<div class="teams-mobile-roster-header">',
    '<div>Pos</div><div>Player</div>',
    '<div style="text-align:right;">Yrs</div>',
    '<div style="text-align:right;">Salary</div>',
    '</div>',
])

if not mobile_roster_rows:
    mobile_top_html.append(
        '<div class="teams-mobile-empty" style="padding-top:8px;">'
        'No roster data for this team.</div>'
    )
else:
    mobile_top_html.extend(mobile_roster_rows[:5])

    if len(mobile_roster_rows) > 5:
        mobile_top_html.extend([
            '<details class="teams-mobile-roster-details">',
            '<summary>',
            '<span class="open-label">View Full Roster ›</span>',
            '<span class="close-label">Close Full Roster ↑</span>',
            '</summary>',
            '<div class="expanded-roster">',
            ''.join(mobile_roster_rows[5:]),
            '</div>',
            '</details>',
        ])

mobile_top_html.append('</section>')

# Trade block and draft picks — intentionally SIDE BY SIDE.
trade_body: list[str] = []
if trade_rows:
    for tb in trade_rows:
        trade_body.append(
            '<div class="teams-mobile-mini-row">'
            + _mobile_escape(tb.get("player_name") or "Player")
            + '</div>'
        )
else:
    trade_body.append(
        '<div class="teams-mobile-empty">No players on the trade block.</div>'
    )

pick_body: list[str] = []
if mobile_pick_counts:
    for season, count in mobile_pick_counts[:3]:
        label = "Pick" if count == 1 else "Picks"
        pick_body.append(
            '<div class="teams-mobile-pick-year">'
            f'<span>{_mobile_escape(season)}</span>'
            f'<span class="teams-mobile-pick-count">{count} {label}</span>'
            '</div>'
        )
else:
    pick_body.append('<div class="teams-mobile-empty">No draft picks data.</div>')

if mobile_pick_lines:
    pick_body.extend([
        '<details class="teams-mobile-picks-details">',
        '<summary>View all ›</summary>',
        '<div class="teams-mobile-picks-full">',
        ''.join(
            f'<div class="teams-mobile-pick-line">{line}</div>'
            for line in mobile_pick_lines
        ),
        '</div>',
        '</details>',
    ])

mobile_top_html.extend([
    '<div class="teams-mobile-pair">',
    '<section class="teams-mobile-card">',
    '<div class="teams-mobile-section-head">',
    '<div class="teams-mobile-heading-left">',
    '<span class="teams-mobile-section-icon">⇄</span>',
    '<div class="teams-mobile-title">Trade Block</div>',
    '</div>',
    '</div>',
    ''.join(trade_body),
    '</section>',
    '<section class="teams-mobile-card">',
    '<div class="teams-mobile-section-head">',
    '<div class="teams-mobile-heading-left">',
    '<span class="teams-mobile-section-icon">▣</span>',
    '<div class="teams-mobile-title">Draft Picks</div>',
    '</div>',
    '</div>',
    ''.join(pick_body),
    '</section>',
    '</div>',
    '</div>',
])

st.markdown(
    ''.join(mobile_top_html),
    unsafe_allow_html=True,
)

# Functional trade button, immediately below the side-by-side cards.
with st.container(border=True, key="teams_mobile_trade_action"):
    if st.button(
        "🔁 Send Trade Offer",
        key="mobile_trade_cta",
        use_container_width=True,
    ):
        st.session_state["trade_from_team"] = sel_name
        st.switch_page("pages/_82_Trades.py")

# Activity + Dead Cap in one tight second block.
mobile_bottom_html: list[str] = ['<div class="teams-mobile-only teams-mobile-flow">']

activity_rows_html: list[str] = []
if mobile_activity_lines:
    for action, player, ts in mobile_activity_lines[:2]:
        activity_rows_html.append(
            '<div class="teams-mobile-activity-row">'
            '<div class="teams-mobile-activity-main">'
            f'<strong>{_mobile_escape(action)}</strong> · {_mobile_escape(player)}'
            '</div>'
            f'<div class="teams-mobile-date">{_mobile_escape(ts)}</div>'
            '</div>'
        )
else:
    activity_rows_html.append(
        '<div class="teams-mobile-empty">No activity for this team yet.</div>'
    )

mobile_bottom_html.extend([
    '<section class="teams-mobile-card">',
    '<div class="teams-mobile-section-head">',
    '<div class="teams-mobile-heading-left">',
    '<span class="teams-mobile-section-icon">●</span>',
    '<div class="teams-mobile-title">Activity Feed</div>',
    '</div>',
    '</div>',
    ''.join(activity_rows_html),
    '</section>',
])

dead_rows_html: list[str] = []
if mobile_dead_cap.empty:
    dead_rows_html.append('<div class="teams-mobile-empty">No dead cap charges.</div>')
else:
    for raw_player, amount in dead_cap_display_rows(mobile_dead_cap.to_dict("records")):
        player = _mobile_escape(raw_player)
        dead_rows_html.append(
            '<div class="teams-mobile-dead-row">'
            '<div class="teams-mobile-dead-main">'
            f'<strong>{player}</strong> · '
            f'<span class="teams-mobile-dead-amount">${amount:.2f}</span>'
            '</div>'
            '</div>'
        )

mobile_bottom_html.extend([
    '<section class="teams-mobile-card">',
    '<div class="teams-mobile-section-head">',
    '<div class="teams-mobile-heading-left">',
    '<div class="teams-mobile-title">Dead Cap</div>',
    '</div>',
    '</div>',
    ''.join(dead_rows_html),
    '</section>',
    '</div>',
])

st.markdown(
    ''.join(mobile_bottom_html),
    unsafe_allow_html=True,
)
