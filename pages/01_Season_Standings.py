# pages/01_Season_Standings.py
# ============================================================
# Fantasy GM — Season Standings (Leaders inline row, no big title)
# Data now computed live from Sleeper Weekly Matchups only.
# ============================================================

from __future__ import annotations
import os, re
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import urllib.parse as urlparse

import requests
import pandas as pd
import streamlit as st
from components.sidebar_nav import render_nav

from services.app_context import get_app_context


# ------------------------------------------------------------------
# [APP/BASE CONFIG]
# ------------------------------------------------------------------
ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Season Standings",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded"
)

render_nav()
# ------------------------------------------------------------------
# [GLOBAL THEME CSS]  (upgraded for card layout + dark theme)
# ------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap');

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

html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: 'Lato', sans-serif !important;
  background: var(--bg);
  color: var(--text);
}
[data-testid="stToolbar"], [data-testid="stHeader"] h1, header, [data-testid="stAppHeader"] {
  display: none !important;
}
[data-testid="stHeader"] { background: transparent; }

.block-container {
  padding-top: 24px;
  padding-bottom: 32px;
  max-width: 1400px;
}

/* Section titles (e.g., "Leaders", "Standings") */
.section-title {
  font-size: 1.25rem;
  font-weight: 900;
  margin: 0 0 8px 0;
  color: #FFF5E7;
}

/* Thin gold divider */
.divider {
  height:1px;
  background:linear-gradient(90deg, rgba(157,121,48,0), rgba(157,121,48,.55), rgba(157,121,48,0));
  margin:12px 0 18px;
}

/* Leaders row as stat cards — horizontal row */
.stat-row {
  display: flex;
  flex-wrap: wrap;          /* allows wrapping on very small screens */
  gap: 18px;
  margin-bottom: 8px;
}
.stat-row > div {
  flex: 1 1 220px;   /* each leader card gets a share of the row */
}


.stat-card {
  position: relative;
  overflow: hidden;
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border-radius: 18px;
  border: 1px solid var(--gold-soft);
  box-shadow: var(--shadow-amb);
  padding: 14px 16px 12px 16px;
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease, background .18s ease;

}

.stat-card::before{
  content:"";
  position:absolute; left:10px; right:10px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(90deg, rgba(226,188,91,.0) 0%, rgba(226,188,91,.35) 22%, rgba(226,188,91,.6) 50%, rgba(226,188,91,.35) 78%, rgba(226,188,91,.0) 100%);
  pointer-events:none;
}
.stat-card:hover{
  box-shadow: var(--shadow-lift);
  transform: translateY(-2px);
  border-color: rgba(226,188,91,.55);
}

/* Leaders inner text */
.leader-block { display:flex; flex-direction:column; }
.leader-label  {
  font-size:.78rem;
  opacity:.9;
  margin-bottom: 4px;
  text-transform:uppercase;
  letter-spacing:.04em;
  color: var(--muted);
}

/* container for team + number */
.leader-value  {
  font-size:1.1rem;
  font-weight:900;
  line-height:1.2;
  color:#FFF5E7;
}

/* make the whole thing (team + number) a vertical, centered link */
.leader-value a.leader-link{
  color: inherit;
  text-decoration: none;
  display:flex;
  flex-direction:column;
  align-items:center;      /* centers both rows horizontally */
}
.leader-value a.leader-link:hover{
  text-decoration: underline;
}

/* row 1: team name */
.leader-team{
  font-size:1.1rem;
  font-weight:900;
}

/* row 2: big centered number */
.leader-number{
  font-size:1.8rem;
  font-weight:900;
}



/* Center & tighten the main standings table */
.center-outer { display:flex; justify-content:center; }
.center-inner { width:min(1050px, 96%); }

