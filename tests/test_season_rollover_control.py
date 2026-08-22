from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.season_rollover_control import (
    RolloverControlError,
    SeasonRolloverControlService,
    TrustedGenerationResult,
    InitiationReadiness,
    build_commissioner_rollover_report,
    derive_lifecycle_timeline,
    require_canonical_commissioner,
    sanitized_result,
    verify_rollover_admin_password,
)


class Response:
    def __init__(self, data, count=None): self.data, self.count = data, count


class Execute:
    def __init__(self, data): self.data = data
    def execute(self): return Response(self.data)


class Query:
    def __init__(self, rows): self.rows, self.start, self.end, self.head = rows, 0, None, False
    def select(self, *_args, **kwargs): self.head = bool(kwargs.get("head")); return self
    def eq(self, key, value):
        self.rows = [row for row in self.rows if str(row.get(key)) == str(value)]
        return self
    def order(self, key): self.rows.sort(key=lambda row: str(row.get(key) or "")); return self
    def range(self, start, end): self.start, self.end = start, end; return self
    def execute(self):
        count = len(self.rows); data = [] if self.head else self.rows[self.start:self.end + 1 if self.end is not None else None]
        return Response(data, count)


class User:
    def __init__(self, user_id): self.id = user_id


class AuthResponse:
    def __init__(self, user_id): self.user = User(user_id) if user_id else None


class Auth:
    def __init__(self, user_id): self.user_id = user_id
    def get_user(self): return AuthResponse(self.user_id)


class Client:
    def __init__(self, user_id="u", memberships=(), tables=None):
        self.auth = Auth(user_id); self.memberships = list(memberships)
        self.tables = dict(tables or {}); self.calls = []
    def table(self, name):
        rows = self.memberships if name == "league_memberships" else self.tables.get(name, [])
        return Query([dict(row) for row in rows])
    def rpc(self, name, args):
        self.calls.append((name, args)); return Execute({"ok": True})


def commissioner(role="commissioner"):
    return Client(memberships=[{"league_id": "l", "user_id": "u", "role": role}])


class AuthorizationTests(unittest.TestCase):
    def test_forged_session_role_is_irrelevant(self):
        with self.assertRaisesRegex(RolloverControlError, "commissioner"):
            require_canonical_commissioner(Client(memberships=[{"league_id": "l", "user_id": "u", "role": "owner"}]), "l")

    def test_non_commissioner_rejected(self):
        with self.assertRaises(RolloverControlError):
            SeasonRolloverControlService(commissioner("member"), "l").authorize()

    def test_all_canonical_commissioner_roles_allowed(self):
        for role in ("commissioner", "host", "admin"):
            self.assertEqual(SeasonRolloverControlService(commissioner(role), "l").authorize(), "u")

    def test_missing_user_fails_closed(self):
        with self.assertRaisesRegex(RolloverControlError, "Authentication"):
            require_canonical_commissioner(Client(user_id=None), "l")

    def test_duplicate_membership_fails_closed(self):
        rows = [{"league_id": "l", "user_id": "u", "role": "commissioner"}] * 2
        with self.assertRaises(RolloverControlError):
            require_canonical_commissioner(Client(memberships=rows), "l")


