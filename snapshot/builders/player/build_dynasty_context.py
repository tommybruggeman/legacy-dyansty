from __future__ import annotations

import pandas as pd

from auth import service_client


RANKINGS_TABLE = "player_rankings"
CAREER_TABLE = "player_career_features"
ENGINE_TABLE = "player_engine_scores"
TARGET_TABLE = "player_dynasty_context"


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _clip(series):
    return series.clip(lower=0, upper=100).round(2)


def _age_curve(pos: str, age: float | None) -> float:
    if age is None or pd.isna(age) or age <= 0:
        return 50

    if pos == "QB":
        if age <= 27:
            return 95
        if age <= 32:
            return 90
        if age <= 36:
            return 70
        return 35

    if pos == "RB":
        if age <= 24:
            return 95
        if age <= 26:
            return 80
        if age <= 28:
            return 55
        return 25

    if pos == "WR":
        if age <= 25:
            return 95
        if age <= 29:
            return 85
        if age <= 32:
            return 55
        return 25

    if pos == "TE":
        if age <= 26:
            return 85
        if age <= 30:
            return 90
        if age <= 33:
            return 60
        return 30

    return 50


def _longevity(pos: str) -> float:
    return {
        "QB": 95,
        "WR": 75,
        "TE": 70,
        "RB": 45,
    }.get(pos, 50)


def _scarcity(pos: str, engine_score: float) -> float:
    if pos == "QB":
        return 95 if engine_score >= 70 else 80
    if pos == "TE":
        return 80 if engine_score >= 60 else 55
    if pos == "WR":
        return 70 if engine_score >= 70 else 55
    if pos == "RB":
        return 65 if engine_score >= 70 else 45
    return 40


def _career_stage(pos: str, seasons: float, age_curve_score: float) -> str:
    if seasons <= 1:
        return "rookie_or_new"
    if seasons <= 3:
        return "breakout_window"
    if age_curve_score >= 80:
        return "prime"
    if age_curve_score >= 55:
        return "late_prime"
    return "decline"


