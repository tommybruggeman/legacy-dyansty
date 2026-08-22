from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

auth_stub = types.ModuleType("auth")
auth_stub.auth_client = lambda: None
auth_stub.current_user = lambda: None
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

request_context = importlib.import_module("gm_assistant.request_context")
retrieval = importlib.import_module("gm_assistant.retrieval")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None
        self.order_spec = None

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def order(self, key, desc=False):
        self.order_spec = (key, bool(desc))
        return self

    def execute(self):
        if self.client.fail_table == self.table_name:
            raise RuntimeError("database unavailable")
        self.client.selects.append((self.table_name, list(self.filters)))
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]
        if self.order_spec is not None:
            key, descending = self.order_spec
            rows.sort(key=lambda row: row.get(key), reverse=descending)
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)


class FakeClient:
    def __init__(self):
        self.fail_table = None
        self.selects = []
        self.rows = {
            "league_memberships": [
                {
                    "id": "membership-owner",
                    "user_id": "owner-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_id": None,
                    "role": "owner",
                },
                {
                    "id": "membership-co-owner",
                    "user_id": "co-owner-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_id": None,
                    "role": "co_owner",
                },
                {
                    "id": "membership-commissioner",
                    "user_id": "commissioner-1",
                    "league_id": "league-1",
                    "league_team_id": "team-2",
                    "team_id": None,
                    "role": "commissioner",
                },
                {
                    "id": "membership-legacy",
                    "user_id": "legacy-1",
                    "league_id": "league-1",
                    "league_team_id": None,
                    "team_id": "team-1",
                    "role": "owner",
                },
                {
                    "id": "membership-wrong-legacy",
                    "user_id": "legacy-wrong",
                    "league_id": "league-1",
                    "league_team_id": None,
                    "team_id": "team-x",
                    "role": "owner",
                },
                {
                    "id": "membership-no-team",
                    "user_id": "no-team",
                    "league_id": "league-1",
                    "league_team_id": None,
                    "team_id": None,
                    "role": "owner",
                },
                {
                    "id": "membership-owner-2",
                    "user_id": "owner-2",
                    "league_id": "league-2",
                    "league_team_id": "team-x",
                    "team_id": None,
                    "role": "owner",
                },
            ],
            "leagues": [
                {"id": "league-1", "name": "League One", "season": 2026, "sleeper_league_id": "123"},
                {"id": "league-2", "name": "League Two", "season": "2027", "sleeper_league_id": "456"},
            ],
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Team One", "owner_name": "Owner One"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Team Two", "owner_name": "Owner Two"},
                {"id": "team-x", "league_id": "league-2", "team_name": "Other League", "owner_name": "Other"},
            ],
            "league_settings": [
                {"league_id": "league-1", "key": "season_current", "value": "2026"},
                {"league_id": "league-2", "key": "season_current", "value": "2027"},
            ],
            "league_seasons": [
                {"id": "ls-2025", "league_id": "league-1", "season": 2025, "is_active": True},
                {"id": "ls-2026", "league_id": "league-1", "season": 2026, "is_active": False},
                {"id": "ls-2027", "league_id": "league-2", "season": 2027, "is_active": True},
            ],
            "team_roster_state": [
                {"league_id": "league-1", "team_id": "team-1", "player_name": "Player One"},
                {"league_id": "league-1", "team_id": "team-2", "player_name": "Player Two"},
                {"league_id": "league-2", "team_id": "team-x", "player_name": "Wrong League"},
            ],
            "player_strategic_profiles": [],
            "team_brain": [
                {"league_id": "league-1", "league_team_id": "team-1", "team_name": "Team One"},
                {"league_id": "league-2", "league_team_id": "team-x", "team_name": "Wrong League"},
            ],
            "league_brain": [
                {"league_id": "league-1", "summary": "League one"},
                {"league_id": "league-2", "summary": "League two"},
            ],
            "v_team_caps": [
                {"league_id": "league-1", "league_team_id": "team-1", "cap_space": 10},
                {"league_id": "league-1", "league_team_id": "team-2", "cap_space": 20},
                {"league_id": "league-2", "league_team_id": "team-x", "cap_space": 99},
            ],
            "draft_picks": [
                {"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "round": 1},
                {"league_id": "league-1", "league_team_id": "team-2", "season": 2027, "round": 2},
                {"league_id": "league-2", "league_team_id": "team-x", "season": 2026, "round": 1},
            ],
            "transactions_enriched": [
                {"league_id": "league-1", "description": "League one trade"},
                {"league_id": "league-2", "description": "Wrong league trade"},
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class AssistantStageOneContextTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeClient()

    def test_valid_authenticated_owner_context(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.user_id, "owner-1")
        self.assertEqual(ctx.league_id, "league-1")
        self.assertEqual(ctx.league_team_id, "team-1")
        self.assertEqual(ctx.current_season, 2025)
        self.assertEqual(ctx.requested_season, 2025)
        self.assertIn(request_context.TEAM_ADVICE, ctx.permission_scopes)
        self.assertIn(request_context.LEAGUE_PUBLIC_READ, ctx.permission_scopes)

    def test_valid_commissioner_receives_admin_scope(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "commissioner-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.league_team_id, "team-2")
        self.assertIn(request_context.LEAGUE_ADMIN, ctx.permission_scopes)

    def test_valid_co_owner_keeps_personal_user_id(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "co-owner-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.user_id, "co-owner-1")
        self.assertEqual(ctx.league_team_id, "team-1")
        self.assertNotIn(request_context.LEAGUE_ADMIN, ctx.permission_scopes)

    def test_unauthenticated_user_rejected(self):
        with self.assertRaises(request_context.AssistantContextError):
            request_context.build_assistant_request_context(sb=self.sb, user=None)

    def test_user_without_league_membership_rejected(self):
        with self.assertRaises(request_context.AssistantContextError):
            request_context.build_assistant_request_context(
                sb=self.sb,
                user={"id": "missing"},
                active_league_id="league-1",
            )

    def test_compatible_legacy_team_id_is_allowed(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "legacy-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.league_team_id, "team-1")

    def test_legacy_team_id_in_another_league_is_rejected(self):
        with self.assertRaises(request_context.AssistantContextError):
            request_context.build_assistant_request_context(
                sb=self.sb,
                user={"id": "legacy-wrong"},
                active_league_id="league-1",
            )

    def test_missing_canonical_team_is_rejected(self):
        with self.assertRaises(request_context.AssistantContextError):
            request_context.build_assistant_request_context(
                sb=self.sb,
                user={"id": "no-team"},
                active_league_id="league-1",
            )

    def test_requested_season_is_preserved(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-1"},
            active_league_id="league-1",
            requested_season=2027,
        )

        self.assertEqual(ctx.current_season, 2025)
        self.assertEqual(ctx.requested_season, 2027)

    def test_current_season_from_canonical_league_integer(self):
        self.sb.rows["league_settings"] = [
            {"league_id": "league-1", "key": "season_current", "value": "2025"},
        ]

        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.current_season, 2025)

    def test_current_season_from_canonical_league_numeric_string(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-2"},
            active_league_id="league-2",
        )

        self.assertEqual(ctx.current_season, 2027)

    def test_current_season_uses_structured_settings_fallback(self):
        self.sb.rows["leagues"] = [
            {"id": "league-1", "name": "League One", "season": None, "sleeper_league_id": "123"},
        ]

        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.current_season, 2025)

    def test_current_season_does_not_fall_back_to_connected_sleeper_metadata(self):
        self.sb.rows["leagues"] = [
            {"id": "league-1", "name": "League One", "sleeper_league_id": "1234567890"},
        ]
        self.sb.rows["league_settings"] = []
        self.sb.rows["league_seasons"] = []
        with self.assertRaises(request_context.AssistantContextError):
            ctx = request_context.build_assistant_request_context(
                sb=self.sb,
                user={"id": "owner-1"},
                active_league_id="league-1",
            )

    def test_current_season_null_and_malformed_without_fallback_fail(self):
        for value in (None, "twenty-six"):
            with self.subTest(value=value):
                self.sb.rows["leagues"] = [{"id": "league-1", "name": "League One", "season": value}]
                self.sb.rows["league_settings"] = []
                self.sb.rows["league_seasons"] = []

                with self.assertRaises(request_context.AssistantContextError):
                    request_context.build_assistant_request_context(
                        sb=self.sb,
                        user={"id": "owner-1"},
                        active_league_id="league-1",
                    )

    def test_conflicting_structured_seasons_prefer_league_record(self):
        self.sb.rows["leagues"] = [
            {
                "id": "league-1",
                "name": "League One",
                "current_season": "2026",
                "season": "2025",
            },
        ]
        self.sb.rows["league_settings"] = [
            {"league_id": "league-1", "key": "season_current", "value": "2024"},
        ]

        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-1"},
            active_league_id="league-1",
        )

        self.assertEqual(ctx.current_season, 2025)

    def test_current_season_is_league_isolated(self):
        ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-2"},
            active_league_id="league-2",
        )

        self.assertEqual(ctx.current_season, 2027)

    def test_no_calendar_or_environment_fallback(self):
        self.sb.rows["leagues"] = []
        self.sb.rows["league_settings"] = []
        self.sb.rows["league_seasons"] = []
        old_value = os.environ.get("CURRENT_SEASON")
        os.environ["CURRENT_SEASON"] = "2099"
        try:
            with self.assertRaises(request_context.AssistantContextError):
                request_context.build_assistant_request_context(
                    sb=self.sb,
                    user={"id": "owner-1"},
                    active_league_id="league-1",
                )
        finally:
            if old_value is None:
                os.environ.pop("CURRENT_SEASON", None)
            else:
                os.environ["CURRENT_SEASON"] = old_value

    def test_unresolved_season_is_clear_failure(self):
        self.sb.rows["leagues"] = []
        self.sb.rows["league_settings"] = []
        self.sb.rows["league_seasons"] = []
        keys = ("APP_CURRENT_SEASON", "FANTASY_CURRENT_SEASON", "CURRENT_SEASON")
        old_env = {key: os.environ.pop(key, None) for key in keys}
        try:
            with self.assertRaises(request_context.AssistantContextError):
                request_context.build_assistant_request_context(
                    sb=self.sb,
                    user={"id": "owner-1"},
                    active_league_id="league-1",
                )
        finally:
            for key, value in old_env.items():
                if value is not None:
                    os.environ[key] = value


class AssistantStageOneRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeClient()
        self.ctx = request_context.build_assistant_request_context(
            sb=self.sb,
            user={"id": "owner-1"},
            active_league_id="league-1",
        )

    def test_every_retrieval_requires_valid_context(self):
        with self.assertRaises(retrieval.AssistantRetrievalError):
            retrieval.get_league_brain(self.sb, object())

    def test_roster_retrieval_is_team_scoped(self):
        result = retrieval.get_team_roster(self.sb, self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual([row["player_name"] for row in result.rows], ["Player One"])

    def test_cap_retrieval_is_league_and_team_scoped(self):
        result = retrieval.get_cap_summary(self.sb, self.ctx, league_team_id="team-1")

        self.assertTrue(result.ok)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["cap_space"], 10)

    def test_draft_pick_retrieval_is_league_scoped(self):
        result = retrieval.get_draft_picks(self.sb, self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual({row["league_id"] for row in result.rows}, {"league-1"})

    def test_transaction_retrieval_is_league_scoped(self):
        result = retrieval.get_transactions(self.sb, self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual([row["description"] for row in result.rows], ["League one trade"])

    def test_team_brain_rejects_team_from_another_league(self):
        with self.assertRaises(retrieval.AssistantRetrievalError):
            retrieval.get_team_brain(self.sb, self.ctx, league_team_id="team-x")

    def test_league_brain_only_returns_active_league(self):
        result = retrieval.get_league_brain(self.sb, self.ctx)

        self.assertTrue(result.ok)
        self.assertEqual(result.rows[0]["summary"], "League one")

    def test_empty_results_are_not_authorization_failures(self):
        self.sb.rows["team_roster_state"] = []
        result = retrieval.get_team_roster(self.sb, self.ctx)

        self.assertTrue(result.ok)
        self.assertTrue(result.empty)

    def test_failed_retrieval_is_not_valid_empty_data(self):
        self.sb.fail_table = "transactions_enriched"
        result = retrieval.get_transactions(self.sb, self.ctx)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "retrieval_failed")
        self.assertFalse(result.empty)

    def test_retrieval_never_uses_unscoped_query(self):
        retrieval.get_transactions(self.sb, self.ctx)

        tx_selects = [filters for table, filters in self.sb.selects if table == "transactions_enriched"]
        self.assertTrue(tx_selects)
        self.assertIn(("league_id", "league-1"), tx_selects[-1])


if __name__ == "__main__":
    unittest.main()
