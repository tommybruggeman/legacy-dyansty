from __future__ import annotations

import pandas as pd

from auth import service_client


def load_player_career_features() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("player_career_features")
        .select("*")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)
