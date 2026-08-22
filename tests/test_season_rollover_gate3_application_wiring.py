import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from services.season_rollover_owner_ui import (
    OWNER_DECISION_RPC,
    OwnerRolloverDecisionError,
    OwnerRolloverDecisionService,
)
from services.season_rollover_ui import (
    execution_idempotency_key,
    execution_ui_enabled,
    matching_active_cutover_lock,
    production_operator_ui_enabled,
    rollover_operator_ui_enabled,
)


class Result:
    def __init__(self, data, count=None): self.data, self.count = data, count


class Query:
    def __init__(self, client, table): self.client, self.table, self.filters, self.start, self.end, self.head = client, table, {}, 0, None, False
    def select(self, *_, **kwargs): self.head = bool(kwargs.get("head")); return self
    def eq(self, key, value): self.filters[key] = value; return self
    def order(self, key): self.order_key = key; return self
    def range(self, start, end): self.start, self.end = start, end; return self
    def execute(self):
        rows = [row for row in self.client.rows.get(self.table, [])
                if all(str(row.get(k)) == str(v) for k, v in self.filters.items())]
        rows.sort(key=lambda row: str(row.get(getattr(self, "order_key", "id")) or ""))
        count = len(rows); data = [] if self.head else rows[self.start:self.end + 1 if self.end is not None else None]
        return Result(data, count)


class FakeClient:
    def __init__(self, actor="user-1", rows=None):
        self.auth = SimpleNamespace(get_user=lambda: SimpleNamespace(user=SimpleNamespace(id=actor)))
        self.rows = rows or {}
        self.rpc_calls = []
    def table(self, name): return Query(self, name)
    def rpc(self, name, payload):
        self.rpc_calls.append((name, payload))
        return SimpleNamespace(execute=lambda: Result({"decision": {"id": "decision-1"}, "idempotent": False}))


def owner_rows(team_id="team-1", status="waiting_for_owner"):
    return {
        "league_memberships": [{"league_id": "league-1", "user_id": "user-1", "role": "member", "league_team_id": team_id}],
        "rollover_executions": [{"id": "execution-1", "league_id": "league-1", "status": "decision_window_open", "created_at": "2026-01-01"}],
        "rollover_owner_decisions": [{"id": "decision-1", "rollover_execution_id": "execution-1", "league_team_id": team_id,
                                       "decision_status": status, "locked_at": None,
                                       "metadata": {"decision_fingerprint": "a" * 64, "revision_number": 1}}],
    }


class OwnerDecisionTests(unittest.TestCase):
    def test_owner_submission_has_no_actor_or_team_spoofing_fields(self):
        client = FakeClient(rows=owner_rows())
        OwnerRolloverDecisionService(client, "league-1").submit("decision-1", "decline", "Decline option")
        name, envelope = client.rpc_calls[0]
        self.assertEqual(name, OWNER_DECISION_RPC)
        request = envelope["p_request"]
        self.assertNotIn("submitted_by", request)
        self.assertNotIn("actor_user_id", request)
        self.assertNotIn("league_team_id", request)
        self.assertEqual(request["expected_revision_number"], 1)
        self.assertTrue(request["idempotency_key"].startswith("owner-ui:decision-1:2:"))

    def test_owner_cannot_submit_another_teams_decision(self):
        rows = owner_rows()
        rows["rollover_owner_decisions"][0]["league_team_id"] = "team-2"
        client = FakeClient(rows=rows)
        with self.assertRaisesRegex(OwnerRolloverDecisionError, "not assigned"):
            OwnerRolloverDecisionService(client, "league-1").submit("decision-1", "decline", "No")
        self.assertEqual(client.rpc_calls, [])

    def test_non_owner_and_closed_window_fail_closed(self):
        rows = owner_rows()
        rows["league_memberships"][0]["role"] = "commissioner"
        with self.assertRaisesRegex(OwnerRolloverDecisionError, "canonical member"):
            OwnerRolloverDecisionService(FakeClient(rows=rows), "league-1").load()
        rows = owner_rows(); rows["rollover_executions"][0]["status"] = "decision_window_closed"
        client = FakeClient(rows=rows)
        with self.assertRaisesRegex(OwnerRolloverDecisionError, "not open"):
            OwnerRolloverDecisionService(client, "league-1").submit("decision-1", "decline", "No")

    def test_duplicate_or_teamless_member_membership_fails_closed(self):
        rows = owner_rows()
        rows["league_memberships"].append(dict(rows["league_memberships"][0], league_team_id="team-2"))
        with self.assertRaisesRegex(OwnerRolloverDecisionError, "Exactly one"):
            OwnerRolloverDecisionService(FakeClient(rows=rows), "league-1").load()
        rows = owner_rows(team_id=None)
        with self.assertRaisesRegex(OwnerRolloverDecisionError, "Exactly one"):
            OwnerRolloverDecisionService(FakeClient(rows=rows), "league-1").load()

    def test_legacy_owner_roles_are_not_required_or_accepted(self):
        for role in ("owner", "co_owner", "co-owner"):
            rows = owner_rows()
            rows["league_memberships"][0]["role"] = role
            with self.assertRaisesRegex(OwnerRolloverDecisionError, "canonical member"):
                OwnerRolloverDecisionService(FakeClient(rows=rows), "league-1").load()

    def test_unauthenticated_actor_fails_closed(self):
        with self.assertRaisesRegex(OwnerRolloverDecisionError, "Authentication"):
            OwnerRolloverDecisionService(FakeClient(actor="", rows=owner_rows()), "league-1").load()

    def test_extend_sends_no_caller_authored_contract_references(self):
        client = FakeClient(rows=owner_rows())
        OwnerRolloverDecisionService(client, "league-1").submit("decision-1", "recontract", "Yes")
        request = client.rpc_calls[-1][1]["p_request"]
        self.assertNotIn("recontract_agreement_id", request)
        self.assertNotIn("recontract_event_id", request)


