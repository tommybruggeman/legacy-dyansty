from __future__ import annotations

import pandas as pd

from engine.config.league_config import DEFAULT_LEAGUE_CONFIG, LeagueConfig


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def build_player_reasoning_view(roster: pd.DataFrame, player_values: pd.DataFrame | None = None, config: LeagueConfig = DEFAULT_LEAGUE_CONFIG) -> pd.DataFrame:
    """
    V1 deterministic GM reasoning layer.

    Input:
    - roster from team_roster_state/load_roster()
    - optional player_values table

    Output:
    - scored player rows with recommendation + reason
    """

    if roster is None or roster.empty:
        return pd.DataFrame()

    df = roster.copy()

    # -------------------------------------------------
    # Normalize expected fields
    # -------------------------------------------------
    if "player" not in df.columns and "player_name" in df.columns:
        df["player"] = df["player_name"]

    if "owner" not in df.columns and "team_id" in df.columns:
        df["owner"] = df["team_id"]

    if "pos" not in df.columns and "position" in df.columns:
        df["pos"] = df["position"]

    df["salary_num"] = _num(df.get("salary", 0))
    df["years_num"] = _num(df.get("years", 0))

    # -------------------------------------------------
    # Optional value merge
    # -------------------------------------------------
    df["trade_value_score"] = 50.0

    if player_values is not None and not player_values.empty:
        pv = player_values.copy()

        # Try common key options
        if "sleeper_id" in df.columns and "sleeper_id" in pv.columns:
            df = df.merge(
                pv[["sleeper_id", "trade_value_score"]].drop_duplicates("sleeper_id"),
                on="sleeper_id",
                how="left",
                suffixes=("", "_pv"),
            )
            df["trade_value_score"] = df["trade_value_score_pv"].fillna(df["trade_value_score"])
            df = df.drop(columns=["trade_value_score_pv"], errors="ignore")

        elif "player" in df.columns and "player_name" in pv.columns:
            df = df.merge(
                pv[["player_name", "trade_value_score"]].drop_duplicates("player_name"),
                left_on="player",
                right_on="player_name",
                how="left",
                suffixes=("", "_pv"),
            )
            df["trade_value_score"] = df["trade_value_score_pv"].fillna(df["trade_value_score"])
            df = df.drop(columns=["trade_value_score_pv", "player_name"], errors="ignore")

    df["trade_value_score"] = _num(df["trade_value_score"], 50)

    # -------------------------------------------------
    # Contract scoring
    # Lower salary/years risk = better contract value
    # -------------------------------------------------
    df["contract_risk_score"] = (
        df["salary_num"] * 1.4
        + df["years_num"] * 3.0
    ).clip(lower=0, upper=100)

    df["contract_value_score"] = (
        100
        - df["contract_risk_score"]
        + (df["trade_value_score"] * 0.25)
    ).clip(lower=0, upper=100)

    # -------------------------------------------------
    # Asset score
    # -------------------------------------------------
    df["base_asset_score"] = (
        df["trade_value_score"] * 0.60
        + df["contract_value_score"] * 0.30
        - df["contract_risk_score"] * 0.10
    ).clip(lower=0, upper=100)

    df["position_bonus"] = (
        df.get("pos", "UNK")
        .fillna("UNK")
        .astype(str)
        .str.upper()
        .map(config.position_weights)
        .fillna(0.0)
    )

    # Superflex-aware asset adjustment.
    # Additive bonus prevents low-end QBs from being inflated into elite assets.
    df["asset_score"] = (
        df["base_asset_score"] + df["position_bonus"]
    ).clip(lower=0, upper=100)

    df["contract_grade"] = df["contract_value_score"].apply(_grade)
    df["asset_grade"] = df["asset_score"].apply(_grade)

    # -------------------------------------------------
    # Recommendation logic
    # Priority order:
    # 1. missing contract data
    # 2. elite assets
    # 3. bad contracts
    # 4. good cheap contracts
    # 5. default holds
    # -------------------------------------------------
    recommendations = []
    reasons = []

    for _, r in df.iterrows():
        player = r.get("player")
        salary = float(r.get("salary_num", 0))
        years = float(r.get("years_num", 0))
        trade = float(r.get("trade_value_score", 50))
        contract_value = float(r.get("contract_value_score", 50))
        risk = float(r.get("contract_risk_score", 0))
        asset = float(r.get("asset_score", 50))

        if salary <= 0 or years <= 0:
            rec = "NEEDS CONTRACT REVIEW"
            reason = "Missing salary or years, so this player cannot be fully evaluated yet."

        elif trade >= 80 and contract_value >= 65:
            rec = "CORE HOLD"
            reason = "High asset value and contract is efficient enough to build around."

        elif trade >= 75 and risk >= 55:
            rec = "HOLD / SHOP ONLY FOR PREMIUM"
            reason = "Strong player value, but contract risk is high enough to listen on overpays."

        elif trade < 45 and salary >= 8:
            rec = "SELL / CUT WATCH"
            reason = "Low asset value relative to salary creates poor roster efficiency."

        elif contract_value >= 80 and trade >= 50:
            rec = "VALUE HOLD"
            reason = "Contract is efficient and player retains enough market value."

        elif years == 1 and trade >= 60:
            rec = "EXTEND OR SELL"
            reason = "Useful asset entering an expiring-contract decision window."

        elif asset >= 65:
            rec = "HOLD"
            reason = "Overall asset profile is positive."

        else:
            rec = "DEPTH / MONITOR"
            reason = "Not a priority move unless needed for roster construction or cap space."

        recommendations.append(rec)
        reasons.append(reason)

    df["recommendation"] = recommendations
    df["reason"] = reasons

    return df.sort_values(
        by=["asset_score", "trade_value_score", "contract_value_score"],
        ascending=False,
    )
