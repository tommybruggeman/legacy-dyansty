from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client
from intelligence.contract_intelligence.expectation_models import position_bands, verdict

TARGET_TABLE = "player_expectation_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def build_player_expectation_engine():
    sb = service_client()

    contract = pd.DataFrame(sb.table("player_contract_intelligence_engine").select("*").execute().data or [])
    future = pd.DataFrame(sb.table("player_future_projection_engine").select("*").execute().data or [])
    market = pd.DataFrame(sb.table("player_market_value_engine").select("*").execute().data or [])
    value = pd.DataFrame(sb.table("player_value_engine").select("*").execute().data or [])

    if contract.empty:
        print("No contract intelligence rows found.")
        return pd.DataFrame()

    for df in [contract, future, market, value]:
        if not df.empty and "sleeper_id" in df.columns:
            df["sleeper_id"] = df["sleeper_id"].astype(str)

    df = contract.copy()
    df["sleeper_id"] = df["sleeper_id"].astype(str)

    if not future.empty:
        df = df.merge(
            future[[
                "sleeper_id",
                "future_projection_score",
                "projection_1yr_score",
                "trajectory_score",
                "production_momentum_score",
            ]],
            on="sleeper_id",
            how="left",
        )

    if not market.empty:
        df = df.merge(
            market[["sleeper_id", "market_value_score"]],
            on="sleeper_id",
            how="left",
        )

    if not value.empty:
        df = df.merge(
            value[[
                "sleeper_id",
                "player_value_score",
                "win_now_score",
                "production_score",
                "dynasty_asset_score",
            ]],
            on="sleeper_id",
            how="left",
        )

    rows = []

    for _, r in df.iterrows():
        pos = r.get("pos")
        rank = int(clamp(r.get("position_salary_rank"), 1, 999))
        salary = clamp(r.get("salary"), 0, 999)
        years = clamp(r.get("years"), 0, 10)

        bands = position_bands(pos, rank)

        contract_pressure = clamp(r.get("contract_pressure_score"))
        future_score = clamp(r.get("future_projection_score"))
        one_year = clamp(r.get("projection_1yr_score"))
        market_score = clamp(r.get("market_value_score"))
        player_value = clamp(r.get("player_value_score"))
        win_now = clamp(r.get("win_now_score"))
        production = clamp(r.get("production_score"))
        dynasty = clamp(r.get("dynasty_asset_score"))
        momentum = clamp(r.get("production_momentum_score"))

        current_delivery = clamp(
            one_year * 0.25
            + win_now * 0.25
            + production * 0.20
            + player_value * 0.15
            + momentum * 0.15
        )

        long_term_delivery = clamp(
            future_score * 0.35
            + dynasty * 0.25
            + market_score * 0.25
            + player_value * 0.15
        )

        expectation_fit = clamp(
            current_delivery * 0.55
            + long_term_delivery * 0.45
            - contract_pressure * 0.20
        )

        underperformance_risk = clamp(
            contract_pressure * 0.45
            + max(0, 70 - current_delivery) * 0.35
            + max(0, 65 - long_term_delivery) * 0.20
        )

        pressure = clamp(
            contract_pressure * 0.70
            + max(0, years - 1) * 8
            + max(0, salary - 20) * 0.60
        )

        expectation_verdict = verdict(expectation_fit, underperformance_risk, pressure)

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "owner_team_name": r.get("owner_team_name"),
            "player_name": r.get("player_name"),
            "pos": pos,
            "salary": round(salary, 2),
            "years": round(years, 2),
            "position_salary_rank": rank,
            "deal_size_tier": r.get("deal_size_tier"),
            "contract_archetype": r.get("contract_archetype"),
            "required_outcome": r.get("required_outcome"),
            "expected_finish_band": bands["expected"],
            "minimum_acceptable_band": bands["minimum"],
            "bust_threshold_band": bands["bust"],
            "league_winner_band": bands["league_winner"],
            "expectation_pressure_score": round(pressure, 2),
            "expectation_fit_score": round(expectation_fit, 2),
            "underperformance_risk_score": round(underperformance_risk, 2),
            "expectation_verdict": expectation_verdict,
        }

        row["expectation_summary"] = (
            f"{row['player_name']} is paid as {row['pos']}#{row['position_salary_rank']} "
            f"on a {row['contract_archetype']} contract. Expected outcome: {row['expected_finish_band']}. "
            f"Minimum acceptable: {row['minimum_acceptable_band']}. "
            f"Bust threshold: {row['bust_threshold_band']}. "
            f"Expectation fit {row['expectation_fit_score']}, underperformance risk {row['underperformance_risk_score']}. "
            f"Verdict: {row['expectation_verdict']}."
        )

        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.dropna(subset=["sleeper_id", "owner_team_name"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out[out["owner_team_name"].astype(str).str.strip() != ""]
    out = out.sort_values("underperformance_risk_score", ascending=False)
    out = out.drop_duplicates(["sleeper_id", "owner_team_name"], keep="first")
    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_expectation_engine rows.")
    return out


if __name__ == "__main__":
    df = build_player_expectation_engine()

    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "owner_team_name",
                    "pos",
                    "salary",
                    "years",
                    "position_salary_rank",
                    "expected_finish_band",
                    "minimum_acceptable_band",
                    "expectation_fit_score",
                    "underperformance_risk_score",
                    "expectation_verdict",
                ]
            ]
            .head(60)
            .to_string(index=False)
        )
