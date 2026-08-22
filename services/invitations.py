from __future__ import annotations

import hashlib
import html
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from auth import auth_client, current_user, service_client


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INVITE_ROLES = {"owner", "co_owner"}
DEFAULT_EXPIRY_DAYS = 14
EMAIL_STATUS_NOT_CONFIGURED = "not_configured"
INVITATION_EMAIL_PREHEADER = "Claim your team and enter the league."
INVITATION_EMAIL_SUBJECT_PREFIX = "You’ve been invited to join"
MIN_SIGNUP_PASSWORD_LENGTH = int(os.getenv("AUTH_PASSWORD_MIN_LENGTH", "6") or "6")
INVITE_TOKEN_QUERY_PARAM = "invite_token"
INVITE_TOKEN_SESSION_KEY = "pending_invite_token"
INVITE_PREVIEW_SESSION_KEY = "pending_invite_preview"
INVITE_ACCEPTED_SESSION_KEY = "pending_invite_accepted"
AUTH_MODE_SESSION_KEY = "auth_mode"
INVITE_LINK_SESSION_NAMESPACE = "owner_invite_links"
INVITE_LINK_ALLOWED_FIELDS = {
    "invitation_id",
    "league_id",
    "league_team_id",
    "invited_role",
    "email",
    "expires_at",
    "code",
    "invite_url",
}

LEAGUE_INVITE_EMAIL_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="x-apple-disable-message-reformatting">
  <meta name="format-detection" content="telephone=no,address=no,email=no,date=no,url=no">
  <title>You’ve been invited to Legacy Dynasty</title>
</head>
<body style="margin:0;padding:0;background:#03140D;font-family:Arial,Helvetica,sans-serif;color:#F5EBD7;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;line-height:1px;font-size:1px;">Claim your team and enter the league.</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#03140D;">
    <tr><td align="center" style="padding:32px 16px;">
      <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;">
        <tr><td align="center" style="padding:4px 0 24px;">
          {{ logo_img }}
          <div style="font-family:Georgia,'Times New Roman',serif;color:#F5EBD7;font-size:22px;line-height:28px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;">Legacy Dynasty</div>
          <div style="color:#C89B4A;font-size:11px;line-height:16px;font-weight:700;letter-spacing:2.2px;text-transform:uppercase;margin-top:4px;">Fantasy Football. Built to Last.</div>
        </td></tr>
        <tr><td style="background:#081F15;border:1px solid rgba(200,155,74,.30);border-radius:20px;overflow:hidden;box-shadow:0 18px 42px rgba(0,0,0,.30);">
          <div style="height:5px;line-height:5px;font-size:0;background:#C89B4A;">&nbsp;</div>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
            <tr><td style="padding:42px 44px 18px;">
              <div style="color:#C89B4A;font-size:12px;line-height:18px;font-weight:700;letter-spacing:1.7px;text-transform:uppercase;margin-bottom:14px;">League Invitation</div>
              <h1 style="margin:0;font-family:Georgia,'Times New Roman',serif;color:#F5EBD7;font-size:36px;line-height:42px;font-weight:700;letter-spacing:-.5px;">Your team is waiting.</h1>
              <p style="margin:18px 0 0;color:#CFC6B4;font-size:17px;line-height:27px;">You’ve been invited to join <strong style="color:#F5EBD7;">{{ league_name }}</strong> and take control of <strong style="color:#F5EBD7;">{{ team_name }}</strong>.</p>
            </td></tr>
            <tr><td style="padding:10px 44px 8px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0A271B;border:1px solid rgba(200,155,74,.22);border-radius:14px;">
                <tr><td style="padding:20px 22px;border-bottom:1px solid rgba(200,155,74,.16);"><div style="color:#CFC6B4;font-size:11px;line-height:16px;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;">League</div><div style="color:#F5EBD7;font-size:16px;line-height:23px;font-weight:700;margin-top:4px;word-break:break-word;">{{ league_name }}</div></td></tr>
                <tr><td style="padding:20px 22px;border-bottom:1px solid rgba(200,155,74,.16);"><div style="color:#CFC6B4;font-size:11px;line-height:16px;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;">Your Team</div><div style="color:#F5EBD7;font-size:18px;line-height:25px;font-weight:700;margin-top:4px;word-break:break-word;">{{ team_name }}</div></td></tr>
                <tr><td style="padding:20px 22px;">
                  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>
                    <td style="width:50%;padding-right:10px;vertical-align:top;"><div style="color:#CFC6B4;font-size:11px;line-height:16px;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;">Role</div><div style="color:#F5EBD7;font-size:15px;line-height:22px;font-weight:700;margin-top:4px;word-break:break-word;">{{ role_label }}</div></td>
                    <td style="width:50%;padding-left:10px;vertical-align:top;"><div style="color:#CFC6B4;font-size:11px;line-height:16px;font-weight:700;letter-spacing:1.3px;text-transform:uppercase;">Invited Email</div><div style="color:#F5EBD7;font-size:15px;line-height:22px;font-weight:700;margin-top:4px;word-break:break-word;">{{ invited_email }}</div></td>
                  </tr></table>
                </td></tr>
              </table>
            </td></tr>
            <tr><td align="center" style="padding:28px 44px 18px;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td align="center" bgcolor="#C89B4A" style="border-radius:10px;"><a href="{{ invite_url }}" style="display:inline-block;padding:15px 30px;color:#03140D;font-size:16px;line-height:20px;font-weight:700;text-decoration:none;border-radius:10px;">Accept Invitation</a></td></tr></table>
            </td></tr>
            <tr><td style="padding:0 44px 10px;"><p style="margin:0;text-align:center;color:#CFC6B4;font-size:14px;line-height:22px;">Create an account or log in with the invited email. We’ll connect you automatically and take you directly to your team portal.</p></td></tr>
            <tr><td style="padding:18px 44px 36px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#061A11;border-radius:12px;"><tr><td style="padding:15px 18px;color:#CFC6B4;font-size:13px;line-height:20px;text-align:center;">{{ expiration_sentence }}</td></tr></table></td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:24px 24px 0;text-align:center;"><p style="margin:0 0 8px;color:#CFC6B4;font-size:12px;line-height:18px;">Button not working? Copy and paste this link into your browser:</p><p style="margin:0;font-size:12px;line-height:18px;word-break:break-all;"><a href="{{ invite_url }}" style="color:#C89B4A;text-decoration:underline;">{{ invite_url }}</a></p></td></tr>
        <tr><td style="padding:26px 24px 8px;text-align:center;"><p style="margin:0;color:#8F8A7F;font-size:11px;line-height:17px;">This invitation was sent by {{ commissioner_name }} through Legacy Dynasty.</p><p style="margin:8px 0 0;color:#6F6A62;font-size:10px;line-height:16px;">Please do not forward this email. The invitation link is tied to the invited email address.</p></td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


