from __future__ import annotations

from auth import service_client


def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _rows(table: str, player_name: str, sleeper_id: str | None = None, gsis_id: str | None = None):
    sb = service_client()
    queries = []

    if sleeper_id:
        queries.append(("sleeper_id", sleeper_id))
    if gsis_id:
        queries.append(("gsis_id", gsis_id))
    if player_name:
        queries.append(("player_name", player_name))

    results = []
    seen = set()

    for col, val in queries:
        try:
            q = sb.table(table).select("*").limit(200)
            if col == "player_name":
                data = q.ilike("player_name", f"%{val}%").execute().data or []
            else:
                data = q.eq(col, val).execute().data or []

            for r in data:
                key = str(r)
                if key not in seen:
                    seen.add(key)
                    results.append(r)
        except Exception:
            pass

    return results


def resolve_best_production(player: dict | None) -> dict:
    player = player or {}

    name = player.get("player_name") or player.get("name")
    sleeper_id = player.get("sleeper_id")
    gsis_id = player.get("gsis_id")

    # 1. Weekly stats are strongest.
    weekly = _rows("player_weekly_stats", name, sleeper_id, gsis_id)
    weekly_ppgs = []
    for r in weekly:
        for key in ["fantasy_points_ppr", "fantasy_points", "points", "ppg"]:
            val = r.get(key)
            if val is not None:
                weekly_ppgs.append(_safe_float(val))
                break

    if weekly_ppgs:
        return {
            "ppg": round(sum(weekly_ppgs) / len(weekly_ppgs), 2),
            "source": "player_weekly_stats",
            "confidence": 95,
            "rows": len(weekly),
        }

    # 2. Season stats.
    season = _rows("player_season_stats", name, sleeper_id, gsis_id)
    vals = []
    for r in season:
        for key in ["fantasy_ppg_ppr", "season_ppg", "ppg", "fantasy_ppg"]:
            val = r.get(key)
            if val is not None:
                vals.append(_safe_float(val))
                break

    if vals:
        return {
            "ppg": round(max(vals), 2),
            "source": "player_season_stats",
            "confidence": 88,
            "rows": len(season),
        }

    # 3. Career features.
    career = _rows("player_career_features", name, sleeper_id, gsis_id)
    vals = []
    for r in career:
        for key in ["career_ppg", "ppg", "fantasy_ppg", "avg_ppg"]:
            val = r.get(key)
            if val is not None:
                vals.append(_safe_float(val))
                break

    if vals:
        return {
            "ppg": round(max(vals), 2),
            "source": "player_career_features",
            "confidence": 75,
            "rows": len(career),
        }

    # 4. Value engine fallback.
    value = _rows("player_value_engine", name, sleeper_id, gsis_id)
    vals = []
    for r in value:
        for key in ["expected_ppg", "projected_ppg", "ppg"]:
            val = r.get(key)
            if val is not None:
                vals.append(_safe_float(val))
                break

    if vals:
        return {
            "ppg": round(max(vals), 2),
            "source": "player_value_engine",
            "confidence": 65,
            "rows": len(value),
        }

    return {
        "ppg": None,
        "source": "production_unavailable",
        "confidence": 0,
        "rows": 0,
    }
