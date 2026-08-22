from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gm_assistant.repositories import (
    CapRepository,
    DraftPickRepository,
    LeagueRepository,
    RosterRepository,
    TeamRepository,
)
from gm_assistant.repositories.common import RepositoryError, RepositoryResult
from gm_assistant.request_context import (
    AssistantContextError,
    AssistantRequestContext,
    LEAGUE_PUBLIC_READ,
    TEAM_ADVICE,
)


class AssistantRetrievalError(RuntimeError):
    """Raised when assistant retrieval cannot be safely scoped."""


@dataclass(frozen=True)
class RetrievalSource:
    table: str
    league_id: str
    league_team_id: str | None = None
    row_count: int = 0


@dataclass(frozen=True)
class RetrievalResult:
    ok: bool
    rows: list[dict]
    source: RetrievalSource
    error: str | None = None

    @property
    def empty(self) -> bool:
        return self.ok and not self.rows


def _from_repository(repo_result: RepositoryResult) -> RetrievalResult:
    return RetrievalResult(
        ok=repo_result.ok,
        rows=repo_result.rows,
        source=RetrievalSource(
            table=repo_result.source.source_name,
            league_id=repo_result.source.league_id or "",
            league_team_id=repo_result.source.league_team_id,
            row_count=repo_result.source.row_count,
        ),
        error=repo_result.error,
    )


def get_team_roster(
    sb: Any,
    context: AssistantRequestContext,
    *,
    league_team_id: str | None = None,
) -> RetrievalResult:
    _require_context(context, TEAM_ADVICE)
    try:
        return _from_repository(RosterRepository(sb).get_team_roster(context, league_team_id=league_team_id or context.league_team_id))
    except RepositoryError as exc:
        raise _repository_error_to_retrieval_error(exc) from exc


def get_team_brain(
    sb: Any,
    context: AssistantRequestContext,
    *,
    league_team_id: str | None = None,
) -> RetrievalResult:
    _require_context(context, TEAM_ADVICE)
    try:
        return _from_repository(TeamRepository(sb).get_team_brain(context, league_team_id=league_team_id or context.league_team_id))
    except RepositoryError as exc:
        raise _repository_error_to_retrieval_error(exc) from exc


def get_league_brain(sb: Any, context: AssistantRequestContext) -> RetrievalResult:
    _require_context(context, LEAGUE_PUBLIC_READ)
    try:
        return _from_repository(LeagueRepository(sb).get_league_brain(context))
    except RepositoryError as exc:
        raise _repository_error_to_retrieval_error(exc) from exc


def get_cap_summary(
    sb: Any,
    context: AssistantRequestContext,
    *,
    league_team_id: str | None = None,
) -> RetrievalResult:
    _require_context(context, LEAGUE_PUBLIC_READ)
    try:
        return _from_repository(CapRepository(sb).get_cap_summary(context, league_team_id=league_team_id))
    except RepositoryError as exc:
        raise _repository_error_to_retrieval_error(exc) from exc


def get_draft_picks(
    sb: Any,
    context: AssistantRequestContext,
    *,
    league_team_id: str | None = None,
    seasons: list[int] | None = None,
) -> RetrievalResult:
    _require_context(context, LEAGUE_PUBLIC_READ)
    try:
        return _from_repository(DraftPickRepository(sb).get_draft_picks(context, league_team_id=league_team_id, seasons=seasons))
    except RepositoryError as exc:
        raise _repository_error_to_retrieval_error(exc) from exc


def get_transactions(
    sb: Any,
    context: AssistantRequestContext,
    *,
    limit: int = 25,
) -> RetrievalResult:
    _require_context(context, LEAGUE_PUBLIC_READ)
    return _select_rows(sb, "transactions_enriched", context, limit=max(1, min(int(limit or 25), 100)))


def _require_context(context: AssistantRequestContext, scope: str) -> None:
    if not isinstance(context, AssistantRequestContext):
        raise AssistantRetrievalError("Assistant retrieval requires a valid request context.")
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise AssistantRetrievalError("Assistant retrieval requires user, league, and team scope.")
    if not context.has_scope(scope):
        raise AssistantRetrievalError("Assistant context does not include the required permission scope.")


def _repository_error_to_retrieval_error(exc: RepositoryError) -> AssistantRetrievalError:
    return AssistantRetrievalError(str(exc))


def _validate_league_team(sb: Any, context: AssistantRequestContext, league_team_id: str) -> str:
    team = _load_league_team(sb, context, league_team_id)
    if not team:
        raise AssistantRetrievalError("Requested team is not in the active league.")
    return str(team["id"])


def _load_league_team(sb: Any, context: AssistantRequestContext, league_team_id: str | None) -> dict | None:
    if not league_team_id:
        return None
    rows = _rows(
        sb.table("league_teams")
        .select("id,league_id,team_name,owner_name")
        .eq("id", league_team_id)
        .eq("league_id", context.league_id)
        .limit(1)
    )
    return rows[0] if rows else None


def _load_league_teams(sb: Any, context: AssistantRequestContext) -> list[dict[str, Any]]:
    return _rows(
        sb.table("league_teams")
        .select("id,league_id,team_name,owner_name")
        .eq("league_id", context.league_id)
        .limit(200)
    )


def _team_from_loaded(teams: list[dict[str, Any]], league_team_id: str | None) -> dict | None:
    if not league_team_id:
        return None
    for team in teams:
        if str(team.get("id") or "").strip() == str(league_team_id).strip():
            return team
    return None


