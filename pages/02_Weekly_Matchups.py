# pages/02_Weekly_Matchups.py
# ------------------------------------------------------------
# Weekly Matchups — same visual system as Teams page (style-only)
# Data fetching/ranking logic unchanged.
# ------------------------------------------------------------

import os, sys
from pathlib import Path
from typing import Dict, List, Tuple
import requests
import pandas as pd
import streamlit as st
from components.sidebar_nav import render_nav

from auth import require_login, current_user, _sb
from season_engine import SeasonResolver

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Weekly Matchups",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded"
)

render_nav()
# ---------- Path prelude ----------
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR  = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))

require_login("home.py")

u = current_user()
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

try:
    league_row = (
        sb.table("leagues")
        .select("id,name,sleeper_league_id")
        .eq("id", league_id)
        .single()
        .execute()
        .data
    )
except Exception as e:
    st.error(f"Could not load active league: {e}")
    st.stop()

ACTIVE_SEASON = SeasonResolver(sb).get_active_season(league_id)
SLEEPER_LEAGUE_ID = str(ACTIVE_SEASON.sleeper_league_id or "").strip()
SLEEPER_LEAGUE_ID = "".join(ch for ch in SLEEPER_LEAGUE_ID if ch.isdigit())

if not SLEEPER_LEAGUE_ID:
    st.error("This league does not have a Sleeper league connected yet.")
    st.stop()
# ---------- HTTP helpers ----------
def _get(url: str):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

def _as_number(x):
    try:
        if x is None: return 0.0
        if isinstance(x, (int, float)): return float(x)
        return float(str(x))
    except Exception:
        return 0.0

