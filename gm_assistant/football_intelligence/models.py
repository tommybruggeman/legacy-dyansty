from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FootballLineage:
    domain: str
    source_name: str
    scope: str
    league_id: str | None = None
    league_team_id: str | None = None
    player_id: str | None = None
    rule_id: str | None = None
    status: str = "found"


@dataclass(frozen=True)
class LineupRequirement:
    slot: str
    required_count: int
    eligible_player_positions: tuple[str, ...]
    source_key: str


@dataclass(frozen=True)
class LineupRulesProfile:
    availability: str
    starter_slots: tuple[LineupRequirement, ...] = ()
    bench_slots: int | None = None
    taxi_slots: int | None = None
    ir_slots: int | None = None
    warnings: tuple[str, ...] = ()
    lineage: tuple[FootballLineage, ...] = ()

    def required_by_position(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for requirement in self.starter_slots:
            if len(requirement.eligible_player_positions) == 1:
                pos = requirement.eligible_player_positions[0]
                out[pos] = out.get(pos, 0) + requirement.required_count
        return out


@dataclass(frozen=True)
class FootballPlayerSnapshot:
    player_id: str | None
    player_name: str
    position: str | None
    roster_status: str
    age: float | None = None
    experience: int | None = None
    is_rookie: bool | None = None
    salary: float | None = None
    contract_years_remaining: int | None = None
    value_tier: str | None = None
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionGroupProfile:
    position: str
    roster_count: int
    active_count: int
    taxi_count: int
    ir_count: int
    required_starters: int | None
    depth_above_required: int | None
    average_age: float | None
    median_age: float | None
    rookie_count: int
    veteran_count: int
    expiring_contract_count: int
    committed_salary: float | None
    salary_share: float | None
    players: tuple[FootballPlayerSnapshot, ...] = ()
    warnings: tuple[str, ...] = ()
    lineage: tuple[FootballLineage, ...] = ()


@dataclass(frozen=True)
class RosterStrength:
    rule_id: str
    label: str
    position: str | None
    explanation: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RosterNeed:
    rule_id: str
    label: str
    position: str | None
    severity: str
    explanation: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RosterRisk:
    rule_id: str
    label: str
    position: str | None
    severity: str
    explanation: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractExposureProfile:
    committed_salary: float | None
    expiring_contract_count: int
    expiring_salary: float | None
    position_salary_shares: dict[str, float] = field(default_factory=dict)
    highest_player_salary_share: float | None = None
    highest_player_salary_name: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgeCurveProfile:
    average_age: float | None
    median_age: float | None
    known_age_count: int
    missing_age_count: int
    veteran_count: int
    rookie_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftFlexibilityProfile:
    future_first_count: int
    future_second_count: int
    future_pick_count: int
    evaluated_seasons: tuple[int, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyFitDimension:
    dimension: str
    status: str
    explanation: str
    metric_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RosterConstructionProfile:
    roster_count: int
    active_roster_count: int
    taxi_count: int
    ir_count: int
    position_groups: tuple[PositionGroupProfile, ...] = ()
    lineup_rules: LineupRulesProfile | None = None
    contract_exposure: ContractExposureProfile | None = None
    age_curve: AgeCurveProfile | None = None
    draft_flexibility: DraftFlexibilityProfile | None = None
    strengths: tuple[RosterStrength, ...] = ()
    needs: tuple[RosterNeed, ...] = ()
    risks: tuple[RosterRisk, ...] = ()
    strategy_fit: tuple[StrategyFitDimension, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FootballIntelligenceContext:
    league_id: str
    league_team_id: str
    season: int
    availability: str
    roster_construction: RosterConstructionProfile | None = None
    owner_goal: str | None = None
    completeness: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    lineage: tuple[FootballLineage, ...] = ()

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)

    def group(self, position: str) -> PositionGroupProfile | None:
        if not self.roster_construction:
            return None
        wanted = position.upper()
        for group in self.roster_construction.position_groups:
            if group.position == wanted:
                return group
        return None