/* Generic panel card (standings & top/bottom blocks) */
.panel-card{
  position: relative;
  overflow: hidden;
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border-radius: 18px;
  border: 1px solid var(--gold-soft);
  box-shadow: var(--shadow-amb);
  padding: 10px 10px 6px 10px;
  margin-bottom: 18px;
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease;
}
.panel-card::before{
  content:"";
  position:absolute; left:10px; right:10px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(90deg, rgba(226,188,91,.0) 0%, rgba(226,188,91,.35) 22%, rgba(226,188,91,.6) 50%, rgba(226,188,91,.35) 78%, rgba(226,188,91,.0) 100%);
  pointer-events:none;
}
.panel-card:hover{
  box-shadow: var(--shadow-lift);
  transform: translateY(-2px);
  border-color: rgba(226,188,91,.55);
}
/* Make each st.dataframe render inside a card-style panel */
div[data-testid="stDataFrame"]{
  position: relative;
  overflow: hidden;
  background: radial-gradient(120% 140% at 20% 0%, var(--panel) 0%, var(--panel-2) 100%);
  border-radius: 18px;
  border: 1px solid var(--gold-soft);
  box-shadow: var(--shadow-amb);
  padding: 10px 10px 6px 10px;
  margin-bottom: 18px;
}

/* Gold accent line on top of each table card */
div[data-testid="stDataFrame"]::before{
  content:"";
  position:absolute; left:10px; right:10px; top:6px; height:2px;
  border-radius:2px;
  background: linear-gradient(
    90deg,
    rgba(226,188,91,.0) 0%,
    rgba(226,188,91,.35) 22%,
    rgba(226,188,91,.6) 50%,
    rgba(226,188,91,.35) 78%,
    rgba(226,188,91,.0) 100%
  );
  pointer-events:none;
}

/* Dataframe compaction & table styling */
[data-testid="stDataFrame"] div[role="table"] {
  font-size: .92rem;
}
thead tr th, tbody tr td {
  padding-top: 6px !important;
  padding-bottom: 6px !important;
}

/* Table colors & zebra stripes */
div[data-testid="stDataFrame"] table thead tr th{
  background: var(--panel-2) !important;
  color: var(--muted) !important;
  border-bottom: 1px solid var(--gold-soft) !important;
}

div[data-testid="stDataFrame"] table tbody td{
  color: var(--text) !important;
  border-bottom: 0 !important;
}

div[data-testid="stDataFrame"] table tbody tr:nth-child(even) td{
  background: rgba(255,255,255,.02);
}
div[data-testid="stDataFrame"] table tbody tr:hover td{
  background: rgba(226,188,91,.08);
}

/* Fix for inner scrollbars overlapping panel corners */
div[data-testid="stDataFrame"] > div:nth-child(1){
  border-radius: 10px;
}
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------
# [HELPERS: numeric cleaners, column pickers]  (unchanged)
# ------------------------------------------------------------------
NUM_RE = re.compile(r"[^0-9.\-()]")

def _clean_num(s: str) -> str:
    s = ("" if s is None else str(s)).strip()
    if not s: return ""
    neg = s.startswith("(") and s.endswith(")")
    s = NUM_RE.sub("", s).replace("(", "").replace(")", "")
    return "-" + s if neg else s

def as_int(x) -> int:
    try: return int(float(_clean_num(x)))
    except: return 0

def as_float(x) -> float:
    try: return float(_clean_num(x))
    except: return 0.0

def pick(cols: List[str], keys: List[str]) -> Optional[str]:
    idx = {c.lower().strip(): c for c in cols}
    for k in keys:
        if k in idx: return idx[k]
    return None

# ------------------------------------------------------------------
# [DERIVED "RICH" METRICS TABLE]  (reused, works with live data)
# ------------------------------------------------------------------
def infer_weeks_played(df: pd.DataFrame) -> int:
    if "Games" in df.columns and not df["Games"].empty:
        return int(df["Games"].max())
    return max(int(df["Wins"].max()), 1) if "Wins" in df.columns and not df["Wins"].empty else 8

