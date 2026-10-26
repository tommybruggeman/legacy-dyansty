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
from auth import auth_client, require_login, service_client
from services.config import configured_value
from services.team_roster_state import invalidate_team_state_session_cache
import html

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
require_login()

PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR_STR = os.path.abspath(os.path.join(PAGES_DIR, ".."))
sys.path.append(ROOT_DIR_STR)
sys.path.append(os.path.join(ROOT_DIR_STR, "lib"))

from services.invitations import (
    INVITE_LINK_SESSION_NAMESPACE,
    can_offer_invite_role,
    classify_invite_status,
    clear_invite_links,
    create_invitation,
    list_team_invitations,
    remember_invite_link,
    resend_invitation,
    revoke_invitation,
    safe_invite_link_payload,
    validate_email,
)
from services.season_rollover_ui import render_season_rollover_control
from services.season_rollover_owner_ui import render_owner_rollover_decisions
from services.commissioner_draft_ui import render_draft, render_fa_auction
from services.league_tools_ui import (
    render_contract_lookup,
    render_league_rules,
    render_tool_picker,
)
from services.free_agents import (
    build_free_agent_results,
    load_league_free_agent_state,
    load_player_universe,
)
from services.offseason_transactions import OffseasonTransactionService
from services.canonical_trades import (
    DraftPickMovement,
    PlayerMovement,
    RetainedSalary,
    RetainedSeason,
    complete_rookie_draft,
    configure_draft_lifecycle,
    draft_season_window,
    execute_canonical_trade,
    is_draft_pick_tradeable,
    team_selection_options,
)
from decimal import Decimal
from uuid import uuid4


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

.ld-kicker {
    color: #E2BC5B;
    font-size: .7rem;
    font-weight: 700;
    letter-spacing: .16em;
    text-transform: uppercase;
}

.ld-title {
    margin: .1rem 0 .2rem 0;
    font-size: 2.1rem;
    font-weight: 800;
    letter-spacing: -.02em;
    color: var(--cream);
    line-height: 1.1;
}

/* Section pills: one rounded bar, the open section filled gold. */
.st-key-control_section [data-testid="stButtonGroup"],
[data-testid="stElementContainer"]:has(> .st-key-control_section),
.st-key-control_section {
    width: 100% !important;
}
.st-key-control_section [role="radiogroup"] {
    display: flex !important;
    width: 100% !important;
    max-width: 640px !important;
    gap: 4px !important;
    background: #0C1917;
    border: 1px solid rgba(245,235,215,.08);
    border-radius: 999px;
    padding: 4px;
}
.st-key-control_section button {
    flex: 1 1 0 !important;
    border: 0 !important;
    border-radius: 999px !important;
    background: transparent !important;
    color: #9DA89C !important;
    min-height: 38px !important;
    padding: 0 .4rem !important;
    box-shadow: none !important;
}
.st-key-control_section button p {
    font-size: .88rem !important;
    white-space: nowrap;
}
.st-key-control_section button[aria-checked="true"] {
    background: #E2BC5B !important;
    color: #1a1405 !important;
}
.st-key-control_section button[aria-checked="true"] p {
    color: #1a1405 !important;
    font-weight: 600 !important;
}

/* Draft year chips. */
.st-key-draft_room_year_pick [role="radiogroup"] {
    border: 0 !important;
    background: transparent !important;
    padding: 0 !important;
    gap: 6px !important;
}
.st-key-draft_room_year_pick button {
    border-radius: 999px !important;
    border-color: rgba(200,155,74,.35) !important;
    background: transparent !important;
    min-height: 34px !important;
}
.st-key-draft_room_year_pick button[aria-checked="true"] {
    background: #E2BC5B !important;
    border-color: #E2BC5B !important;
}
.st-key-draft_room_year_pick button[aria-checked="true"] p {
    color: #1a1405 !important;
    font-weight: 600 !important;
}

