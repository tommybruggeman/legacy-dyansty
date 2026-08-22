from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any


def normalize_name(name: str | None) -> str:
    s = str(name or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_key(row: dict[str, Any]) -> str:
    sleeper = row.get("sleeper_id") or (row.get("identity") or {}).get("sleeper_id")
    gsis = row.get("gsis_id") or (row.get("identity") or {}).get("gsis_id")

    if gsis:
        return f"gsis:{gsis}"
    if sleeper:
        return f"sleeper:{sleeper}"

    return f"name:{normalize_name(row.get('player_name'))}"


def name_similarity(a: str | None, b: str | None) -> float:
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


@dataclass
class IdentityCandidate:
    table: str
    player_name: str | None
    pos: str | None
    sleeper_id: str | None
    gsis_id: str | None
    owner: str | None = None
    canonical_player_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class IdentityCluster:
    resolved_key: str
    display_name: str
    primary_pos: str | None
    sleeper_ids: list[str]
    gsis_ids: list[str]
    aliases: list[str]
    candidates: list[IdentityCandidate]
    confidence: float
    warnings: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "canonical_identity_key": self.resolved_key,
            "display_name": self.display_name,
            "primary_pos": self.primary_pos,
            "sleeper_ids": self.sleeper_ids,
            "gsis_ids": self.gsis_ids,
            "aliases": self.aliases,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "candidate_count": len(self.candidates),
            "position_scores": getattr(self, "position_scores", None),
        }


def resolve_cluster(candidates: list[IdentityCandidate]) -> IdentityCluster:
    names = [c.player_name for c in candidates if c.player_name]
    poses = [c.pos for c in candidates if c.pos]
    sleepers = sorted({str(c.sleeper_id) for c in candidates if c.sleeper_id})
    gsis_ids = sorted({str(c.gsis_id) for c in candidates if c.gsis_id})
    aliases = sorted({normalize_name(n) for n in names if n})

    display_name = max(names, key=len) if names else "Unknown Player"
    from gm_assistant.identity.confidence import choose_canonical_position, identity_confidence

    primary_pos, position_scores = choose_canonical_position(candidates)

    if gsis_ids:
        key = f"gsis:{gsis_ids[0]}"
    elif sleepers:
        key = f"sleeper:{sleepers[0]}"
    else:
        key = f"name:{normalize_name(display_name)}"

    warnings = []

    if len(set(poses)) > 1:
        warnings.append(f"position_conflict:{sorted(set(poses))}")

    if len(set(aliases)) > 1:
        # Only warn when aliases are meaningfully different.
        sims = []
        for a in aliases:
            for b in aliases:
                if a < b:
                    sims.append(name_similarity(a, b))
        if sims and min(sims) < 0.88:
            warnings.append(f"name_conflict:{aliases}")

    if len(sleepers) > 1:
        warnings.append(f"sleeper_conflict:{sleepers}")

    if len(gsis_ids) > 1:
        warnings.append(f"gsis_conflict:{gsis_ids}")

    confidence = identity_confidence(type("ClusterLike", (), {
        "gsis_ids": gsis_ids,
        "sleeper_ids": sleepers,
        "warnings": warnings,
    })())

    cluster = IdentityCluster(
        resolved_key=key,
        display_name=display_name,
        primary_pos=primary_pos,
        sleeper_ids=sleepers,
        gsis_ids=gsis_ids,
        aliases=aliases,
        candidates=candidates,
        confidence=confidence,
        warnings=warnings,
    )
    cluster.position_scores = position_scores
    return cluster
