from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.free_agents import RookieRow


def rookie_draft_player_options(rows: Sequence[RookieRow]) -> tuple[str, ...]:
    """Format the exact canonical Free Agent Rookie Class population."""
    return tuple(
        f"{row.player} — {row.position} ({row.sleeper_player_id})"
        for row in rows
    )


def taxi_eligible_player_names(
    roster_rows: Sequence[Mapping[str, Any]],
    draft_assignments: Sequence[Mapping[str, Any]],
    *,
    league_team_id: str,
    draft_year: int | None = None,
) -> tuple[str, ...]:
    """Return rostered players whose canonical acquisition provenance is a rookie draft."""
    drafted = {
        str(row.get("player_id") or "")
        for row in draft_assignments
        if str(row.get("original_league_team_id") or row.get("league_team_id") or "") == str(league_team_id)
        and bool(row.get("rookie_contract_provenance"))
        and (draft_year is None or int(row.get("draft_year") or 0) == int(draft_year))
    }
    names = {
        str(row.get("player") or row.get("player_name") or "").strip()
        for row in roster_rows
        if str(row.get("sleeper_player_id") or row.get("player_id") or "") in drafted
        and str(row.get("player") or row.get("player_name") or "").strip()
    }
    return tuple(sorted(names))


class OffseasonTransactionService:
    """Thin client for atomic authenticated canonical offseason writes."""

    def __init__(self, client: Any, league_id: str):
        self.client = client
        self.league_id = str(league_id)

    def acquire(self, *, player_id: str, league_team_id: str, season: int,
                salary: float, years: int, acquisition_type: str,
                idempotency_key: str, notes: str = "") -> Mapping[str, Any]:
        request = {
            "league_id": self.league_id, "player_id": str(player_id),
            "league_team_id": str(league_team_id), "season": int(season),
            "salary": float(salary), "years": int(years),
            "acquisition_type": acquisition_type, "idempotency_key": idempotency_key,
            "notes": notes,
        }
        return self.client.rpc("acquire_offseason_player_authenticated", {"p_request": request}).execute().data

    def release(self, *, player_id: str, league_team_id: str, season: int,
                dead_cap: float, idempotency_key: str, notes: str = "") -> Mapping[str, Any]:
        request = {
            "league_id": self.league_id, "player_id": str(player_id),
            "league_team_id": str(league_team_id), "season": int(season),
            "dead_cap": float(dead_cap), "idempotency_key": idempotency_key,
            "notes": notes,
        }
        return self.client.rpc("release_offseason_player_authenticated", {"p_request": request}).execute().data
