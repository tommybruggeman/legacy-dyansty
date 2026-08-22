from __future__ import annotations


def _num(value, default=0.0):
    return value if isinstance(value, (int, float)) else default


def build_player_intelligence_profile(player: dict) -> dict:
    identity = player.get("identity", {}) or {}
    production = player.get("production", {}) or {}
    contract = player.get("contract", {}) or {}
    dynasty = player.get("dynasty", {}) or {}
    situation = player.get("situation", {}) or {}
    opinion = player.get("opinion", {}) or {}

    name = (
        identity.get("name")
        or identity.get("player_name")
        or player.get("player_name")
        or "Unknown"
    )

    current_ppg = _num(production.get("season_ppg"))
    expected_ppg = _num(production.get("expected_ppg"), current_ppg)
    historical_ppg = _num(production.get("historical_ppg"), expected_ppg)
    trend_score = _num(production.get("production_trend_score"), expected_ppg)
    historical_context_score = _num(production.get("historical_context_score"), historical_ppg)

    dynasty_asset = _num(
        dynasty.get("dynasty_asset_score"),
        _num(dynasty.get("asset_value_score"))
    )

    future_projection = _num(
        dynasty.get("future_projection_score"),
        dynasty_asset
    )

    engine_player_score = _num(
        dynasty.get("engine_player_score"),
        dynasty_asset
    )

    salary = _num(contract.get("salary"))
    years = _num(contract.get("years"))
    contract_efficiency = _num(contract.get("contract_efficiency_score"), 50)
    contract_risk = _num(contract.get("contract_risk_score"), 50)

    role_score = _num(situation.get("role_score"), 50)
    opportunity_score = _num(situation.get("opportunity_score"), role_score)
    situation_score = _num(situation.get("situation_score"), role_score)

    age = identity.get("age")

    return {
        "identity": {
            "name": name,
            "pos": str(identity.get("pos") or "").upper(),
            "team": identity.get("nfl_team"),
            "age": age,
            "owner": identity.get("current_owner"),
        },
        "production": {
            "current_ppg": current_ppg,
            "expected_ppg": expected_ppg,
            "historical_ppg": historical_ppg,
            "historical_context_score": historical_context_score,
            "trend_score": trend_score,
            "primary_ppg": expected_ppg or current_ppg or historical_ppg,
        },
        "dynasty": {
            "asset_score": dynasty_asset,
            "future_projection_score": future_projection,
            "engine_player_score": engine_player_score,
            "window_score": _num(dynasty.get("dynasty_window_score"), future_projection),
            "risk_score": _num(dynasty.get("dynasty_risk_score")),
            "cornerstone_flag": bool(dynasty.get("cornerstone_flag")),
            "win_now_flag": bool(dynasty.get("win_now_flag")),
            "rebuild_flag": bool(dynasty.get("rebuild_flag")),
        },
        "contract": {
            "salary": salary,
            "years": years,
            "efficiency_score": contract_efficiency,
            "risk_score": contract_risk,
            "term_risk_score": _num(contract.get("term_risk_score")),
            "grade": contract.get("contract_efficiency_grade"),
        },
        "situation": {
            "role_score": role_score,
            "opportunity_score": opportunity_score,
            "situation_score": situation_score,
        },
        "market": {
            "recommendation": opinion.get("recommendation"),
            "asset_recommendation": opinion.get("asset_recommendation"),
            "confidence": _num(opinion.get("confidence"), 50),
            "reasoning": opinion.get("reasoning"),
        },
    }
