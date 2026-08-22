from __future__ import annotations

import pandas as pd

from auth import service_client


TARGET_TABLE = "player_snapshot"


def load_table(name: str) -> pd.DataFrame:
    sb = service_client()
    try:
        rows = sb.table(name).select("*").execute().data or []
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"Could not load {name}: {e}")
        return pd.DataFrame()


def clean_id(df: pd.DataFrame, col: str = "sleeper_id") -> pd.DataFrame:
    if not df.empty and col in df.columns:
        df[col] = df[col].astype(str).str.strip()
    return df


def build_player_snapshot() -> pd.DataFrame:
    roster = clean_id(load_table("roster"))
    intel = clean_id(load_table("player_intelligence"))

    if roster.empty:
        print("No roster rows found.")
        return pd.DataFrame()

    if intel.empty:
        print("No player_intelligence rows found.")
        return roster

    print(f"roster rows: {len(roster)}")
    print(f"player_intelligence rows: {len(intel)}")

    # Rename roster columns into canonical snapshot names
    roster = roster.rename(columns={
        "player": "player_name",
        "owner_team_name": "owner_team_name",
        "pos": "pos",
        "salary": "salary",
        "years": "years",
    })

    keep_intel_cols = [
        "sleeper_id",
        "engine_score",
        "engine_tier",
        "engine_summary",
        "career_score",
        "recent_production_score",
        "trend_score",
        "durability_score",
        "rank_score",
        "age_curve_score",
        "dynasty_rank",
        "position_rank",
        "tier",
        "recent_avg_ppg_ppr",
        "production_stability_score",
        "seasons_played",
        "recent_games",
        "trade_value_score",
        "contract_value_score",
        "roster_fit_score",
        "positional_need_score",
        "source",
    ]

    keep_intel_cols = [c for c in keep_intel_cols if c in intel.columns]
    intel = intel[keep_intel_cols].copy()

    snapshot = roster.merge(
        intel,
        on="sleeper_id",
        how="left",
    )

    # Keep only real rostered/contracted players.
    # Some roster rows are incomplete placeholders/free-agent artifacts.
    before_filter = len(snapshot)

    for col in ["sleeper_id", "player_name", "owner_team_name"]:
        if col in snapshot.columns:
            snapshot[col] = snapshot[col].astype(object)

    snapshot = snapshot[
        snapshot["owner_team_name"].notna()
        & snapshot["player_name"].notna()
        & snapshot["sleeper_id"].notna()
    ].copy()

    snapshot["owner_team_name"] = snapshot["owner_team_name"].astype(str).str.strip()
    snapshot["player_name"] = snapshot["player_name"].astype(str).str.strip()
    snapshot["sleeper_id"] = snapshot["sleeper_id"].astype(str).str.strip()

    snapshot = snapshot[
        (snapshot["owner_team_name"] != "")
        & (snapshot["player_name"] != "")
        & (snapshot["sleeper_id"] != "")
        & (snapshot["sleeper_id"].str.lower() != "none")
    ].copy()

    print(f"filtered invalid roster rows: {before_filter - len(snapshot)}")

    # Fill important numeric defaults
    numeric_defaults = {
        "salary": 0,
        "years": 0,
        "engine_score": 50,
        "recent_production_score": 0,
        "trend_score": 50,
        "durability_score": 50,
        "age_curve_score": 50,
        "trade_value_score": 50,
        "contract_value_score": 50,
        "roster_fit_score": 50,
        "positional_need_score": 0,
        "recent_games": 0,
        "recent_avg_ppg_ppr": 0,
    }

    for col, default in numeric_defaults.items():
        if col in snapshot.columns:
            snapshot[col] = pd.to_numeric(snapshot[col], errors="coerce").fillna(default)

    # Contract derived fields
    snapshot["total_contract_cost"] = snapshot["salary"] * snapshot["years"].clip(lower=1)
    snapshot["dead_cap_estimate"] = snapshot["salary"] * snapshot["years"] * 0.50

    # Basic blended score for downstream engines
    snapshot["blended_player_value"] = (
        snapshot["engine_score"] * 0.35
        + snapshot["recent_production_score"] * 0.25
        + snapshot["trade_value_score"] * 0.25
        + snapshot["contract_value_score"] * 0.15
    ).round(2)

    # ROI should stay on a roughly 0-100 scale.
    # Cheap contracts get credit, but $1 players shouldn't produce infinite ROI.
    snapshot["contract_roi_score"] = (
        snapshot["blended_player_value"]
        - (snapshot["salary"] * 1.10)
        - (snapshot["years"] * 2.0)
    ).clip(lower=0, upper=100).round(2)

    # Keep only useful canonical columns
    final_cols = [
        "sleeper_id",
        "player_name",
        "owner_team_name",
        "pos",
        "nfl_team",
        "salary",
        "years",
        "engine_score",
        "engine_tier",
        "engine_summary",
        "career_score",
        "recent_production_score",
        "recent_avg_ppg_ppr",
        "trend_score",
        "durability_score",
        "age_curve_score",
        "rank_score",
        "dynasty_rank",
        "position_rank",
        "tier",
        "trade_value_score",
        "contract_value_score",
        "roster_fit_score",
        "positional_need_score",
        "recent_games",
        "seasons_played",
        "production_stability_score",
        "total_contract_cost",
        "dead_cap_estimate",
        "blended_player_value",
        "contract_roi_score",
        "source",
    ]

    final_cols = [c for c in final_cols if c in snapshot.columns]
    snapshot = snapshot[final_cols].copy()

    print(f"snapshot rows: {len(snapshot)}")
    print(f"matched intel rows: {snapshot['engine_score'].notna().sum()}")

    return snapshot


def write_player_snapshot(df: pd.DataFrame):
    if df.empty:
        print("No snapshot rows to write.")
        return

    import numpy as np

    print("NaN counts before write:")
    print(df.isna().sum().sort_values(ascending=False).head(20))

    # Supabase/PostgREST JSON cannot accept NaN, inf, or -inf.
    clean = df.copy()
    clean = clean.replace([np.inf, -np.inf], None)
    clean = clean.astype(object).where(pd.notnull(clean), None)

    rows = clean.to_dict("records")

    sb = service_client()
    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="sleeper_id,owner_team_name",
    ).execute()

    print(f"Upserted {len(rows)} player_snapshot rows.")


if __name__ == "__main__":
    df = build_player_snapshot()
    print(df.head(25).to_string(index=False))
    write_player_snapshot(df)

