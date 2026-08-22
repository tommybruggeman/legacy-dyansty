from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import (
    RepositoryError,
    RepositoryResult,
    failed,
    is_released_roster_row,
    load_league_team,
    require_scoped_context,
    result,
    rows,
    team_owner_key,
)
from gm_assistant.request_context import AssistantRequestContext


class RosterRepository:
    """Team-scoped roster access with production contract-table fallback."""

    def __init__(self, sb: Any):
        self.sb = sb

    def get_team_roster(self, context: AssistantRequestContext, *, league_team_id: str | None = None) -> RepositoryResult:
        require_scoped_context(context)
        team = load_league_team(self.sb, context, league_team_id or context.league_team_id)
        if not team:
            raise RepositoryError("Requested team is not in the active league.")
        team_id = str(team["id"])

        try:
            roster_rows = rows(
                self.sb.table("team_roster_state")
                .select("*")
                .eq("league_id", context.league_id)
                .eq("team_id", team_id)
            )
        except Exception:
            return failed(domain="roster", source_name="team_roster_state", context=context, scope="team", league_team_id=team_id)
        if roster_rows:
            return result(domain="roster", source_name="team_roster_state", context=context, rows=roster_rows, scope="team", league_team_id=team_id)

        owner_name = team_owner_key(team)
        if owner_name:
            contract_rows = rows(
                self.sb.table("contracts")
                .select("*")
                .eq("league_id", context.league_id)
                .eq("owner_name", owner_name)
            )
            contract_rows = [row for row in contract_rows if not is_released_roster_row(row)]
            if contract_rows:
                normalized = [_roster_row_from_contract(row, context, team) for row in contract_rows]
                return result(domain="roster", source_name="contracts", context=context, rows=normalized, scope="team", league_team_id=team_id)

        profile_rows = rows(
            self.sb.table("player_strategic_profiles")
            .select("*")
            .eq("league_id", context.league_id)
            .eq("league_team_id", team_id)
        )
        return result(domain="roster", source_name="player_strategic_profiles", context=context, rows=profile_rows, scope="team", league_team_id=team_id)


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
