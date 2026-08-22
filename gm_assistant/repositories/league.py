from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import RepositoryResult, require_scoped_context, result, rows
from gm_assistant.request_context import AssistantRequestContext


class LeagueRepository:
    """League-scoped league intelligence and settings access."""

    def __init__(self, sb: Any):
        self.sb = sb

    def get_league_brain(self, context: AssistantRequestContext) -> RepositoryResult:
        require_scoped_context(context)
        brain_rows = rows(
            self.sb.table("league_brain")
            .select("*")
            .eq("league_id", context.league_id)
            .limit(1)
        )
        return result(domain="league_intelligence", source_name="league_brain", context=context, rows=brain_rows, scope="league")

    def get_league_settings(self, context: AssistantRequestContext) -> RepositoryResult:
        require_scoped_context(context)
        settings_rows = rows(self.sb.table("league_settings").select("*").eq("league_id", context.league_id))
        return result(domain="league_settings", source_name="league_settings", context=context, rows=settings_rows, scope="league")

    def get_rule_sources(self, context: AssistantRequestContext) -> RepositoryResult:
        require_scoped_context(context)
        rule_rows = rows(self.sb.table("league_rules").select("*").eq("league_id", context.league_id))
        return result(domain="league_rules", source_name="league_rules", context=context, rows=rule_rows, scope="league")