def add_meta_metrics(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()

    # Ensure expected columns exist
    if "Standing Points" not in d.columns and "Points" in d.columns:
        d["Standing Points"] = d["Points"]

    weeks = infer_weeks_played(d)
    for col, default in [("PF", 0.0), ("PA", 0.0), ("Wins", 0), ("Top 5", 0)]:
        if col not in d.columns:
            d[col] = default

    if "Losses" not in d.columns:
        d["Losses"] = (weeks - d["Wins"]).clip(lower=0)

    d["PF Per Game"] = (d["PF"] / weeks).round(1) if weeks > 0 else 0.0
    d["PA Per Game"] = (d["PA"] / weeks).round(1) if weeks > 0 else 0.0

    total_points = d["Standing Points"].sum() if "Standing Points" in d.columns else 0
    total_pf     = d["PF"].sum()
    d["% of Total Standings Points"] = (100.0 * d["Standing Points"] / total_points).round(1) if total_points else 0.0
    d["% of Total Points Scored"]    = (100.0 * d["PF"] / total_pf).round(1) if total_pf else 0.0

    ordered = [
        "Team","Standing Points","Wins","Losses","Top 5",
        "PF","PF Per Game","PA","PA Per Game",
        "% of Total Standings Points","% of Total Points Scored"
    ]
    return d[[c for c in ordered if c in d.columns]]

# ------------------------------------------------------------------
# [CELL STYLE HELPERS FOR BADGES (Wins / Losses / Top 5)]
# ------------------------------------------------------------------
def standings_badge_style(df: pd.DataFrame) -> pd.DataFrame:
    """Return CSS styles for wins / losses / top5 badge-like cells."""
    styles = pd.DataFrame("", index=df.index, columns=df.columns)

    if "Wins" in df.columns:
        styles["Wins"] = (
            "background: rgba(34,197,94,.16); "
            "border-radius: 999px; "
            "padding: 2px 10px; "
            "text-align: center;"
        )

    if "Losses" in df.columns:
        styles["Losses"] = (
            "background: rgba(239,68,68,.14); "
            "border-radius: 999px; "
            "padding: 2px 10px; "
            "text-align: center;"
        )

    if "Top 5" in df.columns:
        styles["Top 5"] = (
            "background: rgba(226,188,91,.20); "
            "border-radius: 999px; "
            "padding: 2px 10px; "
            "text-align: center;"
        )

    return styles

@st.cache_data(ttl=300, show_spinner=False)
def load_standings_snapshot(league_id: str | None, refresh_key: int = 0) -> pd.DataFrame:
    ctx = get_app_context(force_refresh=refresh_key > 0)
    return ctx.get("stand_df", pd.DataFrame()).copy()

# ============================================================
# [PAGE RENDER]
# ============================================================

# --- 1) LOAD DATA (5-minute cached standings snapshot) ---
league_id = st.session_state.get("active_league_id")

if "standings_refresh_key" not in st.session_state:
    st.session_state["standings_refresh_key"] = 0

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
    <div class="legacy-loader-text">Loading Season Standings...</div>
</div>
""",
    unsafe_allow_html=True,
)

live_df = load_standings_snapshot(
    league_id,
    0
)

spinner_placeholder.empty()
if live_df.empty:
    live_df = pd.DataFrame({
        "Team": [
            "Chase Seyforth", "Chasen Hardy", "Connor Cassidy", "Dylan Burruel",
            "Grady Graham", "Kevin Wells", "Mekel Sanchez", "Nando Munoz",
            "Nick Salafia", "Tommy Bruggeman"
        ],
        "Standing Points": [0] * 10,
        "PF": [0] * 10,
        "PA": [0] * 10,
        "Wins": [0] * 10,
        "Top 5": [0] * 10,
        "Games": [0] * 10,
    })

    st.info("No completed Sleeper matchups yet. Showing preseason standings.")
# Build base_df for Leaders (expects 'Points')
base_df = (
    live_df.rename(columns={"Standing Points": "Points"})
           [["Team","Points","PF","Wins","Top 5"]]
)

# Keep canonical names for the rest of the page
meta_full = add_meta_metrics(live_df)

# --- FORCE ALL NUMERIC COLUMNS TO 0 DECIMAL PLACES ---
for col in meta_full.columns:
    if pd.api.types.is_numeric_dtype(meta_full[col]):
        meta_full[col] = meta_full[col].round(0).astype(int)


# --- 2) LEADERS (horizontal stat cards row using Streamlit columns) ---
st.markdown('<div class="section-title">Leaders</div>', unsafe_allow_html=True)

def _leader_row(df: pd.DataFrame, col: str, asc: bool = False) -> Tuple[str, float | int]:
    """
    Return (team_name, value) for the leader in the given column.
    """
    if col not in df.columns or df.empty:
        return "—", 0
    r = df.sort_values(col, ascending=asc).iloc[0]
    return str(r["Team"]), r[col]

leader_specs = [
    ("Points", "Points", False),
    ("PF", "PF", False),
    ("Wins", "Wins", False),
    ("Top 5", "Top 5", False),
]

# 4 side-by-side columns for the 4 leader cards
cols = st.columns(len(leader_specs), gap="large")

for (label, col, asc), c in zip(leader_specs, cols):
    team, raw_val = _leader_row(base_df, col, asc)

    # numeric formatting (always set val_str)
    if isinstance(raw_val, (int, float)):
        val_str = str(int(round(float(raw_val))))
    else:
        val_str = str(raw_val)

    if team == "—":
        target_href = "#"
    else:
        # link into Teams page, passing display name as ?team=...
        target_href = f"/?page=teams&team={urlparse.quote(team)}"

    with c:
        st.markdown(
            f"""
            <div class="stat-card">
              <div class="leader-block">
                <span class="leader-label">{label}</span>
                <span class="leader-value">
                  <a href="{target_href}" class="leader-link">
                    <span class="leader-team">{team}</span>
                    <span class="leader-number">{val_str}</span>
                  </a>
                </span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
# --- 3) MAIN STANDINGS TABLE (in centered panel card) ---
stand_cols = ["Team", "Standing Points", "PF", "Wins", "Top 5"]
available = [c for c in stand_cols if c in meta_full.columns]
standings_df = (
    meta_full[available]
    .sort_values(
        ["Standing Points", "PF", "Wins", "Top 5"],
        ascending=[False, False, False, False]
    )
    .reset_index(drop=True)
)

standings_styler = standings_df.style.apply(standings_badge_style, axis=None)

column_config = {}
try:
    from streamlit import column_config as cc
    if "Standing Points" in standings_df.columns:
        column_config["Standing Points"] = cc.NumberColumn(
            "Standing Points", format="%d", width="small"
        )
    if "PF" in standings_df.columns:
        column_config["PF"] = cc.NumberColumn("PF", format="%.0f", width="small")
    if "Wins" in standings_df.columns:
        column_config["Wins"] = cc.NumberColumn("Wins", format="%d", width="small")
    if "Top 5" in standings_df.columns:
        column_config["Top 5"] = cc.NumberColumn("Top 5", format="%d", width="small")
except Exception:
    pass

st.markdown('<div class="center-outer"><div class="center-inner">', unsafe_allow_html=True)

st.markdown('<div class="section-title">Standings</div>', unsafe_allow_html=True)
st.dataframe(
    standings_styler,
    hide_index=True,
    use_container_width=True,
    column_config=column_config
)

st.markdown('</div></div>', unsafe_allow_html=True)
st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# --- 4) RICH TOP/BOTTOM TABLES (unchanged layout; now panel cards) ---
RANK_BY = "Standing Points"

def _condense_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={
        "Standing Points": "SP",
        "Wins": "Win",
        "Losses": "Loss",
        "PF Per Game": "PF / Game",
        "PA Per Game": "PA / Game",
        "% of Total Standings Points": "% of Total SP",
        "% of Total Points Scored": "% of Total Points",
    })

