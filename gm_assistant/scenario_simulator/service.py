from __future__ import annotations

from dataclasses import replace
from typing import Any

from gm_assistant.draft_intelligence import DraftIntelligenceService
from gm_assistant.repositories import CapRepository, ContractRepository, RosterRepository
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.scenario_simulator.models import (
    CapDelta,
    CapState,
    ContractEntry,
    DesignationDelta,
    DraftCapitalDelta,
    DraftPickEntry,
    FranchiseState,
    RosterDelta,
    RosterEntry,
    RuleValidationResult,
    ScenarioAction,
    ScenarioActionType,
    ScenarioLineage,
    ScenarioSimulationResult,
    ScenarioStatus,
    ScenarioValidationStatus,
)
from gm_assistant.scenario_simulator.normalization import normalize_player_name


RELEASE_DEAD_CAP_MULTIPLIER = 0.5


class ScenarioSimulatorService:
    """Read-only, deterministic scenario simulator for scoped assistant context."""

    def __init__(self, sb: Any):
        self.sb = sb

    def simulate(
        self,
        context: AssistantRequestContext,
        actions: list[ScenarioAction],
        *,
        scenario_id: str = "scenario-1",
        atomic: bool = True,
    ) -> ScenarioSimulationResult:
        current = self.load_state(context)
        simulated = replace(current, roster=list(current.roster), contracts=dict(current.contracts), draft_picks=list(current.draft_picks), warnings=list(current.warnings))
        warnings = list(current.warnings)
        conflicts: list[str] = []
        applied: list[ScenarioAction] = []
        rejected: list[ScenarioAction] = []
        removed_players: list[RosterEntry] = []
        added_players: list[RosterEntry] = []
        moved_to_taxi: list[RosterEntry] = []
        moved_to_ir: list[RosterEntry] = []
        picks_removed: list[DraftPickEntry] = []
        picks_added: list[DraftPickEntry] = []
        active_salary_delta = 0.0
        dead_cap_delta = 0.0
        cap_complete = current.cap is not None and current.cap.available_cap is not None

        for action in actions:
            outcome = self._apply_action(simulated, action)
            if outcome["conflict"]:
                rejected.append(action)
                conflicts.append(outcome["conflict"])
                if atomic:
                    return self._blocked_result(scenario_id, current, actions, rejected, conflicts)
                continue
            applied.append(action)
            removed_players.extend(outcome["removed"])
            added_players.extend(outcome["added"])
            moved_to_taxi.extend(outcome["taxi"])
            moved_to_ir.extend(outcome["ir"])
            picks_removed.extend(outcome["picks_removed"])
            picks_added.extend(outcome["picks_added"])
            active_salary_delta += outcome["active_salary_delta"]
            dead_cap_delta += outcome["dead_cap_delta"]
            warnings.extend(outcome["warnings"])
            cap_complete = cap_complete and not outcome["cap_incomplete"]

        simulated = self._with_projected_cap(simulated, active_salary_delta, dead_cap_delta, cap_complete)
        status = ScenarioStatus.SUCCESS.value if not warnings else ScenarioStatus.PARTIAL.value
        legality = ScenarioValidationStatus.VALID.value if not conflicts else ScenarioValidationStatus.INVALID.value
        completeness = "complete" if cap_complete else "partial"
        cap_delta = CapDelta(
            before_available_cap=current.cap.available_cap if current.cap else None,
            after_available_cap=simulated.cap.available_cap if simulated.cap else None,
            active_salary_delta=active_salary_delta,
            dead_cap_delta=dead_cap_delta,
            warnings=[warning for warning in warnings if "cap" in warning.lower()],
        )
        return ScenarioSimulationResult(
            scenario_id=scenario_id,
            status=status,
            current_state=current,
            simulated_state=simulated,
            applied_actions=applied,
            rejected_actions=rejected,
            roster_delta=RosterDelta(added_players, removed_players, len(current.roster), len(simulated.roster)),
            cap_delta=cap_delta,
            draft_capital_delta=DraftCapitalDelta(picks_added, picks_removed),
            designation_delta=DesignationDelta(moved_to_taxi, moved_to_ir),
            rule_validation_results=[RuleValidationResult("scenario_read_only", ScenarioValidationStatus.VALID.value, "Scenario was simulated without executing a roster, trade, draft, or database action.")],
            legality_status=legality,
            completeness=completeness,
            warnings=_dedupe(warnings),
            conflicts=conflicts,
        )

    def load_state(self, context: AssistantRequestContext) -> FranchiseState:
        roster_result = RosterRepository(self.sb).get_team_roster(context, league_team_id=context.league_team_id)
        contract_result = ContractRepository(self.sb).get_contracts(context, league_team_ids=[context.league_team_id])
        cap_result = CapRepository(self.sb).get_cap_summary(context, league_team_id=context.league_team_id)
        pick_assets = DraftIntelligenceService(self.sb).get_pick_assets(context)

        roster = [_roster_entry(row, context) for row in roster_result.rows]
        contracts = {
            contract.player_id: contract
            for contract in (_contract_entry(row) for row in contract_result.rows)
            if contract and contract.player_id
        }
        cap = _cap_state(cap_result.rows[0], context) if cap_result.rows else None
        picks = [
            DraftPickEntry(asset.canonical_pick_id, asset.season, asset.round, asset.exact_slot.overall_pick if asset.exact_slot else None, asset.pick_label, asset.original_team_id, asset.current_owner_team_id)
            for asset in pick_assets
            if asset.current_owner_team_id == context.league_team_id
        ]
        warnings = []
        if cap is None:
            warnings.append("cap_summary_unavailable")
        return FranchiseState(
            league_id=context.league_id,
            league_team_id=context.league_team_id,
            season=context.requested_season,
            team_name=context.team_name,
            owner_name=context.owner_name,
            roster=roster,
            contracts=contracts,
            cap=cap,
            draft_picks=picks,
            completeness="complete" if cap else "partial",
            warnings=warnings,
            lineage=[
                ScenarioLineage(roster_result.source.source_name, context.league_id, context.league_team_id),
                ScenarioLineage(contract_result.source.source_name, context.league_id, context.league_team_id),
                ScenarioLineage(cap_result.source.source_name, context.league_id, context.league_team_id),
                ScenarioLineage("draft_intelligence", context.league_id, context.league_team_id),
            ],
        )

    def _apply_action(self, state: FranchiseState, action: ScenarioAction) -> dict[str, Any]:
        outcome = _empty_outcome()
        kind = action.action_type
        if kind == ScenarioActionType.RELEASE_PLAYER.value:
            player, conflict = _resolve_rostered_player(state, action)
            if conflict:
                outcome["conflict"] = conflict
                return outcome
            _remove_player(state, player)
            outcome["removed"].append(player)
            salary_delta, dead_delta, warnings = _release_cap_delta(state, player)
            outcome["active_salary_delta"] += salary_delta
            outcome["dead_cap_delta"] += dead_delta
            outcome["warnings"].extend(warnings)
            outcome["cap_incomplete"] = bool(warnings)
            return outcome
        if kind == ScenarioActionType.TRADE_PLAYER_OUT.value:
            player, conflict = _resolve_rostered_player(state, action)
            if conflict:
                outcome["conflict"] = conflict
                return outcome
            _remove_player(state, player)
            outcome["removed"].append(player)
            salary = _contract_salary(state, player.player_id)
            if salary is None:
                outcome["warnings"].append(f"cap impact incomplete for {player.player_name}: contract salary unavailable")
                outcome["cap_incomplete"] = True
            else:
                outcome["active_salary_delta"] -= salary
            return outcome
        if kind == ScenarioActionType.TRADE_PLAYER_IN.value:
            player = RosterEntry(action.player_id or _generated_player_id(action.player_name), action.player_name or "Incoming player", action.position, "active", state.league_team_id)
            state.roster.append(player)
            outcome["added"].append(player)
            if action.salary is None:
                outcome["warnings"].append(f"cap impact incomplete for {player.player_name}: incoming salary unavailable")
                outcome["cap_incomplete"] = True
            else:
                outcome["active_salary_delta"] += float(action.salary)
            return outcome
        if kind == ScenarioActionType.TRADE_PICK_OUT.value:
            pick = _resolve_pick(state, action)
            if not pick:
                outcome["conflict"] = "Requested pick is not verified as owned by this team."
                return outcome
            state.draft_picks = [item for item in state.draft_picks if item != pick]
            outcome["picks_removed"].append(pick)
            return outcome
        if kind == ScenarioActionType.TRADE_PICK_IN.value:
            pick = DraftPickEntry(action.pick_label, action.season, action.round, None, action.pick_label, action.original_team_id, state.league_team_id)
            state.draft_picks.append(pick)
            outcome["picks_added"].append(pick)
            return outcome
        if kind == ScenarioActionType.MOVE_PLAYER_TO_TAXI.value:
            player, conflict = _resolve_rostered_player(state, action)
            if conflict:
                outcome["conflict"] = conflict
                return outcome
            contract = state.contracts.get(player.player_id)
            if contract and contract.is_rookie is False:
                outcome["conflict"] = f"{player.player_name} is not verified as taxi eligible."
                return outcome
            moved = replace(player, status="taxi")
            _replace_player(state, player, moved)
            outcome["taxi"].append(moved)
            return outcome
        if kind == ScenarioActionType.MOVE_PLAYER_TO_IR.value:
            player, conflict = _resolve_rostered_player(state, action)
            if conflict:
                outcome["conflict"] = conflict
                return outcome
            status = str(player.status or "").lower()
            if "injur" not in status and status not in {"ir", "ir_eligible", "out"}:
                outcome["conflict"] = f"{player.player_name} is not verified as IR eligible."
                return outcome
            moved = replace(player, status="ir")
            _replace_player(state, player, moved)
            outcome["ir"].append(moved)
            return outcome
        if kind == ScenarioActionType.DRAFT_PLAYER.value:
            pick = _resolve_pick(state, action)
            if not pick:
                outcome["conflict"] = "Draft simulation requires a verified owned pick."
                return outcome
            state.draft_picks = [item for item in state.draft_picks if item != pick]
            player = RosterEntry(action.player_id or _generated_player_id(action.player_name), action.player_name or "Drafted player", action.position, "active", state.league_team_id)
            state.roster.append(player)
            outcome["picks_removed"].append(pick)
            outcome["added"].append(player)
            outcome["warnings"].append(f"cap impact incomplete for {player.player_name}: rookie contract rules unavailable")
            outcome["cap_incomplete"] = True
            return outcome
        outcome["conflict"] = f"Unsupported scenario action: {kind}."
        return outcome

    def _with_projected_cap(self, state: FranchiseState, active_delta: float, dead_delta: float, cap_complete: bool) -> FranchiseState:
        if not state.cap:
            return state
        active = None if state.cap.active_salary is None else state.cap.active_salary + active_delta
        dead = None if state.cap.dead_cap is None else state.cap.dead_cap + dead_delta
        available = None
        if cap_complete and state.cap.available_cap is not None:
            available = state.cap.available_cap - active_delta - dead_delta
        return replace(state, cap=replace(state.cap, active_salary=active, dead_cap=dead, available_cap=available), completeness="complete" if cap_complete else "partial")

    def _blocked_result(self, scenario_id: str, current: FranchiseState, actions: list[ScenarioAction], rejected: list[ScenarioAction], conflicts: list[str]) -> ScenarioSimulationResult:
        return ScenarioSimulationResult(
            scenario_id=scenario_id,
            status=ScenarioStatus.BLOCKED.value,
            current_state=current,
            simulated_state=current,
            applied_actions=[],
            rejected_actions=rejected or actions,
            roster_delta=RosterDelta(before_count=len(current.roster), after_count=len(current.roster)),
            legality_status=ScenarioValidationStatus.INVALID.value,
            completeness="blocked",
            conflicts=conflicts,
            failure_code="scenario_validation_failed",
        )


