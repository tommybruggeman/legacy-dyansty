from __future__ import annotations

import pandas as pd

from auth import service_client


ROSTER_TABLE = "roster"
ENGINE_TABLE = "player_engine_scores"
DYNASTY_TABLE = "player_dynasty_context"
TARGET_TABLE = "roster_asset_values"


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def build_roster_asset_values() -> pd.DataFrame:
    sb = service_client()

    roster_rows = sb.table(ROSTER_TABLE).select("*").execute().data or []
    engine_rows = sb.table(ENGINE_TABLE).select("*").execute().data or []
    dynasty_rows = sb.table(DYNASTY_TABLE).select("*").execute().data or []

    if not roster_rows:
        print("No roster rows found.")
        return pd.DataFrame()

    if not engine_rows:
        print("No player engine scores found.")
        return pd.DataFrame()

    roster = pd.DataFrame(roster_rows)
    engine = pd.DataFrame(engine_rows)
    dynasty = pd.DataFrame(dynasty_rows) if dynasty_rows else pd.DataFrame()

    if "sleeper_id" not in roster.columns and "player_id" in roster.columns:
        roster["sleeper_id"] = roster["player_id"].astype(str)

    roster["sleeper_id"] = roster["sleeper_id"].astype(str)
    engine["sleeper_id"] = engine["sleeper_id"].astype(str)

    df = roster.merge(
        engine,
        on="sleeper_id",
        how="left",
        suffixes=("_roster", "_engine"),
    )

    # Preserve league scope. Current roster table is league-local, so derive the league
    # from the active owners table. Future version should store league_id directly on roster.
    owner_league_rows = (
        sb.table("owners")
        .select("league_id")
        .limit(1)
        .execute()
        .data
        or []
    )
    active_league_id = owner_league_rows[0].get("league_id") if owner_league_rows else None
    df["league_id"] = active_league_id

    if not dynasty.empty:
        dynasty["sleeper_id"] = dynasty["sleeper_id"].astype(str)
        dynasty_keep = [
            "sleeper_id",
            "career_stage",
            "dynasty_window_score",
            "market_liquidity_score",
            "upside_score",
            "floor_score",
            "decline_risk_score",
            "dynasty_risk_score",
            "cornerstone_flag",
            "sell_high_flag",
            "buy_low_flag",
            "win_now_flag",
            "rebuild_flag",
        ]
        dynasty_keep = [c for c in dynasty_keep if c in dynasty.columns]
        df = df.merge(dynasty[dynasty_keep], on="sleeper_id", how="left")

    df["player_name"] = df.get("player_name_engine", df.get("player_name_roster"))
    if "player" in df.columns:
        df["player_name"] = df["player_name"].fillna(df["player"])

    df["pos"] = df.get("pos_engine", df.get("pos_roster"))

    df["salary"] = _num(df.get("salary"), 0)
    df["years"] = _num(df.get("years"), 1)
    df["engine_player_score"] = _num(df.get("engine_player_score"), 0)

    # V1 fallback: rookies / unmatched players may not exist in engine table yet.
    unmatched_mask = df["engine_player_score"] <= 0
    cheap_mask = df["salary"] <= 5
    mid_mask = (df["salary"] > 5) & (df["salary"] <= 15)

    df.loc[unmatched_mask & cheap_mask, "engine_player_score"] = 35
    df.loc[unmatched_mask & mid_mask, "engine_player_score"] = 45
    df["engine_tier"] = _num(df.get("engine_tier"), 6).round(0).astype(int)
    df["career_feature_score"] = _num(df.get("career_feature_score"), 0)
    df["dynasty_window_score"] = _num(df.get("dynasty_window_score"), 0)
    df["market_liquidity_score"] = _num(df.get("market_liquidity_score"), 0)
    df["upside_score"] = _num(df.get("upside_score"), 0)
    df["floor_score"] = _num(df.get("floor_score"), 0)
    df["decline_risk_score"] = _num(df.get("decline_risk_score"), 0)
    df["dynasty_risk_score"] = _num(df.get("dynasty_risk_score"), 0)

    df["contract_cost_score"] = (100 - (df["salary"] * 4)).clip(0, 100)
    df["term_risk_score"] = (df["years"] * 8).clip(0, 40)
    df["contract_risk_score"] = (
        df["salary"] * 3
        + df["years"] * 5
    ).clip(0, 100)

    df["contract_value_score"] = (
        df["engine_player_score"]
        - df["salary"] * 2.5
        - df["years"] * 1.5
    ).clip(0, 100).round(2)

    df["win_now_asset_score"] = (
        df["engine_player_score"] * 0.65
        + df["contract_value_score"] * 0.25
        + df["contract_cost_score"] * 0.10
    ).clip(0, 100).round(2)

    df["dynasty_asset_score"] = (
        df["dynasty_window_score"] * 0.40
        + df["market_liquidity_score"] * 0.25
        + df["upside_score"] * 0.20
        + df["contract_value_score"] * 0.15
        - df["decline_risk_score"] * 0.10
    ).clip(0, 100).round(2)

    # Default asset score favors dynasty value slightly.
    df["asset_value_score"] = (
        df["dynasty_asset_score"] * 0.55
        + df["win_now_asset_score"] * 0.45
    ).clip(0, 100).round(2)

    def decision(row):
        score = row["asset_value_score"]
        contract = row["contract_value_score"]
        risk = row["contract_risk_score"]
        player = row["engine_player_score"]

        if player >= 80 and contract >= 60:
            return "CORE HOLD"
        if player >= 75 and contract < 45:
            return "EXPENSIVE HOLD"
        if score >= 75:
            return "HOLD"
        if player < 45 and risk >= 40:
            return "CUT / SELL"
        if contract >= 70 and player >= 60:
            return "VALUE BUY"
        if score < 50:
            return "SELL"
        return "HOLD"

    df["asset_recommendation"] = df.apply(decision, axis=1)

    keep = [
        "league_id",
        "sleeper_id",
        "player_name",
        "pos",
        "owner_team_name",
        "salary",
        "years",
        "engine_player_score",
        "engine_tier",
        "career_feature_score",
        "career_stage",
        "dynasty_window_score",
        "market_liquidity_score",
        "upside_score",
        "floor_score",
        "decline_risk_score",
        "dynasty_risk_score",
        "win_now_asset_score",
        "dynasty_asset_score",
        "cornerstone_flag",
        "sell_high_flag",
        "buy_low_flag",
        "win_now_flag",
        "rebuild_flag",
        "contract_cost_score",
        "term_risk_score",
        "contract_risk_score",
        "contract_value_score",
        "asset_value_score",
        "asset_recommendation",
    ]

    existing = [c for c in keep if c in df.columns]
    out = df[existing].copy()

    out = out.sort_values(["owner_team_name", "player_name"])
    out = out.drop_duplicates(subset=["league_id", "sleeper_id"], keep="first")

    out = out.round(2)
    out = out.replace([float("inf"), float("-inf")], None)
    out = out.astype(object).where(pd.notnull(out), None)

    records = out.to_dict("records")

    print(f"Built roster asset value rows: {len(records)}")

    if records:
        sb.table(TARGET_TABLE).upsert(
            records,
            on_conflict="league_id,sleeper_id",
        ).execute()

    print(f"Upserted {len(records)} roster asset value rows.")

    return out


if __name__ == "__main__":
    build_roster_asset_values()
