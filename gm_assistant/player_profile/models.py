from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlayerProfile:

    sleeper_id: str
    player_name: str
    position: str

    # Identity
    age: float | None = None
    years_exp: int | None = None
    career_stage: str | None = None

    # Production
    expected_ppg: float | None = None
    historical_ppg: float | None = None

    # Contract
    salary: float | None = None
    years: int | None = None
    contract_score: float | None = None

    # Market
    market_score: float | None = None
    rookie_asset_score: float | None = None

    # Situation
    role_score: float | None = None
    situation_score: float | None = None
    opportunity_score: float | None = None

    # Future
    future_score: float | None = None

    # Asset
    asset_subtype: str | None = None
    market_pool: str | None = None

    # Summary
    summary: str | None = None
