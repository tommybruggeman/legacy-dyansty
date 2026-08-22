from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class HistoricalContractSeasonRead:
    contract_season_id: str; agreement_id: str; season: int; salary: Decimal; obligation_status: str
    canonical_team_id: str; player_id: str; temporal_role: str


@dataclass(frozen=True)
class ContractReadRecord:
    agreement_id: str; league_id: str; canonical_team_id: str; canonical_team_name: str
    player_id: str; sleeper_player_id: str; player_name: str; player_position: str | None
    agreement_status: str; operational_season: int; operational_contract_season_id: str | None
    operational_obligation_status: str | None; operational_salary: Decimal | None; expiration_season: int
    remaining_contract_seasons: int; future_contract_seasons: tuple[HistoricalContractSeasonRead,...]
    contract_type: str; source_legacy_contract_id: str | None; provenance: dict[str,Any]; warnings: tuple[str,...]


@dataclass(frozen=True)
class LegacyCompatibleContractRecord:
    values: dict[str,Any]; provenance: dict[str,str]; compatibility_warnings: tuple[str,...]

