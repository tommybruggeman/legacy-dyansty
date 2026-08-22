from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MATERIAL_FIELDS = ("league_id", "owner_id", "sleeper_player_id", "salary", "contract_years_left",
                   "contract_total_years", "owner_name", "player_position", "is_rookie", "created_at")


def material_differences(left: dict[str, Any], right: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    return {field: (left.get(field), right.get(field)) for field in MATERIAL_FIELDS if left.get(field) != right.get(field)}


def is_typo_only_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (not material_differences(left, right)
            and left.get("player_name") != right.get("player_name")
            and left.get("sleeper_player_id") == right.get("sleeper_player_id"))


def select_spelling_canonical(rows: list[dict[str, Any]], canonical_name: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("player_name") == canonical_name]
    if len(matches) != 1: raise ValueError("Canonical spelling does not identify exactly one source row.")
    return matches[0]


def reconcile_owner_evidence(evidence: dict[str, str | None]) -> str:
    authoritative = [evidence.get(key) for key in ("historical_snapshot", "active_sleeper", "latest_transaction", "canonical_current") if evidence.get(key)]
    if not authoritative or len(set(authoritative)) != 1:
        raise ValueError(f"Authoritative ownership evidence is ambiguous: {evidence}")
    return authoritative[0]
