from __future__ import annotations

import re
from typing import Any


def norm_name(v: str | None) -> str:
    s = str(v or "").strip().lower()
    s = s.replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b$", "", s).strip()
    return " ".join(s.split())


def id_keys(row: dict[str, Any]) -> set[str]:
    keys = set()

    for k in [
        "sleeper_id",
        "sleeper_player_id",
        "player_id",
        "gsis_id",
        "canonical_player_id",
    ]:
        v = row.get(k)
        if v:
            keys.add(str(v).strip())

    return {k for k in keys if k}


def name_pos_key(row: dict[str, Any]) -> tuple[str, str] | None:
    name = (
        row.get("player_name")
        or row.get("full_name")
        or row.get("name")
    )
    pos = row.get("pos") or row.get("position") or row.get("player_position")

    name = norm_name(name)
    pos = str(pos or "").strip().upper()

    if not name or pos not in {"QB", "RB", "WR", "TE"}:
        return None

    return name, pos


class PlayerIdentityResolver:
    def __init__(self):
        self.key_to_canon: dict[str, str] = {}
        self.namepos_to_canon: dict[tuple[str, str], str] = {}
        self.canon_rows: dict[str, dict[str, Any]] = {}

    def resolve(self, row: dict[str, Any], preferred_id: str | None = None) -> str | None:
        keys = id_keys(row)
        np = name_pos_key(row)

        candidates = set()

        for k in keys:
            if k in self.key_to_canon:
                candidates.add(self.key_to_canon[k])

        if np and np in self.namepos_to_canon:
            candidates.add(self.namepos_to_canon[np])

        if candidates:
            canon = sorted(candidates)[0]
        else:
            canon = str(preferred_id or next(iter(keys), None) or "")
            if not canon and np:
                canon = f"{np[0]}::{np[1]}"
            if not canon:
                return None

        self.canon_rows.setdefault(canon, {})

        for k in keys:
            self.key_to_canon[k] = canon

        if np:
            self.namepos_to_canon[np] = canon

        return canon

    def merge(self, canon: str, row: dict[str, Any]) -> dict[str, Any]:
        base = self.canon_rows.setdefault(canon, {})

        for k, v in row.items():
            if v in [None, "", [], {}]:
                continue
            if base.get(k) in [None, "", 0, 0.0, [], {}]:
                base[k] = v

        return base