@media (max-width: 768px) {
    .block-container {
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        padding-top: 1rem !important;
    }
    .ld-title { font-size: 1.75rem; }
    .st-key-control_section button p { font-size: .8rem !important; }
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
        configured_value("SUPABASE_URL"),
        (
            configured_value("SUPABASE_ANON_KEY")
            or configured_value("SUPABASE_KEY")
        ),
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
        self._count = None
        self._head = False
        self._range = None

    def select(self, cols: str = "*", count: str | None = None, head: bool = False):
        self._select = cols
        self._count = count
        self._head = head
        return self

    def eq(self, col: str, val):
        self._filters[col] = f"eq.{val}"
        return self

    def order(self, col: str, desc: bool = False):
        self._order = (col, desc)
        return self

    def range(self, start: int, end: int):
        self._range = (int(start), int(end))
        return self

    def execute(self):
        url = f"{self.base}/rest/v1/{self.name}"
        params = {"select": self._select}
        params.update(self._filters)

        if self._order:
            params["order"] = f"{self._order[0]}.{'desc' if self._order[1] else 'asc'}"

        headers = dict(self.h)
        if self._count == "exact": headers["Prefer"] = "count=exact"
        if self._range: headers["Range"] = f"{self._range[0]}-{self._range[1]}"
        r = requests.head(url, headers=headers, params=params, timeout=20) if self._head else requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 404:
            return _Resp([])
        r.raise_for_status()
        content_range = r.headers.get("Content-Range", "")
        count = int(content_range.rsplit("/", 1)[1]) if "/" in content_range and content_range.rsplit("/", 1)[1] != "*" else None
        return _Resp([] if self._head else r.json(), count)

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
sb_client = auth_client()

# ============================================================
# Access control
# ============================================================
role = st.session_state.get("role")
active_league_id = st.session_state.get("active_league_id")
from season_engine import SeasonResolver
from services.strict_pagination import complete_rows
canonical_season = SeasonResolver(sb_client).get_active_season(active_league_id).season if active_league_id else None

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

        rows = complete_rows(client, table_name, filters={"league_id": active_league_id} if active_league_id else {})
        if order_col:
            rows.sort(key=lambda row: str(row.get(order_col) or ""))
        return rows

    except Exception as e:
        st.error(f"Could not load {table_name}: {e}")
        return []

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


def fetch_league_rules() -> dict:
    try:
        client = sb_client if sb_client else sb

        rows = (
            client.table("league_rules")
            .select("*")
            .eq("league_id", active_league_id)
            .execute()
            .data
            or []
        )

        return rows[0] if rows else {}

    except Exception:
        return {}

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


def load_connected_memberships(league_id: str) -> dict[str, list[dict]]:
    if not sb_client or not league_id:
        return {}

    try:
        rows = complete_rows(sb_client, "league_memberships", "id,user_id,role,league_team_id,created_at",
                             filters={"league_id": league_id})
    except Exception:
        return {}

    grouped: dict[str, list[dict]] = {}

    for row in rows:
        team_id = row.get("league_team_id")

        if not team_id:
            continue

        grouped.setdefault(team_id, []).append(row)

    return grouped


def load_invites_by_team(league_id: str) -> dict[str, list[dict]]:
    result = list_team_invitations(league_id=league_id, sb=sb_client)

    if not result.get("ok"):
        st.warning(result.get("message") or "Could not load invitations.")
        return {}

    grouped: dict[str, list[dict]] = {}

    for invite in result.get("invitations", []):
        team_id = invite.get("league_team_id")

        if not team_id:
            continue

        grouped.setdefault(team_id, []).append(invite)

    return grouped


def render_invitation_result(result: dict):
    message = result.get("message") or "Invitation action complete."

    if result.get("ok"):
        st.success(message)
    else:
        st.error(message)

    invite_url = result.get("invite_url")

    if invite_url:
        st.caption("Copyable invitation link")
        st.code(invite_url)


def reset_invite_links_when_league_changes(league_id: str | None) -> None:
    state_key = "owner_invite_links_active_league_id"
    previous_league_id = st.session_state.get(state_key)

    if previous_league_id == league_id:
        return

    clear_invite_links(st.session_state)
    st.session_state[state_key] = league_id


def store_invitation_link(result: dict, invited_role: str) -> None:
    if not result.get("ok") or not result.get("invite_url"):
        return

    payload = safe_invite_link_payload(result, invited_role=invited_role)
    remember_invite_link(st.session_state, payload)


def render_retained_invite_links(
    *,
    league_id: str,
    league_team_id: str,
    invited_role: str | None = None,
) -> None:
    prefix = f"{INVITE_LINK_SESSION_NAMESPACE}|"

    for key in list(st.session_state.keys()):
        if not str(key).startswith(prefix):
            continue

        payload = st.session_state.get(key) or {}

        if payload.get("league_id") != league_id:
            st.session_state.pop(key, None)
            continue

        if payload.get("league_team_id") != league_team_id:
            continue

        if invited_role and payload.get("invited_role") != invited_role:
            continue

        if classify_invite_status({"status": "pending", "expires_at": payload.get("expires_at")}) != "pending_active":
            st.session_state.pop(key, None)
            continue

        label_role = str(payload.get("invited_role") or "owner").replace("_", "-")
        email = payload.get("email") or "invited owner"
        st.caption(f"Copy and share this {label_role} link for {email}")
        st.code(payload.get("invite_url"))

        if st.button(
            "Clear link",
            key=f"clear_invite_link_{league_team_id}_{label_role}_{payload.get('invitation_id')}",
        ):
            st.session_state.pop(key, None)
            st.rerun()


reset_invite_links_when_league_changes(active_league_id)


# ============================================================
# League context
# ============================================================
league = fetch_active_league()
league_rules = fetch_league_rules()
league_name = league.get("name") if league else "Current League"
sleeper_league_id = league.get("sleeper_league_id") if league else None

league_teams = safe_table("league_teams", "owner_name")
mapped_count = len([t for t in league_teams if t.get("sleeper_roster_id")])
team_count = len(league_teams)


# ============================================================
# Header + section pills
# ============================================================
st.markdown(
    f"""
<div class="ld-kicker">{"Commissioner" if is_commissioner else html.escape(str(league_name))}</div>
<div class="ld-title">League Control</div>
""",
    unsafe_allow_html=True,
)

nav_options = ["Owners"]

if is_commissioner:
    nav_options += ["League Tools", "Draft", "FA Auction"]
else:
    nav_options += ["My Access"]

if is_platform_admin:
    nav_options.append("Platform Admin")

section = st.segmented_control(
    "Control Center",
    nav_options,
    default=nav_options[0],
    key="control_section",
    label_visibility="collapsed",
) or nav_options[0]

# ============================================================
# OWNERS
# ============================================================
if section == "Owners":
    st.markdown(
        """
<style>
.ow-linked {
    display: flex; align-items: center; gap: 10px;
    background: rgba(19,148,94,.12); border: 1px solid rgba(95,211,156,.4);
    border-radius: 14px; padding: 10px 14px; font-size: .92rem; color: #d6f3e3;
}
.ow-linked b { flex: 1; font-weight: 600; }
.ow-setup-k { color: #E2BC5B; font-size: .72rem; font-weight: 700; letter-spacing: .12em; }
.ow-setup-t { font-size: 1.15rem; font-weight: 700; color: #F5EBD7; margin: .1rem 0 .2rem 0; }
[class*="st-key-owner_card_"], .st-key-owner_setup {
    background: #0C1917;
    border: 1px solid rgba(245,235,215,.08);
    border-radius: 16px;
    padding: 12px 14px 4px 14px;
}
.st-key-owner_setup { background: linear-gradient(160deg,#16301f,#0C1917); border-color: rgba(200,155,74,.28); padding-bottom: 12px; }
.ow-top { display: flex; align-items: center; gap: 10px; }
.ow-av {
    width: 36px; height: 36px; border-radius: 50%; flex: none;
    background: #1d3524; color: #E2BC5B; font-weight: 700; font-size: .8rem;
    display: flex; align-items: center; justify-content: center;
}
.ow-nm { flex: 1; min-width: 0; font-weight: 600; font-size: .98rem; color: #F5EBD7; }
.ow-nm small { display: block; color: #9DA89C; font-weight: 400; font-size: .78rem; }
.ow-pill { font-size: .7rem; font-weight: 600; border-radius: 999px; padding: 3px 10px; white-space: nowrap; }
.ow-pill.g { background: rgba(19,148,94,.18); color: #5fd39c; }
.ow-pill.y { background: rgba(226,188,91,.15); color: #E2BC5B; }
.ow-pill.r { background: rgba(169,74,74,.25); color: #f08a86; }
.ow-mem { display: flex; gap: 6px; flex-wrap: wrap; margin: .55rem 0 .35rem 0; }
.ow-mem span {
    font-size: .76rem; border: 1px solid rgba(245,235,215,.1); border-radius: 999px;
    padding: 3px 10px; color: #d9cfbb;
}
/* Small outline pills in one row, on phones too. */
[class*="st-key-owner_card_"] [data-testid="stHorizontalBlock"],
.st-key-owner_links_row [data-testid="stHorizontalBlock"],
.st-key-owner_setup [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: .5rem !important;
}
[class*="st-key-owner_card_"] [data-testid="stColumn"],
.st-key-owner_links_row [data-testid="stColumn"],
.st-key-owner_setup [data-testid="stColumn"] {
    min-width: 0 !important;
    width: auto !important;
    flex: 1 1 0 !important;
}
.st-key-owner_links_row [data-testid="stColumn"]:last-child { flex: 0 0 auto !important; }
[class*="st-key-owner_card_"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:empty { display: none; }
[class*="st-key-owner_card_"] .stButton button,
.st-key-owner_links_row .stButton button {
    background: transparent !important;
    border: 1px solid rgba(200,155,74,.45) !important;
    color: #E2BC5B !important;
    border-radius: 999px !important;
    min-height: 32px !important;
    padding: 0 12px !important;
    box-shadow: none !important;
}
[class*="st-key-owner_card_"] .stButton button p,
.st-key-owner_links_row .stButton button p {
    color: #E2BC5B !important;
    font-size: .8rem !important;
    font-weight: 600 !important;
    white-space: nowrap;
}
</style>
""",
        unsafe_allow_html=True,
    )

    if not sleeper_league_id:
        st.warning("No Sleeper league is connected yet.")

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

    if not league_teams:
        st.warning("No imported teams found yet. Finalize contract import first.")
        st.stop()

    # --------------------------------------------------------
    # Sleeper links: a setup step only while something is unlinked
    # --------------------------------------------------------
    all_linked = team_count > 0 and mapped_count == team_count
    editing_links = st.session_state.get("owner_links_editing", False)

    if (all_linked and not editing_links) or not is_commissioner:
        links_row = st.container(key="owner_links_row")
        link_col, edit_col = links_row.columns([4, 1], vertical_alignment="center")
        with link_col:
            st.markdown(
                f'<div class="ow-linked">{"✓" if all_linked else "•"} '
                f'<b>{mapped_count}/{team_count} teams linked to Sleeper</b></div>',
                unsafe_allow_html=True,
            )
        with edit_col:
            if is_commissioner and st.button("Edit links", use_container_width=True, key="owner_links_edit"):
                st.session_state["owner_links_editing"] = True
                st.rerun()
    else:
        try:
            with st.spinner("Loading Sleeper teams..."):
                sleeper_users, sleeper_rosters = fetch_sleeper_league_payload(sleeper_league_id)
                sleeper_options, sleeper_option_map = build_sleeper_options(sleeper_users, sleeper_rosters)
        except Exception as e:
            st.error(f"Could not load Sleeper teams: {e}")
            st.stop()

        def guess_sleeper_label(owner_name: str) -> str | None:
            """Suggest the Sleeper team whose handle contains the owner's name."""
            words = [w.casefold() for w in str(owner_name or "").split() if len(w) > 2]
            hits = [
                label for label in sleeper_options[1:]
                if any(w in label.casefold().replace(" ", "") for w in words)
            ]
            return hits[0] if len(hits) == 1 else None

        with st.container(key="owner_setup"):
            st.markdown(
                f'<div class="ow-setup-k">{"EDIT SLEEPER LINKS" if all_linked else "LEAGUE SETUP"}</div>'
                '<div class="ow-setup-t">Link owners to Sleeper teams</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "This only shows when a league is new or a team changes hands. "
                "Unlinked owners get a suggested match when one is obvious."
            )
            with st.form("owner_mapping_form", border=False):
                selected_map = {}

                for team in league_teams:
                    row_id = team.get("id")
                    owner_name = team.get("owner_name") or team.get("team_name") or "Unknown Owner"
                    current_roster_id = team.get("sleeper_roster_id")
                    current_label = None

                    if current_roster_id:
                        for label, payload in sleeper_option_map.items():
                            if str(payload.get("sleeper_roster_id")) == str(current_roster_id):
                                current_label = label
                                break
                    current_label = current_label or guess_sleeper_label(owner_name) or sleeper_options[0]

                    name_col, pick_col = st.columns([1, 1.3], vertical_alignment="center")
                    with name_col:
                        st.markdown(f"**{html.escape(owner_name)}**")
                    with pick_col:
                        selected_map[row_id] = st.selectbox(
                            f"Sleeper team for {owner_name}",
                            sleeper_options,
                            index=sleeper_options.index(current_label) if current_label in sleeper_options else 0,
                            key=f"map_{row_id}",
                            label_visibility="collapsed",
                        )

                submitted = st.form_submit_button("Save links", use_container_width=True, type="primary")
                cancelled = all_linked and st.form_submit_button("Cancel", use_container_width=True)

        if cancelled:
            st.session_state["owner_links_editing"] = False
            st.rerun()

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

            st.session_state["owner_links_editing"] = False
            st.success(f"Saved {updates} Sleeper link(s).")
            st.rerun()

    # --------------------------------------------------------
    # One card per owner: who joined, invites, + co-owner
    # --------------------------------------------------------
    if not is_commissioner:
        st.info("Only commissioners can manage owner invitations.")
        st.stop()

    connected_by_team = load_connected_memberships(active_league_id)
    invites_by_team = load_invites_by_team(active_league_id)

    def initials(name: str) -> str:
        parts = [p for p in str(name or "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def short_date(value) -> str:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%b %-d")
        except (TypeError, ValueError):
            return ""

    for team in sorted(league_teams, key=lambda t: str(t.get("owner_name") or t.get("team_name") or "")):
        team_id = team.get("id")
        owner_name = team.get("owner_name") or team.get("team_name") or "Unknown Owner"
        sleeper_name = team.get("sleeper_team_name") or team.get("team_name") or "Not linked"
        first_name = owner_name.split()[0] if owner_name.split() else owner_name
        connected_members = connected_by_team.get(team_id, [])
        team_invites = invites_by_team.get(team_id, [])

        classified_invites = [
            {**invite, "_classified_status": classify_invite_status(invite)}
            for invite in team_invites
        ]
        pending_invites = [
            invite
            for invite in classified_invites
            if invite.get("_classified_status") == "pending_active"
        ]
        accepted_emails = {
            str(invite.get("role") or ""): invite.get("email")
            for invite in classified_invites
            if invite.get("_classified_status") == "accepted"
        }
        latest_invite_status = (
            classified_invites[0].get("_classified_status") if classified_invites else None
        )

        if connected_members:
            clear_invite_links(
                st.session_state,
                league_id=active_league_id,
                league_team_id=team_id,
                invited_role="owner",
            )
            for member in connected_members:
                member_role = str(member.get("role") or "").replace("-", "_")
                if member_role in {"owner", "co_owner"}:
                    clear_invite_links(
                        st.session_state,
                        league_id=active_league_id,
                        league_team_id=team_id,
                        invited_role=member_role,
                    )

        for invite in classified_invites:
            if invite.get("_classified_status") in {"accepted", "expired", "revoked"}:
                clear_invite_links(
                    st.session_state,
                    league_id=active_league_id,
                    league_team_id=team_id,
                    invited_role=invite.get("role"),
                    invitation_id=invite.get("id"),
                )

        if connected_members:
            pill = '<span class="ow-pill g">Joined</span>'
        elif pending_invites:
            pill = '<span class="ow-pill y">Invite sent</span>'
        elif latest_invite_status == "expired":
            pill = '<span class="ow-pill r">Invite expired</span>'
        else:
            pill = '<span class="ow-pill r">Not invited</span>'

        chips = []
        for member in connected_members:
            role = str(member.get("role") or "member").replace("-", "_")
            who = accepted_emails.get(role) or (first_name if role == "owner" else "")
            label = {"owner": "owner", "co_owner": "co-owner", "commissioner": "commissioner"}.get(role, role)
            chips.append(f"<span>{'👥' if role == 'co_owner' else '👤'} {html.escape(str(who or ''))}{' · ' if who else ''}{label}</span>")
        for invite in pending_invites:
            role = str(invite.get("role") or "owner").replace("_", "-")
            expires = short_date(invite.get("expires_at"))
            chips.append(
                f"<span>✉️ {html.escape(str(invite.get('email') or ''))} · {role}"
                f"{' · expires ' + expires if expires else ''}</span>"
            )

        with st.container(key=f"owner_card_{team_id}"):
            st.markdown(
                f'<div class="ow-top"><div class="ow-av">{initials(owner_name)}</div>'
                f'<div class="ow-nm">{html.escape(owner_name)}<small>{html.escape(sleeper_name)}</small></div>{pill}</div>'
                + (f'<div class="ow-mem">{"".join(chips)}</div>' if chips else ""),
                unsafe_allow_html=True,
            )

            if connected_members:
                invite_role = (
                    "co_owner"
                    if can_offer_invite_role(
                        connected_members=connected_members,
                        active_invites=pending_invites,
                        invite_role="co_owner",
                    )
                    else None
                )
            else:
                invite_role = "owner"

            form_key = f"owner_invite_open_{team_id}"
            buttons = st.columns(3)
            if invite_role and not pending_invites:
                with buttons[0]:
                    label = "+ Co-owner" if connected_members else "+ Invite"
                    if st.button(label, key=f"open_invite_{team_id}", use_container_width=True):
                        st.session_state[form_key] = not st.session_state.get(form_key, False)
                        st.rerun()

            if pending_invites:
                selected_invite = pending_invites[0]
                selected_invite_id = selected_invite.get("id")
                with buttons[0]:
                    if st.button("Resend", key=f"resend_invite_{team_id}", use_container_width=True):
                        result = resend_invitation(selected_invite_id, sb=sb_client)
                        clear_invite_links(
                            st.session_state,
                            league_id=active_league_id,
                            league_team_id=team_id,
                            invited_role=selected_invite.get("role"),
                            invitation_id=selected_invite_id,
                        )
                        store_invitation_link(result, selected_invite.get("role"))
                        render_invitation_result(result)
                with buttons[1]:
                    if st.button("Cancel invite", key=f"revoke_invite_{team_id}", use_container_width=True):
                        result = revoke_invitation(selected_invite_id, sb=sb_client)
                        clear_invite_links(
                            st.session_state,
                            league_id=active_league_id,
                            league_team_id=team_id,
                            invited_role=selected_invite.get("role"),
                            invitation_id=selected_invite_id,
                        )
                        render_invitation_result(result)
                        if result.get("ok"):
                            st.rerun()

            if invite_role and not pending_invites and st.session_state.get(form_key):
                with st.form(f"invite_form_{team_id}", border=False):
                    invite_email = st.text_input(
                        "Email",
                        placeholder="name@email.com",
                        key=f"invite_email_{team_id}",
                    )
                    if connected_members:
                        create_role = "co_owner"
                        st.caption("Co-owners can set lineups and make moves for this team.")
                    else:
                        role_label = st.segmented_control(
                            "Access",
                            ["Owner", "Co-owner"],
                            default="Owner",
                            key=f"invite_role_{team_id}",
                        )
                        create_role = "co_owner" if role_label == "Co-owner" else "owner"
                    send = st.form_submit_button(
                        "Send new invite" if latest_invite_status == "expired" else "Send invite",
                        use_container_width=True,
                        type="primary",
                    )
                if send:
                    if not validate_email(invite_email):
                        st.error("Enter a valid email address.")
                    else:
                        result = create_invitation(
                            league_id=active_league_id,
                            league_team_id=team_id,
                            email=invite_email,
                            invite_role=create_role,
                            sb=sb_client,
                        )
                        store_invitation_link(result, create_role)
                        render_invitation_result(result)
                        if result.get("ok"):
                            st.session_state[form_key] = False

            render_retained_invite_links(
                league_id=active_league_id,
                league_team_id=team_id,
            )


# ============================================================
# MY ACCESS
# ============================================================
elif section == "My Access":
    section_header(
        "My Access",
        "View your league role and team access.",
    )

    st.info("Owner and co-owner rollover decisions are separate from commissioner-only controls.")
    render_owner_rollover_decisions(sb_client, active_league_id)


# ============================================================
# LEAGUE MANAGER TOOLS
# ============================================================
elif section == "League Tools":
    if not is_commissioner:
        show_commissioner_gate()
        st.stop()

    picked = render_tool_picker()
    legacy_names = {
        "rules": "League Rules",
        "ai": "League AI",
        "contracts": "Contract Editor",
        "trades": "Trade Tools",
        "sync": "Sleeper Sync",
        "rollover": "Season Rollover",
    }
    tool = legacy_names.get(picked)
    if picked == "add_drop":
        add_or_drop = st.segmented_control(
            "Add or drop",
            ["Add player", "Drop player"],
            default="Add player",
            key="lm_add_drop",
            label_visibility="collapsed",
        ) or "Add player"
        tool = "Manual Add" if add_or_drop == "Add player" else "Manual Drop"

    if tool == "Season Rollover":
        render_season_rollover_control(sb_client, active_league_id, league_name)

    elif tool == "League Rules":
        render_league_rules(sb_client, active_league_id, league_rules)

    elif tool == "League AI":
        from league_ai.settings_ui import render_league_ai_settings

        render_league_ai_settings(sb_client, active_league_id, sleeper_league_id)

    elif tool == "Contract Editor":
        render_contract_lookup(sb_client, active_league_id, canonical_season)

    elif tool == "Manual Add":
        st.markdown("### Manual Player Add")

        fa_results = build_free_agent_results(
            load_player_universe(service_client()),
            load_league_free_agent_state(service_client(), active_league_id),
            active_season=canonical_season,
        )
        player_options = [f"{p.player} ({p.sleeper_player_id})" for p in fa_results.current]

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
            player_id = player_search.rsplit("(", 1)[-1].rstrip(")")
            team_id = next(t.get("id") for t in league_teams if (t.get("owner_name") or t.get("team_name")) == target_team)
            OffseasonTransactionService(sb_client, active_league_id).acquire(
                player_id=player_id, league_team_id=team_id, season=canonical_season,
                salary=salary, years=years, acquisition_type="commissioner_manual_add",
                idempotency_key=f"manual-add:{active_league_id}:{canonical_season}:{player_id}", notes=notes,
            )
            invalidate_team_state_session_cache(st.session_state)
            st.success("Manual add completed.")

    elif tool == "Sleeper Sync":
        st.markdown("### Sleeper Sync")
        st.caption(
            "Owner moves made in Sleeper convert into contracts automatically. "
            "Taxi and IR stay manual."
        )

        sync_rows = (
            sb_client.table("league_sleeper_sync")
            .select("*")
            .eq("league_id", active_league_id)
            .execute()
            .data
            or []
        )

        if not sync_rows:
            st.warning(
                "No sync configuration for this league yet. Run the "
                "`20261112_sleeper_sync_control.sql` migration in Supabase."
            )
        else:
            sync_row = sync_rows[0]
            sync_enabled = bool(sync_row.get("sync_enabled"))

            status_cols = st.columns(3)
            with status_cols[0]:
                st.metric("Status", "Running" if sync_enabled else "Paused")
            with status_cols[1]:
                st.metric("Last run", str(sync_row.get("last_run_at") or "Never")[:19])
            with status_cols[2]:
                st.metric("Last result", str(sync_row.get("last_run_status") or "—"))

            if sync_row.get("last_run_detail"):
                st.caption(str(sync_row["last_run_detail"]))

            if not sync_enabled and sync_row.get("pause_reason"):
                st.info(f"Paused: {sync_row['pause_reason']}")

            st.markdown("---")

            if sync_enabled:
                st.markdown("#### Pause the sync")
                st.caption(
                    "Pause before events where owners move already-contracted "
                    "players around in Sleeper — right after a draft, most often. "
                    "Anything that happens while paused is skipped permanently, "
                    "not applied later."
                )
                pause_reason = st.text_input(
                    "Reason",
                    value="Post-draft roster setup",
                    key="sleeper_sync_pause_reason",
                )
                if st.button("Pause sync", use_container_width=True):
                    sb_client.table("league_sleeper_sync").update({
                        "sync_enabled": False,
                        "pause_reason": pause_reason,
                        "paused_at": "now()",
                    }).eq("league_id", active_league_id).execute()
                    st.success("Sync paused.")
                    st.rerun()
            else:
                st.markdown("#### Resume the sync")
                st.warning(
                    "Resuming skips everything that happened while paused. Those "
                    "transactions will never be applied — enter any you still "
                    "want by hand before resuming."
                )
                if st.button("Resume sync", use_container_width=True, type="primary"):
                    # Advance the watermark to now so the pause window is a gap,
                    # not a queue waiting to flood in.
                    resume_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    sb_client.table("league_sleeper_sync").update({
                        "sync_enabled": True,
                        "pause_reason": None,
                        "paused_at": None,
                        "watermark_created_ms": resume_ms,
                    }).eq("league_id", active_league_id).execute()
                    st.success("Sync resumed.")
                    st.rerun()

        st.markdown("---")
        st.markdown("#### Needs review")

        exception_rows = (
            sb_client.table("sleeper_sync_exceptions")
            .select("*")
            .eq("league_id", active_league_id)
            .eq("status", "open")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
            .data
            or []
        )

        if not exception_rows:
            st.success("Nothing flagged.")
        else:
            st.caption(
                f"{len(exception_rows)} transaction(s) the sync could not apply."
            )
            for row in exception_rows:
                kind = str(row.get("exception_kind") or "unknown")
                with st.expander(
                    f"{kind} — {row.get('player_id') or 'transaction'} "
                    f"({str(row.get('created_at') or '')[:19]})"
                ):
                    st.write(row.get("detail") or "")
                    st.caption(
                        f"Sleeper transaction `{row.get('sleeper_transaction_id')}`"
                    )
                    if st.button(
                        "Mark resolved", key=f"resolve_exception_{row.get('id')}"
                    ):
                        sb_client.table("sleeper_sync_exceptions").update({
                            "status": "resolved",
                            "resolved_at": "now()",
                        }).eq("id", row.get("id")).execute()
                        st.rerun()

    elif tool == "Manual Drop":
        st.markdown("### Manual Player Drop")

        transaction_service = OffseasonTransactionService(
            sb_client,
            active_league_id,
            read_client=service_client(),
        )

        # Sourced from live canonical agreements, not the legacy "contracts"
        # table, so rookie-draft signings are droppable like anyone else.
        drop_catalog = transaction_service.list_manual_drop_candidates()

        drop_candidate_ids: dict[str, str] = {}
        for candidate in drop_catalog.candidates:
            label = candidate.label
            # Disambiguate only when two rostered players share a display label.
            if label in drop_candidate_ids:
                label = f"{label} ({candidate.player_id})"
            drop_candidate_ids[label] = candidate.player_id
        player_options = sorted(drop_candidate_ids)

        drop_resolution = None
        drop_error = None

        if not player_options:
            st.info("No player on an active roster currently has a droppable contract.")

        if drop_catalog.excluded:
            with st.expander(
                f"{len(drop_catalog.excluded)} rostered player(s) cannot be dropped",
                expanded=False,
            ):
                st.caption(
                    "These hold a live agreement but not exactly one, so the "
                    "canonical drop path refuses them. Usually a duplicate or "
                    "un-superseded contract that needs cleaning up."
                )
                for exclusion in drop_catalog.excluded:
                    st.markdown(
                        f"- **{exclusion.player_name}** "
                        f"(`{exclusion.player_id}`) — {exclusion.reason}"
                    )

        c1, c2 = st.columns(2)

        with c1:
            player_search = st.selectbox(
                "Player",
                options=player_options,
                index=None,
                placeholder="Search or choose player...",
                key="manual_drop_player_select",
            )
            if player_search:
                try:
                    selected_drop_player_id = drop_candidate_ids[player_search]
                    drop_resolution = transaction_service.resolve_manual_drop(selected_drop_player_id)
                except (ValueError, TypeError) as exc:
                    drop_error = str(exc)
            st.text_input(
                "Current team",
                value=drop_resolution.team_name if drop_resolution else "",
                disabled=True,
            )

        with c2:
            st.text_input(
                "Current contract",
                value=(f"${drop_resolution.salary_basis:.2f} — "
                       f"{drop_resolution.years_remaining} year(s) remaining") if drop_resolution else "",
                disabled=True,
            )
            st.text_input(
                "Drop penalty",
                value=f"{drop_resolution.dead_cap_percentage:.2f}%" if drop_resolution else "",
                disabled=True,
            )
            st.text_input(
                "Dead cap",
                value=f"${drop_resolution.dead_cap:.2f}" if drop_resolution else "",
                disabled=True,
            )

        if drop_error:
            st.error(drop_error)

        notes = st.text_area("Reason / notes")

        if st.button("Log Manual Drop", use_container_width=True, disabled=drop_resolution is None):
            transaction_service.release_manual_drop(player_id=drop_resolution.player_id, notes=notes)
            invalidate_team_state_session_cache(st.session_state)
            st.success("Manual drop completed.")
    elif tool == "Trade Tools":
        st.markdown("### Trade Tools")
        st.caption("Build one atomic 2–4 team trade. All assets validate before anything moves.")

        team_option_rows = team_selection_options(league_teams)
        team_labels = dict(team_option_rows)
        team_options = [team_id for team_id, _label in team_option_rows]

        contracts = (
            service_client().table("contract_agreements")
            .select("*")
            .eq("league_id", active_league_id)
            .execute()
            .data
            or []
        )
        player_ids = [str(c.get("sleeper_player_id")) for c in contracts if c.get("sleeper_player_id")]
        player_rows = service_client().table("player_universe").select("sleeper_id,player_name").in_("sleeper_id", player_ids).execute().data or [] if player_ids else []
        player_names = {str(row.get("sleeper_id")): row.get("player_name") for row in player_rows}
        contract_by_label = {
            f"{player_names.get(str(c.get('sleeper_player_id'))) or c.get('sleeper_player_id')} · {str(c.get('id'))[:8]}": c
            for c in contracts
            if c.get("id") and c.get("league_team_id") and c.get("status") in {"active", "scheduled"}
        }
        pick_rows = (
            service_client().table("draft_pick_assets").select("*")
            .eq("league_id", active_league_id).in_("draft_year", list(draft_season_window(canonical_season)))
            .execute().data or []
        )
        lifecycle_rows = service_client().table("league_draft_lifecycles").select("season,status").eq("league_id", active_league_id).execute().data or []
        pick_by_label = {
            f"{p.get('draft_year')} Round {p.get('round_number')} · {str(p.get('stable_pick_id'))[:8]}": p
            for p in pick_rows
            if is_draft_pick_tradeable(
                next((row.get("status") for row in lifecycle_rows if int(row.get("season") or 0) == int(p.get("draft_year") or 0)), ""),
                str(p.get("asset_status") or ""),
            )
        }

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
                        format_func=lambda team_id: team_labels[team_id],
                        key=f"trade_team_{i}",
                    )
                )

        if len(set(selected_teams)) != len(selected_teams):
            st.warning("Each trade slot must use a different team.")
            st.stop()

        st.divider()

        player_movements = []
        pick_movements = []
        retained_salary = []
        receive_cols = st.columns(team_count_trade)

        for i, receiving_team in enumerate(selected_teams):
            receiving_team_id = receiving_team
            other_team_ids = {team_id for team_id in selected_teams if team_id != receiving_team}

            eligible_player_options = [label for label, c in contract_by_label.items() if str(c.get("league_team_id")) in other_team_ids]
            pick_options = [label for label, p in pick_by_label.items() if str(p.get("current_owner_league_team_id")) in other_team_ids]

            with receive_cols[i]:
                st.markdown(f"#### {team_labels[receiving_team]} receives")

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

                for label in players_received:
                    contract = contract_by_label[label]
                    movement = PlayerMovement(str(contract["id"]), str(contract["league_team_id"]), receiving_team_id)
                    player_movements.append(movement)
                    seasons = []
                    st.caption(f"Retained salary: {label}")
                    for season in range(canonical_season, min(int(contract.get("end_season") or canonical_season), canonical_season + 3) + 1):
                        amount = st.number_input(f"{season} retained", min_value=0.0, step=1.0, key=f"trade_retained_{i}_{contract['id']}_{season}")
                        if amount > 0:
                            seasons.append(RetainedSeason(season, Decimal(str(amount))))
                    if seasons:
                        retained_salary.append(RetainedSalary(str(contract["id"]), str(contract["league_team_id"]), receiving_team_id, tuple(seasons)))
                for label in picks_received:
                    pick = pick_by_label[label]
                    pick_movements.append(DraftPickMovement(str(pick["stable_pick_id"]), str(pick["current_owner_league_team_id"]), receiving_team_id))

        st.divider()

        notes = st.text_area(
            "Trade notes",
            placeholder="Optional commissioner notes...",
        )

        st.json({
            "participants": [team_labels[team_id] for team_id in selected_teams],
            "players": [vars(row) for row in player_movements],
            "draft_picks": [vars(row) for row in pick_movements],
            "retained_salary": [
                {**vars(row), "seasons": [{"season": value.season, "amount": str(value.amount)} for value in row.seasons]}
                for row in retained_salary
            ],
        })

        if st.button("Process Trade", use_container_width=True):
            result = execute_canonical_trade(
                sb_client,
                league_id=active_league_id,
                participant_team_ids=selected_teams,
                idempotency_key=f"settings-trade:{uuid4()}",
                player_movements=player_movements,
                draft_pick_movements=pick_movements,
                retained_salary=retained_salary,
                notes=notes,
            )
            invalidate_team_state_session_cache(st.session_state)
            st.cache_data.clear()
            st.success(f"Atomic trade completed: {result['trade_id']}")

