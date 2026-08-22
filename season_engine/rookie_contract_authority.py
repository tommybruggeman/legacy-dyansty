from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum


class ContractRolloverClass(str, Enum):
    ORDINARY_CONTINUING = "ordinary_continuing"
    ORDINARY_EXPIRATION = "ordinary_expiration"
    ROOKIE_CONTINUING = "rookie_initial_continuing"
    ROOKIE_TAXI_PAUSED = "rookie_initial_taxi_paused"
    ROOKIE_OPTION_ELIGIBLE = "rookie_option_eligible"
    ROOKIE_OPTION_CONSUMED = "rookie_option_consumed"


@dataclass(frozen=True)
class RookieTerms:
    initial_salary: Decimal
    initial_years: int
    option_salary: Decimal
    option_years: int = 1


ROOKIE_TERMS = {
    1: RookieTerms(Decimal("0"), 2, Decimal("25")),
    2: RookieTerms(Decimal("3"), 2, Decimal("15")),
    3: RookieTerms(Decimal("1"), 1, Decimal("7")),
}

ROUND_ONE_INITIAL_SALARIES = {
    1: Decimal("15"), 2: Decimal("12"), 3: Decimal("9"),
    4: Decimal("8"), 5: Decimal("6"), 6: Decimal("5"),
    7: Decimal("4"), 8: Decimal("4"), 9: Decimal("4"),
    10: Decimal("4"),
}


def rookie_terms(round_number: int, round_pick: int) -> RookieTerms:
    if round_number not in ROOKIE_TERMS:
        raise ValueError("rookie draft round must be 1, 2, or 3")
    base = ROOKIE_TERMS[round_number]
    if round_number != 1:
        return base
    try:
        salary = ROUND_ONE_INITIAL_SALARIES[round_pick]
    except KeyError:
        raise ValueError("round-one pick must be between 1 and 10") from None
    return RookieTerms(salary, base.initial_years, base.option_salary, base.option_years)


def taxi_charge(normal_annual_charge: Decimal | int | str) -> Decimal:
    value = Decimal(str(normal_annual_charge))
    if value < 0:
        raise ValueError("normal annual charge cannot be negative")
    return (value * Decimal("0.50")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def classify_contract(*, has_board_provenance: bool, agreement_expired: bool,
                      taxi_in_source_season: bool, initial_term_exhausted: bool,
                      option_consumed: bool) -> ContractRolloverClass:
    if not has_board_provenance:
        return (ContractRolloverClass.ORDINARY_EXPIRATION if agreement_expired
                else ContractRolloverClass.ORDINARY_CONTINUING)
    if option_consumed:
        return (ContractRolloverClass.ORDINARY_EXPIRATION if agreement_expired
                else ContractRolloverClass.ROOKIE_OPTION_CONSUMED)
    if taxi_in_source_season:
        return ContractRolloverClass.ROOKIE_TAXI_PAUSED
    if agreement_expired and initial_term_exhausted:
        return ContractRolloverClass.ROOKIE_OPTION_ELIGIBLE
    return ContractRolloverClass.ROOKIE_CONTINUING


def remaining_initial_years(*, initial_years: int, consumed_years: int,
                            taxi_in_source_season: bool) -> int:
    if initial_years <= 0 or consumed_years < 0:
        raise ValueError("contract-year inputs are invalid")
    effective_consumed = consumed_years - (1 if taxi_in_source_season and consumed_years else 0)
    return max(initial_years - effective_consumed, 0)
