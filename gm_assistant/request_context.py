from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from season_engine import SeasonAuthorityError, SeasonResolver
from services.publication_context import publication_generation


class AssistantContextError(RuntimeError):
    """Raised when an assistant request cannot be safely scoped."""


TEAM_ADVICE = "team_advice"
LEAGUE_PUBLIC_READ = "league_public_read"
LEAGUE_ADMIN = "league_admin"
ASSISTANT_DEBUG = "assistant_debug"


@dataclass(frozen=True)
class AssistantRequestContext:
    user_id: str
    league_id: str
    league_team_id: str
    role: str
    current_season: int
    requested_season: int
    permission_scopes: tuple[str, ...]
    membership_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    timezone: str = "America/Boise"
    team_name: str | None = None
    owner_name: str | None = None
    context_generation: int = 0

    def has_scope(self, scope: str) -> bool:
        return scope in self.permission_scopes


def build_assistant_request_context(
    *,
    sb: Any,
    user: dict | Any | None = None,
    user_id: str | None = None,
    active_league_id: str | None = None,
    requested_season: int | str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    timezone: str = "America/Boise",
) -> AssistantRequestContext:
    resolved_user_id = _extract_user_id(user) or _clean_id(user_id)
    if not resolved_user_id or resolved_user_id == "default":
        raise AssistantContextError("GM Assistant requires an authenticated user.")

    membership = _resolve_membership(
        sb,
        user_id=resolved_user_id,
        active_league_id=_clean_id(active_league_id),
    )
    league_id = _clean_id(membership.get("league_id"))
    if not league_id:
        raise AssistantContextError("GM Assistant could not resolve the active league.")

    team = _resolve_membership_team(sb, league_id=league_id, membership=membership)
    league_team_id = _clean_id(team.get("id") if team else None)
    if not league_team_id:
        raise AssistantContextError("GM Assistant requires a verified league team.")

    current_season = _resolve_current_season(sb, league_id=league_id)
    final_requested_season = _coerce_season(requested_season) or current_season
    role = str(membership.get("role") or "member").strip().lower() or "member"

    return AssistantRequestContext(
        user_id=resolved_user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        membership_id=_clean_id(membership.get("id")),
        role=role,
        current_season=current_season,
        requested_season=final_requested_season,
        conversation_id=conversation_id,
        message_id=message_id,
        timezone=timezone,
        permission_scopes=resolve_permission_scopes(role),
        team_name=team.get("team_name") or team.get("owner_name"),
        owner_name=team.get("owner_name") or team.get("team_name"),
        context_generation=publication_generation(sb, league_id),
    )


def resolve_permission_scopes(role: str | None) -> tuple[str, ...]:
    normalized = str(role or "").strip().lower()
    scopes = [TEAM_ADVICE, LEAGUE_PUBLIC_READ]
    if normalized in {"commissioner", "admin", "league_admin"}:
        scopes.extend([LEAGUE_ADMIN, ASSISTANT_DEBUG])
    return tuple(scopes)


def _resolve_membership(sb: Any, *, user_id: str, active_league_id: str | None) -> dict:
    query = (
        sb.table("league_memberships")
        .select("id,league_id,user_id,role,league_team_id,team_id")
        .eq("user_id", user_id)
    )
    if active_league_id:
        query = query.eq("league_id", active_league_id)

    rows = _rows(query)
    if not rows:
        raise AssistantContextError("No matching league membership found for GM Assistant.")
    return rows[0]


def _resolve_membership_team(sb: Any, *, league_id: str, membership: dict) -> dict | None:
    for candidate in (membership.get("league_team_id"), membership.get("team_id")):
        team_id = _clean_id(candidate)
        if not team_id:
            continue
        rows = _rows(
            sb.table("league_teams")
            .select("id,league_id,team_name,owner_name")
            .eq("id", team_id)
            .eq("league_id", league_id)
            .limit(1)
        )
        if rows:
            return rows[0]
    return None


def _resolve_current_season(sb: Any, *, league_id: str) -> int:
    try:
        return SeasonResolver(sb).get_active_season(league_id).season
    except SeasonAuthorityError as exc:
        raise AssistantContextError(str(exc)) from exc


def _coerce_season(value: Any) -> int | None:
    try:
        season = int(float(str(value).strip()))
    except Exception:
        return None
    if 2000 <= season <= 2100:
        return season
    return None


def _extract_user_id(user: dict | Any | None) -> str | None:
    if isinstance(user, dict):
        return _clean_id(user.get("id") or user.get("user_id"))
    return _clean_id(getattr(user, "id", None))


def _clean_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _rows(query: Any) -> list[dict]:
    return query.execute().data or []


def _optional_rows(query: Any) -> list[dict]:
    try:
        return _rows(query)
    except Exception:
        return []