class InvitationError(ValueError):
    """Raised when a low-level invitation helper is used incorrectly."""


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def validate_email(email: str | None) -> bool:
    return bool(EMAIL_RE.match(normalize_email(email)))


def validate_signup_inputs(
    *,
    email: str | None,
    password: str | None,
    password_confirm: str | None,
    min_password_length: int = MIN_SIGNUP_PASSWORD_LENGTH,
) -> dict[str, Any]:
    normalized_email = normalize_email(email)

    if not validate_email(normalized_email):
        return {
            "ok": False,
            "code": "invalid_email",
            "message": "Enter a valid email address.",
            "email": normalized_email,
        }

    if not password:
        return {
            "ok": False,
            "code": "password_required",
            "message": "Password is required.",
            "email": normalized_email,
        }

    if len(password) < min_password_length:
        return {
            "ok": False,
            "code": "password_too_short",
            "message": f"Password must be at least {min_password_length} characters.",
            "email": normalized_email,
        }

    if password != password_confirm:
        return {
            "ok": False,
            "code": "password_mismatch",
            "message": "Passwords do not match.",
            "email": normalized_email,
        }

    return {
        "ok": True,
        "code": "valid",
        "message": "Signup inputs are valid.",
        "email": normalized_email,
    }


def map_signup_exception(exc: Exception) -> dict[str, Any]:
    text = str(exc).lower()

    if "password" in text and ("6" in text or "short" in text or "weak" in text):
        return {
            "ok": False,
            "code": "password_too_short",
            "message": f"Password must be at least {MIN_SIGNUP_PASSWORD_LENGTH} characters.",
        }

    if "email" in text and ("invalid" in text or "malformed" in text):
        return {
            "ok": False,
            "code": "invalid_email",
            "message": "Enter a valid email address.",
        }

    if (
        "already" in text
        or "registered" in text
        or "exists" in text
        or "user_already_exists" in text
    ):
        return {
            "ok": False,
            "code": "account_exists",
            "message": "An account already exists for this email. Switch to Log In to continue.",
        }

    if "rate" in text or "too many" in text or "over_email_send_rate_limit" in text:
        return {
            "ok": False,
            "code": "rate_limited",
            "message": "Signup is temporarily unavailable. Wait a moment, then try again.",
        }

    return {
        "ok": False,
        "code": "signup_failed",
        "message": "Could not create the account. Try again, or switch to Log In if you already have an account.",
    }


def signup_result_state(result: dict[str, Any] | None) -> str:
    result = result or {}

    if not result.get("ok"):
        return "failed"

    if result.get("has_user") and result.get("has_session"):
        return "authenticated"

    if result.get("has_user"):
        return "confirmation_required"

    return "failed"


def should_attempt_invite_acceptance(signup_result: dict[str, Any] | None, user: Any | None) -> bool:
    return signup_result_state(signup_result) == "authenticated" and bool(user)


def sanitized_invite_diagnostics(**values: Any) -> dict[str, Any]:
    blocked = {
        "password",
        "raw_token",
        "invite_token",
        "token_hash",
        "access_token",
        "refresh_token",
        "service_role",
        "service_role_key",
    }

    return {key: value for key, value in values.items() if key not in blocked}


def _sanitize_debug_text(value: Any, *, default: str = "none") -> str:
    if value is None:
        return default

    text = str(value)
    if not text:
        return default

    text = re.sub(r"[A-Za-z0-9_-]{24,256}", "[redacted]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(password|token_hash|access_token|refresh_token|service_role_key|apikey|authorization)\s*[:=]\s*[^,\s}]+", r"\1=[redacted]", text)
    return text[:240]


