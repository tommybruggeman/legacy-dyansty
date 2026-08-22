from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows
from intelligence.contract_intelligence.contract_archetypes import contract_archetype

TARGET_TABLE = "player_contract_intelligence_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def deal_tier(pos_pct, overall_pct, salary):
    if overall_pct >= 95 or pos_pct >= 95:
        return "MARKET-SETTING DEAL"
    if overall_pct >= 85 or pos_pct >= 85:
        return "ELITE POSITIONAL DEAL"
    if overall_pct >= 70 or pos_pct >= 70:
        return "HIGH STARTER DEAL"
    if overall_pct >= 50 or pos_pct >= 50:
        return "STARTER DEAL"
    if salary >= 8:
        return "MID-TIER DEAL"
    return "VALUE / LOW-COST DEAL"


def build_player_contract_intelligence_engine():
    sb = service_client()

    contracts = pd.DataFrame(load_internal_contract_rows(sb))

    if contracts.empty:
        print("No contracts found.")
        return pd.DataFrame()

    contracts = contracts.rename(columns={
        "sleeper_player_id": "sleeper_id",
        "owner_name": "owner_team_name",
        "player_position": "pos",
        "contract_years_left": "years",
    })

    contracts["sleeper_id"] = contracts["sleeper_id"].astype(str)
    contracts["salary"] = pd.to_numeric(contracts["salary"], errors="coerce").fillna(0)
    contracts["years"] = pd.to_numeric(contracts["years"], errors="coerce").fillna(0)

    contracts = (
        contracts.sort_values(["sleeper_id", "owner_team_name", "salary", "years"], ascending=[True, True, False, False])
        .drop_duplicates(["sleeper_id", "owner_team_name"], keep="first")
    )

    df = contracts.copy()

    df["overall_salary_rank"] = df["salary"].rank(method="min", ascending=False)
    df["position_salary_rank"] = df.groupby("pos")["salary"].rank(method="min", ascending=False)

    total_players = max(len(df), 1)
    df["overall_salary_percentile"] = (1 - ((df["overall_salary_rank"] - 1) / total_players)) * 100

    df["position_salary_percentile"] = 0.0
    for pos, g in df.groupby("pos"):
        n = max(len(g), 1)
        idx = g.index
        df.loc[idx, "position_salary_percentile"] = (
            1 - ((df.loc[idx, "position_salary_rank"] - 1) / n)
        ) * 100

    pos_max_salary = df.groupby("pos")["salary"].transform("max").replace(0, np.nan)
    df["position_salary_share"] = (df["salary"] / pos_max_salary * 100).fillna(0)

    rows = []

    for _, r in df.iterrows():
        salary = clamp(r.get("salary"), 0, 999)
        years = clamp(r.get("years"), 0, 10)
        pos_pct = clamp(r.get("position_salary_percentile"))
        overall_pct = clamp(r.get("overall_salary_percentile"))
        pos_share = clamp(r.get("position_salary_share"))

        contract_pressure = clamp(
            pos_pct * 0.40
            + overall_pct * 0.35
            + max(0, years - 1) * 8
            + max(0, salary - 20) * 0.75
        )

        contract_context = clamp(
            pos_share * 0.35
            + pos_pct * 0.35
            + overall_pct * 0.20
            + years * 2.5
        )

        tier = deal_tier(pos_pct, overall_pct, salary)
        archetype = contract_archetype(
            pos=r.get("pos"),
            salary=salary,
            years=years,
            pos_rank=r.get("position_salary_rank"),
            pos_percentile=pos_pct,
        )

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "owner_team_name": r.get("owner_team_name"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),
            "salary": round(salary, 2),
            "years": round(years, 2),
            "position_salary_rank": int(r.get("position_salary_rank")),
            "overall_salary_rank": int(r.get("overall_salary_rank")),
            "position_salary_percentile": round(pos_pct, 2),
            "overall_salary_percentile": round(overall_pct, 2),
            "position_salary_share": round(pos_share, 2),
            "deal_size_tier": tier,
            "contract_context_score": round(contract_context, 2),
            "contract_pressure_score": round(contract_pressure, 2),
            "contract_archetype": archetype["contract_archetype"],
            "required_outcome": archetype["required_outcome"],
            "trade_liquidity_note": archetype["trade_liquidity_note"],
            "contract_window": archetype["contract_window"],
            "contract_expectation": archetype["expectation"],
            "contract_risk_note": archetype["risk_note"],
        }

        row["contract_summary"] = (
            f"{row['player_name']} is on a {tier} / {row['contract_archetype']}: "
            f"${row['salary']} for {row['years']} year(s). "
            f"That ranks #{row['position_salary_rank']} at {row['pos']} and #{row['overall_salary_rank']} overall. "
            f"Required outcome: {row['required_outcome']}. "
            f"Window: {row['contract_window']}. "
            f"Risk: {row['contract_risk_note']}."
        )

        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.dropna(subset=["sleeper_id", "owner_team_name"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out[out["owner_team_name"].astype(str).str.strip() != ""]
    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_contract_intelligence_engine rows.")

    return out


if __name__ == "__main__":
    df = build_player_contract_intelligence_engine()

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
                    "overall_salary_rank",
                    "position_salary_percentile",
                    "deal_size_tier",
                    "contract_pressure_score",
                ]
            ]
            .sort_values(["pos", "position_salary_rank"])
            .head(80)
            .to_string(index=False)
        )
