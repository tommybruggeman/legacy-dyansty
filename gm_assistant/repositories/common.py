from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gm_assistant.request_context import AssistantRequestContext


class RepositoryError(RuntimeError):
    """Raised when repository data access cannot be safely scoped."""


@dataclass(frozen=True)
class RepositorySource:
    domain: str
    source_name: str
    scope: str
    league_id: str | None = None
    league_team_id: str | None = None
    player_id: str | None = None
    status: str = "found"
    row_count: int = 0


@dataclass(frozen=True)
class RepositoryResult:
    ok: bool
    rows: list[dict[str, Any]]
    source: RepositorySource
    error: str | None = None

    @property
    def empty(self) -> bool:
        return self.ok and not self.rows


def result(
    *,
    domain: str,
    source_name: str,
    context: AssistantRequestContext,
    rows: list[dict[str, Any]],
    scope: str,
    league_team_id: str | None = None,
    player_id: str | None = None,
) -> RepositoryResult:
    return RepositoryResult(
        ok=True,
        rows=rows,
        source=RepositorySource(
            domain=domain,
            source_name=source_name,
            scope=scope,
            league_id=context.league_id if scope != "global" else None,
            league_team_id=league_team_id,
            player_id=player_id,
            status="found" if rows else "empty",
            row_count=len(rows),
        ),
    )


def failed(
    *,
    domain: str,
    source_name: str,
    context: AssistantRequestContext,
    scope: str,
    league_team_id: str | None = None,
    error: str = "retrieval_failed",
) -> RepositoryResult:
    return RepositoryResult(
        ok=False,
        rows=[],
        source=RepositorySource(
            domain=domain,
            source_name=source_name,
            scope=scope,
            league_id=context.league_id if scope != "global" else None,
            league_team_id=league_team_id,
            status="failed",
            row_count=0,
        ),
        error=error,
    )


def rows(query: Any) -> list[dict[str, Any]]:
    return query.execute().data or []


def clean_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


def clean_text(value: Any) -> str | None:
    return clean_id(value)


def safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace("$", "").replace(",", ""))
    except Exception:
        return None


def require_scoped_context(context: AssistantRequestContext) -> None:
    if not isinstance(context, AssistantRequestContext):
        raise RepositoryError("Repository access requires a trusted assistant request context.")
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise RepositoryError("Repository access requires user, league, and league-team scope.")


def load_league_team(sb: Any, context: AssistantRequestContext, league_team_id: str | None) -> dict[str, Any] | None:
    if not league_team_id:
        return None
    found = rows(
        sb.table("league_teams")
        .select("id,league_id,team_name,owner_name")
        .eq("id", league_team_id)
        .eq("league_id", context.league_id)
        .limit(1)
    )
    return found[0] if found else None


def load_league_teams(sb: Any, context: AssistantRequestContext) -> list[dict[str, Any]]:
    return rows(
        sb.table("league_teams")
        .select("id,league_id,team_name,owner_name")
        .eq("league_id", context.league_id)
        .limit(200)
    )


def team_owner_key(team: dict[str, Any]) -> str | None:
    for key in ("owner_name", "team_name"):
        value = clean_text(team.get(key))
        if value:
            return value
    return None


def is_released_roster_row(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or row.get("roster_status") or row.get("contract_status") or "").strip().lower()
    return status in {"released", "release", "cut", "dropped", "waived", "inactive_released"}


def resolve_team_reference(value: Any, teams: list[dict[str, Any]]) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for team in teams:
        team_id = str(team.get("id") or "").strip()
        if text == team_id:
            return team_id
        aliases = {
            str(team.get("owner_name") or "").strip().lower(),
            str(team.get("team_name") or "").strip().lower(),
        }
        if text.lower() in {alias for alias in aliases if alias}:
            return team_id
    return None
