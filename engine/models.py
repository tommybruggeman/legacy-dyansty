from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class PlayerAsset:
    player: str
    owner: str
    pos: str | None

    salary: float = 0.0
    years: int = 0

    trade_value_score: float = 50.0
    contract_value_score: float = 50.0
    contract_risk_score: float = 0.0
    asset_score: float = 50.0

    recommendation: str = "DEPTH / MONITOR"
    reason: str = ""


@dataclass
class TeamSummary:
    owner: str

    roster_size: int = 0
    cap_used: float = 0.0
    cap_remaining: float = 0.0

    qb_count: int = 0
    rb_count: int = 0
    wr_count: int = 0
    te_count: int = 0

    expiring_contracts: int = 0
    missing_contracts: int = 0

    avg_asset_score: float = 0.0
    top_asset_score: float = 0.0
    starter_score: float = 0.0
    depth_score: float = 0.0

    position_counts: Dict[str, int] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)

    window: str = "Unknown"
    summary: str = ""


@dataclass
class LeagueSummary:
    teams: List[TeamSummary] = field(default_factory=list)

    best_team: str | None = None
    weakest_team: str | None = None
    deepest_team: str | None = None
    most_cap_flexible_team: str | None = None
