from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import clean_id, require_scoped_context, result, rows
from gm_assistant.request_context import AssistantRequestContext


class PlayerRepository:
    """Player intelligence access.

    `player_intelligence` is global in production and is intentionally queried
    without a league_id filter. League-aware player rows come from scoped brain
    tables and roster/contract joins.
    """

    def __init__(self, sb: Any):
        self.sb = sb

    def get_scoped_player_profiles(self, context: AssistantRequestContext, *, player_ids: list[str] | None = None):
        require_scoped_context(context)
        profile_rows = rows(self.sb.table("player_strategic_profiles").select("*").eq("league_id", context.league_id))
        value_rows = rows(self.sb.table("league_relative_player_values").select("*").eq("league_id", context.league_id))
        profile_rows = _filter_players(profile_rows, player_ids or [])
        value_rows = _filter_players(value_rows, player_ids or [])
        by_id = {
            clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id")): dict(row)
            for row in profile_rows
            if clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id"))
        }
        for row in value_rows:
            player_id = clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id"))
            if not player_id:
                continue
            merged = by_id.setdefault(player_id, {})
            merged.update({key: value for key, value in row.items() if value not in (None, "", [], {})})
        return result(
            domain="player_intelligence",
            source_name="player_strategic_profiles/league_relative_player_values",
            context=context,
            rows=list(by_id.values()),
            scope="team",
            league_team_id=context.league_team_id,
        )

    def get_global_player_intelligence(self, context: AssistantRequestContext, *, player_ids: list[str] | None = None):
        require_scoped_context(context)
        intelligence_rows = rows(self.sb.table("player_intelligence").select("*"))
        intelligence_rows = _filter_players(intelligence_rows, player_ids or [])
        return result(
            domain="player_intelligence",
            source_name="player_intelligence",
            context=context,
            rows=intelligence_rows,
            scope="global",
        )


def _filter_players(rows_in: list[dict[str, Any]], player_ids: list[str]) -> list[dict[str, Any]]:
    if not player_ids:
        return rows_in
    allowed = set(player_ids)
    return [
        row for row in rows_in
        if clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id")) in allowed
    ]