def _draft_pick_row_with_scope(row: dict[str, Any], teams: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(row)
    current_owner = _resolve_team_reference(out.get("current_owner"), teams)
    original_team = _resolve_team_reference(out.get("original_team"), teams)
    if current_owner:
        out["resolved_current_owner_team_id"] = current_owner
    if original_team:
        out["resolved_original_team_id"] = original_team
    return out


def _resolve_team_reference(value: Any, teams: list[dict[str, Any]]) -> str | None:
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


def _team_identity_row(sb: Any, context: AssistantRequestContext, team: dict[str, Any]) -> dict[str, Any]:
    row = dict(team)
    row["league_team_id"] = team.get("id")
    league_name = _load_league_name(sb, context.league_id)
    if league_name:
        row["league_name"] = league_name
    return row


def _load_league_name(sb: Any, league_id: str) -> str | None:
    try:
        rows = _rows(
            sb.table("leagues")
            .select("id,name,league_name")
            .eq("id", league_id)
            .limit(1)
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    for key in ("name", "league_name"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _team_owner_key(team: dict) -> str | None:
    for key in ("owner_name", "team_name"):
        value = team.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _roster_row_from_contract(row: dict[str, Any], context: AssistantRequestContext, team: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["league_id"] = context.league_id
    out["league_team_id"] = team.get("id")
    out["team_id"] = team.get("id")
    out["team_name"] = team.get("team_name") or team.get("owner_name")
    out["owner_name"] = team.get("owner_name") or team.get("team_name")
    out["sleeper_id"] = row.get("sleeper_player_id") or row.get("sleeper_id") or row.get("player_id")
    out["position"] = row.get("player_position") or row.get("position")
    out["status"] = row.get("status") or row.get("roster_status") or "active"
    out["season"] = row.get("season") or context.current_season
    return out


def _is_released_roster_row(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or row.get("roster_status") or row.get("contract_status") or "").strip().lower()
    return status in {"released", "release", "cut", "dropped", "waived", "inactive_released"}


def _computed_cap_summary(sb: Any, context: AssistantRequestContext, team: dict | None) -> dict[str, Any] | None:
    if not team:
        return None
    owner_name = _team_owner_key(team)
    if not owner_name:
        return None
    rule_rows = _rows(
        sb.table("league_rules")
        .select("*")
        .eq("league_id", context.league_id)
        .limit(1)
    )
    if not rule_rows:
        return None
    salary_cap = _safe_float(rule_rows[0].get("salary_cap"))
    if salary_cap is None:
        return None
    contract_rows = _rows(
        sb.table("contracts")
        .select("*")
        .eq("league_id", context.league_id)
        .eq("owner_name", owner_name)
    )
    active_salary = 0.0
    for row in contract_rows:
        salary = _safe_float(row.get("salary"))
        if salary is None:
            return None
        active_salary += salary
    adjustment_rows = _rows(
        sb.table("cap_adjustments")
        .select("*")
        .eq("league_id", context.league_id)
        .eq("owner_name", owner_name)
        .eq("season", context.current_season)
    )
    adjustment_total = sum(_safe_float(row.get("amount")) or 0.0 for row in adjustment_rows)
    dead_cap = sum(
        _safe_float(row.get("amount")) or 0.0
        for row in adjustment_rows
        if str(row.get("adjustment_type") or "").strip().lower() == "dropped_player_charge"
    )
    cap_used = active_salary + adjustment_total
    available_cap = salary_cap - cap_used
    return {
        "league_id": context.league_id,
        "league_team_id": team.get("id"),
        "team_id": team.get("id"),
        "team_name": team.get("team_name") or team.get("owner_name"),
        "owner_name": owner_name,
        "season": context.current_season,
        "salary_cap": round(salary_cap, 2),
        "active_salary": round(active_salary, 2),
        "dead_cap": round(dead_cap, 2),
        "adjustment_total": round(adjustment_total, 2),
        "cap_used": round(cap_used, 2),
        "available_cap": round(available_cap, 2),
        "cap_space": round(available_cap, 2),
        "source_name": "computed_cap_summary",
    }


def _select_rows(
    sb: Any,
    table_name: str,
    context: AssistantRequestContext,
    *,
    league_team_id: str | None = None,
    team_filter_column: str | None = None,
    limit: int | None = None,
) -> RetrievalResult:
    try:
        query = sb.table(table_name).select("*").eq("league_id", context.league_id)
        if league_team_id and team_filter_column:
            query = query.eq(team_filter_column, league_team_id)
        if limit and hasattr(query, "limit"):
            query = query.limit(limit)
        rows = query.execute().data or []
        return _result(table_name, context, rows, league_team_id=league_team_id)
    except AssistantContextError:
        raise
    except Exception:
        return RetrievalResult(
            ok=False,
            rows=[],
            source=RetrievalSource(
                table=table_name,
                league_id=context.league_id,
                league_team_id=league_team_id,
                row_count=0,
            ),
            error="retrieval_failed",
        )


def _result(
    table_name: str,
    context: AssistantRequestContext,
    rows: list[dict],
    *,
    league_team_id: str | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        ok=True,
        rows=rows,
        source=RetrievalSource(
            table=table_name,
            league_id=context.league_id,
            league_team_id=league_team_id,
            row_count=len(rows),
        ),
    )


def _rows(query: Any) -> list[dict]:
    return query.execute().data or []


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace("$", "").replace(",", ""))
    except Exception:
        return None
