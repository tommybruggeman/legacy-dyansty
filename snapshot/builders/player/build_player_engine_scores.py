from __future__ import annotations

import pandas as pd

from auth import service_client


RANKINGS_TABLE = "player_rankings"
CAREER_TABLE = "player_career_features"
TARGET_TABLE = "player_engine_scores"


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _scale_0_100(series):
    s = _num(series)
    if s.empty or s.max() == s.min():
        return pd.Series([50] * len(s), index=s.index)
    return ((s - s.min()) / (s.max() - s.min()) * 100).round(2)


def build_player_engine_scores() -> pd.DataFrame:
    sb = service_client()

    rankings = sb.table(RANKINGS_TABLE).select("*").execute().data or []
    career = sb.table(CAREER_TABLE).select("*").execute().data or []

    if not rankings:
        print("No player rankings found.")
        return pd.DataFrame()

    r = pd.DataFrame(rankings)
    c = pd.DataFrame(career)

    df = r.merge(
        c,
        on="sleeper_id",
        how="left",
        suffixes=("_ranking", "_career"),
    )

    df["player_name"] = df["player"]
    df["pos"] = df["pos_ranking"].fillna(df["pos_career"])

    df["dynasty_rank"] = _num(df.get("dynasty_rank"), 999)

    # Rebuild ranking score from rank directly.
    # Lower dynasty_rank = better player.
    df["rank_score"] = (100 - ((df["dynasty_rank"] - 1) / 149 * 100)).clip(0, 100).round(2)

    # Keep raw source score for debugging, but do not trust it as the main signal.
    df["base_player_score"] = _num(df.get("base_player_score"), 50)
    df["career_feature_score"] = _num(df.get("career_feature_score"), 0)
    df["recent_avg_ppg_ppr"] = _num(df.get("recent_avg_ppg_ppr"), 0)
    df["production_stability_score"] = _num(df.get("production_stability_score"), 0)
    df["years_since_latest_season"] = _num(df.get("years_since_latest_season"), 0)
    df["seasons_played"] = _num(df.get("seasons_played"), 0)
    df["recent_games"] = _num(df.get("recent_games"), 0)

    df["career_score_scaled"] = _scale_0_100(df["career_feature_score"])
    df["recent_ppg_scaled"] = _scale_0_100(df["recent_avg_ppg_ppr"])
    df["stability_scaled"] = _scale_0_100(df["production_stability_score"])

    df["engine_player_score"] = (
        df["career_score_scaled"] * 0.45
        + df["recent_ppg_scaled"] * 0.30
        + df["stability_scaled"] * 0.20
        + df["rank_score"] * 0.05
        - df["years_since_latest_season"] * 5
    )

    # Guardrail: rankings can overrate old/backup QBs.
    old_qb_mask = (
        (df["pos"] == "QB")
        & (df["seasons_played"] >= 8)
        & (
            (df["recent_avg_ppg_ppr"] < 16)
            | (df["recent_games"] < 25)
        )
    )

    df.loc[old_qb_mask, "engine_player_score"] -= 35

    df["engine_player_score"] = (
        df["engine_player_score"]
        .clip(lower=0, upper=100)
        .round(2)
    )

    df["engine_tier"] = pd.cut(
        df["engine_player_score"],
        bins=[-1, 40, 55, 70, 82, 100],
        labels=[5, 4, 3, 2, 1],
    ).astype(int)

    # Compatibility with older player_engine_scores columns
    df["career_score"] = df["career_score_scaled"]
    df["recent_production_score"] = df["recent_ppg_scaled"]
    df["trend_score"] = df["stability_scaled"]
    df["durability_score"] = _scale_0_100(df["recent_games"])
    df["team"] = None

    out = df[
        [
            "sleeper_id",
            "player_name",
            "pos",
            "team",
            "base_player_score",
            "rank_score",
            "career_score",
            "recent_production_score",
            "trend_score",
            "durability_score",
            "engine_player_score",
            "engine_tier",
            "career_feature_score",
            "career_score_scaled",
            "recent_avg_ppg_ppr",
            "recent_ppg_scaled",
            "production_stability_score",
            "stability_scaled",
            "seasons_played",
            "recent_games",
            "years_since_latest_season",
        ]
    ].copy()

    out = out.round(2)
    out = out.replace([float("inf"), float("-inf")], None)
    out = out.astype(object).where(pd.notnull(out), None)

    records = out.to_dict("records")

    print(f"Built player engine score rows: {len(records)}")

    if records:
        sb.table(TARGET_TABLE).upsert(
            records,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(records)} player engine score rows.")

    return out


if __name__ == "__main__":
    build_player_engine_scores()
