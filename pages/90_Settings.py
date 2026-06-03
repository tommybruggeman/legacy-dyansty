# pages/90_Settings.py
from __future__ import annotations

import os
import sys
import requests
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from components.sidebar_nav import render_nav
from auth import _sb
import math

# ============================================================
# Page setup
# ============================================================
ROOT_DIR = Path(__file__).resolve().parents[1]
ICON = ROOT_DIR / "assets" / "page_icon.png"

st.set_page_config(
    page_title="Legacy Dynasty — League Control Center",
    page_icon=str(ICON) if ICON.exists() else "🏈",
    layout="wide",
    initial_sidebar_state="expanded",
)

render_nav()

PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR_STR = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR_STR)
sys.path.append(os.path.join(ROOT_DIR_STR, "lib"))


# ============================================================
# Styling
# ============================================================
st.markdown(
    """
<style>
:root {
    --bg: #03140D;
    --panel: rgba(8, 31, 21, .72);
    --panel-soft: rgba(10, 39, 27, .54);
    --panel-dark: rgba(4, 19, 13, .82);
    --gold: #C89B4A;
    --gold-soft: rgba(200,155,74,.16);
    --gold-border: rgba(200,155,74,.24);
    --cream: #F5EBD7;
    --muted: #CFC6B4;
    --green-ok: #4FBA73;
    --red-warn: #E46B60;
    --blue-info: #8FD6FF;
}

html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--cream) !important;
}

[data-testid="stHeader"] {
    background: transparent !important;
}

.block-container {
    max-width: 1120px;
    padding-top: 2rem;
    padding-bottom: 5rem;
}

h1, h2, h3, h4, p, label {
    color: var(--cream) !important;
}

.small-muted, .stCaption {
    color: var(--muted) !important;
    font-size: .92rem;
}

.app-hero {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: .25rem 0 1rem 0;
    margin-bottom: .75rem;
}

.app-hero h1 {
    margin: 0;
    font-size: 2.35rem;
    letter-spacing: -.03em;
}

.app-hero p {
    margin: .35rem 0 0 0;
    color: var(--muted) !important;
}

.status-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: .65rem;
    margin: .75rem 0 1rem 0;
}

.status-card {
    background: rgba(10,39,27,.38);
    border: 1px solid rgba(200,155,74,.14);
    border-radius: 14px;
    padding: .72rem .85rem;
}

.section-card {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: .5rem 0 1rem 0;
    margin: 1.25rem 0 .5rem 0;
}
.status-label {
    color: var(--gold);
    font-size: .68rem;
    text-transform: uppercase;
    font-weight: 900;
    letter-spacing: .06em;
}

.status-value {
    color: var(--cream);
    font-size: .92rem;
    margin-top: .25rem;
    font-weight: 800;
    word-break: break-word;
}

.section-card {
    background: var(--panel);
    border: 1px solid var(--gold-border);
    border-radius: 20px;
    padding: 1.15rem 1.25rem;
    margin: 1rem 0;
}

.section-card h2, .section-card h3 {
    margin-top: 0;
}

.action-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: .75rem;
    margin: 1rem 0;
}

.action-card {
    background: var(--panel-soft);
    border: 1px solid rgba(200,155,74,.18);
    border-radius: 18px;
    padding: 1rem;
    min-height: 112px;
}

.action-card .kicker {
    color: var(--gold);
    font-size: .7rem;
    text-transform: uppercase;
    font-weight: 900;
    letter-spacing: .06em;
}

.action-card .title {
    color: var(--cream);
    font-size: 1.05rem;
    font-weight: 900;
    margin-top: .3rem;
}

.action-card .copy {
    color: var(--muted);
    font-size: .84rem;
    margin-top: .35rem;
}

.map-row {
    background: rgba(8,31,21,.52);
    border: 1px solid rgba(200,155,74,.16);
    border-radius: 18px;
    padding: .9rem;
    margin-bottom: .7rem;
}

.connected-pill, .needs-pill, .role-pill {
    display: inline-block;
    border-radius: 999px;
    padding: .25rem .55rem;
    font-size: .72rem;
    font-weight: 900;
}

.connected-pill {
    background: rgba(79,186,115,.16);
    border: 1px solid rgba(79,186,115,.32);
    color: #82E0A1;
}

.needs-pill {
    background: rgba(228,107,96,.14);
    border: 1px solid rgba(228,107,96,.28);
    color: #FFAAA3;
}

.role-pill {
    background: rgba(200,155,74,.14);
    border: 1px solid rgba(200,155,74,.24);
    color: var(--gold);
}

div[role="radiogroup"] {
    background: rgba(8,31,21,.58);
    border: 1px solid var(--gold-border);
    border-radius: 18px;
    padding: .45rem;
    gap: .3rem;
    margin-bottom: 1rem;
}

div[role="radiogroup"] label {
    border-radius: 13px !important;
    padding: .58rem .9rem !important;
    color: var(--muted) !important;
}

div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child {
    border-color: var(--gold) !important;
}

.stButton button,
.stDownloadButton button,
.stFormSubmitButton button {
    background: linear-gradient(135deg, #C89B4A, #B88735) !important;
    color: #03140D !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 900 !important;
    min-height: 44px;
}

.stButton button:hover,
.stDownloadButton button:hover,
.stFormSubmitButton button:hover {
    transform: translateY(-1px);
    opacity: .96;
}

.stTextInput input,
.stNumberInput input,
.stTextArea textarea,
.stSelectbox div[data-baseweb="select"] {
    background-color: rgba(10,39,27,.92) !important;
    border: 1px solid rgba(200,155,74,.22) !important;
    border-radius: 12px !important;
    color: var(--cream) !important;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--panel-soft) !important;
    border: 1px solid rgba(200,155,74,.16) !important;
    border-radius: 18px !important;
}

hr {
    border-color: rgba(200,155,74,.14) !important;
}

.locked-card {
    opacity: .55;
}

@media (max-width: 900px) {
    .status-row, .action-grid {
        grid-template-columns: 1fr;
    }
}

.auction-card-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: .9rem;
    margin: 1rem 0 1.35rem 0;
}

.auction-option-card {
    position: relative;
    background: linear-gradient(145deg, rgba(9,35,24,.92), rgba(5,22,15,.96));
    border: 1px solid rgba(200,155,74,.16);
    border-radius: 14px;
    min-height: 118px;
    padding: 1.25rem 1rem 1rem 1rem;
}

.auction-option-card.selected {
    background: linear-gradient(145deg, rgba(22,82,55,.92), rgba(7,31,21,.96));
    border: 1px solid #D6A544;
    box-shadow: 0 0 0 1px rgba(214,165,68,.18);
}

.auction-option-card.locked {
    opacity: .58;
}

.auction-card-kicker {
    color: var(--gold);
    font-size: .72rem;
    font-weight: 900;
    letter-spacing: .06em;
    text-transform: uppercase;
}

.auction-card-value {
    color: var(--cream);
    font-size: 1.75rem;
    font-weight: 950;
    margin-top: .65rem;
}

.auction-card-copy {
    color: var(--muted);
    font-size: .85rem;
    margin-top: .55rem;
}

.best-badge {
    position: absolute;
    top: .45rem;
    left: .55rem;
    background: #D6A544;
    color: #03140D;
    border-radius: 5px;
    padding: .15rem .42rem;
    font-size: .62rem;
    font-weight: 950;
}

.check-badge {
    position: absolute;
    top: .55rem;
    right: .65rem;
    background: rgba(79,186,115,.28);
    color: #CFF6D8;
    border-radius: 999px;
    width: 22px;
    height: 22px;
    text-align: center;
    font-weight: 900;
}

.lock-icon {
    position: absolute;
    top: .75rem;
    right: .75rem;
    color: var(--muted);
}

@media (max-width: 900px) {
    .auction-card-grid {
        grid-template-columns: 1fr;
    }
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# Env + Supabase REST helpers
# ============================================================
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


def _load_env() -> tuple[str, str]:
    here = Path(__file__).resolve()
    root = here.parents[1]
    cwd = Path.cwd()

    possible_paths = [
        here.with_name("fantasy_env"),
        here.with_name(".env"),
        cwd / "fantasy_env",
        cwd / ".env",
        root / "fantasy_env",
        root / ".env",
        cwd / "pages" / "fantasy_env",
        cwd / "pages" / ".env",
    ]

    for p in possible_paths:
        if _load_kv(p):
            break

    return (
        (os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")).strip(),
        (
            os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("SUPABASE_KEY")
            or st.secrets.get("SUPABASE_ANON_KEY", "")
            or st.secrets.get("SUPABASE_KEY", "")
        ).strip(),
    )


SUPABASE_URL, SUPABASE_KEY = _load_env()

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing Supabase credentials.")
    st.stop()


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Table:
    def __init__(self, base: str, headers: dict, name: str):
        self.base = base.rstrip("/")
        self.h = headers
        self.name = name
        self._select = "*"
        self._order = None
        self._filters = {}

    def select(self, cols: str = "*", count: str | None = None):
        self._select = cols
        return self

    def eq(self, col: str, val):
        self._filters[col] = f"eq.{val}"
        return self

    def order(self, col: str, desc: bool = False):
        self._order = (col, desc)
        return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        params = {"select": self._select}
        params.update(self._filters)

        if self._order:
            params["order"] = f"{self._order[0]}.{'desc' if self._order[1] else 'asc'}"

        r = requests.get(url, headers=self.h, params=params, timeout=20)
        if r.status_code == 404:
            return _Resp([])
        r.raise_for_status()
        return _Resp(r.json())

    def insert(self, payload: dict | list[dict]):
        url = f"{self.base}/rest/v1/{self.name}"
        r = requests.post(
            url,
            headers={**self.h, "Prefer": "return=representation"},
            json=payload,
            timeout=20,
        )
        r.raise_for_status()
        return _Resp(r.json())

    def update(self, payload: dict):
        url = f"{self.base}/rest/v1/{self.name}"
        params = dict(self._filters)
        r = requests.patch(
            url,
            headers={**self.h, "Prefer": "return=representation"},
            params=params,
            json=payload,
            timeout=20,
        )
        r.raise_for_status()
        return _Resp(r.json())


class SB:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.h = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def table(self, name: str):
        return _Table(self.url, self.h, name)


sb = SB(SUPABASE_URL, SUPABASE_KEY)
access = st.session_state.get("sb_access_token")
sb_client = _sb(access) if access else None

# ============================================================
# Access control
# ============================================================
role = st.session_state.get("role")
active_league_id = st.session_state.get("active_league_id")

is_platform_admin = role in ["admin"]
is_commissioner = role in ["commissioner", "host", "admin"]
is_owner_level = role in ["owner", "co_owner", "co-owner", "member", "viewer", "commissioner", "host", "admin"]

if not active_league_id:
    st.error("No active league selected. Return to league setup first.")
    st.stop()

if not is_owner_level:
    st.error("You do not have access to this league control center.")
    st.stop()


# ============================================================
# Utility functions
# ============================================================
def safe_table(table_name: str, order_col: str | None = None) -> list[dict]:
    try:
        client = sb_client if sb_client else sb

        q = client.table(table_name).select("*")

        if active_league_id:
            q = q.eq("league_id", active_league_id)

        if order_col:
            q = q.order(order_col)

        return q.execute().data or []

    except Exception as e:
        st.error(f"Could not load {table_name}: {e}")
        return []

def log_commish_action(action_type: str, details: dict):
    if not active_league_id:
        return

    payload = {
        "league_id": active_league_id,
        "action_type": action_type,
        "details": details,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        sb.table("commissioner_action_log").insert(payload)
    except Exception:
        pass


def fetch_active_league() -> dict | None:
    try:
        client = sb_client if sb_client else sb

        rows = (
            client.table("leagues")
            .select("id, name, sleeper_league_id, created_at")
            .eq("id", active_league_id)
            .execute()
            .data
            or []
        )

        return rows[0] if rows else None

    except Exception as e:
        st.error(f"Could not load active league: {e}")
        return None

@st.cache_data(ttl=300, show_spinner=False)
def fetch_sleeper_league_payload(sleeper_league_id: str) -> tuple[list[dict], list[dict]]:
    if not sleeper_league_id:
        return [], []

    users_url = f"https://api.sleeper.app/v1/league/{sleeper_league_id}/users"
    rosters_url = f"https://api.sleeper.app/v1/league/{sleeper_league_id}/rosters"

    users = requests.get(users_url, timeout=20).json()
    rosters = requests.get(rosters_url, timeout=20).json()

    return users or [], rosters or []


def sleeper_display_name(user: dict) -> str:
    metadata = user.get("metadata") or {}
    return (
        metadata.get("team_name")
        or user.get("display_name")
        or user.get("username")
        or user.get("user_id")
        or "Unnamed Sleeper Team"
    )


def build_sleeper_options(users: list[dict], rosters: list[dict]) -> tuple[list[str], dict[str, dict]]:
    user_by_id = {u.get("user_id"): u for u in users if u.get("user_id")}
    options = ["-- choose Sleeper team --"]
    option_map = {}

    for roster in sorted(rosters, key=lambda r: int(r.get("roster_id") or 0)):
        owner_id = roster.get("owner_id")
        user = user_by_id.get(owner_id, {})
        roster_id = roster.get("roster_id")
        team_name = sleeper_display_name(user)
        label = team_name

        options.append(label)
        option_map[label] = {
            "sleeper_roster_id": roster_id,
            "sleeper_user_id": owner_id,
            "sleeper_team_name": team_name,
        }

    return options, option_map


def section_header(title: str, subtitle: str):
    st.markdown(
        f"""
<div class="section-card">
    <h2>{title}</h2>
    <p class="small-muted">{subtitle}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def render_action_cards(cards: list[tuple[str, str, str]]):
    html = ['<div class="action-grid">']

    for kicker, title, copy in cards:
        html.append(
            f'<div class="action-card">'
            f'<div class="kicker">{kicker}</div>'
            f'<div class="title">{title}</div>'
            f'<div class="copy">{copy}</div>'
            f'</div>'
        )

    html.append("</div>")

    st.markdown("".join(html), unsafe_allow_html=True)

def show_commissioner_gate():
    st.markdown(
        """
<div class="section-card">
    <h3>Commissioner access required</h3>
    <p class="small-muted">
        This section changes league-wide rules and tools. Only the league commissioner can access it.
    </p>
</div>
""",
        unsafe_allow_html=True,
    )


# ============================================================
# League context
# ============================================================
league = fetch_active_league()
league_name = league.get("name") if league else "Current League"
sleeper_league_id = league.get("sleeper_league_id") if league else None

league_teams = safe_table("league_teams", "owner_name")
mapped_count = len([t for t in league_teams if t.get("sleeper_roster_id")])
team_count = len(league_teams)


# ============================================================
# Hero
# ============================================================
st.markdown(
    f"""
<div class="app-hero">
    <h1>League Control Center</h1>
    <p>Manage ownership, commissioner tools, draft setup, and league activity for <b>{league_name}</b>.</p>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown(
    f"""
<div class="status-row">
    <div class="status-card">
        <div class="status-label">Your Role</div>
        <div class="status-value">{role or "Unknown"}</div>
    </div>
    <div class="status-card">
        <div class="status-label">Owner Mapping</div>
        <div class="status-value">{mapped_count}/{team_count or 0} connected</div>
    </div>
    <div class="status-card">
        <div class="status-label">Sleeper</div>
        <div class="status-value">{"Connected" if sleeper_league_id else "Missing"}</div>
    </div>
    <div class="status-card">
        <div class="status-label">Mode</div>
        <div class="status-value">Hybrid Sleeper/App</div>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# ============================================================
# Navigation
# ============================================================
nav_options = ["Owners"]

if is_commissioner:
    nav_options += ["League Manager Tools", "Draft Center", "Audit Log"]
else:
    nav_options += ["My Access"]

if is_platform_admin:
    nav_options.append("Platform Admin")

section = st.radio(
    "Control Center",
    nav_options,
    horizontal=True,
    label_visibility="collapsed",
)

# ============================================================
# OWNERS
# ============================================================
if section == "Owners":
    section_header(
        "Owners & Team Mapping",
        "Connect imported contract owners to Sleeper teams, then assign app users and co-owners.",
    )

    if not sleeper_league_id:
        st.warning("No Sleeper league is currently connected.")

        col1, col2 = st.columns([2, 1])

        with col1:
            sleeper_id = st.text_input(
                "Sleeper League ID",
                placeholder="Paste Sleeper League ID",
            )

        with col2:
            st.write("")
            st.write("")

            if st.button("Connect Sleeper League", use_container_width=True):
                if not sleeper_id.strip():
                    st.error("Enter a Sleeper League ID first.")
                    st.stop()

                try:
                    sb_client.table("leagues").update(
                        {"sleeper_league_id": sleeper_id.strip()}
                    ).eq("id", active_league_id).execute()

                    st.success("Sleeper league connected.")
                    st.rerun()

                except Exception as e:
                    st.error(f"Could not connect league: {e}")

        st.stop()
    try:
        with st.spinner("Loading Sleeper teams..."):
            sleeper_users, sleeper_rosters = fetch_sleeper_league_payload(sleeper_league_id)
            sleeper_options, sleeper_option_map = build_sleeper_options(sleeper_users, sleeper_rosters)
    except Exception as e:
        st.error(f"Could not load Sleeper teams: {e}")
        st.stop()

    if not league_teams:
        st.warning("No imported teams found yet. Finalize contract import first.")
        st.stop()

        st.markdown("### Owner Mapping")

    progress_pct = (mapped_count / team_count) if team_count else 0
    st.progress(progress_pct)
    st.caption(f"{mapped_count}/{team_count} teams connected")

    with st.form("owner_mapping_form"):
        selected_map = {}

        header_col1, header_col2 = st.columns([1, 2])
        with header_col1:
            st.markdown("**Contract Owner**")
        with header_col2:
            st.markdown("**Sleeper Team**")

        st.divider()

        for team in league_teams:
            row_id = team.get("id")
            owner_name = team.get("owner_name") or team.get("team_name") or "Unknown Owner"
            current_roster_id = team.get("sleeper_roster_id")
            current_label = "-- choose Sleeper team --"

            if current_roster_id:
                for label, payload in sleeper_option_map.items():
                    if str(payload.get("sleeper_roster_id")) == str(current_roster_id):
                        current_label = label
                        break

            default_index = sleeper_options.index(current_label) if current_label in sleeper_options else 0

            col1, col2 = st.columns([1, 2])

            with col1:
                st.markdown(f"**{owner_name}**")

            with col2:
                selected = st.selectbox(
                    f"Sleeper team for {owner_name}",
                    sleeper_options,
                    index=default_index,
                    key=f"map_{row_id}",
                    label_visibility="collapsed",
                )

            selected_map[row_id] = selected

        submitted = st.form_submit_button("Save Owner Mapping", use_container_width=True)

    if submitted:
        updates = 0

        for team in league_teams:
            row_id = team.get("id")
            selected = selected_map.get(row_id)

            if not row_id or not selected or selected == "-- choose Sleeper team --":
                continue

            payload = sleeper_option_map[selected]

            sb_client.table("league_teams").update(
                {
                    "sleeper_roster_id": payload["sleeper_roster_id"],
                    "sleeper_user_id": payload["sleeper_user_id"],
                    "sleeper_team_name": payload["sleeper_team_name"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", row_id).execute()

            updates += 1

        log_commish_action("save_owner_mapping", {"updated_rows": updates})
        st.success(f"Saved {updates} owner mapping row(s).")
        st.rerun()

    st.divider()

    st.markdown("### Owner Access")
    st.caption("Add owner or co-owner emails for each mapped team.")

    access_rows = []

    for team in league_teams:
        access_rows.append(
            {
                "Contract Owner": team.get("owner_name"),
                "Sleeper Team": team.get("sleeper_team_name") or "Unmapped",
                "Owner Email": team.get("owner_email") or "",
                "Co-Owner Email": team.get("co_owner_email") or "",
            }
        )

    access_df = pd.DataFrame(access_rows)

    edited_access = st.data_editor(
        access_df,
        use_container_width=True,
        hide_index=True,
        disabled=["Contract Owner", "Sleeper Team"],
        column_config={
            "Owner Email": st.column_config.TextColumn("Owner Email"),
            "Co-Owner Email": st.column_config.TextColumn("Co-Owner Email"),
        },
    )

    if st.button("Save Owner Access", use_container_width=True):
        for i, row in edited_access.iterrows():
            team = league_teams[i]

            sb_client.table("league_teams").update(
                {
                    "owner_email": row.get("Owner Email") or None,
                    "co_owner_email": row.get("Co-Owner Email") or None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", team["id"]).execute()

        st.success("Owner access saved.")
        st.rerun()


# ============================================================
# MY ACCESS
# ============================================================
elif section == "My Access":
    section_header(
        "My Access",
        "View your league role and team access.",
    )

    st.info(
        "Owner and co-owner account tools will live here. Commissioner-only league controls are hidden from this role."
    )


# ============================================================
# LEAGUE MANAGER TOOLS
# ============================================================
elif section == "League Manager Tools":
    if not is_commissioner:
        show_commissioner_gate()
        st.stop()

    st.markdown("## League Manager Tools")
    st.caption("Commissioner-only controls for contracts, transactions, and league rules.")

    tools = [
        "League Rules",
        "Contract Editor",
        "Manual Add",
        "Manual Drop",
        "Trade Tools",
        "Owner Matching",
    ]

    if "lm_tool" not in st.session_state:
        st.session_state["lm_tool"] = "League Rules"

    cols = st.columns(len(tools))

    for tool_name, col in zip(tools, cols):
        with col:
            is_active = st.session_state["lm_tool"] == tool_name
            button_label = f"✓ {tool_name}" if is_active else tool_name

            if st.button(
                button_label,
                key=f"lm_tool_{tool_name}",
                use_container_width=True,
            ):
                st.session_state["lm_tool"] = tool_name
                st.rerun()

    tool = st.session_state["lm_tool"]

    st.divider()

    if tool == "League Rules":
        st.markdown("### League Rules")
        st.caption("Commissioner settings for this league only.")

        st.markdown("#### Salary + Contract Defaults")

        c1, c2, c3 = st.columns(3)
        with c1:
            salary_cap = st.number_input("Salary cap", min_value=0, value=300)
        with c2:
            max_contract_years = st.number_input("Max contract years", min_value=1, max_value=10, value=4)
        with c3:
            default_dead_cap_pct = st.number_input("Default dead cap %", min_value=0.0, max_value=100.0, value=0.0)

        c4, c5, c6 = st.columns(3)
        with c4:
            default_fa_years = st.number_input("Default free agent years", min_value=1, max_value=5, value=1)
        with c5:
            default_fa_salary = st.number_input("Default free agent salary", min_value=0.0, value=1.0)
        with c6:
            default_waiver_salary = st.number_input("Default waiver salary", min_value=0.0, value=1.0)

        st.divider()
        st.markdown("#### Rookie Draft Defaults")

        r1, r2, r3 = st.columns(3)
        with r1:
            rookie_contract_years = st.number_input("Default rookie years", min_value=1, max_value=5, value=3)
        with r2:
            rookie_option_years = st.number_input("Rookie option years", min_value=0, max_value=3, value=1)
        with r3:
            rookie_scale_enabled = st.checkbox("Use rookie salary scale", value=True)

        st.divider()
        st.markdown("#### Auction Settings")

        a1, a2, a3 = st.columns(3)
        with a1:
            min_2_year_bid = st.number_input("2-year minimum bid", min_value=0.0, value=4.0)
        with a2:
            min_3_year_bid = st.number_input("3-year minimum bid", min_value=0.0, value=12.0)
        with a3:
            min_4_year_bid = st.number_input("4-year minimum bid", min_value=0.0, value=20.0)

        a4, a5 = st.columns(2)
        with a4:
            year_discount_pct = st.number_input("Year discount %", min_value=0.0, max_value=100.0, value=10.0)
        with a5:
            auction_reset_allowed = st.checkbox("Allow auction reset", value=True)

        if st.button("Save League Rules", use_container_width=True):
            log_commish_action(
                "save_league_rules",
                {
                    "salary_cap": salary_cap,
                    "max_contract_years": max_contract_years,
                    "default_dead_cap_pct": default_dead_cap_pct,
                    "default_fa_years": default_fa_years,
                    "default_fa_salary": default_fa_salary,
                    "default_waiver_salary": default_waiver_salary,
                    "rookie_contract_years": rookie_contract_years,
                    "rookie_option_years": rookie_option_years,
                    "rookie_scale_enabled": rookie_scale_enabled,
                    "min_2_year_bid": min_2_year_bid,
                    "min_3_year_bid": min_3_year_bid,
                    "min_4_year_bid": min_4_year_bid,
                    "year_discount_pct": year_discount_pct,
                    "auction_reset_allowed": auction_reset_allowed,
                },
            )
            st.success("League rules saved.")

    elif tool == "Contract Editor":
        st.markdown("### Contract Editor")
        st.caption("Select an existing player contract and edit it safely.")

        players = (
            sb_client.table("contracts")
            .select("player_name,sleeper_player_id")
            .eq("league_id", active_league_id)
            .order("player_name")
            .execute()
            .data
            or []
        )

        player_options = [
            f"{p.get('player_name')} ({p.get('sleeper_player_id')})"
            for p in players
            if p.get("player_name")
        ]

        contract_tag_map = {
            "Standard": "standard",
            "Rookie Contract": "rookie_contract",
            "FA Auction Contract": "fa_auction_contract",
            "Restricted FA": "restricted_fa",
            "Franchise Tag": "franchise_tag",
            "Manual Adjustment": "manual_adjustment",
        }

        c1, c2 = st.columns(2)

        with c1:
            player_search = st.selectbox(
                "Player",
                options=player_options,
                index=None,
                placeholder="Search or choose player...",
                key="contract_editor_player_select",
            )

            new_salary = st.number_input(
                "New salary",
                min_value=0.0,
                value=1.0,
            )

        with c2:
            new_years_left = st.number_input(
                "New years left",
                min_value=0,
                max_value=10,
                value=1,
            )

            contract_tag_display = st.selectbox(
                "Contract Tag",
                options=list(contract_tag_map.keys()),
            )

            contract_tag = contract_tag_map[contract_tag_display]

        notes = st.text_area("Reason / notes")

        if st.button("Log Contract Edit", use_container_width=True):
            log_commish_action(
                "edit_contract",
                {
                    "player_search": player_search,
                    "new_salary": new_salary,
                    "new_years_left": new_years_left,
                    "contract_tag": contract_tag,
                    "notes": notes,
                },
            )
            st.success("Contract edit logged.")

    elif tool == "Manual Add":
        st.markdown("### Manual Player Add")

        players = (
            sb_client.table("contracts")
            .select("player_name,sleeper_player_id")
            .eq("league_id", active_league_id)
            .order("player_name")
            .execute()
            .data
            or []
        )

        player_options = [
            f"{p.get('player_name')} ({p.get('sleeper_player_id')})"
            for p in players
            if p.get("player_name")
        ]

        c1, c2 = st.columns(2)

        with c1:
            player_search = st.selectbox(
                "Player",
                options=player_options,
                index=None,
                placeholder="Search or choose player...",
                key="manual_add_player_select",
            )

            target_team = st.selectbox(
                "Target team",
                [t.get("owner_name") or t.get("team_name") for t in league_teams],
            )

        with c2:
            salary = st.number_input(
                "Salary",
                min_value=0.0,
                value=1.0,
            )

            years = st.number_input(
                "Contract years",
                min_value=1,
                max_value=10,
                value=1,
            )

        notes = st.text_area("Reason / notes")

        if st.button("Log Manual Add", use_container_width=True):
            log_commish_action(
                "manual_player_add",
                {
                    "player_search": player_search,
                    "target_team": target_team,
                    "salary": salary,
                    "years": years,
                    "notes": notes,
                },
            )
            st.success("Manual add logged.")

    elif tool == "Manual Drop":
        st.markdown("### Manual Player Drop")

        players = (
            sb_client.table("contracts")
            .select("player_name,sleeper_player_id")
            .eq("league_id", active_league_id)
            .order("player_name")
            .execute()
            .data
            or []
        )

        player_options = [
            f"{p.get('player_name')} ({p.get('sleeper_player_id')})"
            for p in players
            if p.get("player_name")
        ]

        c1, c2 = st.columns(2)

        with c1:
            player_search = st.selectbox(
                "Player",
                options=player_options,
                index=None,
                placeholder="Search or choose player...",
                key="manual_drop_player_select",
            )

            drop_team = st.selectbox(
                "Current team",
                [t.get("owner_name") or t.get("team_name") for t in league_teams],
            )

        with c2:
            dead_cap = st.number_input(
                "Dead cap",
                min_value=0.0,
                value=0.0,
            )

        notes = st.text_area("Reason / notes")

        if st.button("Log Manual Drop", use_container_width=True):
            log_commish_action(
                "manual_player_drop",
                {
                    "player_search": player_search,
                    "drop_team": drop_team,
                    "dead_cap": dead_cap,
                    "notes": notes,
                },
            )
            st.success("Manual drop logged.")
    elif tool == "Trade Tools":
        st.markdown("### Trade Tools")
        st.caption("Build trades by entering what each team receives.")

        team_options = [
            t.get("owner_name") or t.get("team_name")
            for t in league_teams
            if t.get("owner_name") or t.get("team_name")
        ]

        contracts = (
            sb_client.table("contracts")
            .select("*")
            .eq("league_id", active_league_id)
            .execute()
            .data
            or []
        )

        def contract_team_name(c):
            return (
                c.get("owner_name")
                or c.get("team_name")
                or c.get("owner")
                or c.get("fantasy_team")
                or ""
            )

        def player_label(c):
            return f"{c.get('player_name')} ({c.get('sleeper_player_id')})"

        team_count_trade = st.segmented_control(
            "Number of teams",
            options=[2, 3, 4],
            default=2,
        )

        selected_teams = []
        team_cols = st.columns(team_count_trade)

        for i in range(team_count_trade):
            with team_cols[i]:
                selected_teams.append(
                    st.selectbox(
                        f"Team {i + 1}",
                        team_options,
                        key=f"trade_team_{i}",
                    )
                )

        if len(set(selected_teams)) != len(selected_teams):
            st.warning("Each trade slot must use a different team.")
            st.stop()

        st.divider()

        trade_payload = []
        receive_cols = st.columns(team_count_trade)

        for i, receiving_team in enumerate(selected_teams):
            other_teams = [t for t in selected_teams if t != receiving_team]

            eligible_contracts = [
                c for c in contracts
                if contract_team_name(c) in other_teams
            ]

            eligible_player_options = [
                player_label(c)
                for c in eligible_contracts
                if c.get("player_name")
            ]

            pick_options = [""] + [
                f"{from_team} — {year} Round {round_num}"
                for from_team in other_teams
                for year in range(2026, 2030)
                for round_num in range(1, 5)
            ]

            cash_from_options = [""] + other_teams

            with receive_cols[i]:
                st.markdown(f"#### {receiving_team} receives")

                players_received = st.multiselect(
                    "Players received",
                    options=eligible_player_options,
                    key=f"trade_players_received_{i}",
                )

                picks_received = st.multiselect(
                    "Draft picks received",
                    options=pick_options,
                    key=f"trade_picks_received_{i}",
                )

                cash_from_team = st.selectbox(
                    "Cash from",
                    options=cash_from_options,
                    key=f"trade_cash_from_{i}",
                )

                cash_received = st.number_input(
                    "Cash received",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    key=f"trade_cash_received_{i}",
                )

                trade_payload.append(
                    {
                        "receiving_team": receiving_team,
                        "players_received": players_received,
                        "draft_picks_received": picks_received,
                        "cash_from_team": cash_from_team,
                        "cash_received": cash_received,
                    }
                )

        st.divider()

        notes = st.text_area(
            "Trade notes",
            placeholder="Optional commissioner notes...",
        )

        if st.button("Process Trade", use_container_width=True):
            log_commish_action(
                "process_multi_team_trade",
                {
                    "teams": trade_payload,
                    "notes": notes,
                },
            )

            st.success(
                "Trade logged. Next step is wiring this to update contract ownership, draft pick ledger, and team cash balances."
            )

    elif tool == "Owner Matching":
        st.info(
            "Owner Mapping has temporarily been removed while the onboarding flow is being rebuilt."
    )


# ============================================================
# DRAFT CENTER
# ============================================================
elif section == "Draft Center":
    if not is_commissioner:
        show_commissioner_gate()
        st.stop()

    section_header(
        "Draft Center",
        "Commissioner-only offseason tools for rookie draft, draft results, and FA auction settings.",
    )

    draft_tab, auction_tab = st.tabs(["Rookie Draft", "FA Auction"])

    with draft_tab:
        st.markdown("### Upcoming Rookie Draft Board")
        st.caption("Enter rookie draft results by owner and pick.")

        top_col1, top_col2 = st.columns([2, 1])

        with top_col1:
            draft_year = st.selectbox(
                "Draft year",
                [2026, 2027],
                index=0,
                key="rookie_draft_year",
            )

        default_rookie_template = pd.DataFrame(
            [
                [1, 1, 15, 2, 25, 3],
                [1, 2, 12, 2, 25, 3],
                [1, 3, 9, 2, 25, 3],
                [1, 4, 8, 2, 25, 3],
                [1, 5, 6, 2, 25, 3],
                [1, 6, 5, 2, 25, 3],
                [1, 7, 4, 2, 25, 3],
                [1, 8, 4, 2, 25, 3],
                [1, 9, 4, 2, 25, 3],
                [1, 10, 4, 2, 25, 3],
                [2, 1, 3, 2, 15, 3],
                [2, 2, 3, 2, 15, 3],
                [2, 3, 3, 2, 15, 3],
                [2, 4, 3, 2, 15, 3],
                [2, 5, 3, 2, 15, 3],
                [2, 6, 3, 2, 15, 3],
                [2, 7, 3, 2, 15, 3],
                [2, 8, 3, 2, 15, 3],
                [2, 9, 3, 2, 15, 3],
                [2, 10, 3, 2, 15, 3],
                [3, 1, 1, 1, 7, 2],
                [3, 2, 1, 1, 7, 2],
                [3, 3, 1, 1, 7, 2],
                [3, 4, 1, 1, 7, 2],
                [3, 5, 1, 1, 7, 2],
                [3, 6, 1, 1, 7, 2],
                [3, 7, 1, 1, 7, 2],
                [3, 8, 1, 1, 7, 2],
                [3, 9, 1, 1, 7, 2],
                [3, 10, 1, 1, 7, 2],
            ],
            columns=["Round", "Pick", "Base Salary", "Base Years", "Option Salary", "Option Year"],
        )

        with top_col2:
            st.write("")
            with st.popover("Adjust Rookie Salaries"):
                edited_template = st.data_editor(
                    default_rookie_template,
                    use_container_width=True,
                    hide_index=True,
                    num_rows="dynamic",
                )

                if st.button("Save Rookie Salary Defaults", use_container_width=True):
                    log_commish_action(
                        "save_rookie_salary_defaults",
                        {"rows": edited_template.to_dict(orient="records")},
                    )
                    st.success("Rookie salary defaults saved.")

        team_names = [
            t.get("owner_name") or t.get("team_name")
            for t in league_teams
            if t.get("owner_name") or t.get("team_name")
        ]

        default_order = team_names[:10]

        rookie_players = (
            sb_client.table("sleeper_players")
            .select("full_name,sleeper_player_id,position")
            .order("full_name")
            .execute()
            .data
            or []
        )

        rookie_options = [
            f"{p.get('full_name')} — {p.get('position')} ({p.get('sleeper_player_id')})"
            for p in rookie_players
            if p.get("full_name")
        ]

        st.divider()

        st.markdown(
            """
<div class="draft-board-header">
    <h3>Draft Board</h3>
    <p class="small-muted">Select the owner and rookie for each individual pick.</p>
</div>
""",
            unsafe_allow_html=True,
        )

        draft_rows = []

        owner_options = team_names

        header_cols = st.columns([0.45, 1.55, 2, 1.55, 2, 1.55, 2])
        headers = [
            "",
            "",
            "Round 1",
            "",
            "Round 2",
            "",
            "Round 3",
        ]

        for col, header in zip(header_cols, headers):
            with col:
                st.markdown(f"**{header}**")

        st.markdown(
            "<hr style='margin:.25rem 0 .65rem 0;border-color:rgba(200,155,74,.25);'>",
            unsafe_allow_html=True,
        )

        for pick_idx in range(1, 11):
            row_cols = st.columns([0.45, 1.55, 2, 1.55, 2, 1.55, 2])

            with row_cols[0]:
                st.markdown(f"<div class='pick-number'>{pick_idx}</div>", unsafe_allow_html=True)

            for round_num, owner_col, player_col in [
                (1, row_cols[1], row_cols[2]),
                (2, row_cols[3], row_cols[4]),
                (3, row_cols[5], row_cols[6]),
            ]:
                default_owner_index = 0
                if len(default_order) >= pick_idx:
                    default_owner = default_order[pick_idx - 1]
                    if default_owner in owner_options:
                        default_owner_index = owner_options.index(default_owner)

                with owner_col:
                    selected_owner = st.selectbox(
                        f"Round {round_num}, Pick {pick_idx} Owner",
                        options=owner_options,
                        index=default_owner_index,
                        key=f"rookie_owner_{draft_year}_{round_num}_{pick_idx}",
                        label_visibility="collapsed",
                    )

                with player_col:
                    selected_player = st.selectbox(
                        f"Round {round_num}, Pick {pick_idx} Player",
                        options=rookie_options,
                        index=None,
                        placeholder="Search rookies...",
                        key=f"rookie_pick_{draft_year}_{round_num}_{pick_idx}",
                        label_visibility="collapsed",
                    )

                salary_row = default_rookie_template[
                    (default_rookie_template["Round"] == round_num)
                    & (default_rookie_template["Pick"] == pick_idx)
                ]

                salary = float(salary_row.iloc[0]["Base Salary"]) if not salary_row.empty else 1.0
                years = int(salary_row.iloc[0]["Base Years"]) if not salary_row.empty else 1

                draft_rows.append(
                    {
                        "draft_year": draft_year,
                        "round": round_num,
                        "pick": pick_idx,
                        "owner": selected_owner,
                        "player": selected_player,
                        "salary": salary,
                        "years": years,
                        "contract_tag": "rookie_contract",
                    }
                )

            st.markdown(
                "<hr style='margin:.25rem 0;border-color:rgba(200,155,74,.10);'>",
                unsafe_allow_html=True,
            )

        if st.button("Submit Rookie Draft Board", use_container_width=True):
            completed = [r for r in draft_rows if r["player"]]

            if not completed:
                st.warning("Add at least one rookie before submitting.")
                st.stop()

            log_commish_action(
                "submit_rookie_draft_board",
                {
                    "draft_year": draft_year,
                    "picks": completed,
                },
            )

            st.success(f"Submitted {len(completed)} rookie draft pick(s).")
    with auction_tab:
        st.markdown(
            """
<div class="auction-shell">
    <div class="auction-title-row">
        <h2>FA AUCTION CALCULATOR <span class="info-dot">i</span></h2>
        <p>Locked years show as — until the bid reaches this league's minimum threshold.</p>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )

        def select_auction_option(new_bid: int, new_years: int):
            st.session_state["auction_current_bid"] = float(new_bid)
            st.session_state["auction_current_years"] = new_years

        players = (
            sb_client.table("sleeper_players")
            .select("full_name,sleeper_player_id,position")
            .order("full_name")
            .execute()
            .data
            or []
        )

        player_options = [
            f"{p.get('full_name')} — {p.get('position')} ({p.get('sleeper_player_id')})"
            for p in players
            if p.get("full_name")
        ]

        contracts = (
            sb_client.table("contracts")
            .select("player_name,sleeper_player_id,owner_name")
            .eq("league_id", active_league_id)
            .execute()
            .data
            or []
        )

        contracted_ids = {
            str(c.get("sleeper_player_id")): c
            for c in contracts
            if c.get("sleeper_player_id")
        }

        with st.container():
            st.markdown('<div class="auction-panel">', unsafe_allow_html=True)

            top_cols = st.columns([1.25, 1.05, .95, 1.05])

            with top_cols[0]:
                selected_player = st.selectbox(
                    "PLAYER SEARCH",
                    options=player_options,
                    index=None,
                    placeholder="Search any player...",
                    key="auction_player_select",
                )

            player_name = ""
            selected_sleeper_id = None

            if selected_player:
                player_name = selected_player.split(" — ")[0]
                selected_sleeper_id = selected_player.split("(")[-1].replace(")", "").strip()

            if selected_sleeper_id and selected_sleeper_id in contracted_ids:
                contract = contracted_ids[selected_sleeper_id]
                current_owner = contract.get("owner_name") or "another team"

                st.warning(f"{player_name} is already under contract with {current_owner}.")

            with top_cols[1]:
                current_bid = st.number_input(
                    "CURRENT BID",
                    min_value=0.0,
                    value=8.0,
                    step=1.0,
                    format="%.0f",
                    key="auction_current_bid",
                )

            with top_cols[2]:
                current_years = st.selectbox(
                    "CURRENT BID YEARS",
                    [1, 2, 3, 4],
                    index=1,
                    key="auction_current_years",
                )

            with top_cols[3]:
                auction_winner = st.selectbox(
                    "CURRENT WINNER",
                    [""] + [t.get("owner_name") or t.get("team_name") for t in league_teams],
                    format_func=lambda x: "—" if x == "" else x,
                    key="auction_winner",
                )

            st.markdown('<div class="auction-divider"></div>', unsafe_allow_html=True)
            st.markdown('<div class="league-minimum-label">LEAGUE MINIMUMS</div>', unsafe_allow_html=True)

            min_cols = st.columns(3)

            with min_cols[0]:
                min_2_year = st.number_input("2-YEAR MINIMUM", min_value=0.0, value=4.0, step=1.0, format="%.0f", key="auction_min_2_year")

            with min_cols[1]:
                min_3_year = st.number_input("3-YEAR MINIMUM", min_value=0.0, value=12.0, step=1.0, format="%.0f", key="auction_min_3_year")

            with min_cols[2]:
                min_4_year = st.number_input("4-YEAR MINIMUM", min_value=0.0, value=20.0, step=1.0, format="%.0f", key="auction_min_4_year")

            def calc_required_bid(target_years: int) -> float | None:
                thresholds = {1: 0, 2: min_2_year, 3: min_3_year, 4: min_4_year}

                if current_bid < thresholds[target_years]:
                    return None

                diff = target_years - current_years

                if diff == 0:
                    return current_bid

                if diff > 0:
                    return current_bid / (1.1 ** diff)

                return current_bid * (1.1 ** abs(diff))

            thresholds = {1: 0, 2: min_2_year, 3: min_3_year, 4: min_4_year}
            valid_values = {years: calc_required_bid(years) for years in [1, 2, 3, 4]}

            card_cols = st.columns(4)

            for idx, years in enumerate([1, 2, 3, 4]):
                val = valid_values[years]
                label = f"{years} YEAR" if years == 1 else f"{years} YEARS"

                with card_cols[idx]:
                    if val is None:
                        st.markdown(
                            f"""
<div class="auction-option-card locked">
    <div class="auction-card-kicker">{label}</div>
    <div class="lock-icon">🔒</div>
    <div class="auction-card-value">—</div>
    <div class="auction-card-copy">Unlocks at ${thresholds[years]:.0f}</div>
</div>
""",
                            unsafe_allow_html=True,
                        )
                    else:
                        rounded_val = math.ceil(val)

                        st.markdown(
                            f"""
<div class="auction-option-card">
    <div class="auction-card-kicker">{label}</div>
    <div class="auction-card-value">${rounded_val}</div>
    <div class="auction-card-copy">Valid contract option</div>
</div>
""",
                            unsafe_allow_html=True,
                        )

                        st.button(
                            f"Select {years} Year{'s' if years > 1 else ''}",
                            key=f"select_auction_option_{years}",
                            use_container_width=True,
                            on_click=select_auction_option,
                            args=(rounded_val, years),
                        )

            if st.button("▣  Log Auction Finish", use_container_width=True, key="auction_finish_btn"):
                if not player_name or not auction_winner:
                    st.warning("Enter a player and auction winner first.")
                elif selected_sleeper_id and selected_sleeper_id in contracted_ids:
                    st.error("This player is already under contract. Confirm before logging this as a free agent auction.")
                else:
                    log_commish_action(
                        "finish_fa_auction",
                        {
                            "player_name": player_name,
                            "sleeper_player_id": selected_sleeper_id,
                            "winner": auction_winner,
                            "current_bid": current_bid,
                            "current_years": current_years,
                        },
                    )
                    st.success("Auction finish logged. Creating the contract can be wired next.")

            st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# AUDIT LOG
# ============================================================
elif section == "Audit Log":
    if not is_commissioner:
        show_commissioner_gate()
        st.stop()

    section_header(
        "Audit Log",
        "Review commissioner actions for this league.",
    )

    rows = safe_table("commissioner_action_log", "created_at")

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No commissioner action log entries found yet.")


# ============================================================
# PLATFORM ADMIN
# ============================================================
elif section == "Platform Admin":
    if not is_platform_admin:
        show_commissioner_gate()
        st.stop()

    section_header(
        "Platform Admin",
        "Platform-level tools for failed syncs, database repair, and cross-league debugging.",
    )

    admin_tool = st.selectbox(
        "Admin Tool",
        ["Failed Sync Queue", "Run Repair", "Database Tools", "Environment Check"],
    )

    if admin_tool == "Failed Sync Queue":
        rows = safe_table("failed_sync_events", "created_at")
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No failed sync events found.")

    elif admin_tool == "Run Repair":
        st.subheader("Repair Utilities")
        st.button("Rebuild League Cache", use_container_width=True)
        st.button("Recalculate Contracts", use_container_width=True)
        st.button("Validate Sleeper Player IDs", use_container_width=True)

    elif admin_tool == "Database Tools":
        st.subheader("Database Debug")
        st.write("Active League ID:", active_league_id)
        st.write("Role:", role)
        st.write("Supabase URL loaded:", bool(SUPABASE_URL))
        st.write("Supabase Key loaded:", bool(SUPABASE_KEY))

    elif admin_tool == "Environment Check":
        st.subheader("Environment")
        st.json(
            {
                "active_league_id": active_league_id,
                "role": role,
                "is_commissioner": is_commissioner,
                "is_platform_admin": is_platform_admin,
            }
        )