left, right = st.columns(2, gap="large")

with left:
    top5 = meta_full.sort_values(RANK_BY, ascending=False).head(5).reset_index(drop=True)
    cols_out = [c for c in [
        "Team","Standing Points","Wins","Losses","Top 5",
        "PF","PF Per Game","PA","PA Per Game",
        "% of Total Standings Points","% of Total Points Scored"
    ] if c in top5.columns]
    top5_df = top5[cols_out]
    top5_df = _condense_columns(top5_df)  # 👈 new line
    top5_styler = top5_df.style.apply(standings_badge_style, axis=None)

    st.markdown(f'<div class="section-title">Top 5 ({RANK_BY})</div>', unsafe_allow_html=True)
    st.dataframe(top5_styler, use_container_width=True, hide_index=True)

with right:
    bottom5 = meta_full.sort_values(RANK_BY, ascending=True).head(5).reset_index(drop=True)
    cols_out = [c for c in [
        "Team","Standing Points","Wins","Losses","Top 5",
        "PF","PF Per Game","PA","PA Per Game",
        "% of Total Standings Points","% of Total Points Scored"
    ] if c in bottom5.columns]
    bottom5_df = bottom5[cols_out]
    bottom5_df = _condense_columns(bottom5_df)  # 👈 new line
    bottom5_styler = bottom5_df.style.apply(standings_badge_style, axis=None)

    st.markdown(f'<div class="section-title">Bottom 5 ({RANK_BY})</div>', unsafe_allow_html=True)
    st.dataframe(bottom5_styler, use_container_width=True, hide_index=True)
