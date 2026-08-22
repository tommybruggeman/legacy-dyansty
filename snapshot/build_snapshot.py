from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from auth import service_client

from snapshot.loaders.teams import load_teams
from snapshot.loaders.owners import load_owners
from snapshot.loaders.contracts import load_contracts
from snapshot.loaders.cap import load_cap_adjustments
from snapshot.loaders.draft_picks import load_draft_picks
from snapshot.loaders.player_rankings import load_player_rankings
from snapshot.loaders.player_season_stats import load_player_season_stats
from snapshot.loaders.player_career_features import load_player_career_features
from snapshot.loaders.player_engine_scores import load_player_engine_scores

from snapshot.builders.cap.build_cap_snapshot import build_cap_snapshot
from snapshot.builders.roster.build_roster_snapshot import build_roster_snapshot
from snapshot.builders.team.build_teams_snapshot import build_teams_snapshot
from snapshot.builders.standings.build_standings_snapshot import build_standings_snapshot
from snapshot.builders.draft.build_draft_picks_snapshot import build_draft_picks_snapshot

from snapshot.builders.player.build_player_rankings_snapshot import build_player_rankings_snapshot
from snapshot.builders.player.build_player_season_stats_snapshot import build_player_season_stats_snapshot
from snapshot.builders.player.build_player_career_features_snapshot import build_player_career_features_snapshot
from snapshot.builders.player.build_players_snapshot import build_players_snapshot
from season_engine import SeasonResolver
from season_engine.service import resolve_single_league_id


def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []

    return df.where(pd.notnull(df), None).to_dict(orient="records")


def _load_league_rules(league_id: str) -> dict:
    sb = service_client()

    rows = (
        sb.table("league_rules")
        .select("*")
        .eq("league_id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    return rows[0] if rows else {}


def build_snapshot(
    league_id: str | None = None,
    sleeper_league_id: str | None = None,
    season: int | None = None,
) -> dict:
    sb = service_client()
    league_id = league_id or resolve_single_league_id(sb)
    authority = SeasonResolver(sb).get_active_season(league_id)
    season = season or authority.season
    sleeper_league_id = sleeper_league_id or authority.sleeper_league_id
    if not sleeper_league_id:
        raise RuntimeError(f"Active season {season} for league {league_id} has no Sleeper league ID.")
    teams = load_teams()
    owners = load_owners()
    contracts = load_contracts()
    cap_adjustments = load_cap_adjustments()
    draft_picks = load_draft_picks()

    player_rankings_raw = load_player_rankings()
    player_season_stats_raw = load_player_season_stats()
    player_career_features_raw = load_player_career_features()
    player_engine_scores_raw = load_player_engine_scores()

    league_rules = _load_league_rules(league_id)

    base_ctx = {
        "league_id": league_id,
        "sleeper_league_id": sleeper_league_id,
        "season": season,
        "teams": teams,
        "owners": owners,
        "contracts": contracts,
        "cap_adjustments": cap_adjustments,
        "draft_picks": draft_picks,
        "league_rules": league_rules,
    }

    cap_snapshot = build_cap_snapshot(base_ctx)
    roster_snapshot = build_roster_snapshot(base_ctx)

    team_snapshot = build_teams_snapshot(
        {
            **base_ctx,
            "cap_snapshot": cap_snapshot,
            "roster_snapshot": roster_snapshot,
        }
    )

    standings_snapshot = build_standings_snapshot(base_ctx)
    draft_picks_snapshot = build_draft_picks_snapshot(base_ctx)

    player_rankings_snapshot = build_player_rankings_snapshot(
        {"player_rankings": player_rankings_raw}
    )

    player_season_stats_snapshot = build_player_season_stats_snapshot(
        {"player_season_stats": player_season_stats_raw}
    )

    player_career_features_snapshot = build_player_career_features_snapshot(
        {"player_career_features": player_career_features_raw}
    )

    players_snapshot = build_players_snapshot(
        {
            "player_rankings_snapshot": player_rankings_snapshot,
            "player_season_stats_snapshot": player_season_stats_snapshot,
            "player_career_features_snapshot": player_career_features_snapshot,
            "player_engine_scores": player_engine_scores_raw,
        }
    )

    return {
        "metadata": {
            "snapshot_version": "v1",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "league_id": league_id,
            "sleeper_league_id": sleeper_league_id,
            "season": season,
        },
        "league": {
            "league_id": league_id,
            "sleeper_league_id": sleeper_league_id,
            "season": season,
            "rules": league_rules,
        },
        "teams": _records(team_snapshot),
        "rosters": _records(roster_snapshot),
        "cap": _records(cap_snapshot),
        "standings": _records(standings_snapshot),
        "draft_picks": _records(draft_picks_snapshot),
        "players": _records(players_snapshot),
        "player_rankings": _records(player_rankings_snapshot),
        "player_season_stats": _records(player_season_stats_snapshot),
        "player_career_features": _records(player_career_features_snapshot),
    }


if __name__ == "__main__":
    snapshot = build_snapshot()

    print("Snapshot built successfully")
    print("Top-level keys:", list(snapshot.keys()))

    for key in [
        "teams",
        "rosters",
        "cap",
        "standings",
        "draft_picks",
        "players",
        "player_rankings",
        "player_season_stats",
        "player_career_features",
    ]:
        print(f"{key}: {len(snapshot.get(key, []))}")

    print("\nSample team:")
    print(snapshot["teams"][:1])

    print("\nSample cap:")
    print(snapshot["cap"][:1])

    print("\nSample player:")
    print(snapshot["players"][:1])
