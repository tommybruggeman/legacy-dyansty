from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client

TARGET_TABLE = "player_value_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def tier(score):
    if score >= 92:
        return "ELITE CORNERSTONE"
    if score >= 85:
        return "FOUNDATIONAL STAR"
    if score >= 75:
        return "HIGH-END STARTER"
    if score >= 65:
        return "STRONG ASSET"
    if score >= 55:
        return "USEFUL STARTER"
    if score >= 45:
        return "DEPTH / FLEX"
    if score >= 35:
        return "FRINGE"
    return "REPLACEMENT"


def summary(row):
    return (
        f"{row['player_name']} value {row['player_value_score']}: "
        f"{row['value_tier']}. "
        f"Dynasty {row['dynasty_asset_score']}, "
        f"win-now {row['win_now_score']}, "
        f"production {row['production_score']}, "
        f"future {row['future_value_score']}, "
        f"situation {row['situation_score']}, "
        f"risk-adjusted {row['risk_adjusted_score']}."
    )


def build_player_value_engine():
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

    market = pd.DataFrame(
        sb.table("player_market_value_engine").select("*").execute().data or []
    )

    if engine.empty:
        print("No player_engine_scores found.")
        return pd.DataFrame()

    engine["sleeper_id"] = engine["sleeper_id"].astype(str)

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)

        situation = (
            situation.sort_values(["sleeper_id", "situation_score"], ascending=[True, False])
            .drop_duplicates(subset=["sleeper_id"], keep="first")
        )

    if not players.empty:
        players["sleeper_id"] = players["sleeper_id"].astype(str)

    if not market.empty:
        market["sleeper_id"] = market["sleeper_id"].astype(str)

    df = engine.copy()

    if not situation.empty:
        df = df.merge(
            situation[
                [
                    "sleeper_id",
                    "situation_score",
                    "situation_risk_score",
                    "nfl_team",
                    "nfl_status",
                    "age",
                    "years_exp",
                    "injury_status",
                ]
            ],
            on="sleeper_id",
            how="left",
            suffixes=("", "_situation"),
        )

    if not players.empty:
        df = df.merge(
            players.rename(columns={
                "full_name": "players_full_name",
                "team": "players_team",
                "position": "players_position",
            })[
                ["sleeper_id", "players_full_name", "players_team", "players_position"]
            ],
            on="sleeper_id",
            how="left",
        )

    if not market.empty:
        df = df.merge(
            market[[
                "sleeper_id",
                "market_value_score",
                "market_tier",
                "market_confidence",
            ]],
            on="sleeper_id",
            how="left",
        )

    rows = []

    for _, r in df.iterrows():
        player_name = r.get("player_name") or r.get("players_full_name")
        pos = r.get("pos") or r.get("players_position")
        nfl_team = r.get("nfl_team") or r.get("team") or r.get("players_team")

        engine_score = clamp(r.get("engine_score"))
        base_score = clamp(r.get("base_player_score"))
        rank_score = clamp(r.get("rank_score"))
        career_score = clamp(r.get("career_score_scaled", r.get("career_score")))
        recent_score = clamp(r.get("recent_production_score"))
        trend_score = clamp(r.get("trend_score"))
        durability = clamp(r.get("durability_score"))
        age_curve = clamp(r.get("age_curve_score", 50))
        situation_score = clamp(r.get("situation_score", 50))
        situation_risk = clamp(r.get("situation_risk_score"))

        dynasty_asset = clamp(
            engine_score * 0.40
            + base_score * 0.25
            + rank_score * 0.20
            + age_curve * 0.15
        )

        win_now = clamp(
            recent_score * 0.40
            + engine_score * 0.25
            + career_score * 0.15
            + situation_score * 0.12
            + durability * 0.08
        )

        production = clamp(
            recent_score * 0.50
            + career_score * 0.25
            + trend_score * 0.15
            + durability * 0.10
        )

        future_value = clamp(
            dynasty_asset * 0.45
            + age_curve * 0.30
            + trend_score * 0.15
            + situation_score * 0.10
        )

        market_value = clamp(r.get("market_value_score"))

        if market_value > 0:
            raw_value = clamp(
                market_value * 0.35
                + dynasty_asset * 0.22
                + win_now * 0.18
                + production * 0.12
                + future_value * 0.08
                + situation_score * 0.05
            )
        else:
            raw_value = clamp(
                dynasty_asset * 0.32
                + win_now * 0.25
                + production * 0.18
                + future_value * 0.15
                + situation_score * 0.10
            )

        risk_penalty = clamp(
            (100 - durability) * 0.18
            + situation_risk * 0.12
            + max(0, 45 - trend_score) * 0.10
        )

        risk_adjusted = clamp(raw_value - risk_penalty)

        player_value = clamp(
            raw_value * 0.70
            + risk_adjusted * 0.30
        )

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": player_name,
            "pos": pos,
            "nfl_team": nfl_team,
            "player_value_score": round(player_value, 2),
            "dynasty_asset_score": round(dynasty_asset, 2),
            "win_now_score": round(win_now, 2),
            "production_score": round(production, 2),
            "future_value_score": round(future_value, 2),
            "risk_adjusted_score": round(risk_adjusted, 2),
            "situation_score": round(situation_score, 2),
            "age_curve_score": round(age_curve, 2),
            "durability_score": round(durability, 2),
            "career_score": round(career_score, 2),
            "recent_production_score": round(recent_score, 2),
        }

        row["value_tier"] = tier(row["player_value_score"])
        row["value_summary"] = summary(row)

        rows.append(row)

    out = pd.DataFrame(rows)

    out = out.dropna(subset=["sleeper_id"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out.sort_values("player_value_score", ascending=False)
    out = out.drop_duplicates(subset=["sleeper_id"], keep="first")

    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    print(f"Prepared player value rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player_value_engine rows.")

    return out


if __name__ == "__main__":
    df = build_player_value_engine()

    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "pos",
                    "player_value_score",
                    "dynasty_asset_score",
                    "win_now_score",
                    "production_score",
                    "future_value_score",
                    "situation_score",
                    "value_tier",
                ]
            ]
            .sort_values("player_value_score", ascending=False)
            .head(40)
            .to_string(index=False)
        )
