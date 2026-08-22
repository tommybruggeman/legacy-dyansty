from auth import service_client

SOURCE_TABLE = "player_data_need_queue"
TARGET_TABLE = "player_data_need_queue"


GROUPS = {
    "identity_complete": ["identity", "sleeper", "gsis", "id"],
    "draft_profile_complete": ["draft", "rookie", "prospect", "college", "class year"],
    "situation_complete": ["depth", "role", "team context", "starter", "roster"],
    "market_complete": ["adp", "trade", "market"],
    "production_complete": ["production", "points", "games", "ppg"],
    "contract_complete": ["contract", "salary", "years"],
    "risk_complete": ["injury", "medical", "age risk", "contract risk"],
}


def _group_need(need, category):
    text = f"{need or ''} {category or ''}".lower()

    for group, terms in GROUPS.items():
        if any(t in text for t in terms):
            return group

    return "general_review"


def _source_for_group(group):
    return {
        "identity_complete": "Sleeper player universe / player identity context",
        "draft_profile_complete": "Draft profile source / college production / consensus prospect source",
        "situation_complete": "Depth chart source / roster source / team context builder",
        "market_complete": "Dynasty ADP / trade value feed",
        "production_complete": "nflverse / Sleeper stats / historical fantasy production",
        "contract_complete": "Legacy contracts / Spotrac / OverTheCap",
        "risk_complete": "Injury reports / age curve / availability history",
        "general_review": "Manual review",
    }.get(group, "Manual review")


def _priority_for_group(group):
    return {
        "identity_complete": 1,
        "draft_profile_complete": 1,
        "situation_complete": 1,
        "production_complete": 2,
        "market_complete": 2,
        "contract_complete": 3,
        "risk_complete": 3,
        "general_review": 4,
    }.get(group, 4)


def build_normalized_data_need_queue(season=None, context_type="rookie"):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    raw = (
        sb.table(SOURCE_TABLE)
        .select("*")
        .eq("season", season)
        .eq("context_type", context_type)
        .execute()
        .data or []
    )

    grouped = {}

    for r in raw:
        group = _group_need(r.get("need"), r.get("need_category"))
        key = (r.get("player_name"), season, context_type, group)

        grouped.setdefault(key, {
            "player_name": r.get("player_name"),
            "sleeper_id": r.get("sleeper_id"),
            "pos": r.get("pos"),
            "nfl_team": r.get("nfl_team"),
            "season": season,
            "context_type": context_type,
            "need": group,
            "need_category": group.replace("_complete", ""),
            "priority": _priority_for_group(group),
            "source_suggestion": _source_for_group(group),
            "status": "open",
        })

    rows = list(grouped.values())

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="player_name,season,context_type,need",
        ).execute()

    print(f"Upserted {len(rows)} normalized grouped data needs.")
    return rows


if __name__ == "__main__":
    build_normalized_data_need_queue()
