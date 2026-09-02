from __future__ import annotations

import pandas as pd
from engine.scoring_engine import roster_with_scores


def analyze_player_trade_value(ctx: dict, owner_id: str, player_name: str) -> dict:
    df = roster_with_scores(ctx)

    if df.empty:
        return {"error": "Empty roster"}

    # DO NOT PRE-FILTER BY OWNER (causes silent bugs)
    team = df.copy()

    if team.empty:
        return {"error": "No roster data"}

    team["player_name_clean"] = (
        team["player_name"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    player_name_clean = str(player_name).strip().lower()

    player = team[team["player_name_clean"] == player_name_clean]

    if player.empty:
        return {
            "error": "Player not found in roster",
            "searched": player_name,
            "available_players": team["player_name"].head(10).tolist()
        }

    p = player.iloc[0]

    trade_value = float(p.get("trade_value_score", 0))
    contract_value = float(p.get("contract_value_score", 0))
    risk = float(p.get("contract_risk_score", 0))

    if trade_value < 40 and contract_value < 50:
        decision = "SELL"
        reason = "Low value + inefficient contract"

    elif trade_value > 75 and contract_value > 70:
        decision = "HOLD"
        reason = "Elite asset + efficient contract"

    elif trade_value > 60 and risk > 70:
        decision = "SELL HIGH"
        reason = "High value but elevated contract risk"

    else:
        decision = "HOLD"
        reason = "Balanced asset"

    return {
        "owner_id": owner_id,
        "player_name": player_name,
        "decision": decision,
        "reason": reason,
        "trade_value_score": trade_value,
        "contract_value_score": contract_value,
        "contract_risk_score": risk,
        "summary": f"{player_name}: {decision} — TV {trade_value:.1f}, CV {contract_value:.1f}, Risk {risk:.1f}"
    }