def _empty_outcome() -> dict[str, Any]:
    return {"removed": [], "added": [], "taxi": [], "ir": [], "picks_removed": [], "picks_added": [], "warnings": [], "conflict": None, "active_salary_delta": 0.0, "dead_cap_delta": 0.0, "cap_incomplete": False}


def _resolve_rostered_player(state: FranchiseState, action: ScenarioAction) -> tuple[RosterEntry | None, str | None]:
    candidates = []
    if action.player_id:
        candidates = [player for player in state.roster if player.player_id == action.player_id]
    elif action.player_name:
        wanted = normalize_player_name(action.player_name)
        candidates = [player for player in state.roster if normalize_player_name(player.player_name) == wanted]
    if not candidates:
        return None, f"{action.player_name or action.player_id or 'Requested player'} is not verified on this roster."
    if len(candidates) > 1:
        return None, f"{action.player_name or action.player_id} matches multiple roster players."
    return candidates[0], None


def _remove_player(state: FranchiseState, player: RosterEntry) -> None:
    state.roster = [item for item in state.roster if item != player]


def _replace_player(state: FranchiseState, before: RosterEntry, after: RosterEntry) -> None:
    state.roster = [after if item == before else item for item in state.roster]


def _release_cap_delta(state: FranchiseState, player: RosterEntry) -> tuple[float, float, list[str]]:
    contract = state.contracts.get(player.player_id)
    if not contract or contract.salary is None or contract.years_remaining is None:
        return 0.0, 0.0, [f"cap impact incomplete for {player.player_name}: contract salary or years remaining unavailable"]
    salary = float(contract.salary)
    dead_cap = round(salary * RELEASE_DEAD_CAP_MULTIPLIER * int(contract.years_remaining), 2)
    return -salary, dead_cap, []


