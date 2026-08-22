from __future__ import annotations

import logging

from .contract_read_service import ContractReadService, compare_normalized_and_legacy_reads


def load_internal_contract_rows(client, league_id: str | None = None, *, compare: bool = False) -> list[dict]:
    """Return normalized active-contract rows for non-UI analytics and reporting.

    Expired agreements are deliberately excluded: an internal consumer asking for
    current contracts must not accidentally treat a satisfied 2025 obligation as
    an active 2026 contract.
    """
    league_ids = [str(league_id)] if league_id else _league_ids(client)
    output: list[dict] = []
    for current_league_id in league_ids:
        service = ContractReadService(client, mode="normalized")
        normalized = service.project_legacy_contract_shape(current_league_id, include_expired=False)
        if compare:
            legacy = client.table("contracts").select("*").eq("league_id", current_league_id).execute().data or []
            diagnostics = compare_normalized_and_legacy_reads(
                normalized, legacy, service.get_contract_operational_season(current_league_id),
                current_league_id, "contract_engine.internal_reads", "compare",
            )
            logging.getLogger(__name__).info("internal_contract_read_comparison %s", diagnostics)
        output.extend(normalized)
    return output


def _league_ids(client) -> list[str]:
    agreements = client.table("contract_agreements").select("league_id").execute().data or []
    ids = sorted({str(row["league_id"]) for row in agreements if row.get("league_id")})
    if not ids:
        raise RuntimeError("No normalized contract league is available for internal contract reads.")
    return ids
