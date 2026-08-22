from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import (
    RepositoryError,
    RepositoryResult,
    load_league_team,
    require_scoped_context,
    result,
    rows,
)
from gm_assistant.request_context import AssistantRequestContext


class TeamRepository:
    """League/team-scoped team identity and team intelligence access."""

    def __init__(self, sb: Any):
        self.sb = sb

    def get_team_brain(self, context: AssistantRequestContext, *, league_team_id: str | None = None) -> RepositoryResult:
        require_scoped_context(context)
        team = load_league_team(self.sb, context, league_team_id or context.league_team_id)
        if not team:
            raise RepositoryError("Requested team is not in the active league.")
        team_id = str(team["id"])
        brain_rows = rows(
            self.sb.table("team_brain")
            .select("*")
            .eq("league_id", context.league_id)
            .eq("league_team_id", team_id)
            .limit(1)
        )
        if brain_rows:
            league_name = self.league_name(context.league_id)
            normalized = [dict(row, league_name=league_name) if league_name else row for row in brain_rows]
            return result(domain="team_intelligence", source_name="team_brain", context=context, rows=normalized, scope="team", league_team_id=team_id)

        identity = dict(team)
        identity["league_team_id"] = team.get("id")
        league_name = self.league_name(context.league_id)
        if league_name:
            identity["league_name"] = league_name
        return result(domain="team_intelligence", source_name="league_teams", context=context, rows=[identity], scope="team", league_team_id=team_id)

    def get_team_brain_rankings(self, context: AssistantRequestContext, *, limit: int = 12) -> RepositoryResult:
        require_scoped_context(context)
        brain_rows = rows(
            self.sb.table("team_brain")
            .select("*")
            .eq("league_id", context.league_id)
            .limit(limit)
        )
        return result(domain="team_intelligence", source_name="team_brain", context=context, rows=brain_rows, scope="league")

    def league_name(self, league_id: str) -> str | None:
        try:
            league_rows = rows(
                self.sb.table("leagues")
                .select("id,name,league_name")
                .eq("id", league_id)
                .limit(1)
            )
        except Exception:
            return None
        if not league_rows:
            return None
        row = league_rows[0]
        for key in ("name", "league_name"):
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None
