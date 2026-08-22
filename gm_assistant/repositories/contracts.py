from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import (
    clean_id,
    clean_text,
    load_league_team,
    require_scoped_context,
    result,
    rows,
    safe_int,
)
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.contract_evidence import GMContractEvidenceService


class ContractRepository:
    """League-scoped contract access with team filtering by canonical team first."""

    def __init__(self, sb: Any):
        self.sb = sb
        self.mode = str(getattr(sb,"gm_contract_read_mode","normalized"))

    def get_contracts(
        self,
        context: AssistantRequestContext,
        *,
        league_team_ids: list[str] | None = None,
        player_ids: list[str] | None = None,
        contract_years_left: int | None = None,
    ):
        require_scoped_context(context)
        if self.mode=="legacy":
            contract_rows=rows(self.sb.table("contracts").select("*").eq("league_id",context.league_id));source_name="contracts_legacy_explicit"
        else:
            contract_rows = [item.to_row() for item in GMContractEvidenceService(self.sb,mode=self.mode).load(context.league_id)];source_name="normalized_contract_model"
        contract_rows = self._filter_to_teams(context, contract_rows, league_team_ids)
        contract_rows = _filter_players(contract_rows, player_ids or [])
        if contract_years_left is not None:
            contract_rows = [
                row for row in contract_rows
                if safe_int(row.get("contract_years_left") or row.get("years_remaining")) == contract_years_left
            ]
        normalized = [_contract_row_with_scope(row, context) for row in contract_rows]
        scope = "team" if league_team_ids else "league"
        league_team_id = league_team_ids[0] if league_team_ids and len(league_team_ids) == 1 else None
        return result(domain="contracts", source_name=source_name, context=context, rows=normalized, scope=scope, league_team_id=league_team_id)

    def _filter_to_teams(
        self,
        context: AssistantRequestContext,
        contract_rows: list[dict[str, Any]],
        league_team_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        if not league_team_ids:
            return contract_rows
        teams = [load_league_team(self.sb, context, team_id) for team_id in league_team_ids]
        teams = [team for team in teams if team]
        allowed_ids = {str(team.get("id")) for team in teams if team.get("id")}
        allowed_names = {
            str(value).strip().lower()
            for team in teams
            for value in (team.get("owner_name"), team.get("team_name"))
            if value is not None and str(value).strip()
        }
        out = []
        for row in contract_rows:
            row_team_id = clean_id(row.get("roster_team_id") or row.get("league_team_id") or row.get("team_id"))
            row_owner = clean_text(row.get("owner_name") or row.get("team_name") or row.get("owner"))
            if row_team_id and row_team_id in allowed_ids:
                out.append(row)
            elif row_owner and row_owner.lower() in allowed_names:
                out.append(row)
        return out


def _contract_row_with_scope(row: dict[str, Any], context: AssistantRequestContext) -> dict[str, Any]:
    out = dict(row)
    if not out.get("season"):
        if str(out.get("data_authority"))=="normalized_contract_model":raise RuntimeError("Normalized GM contract evidence must declare its operational season.")
        out["season"]=context.current_season
    team_id = clean_id(out.get("league_team_id") or out.get("team_id"))
    if not team_id and clean_text(out.get("owner_name")) == clean_text(context.owner_name):
        team_id = context.league_team_id
    if team_id:
        out["league_team_id"] = team_id
        out["team_id"] = team_id
    if not out.get("status"):
        if str(out.get("data_authority"))=="normalized_contract_model":raise RuntimeError("Normalized GM contract evidence must declare lifecycle status.")
        out["status"]="legacy_unresolved"
    return out


def _filter_players(rows_in: list[dict[str, Any]], player_ids: list[str]) -> list[dict[str, Any]]:
    if not player_ids:
        return rows_in
    allowed = set(player_ids)
    return [
        row for row in rows_in
        if clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id")) in allowed
    ]
