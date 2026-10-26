"""Rookie draft board: pick order, pick ownership and who is on the clock.

Pure functions so the order logic can be tested without Streamlit or
Supabase. The Settings page feeds them standings rows, league teams, the
canonical draft_pick_assets rows and the picks already made.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

ROUNDS = 3

# Standings page order, best first: Standing Points, then PF, Wins, Top 5.
STANDINGS_SORT = ("Standing Points", "PF", "Wins", "Top 5")


@dataclass(frozen=True)
class DraftSlot:
    round: int
    pick: int
    overall: int
    owner_team_id: str
    owner_name: str
    original_team_id: str
    original_name: str
    salary: float
    years: int
    option_salary: float
    player_id: str | None = None
    player_name: str | None = None
    position: str | None = None

    @property
    def label(self) -> str:
        return f"{self.round}.{self.pick:02d}"

    @property
    def traded(self) -> bool:
        return self.owner_team_id != self.original_team_id

    @property
    def made(self) -> bool:
        return bool(self.player_id)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _roster_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def owner_label(team: Mapping[str, Any]) -> str:
    return str(team.get("owner_name") or team.get("team_name") or "Unknown").strip()


def short_names(teams: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """First names for the board, full names where two owners share one."""
    full = {str(t.get("id")): owner_label(t) for t in teams if t.get("id")}
    firsts: dict[str, int] = {}
    for name in full.values():
        first = name.split()[0] if name.split() else name
        firsts[first] = firsts.get(first, 0) + 1
    out = {}
    for team_id, name in full.items():
        first = name.split()[0] if name.split() else name
        out[team_id] = first if firsts[first] == 1 else name
    return out


def draft_order(
    standings_rows: Iterable[Mapping[str, Any]],
    teams: Sequence[Mapping[str, Any]],
) -> list[str]:
    """League team ids, worst team first (reverse of the Standings page).

    Standings rows are matched to teams by Sleeper roster id. Teams missing
    from standings go last, in owner-name order, so the board always has
    every team.
    """
    by_roster = {
        _roster_id(t.get("sleeper_roster_id")): str(t.get("id"))
        for t in teams
        if t.get("id") and _roster_id(t.get("sleeper_roster_id")) is not None
    }
    ranked = []
    for row in standings_rows:
        team_id = by_roster.get(_roster_id(row.get("sleeper_roster_id")))
        if team_id and team_id not in {r[1] for r in ranked}:
            ranked.append((tuple(_num(row.get(col)) for col in STANDINGS_SORT), team_id))
    # Python's sort is stable, so ties keep standings order.
    ranked.sort(key=lambda item: item[0])
    order = [team_id for _, team_id in ranked]
    rest = sorted(
        (t for t in teams if t.get("id") and str(t.get("id")) not in order),
        key=owner_label,
    )
    return order + [str(t.get("id")) for t in rest]


def build_slots(
    *,
    draft_year: int,
    order: Sequence[str],
    teams: Sequence[Mapping[str, Any]],
    pick_assets: Iterable[Mapping[str, Any]],
    made_picks: Iterable[Mapping[str, Any]],
    players: Mapping[str, Mapping[str, Any]],
    terms: Callable[[int, int], tuple[float, int, float]],
    rounds: int = ROUNDS,
) -> list[DraftSlot]:
    """Every pick of the draft, with its current owner and any player taken.

    ``pick_assets`` are draft_pick_assets rows (or the canonical team-state
    ``draft_picks`` rows); a pick with no asset row stays with its original
    team. ``made_picks`` are rookie_draft_board_assignments rows.
    """
    names = short_names(teams)
    owner_of: dict[tuple[int, str], str] = {}
    for asset in pick_assets:
        if int(asset.get("draft_year") or 0) != int(draft_year):
            continue
        original = str(asset.get("original_league_team_id") or "")
        current = str(asset.get("current_owner_league_team_id") or "")
        if original and current:
            owner_of[(int(asset.get("round_number") or 0), original)] = current

    made: dict[tuple[int, int], Mapping[str, Any]] = {}
    for row in made_picks:
        if int(row.get("draft_year") or draft_year) != int(draft_year):
            continue
        made[(int(row.get("draft_round") or 0), int(row.get("round_pick") or 0))] = row

    team_count = len(order)
    slots = []
    for rnd in range(1, rounds + 1):
        for index, original in enumerate(order, start=1):
            owner = owner_of.get((rnd, original), original)
            taken = made.get((rnd, index))
            if taken and taken.get("original_league_team_id"):
                # The saved pick records who actually drafted.
                owner = str(taken.get("original_league_team_id"))
            salary, years, option_salary = terms(rnd, index)
            player_id = str(taken.get("player_id")) if taken else None
            info = players.get(player_id or "", {})
            slots.append(DraftSlot(
                round=rnd,
                pick=index,
                overall=(rnd - 1) * team_count + index,
                owner_team_id=owner,
                owner_name=names.get(owner, "Unknown"),
                original_team_id=original,
                original_name=names.get(original, "Unknown"),
                salary=float(salary),
                years=int(years),
                option_salary=float(option_salary),
                player_id=player_id,
                player_name=(info.get("player") or player_id) if taken else None,
                position=info.get("position") if taken else None,
            ))
    return slots


def on_the_clock(slots: Sequence[DraftSlot]) -> DraftSlot | None:
    return next((slot for slot in slots if not slot.made), None)


def slot_payload(slot: DraftSlot) -> dict[str, Any]:
    """JSON-safe dict for the draft room component."""
    return {
        "round": slot.round,
        "pick": slot.pick,
        "label": slot.label,
        "owner": slot.owner_name,
        "owner_id": slot.owner_team_id,
        "via": slot.original_name if slot.traded else None,
        "salary": slot.salary,
        "years": slot.years,
        "option": slot.option_salary,
        "player_id": slot.player_id,
        "player": slot.player_name,
        "pos": slot.position,
    }
