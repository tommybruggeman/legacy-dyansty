from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ContractBackfillPlan:
    league_id: str
    league_season_id: str
    source_season: int
    source_contract_count: int
    source_fingerprint: str
    idempotency_key: str
    agreements: tuple[dict[str, Any], ...] = ()
    contract_seasons: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    future_league_seasons: tuple[int, ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    blocking_errors: tuple[dict[str, Any], ...] = ()

    @property
    def safe_to_apply(self) -> bool: return not self.blocking_errors

    @property
    def counts(self) -> dict[str, int]:
        return {"agreements": len(self.agreements), "contract_seasons": len(self.contract_seasons),
                "events": len(self.events), "future_league_seasons": len(self.future_league_seasons)}

    def payload(self) -> dict[str, Any]:
        return {"league_id": self.league_id, "league_season_id": self.league_season_id,
                "source_season": self.source_season, "source_fingerprint": self.source_fingerprint,
                "idempotency_key": self.idempotency_key, "agreements": list(self.agreements),
                "contract_seasons": list(self.contract_seasons), "events": list(self.events),
                "future_league_seasons": list(self.future_league_seasons)}


def money(value: Any) -> Decimal:
    """Preserve Legacy cap-dollar units using fixed precision."""
    return Decimal(str(value)).quantize(Decimal("0.01"))
