from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence


def draft_season_window(active_season: int) -> tuple[int, int, int]:
    season = int(active_season)
    return season, season + 1, season + 2


def is_draft_pick_tradeable(lifecycle_status: str, asset_status: str) -> bool:
    return lifecycle_status in {"scheduled", "in_progress"} and asset_status == "tradable"


@dataclass(frozen=True)
class PlayerMovement:
    contract_id: str
    from_league_team_id: str
    to_league_team_id: str


@dataclass(frozen=True)
class DraftPickMovement:
    draft_pick_id: str
    from_league_team_id: str
    to_league_team_id: str


@dataclass(frozen=True)
class RetainedSeason:
    season: int
    amount: Decimal


@dataclass(frozen=True)
class RetainedSalary:
    contract_id: str
    retaining_league_team_id: str
    receiving_league_team_id: str
    seasons: tuple[RetainedSeason, ...]


@dataclass(frozen=True)
class DraftInventorySlot:
    round_number: int
    original_league_team_id: str
    current_owner_league_team_id: str
    provenance: Mapping[str, Any]
    rookie_draft_assignment_id: str | None = None


def team_selection_options(teams: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    options = []
    for team in teams:
        team_id = str(team.get("id") or "").strip()
        if not team_id:
            continue
        name = str(team.get("team_name") or team.get("owner_name") or "Team").strip()
        owner = str(team.get("owner_name") or "").strip()
        detail = f" · {owner}" if owner and owner != name else ""
        options.append((team_id, f"{name}{detail} · {team_id[:8]}"))
    return tuple(options)


def execute_canonical_trade(
    client: Any,
    *,
    league_id: str,
    participant_team_ids: Sequence[str],
    idempotency_key: str,
    player_movements: Sequence[PlayerMovement] = (),
    draft_pick_movements: Sequence[DraftPickMovement] = (),
    retained_salary: Sequence[RetainedSalary] = (),
    notes: str = "",
) -> Mapping[str, Any]:
    participants = tuple(dict.fromkeys(str(value).strip() for value in participant_team_ids if str(value).strip()))
    if len(participants) not in {2, 3, 4}:
        raise ValueError("A trade requires 2 to 4 distinct teams.")
    if not player_movements and not draft_pick_movements:
        raise ValueError("A trade requires at least one player or draft-pick movement.")
    if any(len(item.seasons) not in range(1, 5) for item in retained_salary):
        raise ValueError("Retained salary must cover between 1 and 4 seasons.")

    request = {
        "league_id": str(league_id),
        "participant_team_ids": list(participants),
        "idempotency_key": str(idempotency_key).strip(),
        "notes": str(notes).strip(),
        "player_movements": [vars(item) for item in player_movements],
        "draft_pick_movements": [vars(item) for item in draft_pick_movements],
        "retained_salary": [
            {
                "contract_id": item.contract_id,
                "retaining_league_team_id": item.retaining_league_team_id,
                "receiving_league_team_id": item.receiving_league_team_id,
                "seasons": [
                    {"season": season.season, "amount": str(season.amount)}
                    for season in item.seasons
                ],
            }
            for item in retained_salary
        ],
    }
    if not request["idempotency_key"]:
        raise ValueError("An idempotency key is required.")
    result = client.rpc("execute_canonical_trade_authenticated", {"p_request": request}).execute().data
    if not isinstance(result, Mapping) or result.get("status") != "completed":
        raise RuntimeError("Canonical trade execution returned an invalid result.")
    return dict(result)


def initialize_draft_inventory(
    client: Any,
    *,
    league_id: str,
    season: int,
    slots: Sequence[DraftInventorySlot],
) -> Mapping[str, Any]:
    request_slots = [{
        "round_number": int(slot.round_number),
        "original_league_team_id": str(slot.original_league_team_id),
        "current_owner_league_team_id": str(slot.current_owner_league_team_id),
        "rookie_draft_assignment_id": str(slot.rookie_draft_assignment_id or ""),
        "provenance": dict(slot.provenance),
    } for slot in slots]
    result = client.rpc("initialize_draft_inventory_authenticated", {"p_request": {
        "league_id": str(league_id),
        "season": int(season),
        "slots": request_slots,
    }}).execute().data
    if not isinstance(result, Mapping) or int(result.get("season", 0)) != int(season):
        raise RuntimeError("Draft inventory initialization returned an invalid result.")
    return dict(result)


def complete_rookie_draft(client: Any, *, league_id: str, season: int) -> Mapping[str, Any]:
    result = client.rpc(
        "complete_rookie_draft_authenticated",
        {"p_request": {"league_id": str(league_id), "season": int(season)}},
    ).execute().data
    if not isinstance(result, Mapping) or result.get("status") != "completed":
        raise RuntimeError("Draft completion returned an invalid result.")
    return dict(result)


def configure_draft_lifecycle(
    client: Any,
    *,
    league_id: str,
    season: int,
    status: str,
    expected_pick_count: int,
) -> Mapping[str, Any]:
    if status not in {"scheduled", "in_progress"}:
        raise ValueError("Draft lifecycle configuration must be scheduled or in progress.")
    result = client.rpc(
        "configure_draft_lifecycle_authenticated",
        {"p_request": {
            "league_id": str(league_id),
            "season": int(season),
            "status": status,
            "expected_pick_count": int(expected_pick_count),
        }},
    ).execute().data
    if not isinstance(result, Mapping) or result.get("status") != status:
        raise RuntimeError("Draft lifecycle configuration returned an invalid result.")
    return dict(result)
