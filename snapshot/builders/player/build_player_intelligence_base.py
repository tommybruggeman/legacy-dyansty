from __future__ import annotations

import pandas as pd
from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows

TARGET_TABLE = "player_intelligence_base"


def build():
    sb = service_client()

    roster = pd.DataFrame(load_internal_contract_rows(sb))

    if roster.empty:
        print("No contract data")
        return

    roster = roster.rename(columns={
        "sleeper_player_id": "sleeper_id",
        "owner_name": "owner_team_name",
        "player_position": "pos",
        "contract_years_left": "contract_years",
        "salary": "contract_salary",
    })

    # HARD GUARANTEE canonical fields
    roster["sleeper_id"] = roster["sleeper_id"].astype(str)
    roster["owner_team_name"] = roster["owner_team_name"].fillna("UNKNOWN")
    roster["player_name"] = roster["player_name"].fillna("UNKNOWN")

    
    # ============================
    # HARD DATA SANITIZATION LAYER
    # ============================

    roster = roster.dropna(subset=["sleeper_id", "owner_team_name"])

    roster = roster[roster["sleeper_id"].astype(str).str.strip() != ""]
    roster = roster[roster["owner_team_name"].astype(str).str.strip() != ""]

    # FORCE uniqueness BEFORE upsert
    roster = roster.drop_duplicates(subset=["sleeper_id", "owner_team_name"], keep="first")

    base = roster[[
        "sleeper_id",
        "player_name",
        "pos",
        "owner_team_name",
        "contract_salary",
        "contract_years",
    ]].copy()

    base["age"] = 27  # placeholder until age engine plugged in
    base["nfl_team"] = None

    sb.table(TARGET_TABLE).upsert(
        base.to_dict("records"),
        on_conflict="sleeper_id,owner_team_name"
    ).execute()

    print(f"Upserted {len(base)} base players")


if __name__ == "__main__":
    build()
