from __future__ import annotations

from snapshot.intelligence.player_profile.player_intelligence_profile import (
    build_player_intelligence_profile,
)


def _clamp(value, low=0, high=100):
    return max(low, min(high, round(value, 2)))


def _reason(label, score):
    return {"label": label, "score": round(score, 2) if isinstance(score, (int, float)) else score}


def evaluate_player_grades(player: dict, roster_context: dict | None = None) -> dict:
    """
    All GM evaluation must read from the canonical Player Intelligence Profile.
    No raw table/nested-field interpretation should happen here.
    """
    profile = player.get("intelligence_profile") or build_player_intelligence_profile(player)

    identity = profile["identity"]
    production = profile["production"]
    dynasty = profile["dynasty"]
    contract = profile["contract"]
    situation = profile["situation"]
    market_context = profile["market"]

    pos = identity["pos"]
    age = identity.get("age")

    salary = contract["salary"]
    years = contract["years"]
    contract_eff = contract["efficiency_score"]
    contract_risk = contract["risk_score"]

    current_ppg = production["current_ppg"]
    expected_ppg = production["expected_ppg"]
    historical_ppg = production["historical_ppg"]
    primary_ppg = production["primary_ppg"]
    trend_score = production["trend_score"]

    asset = dynasty["asset_score"]
    future = dynasty["future_projection_score"]
    engine_score = dynasty["engine_player_score"]

    role = situation["role_score"]
    opportunity = situation["opportunity_score"]

    football = _clamp(
        (primary_ppg * 2.6)
        + (historical_ppg * 0.6)
        + (trend_score * 0.45)
        + (role * 0.22)
        + (opportunity * 0.12)
        + (asset * 0.2)
    )

    dynasty_score = _clamp((asset * 0.45) + (future * 0.35) + (engine_score * 0.2))

    if isinstance(age, (int, float)):
        if age <= 24:
            dynasty_score = _clamp(dynasty_score + 8)
        elif age <= 26:
            dynasty_score = _clamp(dynasty_score + 4)
        elif age >= 31:
            dynasty_score = _clamp(dynasty_score - 18)
        elif age >= 29:
            dynasty_score = _clamp(dynasty_score - 10)

    contract_score = _clamp((contract_eff * 0.7) + ((100 - contract_risk) * 0.3))

    market_grade = _clamp((asset * 0.45) + (future * 0.25) + (primary_ppg * 1.2) + (engine_score * 0.15))
    if pos == "QB":
        market_grade = _clamp(market_grade + 10)
    if salary >= 30:
        market_grade = _clamp(market_grade - 8)

    replacement = _clamp(
        (football * 0.35)
        + (dynasty_score * 0.25)
        + (market_grade * 0.25)
        + (asset * 0.15)
    )
    if pos == "QB":
        replacement = _clamp(replacement + 10)

    cap_pressure = _clamp(
        (salary * 1.8)
        + (years * 4)
        + ((100 - contract_score) * 0.35)
        - (football * 0.18)
        - (dynasty_score * 0.12)
    )

    roster_fit = _clamp(
        (football * 0.35)
        + (dynasty_score * 0.2)
        + (role * 0.18)
        + (opportunity * 0.12)
        + (contract_score * 0.15)
    )

    liquidity = _clamp(
        (market_grade * 0.65)
        + ((100 - salary) * 0.2)
        + (asset * 0.15)
    )

    production_profile = _clamp(
        (expected_ppg * 2.8)
        + (historical_ppg * 0.9)
        + (trend_score * 0.5)
    )

    return {
        "football_grade": {
            "score": football,
            "reasons": [
                _reason("primary_ppg", primary_ppg),
                _reason("historical_ppg", historical_ppg),
                _reason("trend_score", trend_score),
                _reason("role_score", role),
                _reason("asset_score", asset),
            ],
        },
        "production_grade": {
            "score": production_profile,
            "reasons": [
                _reason("current_ppg", current_ppg),
                _reason("expected_ppg", expected_ppg),
                _reason("historical_ppg", historical_ppg),
                _reason("trend_score", trend_score),
            ],
        },
        "dynasty_grade": {
            "score": dynasty_score,
            "reasons": [
                _reason("asset_score", asset),
                _reason("future_projection_score", future),
                _reason("engine_player_score", engine_score),
                _reason("age", age),
            ],
        },
        "contract_grade": {
            "score": contract_score,
            "reasons": [
                _reason("contract_efficiency", contract_eff),
                _reason("contract_risk", contract_risk),
                _reason("salary", salary),
                _reason("years", years),
            ],
        },
        "market_grade": {
            "score": market_grade,
            "reasons": [
                _reason("asset_score", asset),
                _reason("future_projection", future),
                _reason("primary_ppg", primary_ppg),
                _reason("engine_player_score", engine_score),
                _reason("salary_drag", salary),
                _reason("market_recommendation", market_context.get("recommendation")),
            ],
        },
        "replacement_grade": {
            "score": replacement,
            "reasons": [
                _reason("football_grade", football),
                _reason("dynasty_grade", dynasty_score),
                _reason("market_grade", market_grade),
                _reason("position", pos),
            ],
        },
        "cap_pressure_grade": {
            "score": cap_pressure,
            "reasons": [
                _reason("salary", salary),
                _reason("years", years),
                _reason("contract_grade", contract_score),
            ],
        },
        "roster_fit_grade": {
            "score": roster_fit,
            "reasons": [
                _reason("football_grade", football),
                _reason("dynasty_grade", dynasty_score),
                _reason("role_score", role),
                _reason("opportunity_score", opportunity),
                _reason("contract_grade", contract_score),
            ],
        },
        "liquidity_grade": {
            "score": liquidity,
            "reasons": [
                _reason("market_grade", market_grade),
                _reason("salary", salary),
                _reason("asset_score", asset),
            ],
        },
    }
