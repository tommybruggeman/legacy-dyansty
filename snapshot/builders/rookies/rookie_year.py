from __future__ import annotations

from auth import service_client


def get_active_rookie_class_year(default: int = 2026) -> int:
    sb = service_client()

    try:
        rows = (
            sb.table("league_settings")
            .select("active_rookie_class_year,season")
            .limit(1)
            .execute()
            .data or []
        )

        if rows:
            val = rows[0].get("active_rookie_class_year")
            if val:
                return int(val)

            season = rows[0].get("season")
            if season:
                return int(season)

    except Exception:
        pass

    return default
