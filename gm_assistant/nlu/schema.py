from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedGMQuestion:
    raw_question: str
    intent: str
    player_names: list[str] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)
    count_requested: int | None = None
    target_pool: str | None = None
    decision_type: str | None = None
    team_goal: str | None = None
    is_league_wide: bool = False
    needs_player_lookup: bool = False
    needs_roster: bool = False
    needs_market: bool = False
    needs_contracts: bool = False
    needs_team_fit: bool = False
    confidence: float = 0.7
