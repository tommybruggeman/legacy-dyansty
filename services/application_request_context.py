from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


class ContextFailureCode(str, Enum):
    UNAUTHENTICATED = "unauthenticated"
    NO_ACTIVE_LEAGUE_SELECTED = "no_active_league_selected"
    MEMBERSHIP_NOT_FOUND = "membership_not_found"
    DUPLICATE_MEMBERSHIP = "duplicate_membership"
    LEAGUE_TEAM_NOT_FOUND = "league_team_not_found"
    LEAGUE_TEAM_MISMATCH = "league_team_mismatch"
    LEGACY_IDENTITY_REQUIRED = "legacy_identity_required"
    BACKEND_UNAVAILABLE = "unavailable_backend"
    INVALID_CONTEXT = "invalid_context"


class IdentitySource(str, Enum):
    AUTHENTICATED_USER = "authenticated_user"
    EXPLICIT_ACTIVE_LEAGUE = "explicit_active_league"
    MEMBERSHIP_LEAGUE_TEAM_ID = "membership.league_team_id"
    LEGACY_MEMBERSHIP_TEAM_ID = "legacy_membership.team_id"


@dataclass(frozen=True)
class IdentityProvenance:
    user_id: str
    league_id: str
    league_team_id: str
    legacy_fallback_used: bool = False


@dataclass(frozen=True)
class ApplicationRequestContext:
    user_id: str
    league_id: str
    league_team_id: str
    role: str
    permission_scopes: tuple[str, ...]
    membership_id: str | None
    season: int | None
    provenance: IdentityProvenance

    def has_scope(self, scope: str) -> bool:
        return scope in self.permission_scopes


@dataclass(frozen=True)
class ContextFailure:
    code: ContextFailureCode
    message: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextResolution:
    context: ApplicationRequestContext | None = None
    failure: ContextFailure | None = None

    @property
    def ok(self) -> bool:
        return self.context is not None and self.failure is None


@dataclass(frozen=True)
class ContextRequest:
    authenticated_user_id: str | None
    active_league_id: str | None
    season: int | None = None
    allow_legacy_team_id: bool = True


class IdentityRepository(Protocol):
    def list_memberships(self, user_id: str) -> Sequence[Mapping[str, Any]]: ...

    def list_memberships_for_user_and_league(self, user_id: str, league_id: str) -> Sequence[Mapping[str, Any]]: ...

    def get_league_team(self, league_team_id: str) -> Mapping[str, Any] | None: ...


def resolve_permission_scopes(role: str | None) -> tuple[str, ...]:
    normalized = _clean(role) or "member"
    scopes = ["league_read"]
    if normalized in {"owner", "co_owner", "co-owner", "commissioner", "host", "admin"}:
        scopes.append("team_control")
    if normalized in {"commissioner", "host", "admin"}:
        scopes.append("league_admin")
    return tuple(scopes)


class ApplicationContextResolver:
    """Resolve authorization identity without display-name inference or implicit selection."""

    def __init__(self, repository: IdentityRepository):
        self.repository = repository

    def resolve(self, request: ContextRequest) -> ContextResolution:
        user_id = _clean(request.authenticated_user_id)
        if not user_id or user_id == "default":
            return _failure(ContextFailureCode.UNAUTHENTICATED, "Authenticated user identity is required.")

        league_id = _clean(request.active_league_id)
        if not league_id:
            try:
                memberships = self.repository.list_memberships(user_id)
            except Exception as exc:
                return _backend_failure(exc, "membership_list")
            return _failure(
                ContextFailureCode.NO_ACTIVE_LEAGUE_SELECTED,
                "An explicit active league is required.",
                membership_count=len(memberships),
                multiple_memberships=len(memberships) > 1,
            )

        try:
            memberships = self.repository.list_memberships_for_user_and_league(user_id, league_id)
        except Exception as exc:
            return _backend_failure(exc, "membership_lookup")
        if not memberships:
            return _failure(ContextFailureCode.MEMBERSHIP_NOT_FOUND, "No membership matches the authenticated user and active league.")
        if len(memberships) > 1:
            return _failure(
                ContextFailureCode.DUPLICATE_MEMBERSHIP,
                "Multiple memberships match the authenticated user and active league.",
                membership_count=len(memberships),
            )
        membership = memberships[0]

        membership_user_id = _clean(membership.get("user_id"))
        membership_league_id = _clean(membership.get("league_id"))
        if membership_user_id != user_id or membership_league_id != league_id:
            return _failure(
                ContextFailureCode.INVALID_CONTEXT,
                "Membership identity does not match the requested user and league.",
                membership_user_matches=membership_user_id == user_id,
                membership_league_matches=membership_league_id == league_id,
            )

        team_id = _clean(membership.get("league_team_id"))
        team_source = IdentitySource.MEMBERSHIP_LEAGUE_TEAM_ID
        legacy_used = False
        if not team_id:
            legacy_team_id = _clean(membership.get("team_id"))
            if not legacy_team_id or not request.allow_legacy_team_id:
                return _failure(
                    ContextFailureCode.LEGACY_IDENTITY_REQUIRED,
                    "Membership has no canonical league_team_id.",
                    legacy_team_id_present=bool(legacy_team_id),
                )
            team_id = legacy_team_id
            team_source = IdentitySource.LEGACY_MEMBERSHIP_TEAM_ID
            legacy_used = True

        try:
            team = self.repository.get_league_team(team_id)
        except Exception as exc:
            return _backend_failure(exc, "league_team_lookup")
        if not team:
            return _failure(ContextFailureCode.LEAGUE_TEAM_NOT_FOUND, "The membership league team was not found.", legacy_fallback_used=legacy_used)
        if _clean(team.get("league_id")) != league_id:
            return _failure(ContextFailureCode.LEAGUE_TEAM_MISMATCH, "The membership league team belongs to a different league.", legacy_fallback_used=legacy_used)

        role = (_clean(membership.get("role")) or "member").lower()
        provenance = IdentityProvenance(
            user_id=IdentitySource.AUTHENTICATED_USER.value,
            league_id=IdentitySource.EXPLICIT_ACTIVE_LEAGUE.value,
            league_team_id=team_source.value,
            legacy_fallback_used=legacy_used,
        )
        return ContextResolution(
            context=ApplicationRequestContext(
                user_id=user_id,
                league_id=league_id,
                league_team_id=team_id,
                role=role,
                permission_scopes=resolve_permission_scopes(role),
                membership_id=_clean(membership.get("id")),
                season=request.season,
                provenance=provenance,
            )
        )


def _failure(code: ContextFailureCode, message: str, **diagnostics: Any) -> ContextResolution:
    return ContextResolution(failure=ContextFailure(code, message, diagnostics))


def _backend_failure(exc: Exception, operation: str) -> ContextResolution:
    return _failure(
        ContextFailureCode.BACKEND_UNAVAILABLE,
        "Identity backend is unavailable.",
        operation=operation,
        exception_type=type(exc).__name__,
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
