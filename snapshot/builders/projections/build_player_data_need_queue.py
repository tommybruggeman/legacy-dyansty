from auth import service_client

TARGET_TABLE = "player_data_need_queue"


def _category_for_need(need: str) -> str:
    n = (need or "").lower()

    if "draft" in n or "rookie" in n or "prospect" in n:
        return "draft_profile"
    if "contract" in n or "salary" in n or "years" in n:
        return "contract"
    if "depth" in n or "role" in n or "team context" in n or "starter" in n:
        return "situation"
    if "adp" in n or "trade" in n or "market" in n:
        return "market"
    if "injury" in n or "medical" in n:
        return "risk"
    if "production" in n or "points" in n or "games" in n or "ppg" in n:
        return "production"
    if "sleeper" in n or "id" in n or "identity" in n:
        return "identity"

    return "general"


def _source_for_category(category: str) -> str:
    return {
        "draft_profile": "NFL draft data / college prospect source / consensus rookie source",
        "contract": "Legacy contract tables / Spotrac / OverTheCap",
        "situation": "Depth chart source / Sleeper roster / team context builder",
        "market": "Dynasty ADP / trade value / market feed",
        "risk": "Injury report / age curve / availability history",
        "production": "nflverse / Sleeper scoring / historical fantasy production",
        "identity": "Sleeper player universe / player identity context",
        "general": "Manual review / source mapper",
    }.get(category, "Manual review / source mapper")


def _priority_for_category(category: str) -> int:
    return {
        "identity": 1,
        "draft_profile": 1,
        "situation": 1,
        "production": 2,
        "market": 2,
        "contract": 3,
        "risk": 3,
        "general": 4,
    }.get(category, 4)


def build_player_data_need_queue(season=None, context_type="rookie"):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    audits = (
        sb.table("player_data_quality_context")
        .select("*")
        .eq("season", season)
        .eq("context_type", context_type)
        .execute()
        .data or []
    )

    rows = []

    for audit in audits:
        needs = []

        for item in audit.get("missing_data") or []:
            needs.append(str(item))

        for item in audit.get("needed_sources") or []:
            needs.append(str(item))

        seen = set()

        for need in needs:
            clean_need = need.strip()
            if not clean_need or clean_need.lower() in seen:
                continue

            seen.add(clean_need.lower())

            category = _category_for_need(clean_need)

            rows.append({
                "player_name": audit.get("player_name"),
                "sleeper_id": audit.get("sleeper_id"),
                "pos": audit.get("pos"),
                "nfl_team": audit.get("nfl_team"),
                "season": season,
                "context_type": context_type,
                "need": clean_need,
                "need_category": category,
                "priority": _priority_for_category(category),
                "source_suggestion": _source_for_category(category),
                "status": "open",
            })

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="player_name,season,context_type,need",
        ).execute()

    print(f"Upserted {len(rows)} player_data_need_queue rows.")
    return rows


if __name__ == "__main__":
    build_player_data_need_queue()