# ============================================================
# DRAFT CENTER
# ============================================================
elif section == "Draft":
    if not is_commissioner:
        show_commissioner_gate()
        st.stop()

    def draft_lifecycle_controls(draft_year: int) -> None:
        st.caption(
            "Open the draft before it starts, and lock it when the last pick is in. "
            "Locking turns the remaining picks into used picks."
        )
        status_col, save_col, lock_col = st.columns([1.2, 1, 1])
        with status_col:
            lifecycle_action = st.selectbox(
                "Draft status",
                ["scheduled", "in_progress"],
                key="rookie_draft_lifecycle_status",
                format_func=lambda value: value.replace("_", " ").capitalize(),
            )
        with save_col:
            st.write("")
            if st.button("Save status", use_container_width=True):
                configured = configure_draft_lifecycle(
                    sb_client,
                    league_id=active_league_id,
                    season=draft_year,
                    status=lifecycle_action,
                    expected_pick_count=len(league_teams) * 3,
                )
                st.success(f"{draft_year} draft is {configured['status'].replace('_', ' ')}.")
        with lock_col:
            st.write("")
            if st.button("Complete and lock draft", use_container_width=True):
                completed_draft = complete_rookie_draft(
                    sb_client,
                    league_id=active_league_id,
                    season=draft_year,
                )
                st.success(
                    f"{draft_year} draft completed; "
                    f"{completed_draft['locked_asset_count']} assets locked."
                )

    render_draft(
        sb_client=sb_client,
        league_id=active_league_id,
        league_teams=league_teams,
        league_rules=league_rules,
        active_season=canonical_season,
        draft_years=draft_season_window(canonical_season),
        lifecycle_controls=draft_lifecycle_controls,
    )

