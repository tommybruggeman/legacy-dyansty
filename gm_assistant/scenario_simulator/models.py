from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


SCENARIO_SIMULATOR_VERSION = "scenario_simulator.v1"


class ScenarioActionType(str, Enum):
    RELEASE_PLAYER = "release_player"
    TRADE_PLAYER_OUT = "trade_player_out"
    TRADE_PLAYER_IN = "trade_player_in"
    TRADE_PICK_OUT = "trade_pick_out"
    TRADE_PICK_IN = "trade_pick_in"
    MOVE_PLAYER_TO_TAXI = "move_player_to_taxi"
    MOVE_PLAYER_TO_IR = "move_player_to_ir"
    DRAFT_PLAYER = "draft_player"


class ScenarioStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class ScenarioValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class ScenarioLineage:
    source_name: str
    league_id: str
    league_team_id: str
    read_only: bool = True


@dataclass(frozen=True)
class ScenarioAction:
    action_type: str
    player_id: str | None = None
    player_name: str | None = None
    position: str | None = None
    salary: float | None = None
    years_remaining: int | None = None
    season: int | None = None
    round: int | None = None
    pick_label: str | None = None
    original_team_id: str | None = None
    target_team_id: str | None = None


@dataclass(frozen=True)
class ReleasePlayer(ScenarioAction):
    action_type: str = ScenarioActionType.RELEASE_PLAYER.value


@dataclass(frozen=True)
class TradePlayerOut(ScenarioAction):
    action_type: str = ScenarioActionType.TRADE_PLAYER_OUT.value


@dataclass(frozen=True)
class TradePlayerIn(ScenarioAction):
    action_type: str = ScenarioActionType.TRADE_PLAYER_IN.value


@dataclass(frozen=True)
class TradePickOut(ScenarioAction):
    action_type: str = ScenarioActionType.TRADE_PICK_OUT.value


@dataclass(frozen=True)
class TradePickIn(ScenarioAction):
    action_type: str = ScenarioActionType.TRADE_PICK_IN.value


@dataclass(frozen=True)
class MovePlayerToTaxi(ScenarioAction):
    action_type: str = ScenarioActionType.MOVE_PLAYER_TO_TAXI.value


@dataclass(frozen=True)
class MovePlayerToIR(ScenarioAction):
    action_type: str = ScenarioActionType.MOVE_PLAYER_TO_IR.value


@dataclass(frozen=True)
class DraftPlayer(ScenarioAction):
    action_type: str = ScenarioActionType.DRAFT_PLAYER.value


@dataclass(frozen=True)
class RosterEntry:
    player_id: str
    player_name: str
    position: str | None = None
    status: str = "active"
    league_team_id: str | None = None
    is_rookie: bool | None = None


@dataclass(frozen=True)
class ContractEntry:
    player_id: str
    player_name: str
    salary: float | None = None
    years_remaining: int | None = None
    status: str | None = None
    is_rookie: bool | None = None


@dataclass(frozen=True)
class DraftPickEntry:
    pick_id: str | None
    season: int | None
    round: int | None
    slot: int | None = None
    label: str | None = None
    original_team_id: str | None = None
    current_owner_team_id: str | None = None


@dataclass(frozen=True)
class CapState:
    season: int
    salary_cap: float | None
    active_salary: float | None
    dead_cap: float | None
    adjustment_total: float | None
    available_cap: float | None


@dataclass
class FranchiseState:
    league_id: str
    league_team_id: str
    season: int
    team_name: str | None
    owner_name: str | None
    roster: list[RosterEntry] = field(default_factory=list)
    contracts: dict[str, ContractEntry] = field(default_factory=dict)
    cap: CapState | None = None
    draft_picks: list[DraftPickEntry] = field(default_factory=list)
    completeness: str = "complete"
    warnings: list[str] = field(default_factory=list)
    lineage: list[ScenarioLineage] = field(default_factory=list)


@dataclass(frozen=True)
class RosterDelta:
    added: list[RosterEntry] = field(default_factory=list)
    removed: list[RosterEntry] = field(default_factory=list)
    before_count: int = 0
    after_count: int = 0


@dataclass(frozen=True)
class CapDelta:
    before_available_cap: float | None = None
    after_available_cap: float | None = None
    active_salary_delta: float | None = None
    dead_cap_delta: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DraftCapitalDelta:
    picks_added: list[DraftPickEntry] = field(default_factory=list)
    picks_removed: list[DraftPickEntry] = field(default_factory=list)


@dataclass(frozen=True)
class DesignationDelta:
    moved_to_taxi: list[RosterEntry] = field(default_factory=list)
    moved_to_ir: list[RosterEntry] = field(default_factory=list)


@dataclass(frozen=True)
class RuleValidationResult:
    rule_type: str
    status: str
    explanation: str


@dataclass(frozen=True)
class ScenarioSimulationResult:
    scenario_id: str
    status: str
    current_state: FranchiseState
    simulated_state: FranchiseState
    applied_actions: list[ScenarioAction] = field(default_factory=list)
    rejected_actions: list[ScenarioAction] = field(default_factory=list)
    roster_delta: RosterDelta = field(default_factory=RosterDelta)
    cap_delta: CapDelta = field(default_factory=CapDelta)
    draft_capital_delta: DraftCapitalDelta = field(default_factory=DraftCapitalDelta)
    designation_delta: DesignationDelta = field(default_factory=DesignationDelta)
    rule_validation_results: list[RuleValidationResult] = field(default_factory=list)
    legality_status: str = ScenarioValidationStatus.VALID.value
    completeness: str = "complete"
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    failure_code: str | None = None
    simulator_version: str = SCENARIO_SIMULATOR_VERSION

    def to_evidence_row(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["id"] = self.scenario_id
        payload["transaction_type"] = "scenario_simulation"
        payload["team_ids"] = [self.current_state.league_team_id]
        payload["player_ids"] = [
            player.player_id
            for player in [*self.roster_delta.added, *self.roster_delta.removed, *self.designation_delta.moved_to_taxi, *self.designation_delta.moved_to_ir]
            if player.player_id
        ]
        payload["pick_ids"] = [
            pick.pick_id or pick.label
            for pick in [*self.draft_capital_delta.picks_added, *self.draft_capital_delta.picks_removed]
            if pick.pick_id or pick.label
        ]
        payload["summary"] = self.summary()
        return payload

    def summary(self) -> str:
        if self.status == ScenarioStatus.BLOCKED.value:
            reason = self.conflicts[0] if self.conflicts else self.failure_code or "scenario could not be simulated"
            return f"Scenario blocked: {reason}."
        pieces: list[str] = []
        if self.roster_delta.removed:
            names = ", ".join(player.player_name for player in self.roster_delta.removed)
            pieces.append(f"removes {names}")
        if self.roster_delta.added:
            names = ", ".join(player.player_name for player in self.roster_delta.added)
            pieces.append(f"adds {names}")
        if self.draft_capital_delta.picks_removed:
            labels = ", ".join(pick.label or pick.pick_id or "draft pick" for pick in self.draft_capital_delta.picks_removed)
            pieces.append(f"moves out {labels}")
        if self.draft_capital_delta.picks_added:
            labels = ", ".join(pick.label or pick.pick_id or "draft pick" for pick in self.draft_capital_delta.picks_added)
            pieces.append(f"adds {labels}")
        if self.cap_delta.after_available_cap is not None:
            pieces.append(f"projects available cap at {self.cap_delta.after_available_cap:.1f}")
        if not pieces:
            pieces.append("does not change the verified roster, cap, or draft capital")
        return "This read-only scenario " + "; ".join(pieces) + "."
