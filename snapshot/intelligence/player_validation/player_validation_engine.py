from __future__ import annotations


def _num(value, default=0.0):
    return value if isinstance(value, (int, float)) else default


def validate_player_context(player: dict) -> dict:
    identity = player.get("identity", {}) or {}
    production = player.get("production", {}) or {}
    dynasty = player.get("dynasty", {}) or {}
    situation = player.get("situation", {}) or {}
    contract = player.get("contract", {}) or {}

    name = identity.get("name") or player.get("player_name") or "Unknown"
    pos = str(identity.get("pos") or "").upper()
    age = identity.get("age")
    years_exp = identity.get("years_exp")

    expected_ppg = _num(production.get("expected_ppg"))
    historical_ppg = _num(production.get("historical_ppg"))
    season_ppg = _num(production.get("season_ppg"))

    asset = _num(dynasty.get("dynasty_asset_score"), _num(dynasty.get("asset_value_score")))
    role = _num(situation.get("role_score"))
    opportunity = _num(situation.get("opportunity_score"))

    salary = _num(contract.get("salary"))
    years = _num(contract.get("years"))

    warnings = []
    errors = []

    if not name or name == "Unknown":
        errors.append("missing player name")

    if pos not in {"QB", "RB", "WR", "TE"}:
        warnings.append("missing or unusual position")

    if not isinstance(age, (int, float)):
        warnings.append("missing age")
    elif age < 20 or age > 42:
        errors.append(f"implausible age: {age}")

    if isinstance(age, (int, float)) and isinstance(years_exp, (int, float)):
        if age <= 23 and years_exp >= 5:
            errors.append("age and years_exp conflict")
        if age >= 28 and years_exp in {0, 1}:
            errors.append("veteran age but rookie-level years_exp")

    if isinstance(age, (int, float)) and years_exp is None and asset >= 45:
        warnings.append("asset exists but years_exp missing")

    if expected_ppg <= 0 and historical_ppg <= 0 and season_ppg <= 0:
        warnings.append("all production ppg fields are zero")

    if role <= 0 and opportunity <= 0:
        warnings.append("role and opportunity are both zero")

    if asset <= 0:
        warnings.append("missing dynasty asset score")

    if salary > 0 and years <= 0:
        warnings.append("salary exists but years missing")

    if salary >= 8 and expected_ppg <= 0 and historical_ppg <= 0:
        warnings.append("paid player with missing production context")

    confidence = 100
    confidence -= len(warnings) * 10
    confidence -= len(errors) * 25
    confidence = max(0, min(100, confidence))

    if errors:
        status = "INVALID"
    elif confidence < 80:
        status = "NEEDS_REVIEW"
    else:
        status = "VALID"

    return {
        "player_name": name,
        "status": status,
        "confidence": confidence,
        "warnings": warnings,
        "errors": errors,
    }
