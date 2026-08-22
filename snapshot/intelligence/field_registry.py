from __future__ import annotations

import re


FIELD_ALIASES = {
    "age": ["age", "player age"],
    "age_risk": ["age risk", "age_risk"],
    "asset_score": ["asset score", "asset_score"],
    "class_year": ["class year", "class_year"],
    "data_warning": ["data warning", "data_warning", "warning"],
    "notes": ["notes"],
    "positional_rank": ["positional rank", "positional_rank"],

    "gsis_id": ["gsis", "gsis id", "gsis_id"],
    "sleeper_id": ["sleeper id", "sleeper_id"],
    "identity_complete": ["identity_complete", "identity complete"],

    "draft_pick": ["draft pick", "draft_pick"],
    "draft_round": ["draft round", "draft_round"],
    "draft_year": ["draft year", "draft_year"],
    "rookie_year": ["rookie year", "rookie_year"],
    "prospect_tier": ["prospect tier", "prospect_tier"],
    "draft_profile_complete": ["draft_profile_complete", "draft profile complete"],

    "games": ["games", "games played"],
    "points": ["points", "fantasy points"],
    "season_ppg": ["season_ppg", "season ppg", "ppg"],
    "projected_ppg": ["projected_ppg", "projected ppg"],
    "production_complete": ["production_complete", "production complete"],

    "role_score": ["role score", "role_score"],
    "depth_chart_score": ["depth chart score", "depth_chart_score"],
    "team_context_confirmed": ["team context confirmation", "team_context_confirmed"],
    "situation_complete": ["situation_complete", "situation complete"],

    "adp": ["adp"],
    "trade_value": ["trade value", "trade_value"],
    "market_pool": ["market pool", "market_pool"],
    "market_complete": ["market_complete", "market complete"],

    "salary": ["salary"],
    "years": ["years", "contract years", "years on contract"],
    "has_contract": ["has contract", "has_contract", "has contract status"],
    "contract_owner": ["contract owner", "owner"],
    "contract_risk": ["contract risk", "contract_risk"],
    "contract_complete": ["contract_complete", "contract complete"],

    "injury_risk": ["injury risk", "injury_risk"],
    "risk_complete": ["risk_complete", "risk complete"],
}


FIELD_SOURCE_OWNER = {
    "age": "draft_profile",
    "class_year": "draft_profile",
    "rookie_year": "draft_profile",
    "draft_pick": "draft_profile",
    "draft_round": "draft_profile",
    "draft_year": "draft_profile",
    "prospect_tier": "draft_profile",
    "draft_profile_complete": "draft_profile",

    "gsis_id": "identity",
    "sleeper_id": "identity",
    "identity_complete": "identity",

    "games": "historical_stats",
    "points": "historical_stats",
    "season_ppg": "historical_stats",
    "projected_ppg": "historical_stats",
    "production_complete": "historical_stats",

    "role_score": "depth_chart",
    "depth_chart_score": "depth_chart",
    "team_context_confirmed": "depth_chart",
    "situation_complete": "depth_chart",

    "adp": "market",
    "trade_value": "market",
    "market_pool": "market",
    "market_complete": "market",

    "salary": "contracts",
    "years": "contracts",
    "has_contract": "contracts",
    "contract_owner": "contracts",
    "contract_risk": "contracts",
    "contract_complete": "contracts",

    "injury_risk": "injuries",
    "risk_complete": "injuries",
    "age_risk": "injuries",

    "asset_score": "manual_review",
    "data_warning": "manual_review",
    "notes": "manual_review",
    "positional_rank": "manual_review",
}


def _clean(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9_\s]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_field_name(value: str) -> str | None:
    text = _clean(value)

    if not text:
        return None

    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            alias_clean = _clean(alias)
            if text == alias_clean or alias_clean in text:
                return field

    return None


def source_id_for_field(field_name: str) -> str:
    return FIELD_SOURCE_OWNER.get(field_name, "manual_review")


def route_needs_to_sources(needs: list[str]) -> dict[str, list[str]]:
    routed: dict[str, list[str]] = {}

    for need in needs or []:
        field = normalize_field_name(need)

        if field:
            source_id = source_id_for_field(field)
            routed.setdefault(source_id, []).append(field)
        else:
            routed.setdefault("manual_review", []).append(str(need))

    return {k: sorted(set(v)) for k, v in routed.items()}


if __name__ == "__main__":
    test_needs = [
        "age",
        "age_risk",
        "asset_score",
        "class_year",
        "data_warning",
        "injury_risk",
        "projected_ppg",
        "trade_value",
        "salary",
        "role_score",
        "draft_pick",
        "gsis_id",
    ]

    for source_id, needs in route_needs_to_sources(test_needs).items():
        print(source_id, needs)
