from __future__ import annotations

from .common import find_one, pick, num


def build_production(player_name: str, owner_team_name: str | None = None) -> dict:
    universe = find_one("player_universe", player_name, owner_team_name)
    identity = find_one("player_identity_context", player_name)

    return {
        "expected_ppg": num(universe.get("expected_ppg")),
        "historical_ppg": num(universe.get("historical_ppg")),
        "season_ppg": num(universe.get("season_ppg")),
        "season_games": num(universe.get("season_games")),
        "latest_week_points": num(universe.get("latest_week_points")),
        "latest_week_ppr": num(universe.get("latest_week_ppr")),
        "latest_season": universe.get("latest_season"),
        "latest_week": universe.get("latest_week"),
        "production_trend_score": num(identity.get("production_trend_score")),
        "historical_context_score": num(identity.get("historical_context_score")),
        "_raw": {
            "player_universe": universe,
            "player_identity_context": identity,
        },
    }
