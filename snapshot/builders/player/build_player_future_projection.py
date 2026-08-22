from __future__ import annotations

import pandas as pd

from auth import service_client


TARGET_TABLE = "player_future_projection"


def _num(v, default=0.0):
    converted = pd.to_numeric(v, errors="coerce")

    if hasattr(converted, "fillna"):
        return converted.fillna(default)

    if pd.isna(converted):
        return default

    return converted


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, float(v)))


def build_player_future_projection():
    sb = service_client()

    print("Loading player engine scores...")
    scores = pd.DataFrame(
        sb.table("player_engine_scores").select("*").execute().data or []
    )

    print("Loading player season stats...")
    stats = pd.DataFrame(
        sb.table("player_season_stats").select("*").execute().data or []
    )

    print("Loading current rosters...")
    rosters = pd.DataFrame(
        sb.table("rosters_current").select("*").execute().data or []
    )

    if scores.empty:
        print("No player_engine_scores found.")
        return

    if stats.empty:
        print("No player_season_stats found.")
        return

    scores["sleeper_id"] = scores["sleeper_id"].astype(str)
    stats["sleeper_id"] = stats["sleeper_id"].astype(str)

    latest_season = int(stats["season"].max())
    recent = stats[stats["season"] >= latest_season - 2].copy()

    recent["fantasy_ppg_ppr"] = _num(recent.get("fantasy_ppg_ppr", 0))
    recent["games"] = _num(recent.get("games", 0))

    agg = (
        recent.groupby("sleeper_id")
        .agg(
            recent_ppg=("fantasy_ppg_ppr", "mean"),
            best_ppg=("fantasy_ppg_ppr", "max"),
            recent_games=("games", "sum"),
            seasons_sample=("season", "nunique"),
        )
        .reset_index()
    )

    df = scores.merge(agg, on="sleeper_id", how="left")

    if not rosters.empty:
        roster_id_col = None
        for candidate in ["sleeper_id", "sleeper_player_id", "player_id"]:
            if candidate in rosters.columns:
                roster_id_col = candidate
                break

        if roster_id_col is None:
            print("WARNING: rosters_current has no sleeper/player id column.")
            print("rosters_current columns:", list(rosters.columns))
        else:
            rosters["sleeper_id"] = rosters[roster_id_col].astype(str)

        roster_cols = [
            c for c in [
                "sleeper_id",
                "owner_team_name",
                "salary",
                "years",
                "pos",
                "player_name",
            ]
            if c in rosters.columns
        ]
        df = df.merge(
            rosters[roster_cols].drop_duplicates("sleeper_id"),
            on="sleeper_id",
            how="left",
            suffixes=("", "_roster"),
        )

    df["is_free_agent"] = df.get("owner_team_name").isna() if "owner_team_name" in df.columns else True

    df["recent_ppg"] = _num(df.get("recent_ppg", 0))
    df["best_ppg"] = _num(df.get("best_ppg", 0))
    df["recent_games"] = _num(df.get("recent_games", 0))
    df["seasons_sample"] = _num(df.get("seasons_sample", 0))

    df["age_curve_score"] = _num(df.get("age_curve_score", 50), 50)
    df["dynasty_asset_score"] = _num(df.get("dynasty_asset_score", 50), 50)
    df["contract_value_score"] = _num(df.get("contract_value_score", 50), 50)
    df["contract_risk_score"] = _num(df.get("contract_risk_score", 50), 50)

    max_ppg = max(float(df["recent_ppg"].max()), 1.0)
    df["production_score"] = (df["recent_ppg"] / max_ppg * 100).clip(0, 100)

    df["sample_confidence"] = (
        (df["recent_games"] / 30 * 100).clip(0, 100)
    )

    df["future_production_score"] = (
        df["production_score"] * 0.40
        + df["best_ppg"].fillna(0) / max_ppg * 100 * 0.15
        + df["age_curve_score"] * 0.25
        + df["dynasty_asset_score"] * 0.20
    ).clip(0, 100)

    df["future_value_score"] = (
        df["future_production_score"] * 0.45
        + df["dynasty_asset_score"] * 0.30
        + df["contract_value_score"] * 0.20
        - df["contract_risk_score"] * 0.05
    ).clip(0, 100)

    def tier(row):
        score = row["future_value_score"]
        if row["is_free_agent"] and score >= 55:
            return "FA TARGET"
        if score >= 80:
            return "CORE FUTURE ASSET"
        if score >= 65:
            return "STRONG HOLD / BUY"
        if score >= 50:
            return "DEPTH VALUE"
        if score >= 35:
            return "FRINGE / WATCH"
        return "AVOID / REPLACE"

    df["projection_tier"] = df.apply(tier, axis=1)

    rows = []

    for _, r in df.iterrows():
        rows.append({
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": r.get("player_name") or r.get("player_name_roster"),
            "pos": r.get("pos") or r.get("pos_roster"),
            "owner_team_name": r.get("owner_team_name"),
            "is_free_agent": bool(r.get("is_free_agent", True)),
            "recent_ppg": round(float(r.get("recent_ppg", 0)), 2),
            "best_ppg": round(float(r.get("best_ppg", 0)), 2),
            "recent_games": int(float(r.get("recent_games", 0))),
            "sample_confidence": round(float(r.get("sample_confidence", 0)), 2),
            "production_score": round(float(r.get("production_score", 0)), 2),
            "age_curve_score": round(float(r.get("age_curve_score", 50)), 2),
            "future_production_score": round(float(r.get("future_production_score", 0)), 2),
            "future_value_score": round(float(r.get("future_value_score", 0)), 2),
            "projection_tier": r.get("projection_tier"),
            "source_latest_season": latest_season,
        })

    print(f"Prepared future projection rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player future projection rows.")


if __name__ == "__main__":
    build_player_future_projection()
