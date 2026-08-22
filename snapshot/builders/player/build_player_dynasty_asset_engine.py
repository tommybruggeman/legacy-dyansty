from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows

TARGET_TABLE = "player_dynasty_asset_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def longevity(pos):
    if pos == "QB":
        return 95
    if pos == "WR":
        return 82
    if pos == "TE":
        return 74
    if pos == "RB":
        return 58
    return 50


def tier(score):
    if score >= 95:
        return "ELITE DYNASTY CORNERSTONE"
    if score >= 88:
        return "PREMIUM DYNASTY ASSET"
    if score >= 78:
        return "HIGH-END DYNASTY ASSET"
    if score >= 68:
        return "STRONG DYNASTY ASSET"
    if score >= 55:
        return "USEFUL DYNASTY PIECE"
    if score >= 40:
        return "FRINGE DYNASTY VALUE"
    return "REPLACEMENT DYNASTY VALUE"


def build_player_dynasty_asset_engine():
    sb = service_client()

    engine = pd.DataFrame(sb.table("player_engine_scores").select("*").execute().data or [])
    market = pd.DataFrame(sb.table("player_market_consensus").select("*").execute().data or [])
    market_value = pd.DataFrame(sb.table("player_market_value_engine").select("*").execute().data or [])
    situation = pd.DataFrame(sb.table("player_situation_context").select("*").execute().data or [])
    contracts = pd.DataFrame(load_internal_contract_rows(sb))
    players = pd.DataFrame(sb.table("players").select("*").execute().data or [])

    if engine.empty:
        print("No player_engine_scores found.")
        return pd.DataFrame()

    for df in [engine, market, market_value, situation, contracts, players]:
        if not df.empty and "sleeper_id" in df.columns:
            df["sleeper_id"] = df["sleeper_id"].astype(str)

    if not contracts.empty:
        contracts = contracts.rename(columns={
            "sleeper_player_id": "sleeper_id",
            "player_position": "contract_pos",
        })
        contracts["sleeper_id"] = contracts["sleeper_id"].astype(str)
        contracts = (
            contracts.sort_values(["sleeper_id", "salary"], ascending=[True, False])
            .drop_duplicates("sleeper_id", keep="first")
        )

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)
        situation = (
            situation.sort_values(["sleeper_id", "situation_score"], ascending=[True, False])
            .drop_duplicates("sleeper_id", keep="first")
        )

    df = engine.copy()
    df["sleeper_id"] = df["sleeper_id"].astype(str)

    if not market.empty:
        df = df.merge(
            market[["sleeper_id", "market_consensus_score"]],
            on="sleeper_id",
            how="left",
        )

    if not market_value.empty:
        df = df.merge(
            market_value[["sleeper_id", "market_value_score"]],
            on="sleeper_id",
            how="left",
        )

    if not situation.empty:
        df = df.merge(
            situation[["sleeper_id", "situation_score", "nfl_team", "age", "years_exp"]],
            on="sleeper_id",
            how="left",
        )

    if not contracts.empty:
        df = df.merge(
            contracts[["sleeper_id", "is_rookie", "salary", "contract_pos"]],
            on="sleeper_id",
            how="left",
        )

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

    rows = []

    for _, r in df.iterrows():
        name = r.get("player_name") or r.get("players_full_name")
        pos = r.get("pos") or r.get("contract_pos") or r.get("players_position")
        nfl_team = r.get("nfl_team") or r.get("team") or r.get("players_team")

        consensus = clamp(r.get("market_consensus_score"))
        market_score = clamp(r.get("market_value_score"))
        age_curve = clamp(r.get("age_curve_score", 50))
        situation_score = clamp(r.get("situation_score", 50))
        trend = clamp(r.get("trend_score"))
        engine_score = clamp(r.get("engine_score"))
        salary = clamp(r.get("salary"))
        years_exp = clamp(r.get("years_exp"))
        is_rookie = bool(r.get("is_rookie")) if not pd.isna(r.get("is_rookie")) else False

        market_anchor = consensus if consensus > 0 else market_score

        position_longevity = longevity(pos)

        future_projection = clamp(
            market_anchor * 0.35
            + age_curve * 0.25
            + trend * 0.15
            + engine_score * 0.15
            + situation_score * 0.10
        )

        rookie_asset = 0.0
        if is_rookie or years_exp <= 1:
            rookie_asset = clamp(
                market_anchor * 0.45
                + salary * 1.25
                + age_curve * 0.20
                + situation_score * 0.15
                + position_longevity * 0.10
            )

        dynasty_asset = clamp(
            market_anchor * 0.42
            + age_curve * 0.16
            + future_projection * 0.16
            + position_longevity * 0.10
            + situation_score * 0.08
            + rookie_asset * 0.08
        )

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": name,
            "pos": pos,
            "nfl_team": nfl_team,
            "dynasty_asset_score": round(dynasty_asset, 2),
            "market_consensus_score": round(market_anchor, 2),
            "age_curve_score": round(age_curve, 2),
            "future_projection_score": round(future_projection, 2),
            "position_longevity_score": round(position_longevity, 2),
            "situation_score": round(situation_score, 2),
            "rookie_asset_score": round(rookie_asset, 2),
        }

        row["dynasty_tier"] = tier(row["dynasty_asset_score"])
        row["dynasty_summary"] = (
            f"{name} dynasty asset {row['dynasty_asset_score']}: {row['dynasty_tier']}. "
            f"Market {row['market_consensus_score']}, future {row['future_projection_score']}, "
            f"age curve {row['age_curve_score']}, position longevity {row['position_longevity_score']}, "
            f"situation {row['situation_score']}, rookie asset {row['rookie_asset_score']}."
        )

        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.dropna(subset=["sleeper_id"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out.sort_values("dynasty_asset_score", ascending=False)
    out = out.drop_duplicates("sleeper_id", keep="first")
    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    if rows:
        sb.table(TARGET_TABLE).upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(rows)} player_dynasty_asset_engine rows.")
    return out


if __name__ == "__main__":
    df = build_player_dynasty_asset_engine()
    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "pos",
                    "dynasty_asset_score",
                    "market_consensus_score",
                    "future_projection_score",
                    "position_longevity_score",
                    "situation_score",
                    "rookie_asset_score",
                    "dynasty_tier",
                ]
            ]
            .sort_values("dynasty_asset_score", ascending=False)
            .head(50)
            .to_string(index=False)
        )
