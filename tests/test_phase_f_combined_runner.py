from __future__ import annotations

import unittest
import os
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from tests.phase_f_combined_integration import run_phase_f_combined_certification as runner
from tests.fixtures.season_rollover_domain_factory import SeasonRolloverDomainFactory
from tests.season_rollover_hosted_integration import psql_client
from tests.season_rollover_hosted_integration.run_unified_hosted_rollover import verify_hosted_auth_context
from scripts import prepare_phase_f_clean_branch as clean_branch
from tests.fixtures.certification_sentinel import ENVIRONMENT_VARIABLES, expected_sentinel
from tests.phase_c_snapshot_v3_integration import run_phase_c_snapshot_v3_certification as phase_c_runner


class FakeCatalogSession:
    def json_query(self, _sql):
        return [
            {"order": order, "code": f"OP_{order:02d}",
             "owner": "execution" if order <= 31 else "publication"}
            for order in range(1, 37)
        ]


class PhaseFCombinedRunnerTests(unittest.TestCase):
    def test_clean_branch_forward_migration_inventory_is_exact_and_ordered(self):
        self.assertEqual(tuple(path.name[:8] for path in clean_branch.FORWARD_MIGRATIONS),
                         tuple(f"202610{day:02d}" for day in range(7, 19)))

    def test_phase_a_forward_repair_reasserts_wrapper_without_replacing_private_capture(self):
        sql = (clean_branch.ROOT / "supabase/migrations/20261018_phaseA_final_fingerprint_contract_reassertion.sql").read_text()
        self.assertIn("create or replace function public.capture_pre_rollover_history(p_plan jsonb)", sql.lower())
        self.assertNotIn("create or replace function public.capture_pre_rollover_history_phasea_set_validated_private", sql.lower())
        for marker in ("phasea_history_fingerprint_missing", "phasea_history_fingerprint_malformed",
                       "phasea_history_fingerprint_mismatch"):
            self.assertIn(marker, sql)
        self.assertIn("security definer set search_path=pg_catalog,public", sql.lower())
        self.assertIn("grant execute on function public.capture_pre_rollover_history(jsonb) to service_role", sql.lower())

    def test_clean_branch_probe_classifies_absent_present_and_partial(self):
        self.assertEqual(clean_branch.classify_checks([False, False]), "NEEDS_APPLICATION")
        self.assertEqual(clean_branch.classify_checks([True, True]), "ALREADY_PRESENT")
        self.assertEqual(clean_branch.classify_checks([True, False]), "PARTIAL/CONFLICTING")

    def test_clean_branch_migrate_orders_missing_files_and_stops_on_failure(self):
        applied = []

        def states():
            return [{"version": version,
                     "state": "ALREADY_PRESENT" if version in applied else "NEEDS_APPLICATION",
                     "checks": []} for version in clean_branch.EXPECTED_VERSIONS]

        def fake_psql(*, sql=None, file=None):
            if file:
                version = file.name[:8]
                if version == "20261009": raise RuntimeError("forced migration failure")
                applied.append(version)
                return ""
            return '{"history":true,"phaseb":true,"snapshot_chunks":true,"prepared_caps":true}'

        with patch.object(clean_branch, "preflight", return_value={"sentinel": True}), \
             patch.object(clean_branch, "migration_status", side_effect=states), \
             patch.object(clean_branch, "psql", side_effect=fake_psql):
            with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                clean_branch.migrate()
        self.assertEqual(applied, ["20261007", "20261008"])

    def test_clean_branch_migrate_skips_present_and_applies_mixed_state_in_order(self):
        initially_present = {"20261007", "20261008", "20261010"}
        applied = []

        def states():
            present = initially_present | set(applied)
            return [{"version": version,
                     "state": "ALREADY_PRESENT" if version in present else "NEEDS_APPLICATION",
                     "checks": []} for version in clean_branch.EXPECTED_VERSIONS]

        def fake_psql(*, sql=None, file=None):
            if file:
                applied.append(file.name[:8]); return ""
            return '{"history":true,"phaseb":true,"snapshot_chunks":true,"prepared_caps":true}'

        with patch.object(clean_branch, "preflight", return_value={"sentinel": True}), \
             patch.object(clean_branch, "migration_status", side_effect=states), \
             patch.object(clean_branch, "psql", side_effect=fake_psql):
            result = clean_branch.migrate()
        self.assertEqual(applied, [version for version in clean_branch.EXPECTED_VERSIONS
                                   if version not in initially_present])
        self.assertEqual(result["final_status"][-1]["state"], "ALREADY_PRESENT")

    def test_clean_branch_migrate_rejects_partial_before_applying(self):
        partial = [{"version": version, "state": "PARTIAL/CONFLICTING" if version == "20261008"
                    else "ALREADY_PRESENT", "checks": []} for version in clean_branch.EXPECTED_VERSIONS]
        with patch.object(clean_branch, "preflight", return_value={"sentinel": True}), \
             patch.object(clean_branch, "migration_status", return_value=partial):
            with self.assertRaisesRegex(RuntimeError, "20261008"):
                clean_branch.migrate()

    def test_clean_branch_sentinel_and_final_runner_retention_policy(self):
        sentinel_sql = (clean_branch.ROOT / "supabase/sql/phase_f_final_certification_sentinel.sql").read_text()
        runner_sql = (clean_branch.ROOT / "tests/season_rollover_hosted_integration/run_unified_hosted_rollover.py").read_text()
        self.assertIn("rollover-phase-f-final-certification", sentinel_sql)
        self.assertNotIn("session_replication_role", runner_sql)
        self.assertNotIn("cleanup_sql()", runner_sql)
        self.assertIn("certification_evidence_retained", runner_sql)

    def test_direct_fixture_adapter_supports_phase_e_strict_pagination_contract(self):
        class Session:
            def json_query(self, sql):
                if "count(*)" in sql: return 3
                return [{"id": "b"}, {"id": "c"}]
        query = psql_client._Query(Session(), "league_memberships")
        response = query.select("id", count="exact").order("id").range(1, 2).execute()
        self.assertEqual(response.count, 3)
        self.assertEqual(response.data, [{"id": "b"}, {"id": "c"}])

    def test_persistent_adapter_recycles_between_commands_before_pool_expiry(self):
        session = object.__new__(psql_client.PsqlSession)
        session._configuring = False
        session._started_at = 10.0
        session.reconnect_count = 0
        calls = []
        session.close = lambda: calls.append("close")
        session._start = lambda: calls.append("start")
        session._configure = lambda: calls.append("configure")
        with patch.object(psql_client.time, "monotonic", return_value=31.0):
            session._recycle_before_pool_expiry()
        self.assertEqual(calls, ["close", "start", "configure"])
        self.assertEqual(session.reconnect_count, 1)

    def test_persistent_adapter_does_not_recycle_during_configuration(self):
        session = object.__new__(psql_client.PsqlSession)
        session._configuring = True; session._started_at = 0.0; session.reconnect_count = 0
        session.close = lambda: self.fail("configuration must not recurse")
        with patch.object(psql_client.time, "monotonic", return_value=100.0):
            session._recycle_before_pool_expiry()

    def test_hosted_auth_fixture_provisions_and_verifies_three_real_user_sessions(self):
        users = {}

        class Auth:
            def __init__(self): self.current = None
            def sign_in_with_password(self, credentials):
                if credentials["email"] not in users: raise RuntimeError("not provisioned")
                self.current = users[credentials["email"]]
                return type("SignIn", (), {"session": type("Session", (), {"access_token": "jwt"})()})()
            def get_user(self):
                return type("Response", (), {"user": type("User", (), {"id": self.current})()})()

        class AdminAPI:
            def create_user(self, attributes):
                user_id = f"user-{len(users) + 1}"
                users[attributes["email"]] = user_id
                return type("Response", (), {"user": type("User", (), {"id": user_id})()})()
            def delete_user(self, user_id):
                for email, value in list(users.items()):
                    if value == user_id: del users[email]

        class Client:
            def __init__(self, admin=False):
                self.auth = Auth()
                if admin: self.auth.admin = AdminAPI()

        clients = []
        def create_client(_url, key):
            client = Client(admin=key == "service")
            clients.append(client)
            return client

        env = {"PHASE3B5H_TEST_SUPABASE_URL": "https://disposable.invalid",
               "PHASE3B5H_TEST_SUPABASE_ANON_KEY": "anon",
               "PHASE3B5H_TEST_SUPABASE_SERVICE_ROLE_KEY": "service",
               "PHASE3B5H_TEST_AUTH_PASSWORD": "test-password"}
        with patch.dict("os.environ", env), patch.object(psql_client, "create_client", create_client):
            fixture = psql_client.HostedAuthFixture("auth-contract")
            identities = fixture.establish()
            self.assertEqual(set(identities), {"commissioner", "owner", "foreign-owner"})
            self.assertEqual(len({identity.user_id for identity in identities.values()}), 3)
            self.assertTrue(all(identity.client.auth.get_user().user.id == identity.user_id
                                for identity in identities.values()))
            fixture.cleanup()
        self.assertFalse(users)

    def test_certification_matrix_and_steps_are_complete(self):
        self.assertEqual(runner.SIZES, (1, 10, 32, 100, 2000))
        self.assertEqual(
            [name for name, _ in runner.STEPS],
            ["phase_a", "phase_a_negative", "phase_b", "phase_c", "phase_d",
             "phase_e", "approval_concurrency", "full_pipeline"],
        )

    def test_catalog_requires_exact_execution_publication_boundary(self):
        value = runner.catalog(FakeCatalogSession())
        self.assertEqual(value["execution_count"], 31)
        self.assertEqual(value["publication_count"], 5)

    def test_json_output_parser_ignores_progress_noise(self):
        self.assertEqual(
            runner.json_objects('progress\n{"ok":true}\nnot-json\n[1,2]\n'),
            [{"ok": True}, [1, 2]],
        )

    def test_exact_disposable_sentinel_is_pinned(self):
        self.assertEqual(
            runner.SENTINEL,
            ("rollover-phase-f-final-certification", "disposable_test", "Legacy-Dynasty"),
        )

    def test_every_nested_step_receives_exact_final_sentinel(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs["env"]))
            return type("Result", (), {"returncode": 0, "stdout": "{}\n", "stderr": ""})()

        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            for step_name, command in runner.STEPS:
                runner.run_step(step_name, command)

        self.assertEqual(len(calls), len(runner.STEPS))
        expected = dict(zip(ENVIRONMENT_VARIABLES, runner.SENTINEL, strict=True))
        for _command, environment in calls:
            self.assertEqual({key: environment[key] for key in ENVIRONMENT_VARIABLES}, expected)
        full_environment = calls[-1][1]
        self.assertEqual(full_environment["ROLLOVER_FIXTURE_LABEL"], "phase-f-final-certification")

    def test_historical_default_is_preserved_but_explicit_sentinel_wins(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                expected_sentinel("rollover-cardinality-certification"),
                ("rollover-cardinality-certification", "disposable_test", "Legacy-Dynasty"),
            )
        explicit = dict(zip(ENVIRONMENT_VARIABLES, runner.SENTINEL, strict=True))
        with patch.dict(os.environ, explicit, clear=True):
            self.assertEqual(expected_sentinel("rollover-cardinality-certification"), runner.SENTINEL)

    def test_phase_c_mismatched_actual_sentinel_fails_closed(self):
        with patch.object(phase_c_runner, "database_env", return_value={}), \
             patch.object(phase_c_runner, "psql", return_value="wrong-branch|disposable_test|Legacy-Dynasty"):
            with self.assertRaisesRegex(RuntimeError, "sentinel mismatch"):
                phase_c_runner.main()

    def test_hosted_auth_preflight_proves_actor_role_team_and_foreign_denial(self):
        class Response:
            def __init__(self, data, count=None): self.data, self.count = data, count
        class Query:
            def __init__(self, rows): self.rows, self.start, self.end, self.head = rows, 0, None, False
            def select(self, *_args, **kwargs): self.head = bool(kwargs.get("head")); return self
            def eq(self, key, value): self.rows = [row for row in self.rows if row.get(key) == value]; return self
            def order(self, key): self.rows.sort(key=lambda row: str(row.get(key))); return self
            def range(self, start, end): self.start, self.end = start, end; return self
            def execute(self):
                count = len(self.rows)
                return Response([] if self.head else self.rows[self.start:self.end + 1], count)
        class Auth:
            def __init__(self, actor): self.actor = actor
            def get_user(self):
                user = type("User", (), {"id": self.actor})()
                return type("AuthResponse", (), {"user": user})()
        class Client:
            def __init__(self, actor, rows): self.auth, self.rows = Auth(actor), rows
            def table(self, name):
                if name != "league_memberships": raise AssertionError(name)
                return Query([dict(row) for row in self.rows])

        factory = SeasonRolloverDomainFactory("auth-preflight")
        ids = factory.identity
        commissioner = {"id": "mc", "league_id": ids.league_id, "user_id": ids.commissioner_id,
                        "role": "commissioner", "league_team_id": None}
        owner = {"id": "mo", "league_id": ids.league_id, "user_id": ids.owner_id,
                 "role": "member", "league_team_id": ids.team_ids[0]}
        foreign = {"id": "mf", "league_id": ids.foreign_league_id, "user_id": ids.outsider_id,
                   "role": "member", "league_team_id": ids.foreign_team_id}
        output = StringIO()
        with redirect_stdout(output):
            verify_hosted_auth_context(factory, Client(ids.commissioner_id, [commissioner]),
                                       Client(ids.owner_id, [owner]), Client(ids.outsider_id, [foreign]))
        self.assertIn('"stage": "commissioner_auth_verified"', output.getvalue())
        self.assertIn('"stage": "owner_auth_verified"', output.getvalue())
        self.assertIn('"stage": "foreign_owner_auth_verified"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
