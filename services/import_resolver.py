from __future__ import annotations

import re
from typing import Any


def normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_candidate_label(p: dict[str, Any]) -> str:
    name = p.get("full_name") or ""
    pos = p.get("position") or ""
    team = p.get("team") or ""
    status = p.get("status") or ""
    pid = p.get("sleeper_player_id") or ""

    inactive_tag = ""
    if p.get("is_active") is False or (status and status.lower() in {"inactive", "retired"}):
        inactive_tag = " • INACTIVE"

    return f"{name} — {pos} — {team}{inactive_tag} — {pid}"


def resolve_row(
    sleeper_rows_by_name: dict[str, list[dict[str, Any]]],
    csv_name: str,
    csv_pos: str | None,
) -> dict[str, Any]:
    n = normalize_name(csv_name)
    pos = (csv_pos or "").strip().upper() or None

    if pos in {"DST", "D/ST"}:
        pos = "DEF"
    if pos == "PK":
        pos = "K"

    candidates = sleeper_rows_by_name.get(n, [])

    if not candidates:
        return {
            "match_status": "unresolved",
            "match_reason": "NOT_FOUND",
            "resolved_sleeper_player_id": None,
            "candidates": [],
        }

    if pos:
        pos_matches = [p for p in candidates if (p.get("position") or "").upper() == pos]

        if len(pos_matches) == 1:
            return {
                "match_status": "matched",
                "match_reason": "EXACT_NAME_AND_POSITION",
                "resolved_sleeper_player_id": pos_matches[0]["sleeper_player_id"],
                "candidates": [{"id": p["sleeper_player_id"], "label": build_candidate_label(p)} for p in pos_matches],
            }

        if len(pos_matches) > 1:
            return {
                "match_status": "unresolved",
                "match_reason": "AMBIGUOUS_DUPLICATE_NAME_AND_POSITION",
                "resolved_sleeper_player_id": None,
                "candidates": [{"id": p["sleeper_player_id"], "label": build_candidate_label(p)} for p in pos_matches],
            }

        if len(candidates) == 1:
            return {
                "match_status": "matched_low_confidence",
                "match_reason": "NAME_MATCH_POSITION_MISSING_IN_DB_OR_WRONG_IN_CSV",
                "resolved_sleeper_player_id": candidates[0]["sleeper_player_id"],
                "candidates": [{"id": candidates[0]["sleeper_player_id"], "label": build_candidate_label(candidates[0])}],
            }

        return {
            "match_status": "unresolved",
            "match_reason": "POSITION_MISMATCH_OR_DUPLICATE_NAME",
            "resolved_sleeper_player_id": None,
            "candidates": [{"id": p["sleeper_player_id"], "label": build_candidate_label(p)} for p in candidates],
        }

    if len(candidates) == 1:
        return {
            "match_status": "matched_low_confidence",
            "match_reason": "UNIQUE_NAME_NO_POSITION",
            "resolved_sleeper_player_id": candidates[0]["sleeper_player_id"],
            "candidates": [{"id": candidates[0]["sleeper_player_id"], "label": build_candidate_label(candidates[0])}],
        }

    return {
        "match_status": "unresolved",
        "match_reason": "AMBIGUOUS_NEEDS_POSITION",
        "resolved_sleeper_player_id": None,
        "candidates": [{"id": p["sleeper_player_id"], "label": build_candidate_label(p)} for p in candidates],
    }