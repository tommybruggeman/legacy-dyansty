from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from contract_engine.contract_read_models import HistoricalContractSeasonRead


@dataclass(frozen=True)
class TradeCalculationContext:
    league_season: int
    contract_operational_season: int
    cap_calculation_season: int | None
    roster_snapshot_season: int
    draft_pick_season_basis: str = "league_authority"

    @property
    def supports_definitive_cap_legality(self) -> bool:
        return self.cap_calculation_season is not None and self.cap_calculation_season == self.contract_operational_season == self.league_season


@dataclass(frozen=True)
class TradeContractEvidence:
    agreement_id: str
    league_id: str
    player_id: str
    sleeper_player_id: str
    player_name: str
    canonical_team_id: str
    team_name: str
    agreement_status: str
    contract_operational_season: int
    operational_salary: Decimal | None
    years_remaining: int
    expiration_season: int
    future_contract_schedule: tuple[HistoricalContractSeasonRead, ...]
    future_2027_salary: Decimal
    source_legacy_contract_id: str | None
    contract_type: str | None
    roster_team_id: str | None
    roster_classification: str
    provenance: Mapping[str, object]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradePackageContractImpact:
    outgoing_salary: Decimal
    incoming_salary: Decimal
    outgoing_years_profile: tuple[int, ...]
    incoming_years_profile: tuple[int, ...]
    outgoing_future_commitments: Mapping[int, Decimal]
    incoming_future_commitments: Mapping[int, Decimal]
    warnings: tuple[str, ...]

