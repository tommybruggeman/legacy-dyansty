from __future__ import annotations

# ============================================================
# Imports
# ============================================================

import os
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv


# ============================================================
# Supabase Client
# ============================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# Fetch Helpers
# ============================================================

def fetch_identity_map() -> pd.DataFrame:
    res = (
        sb.table("player_identity_map")
        .select("*")
        .eq("source", "contracts")
        .execute()
    )
    return pd.DataFrame(res.data or [])


# ============================================================
# Build Rankings
# ============================================================

def build_player_rankings(identity: pd.DataFrame) -> pd.DataFrame:
    df = identity.copy()

    df = identity.copy()

    df["sleeper_id"] = pd.to_numeric(
        df["sleeper_id"],
        errors="coerce"
    )

    df = df.dropna(
        subset=["sleeper_id"]
    ).copy()

    df["sleeper_id"] = (
        df["sleeper_id"]
        .astype(int)
        .astype(str)
        .str.strip()
    )
    df["player"] = df["player_name"]
    df["pos"] = df["pos"].astype(str).str.upper().str.strip()

    if "id" in df.columns:
        df["dynasty_rank"] = pd.to_numeric(df["id"], errors="coerce")
    else:
        df["dynasty_rank"] = range(1, len(df) + 1)

    df["position_rank"] = (
        df.groupby("pos")["dynasty_rank"]
        .rank(method="first")
        .astype(int)
    )

    df["tier"] = (
        ((df["dynasty_rank"] - 1) // 12) + 1
    ).astype(int)

    max_rank = df["dynasty_rank"].max()

    df["base_player_score"] = (
        100 - ((df["dynasty_rank"] - 1) / max(max_rank - 1, 1) * 100)
    ).round(2)

    df["source"] = "dynastyprocess"

    output_cols = [
        "sleeper_id",
        "player",
        "pos",
        "dynasty_rank",
        "position_rank",
        "tier",
        "base_player_score",
        "source",
    ]

    return df[output_cols].copy()


# ============================================================
# Main
# ============================================================

def main():
    identity = fetch_identity_map()
    print(f"Fetched DynastyProcess identity rows: {len(identity)}")

    rankings = build_player_rankings(identity)
    print(f"Built player ranking rows: {len(rankings)}")

    if rankings.empty:
        print("No rankings built. Skipping upsert.")
        return

    records = rankings.where(pd.notnull(rankings), None).to_dict("records")

    sb.table("player_rankings").upsert(
        records,
        on_conflict="sleeper_id"
    ).execute()

    print("Upserted player_rankings.")


if __name__ == "__main__":
    main()