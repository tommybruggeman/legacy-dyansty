# auth.py
from __future__ import annotations

import base64
import json
import os
import time
from dotenv import load_dotenv
load_dotenv(".env")
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
    refresh_token = st.session_state.get(REFRESH_KEY)
    effective_access_token = access_token

    try:
        if refresh_token and hasattr(client.auth, "set_session"):
            response = client.auth.set_session(access_token, refresh_token)
            session = getattr(response, "session", None)
            refreshed_access_token = getattr(session, "access_token", None)
            refreshed_refresh_token = getattr(session, "refresh_token", None)
            if refreshed_access_token:
                effective_access_token = refreshed_access_token
                st.session_state[ACCESS_KEY] = refreshed_access_token
            if refreshed_refresh_token:
                st.session_state[REFRESH_KEY] = refreshed_refresh_token
    except Exception:
        pass

    # Apply the effective user JWT after session restoration. This is explicit
    # because page-level clients must never fall back to the anon key for RLS
    # protected reads such as public.league_seasons.
    client.postgrest.auth(effective_access_token)

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

# Streamlit session_state lives only as long as the browser tab's websocket,
# so a page refresh starts a brand new, signed-out session. The Supabase
# tokens are mirrored into a browser cookie so the next session can pick them
# back up on whatever page was refreshed.
#
# The cookie is read and written in the browser by a tiny component rather
# than through st.context.cookies, because Streamlit Community Cloud's proxy
# does not forward app cookies to the server.
AUTH_COOKIE = "ld_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
BRIDGE_KEY = "_ld_auth_bridge"
BROWSER_COOKIE_KEY = "_ld_auth_browser_cookie"
COOKIE_RESTORE_TRIED_KEY = "_ld_auth_cookie_restore_tried"
SIGNED_OUT_KEY = "_ld_signed_out"

# Refresh a little before the access token expires so the rotation happens at
# the top of a page run, where the new refresh token is written straight back
# to the cookie, rather than mid-page inside _sb().
TOKEN_REFRESH_MARGIN_SECONDS = 300

# Writes data.write (a full cookie string) if given, then reports the cookie's
# current value back to Python whenever it differs from what Python last saw.
_BRIDGE_JS = """
export default function ({ data, setStateValue }) {
  const name = "%s=";
  if (data && data.write) {
    document.cookie = data.write;
  }
  const found = document.cookie.split("; ").find((c) => c.startsWith(name));
  const value = found ? found.slice(name.length) : "";
  if (!data || value !== data.reported) {
    setStateValue("cookie", value);
  }
}
""" % AUTH_COOKIE

_auth_bridge = None


def _clear_auth_state():
    for key in [USER_KEY, ACCESS_KEY, REFRESH_KEY, ACTIVE_LEAGUE_KEY, ROLE_KEY]:
        st.session_state.pop(key, None)


# ============================================================
# Auth cookie
# ============================================================
def _encode_cookie(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cookie(value: str | None) -> dict | None:
    if not value:
        return None

    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None

    if not isinstance(payload, dict) or not payload.get("r"):
        return None

    return payload


def _read_auth_cookie() -> dict | None:
    return _decode_cookie(st.session_state.get(BROWSER_COOKIE_KEY))


def _cookie_payload() -> dict | None:
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)

    if not st.session_state.get(USER_KEY) or not refresh:
        return None

    return {
        "a": access,
        "r": refresh,
        "l": st.session_state.get(ACTIVE_LEAGUE_KEY),
    }


def _cookie_string(value: str) -> str:
    max_age = AUTH_COOKIE_MAX_AGE if value else 0
    secure = ""
    try:
        if str(st.context.url or "").startswith("https://"):
            secure = "; Secure"
    except Exception:
        pass

    return f"{AUTH_COOKIE}={value}; Path=/; Max-Age={max_age}; SameSite=Lax{secure}"


def _on_bridge_report() -> None:
    state = st.session_state.get(BRIDGE_KEY) or {}
    value = state.get("cookie") if hasattr(state, "get") else getattr(state, "cookie", None)
    if value is not None:
        st.session_state[BROWSER_COOKIE_KEY] = value


def _render_bridge(write: str | None) -> None:
    global _auth_bridge
    if _auth_bridge is None:
        _auth_bridge = st.components.v2.component("ld_auth_bridge", js=_BRIDGE_JS)

    _auth_bridge(
        key=BRIDGE_KEY,
        data={"reported": st.session_state.get(BROWSER_COOKIE_KEY), "write": write},
        on_cookie_change=_on_bridge_report,
        height=0,
    )


def sync_auth_cookie() -> bool:
    """
    Restore the login from the browser cookie and keep the cookie in step
    with the current tokens. Call once per run, at the top level of a page.

    Returns False until the browser has reported its cookie for this session;
    the report triggers a rerun, so callers should st.stop() meanwhile.
    """
    reported = st.session_state.get(BROWSER_COOKIE_KEY)

    if reported is None:
        _render_bridge(None)
        return False

    if st.session_state.get(USER_KEY):
        _refresh_if_expiring()
    else:
        restore_session()

    payload = _cookie_payload()
    desired = _encode_cookie(payload) if payload else ""
    # The browser confirms every write by reporting the new value, so a write
    # lost to a switch_page() is simply retried on the next page.
    _render_bridge(_cookie_string(desired) if desired != reported else None)
    return True


