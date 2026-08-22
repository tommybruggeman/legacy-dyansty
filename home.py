from __future__ import annotations

from pathlib import Path
import streamlit as st
from auth import is_logged_in, sign_in, sign_out, sign_up, sign_up_with_result, reset_password, current_user, _sb
from services.invitations import (
    AUTH_MODE_SESSION_KEY,
    INVITE_PREVIEW_SESSION_KEY,
    INVITE_ACCEPTED_SESSION_KEY,
    INVITE_TOKEN_SESSION_KEY,
    accept_invitation,
    capture_invite_token,
    clear_invite_onboarding_state,
    clear_invite_query_params,
    map_signup_exception,
    normalize_email,
    preview_invitation,
    should_attempt_invite_acceptance,
    signup_result_state,
    validate_signup_inputs,
)


APP_PAGE = "pages/02_My_Team.py"
SETUP_ENTRY_FILE = Path(__file__).resolve().parent / "pages" / "00_league_Setup.py"
INVITE_ACCEPTANCE_IN_PROGRESS_KEY = "invite_acceptance_in_progress"
INVITE_ACCEPTANCE_LAST_FAILURE_KEY = "invite_acceptance_last_failure"
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

      .st-key-login_forgot_password,
      .st-key-invite_login_forgot_password {
        margin-top: 1.65rem;
      }

      .st-key-login_forgot_password button,
      .st-key-invite_login_forgot_password button {
        min-height: 46px !important;
        width: 100% !important;
        background: rgba(255,255,255,.03) !important;
        border: 1px solid var(--brand-border) !important;
        border-radius: 999px !important;
        color: var(--brand-cream) !important;
        box-shadow: none !important;
        padding: .85rem 1rem !important;
      }

      .st-key-login_forgot_password button:hover,
      .st-key-invite_login_forgot_password button:hover {
        transform: none !important;
        background: var(--brand-gold-soft) !important;
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


invite_token = capture_invite_token(st.query_params, st.session_state)
invite_preview = None


def current_auth_mode() -> str:
    mode = st.session_state.get(AUTH_MODE_SESSION_KEY)
    return mode if mode in {"login", "create"} else "login"


def set_auth_mode(mode: str) -> None:
    st.session_state[AUTH_MODE_SESSION_KEY] = mode
    st.rerun()


def get_invite_preview(token: str | None) -> dict | None:
    if not token:
        return None

    cached = st.session_state.get(INVITE_PREVIEW_SESSION_KEY)
    if cached and cached.get("_token") == token:
        return cached.get("preview")

    preview = preview_invitation(token)
    st.session_state[INVITE_PREVIEW_SESSION_KEY] = {
        "_token": token,
        "preview": preview,
    }
    return preview


def render_invite_notice(preview: dict | None) -> None:
    if not invite_token:
        return

    if not preview:
        st.info("You've been invited. Sign in or create an account to join this league.")
        return

    if preview.get("status") == "pending_active":
        league_name = preview.get("league_name") or "Invited league"
        team_name = preview.get("team_name") or "Assigned team"
        invited_role = str(preview.get("invited_role") or "owner").replace("_", "-")
        invited_email = preview.get("email") or "invited email"
        st.info(
            f"You've been invited to {league_name} as {invited_role} for {team_name}. "
            f"Use {invited_email} to sign in or create an account."
        )
        return

    st.warning(preview.get("message") or "This invitation is no longer active.")


def clear_cached_context() -> None:
    for key in ["app_context", "my_team_context"]:
        st.session_state.pop(key, None)


def render_forgot_password(email: str, key: str) -> None:
    if st.button("Forgot password?", key=key, use_container_width=True):
        result = reset_password(normalize_email(email))
        if result.get("ok"):
            st.info(result.get("message"))
        else:
            st.warning(result.get("message"))


def render_password_with_forgot(email: str, password_key: str, forgot_key: str) -> str:
    password_col, forgot_col = st.columns(2, gap="small")
    with password_col:
        password = st.text_input("Password", key=password_key, type="password", placeholder="••••••••")
    with forgot_col:
        render_forgot_password(email, forgot_key)
    return password


def route_after_invite_acceptance() -> None:
    try:
        go_to_app()
    except Exception:
        st.error("Your invitation was accepted, but the app could not open your team. Please sign in again.")
        st.stop()


def invite_debug(label: str, value) -> None:
    print(f"INVITE_DEBUG {label}={value}", flush=True)


def invite_debug_stage(stage: str) -> None:
    print(f"INVITE_DEBUG stage={stage}", flush=True)


def apply_accepted_invite_result(result: dict) -> None:
    invite_debug_stage("before routing state update")
    league_id = result.get("league_id")
    league_team_id = result.get("league_team_id")

    if league_id:
        st.session_state["active_league_id"] = league_id

    if result.get("role"):
        st.session_state["role"] = result.get("role")
    elif not st.session_state.get("role"):
        st.session_state["role"] = "member"

    if league_team_id:
        st.session_state["league_team_id"] = league_team_id
        st.session_state["active_team_id"] = league_team_id

    st.session_state[INVITE_ACCEPTED_SESSION_KEY] = True
    clear_cached_context()
    clear_invite_query_params(st.query_params)
    clear_invite_onboarding_state(st.session_state)
    invite_debug_stage("after routing state update")


def process_pending_invitation_after_auth() -> str:
    token = st.session_state.get(INVITE_TOKEN_SESSION_KEY)
    invite_debug("invite_token_present", str(bool(token)).lower())

    if not token or st.session_state.get(INVITE_ACCEPTED_SESSION_KEY):
        invite_debug("acceptance_rpc_called", "false")
        invite_debug("acceptance_result_ok", "false")
        invite_debug("acceptance_result_code", "no_invite")
        invite_debug("rpc_response_data_type", "none")
        invite_debug("rpc_response_row_count", 0)
        return "no_invite"

    preview = get_invite_preview(token)
    user = current_user() or {}
    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")
    invite_debug_stage("before current_user verification")
    invite_debug("current_user_resolved", str(bool(user)).lower())
    invite_debug("access_token_present", str(bool(access)).lower())
    invite_debug("refresh_token_present", str(bool(refresh)).lower())
    invite_debug_stage("after current_user verification")

    if not user or not access:
        invite_debug("acceptance_rpc_called", "false")
        invite_debug("acceptance_result_ok", "false")
        invite_debug("acceptance_result_code", "authentication_required")
        invite_debug("rpc_response_data_type", "none")
        invite_debug("rpc_response_row_count", 0)
        st.info("Sign in before accepting this invitation.")
        return "blocked"

    if preview and preview.get("status") == "accepted":
        restore_user_league()

        if st.session_state.get("active_league_id"):
            clear_invite_query_params(st.query_params)
            clear_invite_onboarding_state(st.session_state)
            return "accepted"

        st.error("This invitation was already accepted, but no league membership was found. Ask the commissioner for help.")
        clear_invite_query_params(st.query_params)
        clear_invite_onboarding_state(st.session_state)
        return "blocked"

    signed_in_email = normalize_email(user.get("email"))
    invited_email = normalize_email((preview or {}).get("email"))

    if preview and preview.get("status") in {"expired", "revoked", "invalid"}:
        render_invite_notice(preview)
        clear_invite_query_params(st.query_params)
        clear_invite_onboarding_state(st.session_state)
        return "blocked"

    if invited_email and signed_in_email and signed_in_email != invited_email:
        st.error(
            f"This invite is for {invited_email}. You are signed in as {signed_in_email}. "
            "Sign out, then sign in using the invited email."
        )

        if st.button("Sign out and keep invite", use_container_width=True):
            sign_out()
            st.session_state[INVITE_TOKEN_SESSION_KEY] = token
            st.session_state[AUTH_MODE_SESSION_KEY] = "login"
            st.rerun()

        st.stop()

    last_failure = st.session_state.get(INVITE_ACCEPTANCE_LAST_FAILURE_KEY)
    if last_failure and last_failure.get("token") == token:
        invite_debug("acceptance_rpc_called", "false")
        invite_debug("acceptance_result_ok", "false")
        invite_debug("acceptance_result_code", last_failure.get("code") or "database_error")
        invite_debug("rpc_response_data_type", "cached_failure")
        invite_debug("rpc_response_row_count", 0)
        st.error(last_failure.get("message") or "Could not accept this invitation.")
        if st.button("Try accepting invite again", key="retry_invite_acceptance", use_container_width=True):
            st.session_state.pop(INVITE_ACCEPTANCE_LAST_FAILURE_KEY, None)
            st.rerun()
        return "blocked"

    if st.session_state.get(INVITE_ACCEPTANCE_IN_PROGRESS_KEY) == token:
        st.info("Finishing your invitation. One moment.")
        return "blocked"

    st.session_state[INVITE_ACCEPTANCE_IN_PROGRESS_KEY] = token
    invite_debug_stage("before authenticated client creation")
    authenticated_client = _sb(access)
    invite_debug_stage("after authenticated client creation")
    invite_debug_stage("before RPC invocation")
    invite_debug("acceptance_rpc_called", "true")
    # Diagnostic equivalent of: accept_invitation(token, sb=_sb(access))
    result = accept_invitation(token, sb=authenticated_client)
    invite_debug_stage("after RPC response parsing")
    invite_debug("acceptance_result_ok", str(bool(result.get("ok"))).lower())
    invite_debug("acceptance_result_code", result.get("code") or "missing")
    st.session_state.pop(INVITE_ACCEPTANCE_IN_PROGRESS_KEY, None)

    if result.get("ok"):
        st.session_state.pop(INVITE_ACCEPTANCE_LAST_FAILURE_KEY, None)
        apply_accepted_invite_result(result)
        return "accepted"

    st.error(result.get("message") or "Could not accept this invitation.")

    if result.get("code") in {
        "invitation_not_found",
        "invitation_expired",
        "invitation_revoked",
        "invalid",
    }:
        clear_invite_query_params(st.query_params)
        clear_invite_onboarding_state(st.session_state)
    else:
        st.session_state[INVITE_ACCEPTANCE_LAST_FAILURE_KEY] = {
            "token": token,
            "code": result.get("code") or "database_error",
            "message": result.get("message") or "Could not accept this invitation.",
        }

    return "blocked"


def accept_pending_invite_if_ready() -> bool:
    return process_pending_invitation_after_auth() == "accepted"

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
    if invite_token:
        invite_decision = process_pending_invitation_after_auth()

        if invite_decision == "accepted":
            route_after_invite_acceptance()

        if invite_decision == "blocked":
            st.stop()

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
invite_preview = get_invite_preview(invite_token)
render_invite_notice(invite_preview)

invite_is_active = bool(invite_token and (not invite_preview or invite_preview.get("status") == "pending_active"))
mode = current_auth_mode()

if invite_token and invite_preview and invite_preview.get("status") != "pending_active":
    clear_invite_query_params(st.query_params)
    clear_invite_onboarding_state(st.session_state)

    if st.button("Exit invitation flow", use_container_width=True):
        st.rerun()
    st.stop()

if invite_is_active:
    auth_tabs = st.radio(
        "Authentication mode",
        ["Log In", "Create Account"],
        horizontal=True,
        label_visibility="collapsed",
        index=0 if mode == "login" else 1,
        key="invite_auth_mode_radio",
    )
    mode = "create" if auth_tabs == "Create Account" else "login"
    st.session_state[AUTH_MODE_SESSION_KEY] = mode

    invited_email = normalize_email((invite_preview or {}).get("email"))

    if mode == "login":
        email = st.text_input(
            "Email",
            value=invited_email,
            key="invite_login_email",
            placeholder="you@email.com",
        )
        password = render_password_with_forgot(email, "invite_login_pw", "invite_login_forgot_password")

        if st.button("Sign in", disabled=not (email and password), use_container_width=True):
            try:
                invite_debug_stage("before login success")
                sign_in(email, password)
                invite_debug_stage("after login success")
                invite_decision = process_pending_invitation_after_auth()

                if invite_decision == "accepted":
                    route_after_invite_acceptance()

                if invite_decision == "blocked":
                    st.stop()

                restore_user_league()
                go_to_app() if st.session_state.get("active_league_id") else go_to_setup()

            except Exception:
                st.error("Login failed. Check your email and password, then try again.")

    else:
        email = st.text_input(
            "Email",
            value=invited_email,
            key="invite_signup_email",
            placeholder="you@email.com",
            disabled=bool(invited_email),
        )
        password = st.text_input("Password", key="invite_signup_pw", type="password", placeholder="••••••••")
        password_confirm = st.text_input(
            "Confirm password",
            key="invite_signup_pw2",
            type="password",
            placeholder="••••••••",
        )

        if password and password_confirm and password != password_confirm:
            st.warning("Passwords do not match.")

        signup_validation = validate_signup_inputs(
            email=email,
            password=password,
            password_confirm=password_confirm,
        ) if email or password or password_confirm else {"ok": False}

        if email and password and password_confirm and not signup_validation.get("ok"):
            st.warning(signup_validation.get("message"))

        can_create = bool(email and password and password_confirm)

        if st.button("Create account", disabled=not can_create, use_container_width=True):
            signup_validation = validate_signup_inputs(
                email=email,
                password=password,
                password_confirm=password_confirm,
            )

            if not signup_validation.get("ok"):
                st.error(signup_validation.get("message"))
                st.stop()

            try:
                signup_result = sign_up_with_result(signup_validation["email"], password)
                signup_state = signup_result_state(signup_result)

                if signup_state == "confirmation_required":
                    st.success(
                        "Your account was created. Check your email to confirm your account, "
                        "then return to this invitation and sign in."
                    )
                    st.session_state[AUTH_MODE_SESSION_KEY] = "login"
                    st.stop()

                if not should_attempt_invite_acceptance(signup_result, current_user()):
                    st.info("Account created. Sign in to continue accepting this invitation.")
                    st.session_state[AUTH_MODE_SESSION_KEY] = "login"
                    st.stop()

                invite_decision = process_pending_invitation_after_auth()

                if invite_decision == "accepted":
                    route_after_invite_acceptance()

                if invite_decision == "blocked":
                    st.stop()

            except Exception as exc:
                mapped_error = map_signup_exception(exc)
                st.error(mapped_error["message"])
                if mapped_error.get("code") == "account_exists":
                    if st.button("Switch to Log In", key="invite_signup_existing_login", use_container_width=True):
                        set_auth_mode("login")

else:
    tab_login, tab_host = st.tabs(["Log in", "Create League"])

    with tab_login:
        if mode == "create":
            email = st.text_input("Email", key="li_create_email", placeholder="you@email.com")
            password = st.text_input("Password", key="li_create_pw", type="password", placeholder="••••••••")
            password_confirm = st.text_input(
                "Confirm password",
                key="li_create_pw2",
                type="password",
                placeholder="••••••••",
            )

            if password and password_confirm and password != password_confirm:
                st.warning("Passwords do not match.")

            signup_validation = validate_signup_inputs(
                email=email,
                password=password,
                password_confirm=password_confirm,
            ) if email or password or password_confirm else {"ok": False}

            if email and password and password_confirm and not signup_validation.get("ok"):
                st.warning(signup_validation.get("message"))

            can_create = bool(email and password and password_confirm)

            if st.button("Create account", disabled=not can_create, use_container_width=True):
                signup_validation = validate_signup_inputs(
                    email=email,
                    password=password,
                    password_confirm=password_confirm,
                )

                if not signup_validation.get("ok"):
                    st.error(signup_validation.get("message"))
                    st.stop()

                try:
                    signup_result = sign_up_with_result(signup_validation["email"], password)
                    if signup_result_state(signup_result) == "confirmation_required":
                        st.success("Account created. Check your email to confirm your account, then return to sign in.")
                        st.stop()

                    if not is_logged_in():
                        sign_in(signup_validation["email"], password)

                    go_to_setup()
                except Exception as exc:
                    st.error(map_signup_exception(exc)["message"])

            if st.button("Already have an account? Log in", use_container_width=True):
                set_auth_mode("login")
        else:
            email = st.text_input("Email", key="li_email", placeholder="you@email.com")
            password = render_password_with_forgot(email, "li_pw", "login_forgot_password")

            if st.button("Sign in", disabled=not (email and password), use_container_width=True):
                try:
                    sign_in(email, password)
                    restore_user_league()

                    if st.session_state.get("active_league_id"):
                        go_to_app()
                    else:
                        go_to_setup()

                except Exception as exc:
                    st.error("Post-login flow failed during Gate 3 disposable acceptance.")
                    st.exception(exc)

            if st.button("New to Legacy Dynasty? Create an account", use_container_width=True):
                set_auth_mode("create")

    with tab_host:
        st.markdown("### Create your league host account")
        st.caption("Run league setup, connect Sleeper, upload contracts, and invite members.")

        host_email = st.text_input("Host email", key="ho_email", placeholder="commissioner@email.com")
        host_pw = st.text_input("Password", key="ho_pw", type="password", placeholder="••••••••")
        host_pw2 = st.text_input("Confirm password", key="ho_pw2", type="password", placeholder="••••••••")

        if host_pw and host_pw2 and host_pw != host_pw2:
            st.warning("Passwords do not match.")

        host_validation = validate_signup_inputs(
            email=host_email,
            password=host_pw,
            password_confirm=host_pw2,
        ) if host_email or host_pw or host_pw2 else {"ok": False}

        if host_email and host_pw and host_pw2 and not host_validation.get("ok"):
            st.warning(host_validation.get("message"))

        can_create = bool(host_email and host_pw and host_pw2)

        if st.button("Create host account", disabled=not can_create, use_container_width=True):
            host_validation = validate_signup_inputs(
                email=host_email,
                password=host_pw,
                password_confirm=host_pw2,
            )

            if not host_validation.get("ok"):
                st.error(host_validation.get("message"))
                st.stop()

            try:
                sign_up(host_validation["email"], host_pw)
                sign_in(host_validation["email"], host_pw)
                go_to_setup()

            except Exception as exc:
                st.error(map_signup_exception(exc)["message"])
