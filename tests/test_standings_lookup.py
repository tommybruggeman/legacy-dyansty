import unittest

import pandas as pd

from services.standings_lookup import (
    build_sleeper_team_identities,
    resolve_team_standings_row,
    standings_for_team_cards,
)


class StandingsLookupTest(unittest.TestCase):
    def setUp(self):
        self.identities = build_sleeper_team_identities(
            [
                {"user_id": "sleeper-1", "username": "chaseseyforth", "display_name": "Chase"},
                {"user_id": "sleeper-2", "display_name": "tommybruggeman"},
            ],
            [
                {"roster_id": 4, "owner_id": "sleeper-1"},
                {"roster_id": 9, "owner_id": "sleeper-2"},
            ],
        )
        self.standings = pd.DataFrame(
            [
                {"owner": "chaseseyforth", "wins": 7, "losses": 2, "standing_points": 18, "ppg": 143.2},
                {"owner": "tommybruggeman", "wins": 5, "losses": 4, "standing_points": 14, "ppg": 136.8},
            ]
        )

    def test_resolves_display_name_mismatch_through_roster_id(self):
        row = resolve_team_standings_row(
            self.standings,
            sleeper_roster_id=4,
            identities_by_roster_id=self.identities,
        )
        self.assertEqual(row.iloc[0]["owner"], "chaseseyforth")
        self.assertEqual(row.iloc[0]["wins"], 7)

    def test_resolves_my_team_through_owner_id_not_contract_owner_name(self):
        row = resolve_team_standings_row(
            self.standings,
            sleeper_owner_id="sleeper-2",
            identities_by_roster_id=self.identities,
        )
        self.assertEqual(row.iloc[0]["owner"], "tommybruggeman")
        self.assertEqual(row.iloc[0]["standing_points"], 14)

    def test_does_not_fall_back_to_display_name(self):
        display_named = pd.DataFrame([{"owner": "Tommy Bruggeman", "wins": 99}])
        row = resolve_team_standings_row(
            display_named,
            sleeper_roster_id=9,
            identities_by_roster_id=self.identities,
        )
        self.assertTrue(row.empty)

    def test_prefers_identifier_columns_when_snapshot_has_them(self):
        standings = pd.DataFrame(
            [{"roster_id": 4, "owner": "renamed-handle", "wins": 8}]
        )
        row = resolve_team_standings_row(
            standings,
            sleeper_roster_id=4,
            identities_by_roster_id=self.identities,
        )
        self.assertEqual(row.iloc[0]["wins"], 8)

    def test_normalizes_the_standings_page_columns_for_cards(self):
        normalized = standings_for_team_cards(pd.DataFrame([{
            "Team": "tommybruggeman", "Wins": 4, "Losses": 0,
            "PF Per Game": 71.4, "Standing Points": 11,
        }]))
        self.assertEqual(normalized.iloc[0]["wins"], 4)
        self.assertEqual(normalized.iloc[0]["ppg"], 71.4)
        self.assertEqual(normalized.iloc[0]["standing_points"], 11)


if __name__ == "__main__":
    unittest.main()
