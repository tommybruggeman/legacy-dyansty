# pages/02_Weekly_Matchups.py
# ------------------------------------------------------------
# Weekly Matchups
# Desktop keeps the existing dataframe presentation.
# Mobile uses a purpose-built compact matchup grid.
# Data fetching / ranking logic unchanged.
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


def _as_number(value):
    try:
        if value is None:
            return 0.0

        if isinstance(value, (int, float)):
            return float(value)

        return float(str(value))

    except Exception:
        return 0.0


# ============================================================
# Name mapping
# ============================================================

def _roster_id_to_appname(league_id: str) -> Dict[int, str]:
    """
    roster_id -> Team name from our app database.

    Fallback to Sleeper display name if the roster is not in
    our teams table yet.
    """

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
            roster_id = row.get("sleeper_roster_id")
            team_name = str(
                row.get("team_name") or ""
            ).strip()

            if roster_id is not None and team_name:
                app_map[int(roster_id)] = team_name

    except Exception:
        app_map = {}


    users = _get(
        f"https://api.sleeper.app/v1/league/"
        f"{SLEEPER_LEAGUE_ID}/users"
    )

    rosters = _get(
        f"https://api.sleeper.app/v1/league/"
        f"{SLEEPER_LEAGUE_ID}/rosters"
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

        if roster_id in app_map:
            roster_to_name[roster_id] = app_map[roster_id]

        else:
            roster_to_name[roster_id] = uid_to_display_name.get(
                owner_id,
                f"Roster {roster_id}",
            )

    return roster_to_name


# ============================================================
# Data fetch + ranking
# ============================================================

def _fetch_week(
    league_id: str,
    week: int,
) -> pd.DataFrame:

    columns = [
        "Weekly Rank (A)",
        "Team A",
        "Score A",
        "Team B",
        "Score B",
        "Weekly Rank (B)",
    ]


    roster_to_name = _roster_id_to_appname(
        league_id
    )

    rows = _get(
        f"https://api.sleeper.app/v1/league/"
        f"{league_id}/matchups/{week}"
    ) or []


    by_matchup = {}

    for row in rows:
        matchup_id = row.get("matchup_id")

        if matchup_id is None:
            continue

        by_matchup.setdefault(
            matchup_id,
            [],
        ).append(row)


    output = []
    all_scores = []


    for matchup_id, games in by_matchup.items():

        if len(games) < 2:
            continue


        team_a_raw, team_b_raw = (
            games[0],
            games[1],
        )


        roster_a = team_a_raw.get("roster_id")
        roster_b = team_b_raw.get("roster_id")


        name_a = roster_to_name.get(
            roster_a,
            f"Roster {roster_a}",
        )

        name_b = roster_to_name.get(
            roster_b,
            f"Roster {roster_b}",
        )


        points_a = _as_number(
            team_a_raw.get("points", 0.0)
        )

        points_b = _as_number(
            team_b_raw.get("points", 0.0)
        )


        starters_a = _as_number(
            team_a_raw.get(
                "starters_points",
                0.0,
            )
        )

        starters_b = _as_number(
            team_b_raw.get(
                "starters_points",
                0.0,
            )
        )


        if points_a < 10.0 <= starters_a:
            points_a = starters_a

        if points_b < 10.0 <= starters_b:
            points_b = starters_b


        all_scores.append(
            (name_a, points_a)
        )

        all_scores.append(
            (name_b, points_b)
        )


        output.append(
            {
                "Team A": name_a,
                "Score A": round(points_a, 2),
                "Team B": name_b,
                "Score B": round(points_b, 2),
            }
        )


    if not output:
        return pd.DataFrame(
            columns=columns
        )


    rank_map = {}

    if all_scores:

        score_frame = pd.DataFrame(
            all_scores,
            columns=[
                "Team",
                "Score",
            ],
        )

        score_frame["Rank"] = (
            score_frame["Score"]
            .rank(
                method="min",
                ascending=False,
            )
            .astype(int)
        )

        rank_map = dict(
            zip(
                score_frame["Team"],
                score_frame["Rank"],
            )
        )


    for row in output:

        row["Weekly Rank (A)"] = (
            rank_map.get(row["Team A"])
        )

        row["Weekly Rank (B)"] = (
            rank_map.get(row["Team B"])
        )


    df = pd.DataFrame(output)

    df = df[columns]

    df = (
        df.sort_values(
            [
                "Weekly Rank (A)",
                "Weekly Rank (B)",
            ],
            na_position="last",
        )
        .reset_index(drop=True)
    )

    return df


@st.cache_data(
    ttl=60,
    show_spinner=False,
)
def load_weekly_matchups_cached(
    league_id: str,
    week: int,
) -> pd.DataFrame:

    return _fetch_week(
        league_id,
        week,
    )


def _find_latest_week_with_data(
    league_id: str,
    max_week: int = 25,
) -> int:

    for week in range(
        max_week,
        0,
        -1,
    ):
        try:
            data = _get(
                f"https://api.sleeper.app/v1/league/"
                f"{league_id}/matchups/{week}"
            ) or []

            if data:
                return week

        except Exception:
            continue

    return 1


# ============================================================
# Mobile matchup renderer
# ============================================================

def _mobile_matchup_grid(
    df: pd.DataFrame,
) -> str:

    rows = []


    for _, row in df.iterrows():

        team_a = html.escape(
            str(row["Team A"])
        )

        team_b = html.escape(
            str(row["Team B"])
        )


        score_a = float(
            row["Score A"]
        )

        score_b = float(
            row["Score B"]
        )


        rank_a = html.escape(
            str(row["Weekly Rank (A)"])
        )

        rank_b = html.escape(
            str(row["Weekly Rank (B)"])
        )


        score_a_class = "mobile-score"
        score_b_class = "mobile-score"


        if score_a > score_b:
            score_a_class += " mobile-score-winner"

        elif score_b > score_a:
            score_b_class += " mobile-score-winner"


        rows.append(
            (
                '<div class="mobile-match-row">'
                f'<div class="mobile-rank">{rank_a}</div>'
                f'<div class="mobile-team mobile-team-left" title="{team_a}">{team_a}</div>'
                f'<div class="{score_a_class}">{score_a:.2f}</div>'
                f'<div class="{score_b_class}">{score_b:.2f}</div>'
                f'<div class="mobile-team mobile-team-right" title="{team_b}">{team_b}</div>'
                f'<div class="mobile-rank">{rank_b}</div>'
                '</div>'
            )
        )


    return (
        '<div class="mobile-matchups-wrapper">'
        '<div class="mobile-matchups-card">'
        '<div class="mobile-match-header">'
        '<div>RK</div>'
        '<div class="header-team-left">TEAM</div>'
        '<div>PTS</div>'
        '<div>PTS</div>'
        '<div class="header-team-right">TEAM</div>'
        '<div>RK</div>'
        '</div>'
        f'{"".join(rows)}'
        '</div>'
        '</div>'
    )


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
   Mobile matchup card
========================================================= */

.mobile-matchups-wrapper {
    display: none;
}


.mobile-matchups-card {
    width: 100%;

    max-width: 100%;

    box-sizing: border-box;

    overflow: hidden;

    background:
        radial-gradient(
            120% 140% at 20% 0%,
            var(--panel) 0%,
            var(--panel-2) 100%
        );

    border:
        1px solid var(--gold-soft);

    border-radius: 14px;

    box-shadow:
        var(--shadow-amb);
}


.mobile-match-header,
.mobile-match-row {
    display: grid;

    grid-template-columns:
        24px
        minmax(0, 1fr)
        47px
        47px
        minmax(0, 1fr)
        24px;

    align-items: center;

    width: 100%;

    box-sizing: border-box;
}


.mobile-match-header {
    min-height: 32px;

    padding:
        0 4px;

    background:
        rgba(255,255,255,.025);

    color: var(--muted);

    font-size: .56rem;

    font-weight: 900;

    letter-spacing: .05em;

    text-align: center;

    border-bottom:
        1px solid rgba(226,188,91,.18);
}


.header-team-left {
    text-align: left;
}


.header-team-right {
    text-align: right;
}


.mobile-match-row {
    min-height: 50px;

    padding:
        0 4px;

    border-bottom:
        1px solid rgba(226,188,91,.12);
}


.mobile-match-row:last-child {
    border-bottom: 0;
}


.mobile-rank {
    color: var(--muted);

    font-size: .67rem;

    font-weight: 800;

    text-align: center;
}


.mobile-team {
    min-width: 0;

    overflow: hidden;

    white-space: nowrap;

    text-overflow: ellipsis;

    color: var(--text);

    font-size: .72rem;

    font-weight: 700;
}


.mobile-team-left {
    padding-left: 3px;

    padding-right: 4px;

    text-align: left;
}


.mobile-team-right {
    padding-left: 4px;

    padding-right: 3px;

    text-align: right;
}


.mobile-score {
    color: var(--text);

    font-size: .69rem;

    font-weight: 700;

    text-align: center;

    white-space: nowrap;
}


.mobile-score-winner {
    color: var(--gold);

    font-weight: 900;
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


    .mobile-matchups-card {
        width: 100% !important;

        max-width: 100% !important;

        border-radius: 13px;
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
    st.session_state["weekly_wk"] = 1
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


    df = load_weekly_matchups_cached(
        SLEEPER_LEAGUE_ID,
        int(st.session_state["weekly_wk"]),
    )


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

        mobile_grid = _mobile_matchup_grid(
            df
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