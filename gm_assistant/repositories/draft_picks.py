from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import (
    RepositoryError,
    clean_id,
    load_league_teams,
    require_scoped_context,
    resolve_team_reference,
    result,
    rows,
    safe_int,
)
from gm_assistant.request_context import AssistantRequestContext


class DraftPickRepository:
    """League-scoped draft pick access for the production draft_picks schema."""

    def __init__(self, sb: Any):
        self.sb = sb

    def get_draft_picks(
        self,
        context: AssistantRequestContext,
        *,
        league_team_id: str | None = None,
        seasons: list[int] | None = None,
    ):
        require_scoped_context(context)
        league_teams = load_league_teams(self.sb, context)
        team = _team_from_loaded(league_teams, league_team_id) if league_team_id else None
        if league_team_id and not team:
            raise RepositoryError("Requested team is not in the active league.")

        pick_rows = rows(self.sb.table("draft_picks").select("*").eq("league_id", context.league_id))
        pick_rows = [_draft_pick_row_with_scope(row, league_teams) for row in pick_rows]
        if seasons:
            allowed = {int(season) for season in seasons}
            pick_rows = [row for row in pick_rows if safe_int(row.get("season")) in allowed]
        if team:
            team_values = {
                str(value).strip()
                for value in (team.get("id"), team.get("team_name"), team.get("owner_name"))
                if value
            }
            pick_rows = [
                row for row in pick_rows
                if str(row.get("league_team_id") or "").strip() in team_values
                or str(row.get("team_id") or "").strip() in team_values
                or str(row.get("current_owner") or "").strip() in team_values
                or str(row.get("owner") or "").strip() in team_values
                or str(row.get("original_team") or "").strip() in team_values
            ]
        scope = "team" if league_team_id else "league"
        return result(domain="draft_picks", source_name="draft_picks", context=context, rows=pick_rows, scope=scope, league_team_id=league_team_id)


def _team_from_loaded(teams: list[dict[str, Any]], league_team_id: str | None) -> dict[str, Any] | None:
    if not league_team_id:
        return None
    for team in teams:
        if str(team.get("id") or "").strip() == str(league_team_id).strip():
            return team
    return None


def _draft_pick_row_with_scope(row: dict[str, Any], teams: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(row)
    current_owner = resolve_team_reference(out.get("current_owner"), teams)
    original_team = resolve_team_reference(out.get("original_team"), teams)
    if current_owner:
        out["resolved_current_owner_team_id"] = current_owner
    if original_team:
        out["resolved_original_team_id"] = original_team
    if current_owner and not clean_id(out.get("league_team_id")):
        out["league_team_id"] = current_owner
    return out
