from __future__ import annotations


def get_production_context(player: dict) -> dict:
    production = player.get("production") or {}

    primary_ppg = float(
        production.get("primary_ppg")
        or player.get("season_ppg")
        or player.get("expected_ppg")
        or 0
    )

    source = production.get("source") or "unknown"

    return {
        "primary_ppg": round(primary_ppg, 2),
        "trend_label": production.get("trend_label") or "UNKNOWN",
        "production_score": float(production.get("production_score") or 0),
        "production_confidence": int(production.get("production_confidence") or 0),
        "production_source": source,
        "production_warnings": production.get("production_warnings") or [],
    }


def production_phrase(player: dict) -> str:
    ctx = get_production_context(player)

    if ctx["primary_ppg"] <= 0:
        return "no reliable production signal"

    confidence = "high-confidence" if ctx["production_confidence"] >= 75 else "low-confidence"

    return (
        f"{ctx['primary_ppg']} primary PPG, "
        f"{ctx['trend_label'].lower()} trend, "
        f"{confidence} source"
    )
