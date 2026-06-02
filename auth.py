# auth.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st
from supabase import create_client


# ============================================================
# Safe .env loader
# Does NOT require python-dotenv
# ============================================================
def _load_env_file(path: Path) -> bool:
    if not path.exists():
        return False

    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and os.getenv(key) is None:
                os.environ[key] = value

        return True

    except Exception:
        return False


def load_local_env() -> None:
    here = Path(__file__).resolve()
    root = here.parent
    cwd = Path.cwd()

    possible_paths = [
        root / ".env",
        root / "fantasy_env",
        cwd / ".env",
        cwd / "fantasy_env",
        cwd / "pages" / ".env",
        cwd / "pages" / "fantasy_env",
    ]

    for path in possible_paths:
        if _load_env_file(path):
            break


load_local_env()


# ============================================================
# Supabase clients
# ============================================================
@st.cache_resource
def _sb_anon():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_ANON_KEY", os.getenv("SUPABASE_KEY", "")).strip()

    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY/SUPABASE_KEY in environment.")

    return create_client(url, key)


def _sb(access_token: str | None = None):
    """
    Returns a Supabase client.

    - Without an access token, returns the cached anon client.
    - With an access token, returns a fresh client and applies the user's JWT
      to PostgREST so RLS behaves as the logged-in user.
    """
    if not access_token:
        return _sb_anon()

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_ANON_KEY", os.getenv("SUPABASE_KEY", "")).strip()

    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY/SUPABASE_KEY in environment.")

    client = create_client(url, key)

    try:
        client.postgrest.auth(access_token)
    except Exception:
        pass

    return client


@st.cache_resource
def _sb_service():
    """
    Service-role Supabase client.
    Use only for backend/admin jobs that intentionally bypass RLS.
    """
    url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url or not service_key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment.")

    return create_client(url, service_key)


# ============================================================
# Session keys
# ============================================================
USER_KEY = "user"
ACCESS_KEY = "sb_access_token"
REFRESH_KEY = "sb_refresh_token"

ACTIVE_LEAGUE_KEY = "active_league_id"
ROLE_KEY = "role"


def _clear_auth_state():
    for key in [USER_KEY, ACCESS_KEY, REFRESH_KEY, ACTIVE_LEAGUE_KEY, ROLE_KEY]:
        st.session_state.pop(key, None)


def _store_session(session: Any):
    if not session or not getattr(session, "user", None):
        _clear_auth_state()
        return

    st.session_state[USER_KEY] = {
        "id": session.user.id,
        "email": session.user.email,
    }

    st.session_state[ACCESS_KEY] = getattr(session, "access_token", None)
    st.session_state[REFRESH_KEY] = getattr(session, "refresh_token", None)


def restore_session():
    """
    Rehydrate st.session_state['user'] from existing Supabase tokens.
    Safe to call at the top of every page.
    """
    if st.session_state.get(USER_KEY):
        return

    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)

    if not access:
        return

    client = _sb_anon()

    try:
        if hasattr(client.auth, "set_session"):
            client.auth.set_session(access, refresh)
    except Exception:
        _clear_auth_state()
        return

    try:
        result = client.auth.get_user()

        if result and getattr(result, "user", None):
            st.session_state[USER_KEY] = {
                "id": result.user.id,
                "email": result.user.email,
            }
            return

    except Exception:
        _clear_auth_state()


def is_logged_in() -> bool:
    restore_session()
    return bool(st.session_state.get(USER_KEY))


def current_user() -> dict | None:
    restore_session()
    return st.session_state.get(USER_KEY)


def require_login(redirect_to: str = "home.py"):
    if not is_logged_in():
        st.switch_page(redirect_to)


# ============================================================
# Auth actions
# ============================================================
def sign_in(email: str, password: str):
    email = (email or "").strip()

    if not email or not password:
        raise ValueError("Email and password required.")

    client = _sb_anon()
    result = client.auth.sign_in_with_password(
        {
            "email": email,
            "password": password,
        }
    )

    session = getattr(result, "session", None)

    if session:
        _store_session(session)
    else:
        user = getattr(result, "user", None)
        if user:
            st.session_state[USER_KEY] = {
                "id": user.id,
                "email": user.email,
            }

    return getattr(result, "user", None)


def sign_up(email: str, password: str):
    email = (email or "").strip()

    if not email or not password:
        raise ValueError("Email and password required.")

    client = _sb_anon()
    result = client.auth.sign_up(
        {
            "email": email,
            "password": password,
        }
    )

    session = getattr(result, "session", None)

    if session:
        _store_session(session)

    return getattr(result, "user", None)


def sign_out():
    try:
        _sb_anon().auth.sign_out()
    except Exception:
        pass

    _clear_auth_state()


# ============================================================
# Helpers
# ============================================================
def auth_client():
    access = st.session_state.get(ACCESS_KEY)
    return _sb(access)


def service_client():
    return _sb_service()


def is_app_admin() -> bool:
    user = current_user()

    if not user:
        return False

    admins = os.getenv("ADMIN_EMAILS", "")
    admin_set = {email.strip().lower() for email in admins.split(",") if email.strip()}

    return user.get("email", "").lower() in admin_set