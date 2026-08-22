from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client

TARGET_TABLE = "player_market_consensus"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def tier(score):
    if score >= 95:
        return "ELITE MARKET CORNERSTONE"
    if score >= 88:
        return "PREMIUM DYNASTY ASSET"
    if score >= 78:
        return "HIGH-DEMAND ASSET"
    if score >= 68:
        return "STRONG MARKET ASSET"
    if score >= 55:
        return "TRADEABLE STARTER"
    if score >= 40:
        return "SECONDARY PIECE"
    return "LOW MARKET VALUE"


def confidence(source_count):
    if source_count >= 3:
        return "HIGH"
    if source_count == 2:
        return "MEDIUM"
    return "LOW"


def build_player_market_consensus():
    sb = service_client()

    sources = pd.DataFrame(
        sb.table("player_market_sources").select("*").execute().data or []
    )

    if sources.empty:
        print("No player_market_sources found.")
        return pd.DataFrame()

    sources["sleeper_id"] = sources["sleeper_id"].astype(str)
    sources["normalized_value"] = sources["normalized_value"].apply(clamp)

    grouped = (
        sources.groupby(["sleeper_id", "player_name", "pos"], dropna=False)
        .agg(
            market_consensus_score=("normalized_value", "mean"),
            source_count=("source", "nunique"),
        )
        .reset_index()
    )

    grouped["market_consensus_score"] = grouped["market_consensus_score"].round(2)
    grouped = grouped.sort_values("market_consensus_score", ascending=False)
    grouped["market_rank"] = range(1, len(grouped) + 1)

    rows = []

    for _, r in grouped.iterrows():
        score = clamp(r["market_consensus_score"])
        source_count = int(r["source_count"])

        row = {
            "sleeper_id": str(r["sleeper_id"]),
            "player_name": r["player_name"],
            "pos": r["pos"],
            "market_consensus_score": round(score, 2),
            "market_rank": int(r["market_rank"]),
            "source_count": source_count,
            "confidence": confidence(source_count),
            "consensus_tier": tier(score),
        }

        row["consensus_summary"] = (
            f"{row['player_name']} consensus market score {row['market_consensus_score']} "
            f"from {row['source_count']} source(s): {row['consensus_tier']}."
        )

        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player_market_consensus rows.")
    return out


if __name__ == "__main__":
    df = build_player_market_consensus()
    if not df.empty:
        print(
            df[
                [
                    "market_rank",
                    "player_name",
                    "pos",
                    "market_consensus_score",
                    "source_count",
                    "confidence",
                    "consensus_tier",
                ]
            ]
            .head(50)
            .to_string(index=False)
        )
