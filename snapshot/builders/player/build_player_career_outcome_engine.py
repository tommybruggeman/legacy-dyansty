from __future__ import annotations

import pandas as pd
from auth import service_client


# ============================
# SAFE ARCHETYPE ENGINE (CLEAN)
# ============================

def get_archetype(pos, age, peak, decline, salary):

    age_gap_peak = abs(age - peak)

    if salary > 40 and age <= peak:
        return "ELITE CORE CORNERSTONE"

    if pos == "QB" and age <= 30:
        return 
    if pos == "WR" and age <= 27:
        return 
    if pos == "RB" and age >= decline:
        return "AGING RB CLIFF CANDIDATE"

    if age_gap_peak <= 1:
        return "PEAK WINDOW PLAYER"

    if age < peak - 2:
        return 
    return 


def get_archetype(pos, age, peak, decline, salary):

    age_gap_peak = abs(age - peak)
    age_gap_decline = max(0, age - decline)

    if salary > 40 and age <= peak:
        return "ELITE CORE CORNERSTONE"

    if pos == "RB" and age >= decline:
        return "AGING RB CLIFF CANDIDATE"

    if pos == "WR" and age > decline:
        return "POST-PEAK DECLINING WR"

    if age_gap_peak <= 1:
        return "PEAK WINDOW PLAYER"

    if age < peak - 2:
        return 
    if pos == "QB":
        return "LONG-TERM QB VALUE CURVE"

    return 

TARGET_TABLE = "player_career_outcome_engine"


def build():
    sb = service_client()

    base = pd.DataFrame(
        sb.table("player_intelligence_base").select("*").execute().data or []
    )

    if base.empty:
        print("No base data")
        return

    base["sleeper_id"] = base["sleeper_id"].astype(str)
    base["pos"] = base["pos"].fillna("UNK")

    rows = []

    # -----------------------------
    # SIMPLE CAREER ARCHETYPE MODEL
    # -----------------------------
    for _, r in base.iterrows():

        pos = r["pos"]
        age = float(r.get("age", 27))
        salary = float(r.get("contract_salary", 0))

        # position curve priors
        if pos == "QB":
            longevity = 10
            decline_start = 32
            peak = 27

        elif pos == "WR":
            longevity = 7
            decline_start = 29
            peak = 26

        elif pos == "RB":
            longevity = 4
            decline_start = 26
            peak = 24

        elif pos == "TE":
            longevity = 8
            decline_start = 30
            peak = 27

        else:
            longevity = 6
            decline_start = 28
            peak = 26

        # compute lifecycle probabilities
        years_to_peak = max(0, peak - age)
        years_to_decline = max(0, decline_start - age)
        years_remaining = max(0, longevity - max(0, age - peak))

        # outcome probabilities (simple deterministic priors for now)
        elite_career_prob = max(0, 100 - abs(age - peak) * 6)
        decline_risk = max(0, (age - decline_start) * 10)

        rows.append({
            "sleeper_id": r["sleeper_id"],
            "player_name": r.get("player_name"),
            "pos": pos,
            "owner_team_name": r.get("owner_team_name"),

            "age": age,

            "position_peak_age": peak,
            "position_decline_age": decline_start,
            "estimated_career_length": longevity,

            "years_to_peak": years_to_peak,
            "years_to_decline": years_to_decline,
            "estimated_years_remaining": years_remaining,

            "elite_career_probability": elite_career_prob,
            "decline_risk_score": decline_risk,

            "career_archetype": get_archetype(pos, age, peak, decline_start, salary),
                
                
                
                
            "summary": f"{r.get('player_name')} is a {pos} with {years_remaining:.1f} years of peak-aligned value"
        })

    out = pd.DataFrame(rows)

    # ============================
    # USE CANONICAL IDENTITY LAYER
    # ============================

    identity = sb.table("player_identity_map")         .select("canonical_player_id,sleeper_id,player_name,pos")         .execute().data or []

    id_map = {str(x.get("sleeper_id")): x for x in identity}

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()

    df["player_name"] = df["sleeper_id"].map(lambda x: id_map.get(x, {}).get("player_name"))
    df["pos"] = df["sleeper_id"].map(lambda x: id_map.get(x, {}).get("pos"))

    df["player_name"] = df["player_name"].fillna("UNKNOWN PLAYER")


    sb.table(TARGET_TABLE).upsert(
        out.to_dict("records"),
        on_conflict="sleeper_id,owner_team_name"
    ).execute()

    print(f"Upserted {len(out)} career outcome rows")


if __name__ == "__main__":
    build()
