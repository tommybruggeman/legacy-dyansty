from __future__ import annotations

import pandas as pd
from engine.scoring_engine import roster_with_scores


# ============================================================
# ROSTER BRAIN v1
# Converts snapshot + scoring engine → GM decisions
# ============================================================

def analyze_roster_health(ctx: dict, owner_id: str) -> dict:
    df = roster_with_scores(ctx)

    if df.empty:
        return {"error": "Empty roster"}

    team = df[df["owner_id"] == owner_id].copy()

    if team.empty:
        return {"error": "Owner not found", "owner_id": owner_id}

    # --------------------------------------------------------
    # CORE METRICS
    # --------------------------------------------------------

    avg_trade_value = team["trade_value_score"].mean()
    avg_contract_value = team["contract_value_score"].mean()
    avg_risk = team["contract_risk_score"].mean()

    total_salary = team["salary_num"].sum()

    top_players = team.sort_values(
        "trade_value_score", ascending=False
    ).head(5)

    worst_players = team.sort_values(
        "trade_value_score", ascending=True
    ).head(5)

    # --------------------------------------------------------
    # CAP HEALTH (simple v1 proxy)
    # --------------------------------------------------------

    cap_pressure = avg_risk
    cap_health = max(0, 100 - cap_pressure)

    # --------------------------------------------------------
    # ROSTER QUALITY
    # --------------------------------------------------------

    roster_quality = (
        avg_trade_value * 0.6
        + avg_contract_value * 0.4
    )

    # --------------------------------------------------------
    # CONTENDER SCORE
    # --------------------------------------------------------

    contender_score = (
        roster_quality * 0.7
        + cap_health * 0.3
    )

    # --------------------------------------------------------
    # WEAKNESSES
    # --------------------------------------------------------

    weaknesses = []

    if cap_health < 50:
        weaknesses.append("Cap is tight")

    if avg_trade_value < 50:
        weaknesses.append("Low roster talent")

    pos_counts = team["pos"].value_counts()

    if pos_counts.get("RB", 0) < 3:
        weaknesses.append("RB depth is weak")

    if pos_counts.get("WR", 0) < 3:
        weaknesses.append("WR depth is weak")

    # --------------------------------------------------------
    # STRENGTHS
    # --------------------------------------------------------

    strengths = []

    if avg_trade_value > 70:
        strengths.append("High-end talent")

    if cap_health > 70:
        strengths.append("Flexible cap")

    if avg_contract_value > 70:
        strengths.append("Efficient contracts")

    # --------------------------------------------------------
    # TRADE + CUT CANDIDATES
    # --------------------------------------------------------

    trade_candidates = worst_players[
        ["player_name", "pos", "trade_value_score"]
    ].to_dict("records")

    cut_candidates = team[
        team["trade_value_score"] < 35
    ][["player_name", "pos", "trade_value_score"]].to_dict("records")

    # --------------------------------------------------------
    # RECOMMENDED MOVE
    # --------------------------------------------------------

    if contender_score > 75:
        move = "Contender: consolidate for elite upgrades"
    elif contender_score > 50:
        move = "Playoff team: fix weak positions"
    else:
        move = "Rebuild: acquire picks and youth"

    return {
        "owner_id": owner_id,
        "contender_score": round(contender_score, 1),
        "roster_quality": round(roster_quality, 1),
        "cap_health": round(cap_health, 1),

        "avg_trade_value": round(avg_trade_value, 1),
        "avg_contract_value": round(avg_contract_value, 1),

        "strengths": strengths,
        "weaknesses": weaknesses,

        "top_players": top_players[
            ["player_name", "pos", "trade_value_score"]
        ].to_dict("records"),

        "trade_candidates": trade_candidates,
        "cut_candidates": cut_candidates,

        "recommended_move": move,
    }
