from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProspectSourceRow:
    player_name: str
    position: str
    college: str | None
    source_rank: float
    source: str
    source_url: str | None = None
    notes: str | None = None


REQUIRED_OUTPUT_COLUMNS = [
    "player_name",
    "position",
    "college",
    "source_rank",
    "source",
]
