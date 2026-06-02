# Intro/LogIn/00_league_Setup.py
from __future__ import annotations

import time
from pathlib import Path

import requests
import streamlit as st

from auth import require_login, current_user, _sb, sign_out


ROOT_DIR = Path.cwd()
ICON = ROOT_DIR / "assets" / "page_icon.png"

APP_PAGE = "pages/03_Teams.py"

SETUP_FILES = {
    "league_setup": ROOT_DIR / "pages" / "00_league_Setup.py",
    "import_contracts": ROOT_DIR / "pages" / "02_Import_Contracts.py",
    "fix_players": ROOT_DIR / "pages" / "03_Fix_Players.py",
    "commit_contracts": ROOT_DIR / "pages" / "04_Commit_Contracts.py",
    "invite_members": ROOT_DIR / "pages" / "05_Invite_Members.py",
}

st.set_page_config(
    page_title="Legacy Dynasty — League Setup",
    page_icon=str(ICON) if ICON.exists() else "🏈",
    layout="wide",
)


# ============================================================
# Internal setup router
# ============================================================
setup_step = st.session_state.get("setup_step", "league_setup")

if setup_step != "league_setup":
    target = SETUP_FILES.get(setup_step)

    if target and target.exists():
        exec(target.read_text(), globals())
        st.stop()

    st.error(f"Setup step file not found: {target}")
    st.stop()


