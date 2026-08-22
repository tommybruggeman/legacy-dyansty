from __future__ import annotations

import pandas as pd

from auth import service_client


def load_player_season_stats() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("player_season_stats")
        .select("*")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)
