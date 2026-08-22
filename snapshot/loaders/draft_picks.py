from __future__ import annotations

import pandas as pd

from auth import service_client


def load_draft_picks() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("draft_picks")
        .select("*")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)
