from __future__ import annotations


def contract_archetype(pos: str, salary: float, years: float, pos_rank: float, pos_percentile: float) -> dict:
    pos = (pos or "").upper()

    if pos_percentile >= 95:
        archetype = "MARKET SETTER"
        expectation = "Elite positional production required"
        liquidity = "High if player is elite; fragile if production slips"
        risk_note = "Contract creates major ROI pressure"
    elif pos_percentile >= 85:
        archetype = "ELITE POSITIONAL DEAL"
        expectation = "High-end starter production required"
        liquidity = "Tradeable, but buyer pool depends on performance"
        risk_note = "Overpay risk if not a top positional producer"
    elif pos_percentile >= 70:
        archetype = "HIGH STARTER DEAL"
        expectation = "Reliable weekly starter production required"
        liquidity = "Moderate"
        risk_note = "Contract is acceptable if player remains a lineup anchor"
    elif pos_percentile >= 50:
        archetype = "STARTER DEAL"
        expectation = "Starter or strong flex production required"
        liquidity = "Generally movable"
        risk_note = "Risk depends mostly on role stability"
    elif salary >= 8:
        archetype = "MID-TIER DEAL"
        expectation = "Useful flex / rotation production required"
        liquidity = "Moderate to low"
        risk_note = "Can become inefficient quickly if role weakens"
    else:
        archetype = "VALUE DEAL"
        expectation = "Beat low-cost replacement value"
        liquidity = "Easy to hold or churn"
        risk_note = "Low downside due to limited contract burden"

    if pos == "QB" and pos_percentile >= 85:
        required_outcome = "QB1-level production"
    elif pos == "RB" and pos_percentile >= 85:
        required_outcome = "Top-8 RB production"
    elif pos == "WR" and pos_percentile >= 85:
        required_outcome = "Top-12 WR production"
    elif pos == "TE" and pos_percentile >= 85:
        required_outcome = "Top-5 TE production"
    elif pos_percentile >= 70:
        required_outcome = "Weekly fantasy starter"
    elif pos_percentile >= 50:
        required_outcome = "Usable lineup piece"
    else:
        required_outcome = "Positive value above replacement"

    if years >= 3 and pos_percentile >= 85:
        window = "Multi-year premium investment window"
    elif years >= 2:
        window = "Medium-term commitment"
    else:
        window = "Short-term flexible commitment"

    return {
        "contract_archetype": archetype,
        "required_outcome": required_outcome,
        "expectation": expectation,
        "trade_liquidity_note": liquidity,
        "contract_window": window,
        "risk_note": risk_note,
    }
