from __future__ import annotations

import pandas as pd
from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows

TARGET_TABLE = "player_market_career_alignment_engine"


def clamp(x):
    try:
        return float(max(0, min(100, float(x))))
    except Exception:
        return 50.0


def build():
    sb = service_client()

    market = pd.DataFrame(
        sb.table("player_market_consensus").select("*").execute().data or []
    )

    career = pd.DataFrame(
        sb.table("player_career_outcome_engine").select("*").execute().data or []
    )

    forecast = pd.DataFrame(
        sb.table("player_outcome_forecast_engine").select("*").execute().data or []
    )

    if market.empty or career.empty:
        print("Missing input tables")
        return

    market["sleeper_id"] = market["sleeper_id"].astype(str)
    career["sleeper_id"] = career["sleeper_id"].astype(str)
    forecast["sleeper_id"] = forecast["sleeper_id"].astype(str) if not forecast.empty else None

    df = market.merge(career, on="sleeper_id", how="left")

    rows = []

    for _, r in df.iterrows():

        market_score = clamp(r.get("market_consensus_score", 50))
        career_score = clamp(100 - r.get("decline_risk_score", 50))

        peak = r.get("position_peak_age", 27)
        age = r.get("age", 27)

        age_pressure = abs(age - peak) * 5

        true_value = (career_score * 0.6) + (market_score * 0.4) - age_pressure

        market_gap = market_score - true_value

        if market_gap > 10:
            verdict = "OVERVALUED"
        elif market_gap < -10:
            verdict = "UNDERVALUED"
        else:
            verdict = "FAIR VALUE"

        rows.append({
            "sleeper_id": r["sleeper_id"],
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),

            "market_score": market_score,
            "career_score": career_score,
            "true_value_score": true_value,
            "market_gap": market_gap,

            "alignment_verdict": verdict,

            "career_archetype": r.get("career_archetype"),
            "age": age,

            "trade_signal":
                "BUY" if verdict == "UNDERVALUED" else
                "SELL" if verdict == "OVERVALUED" else
                "HOLD",

            "trade_rationale": f"{r.get('player_name')} is {verdict} with gap {market_gap:.1f}"
        })

    out = pd.DataFrame(rows)

    # ============================
    # CANONICAL IDENTITY JOIN
    # ============================

    identity = sb.table("player_identity_map")         .select("sleeper_id,player_name,pos")         .execute().data or []

    id_map = {str(x["sleeper_id"]).strip(): x for x in identity}

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()

    df["player_name"] = df["sleeper_id"].map(lambda x: id_map.get(x, {}).get("player_name"))
    df["pos"] = df["sleeper_id"].map(lambda x: id_map.get(x, {}).get("pos"))

    df["player_name"] = df["player_name"].fillna("UNKNOWN PLAYER")


    # ============================
    # CANONICAL ID RESOLUTION LAYER (FINAL FIX)
    # ============================

    contracts = load_internal_contract_rows(sb)

    players = sb.table("players")         .select("sleeper_id, full_name")         .execute().data or []

    contract_map = {str(x["sleeper_player_id"]).strip(): x.get("player_name") for x in contracts}
    player_map = {str(x["sleeper_id"]).strip(): x.get("full_name") for x in players}

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()

    # try contract first, fallback to player table
    df["player_name"] = df["sleeper_id"].map(contract_map)
    df["player_name"] = df["player_name"].fillna(df["sleeper_id"].map(player_map))

    df["player_name"] = df["player_name"].fillna("UNKNOWN PLAYER")


    # ============================
    # UNIFIED PLAYER RESOLUTION LAYER
    # ============================

    contracts_players = load_internal_contract_rows(sb)

    contract_map = {
        str(x["sleeper_player_id"]).strip(): x.get("player_name")
        for x in contracts_players
    }

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()

    df["player_name"] = df["sleeper_id"].map(contract_map)

    df["player_name"] = df["player_name"].fillna("UNKNOWN PLAYER")


    # ============================
    # GLOBAL ID NORMALIZATION LAYER
    # ============================

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()

    # normalize players table BEFORE mapping
    players = sb.table("players").select("sleeper_id,full_name").execute().data or []
    players = {str(x["sleeper_id"]).strip(): x["full_name"] for x in players}

    df["player_name"] = df["sleeper_id"].map(players)

    # fallback safety
    df["player_name"] = df["player_name"].fillna(
        df.get("player_name")
    )
    df["player_name"] = df["player_name"].fillna("UNKNOWN PLAYER")


    # ============================
    # HARD NAME RESOLUTION LAYER
    # ============================

    if "player_name" not in df.columns or df["player_name"].isna().all():
        fallback_names = sb.table("players").select("sleeper_id,full_name").execute().data or []
        name_map = {x["sleeper_id"]: x["full_name"] for x in fallback_names}

        df["player_name"] = df["sleeper_id"].map(name_map)

    df["player_name"] = df["player_name"].fillna("UNKNOWN PLAYER")


    sb.table(TARGET_TABLE).upsert(
        out.to_dict("records"),
        on_conflict="sleeper_id"
    ).execute()

    print(f"Upserted {len(out)} market-career alignment rows")


if __name__ == "__main__":
    build()
