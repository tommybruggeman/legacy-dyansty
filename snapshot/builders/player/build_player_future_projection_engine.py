from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows

TARGET_TABLE = "player_future_projection_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def pos_longevity(pos):
    if pos == "QB":
        return 92
    if pos == "WR":
        return 82
    if pos == "TE":
        return 76
    if pos == "RB":
        return 58
    return 50


def tier(score):
    if score >= 90:
        return "ELITE FUTURE VALUE"
    if score >= 80:
        return "PREMIUM FUTURE VALUE"
    if score >= 70:
        return "STRONG FUTURE VALUE"
    if score >= 60:
        return "POSITIVE FUTURE VALUE"
    if score >= 45:
        return "UNCERTAIN FUTURE VALUE"
    if score >= 30:
        return "DECLINING FUTURE VALUE"
    return "LOW FUTURE VALUE"


def build_player_future_projection_engine():
    sb = service_client()

    engine = pd.DataFrame(sb.table("player_engine_scores").select("*").execute().data or [])
    market = pd.DataFrame(sb.table("player_market_value_engine").select("*").execute().data or [])
    consensus = pd.DataFrame(sb.table("player_market_consensus").select("*").execute().data or [])
    situation = pd.DataFrame(sb.table("player_situation_context").select("*").execute().data or [])
    contracts = pd.DataFrame(load_internal_contract_rows(sb))
    players = pd.DataFrame(sb.table("players").select("*").execute().data or [])

    if engine.empty:
        print("No player_engine_scores found.")
        return pd.DataFrame()

    engine["sleeper_id"] = engine["sleeper_id"].astype(str)

    if not market.empty:
        market["sleeper_id"] = market["sleeper_id"].astype(str)

    if not consensus.empty:
        consensus["sleeper_id"] = consensus["sleeper_id"].astype(str)

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)
        situation = (
            situation.sort_values(["sleeper_id", "situation_score"], ascending=[True, False])
            .drop_duplicates("sleeper_id", keep="first")
        )

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

    if not players.empty:
        players["sleeper_id"] = players["sleeper_id"].astype(str)

    df = engine.copy()

    if not market.empty:
        df = df.merge(
            market[["sleeper_id", "market_value_score"]],
            on="sleeper_id",
            how="left",
        )

    if not consensus.empty:
        df = df.merge(
            consensus[["sleeper_id", "market_consensus_score"]],
            on="sleeper_id",
            how="left",
        )

    if not situation.empty:
        df = df.merge(
            situation[[
                "sleeper_id",
                "situation_score",
                "situation_risk_score",
                "nfl_team",
                "age",
                "years_exp",
                "role_security_score",
                "depth_chart_pressure_score",
                "team_environment_score",
                "scheme_fit_score",
                "qb_environment_score",
                "offensive_line_score",
            ]],
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

        engine_score = clamp(r.get("engine_score"))
        market_value = clamp(r.get("market_value_score"))
        consensus_score = clamp(r.get("market_consensus_score"))
        recent_score = clamp(r.get("recent_production_score"))
        career_score = clamp(r.get("career_score_scaled", r.get("career_score")))
        trend_score = clamp(r.get("trend_score"))
        durability = clamp(r.get("durability_score"))
        age_curve = clamp(r.get("age_curve_score", 50))
        situation_score = clamp(r.get("situation_score", 50))
        situation_risk = clamp(r.get("situation_risk_score"))

        role_security = clamp(r.get("role_security_score", 50))
        depth_pressure = clamp(r.get("depth_chart_pressure_score", 50))
        team_environment = clamp(r.get("team_environment_score", 50))
        scheme_fit = clamp(r.get("scheme_fit_score", 50))
        qb_environment = clamp(r.get("qb_environment_score", 50))
        offensive_line = clamp(r.get("offensive_line_score", 50))

        offensive_ecosystem = clamp(
            team_environment * 0.30
            + scheme_fit * 0.25
            + qb_environment * 0.25
            + offensive_line * 0.20
        )

        stability_score = clamp(
            role_security * 0.45
            + (100 - depth_pressure) * 0.25
            + offensive_ecosystem * 0.30
        )

        if pos == "QB":
            stability_score = clamp(
                stability_score * 0.70
                + offensive_ecosystem * 0.30
            )
        salary = clamp(r.get("salary"))
        years_exp = clamp(r.get("years_exp"))
        is_rookie = bool(r.get("is_rookie")) if not pd.isna(r.get("is_rookie")) else False

        market_anchor = consensus_score if consensus_score > 0 else market_value
        longevity = pos_longevity(pos)

        production_momentum = clamp(
            recent_score * 0.45
            + trend_score * 0.30
            + career_score * 0.15
            + durability * 0.10
        )

        rookie_projection_boost = 0.0
        if is_rookie or years_exp <= 1:
            rookie_projection_boost = clamp(
                market_anchor * 0.45
                + salary * 1.20
                + age_curve * 0.20
                + situation_score * 0.15
            )

        market_trend = clamp(
            market_anchor * 0.50
            + trend_score * 0.20
            + engine_score * 0.15
            + rookie_projection_boost * 0.15
        )

        trajectory = clamp(
            market_trend * 0.24
            + production_momentum * 0.22
            + age_curve * 0.16
            + situation_score * 0.12
            + longevity * 0.08
            + stability_score * 0.18
            - situation_risk * 0.06
        )

        projection_1yr = clamp(
            production_momentum * 0.35
            + market_anchor * 0.25
            + situation_score * 0.15
            + engine_score * 0.15
            + durability * 0.10
        )

        projection_2yr = clamp(
            market_anchor * 0.24
            + trajectory * 0.24
            + age_curve * 0.13
            + longevity * 0.13
            + situation_score * 0.08
            + stability_score * 0.13
            + rookie_projection_boost * 0.05
        )

        projection_3yr = clamp(
            market_anchor * 0.24
            + age_curve * 0.18
            + trajectory * 0.17
            + longevity * 0.14
            + stability_score * 0.14
            + rookie_projection_boost * 0.08
            + situation_score * 0.05
        )

        future_projection = clamp(
            projection_1yr * 0.35
            + projection_2yr * 0.35
            + projection_3yr * 0.30
        )

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": name,
            "pos": pos,
            "nfl_team": nfl_team,
            "projection_1yr_score": round(projection_1yr, 2),
            "projection_2yr_score": round(projection_2yr, 2),
            "projection_3yr_score": round(projection_3yr, 2),
            "future_projection_score": round(future_projection, 2),
            "trajectory_score": round(trajectory, 2),
            "age_curve_score": round(age_curve, 2),
            "market_trend_score": round(market_trend, 2),
            "situation_score": round(situation_score, 2),
            "production_momentum_score": round(production_momentum, 2),
            "durability_score": round(durability, 2),
            "stability_score": round(stability_score, 2),
            "offensive_ecosystem_score": round(offensive_ecosystem, 2),
        }

        row["future_tier"] = tier(row["future_projection_score"])
        row["future_summary"] = (
            f"{name} future projection {row['future_projection_score']}: {row['future_tier']}. "
            f"1yr {row['projection_1yr_score']}, 2yr {row['projection_2yr_score']}, "
            f"3yr {row['projection_3yr_score']}, trajectory {row['trajectory_score']}, "
            f"market trend {row['market_trend_score']}, production momentum {row['production_momentum_score']}."
        )

        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.dropna(subset=["sleeper_id"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out.sort_values("future_projection_score", ascending=False)
    out = out.drop_duplicates("sleeper_id", keep="first")
    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    if rows:
        sb.table(TARGET_TABLE).upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(rows)} player_future_projection_engine rows.")
    return out


if __name__ == "__main__":
    df = build_player_future_projection_engine()
    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "pos",
                    "projection_1yr_score",
                    "projection_2yr_score",
                    "projection_3yr_score",
                    "future_projection_score",
                    "trajectory_score",
                    "market_trend_score",
                    "production_momentum_score",
                    "future_tier",
                ]
            ]
            .sort_values("future_projection_score", ascending=False)
            .head(50)
            .to_string(index=False)
        )
