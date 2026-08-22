from __future__ import annotations

import pandas as pd

from auth import service_client


def load_team_caps() -> pd.DataFrame:
    sb = service_client()

    try:
        rows = sb.table("v_team_caps").select("*").execute().data or []
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def load_cap_adjustments() -> pd.DataFrame:
    sb = service_client()

    try:
        rows = sb.table("cap_adjustments").select("*").execute().data or []
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()
