from __future__ import annotations

import pandas as pd

from auth import service_client


def load_teams(league_id: str | None = None) -> pd.DataFrame:
    sb = service_client()

    query = sb.table("teams").select("*")

    if league_id:
        query = query.eq("league_id", league_id)

    rows = (
        query
        .order("team_name")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)
