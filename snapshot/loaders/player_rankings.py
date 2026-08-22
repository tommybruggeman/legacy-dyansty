from __future__ import annotations

import pandas as pd

from auth import service_client


def load_player_rankings() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("player_rankings")
        .select("*")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)
