"""Cross-language Phase A history identity-set fingerprints.

The encoded material is a compact UTF-8 JSON array of positional arrays. Object
keys are deliberately excluded. Rows use deterministic identity ordering and
SHA-256 is returned as lowercase hexadecimal.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence


def _fingerprint(rows: Iterable[Sequence[Any]], key) -> str:
    ordered = sorted((list(row) for row in rows), key=key)
    encoded = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_team_set_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    return _fingerprint(
        ([str(row.get("id") or ""), int(row["sleeper_roster_id"]), row.get("sleeper_user_id")] for row in rows),
        lambda row: (row[0], row[1], str(row[2] or "")),
    )


def source_roster_set_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    return _fingerprint(
        ([int(row["roster_id"]), row.get("owner_id")] for row in rows),
        lambda row: (row[0], str(row[1] or "")),
    )


def mapping_set_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    return _fingerprint(
        ([str(row["league_team_id"]), int(row["sleeper_roster_id"]), row.get("sleeper_user_id")] for row in rows),
        lambda row: (row[0], row[1], str(row[2] or "")),
    )


def standings_set_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    return _fingerprint(
        ([str(row["league_team_id"])] for row in rows),
        lambda row: (row[0],),
    )
