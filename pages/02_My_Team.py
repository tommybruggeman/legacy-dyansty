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
from services.my_team_context import resolve_my_team

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
        self._order = None
        self._limit = None

    def select(self, cols="*"):
        self._select = cols
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
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
  max-height:570px;
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

@media (max-width: 768px) {
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
    margin-top: 8px;
  }

  .mobile-roster-details summary {
    list-style: none;
    cursor: pointer;
    color: var(--gold);
    font-size: .82rem;
    font-weight: 850;
    text-align: center;
    padding: 12px 0 4px 0;
    min-height: 44px;
  }

  .mobile-roster-details summary::-webkit-details-marker {
    display: none;
  }

  .mobile-roster-details summary::after {
    content: " ›";
  }

  .mobile-roster-details[open] summary::after {
    content: " ↑";
  }

  .mobile-roster-details .expanded-roster {
    margin-top: 6px;
    border-top: 1px solid var(--rule);
    padding-top: 4px;
  }

  /* Mobile controls: larger touch targets without changing desktop controls. */
  [data-testid="stButton"] button {
    min-height: 44px;
  }

  [data-testid="stTextInput"] input,
  [data-testid="stTextArea"] textarea,
  [data-testid="stSelectbox"] [data-baseweb="select"] {
    min-height: 46px;
    font-size: 16px !important;
  }

  /* Hover-only cap tooltip is not useful on touch screens. */
  .cap-tooltip {
    display: none !important;
  }


  /* ======================================================
     COMPACT TAXI SQUAD / IR — PHONE ONLY
     ====================================================== */

  .taxi-ir-marker {
    display: none;
  }

  /* Tighten only the bordered container containing Taxi / IR */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker)
  [data-testid="stVerticalBlock"] {
    gap: .45rem !important;
  }

  /* Smaller Taxi / IR heading */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker) h3 {
    font-size: 1.45rem !important;
    line-height: 1.15 !important;
    margin-top: 0 !important;
    margin-bottom: .2rem !important;
  }

  /* Current Designations heading, if one exists */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker) h4 {
    font-size: .95rem !important;
    line-height: 1.2 !important;
    margin-top: .25rem !important;
    margin-bottom: .15rem !important;
  }

  /* Reduce space around the Player + Designation selects */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker)
  [data-testid="stSelectbox"] {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
  }

  /* Smaller field labels */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker)
  [data-testid="stSelectbox"] label {
    font-size: .78rem !important;
    margin-bottom: 2px !important;
  }

  /* Shorter select boxes */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker)
  [data-baseweb="select"] {
    min-height: 42px !important;
  }

  /* Compact cap-adjustment line */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker)
  [data-testid="stCaptionContainer"] {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    font-size: .72rem !important;
  }

  /* Compact Apply / Remove buttons */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker)
  [data-testid="stButton"] button {
    min-height: 40px !important;
    padding-top: 5px !important;
    padding-bottom: 5px !important;
  }

  /* Reduce separator spacing inside Taxi / IR */
  [data-testid="stVerticalBlockBorderWrapper"]:has(.taxi-ir-marker) hr {
    margin-top: .35rem !important;
    margin-bottom: .35rem !important;
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
@st.cache_data(ttl=300, show_spinner=False)
def load_roster(league_id: str) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    rows = (
        sb.table("contracts")
        .select(
            "id, league_id, owner_name, player_name, player_position, "
            "contract_years_left, contract_total_years, salary, sleeper_player_id, is_rookie"
        )
        .eq("league_id", league_id)
        .order("player_position")
        .order("player_name")
        .execute()
        .data
        or []
    )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    return df.rename(
        columns={
            "owner_name": "owner",
            "player_name": "player",
            "player_position": "pos",
            "contract_years_left": "years",
            "sleeper_player_id": "sleeper_id",
        }
    )

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
def load_cap_adjustments(
    league_id: str,
    owner_name: str,
    season: int,
) -> pd.DataFrame:
    if not sb:
        return pd.DataFrame()

    try:
        rows = (
            sb.table("cap_adjustments")
            .select("*")
            .eq("league_id", league_id)
            .eq("owner_name", owner_name)
            .eq("season", season)
            .execute()
            .data
            or []
        )
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

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


def match_owner_row(df: pd.DataFrame, owner_name: str) -> pd.DataFrame:
    if df.empty or not owner_name:
        return pd.DataFrame()

    name_col = "owner" if "owner" in df.columns else "Team" if "Team" in df.columns else None

    if not name_col:
        return pd.DataFrame()

    exact = df[df[name_col].astype(str).str.lower().eq(str(owner_name).lower())]
    if not exact.empty:
        return exact.iloc[:1]

    contains = df[df[name_col].astype(str).str.contains(str(owner_name), case=False, na=False)]
    if not contains.empty:
        return contains.iloc[:1]

    return pd.DataFrame()

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

roster_df = load_roster(league_id)
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

trade_df = load_trade_block(league_id, owner_name)
tick("after load_trade_block")

from season_engine import SeasonResolver

active_season = SeasonResolver(auth_client()).get_active_season(league_id).season
cap_adj_df = load_cap_adjustments(league_id, owner_name, active_season)
tick("after load_cap_adjustments")

stand_df = load_cached_standings(league_id)
tick("after load_cached_standings")

spinner_placeholder.empty()

my_roster = pd.DataFrame()
if not roster_df.empty:
    my_roster = roster_df[
        roster_df["owner"].astype(str).str.strip().str.lower().eq(str(owner_name).strip().lower())
    ].copy()

# ---------- metrics ----------
record_txt = "0 – 0"
standing_txt = "—"
standing_points = 0
ppg = 0.0

row = match_owner_row(stand_df, owner_name)
if not row.empty:
    r = row.iloc[0]
    record_txt = f"{int(r.get('wins', 0))} – {int(r.get('losses', 0))}"
    standing_points = int(r.get("standing_points", 0) or 0)
    ppg = float(r.get("ppg", 0) or 0)
    standing_txt = ordinal(int(r.get("rank", 0) or 0))


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

cap_used = (
    active_salary
    + trade_carryover
    + drop_charge
    + taxi_adjustment
    + ir_adjustment
    + manual_adjustment
)

cap_space = salary_cap - cap_used

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
    ("Record", record_txt, standing_txt),
    ("Standing Points", standing_points, standing_txt),
    ("PPG", f"{ppg:.1f}", "Avg / game"),
    ("Cap Used", cap_txt, f"${cap_space:.1f} space"),
]

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
    <div><strong>Dropped:</strong><span>${drop_charge:.2f}</span></div>
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

