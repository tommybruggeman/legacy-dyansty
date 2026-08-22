from __future__ import annotations

import re

from snapshot.intelligence.source_registry import (
    get_source_display_name,
    get_source_priority,
    get_source_resolver,
)


SOURCE_KEYWORDS = {
    "draft_profile": {
        "draft",
        "prospect",
        "college",
        "rookie",
        "consensus",
        "combine",
        "pro day",
        "scouting",
    },
    "historical_stats": {
        "nflverse",
        "historical",
        "fantasy",
        "sleeper scoring",
        "sleeper stats",
        "production",
        "season_ppg",
        "projected_ppg",
        "games",
        "points",
    },
    "depth_chart": {
        "depth",
        "chart",
        "roster",
        "role",
        "team context",
        "situation",
        "snap",
        "camp",
        "beat",
    },
    "identity": {
        "identity",
        "gsis",
        "sleeper id",
        "sleeper player universe",
        "player universe",
    },
    "market": {
        "adp",
        "trade value",
        "market",
        "dynasty",
        "market_pool",
        "market_complete",
    },
    "contracts": {
        "contract",
        "contracts",
        "salary",
        "years",
        "spotrac",
        "overthecap",
        "dead cap",
        "contract_complete",
    },
    "injuries": {
        "injury",
        "injuries",
        "availability",
        "medical",
        "risk",
        "risk_complete",
        "age curve",
    },
}


def _clean(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9_\s/]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def identify_source_id(source_name: str, needs: list[str] | None = None) -> str:
    text = _clean(source_name)

    if needs:
        text += " " + _clean(" ".join(str(n) for n in needs))

    if not text:
        return "manual_review"

    scores = {}

    for source_id, keywords in SOURCE_KEYWORDS.items():
        scores[source_id] = sum(1 for keyword in keywords if keyword in text)

    best_source_id, best_score = max(scores.items(), key=lambda item: item[1])

    if best_score <= 0:
        return "manual_review"

    return best_source_id


def normalize_source_name(source_name: str, needs: list[str] | None = None) -> str:
    source_id = identify_source_id(source_name, needs)
    return get_source_display_name(source_id)


def source_priority(source_name: str, needs: list[str] | None = None) -> int:
    source_id = identify_source_id(source_name, needs)
    return get_source_priority(source_id)


def source_resolver(source_name: str, needs: list[str] | None = None) -> str:
    source_id = identify_source_id(source_name, needs)
    return get_source_resolver(source_id)


if __name__ == "__main__":
    tests = [
        ("NFL draft data / college prospect source / consensus rookie source", ["draft_pick", "rookie_year"]),
        ("Draft profile source / college production / consensus prospect source", ["draft_profile_complete"]),
        ("nflverse / Sleeper scoring / historical fantasy production", ["season_ppg", "points"]),
        ("nflverse / Sleeper stats / historical fantasy production", ["production_complete"]),
        ("Depth chart source / Sleeper roster / team context builder", ["role_score"]),
        ("Dynasty ADP / trade value feed", ["market_complete"]),
        ("Dynasty ADP / trade value / market feed", ["adp", "trade_value"]),
        ("Legacy contract tables / Spotrac / OverTheCap", ["salary", "years"]),
        ("Legacy contracts / Spotrac / OverTheCap", ["contract_complete"]),
        ("Injury report / age curve / availability history", ["injury_risk"]),
        ("Injury reports / age curve / availability history", ["risk_complete"]),
    ]

    for source_name, needs in tests:
        source_id = identify_source_id(source_name, needs)
        print(source_name, "=>", source_id, "=>", normalize_source_name(source_name, needs))
