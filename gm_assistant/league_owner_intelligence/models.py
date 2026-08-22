from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TransactionActionCategory(str, Enum):
    TRADE_PLAYER_IN = "trade_player_in"
    TRADE_PLAYER_OUT = "trade_player_out"
    TRADE_PICK_IN = "trade_pick_in"
    TRADE_PICK_OUT = "trade_pick_out"
    FREE_AGENT_ADD = "free_agent_add"
    PLAYER_RELEASE = "player_release"
    DRAFT_SELECTION = "draft_selection"
    CONTRACT_EXTENSION = "contract_extension"
    CONTRACT_CHANGE = "contract_change"
    TAXI_ADD = "taxi_add"
    TAXI_REMOVE = "taxi_remove"
    IR_ADD = "ir_add"
    IR_REMOVE = "ir_remove"
    OTHER_VERIFIED_TRANSACTION = "other_verified_transaction"
    UNSUPPORTED = "unsupported"


class BehavioralTendencyType(str, Enum):
    ACTIVE_TRADER = "active_trader"
    LOW_OBSERVED_TRADE_ACTIVITY = "low_observed_trade_activity"
    NET_PICK_ACQUIRER = "net_pick_acquirer"
    NET_PICK_SELLER = "net_pick_seller"
    FUTURE_FIRST_ACQUIRER = "future_first_acquirer"
    FUTURE_FIRST_SELLER = "future_first_seller"
    VETERAN_ACQUISITION_PATTERN = "veteran_acquisition_pattern"
    YOUTH_ACQUISITION_PATTERN = "youth_acquisition_pattern"
    CAP_CLEARING_PATTERN = "cap_clearing_pattern"
    FREQUENT_FREE_AGENT_ACTIVITY = "frequent_free_agent_activity"


@dataclass(frozen=True)
class LeagueOwnerLineage:
    domain: str
    source_name: str
    scope: str
    league_id: str | None = None
    league_team_id: str | None = None
    transaction_id: str | None = None
    status: str = "found"


@dataclass(frozen=True)
class LeagueTeamIdentity:
    league_id: str
    league_team_id: str
    team_name: str | None = None
    owner_name: str | None = None
    user_id: str | None = None
    sleeper_roster_id: str | None = None
    sleeper_owner_id: str | None = None
    sleeper_team_name: str | None = None
    aliases: tuple[str, ...] = ()
    lineage: list[LeagueOwnerLineage] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.team_name or self.owner_name or self.league_team_id


@dataclass(frozen=True)
class TeamReferenceResolution:
    status: str
    league_team_id: str | None = None
    matched_by: str | None = None
    candidates: list[str] = field(default_factory=list)
    warning: str | None = None


@dataclass(frozen=True)
class ObservedTransaction:
    league_id: str
    transaction_id: str | None
    season: int | None
    occurred_at: str | None
    involved_league_team_ids: list[str]
    action_category: str
    player_id: str | None = None
    player_name: str | None = None
    draft_pick_identity: str | None = None
    salary_or_cap_effect: float | None = None
    source_record_type: str = "unknown"
    source_name: str = "unknown"
    completeness: str = "partial"
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    lineage: list[LeagueOwnerLineage] = field(default_factory=list)


@dataclass(frozen=True)
class ObservationWindow:
    first_recorded_transaction: str | None
    last_recorded_transaction: str | None
    seasons_included: list[int]
    transaction_count: int
    history_state: str
    source_complete: bool | None


@dataclass(frozen=True)
class TeamCurrentStateSummary:
    league_team_id: str
    roster_count: int | None = None
    positional_counts: dict[str, int] = field(default_factory=dict)
    contract_count: int | None = None
    committed_salary: float | None = None
    available_cap: float | None = None
    future_pick_counts_by_round: dict[str, int] = field(default_factory=dict)
    exact_picks: list[str] = field(default_factory=list)
    taxi_count: int | None = None
    ir_count: int | None = None
    expiring_contract_count: int | None = None
    lineage: list[LeagueOwnerLineage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TeamAssetMovement:
    players_acquired_by_trade: int = 0
    players_sent_by_trade: int = 0
    picks_acquired: int = 0
    picks_sent: int = 0
    future_firsts_acquired: int = 0
    future_firsts_sent: int = 0
    free_agent_additions: int = 0
    player_releases: int = 0
    draft_selections: int = 0
    salary_acquired: float | None = None
    salary_sent: float | None = None
    cap_cleared: float | None = None


@dataclass(frozen=True)
class TeamActivitySummary:
    league_team_id: str
    completed_trades: int = 0
    distinct_trade_partner_ids: list[str] = field(default_factory=list)
    trades_with_authenticated_team: int = 0
    asset_movement: TeamAssetMovement = field(default_factory=TeamAssetMovement)
    transaction_count: int = 0
    observation_window: ObservationWindow | None = None
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TeamBehavioralTendency:
    tendency_type: str
    league_team_id: str
    evidence_count: int
    threshold_used: str
    observation_window: ObservationWindow
    source_facts: dict[str, Any]
    confidence_band: str = "bounded"
    minimum_evidence_met: bool = True
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TradePartnerHistory:
    team_a_id: str
    team_b_id: str
    verified_trade_count: int
    transaction_ids: list[str] = field(default_factory=list)
    seasons: list[int] = field(default_factory=list)
    most_recent_trade_at: str | None = None
    summaries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LeagueOwnerProfile:
    identity: LeagueTeamIdentity
    current_state: TeamCurrentStateSummary
    activity_summary: TeamActivitySummary
    tendencies: list[TeamBehavioralTendency] = field(default_factory=list)
    trade_partner_history: list[TradePartnerHistory] = field(default_factory=list)
    completeness: dict[str, str] = field(default_factory=dict)
    lineage: list[LeagueOwnerLineage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def to_evidence_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LeagueOwnerIntelligenceContext:
    league_id: str | None
    requesting_user_id: str | None
    requesting_league_team_id: str | None
    profiles: list[LeagueOwnerProfile] = field(default_factory=list)
    observed_transactions: list[ObservedTransaction] = field(default_factory=list)
    observation_window: ObservationWindow | None = None
    completeness: dict[str, str] = field(default_factory=dict)
    lineage: list[LeagueOwnerLineage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    availability: str = "available"

    def profile_for_team(self, league_team_id: str | None) -> LeagueOwnerProfile | None:
        if not league_team_id:
            return None
        for profile in self.profiles:
            if profile.identity.league_team_id == league_team_id:
                return profile
        return None

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)
