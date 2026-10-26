# pages/02_Weekly_Matchups.py
# ------------------------------------------------------------
# Weekly Matchups
# Desktop keeps the existing dataframe presentation.
# Phones get one card per game (services/matchups_view.py),
# with owner names, weekly rank and the signed-in owner's game first.
# ------------------------------------------------------------

import html
import os
import sys
from pathlib import Path
from typing import Dict

import pandas as pd
import requests
import streamlit as st

from auth import require_login, current_user, _sb
from components.sidebar_nav import render_nav
from season_engine import SeasonResolver
from services.app_context import (
    current_nfl_week,
    get_user_id,
    last_scored_week,
    resolve_my_team,
)
from services.matchups_view import build_games, matchup_cards_html


ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — Weekly Matchups",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()


# ============================================================
# Path prelude
# ============================================================

PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(PAGES_DIR, ".."))

sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, "lib"))


require_login("home.py")

u = current_user()

access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")

league_id = (
    st.session_state.get("active_league_id")
    or st.session_state.get("import_league_id")
)

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

SLEEPER_LEAGUE_ID = str(
    ACTIVE_SEASON.sleeper_league_id or ""
).strip()

SLEEPER_LEAGUE_ID = "".join(
    ch for ch in SLEEPER_LEAGUE_ID
    if ch.isdigit()
)

if not SLEEPER_LEAGUE_ID:
    st.error("This league does not have a Sleeper league connected yet.")
    st.stop()


# ============================================================
# HTTP helpers
# ============================================================

def _get(url: str):
    response = requests.get(url, timeout=25)
    response.raise_for_status()
    return response.json()


# ============================================================
# Name mapping
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def _roster_id_to_owner_name(
    app_league_id: str,
    sleeper_league_id: str,
) -> Dict[int, str]:
    """
    roster_id -> owner name, matched by Sleeper roster id.

    Prefers the owner name from league_teams, then the app team
    name, then the Sleeper display name.
    """

    app_map: Dict[int, str] = {}

    for table, columns in (
        ("teams", "sleeper_roster_id,team_name"),
        ("league_teams", "sleeper_roster_id,owner_name,team_name"),
    ):
        try:
            rows = (
                sb.table(table)
                .select(columns)
                .eq("league_id", app_league_id)
                .execute()
                .data
                or []
            )

        except Exception:
            rows = []

        for row in rows:
            name = str(
                row.get("owner_name")
                or row.get("team_name")
                or ""
            ).strip()

            try:
                roster_id = int(row.get("sleeper_roster_id"))
            except (TypeError, ValueError):
                continue

            if name:
                app_map[roster_id] = name


    users = _get(
        f"https://api.sleeper.app/v1/league/"
        f"{sleeper_league_id}/users"
    )

    rosters = _get(
        f"https://api.sleeper.app/v1/league/"
        f"{sleeper_league_id}/rosters"
    )


    uid_to_display_name = {
        user["user_id"]: (
            user.get("display_name")
            or user.get("username")
            or ""
        ).strip()
        for user in users
    }


    roster_to_name: Dict[int, str] = {}

    for roster in rosters:
        roster_id = roster.get("roster_id")
        owner_id = roster.get("owner_id")

        roster_to_name[roster_id] = app_map.get(
            roster_id,
            uid_to_display_name.get(
                owner_id,
                f"Roster {roster_id}",
            ),
        )

    return roster_to_name


# ============================================================
# Data fetch + ranking
# ============================================================

@st.cache_data(
    ttl=60,
    show_spinner=False,
)
def load_weekly_games_cached(
    app_league_id: str,
    sleeper_league_id: str,
    week: int,
) -> list:

    names = _roster_id_to_owner_name(
        app_league_id,
        sleeper_league_id,
    )

    rows = _get(
        f"https://api.sleeper.app/v1/league/"
        f"{sleeper_league_id}/matchups/{week}"
    ) or []

    return build_games(rows, names)


def _games_to_frame(
    games: list,
) -> pd.DataFrame:
    """Desktop table: one row per game, leader on the left."""

    columns = [
        "Weekly Rank (A)",
        "Team A",
        "Score A",
        "Team B",
        "Score B",
        "Weekly Rank (B)",
    ]

    rows = []

    for game in games:
        team_a, team_b = game["teams"]

        rows.append(
            {
                "Weekly Rank (A)": team_a["rank"],
                "Team A": team_a["name"],
                "Score A": team_a["score"],
                "Team B": team_b["name"],
                "Score B": team_b["score"],
                "Weekly Rank (B)": team_b["rank"],
            }
        )

    return pd.DataFrame(rows, columns=columns)


