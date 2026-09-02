from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth import service_client


sb = service_client()


def norm_name(value) -> str:
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


def main(apply: bool = False):
    contracts = pd.DataFrame(
        sb.table("contracts")
        .select("id,league_id,owner_name,player_name,player_position,sleeper_player_id")
        .execute()
        .data
        or []
    )

    identity = pd.DataFrame(
        sb.table("player_identity_map")
        .select("sleeper_id,player_name,pos")
        .execute()
        .data
        or []
    )

    manual = contracts[
        contracts["sleeper_player_id"]
        .astype(str)
        .str.startswith("manual_", na=False)
    ].copy()

    identity["name_key"] = identity["player_name"].apply(norm_name)
    manual["name_key"] = manual["player_name"].apply(norm_name)

    matches = manual.merge(
        identity[["sleeper_id", "player_name", "pos", "name_key"]],
        on="name_key",
        how="left",
        suffixes=("_contract", "_identity"),
    )

    safe = matches[matches["sleeper_id"].notna()].copy()
    missing = matches[matches["sleeper_id"].isna()].copy()

    print("")
    print("Manual contract cleanup")
    print("-" * 60)
    print(f"Manual rows found: {len(manual)}")
    print(f"Safe matches:       {len(safe)}")
    print(f"Unmatched:          {len(missing)}")

    print("")
    print("Sample safe matches")
    print("-" * 60)
    print(
        safe[
            [
                "owner_name",
                "player_name_contract",
                "player_position",
                "sleeper_player_id",
                "sleeper_id",
                "player_name_identity",
            ]
        ]
        .head(25)
        .to_string(index=False)
    )

    if not missing.empty:
        print("")
        print("Unmatched manual rows")
        print("-" * 60)
        print(
            missing[
                [
                    "owner_name",
                    "player_name_contract",
                    "player_position",
                    "sleeper_player_id",
                ]
            ]
            .to_string(index=False)
        )

    if not apply:
        print("")
        print("Dry run only. No rows changed.")
        return

    for _, r in safe.iterrows():
        sb.table("contracts").update(
            {
                "sleeper_player_id": str(r["sleeper_id"]),
            }
        ).eq(
            "id",
            r["id"],
        ).execute()

    print("")
    print(f"Updated {len(safe)} manual contract rows.")


if __name__ == "__main__":
    main(apply=True)