class BoundaryTests(unittest.TestCase):
    def test_service_client_is_not_exposed(self):
        service = SeasonRolloverControlService(commissioner(), "l", lambda: object())
        self.assertFalse(hasattr(service, "service_client"))
        result = sanitized_result(TrustedGenerationResult("dry_run", "s", "e", "l", "valid", 1, "f" * 64))
        self.assertNotIn("client", result); self.assertNotIn("credential", result)

    def test_non_allowlisted_rpc_rejected_before_call(self):
        client = commissioner(); service = SeasonRolloverControlService(client, "l")
        with self.assertRaisesRegex(RolloverControlError, "not allowed"):
            service.authenticated_rpc("persist_rollover_dry_run_service", {})
        self.assertEqual(client.calls, [])

    def test_trusted_methods_scope_execution_to_league(self):
        client = commissioner(); client.tables["rollover_executions"] = [{"id": "e", "league_id": "other"}]
        service = SeasonRolloverControlService(client, "l", lambda: object())
        with self.assertRaisesRegex(RolloverControlError, "outside"):
            service.generate_canonical_dry_run("e", {})

    def test_sanitized_rpc_error_hides_raw_details(self):
        class Broken(Client):
            def rpc(self, _name, _args): raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY=secret raw sql")
        client = Broken(memberships=commissioner().memberships)
        with self.assertRaises(RolloverControlError) as caught:
            SeasonRolloverControlService(client, "l").authenticated_rpc("create_rollover_execution_authenticated", {})
        self.assertNotIn("secret", str(caught.exception))

    def test_trusted_boundary_has_no_execution_or_publication_method(self):
        names = set(dir(SeasonRolloverControlService))
        self.assertNotIn("execute_rollover", names)
        self.assertNotIn("publish_rollover", names)

    def test_trusted_capability_allowlist_rejects_execution_and_publication(self):
        service = SeasonRolloverControlService(commissioner(), "l", lambda: object())
        for capability in ("execute", "publish", "activate_cap", "release_cutover", "refresh_context"):
            with self.assertRaisesRegex(RolloverControlError, "not allowed"):
                service._trusted_client(capability)

    def test_first_time_league_is_not_started(self):
        user = commissioner()
        service_tables = {
            "league_seasons": [
                {"id": "s1", "league_id": "l", "season": 2025, "is_active": True,
                 "status": "active", "sleeper_league_id": "sl"},
                {"id": "s2", "league_id": "l", "season": 2026, "is_active": False,
                 "status": "scheduled", "sleeper_league_id": "sl2"},
            ],
            "league_teams": [{"id": "t", "league_id": "l"}],
            "season_team_mappings": [{"id": "m", "league_season_id": "s1"}],
            "league_rollover_policies": [], "historical_capture_executions": [],
            "rollover_executions": [],
        }
        trusted = Client(tables=service_tables)
        user.tables = service_tables
        readiness = SeasonRolloverControlService(user, "l", lambda: trusted).load_initiation_readiness()
        self.assertEqual(readiness.execution_status, "not_started")
        self.assertEqual(readiness.policy_status, "required")
        self.assertEqual(readiness.history_status, "required")
        self.assertEqual(readiness.blockers, ())

    def test_readiness_mapping_blocker_prevents_start(self):
        user = commissioner()
        trusted = Client(tables={
            "league_seasons": [
                {"id": "s1", "league_id": "l", "season": 2025, "is_active": True, "status": "active", "sleeper_league_id": "sl"},
                {"id": "s2", "league_id": "l", "season": 2026, "is_active": False, "status": "scheduled"},
            ],
            "league_teams": [{"id": "t", "league_id": "l"}], "season_team_mappings": [],
            "league_rollover_policies": [], "historical_capture_executions": [], "rollover_executions": [],
        })
        user.tables = trusted.tables
        readiness = SeasonRolloverControlService(user, "l", lambda: trusted).load_initiation_readiness()
        self.assertIn("canonical_team_mapping_incomplete", readiness.blockers)


class OperatorDispatchTests(unittest.TestCase):
    class Service(SeasonRolloverControlService):
        def __init__(self, status="executed_unpublished"):
            self.status = status; self.dispatched = []
        def _scoped_execution(self, execution_id):
            return {"id": execution_id, "status": self.status}
        def authenticated_rpc(self, name, request):
            self.dispatched.append((name, dict(request)))
            return {"ok": True}

    def state(self):
        return {
            "plans": [{"id":"plan", "plan_version":1, "plan_fingerprint":"p" * 64}],
            "approvals": [{"id":"approval"}],
            "finalizations": [{"id":"final", "deterministic_finalization_fingerprint":"f" * 64,
                               "prepared_artifact_aggregate_hash":"a" * 64}],
            "season_publications": [{"id":"season-pub", "publication_fingerprint":"s" * 64}],
            "cap_publications": [{"id":"cap-pub", "publication_fingerprint":"c" * 64}],
            "market_publications": [{"id":"market-pub", "deterministic_fingerprint":"m" * 64}],
            "cutover_releases": [{"id":"release", "release_fingerprint":"r" * 64}],
            "prepared_cap_sets": [{"id":"cap-set", "aggregate_cap_set_hash":"h" * 64}],
            "prepared_free_agent_sets": [{"aggregate_set_hash":"g" * 64}],
            "prepared_expiring_sets": [{"aggregate_set_hash":"e" * 64}],
            "cache_manifests": [{"aggregate_manifest_hash":"x" * 64}],
        }

    def test_execution_calls_master_boundary_once_with_canonical_assertions(self):
        service = self.Service("execution_ready")
        service.execute_current_plan("execution", {"id":"approval"},
                                     {"id":"plan", "plan_version":2, "plan_fingerprint":"p" * 64})
        self.assertEqual(len(service.dispatched), 1)
        name, request = service.dispatched[0]
        self.assertEqual(name, "execute_rollover_plan_authenticated")
        self.assertEqual(request["expected_execution_status"], "execution_ready")
        self.assertEqual(request["expected_plan_fingerprint"], "p" * 64)

    def test_publication_requests_reuse_existing_rpcs_and_persisted_fingerprints(self):
        expected = {
            32:"publish_target_season_authority_authenticated",
            33:"activate_target_cap_authority_authenticated",
            34:"enable_target_free_agent_visibility_authenticated",
            35:"release_cutover_restrictions_authenticated",
            36:"refresh_published_ui_and_ai_context_authenticated",
        }
        for operation, rpc in expected.items():
            service = self.Service("completed" if operation == 36 else "executed_unpublished")
            state = self.state()
            service.publish_next_operation("execution", operation, state)
            self.assertEqual(service.dispatched[0][0], rpc)
            self.assertEqual(service.dispatched[0][1]["rollover_execution_id"], "execution")
            if operation == 36:
                self.assertEqual(service.dispatched[0][1]["expected_cutover_release_fingerprint"], "r" * 64)

    def test_publication_fails_closed_without_plan_and_approval(self):
        state = self.state(); state["plans"] = []
        with self.assertRaisesRegex(RolloverControlError, "plan and approval"):
            self.Service().publish_next_operation("execution", 32, state)


