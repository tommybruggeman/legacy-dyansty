from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

strategic_builder = importlib.import_module("snapshot.builders.strategy.build_player_strategic_profiles")
relative_builder = importlib.import_module("snapshot.builders.brain.build_league_relative_player_values")
team_builder = importlib.import_module("snapshot.builders.brain.build_team_brain")
league_builder = importlib.import_module("snapshot.builders.brain.build_league_brain")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name, action="select", payload=None, on_conflict=None):
        self.client = client
        self.table_name = table_name
        self.action = action
        self.payload = payload
        self.on_conflict = on_conflict
        self.filters = []
        self.limit_value = None

    def select(self, _columns="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        if self.action == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            inserted = []
            for payload in payloads:
                row = {"id": f"{self.table_name}-{len(self.client.rows.get(self.table_name, [])) + 1}", **payload}
                self.client.rows.setdefault(self.table_name, []).append(row)
                self.client.inserted.append((self.table_name, row))
                inserted.append(row)
            return Result(inserted)

        if self.action == "update":
            rows = self.client.rows.setdefault(self.table_name, [])
            matched = []
            for index, row in enumerate(rows):
                if all(str(row.get(key)) == str(value) for key, value in self.filters):
                    rows[index] = {**row, **self.payload}
                    matched.append(rows[index])
            self.client.updated.append((self.table_name, self.payload, list(self.filters)))
            return Result(matched)

        if self.action == "upsert":
            rows = self.client.rows.setdefault(self.table_name, [])
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            conflict_keys = [key.strip() for key in (self.on_conflict or "").split(",") if key.strip()]
            written = []
            for payload in payloads:
                found = False
                for index, row in enumerate(rows):
                    if conflict_keys and all(row.get(key) == payload.get(key) for key in conflict_keys):
                        rows[index] = {**row, **payload}
                        written.append(rows[index])
                        found = True
                        break
                if not found:
                    row = {"id": f"{self.table_name}-{len(rows) + 1}", **payload}
                    rows.append(row)
                    written.append(row)
            self.client.upserts.append((self.table_name, payloads, self.on_conflict))
            return Result(written)

        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]

        if self.limit_value is not None:
            rows = rows[: self.limit_value]

        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, columns="*"):
        return FakeQuery(self.client, self.table_name).select(columns)

    def insert(self, payload):
        return FakeQuery(self.client, self.table_name, action="insert", payload=payload)

    def update(self, payload):
        return FakeQuery(self.client, self.table_name, action="update", payload=payload)

    def upsert(self, payload, on_conflict=None):
        return FakeQuery(self.client, self.table_name, action="upsert", payload=payload, on_conflict=on_conflict)


class FakeClient:
    def __init__(self):
        self.inserted = []
        self.updated = []
        self.upserts = []
        self.rows = {
            "leagues": [{"id": "league-1"}, {"id": "league-2"}],
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Same Name", "owner_name": "Owner One"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Other Team", "owner_name": "Owner Two"},
                {"id": "team-chasen", "league_id": "league-1", "team_name": "Chasen Hardy", "owner_name": "Chasen Hardy"},
                {"id": "team-dylan", "league_id": "league-1", "team_name": "Dylan Burruel", "owner_name": "Dylan Burruel"},
                {"id": "team-3", "league_id": "league-2", "team_name": "Same Name", "owner_name": "Owner Three"},
            ],
            "player_recommendations": [
                {
                    "league_id": "league-1",
                    "owner_team_name": "Same Name",
                    "sleeper_id": "p1",
                    "player_name": "Player One",
                    "pos": "QB",
                    "dynasty_asset_score": 70,
                    "win_now_score": 80,
                },
                {
                    "league_id": "league-1",
                    "owner_team_name": "Missing Team",
                    "sleeper_id": "p2",
                    "player_name": "Player Two",
                    "pos": "RB",
                    "dynasty_asset_score": 50,
                    "win_now_score": 50,
                },
                {
                    "league_id": "league-2",
                    "owner_team_name": "Same Name",
                    "sleeper_id": "p3",
                    "player_name": "Player Three",
                    "pos": "WR",
                    "dynasty_asset_score": 55,
                    "win_now_score": 55,
                },
            ],
            "player_strategic_profiles": [
                {
                    "id": "profile-existing-other-league",
                    "league_id": "league-2",
                    "league_team_id": "team-3",
                    "owner_team_name": "Same Name",
                    "sleeper_id": "p-other",
                    "player_name": "Other League Player",
                    "pos": "QB",
                    "asset_score": 1,
                    "win_now_score": 1,
                    "median_projection": 1,
                    "opportunity_score": 1,
                }
            ],
            "league_relative_player_values": [
                {
                    "id": "relative-existing-other-league",
                    "league_id": "league-2",
                    "league_team_id": "team-3",
                    "owner_team_name": "Same Name",
                    "sleeper_id": "p-other",
                    "player_name": "Other League Player",
                    "pos": "QB",
                }
            ],
            "player_identity_map": [
                {
                    "sleeper_id": "11571",
                    "canonical_player_id": "11571",
                    "player_name": "Isaiah Davis",
                    "pos": "RB",
                }
            ],
            "player_universe": [],
            "player_engine_scores": [],
            "player_season_stats": [],
            "players": [],
            "sleeper_players": [],
            "team_brain": [],
            "league_brain": [],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class FakeEngine:
    def evaluate(self, player):
        return SimpleNamespace(
            player_name=player["player_name"],
            pos=player["pos"],
            strategic_label="CORE",
            action="HOLD",
            confidence=0.9,
            median_projection=10,
            opportunity_score=60,
            volatility_label="STABLE",
            contract_flag="OK",
            asset_score=player.get("dynasty_asset_score") or 0,
            win_now_score=player.get("win_now_score") or 0,
            explanation="test",
        )


