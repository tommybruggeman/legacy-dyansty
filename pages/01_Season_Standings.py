# pages/01_Season_Standings.py
# ============================================================
# Fantasy GM — Season Standings
#
# Desktop:
# - Existing leaders / standings / top-bottom tables preserved.
#
# Mobile:
# - Dedicated 2x2 Leaders grid
# - Gold outline icons
# - Animated shimmer cards
# - Compact 10-team standings table
# - Existing Top 5 / Bottom 5 sections preserved
#
# Standings data continues to come from the existing app context.
# ============================================================

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import List, Optional, Tuple
import urllib.parse as urlparse

import pandas as pd
import streamlit as st

from components.sidebar_nav import render_nav
from services.app_context import get_app_context


# ============================================================
# APP / BASE CONFIG
# ============================================================

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Season Standings",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()


# ============================================================
# THEME / PAGE CSS
# ============================================================

st.markdown(
    """
<style>

@import url(
    'https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap'
);

:root {
    --bg: #061311;
    --panel: #101E1D;
    --panel-2: #0C1917;

    --gold: #E2BC5B;
    --gold-deep: #B98727;
    --gold-soft: rgba(226,188,91,.35);

    --text: #FFF5E7;
    --muted: #9DA89C;

    --shadow-amb:
        0 2px 10px rgba(0,0,0,.28),
        0 1px 2px rgba(0,0,0,.28);

    --shadow-lift:
        0 6px 22px rgba(0,0,0,.36),
        0 2px 6px rgba(0,0,0,.28);
}


/* =========================================================
   APP CHROME
========================================================= */

html,
body,
[data-testid="stAppViewContainer"],
[data-testid="stSidebar"] {
    font-family: 'Lato', sans-serif !important;
    background: var(--bg);
    color: var(--text);
}


/*
Do not remove the entire Streamlit header.
The shared navigation component needs access to mobile
navigation / sidebar controls.
*/

[data-testid="stHeader"] {
    background: transparent !important;
}


[data-testid="stToolbar"] {
    display: none !important;
}


.block-container {
    padding-top: 24px;
    padding-bottom: 32px;
    max-width: 1400px;
}


/* =========================================================
   TITLES
========================================================= */

.mobile-page-title {
    display: none;

    color: var(--text);

    font-size: 2rem;
    font-weight: 900;

    line-height: 1.1;

    letter-spacing: -.03em;

    margin-bottom: 1.1rem;
}


.section-title {
    font-size: 1.25rem;

    font-weight: 900;

    margin:
        0 0 8px 0;

    color: var(--text);
}


.divider {
    height: 1px;

    background:
        linear-gradient(
            90deg,
            rgba(157,121,48,0),
            rgba(157,121,48,.55),
            rgba(157,121,48,0)
        );

    margin:
        12px 0 18px;
}


/* =========================================================
   DESKTOP LEADER CARDS
========================================================= */

.desktop-leaders {
    display: block;
}


.desktop-leader-grid {
    display: grid;

    grid-template-columns:
        repeat(4, minmax(0, 1fr));

    gap: 18px;

    margin-bottom: 1.5rem;
}


.desktop-leader-card {
    position: relative;

    min-width: 0;

    min-height: 135px;

    overflow: hidden;

    display: grid;

    grid-template-columns:
        46px minmax(0, 1fr);

    gap: 12px;

    align-items: center;

    border:
        1px solid rgba(226,188,91,.48);

    border-radius:
        18px;

    background:
        radial-gradient(
            130% 145% at 16% 0%,
            rgba(18,37,33,.98) 0%,
            rgba(9,27,24,.98) 60%,
            rgba(6,19,17,1) 100%
        );

    box-shadow:
        0 5px 18px rgba(0,0,0,.28),
        inset 0 1px 0 rgba(255,255,255,.015);

    padding:
        18px 16px;

    isolation:
        isolate;

    transition:
        transform .18s ease,
        box-shadow .18s ease,
        border-color .18s ease;
}


.desktop-leader-card::before {
    content: "";

    position: absolute;

    z-index: 1;

    left: 14px;
    right: 14px;
    top: 6px;

    height: 2px;

    border-radius:
        999px;

    background:
        linear-gradient(
            90deg,
            rgba(226,188,91,0),
            rgba(226,188,91,.22),
            rgba(226,188,91,.75),
            rgba(226,188,91,.22),
            rgba(226,188,91,0)
        );

    box-shadow:
        0 0 10px rgba(226,188,91,.18);
}


.desktop-leader-card::after {
    content: "";

    position: absolute;

    z-index: 0;

    top: -70%;

    left: -95%;

    width: 65%;

    height: 240%;

    transform:
        rotate(18deg);

    background:
        linear-gradient(
            90deg,
            transparent,
            rgba(226,188,91,.035),
            rgba(255,245,231,.085),
            rgba(226,188,91,.04),
            transparent
        );

    animation:
        leader-shimmer 6s ease-in-out infinite;

    pointer-events:
        none;
}


.desktop-leader-card:nth-child(2)::after {
    animation-delay: 1.1s;
}


.desktop-leader-card:nth-child(3)::after {
    animation-delay: 2.2s;
}


.desktop-leader-card:nth-child(4)::after {
    animation-delay: 3.3s;
}


.desktop-leader-card:hover {
    transform:
        translateY(-2px);

    border-color:
        rgba(226,188,91,.68);

    box-shadow:
        0 8px 26px rgba(0,0,0,.34),
        0 0 18px rgba(226,188,91,.08);
}


.desktop-leader-icon {
    position: relative;

    z-index: 2;

    display: flex;

    align-items: center;

    justify-content: center;

    color:
        var(--gold);
}


.desktop-leader-icon svg {
    width: 38px;

    height: 38px;

    fill:
        none;

    stroke:
        currentColor;

    stroke-width:
        1.8;

    stroke-linecap:
        round;

    stroke-linejoin:
        round;

    filter:
        drop-shadow(
            0 0 5px rgba(226,188,91,.16)
        );
}


.desktop-leader-copy {
    position: relative;

    z-index: 2;

    min-width: 0;
}


.desktop-leader-label {
    color:
        var(--muted);

    font-size:
        .68rem;

    font-weight:
        700;

    letter-spacing:
        .05em;

    text-transform:
        uppercase;

    margin-bottom:
        5px;
}


.desktop-leader-team {
    display: block;

    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;

    color:
        var(--text);

    font-size:
        1rem;

    font-weight:
        900;

    line-height:
        1.15;

    text-decoration:
        none;

    margin-bottom:
        5px;
}


.desktop-leader-team:hover {
    color:
        var(--gold);

    text-decoration:
        underline;
}


.desktop-leader-number {
    color:
        var(--text);

    font-size:
        1.8rem;

    font-weight:
        900;

    line-height:
        1;
}

/* =========================================================
   MOBILE LEADER GRID
========================================================= */

.mobile-leaders {
    display: none;
}


.mobile-leader-grid {
    display: grid;

    grid-template-columns:
        repeat(2, minmax(0, 1fr));

    gap: 10px;

    margin-bottom: 1.35rem;
}


.mobile-leader-card {
    position: relative;

    min-width: 0;

    min-height: 118px;

    overflow: hidden;

    border:
        1px solid rgba(226,188,91,.48);

    border-radius:
        16px;

    background:
        radial-gradient(
            130% 145% at 16% 0%,
            rgba(18,37,33,.98) 0%,
            rgba(9,27,24,.98) 60%,
            rgba(6,19,17,1) 100%
        );

    box-shadow:
        0 5px 18px rgba(0,0,0,.28),
        inset 0 1px 0 rgba(255,255,255,.015);

    padding:
        14px 12px;

    isolation: isolate;
}


/*
Gold highlight line.
*/

.mobile-leader-card::before {
    content: "";

    position: absolute;

    z-index: 1;

    left: 12px;
    right: 12px;
    top: 5px;

    height: 2px;

    border-radius: 999px;

    background:
        linear-gradient(
            90deg,
            rgba(226,188,91,0),
            rgba(226,188,91,.22),
            rgba(226,188,91,.75),
            rgba(226,188,91,.22),
            rgba(226,188,91,0)
        );

    box-shadow:
        0 0 10px rgba(226,188,91,.18);
}


/*
Animated shimmer.
Very subtle so it feels premium rather than flashy.
*/

.mobile-leader-card::after {
    content: "";

    position: absolute;

    z-index: 0;

    top: -70%;

    left: -95%;

    width: 65%;

    height: 240%;

    transform:
        rotate(18deg);

    background:
        linear-gradient(
            90deg,
            transparent,
            rgba(226,188,91,.035),
            rgba(255,245,231,.085),
            rgba(226,188,91,.04),
            transparent
        );

    animation:
        leader-shimmer 6s ease-in-out infinite;

    pointer-events: none;
}


.mobile-leader-card:nth-child(2)::after {
    animation-delay: 1.1s;
}


.mobile-leader-card:nth-child(3)::after {
    animation-delay: 2.2s;
}


.mobile-leader-card:nth-child(4)::after {
    animation-delay: 3.3s;
}


@keyframes leader-shimmer {

    0% {
        left: -95%;
    }

    35% {
        left: 145%;
    }

    100% {
        left: 145%;
    }
}


.mobile-leader-link {
    position: relative;

    z-index: 2;

    display: grid;

    grid-template-columns:
        36px minmax(0, 1fr);

    gap: 9px;

    align-items: center;

    height: 100%;

    color: inherit;

    text-decoration: none;
}


.mobile-leader-icon {
    display: flex;

    align-items: center;

    justify-content: center;

    color: var(--gold);
}


.mobile-leader-icon svg {
    width: 30px;

    height: 30px;

    fill: none;

    stroke: currentColor;

    stroke-width: 1.8;

    stroke-linecap: round;

    stroke-linejoin: round;

    filter:
        drop-shadow(
            0 0 4px rgba(226,188,91,.14)
        );
}


.mobile-leader-copy {
    min-width: 0;
}


.mobile-leader-label {
    color: var(--muted);

    font-size: .62rem;

    font-weight: 700;

    letter-spacing: .045em;

    text-transform: uppercase;

    margin-bottom: 5px;
}


.mobile-leader-team {
    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;

    color: var(--text);

    font-size: .76rem;

    font-weight: 900;

    line-height: 1.15;

    margin-bottom: 2px;
}


.mobile-leader-number {
    color: var(--text);

    font-size: 1.45rem;

    font-weight: 900;

    line-height: 1;
}


/* =========================================================
   DESKTOP STANDINGS TABLE
========================================================= */

.center-outer {
    display: flex;

    justify-content: center;
}


.center-inner {
    width:
        min(1050px, 96%);
}


.panel-card {
    position: relative;

    overflow: hidden;

    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    border-radius: 18px;

    border:
        1px solid var(--gold-soft);

    box-shadow:
        var(--shadow-amb);

    padding:
        10px 10px 6px 10px;

    margin-bottom:
        18px;
}


div[data-testid="stDataFrame"] {
    position: relative;

    overflow: hidden;

    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    border-radius: 18px;

    border:
        1px solid var(--gold-soft);

    box-shadow:
        var(--shadow-amb);

    padding:
        10px 10px 6px 10px;

    margin-bottom:
        18px;
}


div[data-testid="stDataFrame"]::before {
    content: "";

    position: absolute;

    left: 10px;
    right: 10px;
    top: 6px;

    height: 2px;

    border-radius:
        2px;

    background:
        linear-gradient(
            90deg,
            rgba(226,188,91,0) 0%,
            rgba(226,188,91,.35) 22%,
            rgba(226,188,91,.6) 50%,
            rgba(226,188,91,.35) 78%,
            rgba(226,188,91,0) 100%
        );

    pointer-events:
        none;
}


[data-testid="stDataFrame"]
div[role="table"] {
    font-size:
        .92rem;
}


thead tr th,
tbody tr td {
    padding-top:
        6px !important;

    padding-bottom:
        6px !important;
}


div[data-testid="stDataFrame"]
table thead tr th {
    background:
        var(--panel-2) !important;

    color:
        var(--muted) !important;

    border-bottom:
        1px solid var(--gold-soft) !important;
}


div[data-testid="stDataFrame"]
table tbody td {
    color:
        var(--text) !important;

    border-bottom:
        0 !important;
}


div[data-testid="stDataFrame"]
table tbody tr:nth-child(even) td {
    background:
        rgba(255,255,255,.02);
}


div[data-testid="stDataFrame"]
table tbody tr:hover td {
    background:
        rgba(226,188,91,.08);
}


div[data-testid="stDataFrame"]
> div:nth-child(1) {
    border-radius:
        10px;
}


/* =========================================================
   MOBILE MAIN STANDINGS
========================================================= */

.mobile-standings-wrapper {
    display: none;
}


.mobile-standings-card {
    overflow: hidden;

    width: 100%;

    border:
        1px solid rgba(226,188,91,.45);

    border-radius:
        15px;

    background:
        radial-gradient(
            130% 145% at 16% 0%,
            rgba(18,37,33,.98) 0%,
            rgba(9,27,24,.98) 100%
        );

    box-shadow:
        0 5px 18px rgba(0,0,0,.27);
}


.mobile-standings-header,
.mobile-standings-row {
    display: grid;

    grid-template-columns:
        24px
        minmax(0, 1.65fr)
        40px
        46px
        34px
        34px;

    align-items: center;

    column-gap: 3px;

    width: 100%;

    box-sizing: border-box;
}


.mobile-standings-header {
    min-height: 38px;

    padding:
        0 7px;

    background:
        rgba(255,255,255,.02);

    color: var(--muted);

    font-size: .55rem;

    font-weight: 900;

    letter-spacing: .045em;

    text-transform: uppercase;

    border-bottom:
        1px solid rgba(226,188,91,.16);
}


.mobile-standings-row {
    min-height: 44px;

    padding:
        0 7px;

    color: var(--text);

    border-bottom:
        1px solid rgba(226,188,91,.10);
}


.mobile-standings-row:last-child {
    border-bottom:
        0;
}


.mobile-standing-rank {
    color: var(--muted);

    font-size: .64rem;

    font-weight: 800;

    text-align: center;
}


.mobile-standing-team {
    min-width: 0;

    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;

    font-size: .69rem;

    font-weight: 700;

    padding-left: 4px;
}


.mobile-standing-number {
    font-size: .65rem;

    font-weight: 700;

    text-align: right;
}


.mobile-standings-row:first-of-type
.mobile-standing-rank,
.mobile-standings-row:first-of-type
.mobile-standing-team {
    color: var(--gold);

    font-weight: 900;
}


/* =========================================================
   LOADER
========================================================= */

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

    border:
        6px solid rgba(226,188,91,.25);

    border-top:
        6px solid var(--gold);

    border-radius:
        50%;

    animation:
        legacy-spin 1s linear infinite;
}


.legacy-loader-text {
    margin-top:
        22px;

    font-size:
        1.35rem;

    font-weight:
        800;

    color:
        #F5EBD7;
}


@keyframes legacy-spin {

    0% {
        transform:
            rotate(0deg);
    }

    100% {
        transform:
            rotate(360deg);
    }
}


/* =========================================================
   PHONE
========================================================= */

@media (max-width: 700px) {

    .block-container {
        padding-top:
            1rem !important;

        padding-left:
            .75rem !important;

        padding-right:
            .75rem !important;

        padding-bottom:
            3rem !important;

        width:
            100% !important;

        max-width:
            100% !important;

        box-sizing:
            border-box !important;
    }


    .mobile-page-title {
        display:
            block !important;

        font-size:
            1.55rem;

        margin-top:
            .8rem;

        margin-bottom:
            1.25rem;
    }


    .section-title {
        font-size:
            1.1rem;

        margin-bottom:
            .65rem;
    }

/*
Hide the desktop leader cards on phone.
The dedicated 2x2 mobile leader grid is shown instead.
*/

.desktop-leaders {
    display: none !important;
}
    /*
    Mobile leaders on.
    */

    .mobile-leaders {
        display:
            block !important;
    }


    /*
    Main Streamlit standings dataframe only.
    */

    .st-key-main_standings_table {
        display:
            none !important;
    }


    .mobile-standings-wrapper {
        display:
            block !important;

        margin-bottom:
            1.5rem;
    }


    /*
    Existing Top 5 / Bottom 5 area remains intact.

    We intentionally do NOT hide these dataframes.
    Streamlit can stack the two columns vertically on phone,
    preserving the current presentation.
    */

    .divider {
        margin:
            14px 0 24px;
    }


    .legacy-loader-wrap {
        height:
            55vh;
    }


    .legacy-loader {
        width:
            52px;

        height:
            52px;

        border-width:
            5px;
    }


    .legacy-loader-text {
        margin-top:
            16px;

        font-size:
            .95rem;
    }


    /*
    Keep the bottom Top / Bottom 5 tables readable.
    Horizontal scrolling is allowed there because you explicitly
    want to keep their current rich layout.
    */

    div[data-testid="stDataFrame"] {
        border-radius:
            14px;

        padding:
            7px 7px 4px 7px;
    }

}


/* =========================================================
   DESKTOP
========================================================= */

@media (min-width: 701px) {

    .mobile-page-title {
        display:
            none !important;
    }


    .mobile-leaders {
        display:
            none !important;
    }


    .mobile-standings-wrapper {
        display:
            none !important;
    }

}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# HELPERS
# ============================================================

NUM_RE = re.compile(
    r"[^0-9.\-()]"
)


def _clean_num(value: str) -> str:

    value = (
        ""
        if value is None
        else str(value)
    ).strip()

    if not value:
        return ""

    negative = (
        value.startswith("(")
        and value.endswith(")")
    )

    value = (
        NUM_RE
        .sub("", value)
        .replace("(", "")
        .replace(")", "")
    )

    return (
        "-" + value
        if negative
        else value
    )


def as_int(value) -> int:

    try:
        return int(
            float(
                _clean_num(value)
            )
        )

    except Exception:
        return 0


def as_float(value) -> float:

    try:
        return float(
            _clean_num(value)
        )

    except Exception:
        return 0.0


def pick(
    columns: List[str],
    keys: List[str],
) -> Optional[str]:

    index = {
        column.lower().strip(): column
        for column in columns
    }

    for key in keys:

        if key in index:
            return index[key]

    return None


# ============================================================
# DERIVED METRICS
# ============================================================

def infer_weeks_played(
    df: pd.DataFrame,
) -> int:

    if (
        "Games" in df.columns
        and not df["Games"].empty
    ):
        return int(
            df["Games"].max()
        )

    if (
        "Wins" in df.columns
        and not df["Wins"].empty
    ):
        return max(
            int(df["Wins"].max()),
            1,
        )

    return 8


def add_meta_metrics(
    df: pd.DataFrame,
) -> pd.DataFrame:

    result = df.copy()


    if (
        "Standing Points" not in result.columns
        and "Points" in result.columns
    ):
        result["Standing Points"] = (
            result["Points"]
        )


    weeks = infer_weeks_played(
        result
    )


    for column, default in [
        ("PF", 0.0),
        ("PA", 0.0),
        ("Wins", 0),
        ("Top 5", 0),
    ]:

        if column not in result.columns:
            result[column] = default


    if "Losses" not in result.columns:

        result["Losses"] = (
            weeks - result["Wins"]
        ).clip(lower=0)


    result["PF Per Game"] = (
        (result["PF"] / weeks).round(1)
        if weeks > 0
        else 0.0
    )


    result["PA Per Game"] = (
        (result["PA"] / weeks).round(1)
        if weeks > 0
        else 0.0
    )


    total_standing_points = (
        result["Standing Points"].sum()
        if "Standing Points" in result.columns
        else 0
    )


    total_pf = result["PF"].sum()


    result["% of Total Standings Points"] = (
        (
            100.0
            * result["Standing Points"]
            / total_standing_points
        ).round(1)

        if total_standing_points
        else 0.0
    )


    result["% of Total Points Scored"] = (
        (
            100.0
            * result["PF"]
            / total_pf
        ).round(1)

        if total_pf
        else 0.0
    )


    ordered = [
        "Team",
        "Standing Points",
        "Wins",
        "Losses",
        "Top 5",
        "PF",
        "PF Per Game",
        "PA",
        "PA Per Game",
        "% of Total Standings Points",
        "% of Total Points Scored",
    ]


    return result[
        [
            column
            for column in ordered
            if column in result.columns
        ]
    ]


# ============================================================
# BADGE STYLES
# ============================================================

def standings_badge_style(
    df: pd.DataFrame,
) -> pd.DataFrame:

    styles = pd.DataFrame(
        "",
        index=df.index,
        columns=df.columns,
    )


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


# ============================================================
# DATA
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def load_standings_snapshot(
    league_id: str | None,
    refresh_key: int = 0,
) -> pd.DataFrame:

    ctx = get_app_context(
        force_refresh=refresh_key > 0
    )

    return (
        ctx
        .get(
            "stand_df",
            pd.DataFrame(),
        )
        .copy()
    )


# ============================================================
# ICONS
# ============================================================

LEADER_ICONS = {

    "Points":
        """
        <svg viewBox="0 0 24 24">
            <path d="M8 21h8"/>
            <path d="M12 17v4"/>
            <path d="M7 4h10v4a5 5 0 0 1-10 0V4Z"/>
            <path d="M7 6H4v1a4 4 0 0 0 4 4"/>
            <path d="M17 6h3v1a4 4 0 0 1-4 4"/>
        </svg>
        """,

    "PF":
        """
        <svg viewBox="0 0 24 24">
            <path d="M4 19V5"/>
            <path d="M4 19h16"/>
            <path d="m7 15 4-4 3 2 5-6"/>
            <path d="M16 7h3v3"/>
        </svg>
        """,

    "Wins":
        """
        <svg viewBox="0 0 24 24">
            <path d="M12 3 19 6v5c0 4.5-3 7.5-7 10-4-2.5-7-5.5-7-10V6l7-3Z"/>
            <path d="m9 12 2 2 4-5"/>
        </svg>
        """,

    "Top 5":
        """
        <svg viewBox="0 0 24 24">
            <path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6-5.4-2.9-5.4 2.9 1-6-4.4-4.3 6.1-.9L12 3Z"/>
        </svg>
        """,
}


# ============================================================
# MOBILE HTML HELPERS
# ============================================================

def _mobile_leaders_html(
    leaders: list[dict],
) -> str:

    cards = []


    for leader in leaders:

        label = html.escape(
            str(leader["label"])
        )

        team = html.escape(
            str(leader["team"])
        )

        value = html.escape(
            str(leader["value"])
        )

        href = html.escape(
            str(leader["href"]),
            quote=True,
        )

        icon = LEADER_ICONS.get(
            leader["label"],
            "",
        )


        cards.append(
            (
                '<div class="mobile-leader-card">'
                f'<a class="mobile-leader-link" href="{href}">'
                f'<div class="mobile-leader-icon">{icon}</div>'
                '<div class="mobile-leader-copy">'
                f'<div class="mobile-leader-label">{label}</div>'
                f'<div class="mobile-leader-team">{team}</div>'
                f'<div class="mobile-leader-number">{value}</div>'
                '</div>'
                '</a>'
                '</div>'
            )
        )


    return (
        '<div class="mobile-leaders">'
        '<div class="mobile-leader-grid">'
        f'{"".join(cards)}'
        '</div>'
        '</div>'
    )


def _mobile_standings_html(
    standings_df: pd.DataFrame,
) -> str:

    rows = []


    for index, row in standings_df.iterrows():

        team = html.escape(
            str(row.get("Team", ""))
        )

        standing_points = as_int(
            row.get("Standing Points", 0)
        )

        pf = as_int(
            row.get("PF", 0)
        )

        wins = as_int(
            row.get("Wins", 0)
        )

        top5 = as_int(
            row.get("Top 5", 0)
        )


        rows.append(
            (
                '<div class="mobile-standings-row">'
                f'<div class="mobile-standing-rank">{index + 1}</div>'
                f'<div class="mobile-standing-team">{team}</div>'
                f'<div class="mobile-standing-number">{standing_points}</div>'
                f'<div class="mobile-standing-number">{pf}</div>'
                f'<div class="mobile-standing-number">{wins}</div>'
                f'<div class="mobile-standing-number">{top5}</div>'
                '</div>'
            )
        )


    return (
        '<div class="mobile-standings-wrapper">'
        '<div class="mobile-standings-card">'
        '<div class="mobile-standings-header">'
        '<div>#</div>'
        '<div>TEAM</div>'
        '<div>SP</div>'
        '<div>PF</div>'
        '<div>W</div>'
        '<div>T5</div>'
        '</div>'
        f'{"".join(rows)}'
        '</div>'
        '</div>'
    )


# ============================================================
# PAGE RENDER
# ============================================================

league_id = st.session_state.get(
    "active_league_id"
)


if "standings_refresh_key" not in st.session_state:

    st.session_state[
        "standings_refresh_key"
    ] = 0


spinner_placeholder = st.empty()


spinner_placeholder.markdown(
    (
        '<div class="legacy-loader-wrap">'
        '<div class="legacy-loader"></div>'
        '<div class="legacy-loader-text">'
        'Loading Season Standings...'
        '</div>'
        '</div>'
    ),
    unsafe_allow_html=True,
)

live_df = load_standings_snapshot(
    league_id,
    0,
)


spinner_placeholder.empty()


if live_df.empty:

    live_df = pd.DataFrame(
        {
            "Team": [
                "Chase Seyforth",
                "Chasen Hardy",
                "Connor Cassidy",
                "Dylan Burruel",
                "Grady Graham",
                "Kevin Wells",
                "Mekel Sanchez",
                "Nando Munoz",
                "Nick Salafia",
                "Tommy Bruggeman",
            ],

            "Standing Points": [0] * 10,
            "PF": [0] * 10,
            "PA": [0] * 10,
            "Wins": [0] * 10,
            "Top 5": [0] * 10,
            "Games": [0] * 10,
        }
    )


    st.info(
        "No completed Sleeper matchups yet. "
        "Showing preseason standings."
    )


# ============================================================
# PREP DATA
# ============================================================

base_df = (
    live_df
    .rename(
        columns={
            "Standing Points": "Points"
        }
    )[
        [
            "Team",
            "Points",
            "PF",
            "Wins",
            "Top 5",
        ]
    ]
)


meta_full = add_meta_metrics(
    live_df
)


for column in meta_full.columns:

    if pd.api.types.is_numeric_dtype(
        meta_full[column]
    ):

        meta_full[column] = (
            meta_full[column]
            .round(0)
            .astype(int)
        )


# ============================================================
# MOBILE PAGE TITLE
# ============================================================

st.markdown(
    '<div class="mobile-page-title">Standings</div>',
    unsafe_allow_html=True,
)


# ============================================================
# LEADERS
# ============================================================

st.markdown(
    '<div class="section-title">Leaders</div>',
    unsafe_allow_html=True,
)


def _leader_row(
    df: pd.DataFrame,
    column: str,
    ascending: bool = False,
) -> Tuple[str, float | int]:

    if (
        column not in df.columns
        or df.empty
    ):
        return "—", 0


    row = (
        df
        .sort_values(
            column,
            ascending=ascending,
        )
        .iloc[0]
    )


    return (
        str(row["Team"]),
        row[column],
    )


leader_specs = [
    ("Points", "Points", False),
    ("PF", "PF", False),
    ("Wins", "Wins", False),
    ("Top 5", "Top 5", False),
]


leader_results = []


for label, column, ascending in leader_specs:

    team, raw_value = _leader_row(
        base_df,
        column,
        ascending,
    )


    if isinstance(
        raw_value,
        (int, float),
    ):

        value_string = str(
            int(
                round(
                    float(raw_value)
                )
            )
        )

    else:
        value_string = str(
            raw_value
        )


    if team == "—":

        target_href = "#"

    else:

        target_href = (
            "/?page=teams&team="
            f"{urlparse.quote(team)}"
        )


    leader_results.append(
        {
            "label": label,
            "team": team,
            "value": value_string,
            "href": target_href,
        }
    )


# ------------------------------------------------------------
# MOBILE LEADERS
# ------------------------------------------------------------

st.markdown(
    _mobile_leaders_html(
        leader_results
    ),
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# DESKTOP LEADERS
# ------------------------------------------------------------

desktop_cards = []


for leader in leader_results:

    label = html.escape(
        str(leader["label"])
    )

    team = html.escape(
        str(leader["team"])
    )

    value = html.escape(
        str(leader["value"])
    )

    href = html.escape(
        str(leader["href"]),
        quote=True,
    )

    icon = LEADER_ICONS.get(
        leader["label"],
        "",
    )


    desktop_cards.append(
        (
            '<div class="desktop-leader-card">'
            f'<div class="desktop-leader-icon">{icon}</div>'
            '<div class="desktop-leader-copy">'
            f'<div class="desktop-leader-label">{label}</div>'
            f'<a class="desktop-leader-team" href="{href}">{team}</a>'
            f'<div class="desktop-leader-number">{value}</div>'
            '</div>'
            '</div>'
        )
    )


st.markdown(
    (
        '<div class="desktop-leaders">'
        '<div class="desktop-leader-grid">'
        f'{"".join(desktop_cards)}'
        '</div>'
        '</div>'
    ),
    unsafe_allow_html=True,
)

# ============================================================
# MAIN STANDINGS
# ============================================================

stand_columns = [
    "Team",
    "Standing Points",
    "PF",
    "Wins",
    "Top 5",
]


available = [
    column
    for column in stand_columns
    if column in meta_full.columns
]


standings_df = (
    meta_full[available]
    .sort_values(
        [
            "Standing Points",
            "PF",
            "Wins",
            "Top 5",
        ],
        ascending=[
            False,
            False,
            False,
            False,
        ],
    )
    .reset_index(drop=True)
)


standings_styler = (
    standings_df
    .style
    .apply(
        standings_badge_style,
        axis=None,
    )
)


column_config = {}


try:

    from streamlit import column_config as cc


    if "Standing Points" in standings_df.columns:

        column_config[
            "Standing Points"
        ] = cc.NumberColumn(
            "Standing Points",
            format="%d",
            width="small",
        )


    if "PF" in standings_df.columns:

        column_config[
            "PF"
        ] = cc.NumberColumn(
            "PF",
            format="%.0f",
            width="small",
        )


    if "Wins" in standings_df.columns:

        column_config[
            "Wins"
        ] = cc.NumberColumn(
            "Wins",
            format="%d",
            width="small",
        )


    if "Top 5" in standings_df.columns:

        column_config[
            "Top 5"
        ] = cc.NumberColumn(
            "Top 5",
            format="%d",
            width="small",
        )


except Exception:
    pass


st.markdown(
    '<div class="section-title">Standings</div>',
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# MOBILE STANDINGS
# ------------------------------------------------------------

st.markdown(
    _mobile_standings_html(
        standings_df
    ),
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# DESKTOP STANDINGS
# ------------------------------------------------------------

with st.container(
    key="main_standings_table",
):

    st.dataframe(
        standings_styler,
        hide_index=True,
        use_container_width=True,
        column_config=column_config,
    )


st.markdown(
    '<div class="divider"></div>',
    unsafe_allow_html=True,
)


# ============================================================
# TOP / BOTTOM 5
# ============================================================

RANK_BY = "Standing Points"


def _condense_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:

    return df.rename(
        columns={
            "Standing Points": "SP",
            "Wins": "Win",
            "Losses": "Loss",
            "PF Per Game": "PF / Game",
            "PA Per Game": "PA / Game",
            "% of Total Standings Points":
                "% of Total SP",
            "% of Total Points Scored":
                "% of Total Points",
        }
    )


left, right = st.columns(
    2,
    gap="large",
)


# ------------------------------------------------------------
# TOP 5
# ------------------------------------------------------------

with left:

    top5 = (
        meta_full
        .sort_values(
            RANK_BY,
            ascending=False,
        )
        .head(5)
        .reset_index(drop=True)
    )


    columns_out = [
        column
        for column in [
            "Team",
            "Standing Points",
            "Wins",
            "Losses",
            "Top 5",
            "PF",
            "PF Per Game",
            "PA",
            "PA Per Game",
            "% of Total Standings Points",
            "% of Total Points Scored",
        ]
        if column in top5.columns
    ]


    top5_df = top5[
        columns_out
    ]


    top5_df = _condense_columns(
        top5_df
    )


    top5_styler = (
        top5_df
        .style
        .apply(
            standings_badge_style,
            axis=None,
        )
    )


    st.markdown(
        f'<div class="section-title">'
        f'Top 5 ({RANK_BY})'
        f'</div>',
        unsafe_allow_html=True,
    )


    st.dataframe(
        top5_styler,
        use_container_width=True,
        hide_index=True,
    )


# ------------------------------------------------------------
# BOTTOM 5
# ------------------------------------------------------------

with right:

    bottom5 = (
        meta_full
        .sort_values(
            RANK_BY,
            ascending=True,
        )
        .head(5)
        .reset_index(drop=True)
    )


    columns_out = [
        column
        for column in [
            "Team",
            "Standing Points",
            "Wins",
            "Losses",
            "Top 5",
            "PF",
            "PF Per Game",
            "PA",
            "PA Per Game",
            "% of Total Standings Points",
            "% of Total Points Scored",
        ]
        if column in bottom5.columns
    ]


    bottom5_df = bottom5[
        columns_out
    ]


    bottom5_df = _condense_columns(
        bottom5_df
    )


    bottom5_styler = (
        bottom5_df
        .style
        .apply(
            standings_badge_style,
            axis=None,
        )
    )


    st.markdown(
        f'<div class="section-title">'
        f'Bottom 5 ({RANK_BY})'
        f'</div>',
        unsafe_allow_html=True,
    )


    st.dataframe(
        bottom5_styler,
        use_container_width=True,
        hide_index=True,
    )