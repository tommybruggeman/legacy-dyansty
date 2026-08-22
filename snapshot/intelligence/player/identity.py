from __future__ import annotations

from .common import find_one, pick, num


def build_identity(player_name: str, owner_team_name: str | None = None) -> dict:
    universe = find_one("player_universe", player_name, owner_team_name)
    identity = find_one("player_identity_context", player_name)

    return {
        "name": pick(universe.get("player_name"), identity.get("player_name"), player_name),
        "pos": pick(universe.get("pos"), identity.get("pos")),
        "nfl_team": pick(universe.get("nfl_team"), identity.get("nfl_team")),
        "sleeper_id": pick(universe.get("sleeper_id"), identity.get("sleeper_id")),
        "gsis_id": pick(universe.get("gsis_id")),
        "age": pick(identity.get("age")),
        "years_exp": pick(universe.get("years_exp"), identity.get("years_exp")),
        "college": pick(universe.get("college"), identity.get("college")),
        "active": universe.get("active"),
        "nfl_status": universe.get("nfl_status"),
        "current_owner": universe.get("current_owner"),
        "rookie_class_year": pick(universe.get("rookie_class_year"), identity.get("rookie_class_year")),
        "identity_confidence": num(identity.get("identity_confidence")),
        "_raw": {
            "player_universe": universe,
            "player_identity_context": identity,
        },
    }
