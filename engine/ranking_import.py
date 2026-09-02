from __future__ import annotations

# ============================================================
# Ranking Import
#
# Imports DynastyProcess values into:
# - player_rankings
# - player_identity_map
# ============================================================

from io import StringIO

import pandas as pd
import requests

from auth import service_client


# ============================================================
# Config
# ============================================================

CANDIDATE_URLS = [
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values.csv",
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values-players.csv",
]


# ============================================================
# Supabase Client
# ============================================================

supabase = service_client()


# ============================================================
# Load DynastyProcess Values
# ============================================================

def load_dynasty_process_values() -> pd.DataFrame:
    last_error = None

    for url in CANDIDATE_URLS:
        try:
            res = requests.get(
                url,
                timeout=30,
            )

            res.raise_for_status()

            print(f"Loaded DynastyProcess values from: {url}")

            return pd.read_csv(StringIO(res.text))

        except Exception as e:
            last_error = e

    raise RuntimeError(
        f"Could not load DynastyProcess values. Last error: {last_error}"
    )


# ============================================================
# Normalize Names
# ============================================================

def normalize_name(value) -> str:
    return (
        str(value or "")
        .lower()
        .replace(".", "")
        .replace("'", "")
        .replace("-", " ")
        .strip()
    )


# ============================================================
# Load Contracts For Sleeper ID Matching
# ============================================================

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

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.dropna(
        subset=["sleeper_player_id"]
    )

    df["sleeper_player_id"] = (
        df["sleeper_player_id"]
        .astype(str)
    )

    df = df[
        ~df["sleeper_player_id"]
        .str.startswith(
            "manual_",
            na=False,
        )
    ].copy()

    df["name_key"] = (
        df["player_name"]
        .apply(normalize_name)
    )

    df["pos_key"] = (
        df["player_position"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    return df.drop_duplicates(
        subset=["sleeper_player_id"]
    )


# ============================================================
# Transform DynastyProcess To Player Rankings
# ============================================================

def transform_dynasty_process_values(
    df: pd.DataFrame,
) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    out["player"] = (
        out["player"]
        .astype(str)
        .str.strip()
    )

    out["pos"] = (
        out["pos"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    out["team"] = (
        out["team"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    out["name_key"] = (
        out["player"]
        .apply(normalize_name)
    )

    out["pos_key"] = out["pos"]

    out["value_2qb"] = pd.to_numeric(
        out["value_2qb"],
        errors="coerce",
    ).fillna(0)

    max_value = out["value_2qb"].max()

    if not max_value:
        out["base_player_score"] = 0
    else:
        out["base_player_score"] = (
            out["value_2qb"] / max_value * 100
        ).round(1)

    out = (
        out.sort_values(
            "value_2qb",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    out["dynasty_rank"] = out.index + 1

    out["position_rank"] = (
        out.groupby("pos")
        .cumcount()
        + 1
    )

    out["tier"] = pd.cut(
        out["base_player_score"],
        bins=[-1, 40, 55, 70, 85, 100],
        labels=[5, 4, 3, 2, 1],
    ).astype(int)

    contracts = load_contract_players()

    if contracts.empty:
        out["sleeper_id"] = None
    else:
        out = out.merge(
            contracts[
                [
                    "sleeper_player_id",
                    "name_key",
                    "pos_key",
                ]
            ],
            on=[
                "name_key",
                "pos_key",
            ],
            how="left",
        )

        out["sleeper_id"] = out["sleeper_player_id"]

    final = out[
        [
            "sleeper_id",
            "player",
            "pos",
            "dynasty_rank",
            "position_rank",
            "tier",
            "base_player_score",
        ]
    ].copy()

    final["source"] = "dynastyprocess"

    final = final.dropna(
        subset=["sleeper_id"]
    )

    final["sleeper_id"] = (
        final["sleeper_id"]
        .astype(str)
    )

    final = final[
        ~final["sleeper_id"]
        .str.startswith(
            "manual_",
            na=False,
        )
    ].copy()

    return final.drop_duplicates(
        subset=["sleeper_id"]
    )


# ============================================================
# Upsert Player Identity Map
# ============================================================

def upsert_dynastyprocess_identity(
    rankings: pd.DataFrame,
):
    if rankings.empty:
        print("No DynastyProcess identities to upsert.")
        return

    rows = []

    for _, r in rankings.iterrows():
        sleeper_id = str(r.get("sleeper_id"))

        if not sleeper_id or sleeper_id.startswith("manual_"):
            continue

        rows.append(
            {
                "canonical_player_id": sleeper_id,
                "sleeper_id": sleeper_id,
                "player_name": r.get("player"),
                "normalized_name": normalize_name(
                    r.get("player")
                ),
                "pos": r.get("pos"),
                "dynastyprocess_name": r.get("player"),
                "dynastyprocess_pos": r.get("pos"),
                "source": "dynastyprocess",
                "confidence": 1,
            }
        )

    if not rows:
        print("No clean DynastyProcess identity rows.")
        return

    supabase.table(
        "player_identity_map"
    ).upsert(
        rows,
        on_conflict="canonical_player_id",
    ).execute()

    print(f"Upserted {len(rows)} DynastyProcess identity rows.")


# ============================================================
# Upsert Player Rankings
# ============================================================

def upsert_player_rankings(rankings: pd.DataFrame):
    if rankings.empty:
        print("No rankings to upsert.")
        return

    rows = rankings.to_dict(
        orient="records",
    )

    supabase.table(
        "player_rankings"
    ).upsert(
        rows,
        on_conflict="sleeper_id",
    ).execute()

    print(f"Upserted {len(rows)} player rankings.")


# ============================================================
# Run Import
# ============================================================

def run_ranking_import():
    raw = load_dynasty_process_values()
    rankings = transform_dynasty_process_values(raw)

    print(rankings.head())
    print(f"Matched rankings: {len(rankings)}")

    upsert_dynastyprocess_identity(rankings)
    upsert_player_rankings(rankings)

    return rankings


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    run_ranking_import()