# ---------- Name mapping ----------
def _roster_id_to_appname(league_id: str) -> Dict[int, str]:
    """
    roster_id -> Team name from our app database.
    Fallback to Sleeper display name if the roster is not in our teams table yet.
    """

    # 1) App database source of truth
    app_map: Dict[int, str] = {}

    try:
        rows = (
            sb.table("teams")
            .select("sleeper_roster_id,team_name")
            .eq("league_id", league_id)
            .execute()
            .data
            or []
        )

        for row in rows:
            rid = row.get("sleeper_roster_id")
            name = str(row.get("team_name") or "").strip()

            if rid is not None and name:
                app_map[int(rid)] = name

    except Exception:
        app_map = {}

    # 2) Sleeper fallback
    users = _get(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/users")
    rosters = _get(f"https://api.sleeper.app/v1/league/{SLEEPER_LEAGUE_ID}/rosters")

    uid_to_disp = {
        u["user_id"]: (u.get("display_name") or u.get("username") or "").strip()
        for u in users
    }

    rid_to_name: Dict[int, str] = {}

    for r in rosters:
        rid = r.get("roster_id")
        own = r.get("owner_id")

        if rid in app_map:
            rid_to_name[rid] = app_map[rid]
        else:
            rid_to_name[rid] = uid_to_disp.get(own, f"Roster {rid}")

    return rid_to_name

# ---------- Data fetch + ranking ----------
def _fetch_week(league_id: str, week: int) -> pd.DataFrame:
    """Return DataFrame (Rank A/B, Team A/B, Score A/B) with app display names."""
    columns = [
        "Weekly Rank (A)",
        "Team A",
        "Score A",
        "Team B",
        "Score B",
        "Weekly Rank (B)",
    ]

    rid_to_name = _roster_id_to_appname(league_id)
    rows = _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{week}") or []

    by_mid = {}
    for r in rows:
        mid = r.get("matchup_id")
        if mid is None:
            continue
        by_mid.setdefault(mid, []).append(r)

    out = []
    all_scores = []

    for mid, games in by_mid.items():
        if len(games) < 2:
            continue

        a, b = games[0], games[1]

        rid_a, rid_b = a.get("roster_id"), b.get("roster_id")
        name_a = rid_to_name.get(rid_a, f"Roster {rid_a}")
        name_b = rid_to_name.get(rid_b, f"Roster {rid_b}")

        pa = _as_number(a.get("points", 0.0))
        pb = _as_number(b.get("points", 0.0))
        sa = _as_number(a.get("starters_points", 0.0))
        sb = _as_number(b.get("starters_points", 0.0))

        if pa < 10.0 <= sa:
            pa = sa
        if pb < 10.0 <= sb:
            pb = sb

        all_scores.append((name_a, pa))
        all_scores.append((name_b, pb))

        out.append({
            "Team A": name_a,
            "Score A": round(pa, 2),
            "Team B": name_b,
            "Score B": round(pb, 2),
        })

    if not out:
        return pd.DataFrame(columns=columns)

    rank_map = {}
    if all_scores:
        df_scores = pd.DataFrame(all_scores, columns=["Team", "Score"])
        df_scores["Rank"] = (
            df_scores["Score"]
            .rank(method="min", ascending=False)
            .astype(int)
        )
        rank_map = dict(zip(df_scores["Team"], df_scores["Rank"]))

    for row in out:
        row["Weekly Rank (A)"] = rank_map.get(row["Team A"])
        row["Weekly Rank (B)"] = rank_map.get(row["Team B"])

    df = pd.DataFrame(out)

    df = df[columns]
    df = df.sort_values(
        ["Weekly Rank (A)", "Weekly Rank (B)"],
        na_position="last"
    ).reset_index(drop=True)

    return df

@st.cache_data(ttl=60, show_spinner=False)
def load_weekly_matchups_cached(league_id: str, week: int) -> pd.DataFrame:
    return _fetch_week(league_id, week)

def _find_latest_week_with_data(league_id: str, max_week: int = 25) -> int:
    for wk in range(max_week, 0, -1):
        try:
            data = _get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{wk}") or []
            if data:
                return wk
        except Exception:
            continue
    return 1


def _current_nfl_week(max_week: int = 25) -> int:
    """Return the current live NFL week from Sleeper state; fallback to latest data."""
    try:
        state = _get("https://api.sleeper.app/v1/state/nfl")
        wk = int(state.get("week") or 1)
        if wk <= 0:
            raise ValueError("preseason or unknown week")
        return max(1, min(max_week, wk))
    except Exception:
        return _find_latest_week_with_data(SLEEPER_LEAGUE_ID, max_week=max_week)
# =========================
# STYLE (matches Teams page)
# =========================
st.markdown("""
<style>
:root{
  --bg: #061311;
  --panel: #101E1D;
  --panel-2: #0C1917;
  --gold: #E2BC5B;
  --gold-soft: rgba(226,188,91,.35);
  --text: #FFF5E7;
  --muted: #9DA89C;
  --shadow-amb: 0 2px 10px rgba(0,0,0,.28), 0 1px 2px rgba(0,0,0,.28);
  --shadow-lift: 0 6px 22px rgba(0,0,0,.36), 0 2px 6px rgba(0,0,0,.28);
  --ring-focus: 0 0 0 1px rgba(226,188,91,.45), 0 0 0 3px rgba(226,188,91,.18);
}

/* App chrome */
@import url('https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: 'Lato', sans-serif !important;
  background: var(--bg);
  color: var(--text);
}
[data-testid="stToolbar"], [data-testid="stHeader"] h1, header, [data-testid="stAppHeader"] { display: none !important; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 64px; }
.block-container .stColumns{ gap: 12px !important; }

/* Row 1 — make number input look like a stat card */
.row1 [data-testid="stNumberInput"]{
  position: relative;
  overflow: hidden;
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--gold-soft);
  border-radius: 16px;
  box-shadow: var(--shadow-amb);
  padding: 14px 16px;
  min-height: 88px;
  display: flex; flex-direction: column; justify-content: center;
  margin-bottom: 0 !important;
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease, background .18s ease;
}
.row1 [data-testid="stNumberInput"]:hover{
  box-shadow: var(--shadow-lift);
  transform: translateY(-2px);
  border-color: rgba(226,188,91,.48);
}
.row1 [data-testid="stNumberInput"]::before{
  content:"";
  position:absolute; left:6px; right:6px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(90deg, rgba(226,188,91,.0) 0%, rgba(226,188,91,.35) 22%, rgba(226,188,91,.6) 50%, rgba(226,188,91,.35) 78%, rgba(226,188,91,.0) 100%);
  pointer-events:none;
}
.row1 [data-testid="stNumberInput"] > label{
  margin: 0 0 6px 0 !important;
  font-size:.72rem !important;
  letter-spacing:.04em; text-transform:uppercase; color: var(--muted) !important;
}
.row1 div[data-testid="stNumberInput"] input{
  background: rgba(255,255,255,.02) !important;
  border: 1px solid rgba(226,188,91,.25) !important;
  border-radius: 12px !important;
  padding: 6px 10px !important;
  min-height: 38px; color: var(--text) !important; text-align: center;
}
.row1 div[data-testid="stNumberInput"] input:focus{
  outline: none !important; box-shadow: var(--ring-focus) !important;
  border-color: rgba(226,188,91,.55) !important;
}

/* Make the st.dataframe itself a panel card */
div[data-testid="stDataFrame"]{
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--gold-soft);
  border-radius: 16px;
  box-shadow: var(--shadow-amb);
  padding: 8px 8px 2px 8px;
  position: relative;
}
div[data-testid="stDataFrame"]::before{
  content:"";
  position:absolute; left:6px; right:6px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(90deg, rgba(226,188,91,.0) 0%, rgba(226,188,91,.35) 22%, rgba(226,188,91,.6) 50%, rgba(226,188,91,.35) 78%, rgba(226,188,91,.0) 100%);
  pointer-events:none;
}

/* Dataframe header/rows */
div[data-testid="stDataFrame"] table thead tr th{
  position: sticky; top: 0; z-index: 2;
  background: var(--panel-2);
  border-bottom: 1px solid var(--gold-soft);
}
div[data-testid="stDataFrame"] table tbody tr:nth-child(even) td{ background: rgba(255,255,255,.02); }
div[data-testid="stDataFrame"] table tbody tr:hover td{ background: rgba(226,188,91,.08); }

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
.empty-matchups-card {
  margin-top: 28px;
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--gold-soft);
  border-radius: 18px;
  box-shadow: var(--shadow-amb);
  padding: 34px 28px;
  text-align: center;
}

.empty-title {
  color: var(--text);
  font-size: 1.25rem;
  font-weight: 900;
  margin-bottom: 8px;
}

.empty-subtitle {
  color: var(--muted);
  font-size: .95rem;
  max-width: 720px;
  margin: 0 auto;
  line-height: 1.5;
}

</style>
""", unsafe_allow_html=True)

def show_loading_screen(placeholder, text: str = "Loading Weekly Matchups..."):
    placeholder.markdown(
        f"""
        <div class="legacy-loader-wrap">
            <div class="legacy-loader"></div>
            <div class="legacy-loader-text">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------- UI ----------
left, right = st.columns([1,1])
with left:
    st.caption("Season (for reference)")
    st.markdown("**2025**")

# Default to current week on first load
if "weekly_wk" not in st.session_state:
    st.session_state["weekly_wk"] = _find_latest_week_with_data(SLEEPER_LEAGUE_ID, max_week=25)
    st.session_state["weekly_wk_origin"] = "auto"

# Row 1 — week input card
st.markdown('<div class="row1">', unsafe_allow_html=True)
c1, c2, c3 = st.columns([2,1.2,2])
with c2:
    week = st.number_input("Week", min_value=1, max_value=25,
                           value=int(st.session_state["weekly_wk"]), step=1, key="weekly_week_picker")
    if int(week) != int(st.session_state["weekly_wk"]):
        st.session_state["weekly_wk"] = int(week)
        st.session_state["weekly_wk_origin"] = "user"
st.markdown('</div>', unsafe_allow_html=True)

# ---------- Load + style (logic preserved) ----------
try:
    loading_box = st.empty()
    show_loading_screen(loading_box, "Loading Weekly Matchups...")

    df = load_weekly_matchups_cached(
        SLEEPER_LEAGUE_ID,
        int(st.session_state["weekly_wk"])
    )

    loading_box.empty()

    if df.empty:
        st.markdown("""
        <div class="empty-matchups-card">
            <div class="empty-title">No matchups found for this week yet</div>
            <div class="empty-subtitle">
                Sleeper may not have generated the official schedule for this week.
                Once matchups are available, this page will automatically display them here.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Normalize numeric presentation
        df["Score A"] = df["Score A"].astype(float).round(2)
        df["Score B"] = df["Score B"].astype(float).round(2)
        # Match Teams page palette (for badges)
        GOLD      = "#E2BC5B"
        GOLD_SOFT = "rgba(226,188,91,.35)"
        PANEL     = "#101E1D"
        PANEL_2   = "#0C1917"

        # Score badges (panel look; winner gets gold text/border glow)
        SCORE_BADGE = (
            "background: radial-gradient(120% 140% at 20% 0%, {p1} 0%, {p2} 100%);"
            "border: 1px solid {border}; border-radius: 10px;"
            "padding: 2px 8px; display: inline-block;"
            "box-shadow: 0 2px 10px rgba(0,0,0,.20);"
            "text-align: right; font-weight: 600;"
        ).format(p1=PANEL, p2=PANEL_2, border=GOLD_SOFT)

        WINNER_BADGE = (
            "background: radial-gradient(120% 140% at 20% 0%, {p1} 0%, {p2} 100%);"
            "border: 1px solid {border}; border-radius: 10px;"
            "padding: 2px 8px; display: inline-block;"
            "box-shadow: 0 2px 14px rgba(226,188,91,.25);"
            "text-align: right; font-weight: 800; color: {gold};"
        ).format(p1=PANEL, p2=PANEL_2, border=GOLD, gold=GOLD)

        base_styles = pd.DataFrame("", index=df.index, columns=df.columns)
        base_styles.loc[:, "Score A"] = SCORE_BADGE
        base_styles.loc[:, "Score B"] = SCORE_BADGE

        for i, row in df.iterrows():
            try:
                a = float(row["Score A"]); b = float(row["Score B"])
                if a > b:
                    base_styles.at[i, "Score A"] = WINNER_BADGE
                elif b > a:
                    base_styles.at[i, "Score B"] = WINNER_BADGE
            except Exception:
                pass

        styler = (
            df.style
              .set_table_styles([
                  {"selector": "tbody td", "props": [("padding", "6px 10px")]},
                  {"selector": "thead th", "props": [("padding", "8px 10px")]},
              ])
              .format({"Score A": "{:.2f}", "Score B": "{:.2f}"})
              .set_properties(subset=["Team A","Team B"], **{"text-align": "left"})
              .set_properties(subset=["Weekly Rank (A)","Weekly Rank (B)"], **{"text-align": "right"})
              .apply(lambda _: base_styles, axis=None)
        )

        # Render the table (styled directly as a panel via CSS above)
        st.dataframe(
            styler,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Weekly Rank (A)": st.column_config.NumberColumn("Weekly Rank", width="small"),
                "Team A": st.column_config.TextColumn("", width="medium"),
                "Score A": st.column_config.NumberColumn("Score", format="%.2f"),
                "Team B": st.column_config.TextColumn("", width="medium"),
                "Score B": st.column_config.NumberColumn("Score", format="%.2f"),
                "Weekly Rank (B)": st.column_config.NumberColumn("Weekly Rank", width="small"),
            },
        )
except Exception as e:
    st.error(f"Failed to load matchups: {e}")
