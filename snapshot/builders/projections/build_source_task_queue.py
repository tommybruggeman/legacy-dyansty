from auth import service_client
from snapshot.intelligence.audit.source_name_normalizer import identify_source_id, normalize_source_name, source_priority, source_resolver
from snapshot.intelligence.field_registry import route_needs_to_sources
from snapshot.intelligence.source_registry import get_source_display_name, get_source_priority, get_source_resolver
from snapshot.intelligence.resolvers.general_resolver import resolve_general_need

TARGET_TABLE = "legacy_source_task_queue"


def _priority_for_source(source):
    s = (source or "").lower()

    if "identity" in s or "sleeper" in s:
        return 1
    if "depth" in s or "roster" in s or "team context" in s:
        return 1
    if "draft" in s or "college prospect" in s:
        return 1
    if "production" in s or "nflverse" in s:
        return 2
    if "adp" in s or "trade value" in s or "market" in s:
        return 2
    if "contract" in s or "spotrac" in s or "overthecap" in s:
        return 3
    if "injury" in s or "medical" in s:
        return 3

    return 4


def build_source_task_queue(season=None, context_type="rookie", limit=10):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    players = (
        sb.table("player_data_quality_context")
        .select("player_name,pos,nfl_team,trust_grade")
        .eq("season", season)
        .eq("context_type", context_type)
        .eq("trust_grade", "LOW")
        .limit(limit)
        .execute()
        .data or []
    )

    rows = []

    for p in players:
        player_name = p.get("player_name")
        result = resolve_general_need(player_name, season=season, context_type=context_type)

        for raw_source_name, needs in (result.get("source_tasks") or {}).items():
            routed_sources = route_needs_to_sources(needs)

            # If the AI gave only descriptive needs that do not map cleanly to fields,
            # fall back to the source-name registry classifier.
            if list(routed_sources.keys()) == ["manual_review"]:
                fallback_source_id = identify_source_id(raw_source_name, needs)

                if fallback_source_id != "manual_review":
                    routed_sources = {fallback_source_id: needs}

            for source_id, routed_needs in routed_sources.items():
                rows.append({
                    "player_name": player_name,
                    "season": season,
                    "context_type": context_type,
                    "source_name": get_source_display_name(source_id),
                    "source_id": source_id,
                    "resolver": get_source_resolver(source_id),
                    "needs": routed_needs,
                    "priority": get_source_priority(source_id),
                    "status": "open",
                })

    # Merge duplicate rows created by source-name normalization before Supabase upsert.
    merged = {}

    for row in rows:
        key = (
            row.get("player_name"),
            row.get("season"),
            row.get("context_type"),
            row.get("source_name"),
        )

        if key not in merged:
            merged[key] = row
            continue

        existing_needs = merged[key].get("needs") or []
        new_needs = row.get("needs") or []

        merged[key]["needs"] = sorted(set(existing_needs + new_needs))
        merged[key]["priority"] = min(
            merged[key].get("priority", 99),
            row.get("priority", 99),
        )

    rows = list(merged.values())

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="player_name,season,context_type,source_name",
        ).execute()

    print(f"Upserted {len(rows)} legacy_source_task_queue rows.")
    return rows


if __name__ == "__main__":
    build_source_task_queue(limit=10)
