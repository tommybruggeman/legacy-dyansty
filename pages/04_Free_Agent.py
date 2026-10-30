from __future__ import annotations

from pathlib import Path
import html

import pandas as pd
import streamlit as st

from auth import current_user, require_login, service_client
from components.sidebar_nav import render_nav
from gm_assistant.request_context import AssistantContextError, build_assistant_request_context
from services.free_agents import (
    SUPPORTED_POSITIONS,
    build_free_agent_results,
    current_free_agents_for_filters,
    future_free_agents_for_season,
    future_season_options,
    load_league_free_agent_state,
    load_lifetime_points,
    load_player_universe,
    load_ranking_ppg,
    rookie_class_for_position,
    resolve_active_league_season,
)

from services.publication_context import publication_generation

import sys  # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[1]))
from api import market as app_market, sleeper as app_sleeper, teams as app_teams  # noqa: E402
from auth import _sb  # noqa: E402
from season_engine import SeasonResolver  # noqa: E402
from services.team_roster_state import load_team_state  # noqa: E402


ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

# ============================================================
# FREE AGENT PAGE SIZE SETTINGS
# Change these numbers only when you want to resize this page.
# ============================================================

PAGE_FONT_SIZE = 16
TABLE_FONT_SIZE = 16
TABLE_HEADER_FONT_SIZE = 15
TABLE_ROW_PADDING = 10


st.set_page_config(
    page_title="Free Agent",
    page_icon=str(ICON),
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()
require_login()

loading_placeholder = st.empty()
loading_placeholder.markdown(
    """
    <style>
    .legacy-loader-wrap { position: fixed; inset: 0; z-index: 999999; display: flex; flex-direction: column; justify-content: center; align-items: center; background: #061311; }
    .legacy-loader { width: 70px; height: 70px; border: 6px solid rgba(226,188,91,.25); border-top: 6px solid #E2BC5B; border-radius: 50%; animation: legacy-spin 1s linear infinite; box-shadow: 0 0 24px rgba(226,188,91,.18); }
    .legacy-loader-text { margin-top: 22px; font-size: 1.35rem; font-weight: 800; color: #F5EBD7; letter-spacing: .04em; text-transform: uppercase; }
    @keyframes legacy-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
    <div class="legacy-loader-wrap">
        <div class="legacy-loader"></div>
        <div class="legacy-loader-text">Loading Free Agent Market...</div>
    </div>
    """,
    unsafe_allow_html=True,
)


def _user_id(user) -> str | None:
    if isinstance(user, dict):
        return str(user.get("id") or user.get("user_id") or "").strip() or None
    return str(getattr(user, "id", "") or "").strip() or None


@st.cache_data(ttl=900, show_spinner=False)
def _cached_player_universe(context_generation: int):
    sb = service_client()
    return load_player_universe(sb), load_lifetime_points(sb)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_league_state(
    league_id: str,
    context_generation: int,
    relevant_player_ids: tuple[str, ...],
):
    return load_league_free_agent_state(
        service_client(),
        league_id,
        relevant_player_ids,
    )


@st.cache_data(ttl=60, show_spinner=False)
def _cached_ranking_ppg(
    active_season: int,
    context_generation: int,
):
    return load_ranking_ppg(
        service_client(),
        active_season=active_season,
    )


def _current_frame(
    rows,
    *,
    ppg_state,
) -> pd.DataFrame:

    last_column = (
        f"Last Season PPG ({ppg_state.last_season})"
    )

    current_column = (
        f"Current Season PPG ({ppg_state.active_season})"
    )

    return pd.DataFrame(
        [
            {
                "Player": row.player,
                "Position": row.position,
                "NFL Team": row.nfl_team,
                last_column: (
                    "—"
                    if row.last_season_ppg is None
                    else f"{row.last_season_ppg:.2f}"
                ),
                current_column: (
                    (
                        "No games yet"
                        if not ppg_state.active_season_started
                        else "—"
                    )
                    if row.current_season_ppg is None
                    else f"{row.current_season_ppg:.2f}"
                ),
            }
            for row in rows
        ],
        columns=[
            "Player",
            "Position",
            "NFL Team",
            last_column,
            current_column,
        ],
    )


def _future_frame(
    rows,
    *,
    ppg_state,
) -> pd.DataFrame:

    last_column = (
        f"Last Season PPG ({ppg_state.last_season})"
    )

    current_column = (
        f"Current Season PPG ({ppg_state.active_season})"
    )

    return pd.DataFrame(
        [
            {
                "Player": row.player,
                "Position": row.position,
                "NFL Team": row.nfl_team,
                "Owner": row.contracted_team,
                "Salary": (
                    "—"
                    if row.salary is None
                    else f"${row.salary:.1f}"
                ),
                last_column: (
                    "—"
                    if row.last_season_ppg is None
                    else f"{row.last_season_ppg:.2f}"
                ),
                current_column: (
                    (
                        "No games yet"
                        if not ppg_state.active_season_started
                        else "—"
                    )
                    if row.current_season_ppg is None
                    else f"{row.current_season_ppg:.2f}"
                ),
            }
            for row in rows
        ],
        columns=[
            "Player",
            "Position",
            "NFL Team",
            "Owner",
            "Salary",
            last_column,
            current_column,
        ],
    )


def _rookie_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Rank": rank,
                "Player": row.player,
                "Position": row.position,
                "NFL Team": row.nfl_team,
                "College": row.college,
                "Draft Capital": row.drafted,
                "Rookie Status": row.rookie_status,
            }
            for rank, row in enumerate(rows, start=1)
        ],
        columns=[
            "Rank",
            "Player",
            "Position",
            "NFL Team",
            "College",
            "Draft Capital",
            "Rookie Status",
        ],
    )


