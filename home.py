from __future__ import annotations

from pathlib import Path
import streamlit as st
from auth import is_logged_in, sign_in, sign_out, current_user, _sb


APP_PAGE = "pages/02_My_Team.py"
SETUP_ENTRY_FILE = Path(__file__).resolve().parent / "pages" / "00_league_Setup.py"
st.set_page_config(
    page_title="Legacy Dynasty — Sign in",
    page_icon="assets/page_icon.png",
    layout="centered",
)


# ============================================================
# Internal router for setup flow
# ============================================================
def go_to_setup():
    st.session_state["app_mode"] = "setup"
    st.rerun()


def go_to_app():
    st.session_state["app_mode"] = "app"
    st.switch_page(APP_PAGE)


def clear_mode():
    st.session_state.pop("app_mode", None)


if st.session_state.get("app_mode") == "setup":
    if not SETUP_ENTRY_FILE.exists():
        st.error(f"Setup file not found: {SETUP_ENTRY_FILE}")
        if st.button("Back to sign in"):
            clear_mode()
            st.rerun()
        st.stop()

    exec(SETUP_ENTRY_FILE.read_text(), globals())
    st.stop()


# ============================================================
# Styling
# ============================================================
st.markdown(
    """
    <style>
      [data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="collapsedControl"] {
        display: none;
      }

      .block-container {
        padding-top: 3rem;
        padding-left: 2rem;
        padding-right: 2rem;
        max-width: 920px;
      }

      @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Oswald:wght@500;600;700&display=swap');

      :root {
        --brand-bg: #071A12;
        --brand-gold-soft: rgba(200,155,74,.18);
        --brand-cream: #F5EBD7;
        --brand-muted: #C8BEAD;
        --brand-border: rgba(200,155,74,.28);
      }

      html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Poppins', sans-serif !important;
        background: var(--brand-bg) !important;
        color: var(--brand-cream) !important;
      }

      [data-testid="stHeader"] {
        background: transparent !important;
      }

      h1, h2, h3 {
        font-family: 'Oswald', sans-serif !important;
      }

      .brand-shell {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 1.15rem;
      }

      .brand-title {
        font-family: 'Oswald', sans-serif;
        font-size: 4.25rem;
        font-weight: 700;
        line-height: 1;
        color: var(--brand-cream);
        text-align: center;
      }

      .brand-subtitle {
        font-size: 1.4rem;
        font-weight: 600;
        color: var(--brand-muted);
        text-align: center;
      }

      .stTabs [data-baseweb="tab-list"] {
        gap: .45rem;
        border-bottom: 1px solid rgba(200,155,74,.22);
      }

      .stTabs [data-baseweb="tab"] {
        border-radius: 999px;
        padding: .65rem 1rem;
        color: var(--brand-muted);
      }

      .stTabs [aria-selected="true"] {
        background: var(--brand-gold-soft);
        border: 1px solid var(--brand-border);
        color: var(--brand-cream) !important;
      }

      [data-testid="stTabs"] [data-baseweb="tab-panel"] {
        padding-top: 1rem !important;
      }

      .stTextInput input {
        background: rgba(255,255,255,.03) !important;
        border: 1px solid var(--brand-border) !important;
        border-radius: 999px !important;
        color: var(--brand-cream) !important;
        padding: .85rem 1rem !important;
        box-shadow: none !important;
      }

      .stButton > button {
        border-radius: 999px !important;
        border: 1px solid rgba(200,155,74,.28) !important;
        background: linear-gradient(135deg, #8C6A2E, #A8823A) !important;
        color: #F5EBD7 !important;
        font-weight: 700 !important;
        padding: .85rem 1.1rem !important;
        box-shadow: 0 8px 20px rgba(0,0,0,.16);
      }

      .stButton > button:hover {
        transform: translateY(-1px);
        background: linear-gradient(135deg, #A8823A, #BC9550) !important;
      }

      .stAlert {
        border-radius: 18px !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# Assets
# ============================================================
def asset_path(*names: str) -> str | None:
    root = Path(__file__).resolve().parent

    for name in names:
        for p in [
            root / "assets" / name,
            root / name,
            root.parent / "assets" / name,
            root.parent / name,
        ]:
            if p.exists():
                return str(p)

    return None


crest = asset_path("page_icon.png", "FantasyCrest.png", "fantasy_crest.png", "crest.png")


# ============================================================
# Header
# ============================================================
st.markdown('<div class="brand-shell">', unsafe_allow_html=True)

if crest:
    st.image(crest, width=165)

st.markdown(
    """
    <div class="brand-title">Legacy Dynasty</div>
    <div class="brand-subtitle">Built for the Long Game</div>
    """,
    unsafe_allow_html=True,
)

st.markdown("</div>", unsafe_allow_html=True)
st.write("")

def restore_user_league():
    user = current_user()
    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if not user or not access:
        return

    sb = _sb(access)

    try:
        sb.auth.set_session(access, refresh)
    except Exception:
        pass

    memberships = (
        sb.table("league_memberships")
        .select("league_id, role, created_at")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

    if not memberships:
        return

    membership = memberships[0]
    st.session_state["active_league_id"] = membership["league_id"]
    st.session_state["role"] = membership.get("role", "member")

# ============================================================
# Logged-in state
# ============================================================
if is_logged_in():
    restore_user_league()

    active_league_id = st.session_state.get("active_league_id")

    if active_league_id:
        go_to_app()
    else:
        go_to_setup()

    st.stop()

# ============================================================
# Logged-out state
# ============================================================
tab_login, tab_host = st.tabs(["Log in", "Create League"])


with tab_login:
    email = st.text_input("Email", key="li_email", placeholder="you@email.com")
    password = st.text_input("Password", key="li_pw", type="password", placeholder="••••••••")

    if st.button("Sign in", disabled=not (email and password), use_container_width=True):
        try:
            sign_in(email, password)
            restore_user_league()

            if st.session_state.get("active_league_id"):
                go_to_app()
            else:
                go_to_setup()

        except Exception as e:
            st.error(f"Login failed: {e}")

with tab_host:
    st.markdown("### Create your league host account")
    st.caption("Run league setup, connect Sleeper, upload contracts, and invite members.")

    host_email = st.text_input("Host email", key="ho_email", placeholder="commissioner@email.com")
    host_pw = st.text_input("Password", key="ho_pw", type="password", placeholder="••••••••")
    host_pw2 = st.text_input("Confirm password", key="ho_pw2", type="password", placeholder="••••••••")

    if host_pw and host_pw2 and host_pw != host_pw2:
        st.warning("Passwords do not match.")

    can_create = bool(host_email and host_pw and host_pw2 and host_pw == host_pw2)

    if st.button("Create host account", disabled=not can_create, use_container_width=True):
        from auth import sign_up

        try:
            sign_up(host_email, host_pw)
            sign_in(host_email, host_pw)
            go_to_setup()

        except Exception as e:
            st.error(f"Could not create/sign in: {e}")