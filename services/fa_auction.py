"""FA auction bid math, shared by the Python award check and the browser.

The calculator itself runs in the browser (services/commissioner_widgets.py
mirrors these formulas in JavaScript) so every tap updates instantly. The
award is checked again here before any contract is written.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


def auction_rules(rules: Mapping[str, Any]) -> dict[str, Any]:
    """The league's auction settings with the app's long-standing defaults."""
    def money(key: str, default: float) -> float:
        try:
            return float(rules.get(key) if rules.get(key) is not None else default)
        except (TypeError, ValueError):
            return default

    try:
        max_years = int(float(rules.get("max_contract_years") or 4))
    except (TypeError, ValueError):
        max_years = 4
    max_years = max(1, min(max_years, 4))
    return {
        "max_years": max_years,
        "discount_pct": money("year_discount_pct", 10.0),
        "minimums": {
            1: 0.0,
            2: money("min_2_year_bid", 4.0),
            3: money("min_3_year_bid", 12.0),
            4: money("min_4_year_bid", 20.0),
        },
    }


def minimum_for(rules: Mapping[str, Any], years: int) -> float:
    return float(rules["minimums"].get(int(years), 0.0))


def is_valid_bid(rules: Mapping[str, Any], bid: float, years: int) -> bool:
    return (
        1 <= int(years) <= int(rules["max_years"])
        and float(bid) >= 1
        and float(bid) >= minimum_for(rules, years)
    )


def equivalent_bid(rules: Mapping[str, Any], bid: float, years: int, target_years: int) -> int | None:
    """The same bid expressed at another contract length, rounded up.

    Each extra year is worth the league's per-year discount. Returns None
    when the converted bid is below that length's minimum (not a valid bid).
    """
    factor = 1 + float(rules["discount_pct"]) / 100
    value = float(bid) * factor ** (int(years) - int(target_years))
    # Round away float noise before rounding up: 14 * 1.1 / 1.1 is 14, not 15.
    value = int(math.ceil(round(value, 6)))
    return value if value >= minimum_for(rules, target_years) else None