def _render_html_table(frame: pd.DataFrame) -> None:
    """
    Render a responsive Legacy table whose font size is controlled
    by the settings at the top of this file.
    """

    header_html = "".join(
        f"<th>{html.escape(str(column))}</th>"
        for column in frame.columns
    )

    body_rows = []

    for _, row in frame.iterrows():
        cells = "".join(
            f"<td>{html.escape(str(value))}</td>"
            for value in row.tolist()
        )

        body_rows.append(
            f"<tr>{cells}</tr>"
        )

    body_html = "".join(body_rows)

    st.markdown(
        f"""
        <div class="fa-table-wrap">
            <table class="fa-table">
                <thead>
                    <tr>{header_html}</tr>
                </thead>
                <tbody>
                    {body_html}
                </tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _season_label(season) -> str:
    return f"'{str(season)[-2:]} PPG"


def _ppg_text(value) -> str:
    return "—" if value is None else f"{value:.1f}"


def _render_player_cards(cards) -> None:
    """
    Render phone-only player cards. Each card is
    (position, player, subline, [(value, label, highlight), ...]).
    """

    card_html = []

    for position, player, subline, stats in cards:
        stat_html = "".join(
            f'<div class="fa-card-stat{" fa-card-stat-hot" if highlight else ""}">'
            f"<b>{html.escape(str(value))}</b>"
            f"<span>{html.escape(str(label))}</span>"
            "</div>"
            for value, label, highlight in stats
        )

        card_html.append(
            '<div class="fa-card">'
            f'<span class="fa-card-pos">{html.escape(str(position))}</span>'
            '<div class="fa-card-main">'
            f'<div class="fa-card-name">{html.escape(str(player))}</div>'
            f'<div class="fa-card-sub">{html.escape(str(subline))}</div>'
            "</div>"
            f"{stat_html}"
            "</div>"
        )

    st.markdown(
        f'<div class="fa-cards">{"".join(card_html)}</div>',
        unsafe_allow_html=True,
    )


def _open_market_cards(rows, *, ppg_state):
    return [
        (
            row.position,
            row.player,
            row.nfl_team,
            [
                (
                    _ppg_text(row.last_season_ppg),
                    _season_label(ppg_state.last_season),
                    False,
                ),
                (
                    _ppg_text(row.current_season_ppg),
                    _season_label(ppg_state.active_season),
                    True,
                ),
            ],
        )
        for row in rows
    ]


def _expiring_cards(rows, *, ppg_state):
    return [
        (
            row.position,
            row.player,
            f"{row.contracted_team} · {row.nfl_team}",
            [
                (
                    "—" if row.salary is None else f"${row.salary:.0f}",
                    "Salary",
                    False,
                ),
                (
                    _ppg_text(row.current_season_ppg),
                    _season_label(ppg_state.active_season),
                    True,
                ),
            ],
        )
        for row in rows
    ]


def _position_chips(key: str) -> str:
    choice = st.segmented_control(
        "Position",
        ["All", *SUPPORTED_POSITIONS],
        default="All",
        key=key,
    )

    return choice or "All"


user = current_user() or {}
user_id = _user_id(user)
active_league_id = str(
    st.session_state.get("active_league_id") or ""
).strip()

if not user_id:
    loading_placeholder.empty()
    st.error("Free Agent requires an authenticated user.")
    st.stop()

if not active_league_id:
    loading_placeholder.empty()
    st.warning("Select a league before opening Free Agent.")
    st.stop()


try:
    context = build_assistant_request_context(
        sb=service_client(),
        user=user,
        active_league_id=active_league_id,
    )

    active_season = resolve_active_league_season(
        context
    )

    context_generation = publication_generation(
        service_client(),
        active_league_id,
    )

except AssistantContextError:
    loading_placeholder.empty()

    st.error(
        "Free Agent could not verify your membership and selected league. "
        "Check your league access and try again."
    )

    st.stop()

except ValueError:
    loading_placeholder.empty()

    st.error(
        "The active league season is unavailable. "
        "Ask the commissioner to verify the league season settings."
    )

    st.stop()


try:
    universe, lifetime_points = _cached_player_universe(
        context_generation
    )

    state = _cached_league_state(
        context.league_id,
        context_generation,
        tuple(
            str(row.get("sleeper_id") or "").strip()
            for row in universe
            if str(row.get("sleeper_id") or "").strip()
        ),
    )

    ppg_state = _cached_ranking_ppg(
        active_season,
        context_generation,
    )

    results = build_free_agent_results(
        universe,
        state,
        active_season=active_season,
        lifetime_points_by_player=lifetime_points,
        last_season_ppg_by_player=ppg_state.last_ppg,
        current_season_ppg_by_player=ppg_state.current_ppg,
        ranking_ppg_by_player=ppg_state.ranking_ppg,
    )

except Exception:
    loading_placeholder.empty()

    st.error(
        "Free-agent data is temporarily unavailable. "
        "Refresh the page or try again later."
    )

    st.stop()


if not universe:
    loading_placeholder.empty()

    st.warning(
        "The player universe is unavailable, "
        "so the free-agent pool cannot be calculated."
    )

    st.stop()


loading_placeholder.empty()


# ============================================================
# Dynamic page/table sizing
# Change the four values near the top of this file only.
# ============================================================

st.markdown(
    f"""
    <style>
    [data-testid="stAppViewContainer"] p,
    [data-testid="stAppViewContainer"] label,
    [data-testid="stAppViewContainer"] input,
    [data-testid="stAppViewContainer"] button,
    [data-testid="stAppViewContainer"] [data-baseweb="select"] {{
        font-size:{PAGE_FONT_SIZE}px !important;
    }}

    .fa-table {{
        font-size:{TABLE_FONT_SIZE}px !important;
    }}

    .fa-table th {{
        font-size:{TABLE_HEADER_FONT_SIZE}px !important;
        padding:{TABLE_ROW_PADDING}px 12px !important;
    }}

    .fa-table td {{
        padding:{TABLE_ROW_PADDING}px 12px !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


st.markdown(
    """
    <style>
    :root { --legacy-bg:#061311; --legacy-panel:#101E1D; --legacy-panel-deep:#0C1917; --legacy-gold:#E2BC5B; --legacy-gold-soft:rgba(226,188,91,.34); --legacy-text:#FFF5E7; --legacy-muted:#9DA89C; }
    [data-testid="stAppViewContainer"] { background:var(--legacy-bg); color:var(--legacy-text); }
    .block-container { padding-top:3.2rem; }
    .fa-hero { background:radial-gradient(100% 160% at 8% 0%,rgba(226,188,91,.18),transparent 46%),linear-gradient(135deg,var(--legacy-panel),var(--legacy-panel-deep)); border:1px solid var(--legacy-gold-soft); border-radius:22px; padding:1.45rem 1.6rem; margin-bottom:1.65rem; box-shadow:0 5px 22px rgba(0,0,0,.30); }
    .fa-kicker { color:var(--legacy-gold); font-size:.72rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; }
    .fa-title { margin:.2rem 0 .35rem; color:var(--legacy-text); font-family:Georgia,'Times New Roman',serif; font-size:2.35rem; line-height:1.05; }
    .fa-subtitle { color:var(--legacy-muted); margin:0; max-width:760px; }
    .fa-section-space { height:.55rem; }
    [data-testid="stTabs"] [data-baseweb="tab-list"] { gap:1.5rem; margin-bottom:1rem; }
    [data-testid="stTabs"] button[role="tab"] { color:var(--legacy-muted); font-weight:800; padding:.65rem .35rem .8rem; }
    [data-testid="stTabs"] button[aria-selected="true"] { color:var(--legacy-gold); }
    [data-testid="stAppViewContainer"] p,
    [data-testid="stAppViewContainer"] label,
    [data-testid="stAppViewContainer"] input,
    [data-testid="stAppViewContainer"] button,
    [data-testid="stAppViewContainer"] [data-baseweb="select"] {
        font-size:16px;
    }
    .fa-table-wrap {
        width:100%;
        overflow-x:auto;
        margin-top:.55rem;
        border:1px solid var(--legacy-gold-soft);
        border-radius:14px;
        background:rgba(255,255,255,.015);
    }
    .fa-table {
        width:100%;
        border-collapse:collapse;
        table-layout:auto;
        color:var(--legacy-text);
        font-size:16px;
    }
    .fa-table th {
        background:#1A1D25;
        color:var(--legacy-muted);
        font-size:15px;
        font-weight:800;
        text-align:left;
        padding:10px 12px;
        border-bottom:1px solid rgba(226,188,91,.20);
        white-space:nowrap;
    }
    .fa-table td {
        padding:10px 12px;
        border-bottom:1px solid rgba(226,188,91,.12);
        vertical-align:middle;
        white-space:nowrap;
    }
    .fa-table tbody tr:last-child td {
        border-bottom:none;
    }
    [data-testid="stTextInput"] input, [data-testid="stSelectbox"] [data-baseweb="select"] > div { background:rgba(255,255,255,.035)!important; border-color:var(--legacy-gold-soft)!important; border-radius:12px!important; color:var(--legacy-text)!important; }
    [data-testid="stTextInput"] input:focus, [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within { border-color:var(--legacy-gold)!important; box-shadow:0 0 0 1px rgba(226,188,91,.28)!important; }
    div[data-testid="stButton"] button { border:1px solid var(--legacy-gold); border-radius:12px; min-height:3rem; font-weight:850; background:var(--legacy-panel-deep); color:var(--legacy-gold); }
    div[data-testid="stButton"] button[kind="primary"] { background:var(--legacy-gold); border-color:var(--legacy-gold); color:#061311; box-shadow:0 0 18px rgba(226,188,91,.24); }
    div[data-testid="stButton"] button:hover, div[data-testid="stButton"] button:focus { border-color:var(--legacy-gold); color:var(--legacy-text); box-shadow:0 0 0 1px rgba(226,188,91,.25); }
    [data-testid="stElementContainer"]:has(> [data-testid="stButtonGroup"]), [data-testid="stButtonGroup"] { width:100%!important; }
    [data-testid="stButtonGroup"] [role="radiogroup"] { display:flex; flex-wrap:nowrap; width:100%!important; max-width:none!important; gap:.4rem; }
    [data-testid="stButtonGroup"] button { flex:1 1 0; min-width:0; min-height:2.5rem; padding:0 .2rem; background:rgba(255,255,255,.035)!important; border:1px solid var(--legacy-gold-soft)!important; border-radius:999px!important; color:var(--legacy-muted)!important; }
    [data-testid="stButtonGroup"] button p { font-weight:800; }
    [data-testid="stButtonGroup"] button[aria-checked="true"] { background:var(--legacy-gold)!important; border-color:var(--legacy-gold)!important; color:#061311!important; }
    .st-key-fa_expiring_filters [data-testid="stButtonGroup"] { max-width:440px; }
    .fa-cards { display:none; }
    .fa-card { display:flex; align-items:center; gap:.65rem; background:linear-gradient(135deg,var(--legacy-panel),var(--legacy-panel-deep)); border:1px solid var(--legacy-gold-soft); border-radius:16px; padding:.65rem .75rem; margin-bottom:.5rem; }
    .fa-card-pos { flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; width:2.3rem; height:1.55rem; border-radius:999px; border:1px solid rgba(226,188,91,.5); background:#1c2418; font-size:.7rem; font-weight:800; color:var(--legacy-text); }
    .fa-card-main { flex:1; min-width:0; }
    .fa-card-name { font-weight:800; color:var(--legacy-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .fa-card-sub { color:var(--legacy-muted); font-size:.78rem; margin-top:.1rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .fa-card-stat { flex:0 0 3.4rem; text-align:center; }
    .fa-card-stat b { display:block; font-size:1rem; color:var(--legacy-text); }
    .fa-card-stat span { display:block; font-size:.62rem; font-weight:800; color:var(--legacy-muted); text-transform:uppercase; letter-spacing:.02em; }
    .fa-card-stat-hot b { color:var(--legacy-gold); }
    @media (max-width:768px) {
        .block-container { padding-left:1rem; padding-right:1rem; }
        .fa-hero { padding:1rem 1.1rem; margin-bottom:1rem; border-radius:18px; }
        .fa-title { font-size:1.8rem; }
        .fa-subtitle { font-size:.9rem!important; }
        [data-testid="stTabs"] [data-baseweb="tab-list"] { gap:1rem; }
        .fa-section-space { display:none; }
        .fa-table-wrap { display:none; }
        .fa-cards { display:block; margin-top:.35rem; }
        .st-key-fa_open_market_filters [data-testid="stHorizontalBlock"] { flex-wrap:wrap!important; gap:.5rem!important; }
        .st-key-fa_open_market_filters [data-testid="stColumn"] { flex:1 1 calc(50% - .25rem)!important; min-width:calc(50% - .25rem)!important; width:calc(50% - .25rem)!important; }
        .st-key-fa_open_market_filters [data-testid="stColumn"]:nth-child(-n+2) { flex-basis:100%!important; min-width:100%!important; width:100%!important; }
        .st-key-fa_open_market_filters [data-testid="stColumn"]:nth-child(-n+2) [data-testid="stWidgetLabel"] { display:none; }
        .st-key-fa_expiring_filters [data-testid="stWidgetLabel"] { display:none; }
        .st-key-fa_expiring_filters [data-testid="stButtonGroup"] { max-width:none; }
        [data-testid="stButtonGroup"] button p { font-size:14px!important; overflow:visible; text-overflow:clip; }
        .st-key-fa_expiring_years [data-testid="stHorizontalBlock"] { flex-wrap:nowrap!important; gap:.4rem!important; }
        .st-key-fa_expiring_years [data-testid="stColumn"] { min-width:0!important; width:auto!important; flex:1 1 0!important; }
        .st-key-fa_expiring_years div[data-testid="stButton"] button { min-height:2.5rem; padding:0 .25rem; }
    }
    </style>
    <div class="fa-hero">
        <div class="fa-kicker">League Market</div>
        <h1 class="fa-title">Free Agent</h1>
        <p class="fa-subtitle">Browse the open market, upcoming contract expirations, and the active rookie class.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


for warning in results.warnings:
    if warning.startswith("internal:"):
        continue

    st.warning(warning)


open_market_tab, league_players_tab, expiring_tab, rookie_tab = st.tabs(
    [
        "Open Market",
        "League Players",
        "Expiring Contracts",
        "Rookie Class",
    ]
)


with open_market_tab:
    st.subheader("Open Market")

    st.caption(
        "Explore players currently available in your league."
    )

    st.markdown(
        '<div class="fa-section-space"></div>',
        unsafe_allow_html=True,
    )

    filter_box = st.container(
        key="fa_open_market_filters",
    )

    with filter_box:
        search_col, position_col, team_col, roster_col = st.columns(
            [1.6, 1.7, 1, 1.35],
            gap="medium",
        )

    with search_col:
        search = st.text_input(
            "Search player",
            placeholder="🔍  Search player",
            key="open_market_search",
        )

    with position_col:
        position = _position_chips(
            "open_market_position_chips"
        )

    with team_col:
        nfl_team = st.selectbox(
            "NFL Team",
            ["All", *results.nfl_teams],
            key="open_market_nfl_team",
        )

    with roster_col:
        roster_status = st.selectbox(
            "Roster Status",
            [
                "All",
                "Active roster",
                "Not on active roster",
            ],
            key="open_market_roster_status",
        )


    current_rows = current_free_agents_for_filters(
        results.current,
        search=search,
        position=position,
        nfl_team=nfl_team,
        nfl_roster_status=roster_status,
    )


    filter_signature = (
        search,
        position,
        nfl_team,
        roster_status,
    )


    if (
        st.session_state.get(
            "open_market_filter_signature"
        )
        != filter_signature
    ):
        st.session_state[
            "open_market_filter_signature"
        ] = filter_signature

        st.session_state[
            "open_market_visible_count"
        ] = 50


    visible_count = st.session_state.get(
        "open_market_visible_count",
        50,
    )


    if current_rows:
        visible_rows = current_rows[
            :visible_count
        ]

        last_ppg_column = (
            f"Last Season PPG ({ppg_state.last_season})"
        )

        current_ppg_column = (
            f"Current Season PPG ({ppg_state.active_season})"
        )

        _render_html_table(
            _current_frame(
                visible_rows,
                ppg_state=ppg_state,
            )
        )

        _render_player_cards(
            _open_market_cards(
                visible_rows,
                ppg_state=ppg_state,
            )
        )

        if visible_count < len(current_rows):
            remaining_players = (
                len(current_rows)
                - visible_count
            )

            load_amount = min(
                50,
                remaining_players,
            )

            if st.button(
                f"Load More Players (+{load_amount})",
                key="open_market_load_more",
                use_container_width=True,
            ):
                st.session_state[
                    "open_market_visible_count"
                ] = (
                    visible_count
                    + 50
                )

                st.rerun()

    else:
        st.info(
            "No Open Market players match these filters."
        )


@st.cache_data(ttl=120, show_spinner=False)
def _cached_league_players(league_id: str, team_id: str, active_season: int, context_generation: int, _ppg) -> dict:
    """The app's Market > Players list (api.market.all_players), so the website and app show the same players."""
    client = _sb(st.session_state.get("sb_access_token"))
    season = SeasonResolver(client).get_active_season(league_id)
    rosters = app_sleeper.get(f"league/{season.sleeper_league_id}/rosters") or [] if season.sleeper_league_id else []
    return app_market.all_players(
        app_market.player_pool(service_client()),
        load_team_state(client, league_id, active_season),
        _ppg,
        app_teams.league_teams(client, league_id),
        rosters,
        team_id,
    )


with league_players_tab:
    st.subheader("League Players")
    st.caption("Every rostered player plus the best free agents. Searching a name looks through the whole league.")
    try:
        league_players = _cached_league_players(
            context.league_id, str(context.league_team_id or ""), active_season, context_generation, ppg_state,
        )
    except Exception:
        league_players = None
        st.error("League players are temporarily unavailable. Refresh the page or try again later.")

    if league_players:
        owners = {o["team_id"]: ("You" if o["is_mine"] else o["owner_name"]) for o in league_players["owners"]}
        with st.container(key="fa_league_players_filters"):
            lp_search_col, lp_status_col, lp_owner_col = st.columns([1.6, 1.4, 1], gap="medium")
            with lp_search_col:
                lp_search = st.text_input("Search player", placeholder="🔍  Search every player", key="league_players_search")
            with lp_status_col:
                lp_status = st.segmented_control(
                    "Status", ["Free agents", "Rostered", "All"], default="Free agents", key="league_players_status",
                ) or "Free agents"
            with lp_owner_col:
                lp_owner = st.selectbox("Owner", ["Everyone", *owners], key="league_players_owner",
                                        format_func=lambda t: owners.get(t, t))
            lp_pos_col, lp_salary_col, lp_contract_col, lp_sort_col = st.columns(4, gap="medium")
            with lp_pos_col:
                lp_pos = st.selectbox("Position", ["All", *app_market.POSITIONS], key="league_players_pos")
            with lp_salary_col:
                lp_salary = st.selectbox("Salary", ["Any", *app_market.SALARY_CAPS, "$20+"], key="league_players_salary")
            with lp_contract_col:
                lp_contract = st.selectbox("Contract", ["Any", "Expiring", "2+ years", "Rookie deal", "Taxi", "IR"],
                                           key="league_players_contract")
            with lp_sort_col:
                lp_sort = st.selectbox("Sort", ["Points", "PPG", "Value per $", "Youngest"], key="league_players_sort")

        lp_rows = app_market.filter_players(
            league_players["players"], search=lp_search, status=lp_status, pos=lp_pos,
            owner=None if lp_owner == "Everyone" else lp_owner, salary=lp_salary, contract=lp_contract, sort=lp_sort,
        )
        lp_rank = _season_label(league_players["season"] if league_players["season_started"] else league_players["last_season"])

        def _lp_owner(p: dict) -> str:
            return ("You" if p["is_mine"] else p["owner_name"] or "Rostered") if p["team_id"] else "Free agent"

        def _lp_contract(p: dict) -> str:
            if p["salary"] is None:
                return "—"
            years = f" · {p['years_left']} yr{'s' if p['years_left'] != 1 else ''}" if p["years_left"] else ""
            return f"${p['salary']:g}{years}"

        def _lp_note(p: dict) -> str:
            return " · ".join(filter(None, [
                (p["designation"] or "").upper(), "Rookie deal" if p["rookie_deal"] else "", p["injury"] or "",
            ]))

        st.caption(f"{len(lp_rows)} players")
        if lp_rows:
            lp_visible = lp_rows[:150]
            _render_html_table(pd.DataFrame([{
                "Player": p["name"],
                "Pos": p["pos"],
                "NFL": p["nfl_team"],
                "Owner": _lp_owner(p),
                "Contract": _lp_contract(p),
                lp_rank: _ppg_text(p["rank_ppg"]),
                "Points": f"{p['points']:.0f}" if p["points"] is not None else "—",
                "Notes": _lp_note(p) or "",
            } for p in lp_visible]))
            _render_player_cards([
                (p["pos"], p["name"], f"{_lp_owner(p)} · {_lp_contract(p)}" if p["team_id"] else f"{p['nfl_team']} · Free agent",
                 [(_ppg_text(p["rank_ppg"]), lp_rank, True)])
                for p in lp_visible
            ])
            if len(lp_rows) > len(lp_visible):
                st.caption(f"Showing the top {len(lp_visible)}. Search or filter to narrow the list.")
        else:
            st.info("No players match these filters.")


with expiring_tab:
    st.subheader("Expiring Contracts")

    st.caption(
        "Plan ahead by reviewing players scheduled to reach free agency."
    )

    st.markdown(
        '<div class="fa-section-space"></div>',
        unsafe_allow_html=True,
    )


    seasons = future_season_options(
        active_season
    )

    selected_key = "expiring_contracts_season"


    if st.session_state.get(
        selected_key
    ) not in seasons:
        st.session_state[
            selected_key
        ] = seasons[0]


    with st.container(
        key="fa_expiring_years",
    ):
        year_columns = st.columns(
            len(seasons),
            gap="small",
        )


    for column, season in zip(
        year_columns,
        seasons,
    ):

        with column:
            if st.button(
                str(season),
                key=f"expiring_year_{season}",
                type=(
                    "primary"
                    if st.session_state[selected_key] == season
                    else "secondary"
                ),
                use_container_width=True,
            ):
                st.session_state[
                    selected_key
                ] = season

                st.rerun()


    selected_season = st.session_state[
        selected_key
    ]


    with st.container(
        key="fa_expiring_filters",
    ):
        position = _position_chips(
            "expiring_contracts_position_chips"
        )


    future_rows = future_free_agents_for_season(
        results.future,
        selected_season,
        position=position,
    )


    if future_rows:
        last_ppg_column = (
            f"Last Season PPG ({ppg_state.last_season})"
        )

        current_ppg_column = (
            f"Current Season PPG ({ppg_state.active_season})"
        )

        _render_html_table(
            _future_frame(
                future_rows,
                ppg_state=ppg_state,
            )
        )

        _render_player_cards(
            _expiring_cards(
                future_rows,
                ppg_state=ppg_state,
            )
        )

    else:
        st.info(
            f"No contracts are scheduled to enter free agency "
            f"in {selected_season}."
        )


with rookie_tab:
    st.subheader("Rookie Class")

    st.caption(
        "Scout the active rookie class and track the next wave of dynasty talent."
    )

    st.markdown(
        '<div class="fa-section-space"></div>',
        unsafe_allow_html=True,
    )


    rookie_position = st.selectbox(
        "Position",
        ["All", *SUPPORTED_POSITIONS],
        key="rookie_class_position",
    )


    rookie_rows = rookie_class_for_position(
        results.rookies,
        position=rookie_position,
    )


    if rookie_rows:
        _render_html_table(
            _rookie_frame(
                rookie_rows
            )
        )

    else:
        st.info(
            f"No eligible {active_season} rookies "
            f"match this position filter."
        )
