from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

from season_engine import (
    DuplicateActiveSeasonError,
    DuplicateLeagueSeasonError,
    LeagueSeason,
    SeasonNotFoundError,
    SeasonResolver,
    SeasonStatus,
)


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self.rows = [row for row in self.rows if row.get(column) == value]
        return self

    def execute(self):
        return Response(self.rows)


class Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        if name == "leagues":
            return Query([{"id": "league-1"}])
        if name != "league_seasons":
            raise AssertionError(f"Unexpected season authority table: {name}")
        return Query(self.rows)


def resolver(*rows):
    return SeasonResolver(Client(rows))


class SeasonResolverTest(unittest.TestCase):
    def test_initial_managed_state_resolves_active_upcoming_and_next(self):
        subject = resolver(
            {"id": "a", "league_id": "league-1", "season": 2025, "sleeper_league_id": "old", "is_active": True},
            {"id": "b", "league_id": "league-1", "season": 2026, "sleeper_league_id": "renewed", "is_active": False},
        )
        active = subject.get_active_season("league-1")
        self.assertEqual((active.season, active.sleeper_league_id), (2025, "old"))
        self.assertEqual(subject.get_next_season("league-1"), 2026)
        self.assertEqual(active.status_relative_to(2025), SeasonStatus.ACTIVE)
        upcoming = LeagueSeason.from_row({"league_id": "league-1", "season": 2026, "is_active": False})
        self.assertEqual(upcoming.status_relative_to(2025), SeasonStatus.SCHEDULED)
        with self.assertRaisesRegex(SeasonNotFoundError, "no completed season"):
            subject.get_completed_season("league-1")

    def test_latest_prior_nonactive_season_is_completed(self):
        subject = resolver(
            {"league_id": "league-1", "season": 2024, "is_active": False},
            {"league_id": "league-1", "season": 2025, "is_active": False},
            {"league_id": "league-1", "season": 2026, "is_active": True},
        )
        self.assertEqual(subject.get_completed_season("league-1").season, 2025)

    def test_requires_exactly_one_active_season(self):
        with self.assertRaisesRegex(SeasonNotFoundError, "no active"):
            resolver({"league_id": "league-1", "season": 2025, "is_active": False}).get_active_season("league-1")
        with self.assertRaises(DuplicateActiveSeasonError):
            resolver(
                {"league_id": "league-1", "season": 2025, "is_active": True},
                {"league_id": "league-1", "season": 2026, "is_active": True},
            ).get_active_season("league-1")

    def test_rejects_duplicate_league_season_rows(self):
        with self.assertRaises(DuplicateLeagueSeasonError):
            resolver(
                {"league_id": "league-1", "season": 2026, "is_active": True},
                {"league_id": "league-1", "season": 2026, "is_active": False},
            ).get_active_season("league-1")

    def test_does_not_use_rows_from_another_league(self):
        with self.assertRaises(SeasonNotFoundError):
            resolver({"league_id": "league-2", "season": 2026, "is_active": True}).get_active_season("league-1")

    def test_snapshot_generation_labels_source_as_active_2025(self):
        from snapshot import build_snapshot as module

        client = Client([
            {"league_id": "league-1", "season": 2025, "sleeper_league_id": "sleeper-2025", "is_active": True},
            {"league_id": "league-1", "season": 2026, "sleeper_league_id": "sleeper-2026", "is_active": False},
        ])
        empty_loaders = (
            "load_teams", "load_owners", "load_contracts", "load_cap_adjustments", "load_draft_picks",
            "load_player_rankings", "load_player_season_stats", "load_player_career_features", "load_player_engine_scores",
        )
        empty_builders = (
            "build_cap_snapshot", "build_roster_snapshot", "build_teams_snapshot", "build_standings_snapshot",
            "build_draft_picks_snapshot", "build_player_rankings_snapshot", "build_player_season_stats_snapshot",
            "build_player_career_features_snapshot",
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(module, "service_client", return_value=client))
            stack.enter_context(patch.object(module, "_load_league_rules", return_value={}))
            for name in empty_loaders:
                stack.enter_context(patch.object(module, name, return_value=pd.DataFrame()))
            for name in empty_builders:
                stack.enter_context(patch.object(module, name, return_value=pd.DataFrame()))
            stack.enter_context(patch.object(module, "build_players_snapshot", return_value=pd.DataFrame()))
            snapshot = module.build_snapshot()
        self.assertEqual(snapshot["metadata"]["season"], 2025)
        self.assertEqual(snapshot["metadata"]["sleeper_league_id"], "sleeper-2025")


if __name__ == "__main__":
    unittest.main()
