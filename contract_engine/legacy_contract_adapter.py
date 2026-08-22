from __future__ import annotations

from .contract_read_models import LegacyCompatibleContractRecord


def project_legacy_contract_shape(record,legacy_row=None):
    legacy_row=legacy_row or {}; warnings=[]; provenance={
        "status":"normalized_agreement","salary":"normalized_operational_obligation","contract_years_left":"normalized_schedule",
        "owner_name":"canonical_team","player_name":"canonical_player","player_position":"canonical_player"}
    is_rookie=legacy_row.get("is_rookie")
    if is_rookie is not None:provenance["is_rookie"]="legacy_descriptive_fallback";warnings.append("is_rookie uses legacy descriptive fallback.")
    values={"id":record.source_legacy_contract_id or record.agreement_id,"agreement_id":record.agreement_id,"league_id":record.league_id,
        "league_team_id":record.canonical_team_id,"owner_name":record.canonical_team_name,"player_name":record.player_name,
        "player_position":record.player_position,"sleeper_player_id":record.sleeper_player_id,"player_id":record.player_id,
        "salary":float(record.operational_salary) if record.operational_salary is not None else None,
        "contract_years_left":record.remaining_contract_seasons,"years_remaining":record.remaining_contract_seasons,
        "contract_total_years":record.provenance.get("schedule_count"),
        "status":record.agreement_status,"agreement_status":record.agreement_status,"expiration_season":record.expiration_season,
        "operational_season":record.operational_season,"source_legacy_contract_id":record.source_legacy_contract_id,
        "is_rookie":is_rookie,"provenance":provenance,"compatibility_warnings":list(record.warnings)+warnings}
    return LegacyCompatibleContractRecord(values,provenance,tuple(values["compatibility_warnings"]))
