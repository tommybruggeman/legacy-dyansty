from __future__ import annotations

import importlib
import inspect
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

auth_stub = types.ModuleType("auth")
auth_stub.current_user = lambda: {"id": "user-1"}
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

brain_context = importlib.import_module("gm_assistant.brain_context")
coach_condor = importlib.import_module("gm_assistant.coach_condor")


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

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        if self.action == "upsert":
            return self._execute_upsert()

        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]

        if self.limit_value is not None:
            rows = rows[: self.limit_value]

        return Result(rows)

    def _execute_upsert(self):
        rows = self.client.rows.setdefault(self.table_name, [])
        payload = dict(self.payload)
        conflict_keys = [key.strip() for key in (self.on_conflict or "").split(",") if key.strip()]

        if conflict_keys:
            for index, row in enumerate(rows):
                if all(row.get(key) == payload.get(key) for key in conflict_keys):
                    rows[index] = {**row, **payload}
                    return Result([rows[index]])

        rows.append(payload)
        return Result([payload])


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)

    def upsert(self, payload, on_conflict=None):
        return FakeQuery(
            self.client,
            self.table_name,
            action="upsert",
            payload=payload,
            on_conflict=on_conflict,
        )


class FakeClient:
    def __init__(self):
        self.rows = {
            "league_memberships": [
                {
                    "id": "membership-1",
                    "user_id": "user-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_id": "legacy-1",
                    "role": "member",
                },
                {
                    "id": "membership-2",
                    "user_id": "user-1",
                    "league_id": "league-2",
                    "league_team_id": "team-2",
                    "team_id": "legacy-2",
                    "role": "member",
                },
                {
                    "id": "membership-3",
                    "user_id": "user-2",
                    "league_id": "league-1",
                    "league_team_id": "team-3",
                    "team_id": "legacy-3",
                    "role": "member",
                },
                {
                    "id": "membership-4",
                    "user_id": "user-3",
                    "league_id": "league-1",
                    "league_team_id": None,
                    "team_id": "team-4",
                    "role": "member",
                },
                {
                    "id": "membership-5",
                    "user_id": "user-4",
                    "league_id": "league-1",
                    "league_team_id": None,
                    "team_id": None,
                    "role": "member",
                },
                {
                    "id": "membership-6",
                    "user_id": "user-5",
                    "league_id": "league-1",
                    "league_team_id": None,
                    "team_id": "team-2",
                    "role": "member",
                },
            ],
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Same Team Name"},
                {"id": "team-2", "league_id": "league-2", "team_name": "Same Team Name"},
                {"id": "team-3", "league_id": "league-1", "team_name": "Other Team"},
                {"id": "team-4", "league_id": "league-1", "team_name": "Fallback Team"},
            ],
            "team_brain": [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Same Team Name",
                    "team_direction": "CONTEND_NOW",
                    "position_strengths": ["QB"],
                    "position_needs": ["RB"],
                    "core_players": ["Josh Allen"],
                },
                {
                    "league_id": "league-2",
                    "league_team_id": "team-2",
                    "team_name": "Same Team Name",
                    "team_direction": "RETOOL",
                    "position_strengths": ["WR"],
                    "position_needs": ["TE"],
                    "core_players": ["Garrett Wilson"],
                },
                {
                    "league_id": "league-1",
                    "league_team_id": "team-4",
                    "team_name": "Fallback Team",
                    "team_direction": "CONTEND_NOW",
                    "position_strengths": ["WR"],
                    "position_needs": ["QB"],
                    "core_players": ["Fallback Player"],
                },
            ],
            "league_brain": [
                {
                    "league_id": "league-1",
                    "summary": "League one only.",
                    "trade_fits": [],
                },
                {
                    "league_id": "league-2",
                    "summary": "League two only.",
                    "trade_fits": [],
                },
            ],
            "player_strategic_profiles": [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "owner_team_name": "Same Team Name",
                    "player_name": "Josh Allen",
                    "sleeper_id": "p1",
                },
                {
                    "league_id": "league-2",
                    "league_team_id": "team-2",
                    "owner_team_name": "Same Team Name",
                    "player_name": "Garrett Wilson",
                    "sleeper_id": "p2",
                },
            ],
            "league_relative_player_values": [],
            "gm_user_memory": [
                {
                    "user_id": "user-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Same Team Name",
                    "current_focus": "win now",
                    "gm_style": "balanced",
                    "risk_tolerance": "medium",
                    "team_build_preference": "contend",
                    "trade_style": "patient",
                    "preferred_strategy": "consolidate",
                },
                {
                    "user_id": "user-1",
                    "league_id": "league-2",
                    "league_team_id": "team-2",
                    "team_name": "Same Team Name",
                    "current_focus": "retool",
                    "gm_style": "balanced",
                    "risk_tolerance": "medium",
                    "team_build_preference": "build",
                    "trade_style": "patient",
                    "preferred_strategy": "collect picks",
                },
                {
                    "user_id": "user-2",
                    "league_id": "league-1",
                    "league_team_id": "team-3",
                    "team_name": "Other Team",
                    "current_focus": "separate user",
                },
                {
                    "user_id": "user-3",
                    "league_id": "league-1",
                    "league_team_id": "team-4",
                    "team_name": "Fallback Team",
                    "current_focus": "fallback context",
                },
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class AssistantIdentityTest(unittest.TestCase):
    def test_memory_separates_same_user_across_leagues(self):
        client = FakeClient()

        league_one = brain_context.load_gm_memory(
            "Same Team Name",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
            sb=client,
        )
        league_two = brain_context.load_gm_memory(
            "Same Team Name",
            user_id="user-1",
            league_id="league-2",
            league_team_id="team-2",
            sb=client,
        )

        self.assertEqual(league_one["current_focus"], "win now")
        self.assertEqual(league_two["current_focus"], "retool")

    def test_memory_separates_users_same_league(self):
        client = FakeClient()

        user_one = brain_context.load_gm_memory(
            "Same Team Name",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
            sb=client,
        )
        user_two = brain_context.load_gm_memory(
            "Other Team",
            user_id="user-2",
            league_id="league-1",
            league_team_id="team-3",
            sb=client,
        )

        self.assertEqual(user_one["current_focus"], "win now")
        self.assertEqual(user_two["current_focus"], "separate user")

    def test_team_name_collision_uses_league_and_team_ids(self):
        client = FakeClient()

        ctx = brain_context.load_gm_brain_context(
            "Same Team Name",
            user_id="user-1",
            league_id="league-2",
            league_team_id="team-2",
            sb=client,
        )

        self.assertEqual(ctx["team_brain"]["team_direction"], "RETOOL")
        self.assertEqual(ctx["league_brain"]["summary"], "League two only.")
        self.assertEqual(ctx["player_profiles"][0]["player_name"], "Garrett Wilson")

    def test_membership_with_league_team_id_populated_is_valid(self):
        client = FakeClient()

        membership = brain_context._validate_membership(
            client,
            brain_context.AssistantIdentity(
                team_name="Same Team Name",
                user_id="user-1",
                league_id="league-1",
                league_team_id="team-1",
            ),
        )

        self.assertEqual(membership["id"], "membership-1")

    def test_membership_uses_team_id_as_safe_league_team_fallback(self):
        client = FakeClient()

        ctx = brain_context.load_gm_brain_context(
            "Fallback Team",
            user_id="user-3",
            league_id="league-1",
            league_team_id="team-4",
            sb=client,
        )

        self.assertEqual(ctx["team_brain"]["team_name"], "Fallback Team")
        self.assertEqual(ctx["gm_memory"]["current_focus"], "fallback context")

    def test_membership_without_any_team_scope_is_rejected(self):
        client = FakeClient()

        with self.assertRaises(brain_context.AssistantAccessError):
            brain_context.load_gm_brain_context(
                "No Team",
                user_id="user-4",
                league_id="league-1",
                league_team_id="team-4",
                sb=client,
            )

    def test_membership_team_id_in_wrong_league_is_rejected(self):
        client = FakeClient()

        with self.assertRaises(brain_context.AssistantAccessError):
            brain_context.load_gm_brain_context(
                "Wrong League Team",
                user_id="user-5",
                league_id="league-1",
                league_team_id="team-2",
                sb=client,
            )

    def test_unauthenticated_user_is_rejected(self):
        client = FakeClient()

        with self.assertRaises(brain_context.AssistantAccessError):
            brain_context.load_gm_brain_context(
                "Same Team Name",
                user_id=None,
                league_id="league-1",
                league_team_id="team-1",
                sb=client,
            )

    def test_unauthorized_league_rejected(self):
        client = FakeClient()

        with self.assertRaises(brain_context.AssistantAccessError):
            brain_context.load_gm_brain_context(
                "Same Team Name",
                user_id="user-2",
                league_id="league-2",
                league_team_id="team-2",
                sb=client,
            )

    def test_update_requires_real_user_and_team_scope(self):
        client = FakeClient()

        with self.assertRaises(brain_context.AssistantAccessError):
            brain_context.update_gm_memory(
                "Same Team Name",
                user_id="default",
                league_id="league-1",
                league_team_id="team-1",
                sb=client,
            )

        with self.assertRaises(brain_context.AssistantAccessError):
            brain_context.update_gm_memory(
                "Same Team Name",
                user_id="user-1",
                league_id="league-1",
                league_team_id=None,
                sb=client,
            )

    def test_update_upserts_by_user_league_and_team(self):
        client = FakeClient()

        brain_context.update_gm_memory(
            "Same Team Name",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
            current_focus="trade planning",
            players_discussed=["Josh Allen"],
            sb=client,
        )

        matching = [
            row for row in client.rows["gm_user_memory"]
            if row.get("user_id") == "user-1"
            and row.get("league_id") == "league-1"
            and row.get("league_team_id") == "team-1"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["current_focus"], "trade planning")
        self.assertEqual(matching[0]["players_discussed"], ["Josh Allen"])

    def test_coach_condor_writes_one_scoped_memory_row(self):
        original_load = coach_condor.load_gm_brain_context
        original_update = coach_condor.update_gm_memory
        updates = []

        def fake_load(team_name, **kwargs):
            return {
                "team_name": team_name,
                "team_brain": {
                    "team_direction": "CONTEND_NOW",
                    "position_strengths": ["QB"],
                    "position_needs": ["RB"],
                    "trade_candidates": [],
                    "contract_problems": [],
                },
                "league_brain": {"trade_fits": []},
                "player_profiles": [],
                "relative_values": [],
                "gm_memory": {},
            }

        def fake_update(**kwargs):
            updates.append(kwargs)

        try:
            coach_condor.load_gm_brain_context = fake_load
            coach_condor.update_gm_memory = fake_update

            response = coach_condor.answer_as_coach_condor(
                "Give me a 3-step GM plan.",
                "Same Team Name",
                user_id="user-1",
                league_id="league-1",
                league_team_id="team-1",
            )
        finally:
            coach_condor.load_gm_brain_context = original_load
            coach_condor.update_gm_memory = original_update

        self.assertIn("3-step", response)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["user_id"], "user-1")
        self.assertEqual(updates[0]["league_id"], "league-1")
        self.assertEqual(updates[0]["league_team_id"], "team-1")

    def test_coach_condor_does_not_import_openai(self):
        source = inspect.getsource(coach_condor)

        self.assertNotIn("openai", source.lower())
        self.assertNotIn("OpenAI", source)

    def test_gm_assistant_page_uses_current_user_and_friendly_identity_error(self):
        source = (ROOT / "pages" / "05_GM_Assistant.py").read_text()

        self.assertIn("user = current_user() or {}", source)
        self.assertIn("request_context: AssistantRequestContext", source)
        self.assertIn("league_team_id = request_context.league_team_id", source)
        self.assertIn("except AssistantAccessError", source)
        self.assertIn("allow_legacy_fallback=False", source)
        self.assertNotIn('user_id = "default"', source)
        self.assertNotIn('league_key = "default"', source)


if __name__ == "__main__":
    unittest.main()
