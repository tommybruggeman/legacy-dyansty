from __future__ import annotations

import pandas as pd

from auth import service_client


def load_owners() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("owners")
        .select("*")
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)
