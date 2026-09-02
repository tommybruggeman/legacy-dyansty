from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class LeagueConfig:
    salary_cap: float = 225.0
    roster_limit: int = 22

    starting_slots: Dict[str, int] = field(default_factory=lambda: {
        "QB": 1,
        "RB": 2,
        "WR": 2,
        "TE": 1,
        "FLEX": 1,
        "SUPERFLEX": 1,
    })

    position_weights: Dict[str, float] = field(default_factory=lambda: {
        "QB": 8.0,
        "RB": 2.0,
        "WR": 0.0,
        "TE": 1.0,
        "UNK": -5.0,
    })

    scoring_type: str = "0.5 PPR + 0.5 first down"
    league_format: str = "10-team superflex dynasty salary cap"


DEFAULT_LEAGUE_CONFIG = LeagueConfig()
