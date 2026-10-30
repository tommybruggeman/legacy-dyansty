"""Lineups set in the app: which slots the league starts, who can play each one, and who is locked.

Saved through set_lineup_authenticated (supabase/migrations/20261212_app_lineups.sql), which checks the same rules:
the player is yours and not on taxi or IR, he fits the slot, and nobody moves in or out of the lineup once his game
has kicked off. Only on while the league's switch (league_app_transactions.enabled) is on.

A week with no saved lineup carries the latest saved one forward; before the first save it starts from Sleeper's.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

NOT_STARTERS = {"BN", "TAXI", "IR"}
FITS = {
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
}
LABELS = {"FLEX": "FLX", "SUPER_FLEX": "OP", "REC_FLEX": "W/T", "WRRB_FLEX": "W/R"}
ET = ZoneInfo("America/New_York")

ERRORS = {
    "transactions_not_enabled": "Lineups in the app turn on for the playoffs.",
    "not_a_team_owner": "Your account isn't linked to a team in this league.",
    "lineup_week_invalid": "That week can't be set.",
    "lineup_week_closed": "Every game this week has started.",
    "lineup_slots_missing": "The league's lineup slots aren't saved yet. The commissioner can save them in Settings.",
    "lineup_shape_invalid": "The lineup didn't match the league's slots. Pull down to refresh.",
    "lineup_duplicate": "A player can only start once.",
    "lineup_player_not_yours": "That player isn't on your roster anymore.",
    "lineup_player_inactive": "Taxi and IR players can't start.",
    "lineup_slot_mismatch": "That player can't play that slot.",
    "lineup_player_locked": "His game has already started, so he's locked for this week.",
}


def friendly(exc: Exception) -> str:
    text = str(exc)
    for code, message in ERRORS.items():
        if code in text:
            return message
    return "The lineup wasn't saved. Try again."


def starter_slots(roster_positions: Iterable[str]) -> list[str]:
    return [str(s) for s in roster_positions or [] if str(s) not in NOT_STARTERS]


def fits(slot: str, pos: str) -> bool:
    return pos in FITS.get(slot, {slot})


def label(slot: str) -> str:
    return LABELS.get(slot, slot)


def kickoffs(games: Iterable[Mapping[str, Any]]) -> dict[str, datetime]:
    """NFL team -> that week's kickoff (UTC), from nfl_games rows (gameday + gametime are Eastern)."""
    out: dict[str, datetime] = {}
    for g in games:
        try:
            at = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=ET).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        for team in (g.get("home_team"), g.get("away_team")):
            if team:
                out[str(team)] = at
    return out


def is_locked(player_id: str, nfl_team: str | None, starts: Mapping[str, datetime], now: datetime) -> bool:
    at = starts.get(nfl_team or "") or starts.get(player_id)  # team defenses use the team code as their id
    return bool(at and at <= now)


def effective(slots: list[str], saved: list[Mapping[str, Any]], sleeper_starters: list[str], eligible: set[str]) -> list[str | None]:
    """This week's lineup as player ids in slot order (None = empty).

    saved: this team's league_lineups rows for weeks up to this one, newest first. Players who left the roster or
    went to taxi/IR since are dropped to an empty slot.
    """
    row = next((r for r in saved if [s.get("slot") for s in r.get("slots") or []] == slots), None)
    ids = [s.get("player_id") for s in row["slots"]] if row else list(sleeper_starters[: len(slots)])
    ids += [None] * (len(slots) - len(ids))
    out: list[str | None] = []
    for pid in ids:
        pid = str(pid) if pid else None
        out.append(pid if pid and pid in eligible and pid not in out else None)
    return out


def view(slots: list[str], ids: list[str | None], week: int, locked: set[str], enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "week": week,
        "slots": [{"index": i, "slot": s, "label": label(s), "player_id": ids[i], "locked": bool(ids[i] and ids[i] in locked)}
                  for i, s in enumerate(slots)],
        "locked": sorted(locked),
        "fits": {s: sorted(FITS.get(s, {s})) for s in dict.fromkeys(slots)},
    }