def _contract_salary(state: FranchiseState, player_id: str) -> float | None:
    contract = state.contracts.get(player_id)
    return None if not contract or contract.salary is None else float(contract.salary)


def _resolve_pick(state: FranchiseState, action: ScenarioAction) -> DraftPickEntry | None:
    for pick in state.draft_picks:
        if action.pick_label and action.pick_label in {pick.pick_id, pick.label}:
            return pick
        if action.season and action.round and pick.season == action.season and pick.round == action.round:
            return pick
    return None


def _roster_entry(row: dict[str, Any], context: AssistantRequestContext) -> RosterEntry:
    return RosterEntry(
        player_id=_clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id")) or _generated_player_id(row.get("player_name")),
        player_name=_clean_text(row.get("player_name") or row.get("name")) or "Unknown player",
        position=_clean_text(row.get("position") or row.get("player_position")),
        status=_clean_text(row.get("status") or row.get("roster_status")) or "active",
        league_team_id=_clean_id(row.get("league_team_id") or row.get("team_id")) or context.league_team_id,
        is_rookie=_safe_bool(_first_present(row.get("is_rookie"), row.get("rookie_draft_selected"))),
    )


def _contract_entry(row: dict[str, Any]) -> ContractEntry | None:
    player_id = _clean_id(row.get("sleeper_player_id") or row.get("sleeper_id") or row.get("player_id"))
    if not player_id:
        return None
    return ContractEntry(
        player_id=player_id,
        player_name=_clean_text(row.get("player_name") or row.get("name")) or player_id,
        salary=_safe_float(row.get("salary") or row.get("contract_salary")),
        years_remaining=_safe_int(row.get("contract_years_left") or row.get("years_remaining")),
        status=_clean_text(row.get("contract_status") or row.get("status")),
        is_rookie=_safe_bool(_first_present(row.get("is_rookie"), row.get("rookie_draft_selected"))),
    )


def _cap_state(row: dict[str, Any], context: AssistantRequestContext) -> CapState:
    return CapState(
        season=_safe_int(row.get("season")) or context.requested_season,
        salary_cap=_safe_float(row.get("salary_cap") or row.get("cap_limit")),
        active_salary=_safe_float(row.get("active_salary") or row.get("total_salary")),
        dead_cap=_safe_float(row.get("dead_cap") or row.get("dead_money")) or 0.0,
        adjustment_total=_safe_float(row.get("adjustment_total")),
        available_cap=_safe_float(row.get("available_cap") or row.get("cap_space")),
    )


def _clean_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_float(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return float(text)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return int(float(text))
    except Exception:
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "t", "1", "yes", "y"}:
        return True
    if text in {"false", "f", "0", "no", "n"}:
        return False
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _generated_player_id(name: Any) -> str:
    return f"unresolved:{normalize_player_name(name) or 'player'}"


def _dedupe(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out
