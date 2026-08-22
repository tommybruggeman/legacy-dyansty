from __future__ import annotations

from snapshot.intelligence.evaluation.gm_evaluation_engine import evaluate_player_grades


def _score(grades: dict, key: str, default=50.0):
    item = grades.get(key, {}) or {}
    value = item.get("score", default)
    return value if isinstance(value, (int, float)) else default


def _clamp(value, low=0, high=100):
    return max(low, min(high, round(value, 2)))


def evaluate_player(player: dict, roster_context: dict | None = None) -> dict:
    identity = player.get("identity", {}) or {}
    production = player.get("production", {}) or {}
    contract = player.get("contract", {}) or {}

    pos = str(identity.get("pos") or "").upper()
    salary = contract.get("salary") if isinstance(contract.get("salary"), (int, float)) else 0

    dynasty = player.get("dynasty", {}) or {}
    asset = dynasty.get("dynasty_asset_score")
    if not isinstance(asset, (int, float)):
        asset = dynasty.get("asset_value_score")
    if not isinstance(asset, (int, float)):
        asset = production.get("asset_score")
    if not isinstance(asset, (int, float)):
        asset = 0

    grades = evaluate_player_grades(player, roster_context)

    football = _score(grades, "football_grade")
    dynasty = _score(grades, "dynasty_grade")
    contract_grade = _score(grades, "contract_grade")
    market = _score(grades, "market_grade")
    replacement = _score(grades, "replacement_grade")
    cap_pressure = _score(grades, "cap_pressure_grade")
    fit = _score(grades, "roster_fit_grade")
    liquidity = _score(grades, "liquidity_grade")

    reasons = []

    move_pressure = (
        cap_pressure * 0.32
        + (100 - contract_grade) * 0.18
        + liquidity * 0.16
        + (100 - fit) * 0.14
        - replacement * 0.14
        - dynasty * 0.06
    )

    if contract_grade <= 35 and salary >= 15:
        move_pressure += 12
        reasons.append("contract grade is poor relative to salary")

    if football >= 65:
        move_pressure -= 8
        reasons.append("football value lowers dump risk")

    if dynasty >= 65:
        move_pressure -= 8
        reasons.append("dynasty value lowers dump risk")

    if liquidity >= 65 and cap_pressure >= 60:
        move_pressure += 8
        reasons.append("player has enough market liquidity to explore a cap exit")

    if cap_pressure >= 70 and contract_grade <= 25 and replacement < 55:
        move_pressure += 8
        reasons.append("bad contract with manageable replacement difficulty raises shop pressure")

    if replacement >= 75:
        move_pressure -= 10
        reasons.append("replacement difficulty lowers willingness to move")

    if football >= 75 and replacement >= 70:
        move_pressure = min(move_pressure, 24)
        reasons.append("elite production plus replacement difficulty caps move pressure")

    if pos == "QB" and dynasty >= 65 and replacement >= 70:
        move_pressure = min(move_pressure, 20)
        reasons.append("premium superflex QB profile is protected")

    if salary <= 2 and market < 45:
        move_pressure = min(move_pressure, 45)
        reasons.append("cheap fringe player is a churn decision, not a trade centerpiece")

    if pos == "QB" and dynasty >= 55:
        move_pressure -= 5
        reasons.append("superflex QB value adds hold pressure")

    move_pressure = _clamp(move_pressure)

    if salary <= 2 and market < 45:
        decision = "CHURN / REPLACE"
    elif move_pressure >= 75:
        decision = "SELL / ACTIVELY SHOP"
    elif move_pressure >= 60:
        decision = "SHOP"
    elif move_pressure >= 45:
        decision = "LISTEN / PRICE CHECK"
    elif move_pressure >= 25:
        decision = "HOLD"
    else:
        decision = "CORE / PROTECT"

    if contract_grade <= 35 and football < 45 and dynasty >= 50:
        stance = "uncertain production, bad contract"
        action = "hold only if the missing production context is explainable; otherwise market-check or downgrade"
    elif contract_grade <= 35 and (football >= 55 or dynasty >= 55 or asset >= 50):
        stance = "good player, bad contract"
        action = "listen aggressively, but do not salary-dump unless return or cap flexibility clearly helps"
    elif salary <= 2 and market < 45:
        stance = "cheap churn piece"
        action = "replace if a better waiver or roster spot appears"
    elif football >= 75 and replacement >= 75:
        stance = "premium player, hard to replace"
        action = "do not shop unless overwhelmed"
    elif cap_pressure >= 70 and liquidity >= 55:
        stance = "cap pressure trade candidate"
        action = "shop in upgrade packages or cap-clearing structures"
    elif dynasty >= 65:
        stance = "dynasty asset"
        action = "hold unless market overpays"
    else:
        stance = "standard roster evaluation"
        action = "market-check only if it supports the broader roster plan"

    if not reasons:
        reasons.append("decision synthesized from football, dynasty, contract, market, fit, cap, liquidity, and replacement grades")

    return {
        "grades": grades,
        "move_pressure": move_pressure,
        "decision": decision,
        "stance": stance,
        "action": action,
        "reasons": reasons[:6],
    }
