from __future__ import annotations

from auth import service_client
from datetime import datetime, UTC

TARGET_TABLE = "player_profile"


def safe_int(v):
    if v is None:
        return None
    try:
        return int(float(v))
    except Exception:
        return None


def build_player_profiles():
    sb = service_client()

    universe = (
        sb.table("player_universe")
        .select("*")
        .execute()
        .data
        or []
    )

    rows = []
    now = datetime.now(UTC).isoformat()

    for p in universe:
        rows.append({
            "sleeper_id": p.get("sleeper_id"),
            "player_name": p.get("player_name"),
            "position": p.get("pos"),
            "nfl_team": p.get("nfl_team"),

            "age": p.get("age"),
            "years_exp": safe_int(p.get("years_exp")),
            "career_stage": p.get("age_curve_stage"),

            "expected_ppg": p.get("expected_ppg"),
            "historical_ppg": p.get("historical_ppg"),
            "ceiling_ppg": None,
            "floor_ppg": None,
            "consistency_score": None,

            "salary": p.get("salary"),
            "contract_years": safe_int(p.get("years")),

            "contract_roi": p.get("contract_efficiency_score"),
            "contract_grade": p.get("contract_efficiency_grade"),

            "dynasty_score": p.get("dynasty_asset_score"),
            "market_score": p.get("market_consensus_score"),
            "future_score": p.get("future_projection_score"),

            "role_score": p.get("role_score"),
            "opportunity_score": p.get("opportunity_score"),
            "situation_score": p.get("situation_score"),

            "asset_category": "PLAYER",
            "asset_subtype": p.get("asset_subtype"),
            "market_pool": p.get("market_pool"),

            "trade_liquidity": None,
            "value_trend": None,
            "risk_level": None,
            "archetypes": [],

            "profile_summary": p.get("player_universe_summary"),
            "updated_at": now,
        })

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player profiles.")


if __name__ == "__main__":
    build_player_profiles()
