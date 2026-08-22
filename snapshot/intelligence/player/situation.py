from __future__ import annotations

from .common import find_one, num


def build_situation(player_name: str, owner_team_name: str | None = None) -> dict:
    universe = find_one("player_universe", player_name, owner_team_name)
    identity = find_one("player_identity_context", player_name)

    return {
        "role_score": num(identity.get("role_score")),
        "situation_score": num(identity.get("situation_score")),
        "opportunity_score": num(identity.get("opportunity_score")),
        "nfl_intelligence_score": num(universe.get("nfl_intelligence_score")),
        "nfl_intelligence_grade": universe.get("nfl_intelligence_grade"),
        "nfl_intelligence_flags": universe.get("nfl_intelligence_flags") or [],
        "roster_status": universe.get("roster_status"),
        "_raw": {
            "player_universe": universe,
            "player_identity_context": identity,
        },
    }
