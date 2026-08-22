from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import (
    RepositoryError,
    RepositoryResult,
    failed,
    load_league_team,
    require_scoped_context,
    result,
    rows,
    safe_float,
    team_owner_key,
)
from gm_assistant.request_context import AssistantRequestContext
from services.publication_context import published_cap_rows


class CapRepository:
    """League/team-scoped cap access with deterministic production fallback."""

    def __init__(self, sb: Any):
        self.sb = sb

    def get_cap_summary(self, context: AssistantRequestContext, *, league_team_id: str | None = None) -> RepositoryResult:
        require_scoped_context(context)
        team = None
        if league_team_id:
            team = load_league_team(self.sb, context, league_team_id)
            if not team:
                raise RepositoryError("Requested team is not in the active league.")

        try:
            cap_rows = published_cap_rows(self.sb, context.league_id)
        except Exception:
            fallback = self._computed_cap_summary(context, team)
            if fallback:
                return result(domain="cap", source_name="contracts/cap_adjustments/league_rules", context=context, rows=[fallback], scope="team", league_team_id=league_team_id)
            return failed(domain="cap", source_name="published_operation_33_cap_authority", context=context, scope="team", league_team_id=league_team_id)

        if not cap_rows and team:
            legacy_rows = rows(
                self.sb.table("v_team_caps")
                .select("*")
                .eq("league_id", context.league_id)
                .eq("league_team_id", team.get("id"))
            )
            if legacy_rows:
                return result(domain="cap", source_name="v_team_caps", context=context,
                              rows=legacy_rows, scope="team", league_team_id=league_team_id)
            fallback = self._computed_cap_summary(context, team)
            if fallback:
                return result(domain="cap", source_name="contracts/cap_adjustments/league_rules", context=context, rows=[fallback], scope="team", league_team_id=league_team_id)

        if not team:
            return result(domain="cap", source_name="published_operation_33_cap_authority", context=context, rows=cap_rows, scope="league")

        team_names = {
            str(value).strip()
            for value in (team.get("id"), team.get("team_name"), team.get("owner_name"))
            if value
        }
        filtered = [
            row for row in cap_rows
            if str(row.get("league_team_id") or "").strip() in team_names
            or str(row.get("team_id") or "").strip() in team_names
            or str(row.get("owner_name") or "").strip() in team_names
            or str(row.get("team_name") or "").strip() in team_names
        ]
        return result(domain="cap", source_name="published_operation_33_cap_authority", context=context, rows=filtered, scope="team", league_team_id=league_team_id)

    def _computed_cap_summary(self, context: AssistantRequestContext, team: dict[str, Any] | None) -> dict[str, Any] | None:
        if not team:
            return None
        owner_name = team_owner_key(team)
        if not owner_name:
            return None
        rule_rows = rows(self.sb.table("league_rules").select("*").eq("league_id", context.league_id).limit(1))
        if not rule_rows:
            return None
        salary_cap = safe_float(rule_rows[0].get("salary_cap"))
        if salary_cap is None:
            return None
        contract_rows = rows(
            self.sb.table("contracts")
            .select("*")
            .eq("league_id", context.league_id)
            .eq("owner_name", owner_name)
        )
        active_salary = 0.0
        for row in contract_rows:
            salary = safe_float(row.get("salary"))
            if salary is None:
                return None
            active_salary += salary
        adjustment_rows = rows(
            self.sb.table("cap_adjustments")
            .select("*")
            .eq("league_id", context.league_id)
            .eq("owner_name", owner_name)
            .eq("season", context.current_season)
        )
        adjustment_total = sum(safe_float(row.get("amount")) or 0.0 for row in adjustment_rows)
        dead_cap = sum(
            safe_float(row.get("amount")) or 0.0
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