class CommissionerExecutionWiringTests(unittest.TestCase):
    def test_execution_feature_gate_defaults_off(self):
        with patch.dict(os.environ, {}, clear=True): self.assertFalse(execution_ui_enabled())
        with patch.dict(os.environ, {"LEGACY_DISPOSABLE_ROLLOVER_EXECUTION_UI": "1"}, clear=True):
            self.assertFalse(execution_ui_enabled())
        with patch.dict(os.environ, {"LEGACY_DISPOSABLE_ROLLOVER_EXECUTION_UI": "1",
                                     "LEGACY_ENVIRONMENT_TYPE": "disposable_test"}, clear=True):
            self.assertTrue(execution_ui_enabled())

    def test_matching_lock_requires_approval_plan_and_fingerprint(self):
        approval = {"id": "approval-1"}; plan = {"id": "plan-1", "plan_fingerprint": "f" * 64}
        lock = {"id": "lock-1", "status": "active", "lock_type": "cutover", "approval_id": "approval-1",
                "execution_plan_id": "plan-1", "plan_fingerprint": "f" * 64}
        self.assertEqual(matching_active_cutover_lock([lock], approval, plan), lock)
        self.assertIsNone(matching_active_cutover_lock([{**lock, "approval_id": "wrong"}], approval, plan))
        self.assertIsNone(matching_active_cutover_lock([{**lock, "plan_fingerprint": "0" * 64}], approval, plan))

    def test_production_operator_gate_is_separate_and_league_allowlisted(self):
        values = {"LEGACY_PRODUCTION_ROLLOVER_OPERATOR_UI": "1", "LEGACY_ENVIRONMENT_TYPE": "production",
                  "LEGACY_PRODUCTION_ROLLOVER_LEAGUE_IDS": "league-1,league-2"}
        with patch.dict(os.environ, values, clear=True):
            self.assertTrue(production_operator_ui_enabled("league-1"))
            self.assertTrue(rollover_operator_ui_enabled("league-1"))
            self.assertFalse(production_operator_ui_enabled("league-3"))
            self.assertFalse(execution_ui_enabled())
        values["LEGACY_ENVIRONMENT_TYPE"] = "disposable_test"
        with patch.dict(os.environ, values, clear=True):
            self.assertFalse(production_operator_ui_enabled("league-1"))

    def test_repeated_execution_uses_same_idempotency_key(self):
        execution = {"id": "execution-1"}; approval = {"id": "approval-1"}
        plan = {"plan_fingerprint": "f" * 64}
        self.assertEqual(execution_idempotency_key(execution, approval, plan),
                         execution_idempotency_key(execution, approval, plan))

    def test_ui_uses_existing_publication_dispatch_and_no_service_role_secret(self):
        source = Path("services/season_rollover_ui.py").read_text()
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", source)
        self.assertIn("publish_next_operation", source)
        self.assertIn("publication_ui_authorized", source)

    def test_recovery_controls_use_only_certified_rpc_names(self):
        source = Path("services/season_rollover_ui.py").read_text()
        for name in (
            "cancel_rollover_execution_authenticated",
            "revoke_rollover_execution_plan_approval_authenticated",
            "cancel_rollover_authority_preparation_authenticated",
            "cancel_rollover_commissioner_review_authenticated",
            "supersede_rollover_commissioner_review_authenticated",
        ):
            self.assertIn(f'"{name}"', source)


if __name__ == "__main__": unittest.main()
