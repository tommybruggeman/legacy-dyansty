from __future__ import annotations

SOURCE_REGISTRY = {
    "draft_profile": {
        "display_name": "Draft profile / college production / consensus prospect source",
        "priority": 1,
        "resolver": "draft_profile_resolver",
        "fields": [
            "age",
            "class_year",
            "rookie_year",
            "draft_year",
            "draft_round",
            "draft_pick",
            "prospect_tier",
            "draft_profile_complete",
        ],
        "dependencies": ["identity"],
    },
    "historical_stats": {
        "display_name": "nflverse / Sleeper stats / historical fantasy production",
        "priority": 1,
        "resolver": "historical_stats_resolver",
        "fields": [
            "games",
            "points",
            "season_ppg",
            "projected_ppg",
            "production_complete",
        ],
        "dependencies": ["identity"],
    },
    "depth_chart": {
        "display_name": "Depth chart source / roster source / team context builder",
        "priority": 1,
        "resolver": "depth_chart_resolver",
        "fields": [
            "role_score",
            "depth_chart_score",
            "team_context_confirmed",
            "situation_complete",
        ],
        "dependencies": ["identity"],
    },
    "identity": {
        "display_name": "Sleeper player universe / player identity context",
        "priority": 1,
        "resolver": "identity_resolver",
        "fields": [
            "gsis_id",
            "sleeper_id",
            "identity_complete",
        ],
        "dependencies": [],
    },
    "market": {
        "display_name": "Dynasty ADP / trade value / market feed",
        "priority": 2,
        "resolver": "market_resolver",
        "fields": [
            "adp",
            "trade_value",
            "market_pool",
            "market_complete",
        ],
        "dependencies": ["identity"],
    },
    "contracts": {
        "display_name": "Legacy contracts / Spotrac / OverTheCap",
        "priority": 3,
        "resolver": "contract_resolver",
        "fields": [
            "salary",
            "years",
            "has_contract",
            "contract_owner",
            "contract_risk",
            "contract_complete",
        ],
        "dependencies": ["identity"],
    },
    "injuries": {
        "display_name": "Injury reports / age curve / availability history",
        "priority": 3,
        "resolver": "injury_resolver",
        "fields": [
            "injury_risk",
            "risk_complete",
            "age_risk",
        ],
        "dependencies": ["identity"],
    },
    "manual_review": {
        "display_name": "Manual review / source mapper",
        "priority": 4,
        "resolver": "manual_review",
        "fields": [
            "data_warning",
            "notes",
            "asset_score",
            "positional_rank",
        ],
        "dependencies": [],
    },
}


def get_source(source_id: str) -> dict:
    return SOURCE_REGISTRY.get(source_id, SOURCE_REGISTRY["manual_review"])


def get_source_display_name(source_id: str) -> str:
    return get_source(source_id)["display_name"]


def get_source_priority(source_id: str) -> int:
    return int(get_source(source_id).get("priority", 99))


def get_source_resolver(source_id: str) -> str:
    return str(get_source(source_id).get("resolver", "manual_review"))
