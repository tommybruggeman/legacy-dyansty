from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows

TARGET_TABLE = "player_market_value_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def market_tier(score):
    if score >= 92:
        return "ELITE MARKET ASSET"
    if score >= 85:
        return "PREMIUM MARKET ASSET"
    if score >= 75:
        return "HIGH-DEMAND ASSET"
    if score >= 65:
        return "STRONG MARKET ASSET"
    if score >= 55:
        return "TRADEABLE STARTER"
    if score >= 45:
        return "SECONDARY MARKET PIECE"
    if score >= 35:
        return "LOW-DEMAND ASSET"
    return "REPLACEMENT MARKET VALUE"


def confidence(row):
    rank = row["dynasty_rank_score"]
    engine = row["engine_score"]
    prod = row["production_reputation_score"]

    if rank >= 70 and engine >= 70:
        return "HIGH"
    if rank >= 50 or prod >= 60:
        return "MEDIUM"
    return "LOW"


def summary(row):
    return (
        f"{row['player_name']} market value {row['market_value_score']}: "
        f"{row['market_tier']}. "
        f"Rank {row['dynasty_rank_score']}, "
        f"engine {row['engine_score']}, "
        f"future {row['future_market_score']}, "
        f"production reputation {row['production_reputation_score']}, "
        f"confidence {row['market_confidence']}."
    )


def build_player_market_value_engine():
    sb = service_client()

    engine = pd.DataFrame(
        sb.table("player_engine_scores").select("*").execute().data or []
    )

    situation = pd.DataFrame(
        sb.table("player_situation_context").select("*").execute().data or []
    )

    players = pd.DataFrame(
        sb.table("players").select("*").execute().data or []
    )

    contracts = pd.DataFrame(load_internal_contract_rows(sb))

    consensus = pd.DataFrame(
        sb.table("player_market_consensus").select("*").execute().data or []
    )

    if engine.empty:
        print("No player_engine_scores found.")
        return pd.DataFrame()

    engine["sleeper_id"] = engine["sleeper_id"].astype(str)

    if not players.empty:
        players["sleeper_id"] = players["sleeper_id"].astype(str)

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)
        situation = (
            situation.sort_values(["sleeper_id", "situation_score"], ascending=[True, False])
            .drop_duplicates(subset=["sleeper_id"], keep="first")
        )

    if not contracts.empty:
        contracts = contracts.rename(columns={
            "sleeper_player_id": "sleeper_id",
            "player_position": "contract_pos",
        })
        contracts["sleeper_id"] = contracts["sleeper_id"].astype(str)
        contracts = (
            contracts.sort_values(["sleeper_id", "salary"], ascending=[True, False])
            .drop_duplicates(subset=["sleeper_id"], keep="first")
        )

    if not consensus.empty:
        consensus["sleeper_id"] = consensus["sleeper_id"].astype(str)

    df = engine.copy()

    if not players.empty:
        df = df.merge(
            players.rename(columns={
                "full_name": "players_full_name",
                "team": "players_team",
                "position": "players_position",
            })[["sleeper_id", "players_full_name", "players_team", "players_position"]],
            on="sleeper_id",
            how="left",
        )

    if not situation.empty:
        df = df.merge(
            situation[[
                "sleeper_id",
                "situation_score",
                "nfl_team",
                "age",
                "years_exp",
                "depth_chart_order",
                "nfl_status",
            ]],
            on="sleeper_id",
            how="left",
            suffixes=("", "_situation"),
        )

    if not contracts.empty:
        df = df.merge(
            contracts[["sleeper_id", "is_rookie", "salary", "contract_pos"]],
            on="sleeper_id",
            how="left",
        )

    if not consensus.empty:
        df = df.merge(
            consensus[[
                "sleeper_id",
                "market_consensus_score",
                "market_rank",
                "source_count",
                "confidence",
                "consensus_tier",
            ]],
            on="sleeper_id",
            how="left",
        )

    rows = []

    for _, r in df.iterrows():
        player_name = r.get("player_name") or r.get("players_full_name")
        pos = r.get("pos") or r.get("players_position") or r.get("contract_pos")
        nfl_team = r.get("nfl_team") or r.get("team") or r.get("players_team")

        engine_score = clamp(r.get("engine_score"))
        base_score = clamp(r.get("base_player_score"))
        rank_score = clamp(r.get("rank_score"))
        age_curve = clamp(r.get("age_curve_score", 50))
        career_score = clamp(r.get("career_score_scaled", r.get("career_score")))
        recent_score = clamp(r.get("recent_production_score"))
        trend_score = clamp(r.get("trend_score"))
        situation_score = clamp(r.get("situation_score", 50))
        salary = clamp(r.get("salary"))
        years_exp = clamp(r.get("years_exp"))
        is_rookie = bool(r.get("is_rookie")) if not pd.isna(r.get("is_rookie")) else False

        # Market perception proxy from available rankings/value inputs.
        dynasty_rank_score = clamp(
            rank_score * 0.45
            + base_score * 0.35
            + engine_score * 0.20
        )

        production_reputation = clamp(
            recent_score * 0.45
            + career_score * 0.35
            + trend_score * 0.20
        )

        rookie_boost = 0.0
        if is_rookie or years_exp <= 1:
            rookie_boost = clamp(
                42
                + age_curve * 0.25
                + salary * 1.10
                + situation_score * 0.20
            )

        if pos == "QB":
            scarcity = 82
        elif pos == "TE":
            scarcity = 68
        elif pos == "WR":
            scarcity = 62
        elif pos == "RB":
            scarcity = 58
        else:
            scarcity = 45

        future_market = clamp(
            dynasty_rank_score * 0.45
            + age_curve * 0.25
            + trend_score * 0.15
            + rookie_boost * 0.15
        )

        internal_market_value = clamp(
            dynasty_rank_score * 0.38
            + engine_score * 0.20
            + future_market * 0.18
            + production_reputation * 0.12
            + scarcity * 0.07
            + rookie_boost * 0.05
        )

        consensus_score = clamp(r.get("market_consensus_score"))

        if consensus_score > 0:
            market_value = clamp(
                consensus_score * 0.75
                + internal_market_value * 0.25
            )
        else:
            market_value = internal_market_value

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": player_name,
            "pos": pos,
            "nfl_team": nfl_team,
            "market_value_score": round(market_value, 2),
            "dynasty_rank_score": round(dynasty_rank_score, 2),
            "engine_score": round(engine_score, 2),
            "age_curve_score": round(age_curve, 2),
            "rookie_boost_score": round(rookie_boost, 2),
            "production_reputation_score": round(production_reputation, 2),
            "positional_scarcity_score": round(scarcity, 2),
            "future_market_score": round(future_market, 2),
        }

        row["market_tier"] = market_tier(row["market_value_score"])
        row["market_confidence"] = confidence(row)
        row["market_summary"] = summary(row)

        rows.append(row)

    out = pd.DataFrame(rows)

    out = out.dropna(subset=["sleeper_id"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out.sort_values("market_value_score", ascending=False)
    out = out.drop_duplicates(subset=["sleeper_id"], keep="first")

    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    print(f"Prepared market value rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player_market_value_engine rows.")

    return out


if __name__ == "__main__":
    df = build_player_market_value_engine()

    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "pos",
                    "market_value_score",
                    "dynasty_rank_score",
                    "engine_score",
                    "future_market_score",
                    "rookie_boost_score",
                    "production_reputation_score",
                    "market_tier",
                    "market_confidence",
                ]
            ]
            .sort_values("market_value_score", ascending=False)
            .head(50)
            .to_string(index=False)
        )
