from __future__ import annotations

import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows


def load_contracts(league_id: str | None = None) -> pd.DataFrame:
    sb = service_client()

    rows = load_internal_contract_rows(sb, league_id)
    rows.sort(key=lambda row: (str(row.get("owner_name") or ""), str(row.get("player_position") or ""), str(row.get("player_name") or "")))

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    return df.rename(
        columns={
            "owner_name": "owner",
            "player_name": "player",
            "player_position": "pos",
            "contract_years_left": "years",
            "sleeper_player_id": "sleeper_id",
        }
    )