# Phone only: compact 2 x 2 + full-width roster metric.
st.markdown(
    f"""
<div class="mobile-metrics-grid">
  <div class="card">
    <div class="metric-title">Record</div>
    <div class="metric-value">{record_txt}</div>
    <div class="metric-sub">{standing_txt}</div>
  </div>

  <div class="card">
    <div class="metric-title">Standing Points</div>
    <div class="metric-value">{standing_points}</div>
    <div class="metric-sub">{standing_txt}</div>
  </div>

  <div class="card">
    <div class="metric-title">PPG</div>
    <div class="metric-value">{ppg:.1f}</div>
    <div class="metric-sub">Avg / game</div>
  </div>

  <div class="card">
    <div class="metric-title">Cap Used</div>
    <div class="metric-value">{cap_txt}</div>
    <div class="metric-sub">${cap_space:.1f} space</div>
  </div>

</div>
""",
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# ---------- main layout ----------
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

            player_cap_rows = pd.DataFrame()

            if not cap_adj_df.empty:
                player_cap_rows = cap_adj_df[
                    cap_adj_df["player_name"]
                    .astype(str)
                    .str.lower()
                    .eq(str(player).lower())
                ]

            if not player_cap_rows.empty:
                if any(
                    player_cap_rows["adjustment_type"]
                    .astype(str)
                    .eq("taxi_adjustment")
                ):
                    badges.append(
                        '<span class="roster-badge roster-badge-action" title="Taxi Squad">T</span>'
                    )

                if any(
                    player_cap_rows["adjustment_type"]
                    .astype(str)
                    .eq("ir_adjustment")
                ):
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
  <summary>View Full Roster</summary>
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
with right:
    with st.container(border=True):
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
    st.markdown("<br>", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(
            '<div class="taxi-ir-marker"></div>',
            unsafe_allow_html=True,
        )
        st.markdown("### Taxi Squad / IR")
        current_designations = pd.DataFrame()

        if not cap_adj_df.empty:
            current_designations = cap_adj_df[
                cap_adj_df["adjustment_type"]
                .astype(str)
                .isin(["taxi_adjustment", "ir_adjustment"])
            ]

        if not current_designations.empty:
            st.markdown("#### Current Designations")

            for _, d in current_designations.iterrows():
                designation_id = d.get("id")
                player_name = d.get("player_name") or "Player"
                adjustment_type = d.get("adjustment_type")

                if adjustment_type == "taxi_adjustment":
                    label = "Taxi Squad"
                    remove_action = "taxi_removed"
                else:
                    label = "Injured Reserve"
                    remove_action = "ir_removed"

                cols = st.columns([3, 1])

                with cols[0]:
                    st.markdown(f"**{label}:** {player_name}")

                with cols[1]:
                    if designation_id and st.button(
                        "Remove",
                        key=f"remove_designation_{designation_id}"
                    ):
                        rest_request(
                            "DELETE",
                            "cap_adjustments",
                            params={"id": f"eq.{designation_id}"},
                        )

                        load_cap_adjustments.clear()

                        log_team_activity(
                            remove_action,
                            player_name,
                        )

                        st.toast(f"{player_name} removed from {label}")
                        st.rerun()

            st.markdown("---")

        roster_options = sorted(
            my_roster["player"].dropna().astype(str).unique().tolist()
        ) if not my_roster.empty else []

        selected_designation_player = st.selectbox(
            "Player",
            roster_options,
            key="designation_player",
        )

        designation = st.selectbox(
            "Designation",
            ["Taxi Squad", "Injured Reserve"],
            key="designation_type",
        )

        if selected_designation_player:
            player_row = my_roster[
                my_roster["player"] == selected_designation_player
            ].iloc[0]

            salary = float(player_row.get("salary") or 0)

            if designation == "Taxi Squad":
                adjustment_type = "taxi_adjustment"
                adjustment_amount = round(-(salary / 3), 2)
            else:
                adjustment_type = "ir_adjustment"
                adjustment_amount = round(-(salary / 2), 2)

            st.caption(
                f"Cap Adjustment: ${adjustment_amount:.2f}"
            )

            if st.button(
                "Apply Designation",
                key="apply_designation"
            ):
                payload = {
                    "league_id": league_id,
                    "owner_name": owner_name,
                    "player_name": selected_designation_player,
                    "season": active_season,
                    "adjustment_type": adjustment_type,
                    "amount": adjustment_amount,
                    "note": f"{designation} designation",
                }

                existing_designation = pd.DataFrame()

                if not cap_adj_df.empty:
                    existing_designation = cap_adj_df[
                        cap_adj_df["adjustment_type"]
                        .astype(str)
                        .eq(adjustment_type)
                    ]

                if not existing_designation.empty:
                    st.warning(
                        f"{team_name} already has a player designated to {designation}. Remove the current player before assigning a new one."
                    )
                else:
                    rest_request(
                        "POST",
                        "cap_adjustments",
                        json_body=payload,
                    )

                    load_cap_adjustments.clear()
                    log_team_activity(
                        designation.lower(),
                        selected_designation_player,
                    )

                    st.success(
                        f"{selected_designation_player} added to {designation}"
                    )

                    st.rerun()
# ---------- activity ----------
with st.container(border=True):
    st.markdown("### Recent Team Activity")

    activity_parts = []

    if not tx_df.empty:
        mask = pd.Series(False, index=tx_df.index)

        for c in ["from_owner_name", "to_owner_name", "from_team_name", "to_team_name"]:
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
        for _, r in activity.head(12).iterrows():
            ts_raw = r.get("ts") or r.get("created_at") or ""
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).strftime("%Y-%m-%d")
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

            st.markdown(
                f"<div class='activity-item'><strong>{action}</strong> · {player}<small>{ts}</small></div>",
                unsafe_allow_html=True,
            )