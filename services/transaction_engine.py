from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


RosterStatus = Literal["Active", "IR", "Taxi"]


@dataclass
class LeagueRules:
    max_roster_size: int = 22
    max_ir: int = 1
    max_taxi: int = 1
    taxi_cap_multiplier: float = 2 / 3
    ir_cap_multiplier: float = 1 / 2


@dataclass
class Contract:
    player_id: str
    player_name: str
    team_id: str | None
    salary: float
    years_left: int
    status: RosterStatus = "Active"
    rookie: bool = False
    active: bool = True


@dataclass
class TeamCapState:
    team_id: str
    faab_remaining: float
    active_cap: float = 0
    dead_cap: float = 0
    roster_count: int = 0
    ir_count: int = 0
    taxi_count: int = 0


class TransactionEngineError(Exception):
    pass


class TransactionEngine:
    def __init__(self, rules: LeagueRules | None = None):
        self.rules = rules or LeagueRules()

    def cap_hit_for_contract(self, contract: Contract) -> float:
        if not contract.active:
            return 0

        if contract.status == "Taxi":
            return round(contract.salary * self.rules.taxi_cap_multiplier, 2)

        if contract.status == "IR":
            return round(contract.salary * self.rules.ir_cap_multiplier, 2)

        return round(contract.salary, 2)

    def validate_roster_limits(self, team: TeamCapState) -> None:
        if team.roster_count > self.rules.max_roster_size:
            raise TransactionEngineError(
                f"Roster limit exceeded: {team.roster_count}/{self.rules.max_roster_size}"
            )

        if team.ir_count > self.rules.max_ir:
            raise TransactionEngineError(
                f"IR limit exceeded: {team.ir_count}/{self.rules.max_ir}"
            )

        if team.taxi_count > self.rules.max_taxi:
            raise TransactionEngineError(
                f"Taxi limit exceeded: {team.taxi_count}/{self.rules.max_taxi}"
            )

    def validate_taxi_eligibility(self, contract: Contract) -> None:
        if contract.status == "Taxi" and not contract.rookie:
            raise TransactionEngineError(
                f"{contract.player_name} is not eligible for Taxi because he is not marked rookie."
            )

    def apply_faab_bid(self, team: TeamCapState, bid: float) -> TeamCapState:
        if bid < 0:
            raise TransactionEngineError("FAAB bid cannot be negative.")

        if bid > team.faab_remaining:
            raise TransactionEngineError(
                f"FAAB bid ${bid} exceeds remaining FAAB ${team.faab_remaining}."
            )

        team.faab_remaining = round(team.faab_remaining - bid, 2)
        return team

    def drop_contract(self, contract: Contract) -> tuple[Contract, float]:
        """
        V1 dead cap assumption:
        Dropped player creates dead cap equal to current-year salary.
        """

        dead_cap_hit = self.cap_hit_for_contract(contract)

        contract.team_id = None
        contract.active = False
        contract.status = "Active"

        return contract, dead_cap_hit

    def add_contract_to_team(
        self,
        contract: Contract,
        team: TeamCapState,
        status: RosterStatus = "Active",
    ) -> tuple[Contract, TeamCapState]:

        contract.team_id = team.team_id
        contract.status = status
        contract.active = True

        self.validate_taxi_eligibility(contract)

        team.roster_count += 1

        if status == "IR":
            team.ir_count += 1
        elif status == "Taxi":
            team.taxi_count += 1

        team.active_cap = round(
            team.active_cap + self.cap_hit_for_contract(contract),
            2,
        )

        self.validate_roster_limits(team)

        return contract, team

    def process_add_drop(
        self,
        added_contract: Contract,
        dropped_contract: Contract | None,
        team: TeamCapState,
        waiver_bid: float = 0,
    ) -> dict[str, Any]:

        events: list[dict[str, Any]] = []

        team = self.apply_faab_bid(team, waiver_bid)

        if dropped_contract:
            dropped_contract, dead_cap_hit = self.drop_contract(
                dropped_contract
            )

            team.dead_cap = round(
                team.dead_cap + dead_cap_hit,
                2,
            )

            team.roster_count = max(team.roster_count - 1, 0)

            events.append({
                "event": "drop",
                "player_id": dropped_contract.player_id,
                "player_name": dropped_contract.player_name,
                "dead_cap_added": dead_cap_hit,
            })

        added_contract, team = self.add_contract_to_team(
            added_contract,
            team,
            status="Active",
        )

        events.append({
            "event": "add",
            "player_id": added_contract.player_id,
            "player_name": added_contract.player_name,
            "waiver_bid": waiver_bid,
        })

        return {
            "ok": True,
            "transaction_type": "add_drop",
            "team": team,
            "added_contract": added_contract,
            "dropped_contract": dropped_contract,
            "events": events,
        }

    def process_trade(
        self,
        outgoing_contracts: list[Contract],
        incoming_contracts: list[Contract],
        from_team: TeamCapState,
        to_team: TeamCapState,
        cash_from_team_to_to_team: float = 0,
    ) -> dict[str, Any]:

        events: list[dict[str, Any]] = []

        if cash_from_team_to_to_team:
            if cash_from_team_to_to_team > from_team.faab_remaining:
                raise TransactionEngineError(
                    "Trade cash exceeds sender budget."
                )

            from_team.faab_remaining = round(
                from_team.faab_remaining - cash_from_team_to_to_team,
                2,
            )

            to_team.faab_remaining = round(
                to_team.faab_remaining + cash_from_team_to_to_team,
                2,
            )

        for c in outgoing_contracts:
            c.team_id = to_team.team_id

            events.append({
                "event": "trade_player",
                "player_id": c.player_id,
                "player_name": c.player_name,
                "from_team": from_team.team_id,
                "to_team": to_team.team_id,
            })

        for c in incoming_contracts:
            c.team_id = from_team.team_id

            events.append({
                "event": "trade_player",
                "player_id": c.player_id,
                "player_name": c.player_name,
                "from_team": to_team.team_id,
                "to_team": from_team.team_id,
            })

        return {
            "ok": True,
            "transaction_type": "trade",
            "from_team": from_team,
            "to_team": to_team,
            "events": events,
        }
