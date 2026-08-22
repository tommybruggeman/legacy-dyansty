from __future__ import annotations

from auth import service_client
from snapshot.builders.rookies.rookie_year import get_active_rookie_class_year


ACTIVE_ROOKIE_CLASS_YEAR = get_active_rookie_class_year()


def age_curve_stage(age, pos):
    if age is None:
        return "UNKNOWN"

    age = float(age)

    if pos == "RB":
        if age <= 23: return "ASCENDING"
        if age <= 26: return "PRIME"
        if age <= 28: return "LATE_PRIME"
        return "DECLINE_RISK"

    if pos == "WR":
        if age <= 24: return "ASCENDING"
        if age <= 29: return "PRIME"
        if age <= 31: return "LATE_PRIME"
        return "DECLINE_RISK"

    if pos == "TE":
        if age <= 25: return "ASCENDING"
        if age <= 30: return "PRIME"
        if age <= 32: return "LATE_PRIME"
        return "DECLINE_RISK"

    if pos == "QB":
        if age <= 25: return "ASCENDING"
        if age <= 34: return "PRIME"
        if age <= 37: return "LATE_PRIME"
        return "DECLINE_RISK"

    return "UNKNOWN"


def age_curve_score(stage):
    return {
        "ASCENDING": 80,
        "PRIME": 90,
        "LATE_PRIME": 65,
        "DECLINE_RISK": 35,
        "UNKNOWN": 50,
    }.get(stage, 50)


def build_player_identity_context():
    sb = service_client()

    players = (
        sb.table("player_universe")
        .select("*")
        .in_("pos", ["QB", "RB", "WR", "TE"])
        .execute()
        .data
        or []
    )

    rows = []

    for p in players:
        sleeper_id = p.get("sleeper_id")
        if not sleeper_id:
            continue

        pos = p.get("pos")
        age = p.get("age")
        rookie_class_year = p.get("rookie_class_year")

        stage = age_curve_stage(age, pos)

        rows.append({
            "sleeper_id": sleeper_id,
            "player_name": p.get("player_name"),
            "pos": pos,
            "search_name": p.get("search_name"),

            "nfl_team": p.get("nfl_team") or p.get("team") or p.get("team_abbr"),
            "age": age,
            "years_exp": p.get("years_exp") or p.get("experience"),
            "draft_year": p.get("draft_year"),
            "draft_round": p.get("draft_round"),
            "draft_pick": p.get("draft_pick"),
            "college": p.get("college"),

            "rookie_class_year": rookie_class_year,
            "is_active_rookie": rookie_class_year == ACTIVE_ROOKIE_CLASS_YEAR,

            "age_curve_stage": stage,
            "age_curve_score": age_curve_score(stage),

            "historical_context_score": p.get("historical_context_score") or p.get("history_score") or 0,
            "production_trend_score": p.get("production_trend_score") or p.get("trend_score") or 0,
            "role_score": p.get("role_score") or 0,
            "situation_score": p.get("situation_score") or 0,
            "opportunity_score": p.get("opportunity_score") or 0,
            "contract_score": p.get("contract_score") or 0,
            "market_score": p.get("market_score") or 0,
            "rookie_asset_score": p.get("rookie_asset_score") or 0,

            "identity_confidence": 70 if age else 45,
            "identity_notes": "Built from player_universe and connected context fields.",
        })

    if rows:
        sb.table("player_identity_context").upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player_identity_context rows")


if __name__ == "__main__":
    build_player_identity_context()