def _my_roster_id():
    """The signed-in owner's Sleeper roster id, or None.
    Looked up once per session."""

    cache_key = f"matchups_my_roster_id:{league_id}"

    if cache_key not in st.session_state:
        st.session_state[cache_key] = _lookup_my_roster_id()

    return st.session_state[cache_key]


def _lookup_my_roster_id():
    ctx = st.session_state.get("app_context") or {}
    my_team = ctx.get("my_team") if isinstance(ctx, dict) else None

    if not isinstance(my_team, dict):
        try:
            my_team = resolve_my_team(
                get_user_id(),
                league_id,
            ) or {}
        except Exception:
            my_team = {}

    team_id = my_team.get("team_id")

    if not team_id:
        return None

    try:
        rows = (
            sb.table("league_teams")
            .select("sleeper_roster_id")
            .eq("id", team_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return None

    return rows[0].get("sleeper_roster_id") if rows else None


# ============================================================
# STYLE
# ============================================================

st.markdown(
    """
<style>

:root {
    --bg: #061311;
    --panel: #101E1D;
    --panel-2: #0C1917;
    --gold: #E2BC5B;
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
   App chrome
========================================================= */

@import url(
    'https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap'
);


html,
body,
[data-testid="stAppViewContainer"],
[data-testid="stSidebar"] {
    font-family: 'Lato', sans-serif !important;
    background: var(--bg);
    color: var(--text);
}


/* =========================================================
   Streamlit header / mobile navigation
========================================================= */

[data-testid="stHeader"] {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    background: transparent !important;
    pointer-events: none !important;
}

[data-testid="stToolbar"] {
    display: none !important;
}

/* Sidebar reopen control must remain usable on mobile. */
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    pointer-events: auto !important;
    position: fixed !important;
    top: 0.75rem !important;
    left: 0.75rem !important;
    z-index: 999999 !important;
}

[data-testid="stHeader"] button {
    visibility: visible !important;
    opacity: 1 !important;
    pointer-events: auto !important;
}

.block-container {
    padding-top: 64px;
}


.block-container .stColumns {
    gap: 12px !important;
}


/* =========================================================
   Header
========================================================= */

.matchups-page-title {
    color: var(--text);

    font-size: 2rem;

    font-weight: 900;

    line-height: 1.1;

    letter-spacing: -.03em;

    padding-top: .35rem;
}


div[data-testid="stSelectbox"] {
    margin-bottom: .35rem;
}


div[data-testid="stSelectbox"] > div > div {
    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    border-color: var(--gold-soft);

    border-radius: 12px;
}


/* =========================================================
   Desktop dataframe
========================================================= */

div[data-testid="stDataFrame"] {
    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    border: 1px solid var(--gold-soft);

    border-radius: 16px;

    box-shadow: var(--shadow-amb);

    padding: 8px 8px 2px 8px;

    position: relative;
}


div[data-testid="stDataFrame"]::before {
    content: "";

    position: absolute;

    left: 6px;
    right: 6px;
    top: 6px;

    height: 2px;

    border-radius: 2px;

    background:
        linear-gradient(
            90deg,
            rgba(226,188,91,0) 0%,
            rgba(226,188,91,.35) 22%,
            rgba(226,188,91,.6) 50%,
            rgba(226,188,91,.35) 78%,
            rgba(226,188,91,0) 100%
        );

    pointer-events: none;
}


div[data-testid="stDataFrame"]
table thead tr th {
    position: sticky;

    top: 0;

    z-index: 2;

    background: var(--panel-2);

    border-bottom:
        1px solid var(--gold-soft);
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


/* =========================================================
   Phone matchup cards
   One card per game: two even rows (rank, owner, score) and a
   status line. Shown only under 700px; desktop keeps the table.
========================================================= */

.mobile-matchups-wrapper {
    display: none;
}


.mm-summary {
    display: grid;

    grid-template-columns:
        repeat(3, minmax(0, 1fr));

    gap: 8px;

    margin-bottom: 12px;
}


.mm-box {
    min-width: 0;

    padding: 9px 6px 8px;

    border:
        1px solid rgba(226,188,91,.22);

    border-radius: 12px;

    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    text-align: center;
}


.mm-box span,
.mm-box b,
.mm-box em {
    display: block;

    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;
}


.mm-box span {
    color: var(--muted);

    font-size: .56rem;

    font-weight: 800;

    letter-spacing: .07em;

    text-transform: uppercase;
}


.mm-box b {
    margin-top: 3px;

    color: var(--gold);

    font-size: .98rem;

    font-weight: 900;

    font-variant-numeric: tabular-nums;
}


.mm-box em {
    margin-top: 1px;

    color: var(--text);

    font-size: .66rem;

    font-style: normal;

    font-weight: 600;

    opacity: .85;
}


.mm-card {
    margin-bottom: 10px;

    overflow: hidden;

    border:
        1px solid var(--gold-soft);

    border-radius: 14px;

    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    box-shadow:
        var(--shadow-amb);
}


.mm-card.is-mine {
    border-color: var(--gold);

    box-shadow:
        0 0 0 1px rgba(226,188,91,.25),
        var(--shadow-amb);
}


.mm-label {
    padding: 7px 14px 0;

    color: var(--gold);

    font-size: .58rem;

    font-weight: 900;

    letter-spacing: .1em;

    text-transform: uppercase;
}


.mm-team {
    display: grid;

    grid-template-columns:
        26px
        minmax(0, 1fr)
        72px;

    align-items: center;

    column-gap: 10px;

    min-height: 46px;

    padding: 0 14px 0 12px;

    border-bottom:
        1px solid rgba(226,188,91,.09);
}


.mm-rank {
    width: 24px;

    height: 24px;

    line-height: 24px;

    margin: 0 auto;

    border-radius: 50%;

    background: rgba(255,255,255,.06);

    color: var(--muted);

    font-size: .7rem;

    font-weight: 800;

    text-align: center;
}


.mm-rank.top5 {
    background: var(--gold);

    color: #0C1917;

    font-weight: 900;
}


.mm-name {
    display: flex;

    align-items: center;

    min-width: 0;

    color: var(--text);

    font-size: .9rem;

    font-weight: 700;
}


.mm-name-text {
    min-width: 0;

    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;
}


.mm-you {
    flex-shrink: 0;

    margin-left: 7px;

    padding: 1px 6px;

    border-radius: 999px;

    background: rgba(226,188,91,.16);

    color: var(--gold);

    font-size: .56rem;

    font-weight: 900;

    letter-spacing: .06em;

    text-transform: uppercase;
}


.mm-score {
    color: var(--text);

    font-size: .95rem;

    font-weight: 700;

    font-variant-numeric: tabular-nums;

    text-align: right;
}


.mm-team.is-winner .mm-name-text,
.mm-team.is-winner .mm-score {
    color: var(--gold);

    font-weight: 900;
}


.mm-team:not(.is-winner) .mm-score {
    opacity: .8;
}


.mm-status {
    display: flex;

    align-items: center;

    justify-content: space-between;

    gap: 8px;

    min-height: 32px;

    padding: 0 14px;

    background: rgba(255,255,255,.03);

    color: var(--muted);

    font-size: .7rem;

    font-weight: 700;
}


.mm-badge {
    flex-shrink: 0;

    padding: 2px 8px;

    border-radius: 999px;

    font-size: .56rem;

    font-weight: 900;

    letter-spacing: .08em;

    text-transform: uppercase;
}


.mm-badge.final {
    background: rgba(255,255,255,.07);

    color: var(--muted);
}


.mm-badge.live {
    background: rgba(74,222,128,.14);

    color: #4ADE80;
}


.mm-badge.upcoming {
    background: rgba(226,188,91,.12);

    color: var(--gold);
}


.mm-hint {
    margin-top: 2px;

    color: var(--muted);

    font-size: .68rem;

    text-align: center;
}


/* =========================================================
   Loading
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
        6px solid #E2BC5B;

    border-radius: 50%;

    animation:
        legacy-spin 1s linear infinite;

    box-shadow:
        0 0 24px rgba(226,188,91,.18);
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
    0% {
        transform: rotate(0deg);
    }

    100% {
        transform: rotate(360deg);
    }
}


/* =========================================================
   Empty state
========================================================= */

.empty-matchups-card {
    margin-top: 28px;

    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    border:
        1px solid var(--gold-soft);

    border-radius: 18px;

    box-shadow:
        var(--shadow-amb);

    padding:
        34px 28px;

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

    margin:
        0 auto;

    line-height: 1.5;
}


/* =========================================================
   PHONE
========================================================= */

@media (max-width: 700px) {

    .block-container {
        padding-top: 1rem !important;

        padding-left: .65rem !important;

        padding-right: .65rem !important;

        padding-bottom: 3rem !important;

        max-width: 100% !important;

        width: 100% !important;

        box-sizing: border-box !important;
    }


    /*
       Keep title and week dropdown on one line.
    */

    div[data-testid="stHorizontalBlock"] {
        display: grid !important;

        grid-template-columns:
            minmax(0, 1fr) 104px !important;

        gap: 8px !important;

        align-items: center !important;

        width: 100% !important;

        max-width: 100% !important;
    }


    div[data-testid="stHorizontalBlock"]
    > div[data-testid="stColumn"] {
        width: 100% !important;

        min-width: 0 !important;

        max-width: 100% !important;

        flex: none !important;
    }


    .matchups-page-title {
        font-size: 1.35rem;

        line-height: 1.1;

        padding-top: 0;

        white-space: nowrap;
    }


    div[data-testid="stSelectbox"] {
        width: 104px !important;

        max-width: 104px !important;

        margin:
            0 !important;
    }


    div[data-testid="stSelectbox"] > div {
        width: 100% !important;
    }


    div[data-testid="stSelectbox"] > div > div {
        min-height: 38px !important;

        width: 100% !important;

        border-radius: 10px !important;

        font-size: .76rem !important;
    }


    /*
       Hide the normal Streamlit desktop dataframe.
       This page has only one dataframe.
    */

    div[data-testid="stDataFrame"] {
        display: none !important;
    }


    /*
       Show compact matchup table.
    */

    .mobile-matchups-wrapper {
        display: block !important;

        width: 100% !important;

        max-width: 100% !important;

        margin-top: .7rem;

        overflow: hidden;
    }


    .legacy-loader-wrap {
        height: 55vh;
    }


    .legacy-loader {
        width: 52px;

        height: 52px;

        border-width: 5px;
    }


    .legacy-loader-text {
        margin-top: 16px;

        font-size: .95rem;
    }


    .empty-matchups-card {
        margin-top: .7rem;

        padding:
            22px 14px;

        border-radius: 14px;
    }


    .empty-title {
        font-size: 1rem;
    }


    .empty-subtitle {
        font-size: .82rem;
    }
}


/* =========================================================
   DESKTOP
========================================================= */

@media (min-width: 701px) {

    .mobile-matchups-wrapper {
        display: none !important;
    }

}

</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# Loading component
# ============================================================

def show_loading_screen(
    placeholder,
    text: str = "Loading Weekly Matchups...",
):

    placeholder.markdown(
        (
            '<div class="legacy-loader-wrap">'
            '<div class="legacy-loader"></div>'
            f'<div class="legacy-loader-text">{html.escape(text)}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


# ============================================================
# UI
# ============================================================

if "weekly_wk" not in st.session_state:
    # Open on the week being played now, not week 1.
    st.session_state["weekly_wk"] = current_nfl_week()
    st.session_state["weekly_wk_origin"] = "auto"


# ============================================================
# Header
# ============================================================

title_col, week_col = st.columns(
    [3, 1]
)


with title_col:

    st.markdown(
        '<div class="matchups-page-title">Weekly Matchups</div>',
        unsafe_allow_html=True,
    )


with week_col:

    week_options = list(
        range(1, 26)
    )


    current_week = int(
        st.session_state["weekly_wk"]
    )


    current_index = (
        week_options.index(current_week)
        if current_week in week_options
        else 0
    )


    week = st.selectbox(
        "Week",
        options=week_options,
        index=current_index,
        format_func=lambda value: f"Week {value}",
        key="weekly_week_picker",
        label_visibility="collapsed",
    )


    if int(week) != int(
        st.session_state["weekly_wk"]
    ):

        st.session_state["weekly_wk"] = int(
            week
        )

        st.session_state["weekly_wk_origin"] = (
            "user"
        )


# ============================================================
# Load matchups
# ============================================================

try:

    loading_box = st.empty()


    show_loading_screen(
        loading_box,
        "Loading Weekly Matchups...",
    )


    selected_week = int(st.session_state["weekly_wk"])

    games = load_weekly_games_cached(
        str(league_id),
        SLEEPER_LEAGUE_ID,
        selected_week,
    )

    df = _games_to_frame(games)


    loading_box.empty()


    # ========================================================
    # Empty week
    # ========================================================

    if df.empty:

        st.markdown(
            (
                '<div class="empty-matchups-card">'
                '<div class="empty-title">'
                'No matchups found for this week yet'
                '</div>'
                '<div class="empty-subtitle">'
                'Sleeper may not have generated the official schedule '
                'for this week. Once matchups are available, this page '
                'will automatically display them here.'
                '</div>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )


    # ========================================================
    # Matchups available
    # ========================================================

    else:

        df["Score A"] = (
            df["Score A"]
            .astype(float)
            .round(2)
        )


        df["Score B"] = (
            df["Score B"]
            .astype(float)
            .round(2)
        )


        # ----------------------------------------------------
        # MOBILE TABLE
        # ----------------------------------------------------

        try:
            week_is_final = selected_week <= int(
                last_scored_week(SLEEPER_LEAGUE_ID)
            )
        except Exception:
            week_is_final = False


        mobile_grid = matchup_cards_html(
            games,
            my_roster_id=_my_roster_id(),
            is_final=week_is_final,
        )


        st.markdown(
            mobile_grid,
            unsafe_allow_html=True,
        )


        # ----------------------------------------------------
        # DESKTOP TABLE
        # ----------------------------------------------------

        GOLD = "#E2BC5B"

        GOLD_SOFT = (
            "rgba(226,188,91,.35)"
        )

        PANEL = "#101E1D"

        PANEL_2 = "#0C1917"


        SCORE_BADGE = (
            "background: radial-gradient("
            "120% 140% at 20% 0%, "
            "{p1} 0%, {p2} 100%);"
            "border: 1px solid {border};"
            "border-radius: 10px;"
            "padding: 2px 8px;"
            "display: inline-block;"
            "box-shadow: 0 2px 10px rgba(0,0,0,.20);"
            "text-align: right;"
            "font-weight: 600;"
        ).format(
            p1=PANEL,
            p2=PANEL_2,
            border=GOLD_SOFT,
        )


        WINNER_BADGE = (
            "background: radial-gradient("
            "120% 140% at 20% 0%, "
            "{p1} 0%, {p2} 100%);"
            "border: 1px solid {border};"
            "border-radius: 10px;"
            "padding: 2px 8px;"
            "display: inline-block;"
            "box-shadow: 0 2px 14px rgba(226,188,91,.25);"
            "text-align: right;"
            "font-weight: 800;"
            "color: {gold};"
        ).format(
            p1=PANEL,
            p2=PANEL_2,
            border=GOLD,
            gold=GOLD,
        )


        base_styles = pd.DataFrame(
            "",
            index=df.index,
            columns=df.columns,
        )


        base_styles.loc[
            :,
            "Score A",
        ] = SCORE_BADGE


        base_styles.loc[
            :,
            "Score B",
        ] = SCORE_BADGE


        for index, row in df.iterrows():

            try:

                score_a = float(
                    row["Score A"]
                )

                score_b = float(
                    row["Score B"]
                )


                if score_a > score_b:

                    base_styles.at[
                        index,
                        "Score A",
                    ] = WINNER_BADGE


                elif score_b > score_a:

                    base_styles.at[
                        index,
                        "Score B",
                    ] = WINNER_BADGE


            except Exception:
                pass


        styler = (
            df.style

            .set_table_styles(
                [
                    {
                        "selector": "tbody td",
                        "props": [
                            (
                                "padding",
                                "6px 10px",
                            )
                        ],
                    },

                    {
                        "selector": "thead th",
                        "props": [
                            (
                                "padding",
                                "8px 10px",
                            )
                        ],
                    },
                ]
            )

            .format(
                {
                    "Score A": "{:.2f}",
                    "Score B": "{:.2f}",
                }
            )

            .set_properties(
                subset=[
                    "Team A",
                    "Team B",
                ],
                **{
                    "text-align": "left"
                },
            )

            .set_properties(
                subset=[
                    "Weekly Rank (A)",
                    "Weekly Rank (B)",
                ],
                **{
                    "text-align": "right"
                },
            )

            .apply(
                lambda _: base_styles,
                axis=None,
            )
        )


        st.dataframe(
            styler,
            use_container_width=True,
            hide_index=True,

            column_config={

                "Weekly Rank (A)":
                    st.column_config.NumberColumn(
                        "Weekly Rank",
                        width="small",
                    ),

                "Team A":
                    st.column_config.TextColumn(
                        "",
                        width="medium",
                    ),

                "Score A":
                    st.column_config.NumberColumn(
                        "Score",
                        format="%.2f",
                    ),

                "Team B":
                    st.column_config.TextColumn(
                        "",
                        width="medium",
                    ),

                "Score B":
                    st.column_config.NumberColumn(
                        "Score",
                        format="%.2f",
                    ),

                "Weekly Rank (B)":
                    st.column_config.NumberColumn(
                        "Weekly Rank",
                        width="small",
                    ),
            },
        )


except Exception as e:

    st.error(
        f"Failed to load matchups: {e}"
    )