def _exception_payload(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    for attr in ("code", "message", "details", "hint"):
        value = getattr(exc, attr, None)
        if value is not None:
            payload[attr] = value

    for arg in getattr(exc, "args", ()) or ():
        if isinstance(arg, dict):
            payload.update({key: value for key, value in arg.items() if key in {"code", "message", "details", "hint"}})

    if not payload:
        payload["message"] = str(exc)

    return payload


def sanitized_rpc_exception_fields(exc: Exception) -> dict[str, str]:
    payload = _exception_payload(exc)
    return {
        "class": _sanitize_debug_text(exc.__class__.__name__, default="Exception"),
        "code": _sanitize_debug_text(payload.get("code")),
        "message": _sanitize_debug_text(payload.get("message")),
        "details": _sanitize_debug_text(payload.get("details")),
        "hint": _sanitize_debug_text(payload.get("hint")),
    }


def normalize_invite_token(raw_token: Any) -> str:
    if isinstance(raw_token, (list, tuple)):
        raw_token = raw_token[0] if raw_token else ""

    return str(raw_token or "").strip()


def is_valid_looking_invite_token(raw_token: Any) -> bool:
    token = normalize_invite_token(raw_token)
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{24,256}", token))


def generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


def hash_invite_token(raw_token: str) -> str:
    if not raw_token or not raw_token.strip():
        raise InvitationError("Invitation token is required.")

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def build_invite_url(raw_token: str, base_url: str | None = None) -> str:
    base = (
        base_url
        or os.getenv("INVITE_BASE_URL")
        or os.getenv("APP_BASE_URL")
        or "http://localhost:8501"
    ).rstrip()

    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{INVITE_TOKEN_QUERY_PARAM}={quote(raw_token)}"


def _html_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _html_attr(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _safe_public_url(value: str | None) -> str:
    url = (value or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    return url


def _role_label(role: str | None) -> str:
    normalized = (role or "owner").strip().lower().replace("-", "_")
    return "Co-Owner" if normalized == "co_owner" else "Owner"


def _format_invite_expiration(expires_at: datetime | str | None) -> str | None:
    parsed = parse_invite_datetime(expires_at) if not isinstance(expires_at, datetime) else expires_at
    if not parsed:
        return None

    return parsed.astimezone(timezone.utc).strftime("%B %-d, %Y")


def build_league_invite_email(
    *,
    league_name: str,
    team_name: str,
    role_label: str,
    invited_email: str,
    invite_url: str,
    expires_at: datetime | str | None,
    logo_url: str,
    commissioner_name: str | None = None,
) -> dict[str, str]:
    safe_logo_url = _safe_public_url(logo_url)
    logo_img = ""
    if safe_logo_url:
        logo_img = (
            f'<img src="{_html_attr(safe_logo_url)}" width="76" alt="Legacy Dynasty" '
            'style="display:block;width:76px;height:auto;margin:0 auto 12px;border:0;">'
        )

    expiration = _format_invite_expiration(expires_at)
    expiration_sentence = (
        f'This invitation expires on <strong style="color:#F5EBD7;">{_html_text(expiration)}</strong>.'
        if expiration
        else "This invitation will expire soon."
    )
    commissioner = (commissioner_name or "").strip() or "your commissioner"

    values = {
        "{{ logo_img }}": logo_img,
        "{{ league_name }}": _html_text(league_name or "Invited league"),
        "{{ team_name }}": _html_text(team_name or "Assigned team"),
        "{{ role_label }}": _html_text(role_label or "Owner"),
        "{{ invited_email }}": _html_text(invited_email),
        "{{ invite_url }}": _html_attr(_safe_public_url(invite_url) or invite_url),
        "{{ expiration_sentence }}": expiration_sentence,
        "{{ commissioner_name }}": _html_text(commissioner),
    }

    html_body = LEAGUE_INVITE_EMAIL_TEMPLATE
    for placeholder, replacement in values.items():
        html_body = html_body.replace(placeholder, replacement)

    subject = f"{INVITATION_EMAIL_SUBJECT_PREFIX} {league_name or 'your league'}"
    text_expiration = f"This invitation expires on {expiration}." if expiration else "This invitation will expire soon."
    text_body = "\n".join(
        [
            "Legacy Dynasty",
            INVITATION_EMAIL_PREHEADER,
            "",
            f"You've been invited to join {league_name or 'Invited league'} and take control of {team_name or 'Assigned team'}.",
            f"Role: {role_label or 'Owner'}",
            f"Invited email: {invited_email}",
            text_expiration,
            "",
            "Accept Invitation:",
            invite_url,
            "",
            "Create an account or log in with the invited email. We'll connect you automatically and take you directly to your team portal.",
            f"This invitation was sent by {commissioner} through Legacy Dynasty.",
            "Please do not forward this email. The invitation link is tied to the invited email address.",
        ]
    )

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }


def _lookup_invitation_email_context(
    *,
    sb: Any,
    league_id: str | None,
    league_team_id: str | None,
    invited_by: str | None = None,
) -> dict[str, str | None]:
    context: dict[str, str | None] = {
        "league_name": None,
        "team_name": None,
        "commissioner_name": None,
    }

    if league_id:
        try:
            league_rows = (
                sb.table("leagues")
                .select("name")
                .eq("id", league_id)
                .limit(1)
                .execute()
                .data
                or []
            )
            if league_rows:
                context["league_name"] = league_rows[0].get("name")
        except Exception:
            pass

    if league_id and league_team_id:
        try:
            team_rows = (
                sb.table("league_teams")
                .select("team_name, owner_name")
                .eq("id", league_team_id)
                .eq("league_id", league_id)
                .limit(1)
                .execute()
                .data
                or []
            )
            if team_rows:
                team = team_rows[0]
                context["team_name"] = team.get("team_name") or team.get("owner_name")
        except Exception:
            pass

    user = current_user() or {}
    context["commissioner_name"] = (
        user.get("name")
        or user.get("full_name")
        or user.get("email")
        or invited_by
    )
    return context


def _prepare_invitation_email(
    *,
    sb: Any,
    invitation: dict[str, Any],
    invite_url: str,
    fallback_league_id: str | None = None,
    fallback_league_team_id: str | None = None,
) -> dict[str, str]:
    league_id = invitation.get("league_id") or fallback_league_id
    league_team_id = invitation.get("league_team_id") or fallback_league_team_id
    context = _lookup_invitation_email_context(
        sb=sb,
        league_id=league_id,
        league_team_id=league_team_id,
        invited_by=invitation.get("invited_by"),
    )
    return build_league_invite_email(
        league_name=context.get("league_name") or "Invited league",
        team_name=context.get("team_name") or "Assigned team",
        role_label=_role_label(invitation.get("role")),
        invited_email=normalize_email(invitation.get("email")),
        invite_url=invite_url,
        expires_at=invitation.get("expires_at"),
        logo_url=os.getenv("LEGACY_DYNASTY_LOGO_URL", "").strip(),
        commissioner_name=context.get("commissioner_name"),
    )


def _deliver_invitation_email(*, email: str | None, email_payload: dict[str, str]) -> dict[str, Any]:
    _ = email
    _ = email_payload
    return {
        "sent": False,
        "status": EMAIL_STATUS_NOT_CONFIGURED,
    }


def capture_invite_token(query_params: Any, session_state: dict[str, Any]) -> str | None:
    raw_value = None

    if hasattr(query_params, "get"):
        raw_value = query_params.get(INVITE_TOKEN_QUERY_PARAM)

    token = normalize_invite_token(raw_value)

    if is_valid_looking_invite_token(token):
        session_state[INVITE_TOKEN_SESSION_KEY] = token
        session_state.pop(INVITE_ACCEPTED_SESSION_KEY, None)
        return token

    existing = normalize_invite_token(session_state.get(INVITE_TOKEN_SESSION_KEY))
    return existing if is_valid_looking_invite_token(existing) else None


def clear_invite_onboarding_state(session_state: dict[str, Any]) -> None:
    for key in [
        INVITE_TOKEN_SESSION_KEY,
        INVITE_PREVIEW_SESSION_KEY,
        INVITE_ACCEPTED_SESSION_KEY,
    ]:
        session_state.pop(key, None)


def clear_invite_query_params(query_params: Any) -> None:
    try:
        if hasattr(query_params, "pop"):
            query_params.pop(INVITE_TOKEN_QUERY_PARAM, None)
        elif hasattr(query_params, "clear"):
            query_params.clear()
    except Exception:
        return


def _client(sb: Any | None = None) -> Any:
    return sb or auth_client()


def _preview_client(sb: Any | None = None) -> Any:
    return sb or service_client()


def _current_user_id() -> str | None:
    user = current_user()

    if isinstance(user, dict):
        user_id = user.get("id") or user.get("user_id")
    else:
        user_id = getattr(user, "id", None)

    return str(user_id) if user_id else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def parse_invite_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def classify_invite_status(invite: dict[str, Any], now: datetime | None = None) -> str:
    status = str(invite.get("status") or "").strip().lower()

    if status == "accepted":
        return "accepted"

    if status == "revoked":
        return "revoked"

    if status == "expired":
        return "expired"

    if status != "pending":
        return status or "unknown"

    expires_value = invite.get("expires_at")
    expires_at = parse_invite_datetime(expires_value)

    if expires_value and not expires_at:
        return "expired"

    if expires_at and expires_at < (now or _utc_now()):
        return "expired"

    return "pending_active"


def invite_link_session_key(
    *,
    league_id: str | None,
    league_team_id: str | None,
    invited_role: str | None,
    invitation_id: str | None,
) -> str:
    return "|".join(
        [
            INVITE_LINK_SESSION_NAMESPACE,
            str(league_id or ""),
            str(league_team_id or ""),
            str(invited_role or ""),
            str(invitation_id or ""),
        ]
    )


def safe_invite_link_payload(result: dict[str, Any], invited_role: str | None = None) -> dict[str, Any]:
    payload = {
        "invitation_id": result.get("invitation_id"),
        "league_id": result.get("league_id"),
        "league_team_id": result.get("league_team_id"),
        "invited_role": invited_role or result.get("invited_role") or result.get("role"),
        "email": result.get("email"),
        "expires_at": result.get("expires_at"),
        "code": result.get("code"),
        "invite_url": result.get("invite_url"),
    }

    return {key: value for key, value in payload.items() if key in INVITE_LINK_ALLOWED_FIELDS}


def remember_invite_link(session_state: dict[str, Any], payload: dict[str, Any]) -> str | None:
    if not payload.get("invite_url"):
        return None

    key = invite_link_session_key(
        league_id=payload.get("league_id"),
        league_team_id=payload.get("league_team_id"),
        invited_role=payload.get("invited_role"),
        invitation_id=payload.get("invitation_id"),
    )
    session_state[key] = {
        field: payload.get(field)
        for field in INVITE_LINK_ALLOWED_FIELDS
        if field in payload and field not in {"token_hash", "service_role", "credentials"}
    }
    return key


def clear_invite_links(
    session_state: dict[str, Any],
    *,
    league_id: str | None = None,
    league_team_id: str | None = None,
    invited_role: str | None = None,
    invitation_id: str | None = None,
) -> None:
    prefix = f"{INVITE_LINK_SESSION_NAMESPACE}|"

    for key in list(session_state.keys()):
        if not str(key).startswith(prefix):
            continue

        payload = session_state.get(key) or {}

        if league_id and payload.get("league_id") != league_id:
            continue
        if league_team_id and payload.get("league_team_id") != league_team_id:
            continue
        if invited_role and payload.get("invited_role") != invited_role:
            continue
        if invitation_id and payload.get("invitation_id") != invitation_id:
            continue

        session_state.pop(key, None)


def can_offer_invite_role(
    *,
    connected_members: list[dict[str, Any]] | None,
    active_invites: list[dict[str, Any]] | None,
    invite_role: str,
) -> bool:
    role = str(invite_role or "").strip().lower().replace("-", "_")

    if role == "owner" and connected_members:
        return False

    if role == "co_owner":
        return not any(
            invite.get("role") == "co_owner"
            and classify_invite_status(invite) == "pending_active"
            for invite in (active_invites or [])
        )

    return role == "owner"


def _preview_result(
    *,
    ok: bool,
    code: str,
    message: str,
    email: str | None = None,
    league_name: str | None = None,
    team_name: str | None = None,
    invited_role: str | None = None,
    status: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "code": code,
        "message": message,
        "email": email,
        "league_name": league_name,
        "team_name": team_name,
        "invited_role": invited_role,
        "status": status,
        "expires_at": expires_at,
    }


def preview_invitation(raw_token: str, *, sb: Any | None = None) -> dict[str, Any]:
    token = normalize_invite_token(raw_token)

    if not is_valid_looking_invite_token(token):
        return _preview_result(
            ok=False,
            code="invalid",
            message="This invitation link is invalid.",
            status="invalid",
        )

    try:
        client = _preview_client(sb)
    except Exception:
        return _preview_result(
            ok=False,
            code="preview_unavailable",
            message="Invitation preview is unavailable. Sign in to continue.",
            status="unknown",
        )

    token_hash = hash_invite_token(token)

    try:
        invite_rows = (
            client.table("league_invites")
            .select("id, league_id, league_team_id, email, role, status, expires_at")
            .eq("token_hash", token_hash)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return _preview_result(
            ok=False,
            code="preview_unavailable",
            message="Invitation preview is unavailable. Sign in to continue.",
            status="unknown",
        )

    if not invite_rows:
        return _preview_result(
            ok=False,
            code="not_found",
            message="This invitation link was not found.",
            status="invalid",
        )

    invite = invite_rows[0]
    status = classify_invite_status(invite)
    league_name = "Invited league"
    team_name = "Assigned team"

    try:
        league_rows = (
            client.table("leagues")
            .select("name")
            .eq("id", invite.get("league_id"))
            .limit(1)
            .execute()
            .data
            or []
        )
        if league_rows:
            league_name = league_rows[0].get("name") or league_name
    except Exception:
        pass

    try:
        team_rows = (
            client.table("league_teams")
            .select("team_name, owner_name")
            .eq("id", invite.get("league_team_id"))
            .eq("league_id", invite.get("league_id"))
            .limit(1)
            .execute()
            .data
            or []
        )
        if team_rows:
            team = team_rows[0]
            team_name = team.get("team_name") or team.get("owner_name") or team_name
    except Exception:
        pass

    message = "Sign in or create an account to join this league."
    if status == "expired":
        message = "This invitation has expired. Ask the commissioner for a new link."
    elif status == "revoked":
        message = "This invitation has been revoked."
    elif status == "accepted":
        message = "This invitation has already been accepted."

    return _preview_result(
        ok=status == "pending_active",
        code=status,
        message=message,
        email=invite.get("email"),
        league_name=league_name,
        team_name=team_name,
        invited_role=invite.get("role"),
        status=status,
        expires_at=invite.get("expires_at"),
    )


def _is_active_pending(row: dict[str, Any], now: datetime | None = None) -> bool:
    if row.get("status") != "pending":
        return False

    expires_at = parse_invite_datetime(row.get("expires_at"))

    if row.get("expires_at") and not expires_at:
        return False

    return expires_at is None or expires_at >= (now or _utc_now())


def _ensure_valid_role(invite_role: str) -> str:
    role = (invite_role or "").strip().lower().replace("-", "_")

    if role not in INVITE_ROLES:
        raise InvitationError("Invitation role must be owner or co_owner.")

    return role


def _result(
    *,
    ok: bool,
    code: str,
    message: str,
    invitation: dict[str, Any] | None = None,
    invite_url: str | None = None,
    email: str | None = None,
    league_id: str | None = None,
    league_team_id: str | None = None,
    role: str | None = None,
    expires_at: str | None = None,
    email_sent: bool = False,
    email_status: str = EMAIL_STATUS_NOT_CONFIGURED,
) -> dict[str, Any]:
    invitation = invitation or {}

    return {
        "ok": ok,
        "code": code,
        "message": message,
        "invitation_id": invitation.get("id"),
        "invite_url": invite_url,
        "email": email or invitation.get("email"),
        "league_id": league_id or invitation.get("league_id"),
        "league_team_id": league_team_id or invitation.get("league_team_id"),
        "role": role or invitation.get("role"),
        "expires_at": expires_at or invitation.get("expires_at"),
        "email_sent": email_sent,
        "email_status": email_status,
    }


def _database_error(message: str) -> dict[str, Any]:
    return _result(ok=False, code="database_error", message=message)


def _acceptance_error(exc: Exception) -> dict[str, Any]:
    text = str(exc).lower()
    mappings = [
        ("authentication required", "authentication_required", "Your account was created, but you are not signed in yet. Sign in to finish joining the league."),
        ("authenticated user email not found", "authentication_required", "Your account was created, but you are not signed in yet. Sign in to finish joining the league."),
        ("token is required", "invalid", "This invitation link is invalid."),
        ("not found", "invitation_not_found", "This invitation link was not found."),
        ("not pending", "invitation_not_pending", "This invitation is no longer pending."),
        ("expired", "invitation_expired", "This invitation has expired. Ask the commissioner for a new link."),
        ("revoked", "invitation_revoked", "This invitation has been revoked."),
        ("already accepted", "invitation_already_accepted", "This invitation has already been accepted."),
        ("email does not match", "wrong_email", "This invitation belongs to a different email address."),
        ("missing an assigned team", "invalid_team", "This invitation is missing an assigned team."),
        ("team is invalid", "invalid_team", "This invitation team is invalid."),
    ]

    for needle, code, message in mappings:
        if needle in text:
            return _result(ok=False, code=code, message=message)

    return _database_error("Could not accept invitation.")


def _require_signed_in() -> tuple[str | None, dict[str, Any] | None]:
    user_id = _current_user_id()

    if not user_id:
        return None, _result(
            ok=False,
            code="permission_denied",
            message="A signed-in commissioner is required.",
        )

    return user_id, None


def _require_commissioner(
    *,
    sb: Any,
    league_id: str,
) -> dict[str, Any] | None:
    user_id, error = _require_signed_in()

    if error:
        return error

    try:
        rows = (
            sb.table("league_memberships")
            .select("id, role")
            .eq("league_id", league_id)
            .eq("user_id", user_id)
            .eq("role", "commissioner")
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return _database_error("Could not verify commissioner permission.")

    if not rows:
        return _result(
            ok=False,
            code="permission_denied",
            message="Only commissioners can manage invitations for this league.",
            league_id=league_id,
        )

    return None


def _validate_team(
    *,
    sb: Any,
    league_id: str,
    league_team_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        rows = (
            sb.table("league_teams")
            .select("id, league_id")
            .eq("id", league_team_id)
            .eq("league_id", league_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return None, _database_error("Could not validate invitation team.")

    if not rows:
        return None, _result(
            ok=False,
            code="invalid_team",
            message="Invitation team does not belong to the selected league.",
            league_id=league_id,
            league_team_id=league_team_id,
        )

    return rows[0], None


def _revoke_expired_pending(
    *,
    sb: Any,
    league_id: str,
    league_team_id: str | None = None,
    email: str | None = None,
) -> dict[str, Any] | None:
    query = (
        sb.table("league_invites")
        .update({"status": "revoked", "revoked_at": _iso(_utc_now())})
        .eq("league_id", league_id)
        .eq("status", "pending")
        .lt("expires_at", _iso(_utc_now()))
    )

    if league_team_id:
        query = query.eq("league_team_id", league_team_id)

    if email:
        query = query.eq("email", normalize_email(email))

    try:
        query.execute()
    except Exception:
        return _database_error("Could not revoke expired pending invitations.")

    return None


def _find_active_pending(
    *,
    sb: Any,
    league_id: str,
    league_team_id: str,
    email: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        rows = (
            sb.table("league_invites")
            .select("id, email, league_id, league_team_id, expires_at, status")
            .eq("league_id", league_id)
            .eq("league_team_id", league_team_id)
            .eq("email", normalize_email(email))
            .eq("status", "pending")
            .execute()
            .data
            or []
        )
    except Exception:
        return None, _database_error("Could not check for existing invitations.")

    for row in rows:
        if _is_active_pending(row):
            return row, None

    return None, None


def create_invitation(
    *,
    league_id: str,
    league_team_id: str,
    email: str,
    invite_role: str = "owner",
    expires_in_days: int = DEFAULT_EXPIRY_DAYS,
    sb: Any | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    client = _client(sb)
    user_id, signed_in_error = _require_signed_in()

    if signed_in_error:
        return signed_in_error

    normalized_email = normalize_email(email)

    if not validate_email(normalized_email):
        return _result(
            ok=False,
            code="invalid_email",
            message="Enter a valid email address.",
            email=normalized_email,
            league_id=league_id,
            league_team_id=league_team_id,
        )

    if not league_id or not league_team_id:
        return _result(
            ok=False,
            code="invalid_team",
            message="League and team are required.",
            email=normalized_email,
            league_id=league_id,
            league_team_id=league_team_id,
        )

    permission_error = _require_commissioner(sb=client, league_id=league_id)
    if permission_error:
        return permission_error

    _, team_error = _validate_team(
        sb=client,
        league_id=league_id,
        league_team_id=league_team_id,
    )
    if team_error:
        return team_error

    try:
        role = _ensure_valid_role(invite_role)
    except InvitationError:
        return _result(
            ok=False,
            code="invalid_team",
            message="Invitation role must be owner or co_owner.",
            email=normalized_email,
            league_id=league_id,
            league_team_id=league_team_id,
        )

    revoke_error = _revoke_expired_pending(
        sb=client,
        league_id=league_id,
        league_team_id=league_team_id,
        email=normalized_email,
    )
    if revoke_error:
        return revoke_error

    pending, pending_error = _find_active_pending(
        sb=client,
        league_id=league_id,
        league_team_id=league_team_id,
        email=normalized_email,
    )
    if pending_error:
        return pending_error

    if pending:
        return _result(
            ok=False,
            code="already_pending",
            message="A pending invitation already exists. Resend it to generate a new link, or revoke it first.",
            invitation=pending,
            email=normalized_email,
            league_id=league_id,
            league_team_id=league_team_id,
        )

    raw_token = generate_invite_token()
    expires_at = _utc_now() + timedelta(days=max(1, int(expires_in_days or DEFAULT_EXPIRY_DAYS)))

    payload = {
        "league_id": league_id,
        "league_team_id": league_team_id,
        "email": normalized_email,
        "role": role,
        "token": None,
        "token_hash": hash_invite_token(raw_token),
        "status": "pending",
        "invited_by": user_id,
        "expires_at": _iso(expires_at),
        "last_sent_at": None,
        "send_count": 0,
    }

    try:
        rows = (
            client.table("league_invites")
            .insert(payload)
            .execute()
            .data
            or []
        )
    except Exception:
        return _database_error("Could not create invitation.")

    if not rows:
        return _database_error("Invitation was not created.")

    invitation = rows[0]
    invite_url = build_invite_url(raw_token, base_url)
    email_payload = _prepare_invitation_email(
        sb=client,
        invitation=invitation,
        invite_url=invite_url,
        fallback_league_id=league_id,
        fallback_league_team_id=league_team_id,
    )
    delivery = _deliver_invitation_email(email=normalized_email, email_payload=email_payload)

    return _result(
        ok=True,
        code="created",
        message="Invitation created. Email delivery is not configured; use the copyable link.",
        invitation=invitation,
        invite_url=invite_url,
        email=normalized_email,
        league_id=league_id,
        league_team_id=league_team_id,
        expires_at=payload["expires_at"],
        email_sent=bool(delivery.get("sent")),
        email_status=delivery.get("status") or EMAIL_STATUS_NOT_CONFIGURED,
    )


def resend_invitation(
    invitation_id: str,
    *,
    sb: Any | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    client = _client(sb)

    try:
        rows = (
            client.table("league_invites")
            .select("*")
            .eq("id", invitation_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return _database_error("Could not load invitation.")

    if not rows:
        return _result(ok=False, code="not_found", message="Invitation not found.")

    invitation = rows[0]
    league_id = invitation.get("league_id")
    league_team_id = invitation.get("league_team_id")

    permission_error = _require_commissioner(sb=client, league_id=league_id)
    if permission_error:
        return permission_error

    _, team_error = _validate_team(
        sb=client,
        league_id=league_id,
        league_team_id=league_team_id,
    )
    if team_error:
        return team_error

    if invitation.get("status") == "accepted":
        return _result(
            ok=False,
            code="already_accepted",
            message="Accepted invitations cannot be resent.",
            invitation=invitation,
        )

    if invitation.get("status") == "revoked":
        return _result(
            ok=False,
            code="already_revoked",
            message="Revoked invitations cannot be resent.",
            invitation=invitation,
        )

    if invitation.get("status") != "pending":
        return _result(
            ok=False,
            code="not_found",
            message="Only pending invitations can be resent.",
            invitation=invitation,
        )

    expires_at = _parse_datetime(invitation.get("expires_at"))
    if expires_at and expires_at < _utc_now():
        return _result(
            ok=False,
            code="expired",
            message="Expired invitations cannot be resent. Create a new invitation.",
            invitation=invitation,
        )

    current_hash = invitation.get("token_hash")
    if not current_hash:
        return _result(
            ok=False,
            code="conflict",
            message="This legacy invitation cannot be resent safely. Create a new invitation.",
            invitation=invitation,
        )

    raw_token = generate_invite_token()
    new_hash = hash_invite_token(raw_token)

    try:
        updated = (
            client.table("league_invites")
            .update({"token": None, "token_hash": new_hash})
            .eq("id", invitation_id)
            .eq("status", "pending")
            .eq("token_hash", current_hash)
            .execute()
            .data
            or []
        )
    except Exception:
        return _database_error("Could not resend invitation.")

    if not updated:
        return _result(
            ok=False,
            code="conflict",
            message="Invitation changed while resending. Reload and try again.",
            invitation=invitation,
        )

    updated_invitation = updated[0]
    invite_url = build_invite_url(raw_token, base_url)
    email_payload = _prepare_invitation_email(
        sb=client,
        invitation={**invitation, **updated_invitation},
        invite_url=invite_url,
        fallback_league_id=league_id,
        fallback_league_team_id=league_team_id,
    )
    delivery = _deliver_invitation_email(email=invitation.get("email"), email_payload=email_payload)

    return _result(
        ok=True,
        code="resent",
        message="Invitation link regenerated. Email delivery is not configured; use the copyable link.",
        invitation=updated_invitation,
        invite_url=invite_url,
        email=invitation.get("email"),
        league_id=league_id,
        league_team_id=league_team_id,
        expires_at=invitation.get("expires_at"),
        email_sent=bool(delivery.get("sent")),
        email_status=delivery.get("status") or EMAIL_STATUS_NOT_CONFIGURED,
    )


def revoke_invitation(invitation_id: str, *, sb: Any | None = None) -> dict[str, Any]:
    client = _client(sb)

    try:
        rows = (
            client.table("league_invites")
            .select("*")
            .eq("id", invitation_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        return _database_error("Could not load invitation.")

    if not rows:
        return _result(ok=False, code="not_found", message="Invitation not found.")

    invitation = rows[0]
    permission_error = _require_commissioner(sb=client, league_id=invitation.get("league_id"))
    if permission_error:
        return permission_error

    if invitation.get("status") == "accepted":
        return _result(
            ok=False,
            code="already_accepted",
            message="Accepted invitations cannot be revoked.",
            invitation=invitation,
        )

    if invitation.get("status") == "revoked":
        return _result(
            ok=False,
            code="already_revoked",
            message="Invitation is already revoked.",
            invitation=invitation,
        )

    try:
        updated = (
            client.table("league_invites")
            .update({"status": "revoked", "revoked_at": _iso(_utc_now())})
            .eq("id", invitation_id)
            .eq("status", "pending")
            .execute()
            .data
            or []
        )
    except Exception:
        return _database_error("Could not revoke invitation.")

    if not updated:
        return _result(
            ok=False,
            code="conflict",
            message="Invitation changed while revoking. Reload and try again.",
            invitation=invitation,
        )

    return _result(
        ok=True,
        code="revoked",
        message="Invitation revoked.",
        invitation=updated[0],
    )


def list_team_invitations(
    *,
    league_id: str,
    league_team_id: str | None = None,
    sb: Any | None = None,
) -> dict[str, Any]:
    client = _client(sb)
    permission_error = _require_commissioner(sb=client, league_id=league_id)

    if permission_error:
        return permission_error

    query = (
        client.table("league_invites")
        .select("id, league_id, league_team_id, email, role, status, created_at, expires_at, accepted_at, revoked_at, last_sent_at, send_count")
        .eq("league_id", league_id)
        .order("created_at", desc=True)
    )

    if league_team_id:
        query = query.eq("league_team_id", league_team_id)

    try:
        rows = query.execute().data or []
    except Exception:
        return _database_error("Could not load invitations.")

    result = _result(
        ok=True,
        code="listed",
        message="Invitations loaded.",
        league_id=league_id,
        league_team_id=league_team_id,
    )
    result["invitations"] = rows
    return result


def get_invitation_by_token(*_: Any, **__: Any) -> dict[str, Any]:
    return _result(
        ok=False,
        code="permission_denied",
        message="Invitation lookup by token is restricted. Accept invitations through the secure RPC.",
    )


def _invite_debug(label: str, value: Any) -> None:
    print(f"INVITE_DEBUG {label}={value}", flush=True)


def _invite_debug_stage(stage: str) -> None:
    print(f"INVITE_DEBUG stage={stage}", flush=True)


def accept_invitation(raw_token: str, *, sb: Any | None = None) -> dict[str, Any]:
    client = _client(sb)
    token = normalize_invite_token(raw_token)

    if not is_valid_looking_invite_token(token):
        _invite_debug("acceptance_rpc_called", "false")
        _invite_debug("acceptance_result_ok", "false")
        _invite_debug("acceptance_result_code", "invalid")
        _invite_debug("rpc_response_data_type", "none")
        _invite_debug("rpc_response_row_count", 0)
        return _result(ok=False, code="invalid", message="This invitation link is invalid.")

    try:
        _invite_debug_stage("before RPC invocation")
        _invite_debug("acceptance_rpc_called", "true")
        data = client.rpc("accept_league_invite", {"raw_token": token}).execute().data or []
        _invite_debug_stage("after RPC invocation")
    except Exception as exc:
        result = _acceptance_error(exc)
        exception_fields = sanitized_rpc_exception_fields(exc)
        _invite_debug("rpc_exception_class", exception_fields["class"])
        _invite_debug("rpc_exception_code", exception_fields["code"])
        _invite_debug("rpc_exception_message", exception_fields["message"])
        _invite_debug("rpc_exception_details", exception_fields["details"])
        _invite_debug("rpc_exception_hint", exception_fields["hint"])
        _invite_debug("acceptance_result_ok", "false")
        _invite_debug("acceptance_result_code", result.get("code") or "database_error")
        _invite_debug("rpc_response_data_type", "exception")
        _invite_debug("rpc_response_row_count", 0)
        return result

    rows = data if isinstance(data, list) else [data]
    _invite_debug_stage("before RPC response parsing")
    _invite_debug("rpc_response_data_type", type(data).__name__)
    _invite_debug("rpc_response_row_count", len(rows))

    if not rows:
        _invite_debug_stage("after RPC response parsing")
        _invite_debug("acceptance_result_ok", "false")
        _invite_debug("acceptance_result_code", "invitation_not_found")
        return _result(ok=False, code="invitation_not_found", message="This invitation link is invalid.")

    accepted = rows[0]
    result = _result(
        ok=True,
        code="accepted",
        message="Invitation accepted.",
        invitation={
            "league_id": accepted.get("league_id"),
            "league_team_id": accepted.get("league_team_id"),
            "id": accepted.get("membership_id"),
        },
        league_id=accepted.get("league_id"),
        league_team_id=accepted.get("league_team_id"),
        role=accepted.get("role"),
        email=accepted.get("email"),
    )
    _invite_debug_stage("after RPC response parsing")
    _invite_debug("acceptance_result_ok", "true")
    _invite_debug("acceptance_result_code", "accepted")
    return result