class AssistantBrainBuilderTest(unittest.TestCase):
    def add_team_brain_inputs(self, client, teams):
        for team_name, league_team_id in teams:
            client.rows["league_relative_player_values"].append({
                "league_id": "league-1",
                "league_team_id": league_team_id,
                "owner_team_name": team_name,
                "sleeper_id": f"player-{league_team_id}",
                "player_name": f"Player {team_name}",
                "pos": "QB",
                "asset_score": 70,
                "win_now_score": 80,
                "opportunity_score": 60,
                "median_projection": 10,
                "overall_value_score": 70,
                "overall_percentile": 90,
                "position_overall_percentile": 90,
                "league_value_tier": "LEAGUE_ELITE",
            })
            client.rows["player_strategic_profiles"].append({
                "league_id": "league-1",
                "league_team_id": league_team_id,
                "owner_team_name": team_name,
                "sleeper_id": f"player-{league_team_id}",
                "strategic_label": "CORE",
                "contract_flag": "OK",
                "action": "HOLD",
            })

    def run_team_builder(self, client, **kwargs):
        original_team_service = team_builder.service_client
        team_builder.service_client = lambda: client
        try:
            return team_builder.build_team_brain(**kwargs)
        finally:
            team_builder.service_client = original_team_service

    def test_strategic_profiles_scope_and_skip_unresolved_team(self):
        client = FakeClient()
        original_engine = strategic_builder.PlayerStrategicProfileEngine
        strategic_builder.PlayerStrategicProfileEngine = lambda samples=100: FakeEngine()
        try:
            result = strategic_builder.build_player_strategic_profiles(
                league_id="league-1",
                dry_run=False,
                sb=client,
            )
        finally:
            strategic_builder.PlayerStrategicProfileEngine = original_engine

        self.assertEqual(result["input_count"], 2)
        self.assertEqual(result["prepared_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        inserted = [row for table, row in client.inserted if table == "player_strategic_profiles"]
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0]["league_id"], "league-1")
        self.assertEqual(inserted[0]["league_team_id"], "team-1")

    def test_strategic_profiles_dry_run_does_not_write(self):
        client = FakeClient()
        original_engine = strategic_builder.PlayerStrategicProfileEngine
        strategic_builder.PlayerStrategicProfileEngine = lambda samples=100: FakeEngine()
        try:
            result = strategic_builder.build_player_strategic_profiles(
                league_id="league-1",
                dry_run=True,
                sb=client,
            )
        finally:
            strategic_builder.PlayerStrategicProfileEngine = original_engine

        self.assertEqual(result["written_count"], 0)
        self.assertEqual(client.inserted, [])
        self.assertEqual(client.updated, [])

    def test_strategic_profiles_resolves_explicit_team_alias(self):
        client = FakeClient()
        client.rows["player_recommendations"] = [
            {
                "league_id": "league-1",
                "owner_team_name": "Dburruel",
                "sleeper_id": "isaiah-davis",
                "player_name": "Isaiah Davis",
                "pos": "RB",
                "dynasty_asset_score": 45,
                "win_now_score": 40,
            }
        ]
        original_engine = strategic_builder.PlayerStrategicProfileEngine
        strategic_builder.PlayerStrategicProfileEngine = lambda samples=100: FakeEngine()
        try:
            result = strategic_builder.build_player_strategic_profiles(
                league_id="league-1",
                dry_run=False,
                sb=client,
            )
        finally:
            strategic_builder.PlayerStrategicProfileEngine = original_engine

        self.assertEqual(result["prepared_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        inserted = [row for table, row in client.inserted if table == "player_strategic_profiles"]
        self.assertEqual(inserted[0]["league_team_id"], "team-dylan")
        self.assertEqual(inserted[0]["owner_team_name"], "Dylan Burruel")

    def test_strategic_profiles_resolves_placeholder_sleeper_ids(self):
        placeholders = [None, "None", "null", "", "   "]

        for placeholder in placeholders:
            with self.subTest(placeholder=placeholder):
                client = FakeClient()
                client.rows["player_recommendations"] = [
                    {
                        "league_id": "league-1",
                        "owner_team_name": "Dburruel",
                        "sleeper_id": placeholder,
                        "player_name": "Isaiah Davis",
                        "pos": "RB",
                        "dynasty_asset_score": 45,
                        "win_now_score": 40,
                    }
                ]
                original_engine = strategic_builder.PlayerStrategicProfileEngine
                strategic_builder.PlayerStrategicProfileEngine = lambda samples=100: FakeEngine()
                try:
                    result = strategic_builder.build_player_strategic_profiles(
                        league_id="league-1",
                        dry_run=False,
                        sb=client,
                    )
                finally:
                    strategic_builder.PlayerStrategicProfileEngine = original_engine

                self.assertEqual(result["prepared_count"], 1)
                self.assertEqual(result["skipped_count"], 0)
                inserted = [row for table, row in client.inserted if table == "player_strategic_profiles"]
                self.assertEqual(inserted[0]["sleeper_id"], "11571")
                self.assertNotIn(str(inserted[0]["sleeper_id"]).lower(), {"none", "null", ""})

    def test_strategic_profiles_skips_unresolved_missing_sleeper_id(self):
        client = FakeClient()
        client.rows["player_recommendations"] = [
            {
                "league_id": "league-1",
                "owner_team_name": "Same Name",
                "sleeper_id": "None",
                "player_name": "No Local Identity",
                "pos": "RB",
                "dynasty_asset_score": 45,
                "win_now_score": 40,
            }
        ]
        original_engine = strategic_builder.PlayerStrategicProfileEngine
        strategic_builder.PlayerStrategicProfileEngine = lambda samples=100: FakeEngine()
        try:
            result = strategic_builder.build_player_strategic_profiles(
                league_id="league-1",
                dry_run=False,
                sb=client,
            )
        finally:
            strategic_builder.PlayerStrategicProfileEngine = original_engine

        self.assertEqual(result["prepared_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(client.inserted, [])
        self.assertEqual(client.updated, [])

    def test_strategic_profiles_updates_legacy_unique_key_row_idempotently(self):
        client = FakeClient()
        client.rows["player_recommendations"] = [
            {
                "league_id": "league-1",
                "owner_team_name": "Chasen Hardy",
                "sleeper_id": "7021",
                "player_name": "Legacy Existing Player",
                "pos": "RB",
                "dynasty_asset_score": 45,
                "win_now_score": 40,
            }
        ]
        client.rows["player_strategic_profiles"].append({
            "id": "legacy-profile-7021",
            "league_id": None,
            "league_team_id": None,
            "owner_team_name": "Chasen Hardy",
            "sleeper_id": "7021",
            "player_name": "Old Name",
            "pos": "RB",
        })
        original_engine = strategic_builder.PlayerStrategicProfileEngine
        strategic_builder.PlayerStrategicProfileEngine = lambda samples=100: FakeEngine()
        try:
            first = strategic_builder.build_player_strategic_profiles(
                league_id="league-1",
                dry_run=False,
                sb=client,
            )
            second = strategic_builder.build_player_strategic_profiles(
                league_id="league-1",
                dry_run=False,
                sb=client,
            )
        finally:
            strategic_builder.PlayerStrategicProfileEngine = original_engine

        self.assertEqual(first["inserted_count"], 0)
        self.assertEqual(first["updated_count"], 1)
        self.assertEqual(second["inserted_count"], 0)
        self.assertEqual(second["updated_count"], 1)
        self.assertEqual(
            len([
                row for row in client.rows["player_strategic_profiles"]
                if row.get("sleeper_id") == "7021" and row.get("owner_team_name") == "Chasen Hardy"
            ]),
            1,
        )
        row = [
            row for row in client.rows["player_strategic_profiles"]
            if row.get("id") == "legacy-profile-7021"
        ][0]
        self.assertEqual(row["league_id"], "league-1")
        self.assertEqual(row["league_team_id"], "team-chasen")
        self.assertEqual(client.inserted, [])

    def test_relative_values_scope_propagation_and_no_cross_league_overwrite(self):
        client = FakeClient()
        client.rows["player_strategic_profiles"].append({
            "id": "profile-league-1",
            "league_id": "league-1",
            "league_team_id": "team-1",
            "owner_team_name": "Same Name",
            "sleeper_id": "p1",
            "player_name": "Player One",
            "pos": "QB",
            "asset_score": 70,
            "win_now_score": 80,
            "median_projection": 10,
            "opportunity_score": 60,
        })

        result = relative_builder.build_league_relative_player_values(
            league_id="league-1",
            league_team_id="team-1",
            dry_run=False,
            sb=client,
        )

        self.assertEqual(result["prepared_count"], 1)
        league_two_row = [
            row for row in client.rows["league_relative_player_values"]
            if row.get("id") == "relative-existing-other-league"
        ][0]
        self.assertEqual(league_two_row["player_name"], "Other League Player")

        inserted = [row for table, row in client.inserted if table == "league_relative_player_values"]
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0]["league_id"], "league-1")
        self.assertEqual(inserted[0]["league_team_id"], "team-1")

    def test_relative_values_dry_run_does_not_write(self):
        client = FakeClient()
        client.rows["player_strategic_profiles"].append({
            "league_id": "league-1",
            "league_team_id": "team-1",
            "owner_team_name": "Same Name",
            "sleeper_id": "p1",
            "player_name": "Player One",
            "pos": "QB",
            "asset_score": 70,
            "win_now_score": 80,
            "median_projection": 10,
            "opportunity_score": 60,
        })

        result = relative_builder.build_league_relative_player_values(
            league_id="league-1",
            dry_run=True,
            sb=client,
        )

        self.assertEqual(result["written_count"], 0)
        self.assertEqual(client.inserted, [])
        self.assertEqual(client.updated, [])

    def test_team_and_league_brain_scoped_builders(self):
        client = FakeClient()
        client.rows["league_relative_player_values"].append({
            "league_id": "league-1",
            "league_team_id": "team-1",
            "owner_team_name": "Same Name",
            "sleeper_id": "p1",
            "player_name": "Player One",
            "pos": "QB",
            "asset_score": 70,
            "win_now_score": 80,
            "opportunity_score": 60,
            "median_projection": 10,
            "overall_value_score": 70,
            "overall_percentile": 90,
            "position_overall_percentile": 90,
            "league_value_tier": "LEAGUE_ELITE",
        })
        client.rows["player_strategic_profiles"].append({
            "league_id": "league-1",
            "league_team_id": "team-1",
            "owner_team_name": "Same Name",
            "sleeper_id": "p1",
            "strategic_label": "CORE",
            "contract_flag": "OK",
            "action": "HOLD",
        })

        original_team_service = team_builder.service_client
        original_league_service = league_builder.service_client
        team_builder.service_client = lambda: client
        league_builder.service_client = lambda: client
        try:
            team_result = team_builder.build_team_brain(league_id="league-1", dry_run=False)
            league_result = league_builder.build_league_brain(league_id="league-1", dry_run=False)
        finally:
            team_builder.service_client = original_team_service
            league_builder.service_client = original_league_service

        self.assertEqual(team_result["prepared_count"], 1)
        self.assertEqual(client.rows["team_brain"][0]["league_id"], "league-1")
        self.assertEqual(client.rows["team_brain"][0]["league_team_id"], "team-1")
        self.assertEqual(league_result["prepared_count"], 1)
        self.assertEqual(client.rows["league_brain"][0]["league_id"], "league-1")

    def test_team_brain_updates_ten_unscoped_legacy_team_rows(self):
        client = FakeClient()
        teams = [(f"Legacy Team {i}", f"legacy-team-{i}") for i in range(10)]
        self.add_team_brain_inputs(client, teams)
        for team_name, _league_team_id in teams:
            client.rows["team_brain"].append({
                "id": f"legacy-{team_name}",
                "league_id": None,
                "league_team_id": None,
                "team_name": team_name,
            })

        result = self.run_team_builder(client, league_id="league-1", dry_run=False)

        self.assertEqual(result["prepared_count"], 10)
        self.assertEqual(result["updated_count"], 10)
        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(
            len([payload for table, payload, _filters in client.updated if table == "team_brain"]),
            10,
        )
        self.assertEqual(
            len([row for table, row in client.inserted if table == "team_brain"]),
            0,
        )
        for team_name, league_team_id in teams:
            row = [row for row in client.rows["team_brain"] if row["team_name"] == team_name][0]
            self.assertEqual(row["league_id"], "league-1")
            self.assertEqual(row["league_team_id"], league_team_id)

    def test_team_brain_updates_modern_scoped_match(self):
        client = FakeClient()
        self.add_team_brain_inputs(client, [("Same Name", "team-1")])
        client.rows["team_brain"].append({
            "id": "modern-team-1",
            "league_id": "league-1",
            "league_team_id": "team-1",
            "team_name": "Same Name",
        })

        result = self.run_team_builder(client, league_id="league-1", dry_run=False)

        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["inserted_count"], 0)
        self.assertEqual(client.rows["team_brain"][0]["league_team_id"], "team-1")

    def test_team_brain_mixed_partial_prior_writes(self):
        client = FakeClient()
        teams = [("Same Name", "team-1"), ("Other Team", "team-2"), ("Dylan Burruel", "team-dylan")]
        self.add_team_brain_inputs(client, teams)
        client.rows["team_brain"].extend([
            {
                "id": "modern-team-1",
                "league_id": "league-1",
                "league_team_id": "team-1",
                "team_name": "Same Name",
            },
            {
                "id": "legacy-team-2",
                "league_id": None,
                "league_team_id": None,
                "team_name": "Other Team",
            },
        ])

        result = self.run_team_builder(client, league_id="league-1", dry_run=False)

        self.assertEqual(result["prepared_count"], 3)
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual(result["inserted_count"], 1)
        self.assertEqual(
            len([row for row in client.rows["team_brain"] if row["team_name"] == "Other Team"]),
            1,
        )
        self.assertEqual(
            len([row for row in client.rows["team_brain"] if row["team_name"] == "Dylan Burruel"]),
            1,
        )

    def test_team_brain_refuses_cross_league_team_name_collision(self):
        client = FakeClient()
        self.add_team_brain_inputs(client, [("Same Name", "team-1")])
        client.rows["team_brain"].append({
            "id": "other-league-team",
            "league_id": "league-2",
            "league_team_id": "team-3",
            "team_name": "Same Name",
        })

        with self.assertRaisesRegex(RuntimeError, "another league"):
            self.run_team_builder(client, league_id="league-1", dry_run=False)

        self.assertEqual(
            len([row for table, row in client.inserted if table == "team_brain"]),
            0,
        )

    def test_team_brain_idempotent_after_legacy_backfill(self):
        client = FakeClient()
        self.add_team_brain_inputs(client, [("Same Name", "team-1")])
        client.rows["team_brain"].append({
            "id": "legacy-team-1",
            "league_id": None,
            "league_team_id": None,
            "team_name": "Same Name",
        })

        first = self.run_team_builder(client, league_id="league-1", dry_run=False)
        second = self.run_team_builder(client, league_id="league-1", dry_run=False)

        self.assertEqual(first["inserted_count"], 0)
        self.assertEqual(first["updated_count"], 1)
        self.assertEqual(second["inserted_count"], 0)
        self.assertEqual(second["updated_count"], 1)
        self.assertEqual(
            len([row for row in client.rows["team_brain"] if row["team_name"] == "Same Name"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
