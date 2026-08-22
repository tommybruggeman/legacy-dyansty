from auth import service_client
from snapshot.intelligence.llm.legacy_reasoning_layer import build_reasoned_player_dossier
from snapshot.intelligence.llm.data_scout_agent import scout_dossier_data_quality

TARGET_TABLE = "player_data_quality_context"


def build_player_data_quality_context(season=None, limit=25):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    rookies = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("rookie_class_year", season)
        .order("rookie_rank")
        .limit(limit)
        .execute()
        .data or []
    )

    projections = (
        sb.table("player_projection_context")
        .select("*")
        .eq("season", season)
        .eq("projection_type", "rookie_v1")
        .execute()
        .data or []
    )

    projections_by_name = {
        p.get("player_name"): p
        for p in projections
        if p.get("player_name")
    }

    rows = []

    for r in rookies:
        projection = projections_by_name.get(r.get("player_name"), {})

        platform_row = {
            **r,
            **projection,
            "rookie_score": r.get("final_rookie_score"),
            "market_score": r.get("future_score"),
            "situation_score": r.get("team_need_fit_score"),
            "risk_score": r.get("risk_score") or 60,
        }

        dossier = build_reasoned_player_dossier(platform_row, use_llm=False)
        audit = scout_dossier_data_quality(dossier)

        rows.append({
            "player_name": r.get("player_name"),
            "sleeper_id": r.get("sleeper_id"),
            "pos": r.get("pos"),
            "nfl_team": r.get("nfl_team"),
            "season": season,
            "context_type": "rookie",

            "trust_grade": audit.get("trust_grade"),
            "projection_trust": audit.get("projection_trust"),
            "situation_trust": audit.get("situation_trust"),
            "market_trust": audit.get("market_trust"),
            "risk_trust": audit.get("risk_trust"),

            "do_not_overreact": audit.get("do_not_overreact"),

            "missing_data": audit.get("missing_data") or [],
            "weak_fields": audit.get("weak_fields") or [],
            "needed_sources": audit.get("needed_sources") or [],

            "recommended_next_step": audit.get("recommended_next_step"),
            "coach_warning": audit.get("coach_warning"),
        })

        print(f"Audited {r.get('player_name')}: {audit.get('trust_grade')}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="player_name,season,context_type",
        ).execute()

    print(f"Upserted {len(rows)} player_data_quality_context rows.")
    return rows


if __name__ == "__main__":
    build_player_data_quality_context(limit=10)