elif section == "FA Auction":
    if not is_commissioner:
        show_commissioner_gate()
        st.stop()

    render_fa_auction(
        sb_client=sb_client,
        league_id=active_league_id,
        league_teams=league_teams,
        league_rules=league_rules,
        active_season=canonical_season,
    )


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
        with st.expander("Deployment diagnostics", expanded=False):
            st.caption(
                "What code this process is actually running. If a fix looks like it "
                "did not deploy, check here before re-pushing."
            )
            try:
                import services.offseason_transactions as _svc

                diag_pid = os.getpid()
                diag_module = getattr(_svc, "__file__", "unknown")
                diag_commit = "unknown"
                try:
                    head = Path(ROOT_DIR_STR) / ".git" / "HEAD"
                    if head.exists():
                        ref = head.read_text().strip()
                        if ref.startswith("ref: "):
                            ref_path = Path(ROOT_DIR_STR) / ".git" / ref[5:]
                            if ref_path.exists():
                                diag_commit = ref_path.read_text().strip()[:12]
                        else:
                            diag_commit = ref[:12]
                except Exception:
                    pass

                # Symbols added at known points in history: a missing one means this
                # process is running an older copy of the module than the checkout.
                markers = {
                    "assign_injured_reserve": "76b19e6",
                    "list_manual_drop_candidates": "bbc14ae",
                }
                st.code(
                    f"pid           {diag_pid}\n"
                    f"commit        {diag_commit}\n"
                    f"module        {diag_module}",
                    language="text",
                )
                for symbol, since in markers.items():
                    present = hasattr(_svc.OffseasonTransactionService, symbol)
                    st.markdown(
                        f"{'✅' if present else '❌'} `{symbol}` "
                        f"— expected since `{since}`"
                    )
                if not all(
                    hasattr(_svc.OffseasonTransactionService, s) for s in markers
                ):
                    st.error(
                        "This process is running an older copy of "
                        "services/offseason_transactions.py than the deployed "
                        "checkout. Reboot the app and wait for the logs to restart "
                        "from the beginning — a browser refresh or Rerun is not a "
                        "reboot."
                    )
            except Exception as exc:
                st.warning(f"Diagnostics unavailable: {exc}")
        st.subheader("Environment")
        st.json(
            {
                "active_league_id": active_league_id,
                "role": role,
                "is_commissioner": is_commissioner,
                "is_platform_admin": is_platform_admin,
            }
        )