def _access_token_expires_soon(access_token: str | None) -> bool:
    if not access_token:
        return True

    try:
        segment = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        return int(claims.get("exp", 0)) - time.time() <= TOKEN_REFRESH_MARGIN_SECONDS
    except Exception:
        return True


def _restore_active_league(client) -> None:
    """
    A refreshed tab has lost active_league_id/role too. Prefer the league the
    cookie remembered, as long as the user is still a member of it.
    """
    if st.session_state.get(ACTIVE_LEAGUE_KEY):
        return

    user = st.session_state.get(USER_KEY) or {}
    if not user.get("id"):
        return

    try:
        rows = (
            client.table("league_memberships")
            .select("league_id, role, created_at")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:
        return

    if not rows:
        return

    remembered = (_read_auth_cookie() or {}).get("l")
    membership = next((row for row in rows if row.get("league_id") == remembered), rows[0])

    st.session_state[ACTIVE_LEAGUE_KEY] = membership["league_id"]
    st.session_state[ROLE_KEY] = membership.get("role", "member")


def _restore_from_cookie() -> None:
    if st.session_state.get(SIGNED_OUT_KEY) or st.session_state.get(COOKIE_RESTORE_TRIED_KEY):
        return

    if st.session_state.get(BROWSER_COOKIE_KEY) is None:
        return

    st.session_state[COOKIE_RESTORE_TRIED_KEY] = True

    payload = _read_auth_cookie()
    if not payload:
        return

    client = _sb_anon()

    try:
        if _access_token_expires_soon(payload.get("a")):
            response = client.auth.refresh_session(payload["r"])
        else:
            response = client.auth.set_session(payload["a"], payload["r"])
    except Exception:
        response = None

    session = getattr(response, "session", None)
    if not session or not getattr(session, "user", None):
        # Expired or revoked: sync_auth_cookie() deletes it.
        return

    _store_session(session)
    client.postgrest.auth(session.access_token)
    _restore_active_league(client)


def _refresh_if_expiring() -> None:
    access = st.session_state.get(ACCESS_KEY)
    refresh = st.session_state.get(REFRESH_KEY)

    if not refresh or not _access_token_expires_soon(access):
        return

    try:
        response = _sb_anon().auth.refresh_session(refresh)
    except Exception:
        return

    session = getattr(response, "session", None)
    if session and getattr(session, "access_token", None):
        st.session_state[ACCESS_KEY] = session.access_token
        st.session_state[REFRESH_KEY] = session.refresh_token


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
        _restore_from_cookie()
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
    if not sync_auth_cookie():
        st.stop()

    if not is_logged_in():
        st.switch_page(redirect_to)


# ============================================================
# Auth actions
# ============================================================
def sign_in(email: str, password: str):
    email = (email or "").strip()

    if not email or not password:
        raise ValueError("Email and password required.")

    import hashlib
    expected_password = os.getenv("GATE3_COMMISSIONER_PASSWORD", "")
    supplied_fp = hashlib.sha256(password.encode()).hexdigest()[:12]
    expected_fp = hashlib.sha256(expected_password.encode()).hexdigest()[:12] if expected_password else "MISSING"

    print(
        f"[GATE3 AUTH DEBUG] url={os.getenv('SUPABASE_URL')} "
        f"email={email} supplied_password_fp={supplied_fp} "
        f"expected_password_fp={expected_fp} "
        f"password_match={bool(expected_password and password == expected_password)}",
        flush=True,
    )

    client = _sb_anon()
    result = client.auth.sign_in_with_password(
        {
            "email": email,
            "password": password,
        }
    )

    session = getattr(result, "session", None)
    st.session_state.pop(SIGNED_OUT_KEY, None)

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


def sign_up_with_result(email: str, password: str) -> dict[str, Any]:
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

    user = getattr(result, "user", None)
    session = getattr(result, "session", None)

    if session:
        _store_session(session)

    return {
        "ok": bool(user),
        "user": user,
        "session": session,
        "has_user": bool(user),
        "has_session": bool(session),
    }


def reset_password(email: str, redirect_to: str | None = None) -> dict[str, Any]:
    email = (email or "").strip()

    if not email:
        return {
            "ok": False,
            "code": "invalid_email",
            "message": "Enter your email address before requesting a password reset.",
        }

    client = _sb_anon()
    options = {"redirect_to": redirect_to} if redirect_to else None

    try:
        if options:
            client.auth.reset_password_for_email(email, options=options)
        else:
            client.auth.reset_password_for_email(email)
    except Exception:
        return {
            "ok": False,
            "code": "reset_unavailable",
            "message": "Password recovery is not available yet. Ask the commissioner for help signing in.",
        }

    return {
        "ok": True,
        "code": "reset_requested",
        "message": "If password recovery email is configured, a reset link will be sent to that address.",
    }


def sign_out():
    try:
        _sb_anon().auth.sign_out()
    except Exception:
        pass

    _clear_auth_state()
    # home.py's sync_auth_cookie() deletes the cookie on the next run; this
    # flag stops it being restored again in the meantime.
    st.session_state[SIGNED_OUT_KEY] = True


# ============================================================
# Helpers
# ============================================================
def auth_client():
    restore_session()
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
