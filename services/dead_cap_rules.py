"""The league's dead cap rule, in one place.

Two paths can drop a player -- the Sleeper sync and the commissioner's Manual
Drop screen -- and each used to compute the penalty on its own. They disagreed
on minimum contracts: the sync exempted them, the screen charged the full
percentage, so an identical drop cost $0.00 or $0.50 depending on which door it
came through. Both now call this module, so there is one answer.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

# A contract season at or below this salary carries no dead cap when dropped:
# dropping a minimum deal is free.
NO_DEAD_CAP_SALARY_CEILING = Decimal("1")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dead_cap_for_season(salary: Any, dead_cap_pct: Any) -> Decimal:
    """Dead cap owed for one contract season when the contract is dropped."""
    pct = Decimal(str(dead_cap_pct or 0))
    if pct < 0 or pct > 100:
        raise ValueError("dead cap percentage is out of range")
    basis = money(salary)
    if basis <= NO_DEAD_CAP_SALARY_CEILING:
        return Decimal("0.00")
    return (basis * pct / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