# ============================================================
# Styling
# ============================================================
st.markdown(
    """
    <style>
        section[data-testid="stSidebar"] { display: none; }
        [data-testid="collapsedControl"] { display: none; }

        html, body, [data-testid="stAppViewContainer"] {
            background: #071A12 !important;
            color: #F5EBD7 !important;
        }

        [data-testid="stHeader"] {
            background: transparent !important;
        }

        .block-container {
            padding-top: 2.25rem;
            padding-bottom: 3rem;
            max-width: 720px;
        }

        h1 {
            font-size: 2.4rem !important;
            margin-bottom: .35rem !important;
            color: #F5EBD7 !important;
        }

        h2, h3, p, label {
            color: #F5EBD7 !important;
        }

        .muted {
            color: #CFC6B4;
            font-size: .9rem;
        }

        .setup-card {
            background: rgba(13, 36, 26, 0.58);
            border: 1px solid rgba(200, 155, 74, 0.22);
            border-radius: 20px;
            padding: 24px;
            margin-top: 1.4rem;
        }

        .status-pill {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 999px;
            background: rgba(200, 155, 74, 0.18);
            color: #E2BC5B;
            font-size: .72rem;
            font-weight: 800;
            margin-bottom: .85rem;
        }

        .brand-lockup {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 1.4rem;
        }

        .brand-lockup img {
            width: 54px;
            height: 54px;
            object-fit: contain;
            border-radius: 14px;
            background: #F5EBD7;
            padding: 4px;
        }

        .stTextInput input {
            background-color: rgba(17, 43, 32, 0.88) !important;
            border-radius: 12px !important;
            border: 1px solid rgba(200, 155, 74, 0.24) !important;
            color: #F5EBD7 !important;
            min-height: 42px !important;
        }

        .stButton button,
        .stFormSubmitButton button {
            background: linear-gradient(135deg, #C89B4A, #B88735) !important;
            color: #071A12 !important;
            border: none !important;
            border-radius: 12px !important;
            font-weight: 800 !important;
            min-height: 44px !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: .5rem;
            border-bottom: 1px solid rgba(200,155,74,.2);
        }

        .stTabs [data-baseweb="tab"] {
            color: #CFC6B4;
            font-weight: 700;
            padding: .5rem .25rem;
        }

        .stTabs [aria-selected="true"] {
            color: #E2BC5B !important;
            border-bottom: 2px solid #C89B4A;
        }

        div[data-testid="stForm"] {
            border: 1px solid rgba(200,155,74,.2);
            border-radius: 16px;
            padding: 18px;
            background: rgba(255,255,255,.015);
        }

        hr {
            border-color: rgba(200,155,74,.16) !important;
        }

        .verify-shell {
            margin-top: 2rem;
            background: rgba(13, 36, 26, 0.58);
            border: 1px solid rgba(200, 155, 74, 0.22);
            border-radius: 20px;
            padding: 42px 28px;
            text-align: center;
            min-height: 260px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }

        .verify-spinner {
            width: 46px;
            height: 46px;
            border: 4px solid rgba(245,235,215,0.16);
            border-top: 4px solid #C89B4A;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 18px;
        }

        .verify-check {
            width: 52px;
            height: 52px;
            border-radius: 50%;
            border: 2px solid rgba(200,155,74,0.7);
            color: #E2BC5B;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.7rem;
            font-weight: 900;
            margin-bottom: 18px;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# Auth / Supabase
# ============================================================
require_login("home.py")

user = current_user()
user_email = user["email"]

access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")

if not access:
    st.error("No access token found. Please sign in again.")
    if st.button("Back to sign in"):
        sign_out()
        st.session_state.pop("app_mode", None)
        st.rerun()
    st.stop()

sb = _sb(access)

try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass


# ============================================================
# Helpers
# ============================================================
def db_uid() -> str:
    try:
        result = sb.rpc("whoami", {}).execute().data
        if isinstance(result, dict) and result.get("uid"):
            return result["uid"]
    except Exception:
        pass

    return user["id"]


def validate_sleeper_league(sleeper_league_id: str) -> dict | None:
    sleeper_league_id = (sleeper_league_id or "").strip()

    if not sleeper_league_id:
        return None

    try:
        r = requests.get(
            f"https://api.sleeper.app/v1/league/{sleeper_league_id}",
            timeout=12,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def get_user_leagues(uid: str) -> tuple[list[dict], dict]:
    try:
        memberships = (
            sb.table("league_memberships")
            .select("league_id, role, created_at")
            .eq("user_id", uid)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:
        memberships = []

    if not memberships:
        return [], {}

    role_by_league = {m["league_id"]: m.get("role", "member") for m in memberships}
    ids = list(role_by_league.keys())

    try:
        leagues = (
            sb.table("leagues")
            .select("id, name, sleeper_league_id, created_at, created_by")
            .in_("id", ids)
            .execute()
            .data
            or []
        )
    except Exception:
        leagues = []

    return leagues, role_by_league


def get_pending_invites(email: str) -> list[dict]:
    """
    Optional future table.
    If league_invites does not exist yet, this safely returns [].
    """
    try:
        return (
            sb.table("league_invites")
            .select("*")
            .eq("email", email.lower())
            .eq("status", "pending")
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def create_league(name: str, sleeper_id: str | None, uid: str) -> dict:
    payload = {
        "name": name.strip(),
        "created_by": uid,
    }

    if sleeper_id and sleeper_id.strip():
        payload["sleeper_league_id"] = sleeper_id.strip()

    league = (
        sb.table("leagues")
        .insert(payload)
        .execute()
        .data
        or []
    )[0]

    sb.table("league_memberships").insert(
        {
            "league_id": league["id"],
            "user_id": uid,
            "role": "commissioner",
        }
    ).execute()

    st.session_state["active_league_id"] = league["id"]
    st.session_state["role"] = "commissioner"

    return league


def update_sleeper_id(league_id: str, sleeper_id: str):
    sb.table("leagues").update(
        {"sleeper_league_id": sleeper_id.strip()}
    ).eq("id", league_id).execute()


def go_to_app():
    st.session_state["app_mode"] = "app"
    st.switch_page(APP_PAGE)


def go_to_next_setup_step():
    target = SETUP_FILES["import_contracts"]

    if not target.exists():
        st.error(f"Import contracts page not found: {target}")
        st.stop()

    st.session_state["setup_step"] = "import_contracts"
    exec(target.read_text(), globals())
    st.stop()


# ============================================================
# Header
# ============================================================
st.markdown(
    """
    <h1>League Setup</h1>
    <div class="muted">Build your league portal, connect Sleeper, and prepare contracts.</div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# Existing non-commissioner user with league
# ============================================================
uid = db_uid()
leagues, role_by_league = get_user_leagues(uid)
pending_invites = get_pending_invites(user_email)

active_league_id = st.session_state.get("active_league_id")

if not active_league_id and leagues:
    latest = sorted(leagues, key=lambda x: x.get("created_at", ""), reverse=True)[0]
    active_league_id = latest["id"]
    st.session_state["active_league_id"] = active_league_id
    st.session_state["role"] = role_by_league.get(active_league_id, "member")

active_league = None

if active_league_id:
    for league in leagues:
        if league["id"] == active_league_id:
            active_league = league
            break

if active_league and st.session_state.get("role") not in ["commissioner", "host", "admin"]:
    with st.spinner("Loading your league..."):
        time.sleep(0.7)
    go_to_app()


# ============================================================
# No league yet
# ============================================================
if not active_league:
    st.markdown('<div class="setup-card">', unsafe_allow_html=True)

    st.subheader("Finish getting started")
    st.caption(f"Signed in as {user_email}")

    st.write("Choose one:")

    tab_create, tab_join = st.tabs(["Create a league", "Join an existing league"])

    with tab_create:
        st.markdown("### Create your league as commissioner")
        st.caption("Use this if you are setting up the league for everyone else.")

        with st.form("create_league_form"):
            name = st.text_input("League name", placeholder="League Name")
            sleeper_id = st.text_input("Sleeper League ID", placeholder="Sleeper League ID")
            submitted = st.form_submit_button("Create League", use_container_width=True)

        if submitted:
            if not name.strip():
                st.error("League name is required.")
                st.stop()

            try:
                if sleeper_id.strip():
                    with st.spinner("Validating Sleeper league..."):
                        info = validate_sleeper_league(sleeper_id)

                    if not info:
                        st.error("Sleeper could not find that league ID.")
                        st.stop()

                with st.spinner("Creating your league portal..."):
                    league = create_league(name, sleeper_id or None, uid)

                st.success(f"Created {league['name']}.")
                time.sleep(0.6)
                st.rerun()

            except Exception as e:
                st.error(f"Could not create league: {e}")

    with tab_join:
        st.markdown("### Join as owner or co-owner")
        st.caption("If you are not the commissioner, your commissioner needs to invite or assign your account to a team.")

        if pending_invites:
            st.success("Pending invite found.")
            st.dataframe(pending_invites, use_container_width=True, hide_index=True)
            st.info("Invite acceptance can be wired next once the invite table is finalized.")
        else:
            st.info("No league invite found for this email yet.")

        st.write("Ask your commissioner to invite this email:")
        st.code(user_email)

        if st.button("Refresh invite status", use_container_width=True):
            st.rerun()

    st.divider()

    if st.button("Sign out", use_container_width=True):
        sign_out()
        st.session_state.pop("app_mode", None)
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


# ============================================================
# Commissioner: missing Sleeper connection
# ============================================================
if not active_league.get("sleeper_league_id"):
    st.markdown('<div class="setup-card">', unsafe_allow_html=True)
    st.markdown('<span class="status-pill">Sleeper Required</span>', unsafe_allow_html=True)

    st.subheader(active_league["name"])
    st.caption("Add your Sleeper league ID so we can import players, rosters, matchups, and standings.")

    sleeper_id = st.text_input("Sleeper league ID", placeholder="Paste Sleeper league ID")

    if st.button("Connect Sleeper League", use_container_width=True, disabled=not sleeper_id.strip()):
        try:
            with st.spinner("Checking Sleeper league..."):
                info = validate_sleeper_league(sleeper_id)

            if not info:
                st.error("Sleeper could not find that league ID. Check the number and try again.")
                st.stop()

            with st.spinner("Saving Sleeper connection..."):
                update_sleeper_id(active_league["id"], sleeper_id)

            st.success(f"Connected to {info.get('name', 'Sleeper league')}.")
            time.sleep(0.7)
            st.rerun()

        except Exception as e:
            st.error(f"Could not connect Sleeper league: {e}")

    if st.button("Sign out", use_container_width=True):
        sign_out()
        st.session_state.pop("app_mode", None)
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


# ============================================================
# League + Sleeper exist: validate and continue
# ============================================================
slot = st.empty()

slot.markdown(
    f"""
    <div class="verify-shell">
        <div class="verify-spinner"></div>
        <h2>Verifying league</h2>
        <p class="muted">Checking Sleeper access for {active_league["name"]}...</p>
    </div>
    """,
    unsafe_allow_html=True,
)

time.sleep(0.8)
info = validate_sleeper_league(active_league["sleeper_league_id"])

if not info:
    slot.empty()

    st.markdown('<div class="setup-card">', unsafe_allow_html=True)
    st.subheader("Sleeper connection needs attention")
    st.caption("We could not verify this Sleeper league ID. Update it below and try again.")

    sleeper_id = st.text_input(
        "Sleeper league ID",
        value=active_league.get("sleeper_league_id") or "",
    )

    if st.button("Update Sleeper Connection", use_container_width=True):
        try:
            with st.spinner("Checking Sleeper league..."):
                info = validate_sleeper_league(sleeper_id)

            if not info:
                st.error("Sleeper could not find that league ID.")
                st.stop()

            update_sleeper_id(active_league["id"], sleeper_id)
            st.success("Sleeper connection updated.")
            time.sleep(0.7)
            st.rerun()

        except Exception as e:
            st.error(f"Could not update Sleeper connection: {e}")

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

slot.markdown(
    f"""
    <div class="verify-shell">
        <div class="verify-check">✓</div>
        <h2>League verified</h2>
        <p class="muted">Connected to {info.get("name", "Sleeper league")}.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

time.sleep(0.7)

slot.markdown(
    """
    <div class="verify-shell">
        <div class="verify-spinner"></div>
        <h2>Preparing contract import</h2>
        <p class="muted">Building your workspace and sending you to the next setup step...</p>
    </div>
    """,
    unsafe_allow_html=True,
)

time.sleep(0.7)
slot.empty()
go_to_next_setup_step()