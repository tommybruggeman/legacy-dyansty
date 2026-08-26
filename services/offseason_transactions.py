from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from services.free_agents import RookieRow


ROOKIE_SALARY_SCALE_BASE_CAP = Decimal("225")
_ROOKIE_BASE_SALARIES = {
    1: (15, 12, 9, 8, 6, 5, 4, 4, 4, 4),
    2: (3, 3, 3, 3, 3, 3, 3, 3, 3, 3),
    3: (1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
}


@dataclass(frozen=True)
class ContractTerms:
    salary: Decimal
    years: int
    option_salary: Decimal | None = None


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def scale_rookie_salary(base_salary: Any, current_cap: Any, base_cap: Any = ROOKIE_SALARY_SCALE_BASE_CAP) -> Decimal:
    denominator = Decimal(str(base_cap))
    numerator = Decimal(str(current_cap))
    if denominator <= 0 or numerator <= 0:
        raise ValueError("rookie salary scale cap authority is invalid")
    return _money(Decimal(str(base_salary)) * numerator / denominator)


def resolve_normal_free_agent_terms(rules: Mapping[str, Any]) -> ContractTerms:
    """Ordinary non-auction additions always receive the league floor for one year."""
    return ContractTerms(Decimal(str(rules.get("league_min_salary") or rules.get("default_fa_salary") or 1)), 1)


def resolve_waiver_terms(rules: Mapping[str, Any], faab_spent: Any) -> ContractTerms:
    floor = Decimal(str(rules.get("league_min_salary") or rules.get("default_fa_salary") or 1))
    return ContractTerms(max(Decimal(str(faab_spent)), floor), 1)


def resolve_auction_terms(rules: Mapping[str, Any], salary: Any, years: int) -> ContractTerms:
    amount, term = Decimal(str(salary)), int(years)
    maximum = int(rules.get("max_contract_years") or 4)
    if term < 1 or term > maximum:
        raise ValueError("auction term exceeds league contract rules")
    minimum = Decimal(str(rules.get(f"min_{term}_year_bid") or rules.get("league_min_salary") or 1))
    if amount < minimum:
        raise ValueError(f"auction salary is below the {term}-year minimum bid")
    discount = Decimal(str(rules.get("year_discount_pct") or 0))
    if discount < 0 or discount > 100:
        raise ValueError("auction year discount percentage is invalid")
    return ContractTerms(amount, term)


def resolve_rookie_contract_terms(
    rules: Mapping[str, Any], draft_round: int, round_pick: int,
) -> ContractTerms:
    """Resolve from the immutable base scale; cap scaling never compounds."""
    if not bool(rules.get("rookie_scale_enabled", True)):
        raise ValueError("canonical rookie salary scale is disabled")
    try:
        base_salary = Decimal(str(_ROOKIE_BASE_SALARIES[int(draft_round)][int(round_pick) - 1]))
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("canonical rookie salary scale entry is missing") from None
    salary = base_salary
    option_salary = Decimal("25") if int(draft_round) == 1 else Decimal("15") if int(draft_round) == 2 else Decimal("7")
    if bool(rules.get("scale_rookie_salaries_with_cap", False)):
        base_cap = Decimal(str(rules.get("rookie_salary_scale_base_cap") or ROOKIE_SALARY_SCALE_BASE_CAP))
        current_cap = Decimal(str(rules.get("salary_cap") or 0))
        if base_cap <= 0 or current_cap <= 0:
            raise ValueError("rookie salary scale cap authority is invalid")
        salary = scale_rookie_salary(base_salary, current_cap, base_cap)
        option_salary = scale_rookie_salary(option_salary, current_cap, base_cap)
    years = 2 if int(draft_round) in (1, 2) else 1
    return ContractTerms(_money(salary), years, _money(option_salary))


def calculate_default_dead_cap(rules: Mapping[str, Any], salary: Any) -> Decimal:
    percentage = Decimal(str(rules.get("default_dead_cap_pct") or 0))
    if percentage < 0 or percentage > 100:
        raise ValueError("default dead cap percentage is invalid")
    return (Decimal(str(salary)) * percentage / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )


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

    def canonical_active_season(self) -> int:
        rows = (self.client.table("league_seasons").select("season,status,is_active")
                .eq("league_id", self.league_id).eq("status", "active")
                .eq("is_active", True).execute().data or [])
        if len(rows) != 1:
            raise ValueError("canonical active league season is missing or ambiguous")
        return int(rows[0]["season"])

    def release(self, *, player_id: str, league_team_id: str, season: int | None = None,
                dead_cap: float, idempotency_key: str, notes: str = "") -> Mapping[str, Any]:
        active_season = self.canonical_active_season()
        if season is not None and int(season) != active_season:
            raise ValueError("release season does not match canonical active season")
        request = {
            "league_id": self.league_id, "player_id": str(player_id),
            "league_team_id": str(league_team_id), "season": active_season,
            "dead_cap": float(dead_cap), "idempotency_key": idempotency_key,
            "notes": notes,
        }
        return self.client.rpc("release_offseason_player_authenticated", {"p_request": request}).execute().data