class ConfirmationTests(unittest.TestCase):
    def test_missing_password_fails_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROLLOVER_ADMIN_PASSWORD", None)
            self.assertFalse(verify_rollover_admin_password("1"))

    def test_password_is_configuration_not_hardcoded(self):
        with patch.dict(os.environ, {"ROLLOVER_ADMIN_PASSWORD": "configured-secret"}):
            self.assertFalse(verify_rollover_admin_password("1"))
            self.assertTrue(verify_rollover_admin_password("configured-secret"))


class TimelineTests(unittest.TestCase):
    def readiness(self, **changes):
        values = dict(league_id="l", source_season=2025, target_season=2026, sleeper_league_id="sl",
                      canonical_team_count=10, mapped_team_count=10, history_status="validated",
                      policy_status="approved", policy_id="p", execution_status="not_started", blockers=())
        values.update(changes); return InitiationReadiness(**values)

    def state(self, status="preflight_ready", **changes):
        value = {"execution": {"id": "e", "source_season": 2025, "target_season": 2026, "status": status},
                 "simulations": [], "plans": [], "approvals": [], "operation_results": [], "finalizations": [],
                 "season_publications": [], "cap_publications": [], "market_publications": [],
                 "cutover_releases": [], "context_generations": []}
        value.update(changes); return value

    def test_empty_and_blocked_states(self):
        stages = derive_lifecycle_timeline({"execution": None}, self.readiness())
        self.assertEqual(stages[4].status, "current")
        blocked = derive_lifecycle_timeline({"execution": None}, self.readiness(blockers=("mapping",)))
        self.assertEqual(blocked[0].status, "blocked")

    def test_window_open_and_closed(self):
        self.assertEqual(derive_lifecycle_timeline(self.state("decision_window_open"))[2].status, "warning")
        self.assertEqual(derive_lifecycle_timeline(self.state("decision_window_closed"))[2].status, "complete")

    def test_dry_run_plan_approval_and_executing(self):
        simulation = {"simulation_status": "valid", "blockers": []}
        plan = {"plan_status": "valid", "blockers": []}
        self.assertEqual(derive_lifecycle_timeline(self.state("authority_ready", simulations=[simulation]))[5].status, "complete")
        self.assertEqual(derive_lifecycle_timeline(self.state("plan_ready", simulations=[simulation], plans=[plan]))[6].status, "complete")
        approved = derive_lifecycle_timeline(self.state("execution_ready", simulations=[simulation], plans=[plan], approvals=[{"approval_status": "approved"}]))
        self.assertEqual(approved[7].status, "complete")
        self.assertEqual(derive_lifecycle_timeline(self.state("executing"))[8].status, "warning")

    def test_executed_partial_publication_and_complete(self):
        results = [{"operation_index": i} for i in range(1, 32)]
        finalized = [{"publication_eligible": True}]
        executed = derive_lifecycle_timeline(self.state("executed_unpublished", operation_results=results, finalizations=finalized))
        self.assertEqual(executed[9].status, "complete"); self.assertEqual(executed[10].status, "current")
        partial = derive_lifecycle_timeline(self.state("executed_unpublished", operation_results=results,
            finalizations=finalized, season_publications=[{}], cap_publications=[{}]))
        self.assertEqual(partial[10].summary, "2 of 5 publication steps")
        complete = derive_lifecycle_timeline(self.state("completed", operation_results=results, finalizations=finalized,
            season_publications=[{}], cap_publications=[{}], market_publications=[{}], cutover_releases=[{}], context_generations=[{}]))
        self.assertTrue(all(stage.status == "complete" for stage in complete))


class ReportTests(unittest.TestCase):
    def test_canonical_values_and_unavailable_not_guessed(self):
        report = build_commissioner_rollover_report({
            "execution": {"source_season": 2025, "target_season": 2026},
            "prepared_cap_sets": [{"canonical_team_count": 10}],
            "prepared_free_agent_sets": [{"expected_player_count": 11}],
            "prepared_expiring_sets": [{"expected_row_count": 7}],
            "simulations": [], "plans": [], "preparations": [],
        })
        self.assertEqual((report["target_cap_teams"], report["free_agent_eligibility"], report["expiring_contracts"]), (10, 11, 7))
        self.assertIsNone(report["taxi_unlocks"])
        self.assertIsNone(report["dead_cap_total"])


if __name__ == "__main__":
    unittest.main()
