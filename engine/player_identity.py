from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from auth import service_client
# ============================================================
# Player Identity
#
# Seeds and repairs player_identity_map from contract data.
#
# This file is for identity maintenance, not validation.
# Use validate_identity_matches.py to audit match health.
# ============================================================

import pandas as pd

from auth import service_client


# ============================================================
# Supabase Client
# ============================================================

supabase = service_client()


# ============================================================
# Helpers
# ============================================================

def normalize_name(value) -> str:
    return (
        str(value or "")
        .lower()
        .replace(".", "")
        .replace("'", "")
        .replace("-", " ")
        .replace(" jr", "")
        .replace(" sr", "")
        .replace(" ii", "")
        .replace(" iii", "")
        .strip()
    )


def clean_sleeper_id(value) -> str | None:
    if value is None:
        return None

    sleeper_id = str(value).strip()

    if not sleeper_id:
        return None

    if sleeper_id.lower() in ["nan", "none", "null"]:
        return None

    if sleeper_id.startswith("manual_"):
        return None

    return sleeper_id


def clean_pos(value) -> str | None:
    if value is None:
        return None

    pos = str(value).upper().strip()

    if not pos or pos in ["NAN", "NONE", "NULL"]:
        return None

    return pos


# ============================================================
# Loaders
# ============================================================

def load_identity_map() -> pd.DataFrame:
    rows = (
        supabase
        .table("player_identity_map")
        .select("*")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)


def load_contract_players() -> pd.DataFrame:
    rows = (
        supabase
        .table("contracts")
        .select(
            "sleeper_player_id, player_name, player_position"
        )
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)


# ============================================================
# Build Identity Rows From Contracts
# ============================================================

def build_identity_rows_from_contracts(
    contracts: pd.DataFrame,
) -> list[dict]:
    if contracts.empty:
        return []

    df = contracts.copy()

    df["clean_sleeper_id"] = df["sleeper_player_id"].apply(
        clean_sleeper_id
    )

    df["clean_pos"] = df["player_position"].apply(
        clean_pos
    )

    df = df.dropna(
        subset=[
            "clean_sleeper_id",
            "player_name",
        ]
    ).copy()

    df = df.drop_duplicates(
        subset=["clean_sleeper_id"]
    )

    rows = []

    for _, r in df.iterrows():
        sleeper_id = str(r["clean_sleeper_id"])

        rows.append(
            {
                "canonical_player_id": sleeper_id,
                "sleeper_id": sleeper_id,
                "player_name": r.get("player_name"),
                "normalized_name": normalize_name(
                    r.get("player_name")
                ),
                "pos": r.get("clean_pos"),
                "source": "contracts",
                "confidence": 1,
            }
        )

    return rows


# ============================================================
# Seed / Repair Identity Map
# ============================================================

def seed_identity_from_contracts():
    contracts = load_contract_players()

    if contracts.empty:
        print("No contracts found.")
        return pd.DataFrame()

    rows = build_identity_rows_from_contracts(contracts)

    if not rows:
        print("No clean contract identities found.")
        return pd.DataFrame()

    supabase.table("player_identity_map").upsert(
        rows,
        on_conflict="canonical_player_id",
    ).execute()

    print(f"Seeded {len(rows)} identity rows from contracts.")

    return pd.DataFrame(rows)


# ============================================================
# Main Test
# ============================================================

if __name__ == "__main__":
    seeded = seed_identity_from_contracts()

    identity = load_identity_map()

    print("")
    print("Seeded rows:")
    print(seeded.head())

    print("")
    print(f"Identity rows total: {len(identity)}")