def build_dynasty_context() -> pd.DataFrame:
    sb = service_client()

    rankings = sb.table(RANKINGS_TABLE).select("*").execute().data or []
    career = sb.table(CAREER_TABLE).select("*").execute().data or []
    engine = sb.table(ENGINE_TABLE).select("*").execute().data or []

    if not engine:
        print("No player engine scores found.")
        return pd.DataFrame()

    e = pd.DataFrame(engine)
    r = pd.DataFrame(rankings) if rankings else pd.DataFrame()
    c = pd.DataFrame(career) if career else pd.DataFrame()

    e["sleeper_id"] = e["sleeper_id"].astype(str)

    df = e.copy()

    if not r.empty:
        r["sleeper_id"] = r["sleeper_id"].astype(str)
        keep = [col for col in ["sleeper_id", "dynasty_rank", "position_rank", "tier", "source"] if col in r.columns]
        df = df.merge(r[keep], on="sleeper_id", how="left", suffixes=("", "_ranking"))

    if not c.empty:
        c["sleeper_id"] = c["sleeper_id"].astype(str)
        keep = [
            col for col in [
                "sleeper_id",
                "seasons_played",
                "recent_games",
                "recent_avg_ppg_ppr",
                "career_feature_score",
                "production_stability_score",
                "years_since_latest_season",
            ]
            if col in c.columns
        ]
        df = df.merge(c[keep], on="sleeper_id", how="left", suffixes=("", "_career"))

    df["player_name"] = df.get("player_name")
    df["pos"] = df.get("pos")

    df["engine_player_score"] = _num(df.get("engine_player_score"), 0)
    df["rank_score"] = _num(df.get("rank_score"), 0)
    df["career_feature_score"] = _num(df.get("career_feature_score"), 0)
    df["recent_avg_ppg_ppr"] = _num(df.get("recent_avg_ppg_ppr"), 0)
    df["production_stability_score"] = _num(df.get("production_stability_score"), 0)
    df["recent_games"] = _num(df.get("recent_games"), 0)
    df["seasons_played"] = _num(df.get("seasons_played"), 0)
    df["years_since_latest_season"] = _num(df.get("years_since_latest_season"), 0)

    # Age is not reliably present yet, so infer a rough dynasty-age proxy from seasons played.
    df["estimated_age"] = df.apply(
        lambda row: (
            22 + float(row["seasons_played"])
            if row["pos"] in ["RB", "WR", "TE"]
            else 23 + float(row["seasons_played"])
        ),
        axis=1,
    )

    df["age_curve_score"] = df.apply(
        lambda row: _age_curve(row["pos"], row["estimated_age"]),
        axis=1,
    )

    df["position_longevity_score"] = df["pos"].apply(_longevity)

    df["scarcity_score"] = df.apply(
        lambda row: _scarcity(row["pos"], row["engine_player_score"]),
        axis=1,
    )

    df["dynasty_window_score"] = _clip(
        df["engine_player_score"] * 0.35
        + df["age_curve_score"] * 0.30
        + df["position_longevity_score"] * 0.20
        + df["scarcity_score"] * 0.15
        - df["years_since_latest_season"] * 5
    )

    df["market_liquidity_score"] = _clip(
        df["engine_player_score"] * 0.45
        + df["dynasty_window_score"] * 0.35
        + df["scarcity_score"] * 0.20
    )

    df["upside_score"] = _clip(
        df["engine_player_score"] * 0.35
        + df["age_curve_score"] * 0.35
        + df["scarcity_score"] * 0.15
        + df["rank_score"] * 0.15
    )

    df["floor_score"] = _clip(
        df["engine_player_score"] * 0.45
        + df["production_stability_score"] * 0.25
        + df["recent_games"] * 0.75
        + df["position_longevity_score"] * 0.10
    )

    df["decline_risk_score"] = _clip(
        100
        - df["age_curve_score"] * 0.55
        - df["position_longevity_score"] * 0.25
        + df["years_since_latest_season"] * 8
    )

    df["dynasty_risk_score"] = _clip(
        df["decline_risk_score"] * 0.55
        + (100 - df["floor_score"]) * 0.30
        + df["years_since_latest_season"] * 8
    )

    df["career_stage"] = df.apply(
        lambda row: _career_stage(row["pos"], row["seasons_played"], row["age_curve_score"]),
        axis=1,
    )

    df["rookie_flag"] = df["seasons_played"] <= 1
    df["veteran_flag"] = df["seasons_played"] >= 5
    df["cornerstone_flag"] = (
        (df["engine_player_score"] >= 80)
        & (df["dynasty_window_score"] >= 75)
        & (df["market_liquidity_score"] >= 75)
    )
    df["elite_flag"] = df["engine_player_score"] >= 80
    df["tradeable_flag"] = df["market_liquidity_score"] >= 60
    df["sell_high_flag"] = (
        (df["engine_player_score"] >= 60)
        & (df["dynasty_window_score"] < 60)
        & (df["decline_risk_score"] >= 45)
    )
    df["buy_low_flag"] = (
        (df["dynasty_window_score"] >= 65)
        & (df["engine_player_score"] < 60)
        & (df["upside_score"] >= 65)
    )
    df["win_now_flag"] = df["engine_player_score"] >= 65
    df["rebuild_flag"] = df["dynasty_window_score"] >= 70

    out = df[
        [
            "sleeper_id",
            "player_name",
            "pos",
            "estimated_age",
            "career_stage",
            "seasons_played",
            "engine_player_score",
            "age_curve_score",
            "dynasty_window_score",
            "position_longevity_score",
            "scarcity_score",
            "market_liquidity_score",
            "upside_score",
            "floor_score",
            "decline_risk_score",
            "dynasty_risk_score",
            "rookie_flag",
            "veteran_flag",
            "cornerstone_flag",
            "elite_flag",
            "tradeable_flag",
            "sell_high_flag",
            "buy_low_flag",
            "win_now_flag",
            "rebuild_flag",
        ]
    ].copy()

    out = out.round(2)
    out = out.replace([float("inf"), float("-inf")], None)
    out = out.astype(object).where(pd.notnull(out), None)

    records = out.to_dict("records")

    print(f"Built dynasty context rows: {len(records)}")

    if records:
        sb.table(TARGET_TABLE).upsert(
            records,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(records)} dynasty context rows.")

    return out


if __name__ == "__main__":
    build_dynasty_context()
