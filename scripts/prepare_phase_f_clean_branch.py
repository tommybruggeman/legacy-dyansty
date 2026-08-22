#!/usr/bin/env python3
"""Fail-closed bootstrap/preflight for a new disposable Phase F branch.

This script contacts a database only when explicitly invoked with ``preflight``
or ``migrate``. It never accepts a database URL and never performs fixture cleanup.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures.season_rollover_domain_factory import SeasonRolloverDomainFactory
import subprocess
import urllib.request
from uuid import UUID, uuid5
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
PSQL = "/opt/homebrew/opt/postgresql@16/bin/psql"
DB_ENV = ("PHASE3B5H_TEST_DB_HOST", "PHASE3B5H_TEST_DB_PORT", "PHASE3B5H_TEST_DB_NAME",
          "PHASE3B5H_TEST_DB_USER", "PHASE3B5H_TEST_DB_PASSWORD")
REST_ENV = ("PHASE3B5H_TEST_SUPABASE_URL", "PHASE3B5H_TEST_SUPABASE_ANON_KEY",
            "PHASE3B5H_TEST_SUPABASE_SERVICE_ROLE_KEY", "PHASE3B5H_TEST_AUTH_PASSWORD")
FORBIDDEN = ("DATABASE_URL", "SUPABASE_DB_URL", "LEGACY_PROD_DATABASE_URL",
             "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY")
FORBIDDEN_PREFIXES = ("LEGACY_PROD_", "PRODUCTION_DB_", "PROD_DB_")
SENTINEL = ("rollover-phase-f-final-certification", "disposable_test", "Legacy-Dynasty")
LABEL = "phase-f-final-certification"
NAMESPACE = UUID("e9a2f08f-e4f4-44d0-bf06-d6892917ff5c")
FORWARD_MIGRATIONS = tuple(sorted((ROOT / "supabase/migrations").glob("202610*.sql")))
FORWARD_MIGRATIONS = tuple(path for path in FORWARD_MIGRATIONS
                           if "20261007" <= path.name[:8] <= "20261018")
EXPECTED_VERSIONS = tuple(f"202610{day:02d}" for day in range(7, 19))


@dataclass(frozen=True)
class MigrationProbe:
    version: str
    checks: tuple[str, ...]


def function(signature: str) -> str:
    return f"to_regprocedure('public.{signature}') is not null"


def function_contains(signature: str, marker: str) -> str:
    return (f"coalesce(position('{marker}' in pg_get_functiondef("
            f"to_regprocedure('public.{signature}')))>0,false)")


PROBES = (
    MigrationProbe("20261007", (function("capture_pre_rollover_history(jsonb)"),
        "(" + function("capture_pre_rollover_history_phasea_set_validated_private(jsonb)") + " or "
        + function_contains("capture_pre_rollover_history(jsonb)", "canonical_team_count") + ")")),
    MigrationProbe("20261008", (function("capture_pre_rollover_history_phasea_set_validated_private(jsonb)"),
        function("phasea_history_sha256_private(text)"),
        function("phasea_history_canonical_team_fingerprint_private(uuid)"),
        function("phasea_history_source_roster_fingerprint_private(jsonb)"),
        function("phasea_history_mapping_fingerprint_private(jsonb)"),
        function("phasea_history_standings_fingerprint_private(jsonb)"),
        function_contains("capture_pre_rollover_history(jsonb)", "phasea_history_fingerprint_missing"),
        function_contains("capture_pre_rollover_history(jsonb)", "phasea_history_fingerprint_malformed"),
        function_contains("capture_pre_rollover_history(jsonb)", "phasea_history_fingerprint_mismatch"),
        function_contains("capture_pre_rollover_history(jsonb)", "capture_pre_rollover_history_phasea_set_validated_private"),
        "(select prosecdef from pg_proc where oid=to_regprocedure('public.capture_pre_rollover_history(jsonb)'))",
        "coalesce((select 'search_path=pg_catalog, public'=any(proconfig) from pg_proc where oid=to_regprocedure('public.capture_pre_rollover_history(jsonb)')),false)",
        "not has_function_privilege('anon','public.capture_pre_rollover_history(jsonb)','execute')",
        "not has_function_privilege('authenticated','public.capture_pre_rollover_history(jsonb)','execute')",
        "has_function_privilege('service_role','public.capture_pre_rollover_history(jsonb)','execute')")),
    MigrationProbe("20261009", ("exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_commissioner_reviews' and column_name='phaseb_case_key')",
        "to_regclass('public.rollover_reviews_execution_phaseb_case_uidx') is not null",
        function("phaseb_guard_authority_population_transition_private()"),
        "exists(select 1 from pg_trigger where tgname='phaseb_guard_authority_population_transition' and not tgisinternal)")),
    MigrationProbe("20261010", (function("phaseb_sha256_private(text)"),
        function("phaseb_json_string_private(text)"), function("phaseb_population_fingerprint_private(text,jsonb)"))),
    MigrationProbe("20261011", (function("phaseb_owner_case_material_v3_private(jsonb)"),
        function("phaseb_commissioner_case_material_v3_private(jsonb)"),
        function("phaseb_owner_case_fingerprint_v3_private(jsonb)"),
        function("phaseb_commissioner_case_fingerprint_v3_private(jsonb)"))),
    MigrationProbe("20261012", (function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "phaseb_owner_cross_league_or_identity_mismatch"),
        function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "phaseb_commissioner_source_identity_cross_league"))),
    MigrationProbe("20261013", ("(" + function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "assert_population.p_execution_id")
        + " or " + function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "phaseb_%_case_not_expected") + ")",
        "(" + function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "supplied_case_doc")
        + " or " + function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "v_supplied_case") + ")")),
    MigrationProbe("20261014", (function_contains("phaseb_assert_population_private(uuid,text,jsonb)", "phaseb_%_case_not_expected"),
        function_contains("phaseb_assert_frozen_populations_private(uuid)", "phaseb_frozen_commissioner_population_mismatch"),
        function_contains("initialize_rollover_commissioner_reviews_authenticated(jsonb)", "phaseb_commissioner_conflict_source_missing"))),
    MigrationProbe("20261015", (function("phaseb_commissioner_review_plan_approved_private(uuid)"),
        function_contains("enforce_commissioner_review_state()", "Commissioner review cannot change after final plan approval"),
        function_contains("supersede_rollover_commissioner_review_authenticated(jsonb)", "Review cannot be superseded after final plan approval"))),
    MigrationProbe("20261016", ("to_regclass('public.rollover_execution_input_snapshot_component_chunks') is not null",
        "to_regclass('public.rollover_snapshot_component_chunks_order_idx') is not null",
        "exists(select 1 from pg_trigger where tgname='rollover_execution_input_snapshot_component_chunks_immutable' and not tgisinternal)",
        "exists(select 1 from pg_policies where schemaname='public' and tablename='rollover_execution_input_snapshot_component_chunks' and policyname='rollover_input_snapshot_component_chunks_commissioner_read')",
        function("phase3b6c_snapshot_v3_assert_snapshot_private(uuid)"))),
    MigrationProbe("20261017", (function("phase3b10b_derive_team_caps_private(uuid,uuid,uuid,numeric,text)"),
        function_contains("phase3b10b_derive_team_caps_private(uuid,uuid,uuid,numeric,text)", "expected_teams"),
        function("write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)"))),
    MigrationProbe("20261018", ("coalesce(obj_description(to_regprocedure('public.capture_pre_rollover_history(jsonb)'),'pg_proc') like 'Phase A final fingerprint contract reassertion v1%',false)",
        function_contains("capture_pre_rollover_history(jsonb)", "phasea_history_fingerprint_missing"),
        function_contains("capture_pre_rollover_history(jsonb)", "phasea_history_fingerprint_malformed"),
        function_contains("capture_pre_rollover_history(jsonb)", "phasea_history_fingerprint_mismatch"),
        function_contains("capture_pre_rollover_history(jsonb)", "capture_pre_rollover_history_phasea_set_validated_private"),
        "(select prosecdef from pg_proc where oid=to_regprocedure('public.capture_pre_rollover_history(jsonb)'))",
        "coalesce((select 'search_path=pg_catalog, public'=any(proconfig) from pg_proc where oid=to_regprocedure('public.capture_pre_rollover_history(jsonb)')),false)",
        "not has_function_privilege('anon','public.capture_pre_rollover_history(jsonb)','execute')",
        "not has_function_privilege('authenticated','public.capture_pre_rollover_history(jsonb)','execute')",
        "has_function_privilege('service_role','public.capture_pre_rollover_history(jsonb)','execute')")),
)


def fixture_id(suffix: str) -> str:
    namespace = f"season-rollover-no-seeding-domain-v1-{LABEL}"
    return str(uuid5(NAMESPACE, namespace + suffix))


def environment() -> dict[str, str]:
    missing = [name for name in (*DB_ENV, *REST_ENV) if not os.getenv(name, "").strip()]
    forbidden = sorted(name for name, value in os.environ.items() if value and
                       (name in FORBIDDEN or name.startswith(FORBIDDEN_PREFIXES)))
    if missing: raise RuntimeError("missing disposable variables: " + ", ".join(missing))
    if forbidden: raise RuntimeError("production variables are forbidden: " + ", ".join(forbidden))
    env = os.environ.copy()
    env.update(PGHOST=env[DB_ENV[0]], PGPORT=env[DB_ENV[1]], PGDATABASE=env[DB_ENV[2]],
               PGUSER=env[DB_ENV[3]], PGPASSWORD=env[DB_ENV[4]])
    for name in FORBIDDEN: env.pop(name, None)
    return env


def psql(*, sql: str | None = None, file: Path | None = None) -> str:
    command = [PSQL, "-X", "-v", "ON_ERROR_STOP=1", "-At"]
    command += ["-f", str(file)] if file else ["-c", str(sql)]
    result = subprocess.run(command, cwd=ROOT, env=environment(), text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("psql failed without exposing credentials: " + result.stderr[-1200:])
    return result.stdout.strip()


def verify_auth_reachable() -> None:
    request = urllib.request.Request(os.environ[REST_ENV[0]].rstrip("/") + "/auth/v1/settings",
        headers={"apikey": os.environ[REST_ENV[1]]})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200: raise RuntimeError("Auth settings endpoint did not return 200")


def preflight() -> dict[str, object]:
    environment()
    league_id = fixture_id(":league")
    legacy_actor_ids = [fixture_id(":commissioner"), fixture_id(":owner"),
        *(fixture_id(f":owner:{n}") for n in range(2, 11)), fixture_id(":outsider")]
    quoted_ids = ",".join("'" + value + "'::uuid" for value in legacy_actor_ids)
    sql = f"""select jsonb_build_object(
      'sentinel',(select count(*)=1 and bool_and(singleton and environment_name='{SENTINEL[0]}'
        and environment_type='{SENTINEL[1]}' and parent_project='{SENTINEL[2]}')
        from public.environment_identity),
      'fixture_users',(select count(*) from auth.users where id in ({quoted_ids})
        or email like 'phasef-{LABEL}-%@rollover-certification.invalid'),
      'fixture_leagues',(select count(*) from public.leagues where id='{league_id}'::uuid),
      'fixture_executions',(select count(*) from public.rollover_executions
        where league_id='{league_id}'::uuid),
      'base_ready',to_regclass('public.environment_identity') is not null
        and to_regclass('public.rollover_executions') is not null
        and to_regprocedure('public.require_commissioner_authority(uuid)') is not null,
      'forward_versions',(select coalesce(jsonb_agg(version order by version),'[]'::jsonb)
        from supabase_migrations.schema_migrations where version between '20261007' and '20261017'))"""
    report = json.loads(psql(sql=sql))
    if report["sentinel"] is not True or report["base_ready"] is not True:
        raise RuntimeError("sentinel or certified base-schema preflight failed")
    if any(int(report[key]) for key in ("fixture_users", "fixture_leagues", "fixture_executions")):
        raise RuntimeError("phase-f-final-certification namespace is not clean; discard this branch")
    verify_auth_reachable()
    report["auth_api"] = "reachable"
    report["production_variables_absent"] = True
    return report


def migration_status() -> list[dict[str, object]]:
    results = []
    for probe in PROBES:
        checks = json.loads(psql(sql="select jsonb_build_array(" + ",".join(probe.checks) + ")"))
        state = ("NEEDS_APPLICATION" if probe.version == "20261018" and not checks[0]
                 else classify_checks(checks))
        results.append({"version": probe.version, "state": state, "checks": checks})
    return results


def branch_state() -> dict[str, object]:
    label = os.getenv("ROLLOVER_FIXTURE_LABEL", "phase-f-final-certification")
    fixture = SeasonRolloverDomainFactory(label)
    ids = fixture.identity

    actor_ids = [
        ids.commissioner_id,
        *ids.owner_ids,
        ids.outsider_id,
    ]
    quoted_ids = ",".join("'" + value + "'::uuid" for value in actor_ids)

    hosted_auth_prefix = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:40]

    sql = f"""select jsonb_build_object(
      'fixture_users',(
        select count(*)
        from auth.users
        where id in ({quoted_ids})
           or email like 'phasef-{hosted_auth_prefix}-%@rollover-certification.invalid'
           or (
                email like '%@no-seeding.invalid'
                and id in ({quoted_ids})
           )
      ),
      'fixture_leagues',(
        select count(*)
        from public.leagues
        where id in ('{ids.league_id}'::uuid, '{ids.foreign_league_id}'::uuid)
      ),
      'fixture_executions',(
        select count(*)
        from public.rollover_executions
        where league_id='{ids.league_id}'::uuid
      ),
      'historical_captures',(
        select count(*)
        from public.historical_capture_executions
        where league_season_id='{ids.source_season_id}'::uuid
      ),
      'snapshots',(
        select count(*)
        from public.rollover_execution_input_snapshots
        where league_id='{ids.league_id}'::uuid
      ),
      'immutable_evidence',(
        (select count(*) from public.rollover_execution_plan_approvals
         where league_id='{ids.league_id}'::uuid)
        +
        (select count(*) from public.rollover_execution_locks
         where league_id='{ids.league_id}'::uuid)
        +
        (select count(*)
         from public.rollover_execution_operation_results r
         join public.rollover_executions x
           on x.id=r.rollover_execution_id
         where x.league_id='{ids.league_id}'::uuid)
        +
        (select count(*) from public.rollover_post_execution_validation_reports
         where league_id='{ids.league_id}'::uuid)
      )
    )"""

    return json.loads(psql(sql=sql))


def classify_checks(checks: list[bool]) -> str:
    if not checks: raise ValueError("migration probe must contain at least one check")
    return "ALREADY_PRESENT" if all(checks) else "NEEDS_APPLICATION" if not any(checks) else "PARTIAL/CONFLICTING"


def migrate() -> dict[str, object]:
    before = preflight()
    detected = migration_status()
    conflicts = [row["version"] for row in detected if row["state"] == "PARTIAL/CONFLICTING"]
    if conflicts == ["20261008"] and detected[-1]["state"] == "NEEDS_APPLICATION":
        conflicts = []
    if conflicts: raise RuntimeError("partial/conflicting migration state: " + ", ".join(conflicts))
    applied = []
    for path, row in zip(FORWARD_MIGRATIONS, detected, strict=True):
        if row["state"] == "ALREADY_PRESENT": continue
        if row["version"] == "20261008" and row["state"] == "PARTIAL/CONFLICTING": continue
        psql(file=path)
        applied.append(path.name)
        post = next(item for item in migration_status() if item["version"] == row["version"])
        if post["state"] != "ALREADY_PRESENT":
            raise RuntimeError(f"{row['version']} did not reach its full post-state")
    verification = json.loads(psql(sql="""select jsonb_build_object(
      'history',to_regprocedure('public.capture_pre_rollover_history(jsonb)') is not null,
      'phaseb',to_regprocedure('public.phaseb_assert_population_private(uuid,text,jsonb)') is not null,
      'snapshot_chunks',to_regclass('public.rollover_execution_input_snapshot_component_chunks') is not null,
      'prepared_caps',to_regprocedure('public.phase3b10b_derive_team_caps_private(uuid,uuid,uuid,numeric,text)') is not null)"""))
    if not all(verification.values()):
        raise RuntimeError("post-migration object verification failed")
    after = preflight()
    return {"before": before, "initial_status": detected, "applied": applied,
            "verification": verification, "final_status": migration_status(), "after": after,
            "migration_order": [path.name for path in FORWARD_MIGRATIONS]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "status", "branch-state", "migrate", "inventory"))
    args = parser.parse_args()
    if len(FORWARD_MIGRATIONS) != 12 or tuple(path.name[:8] for path in FORWARD_MIGRATIONS) != EXPECTED_VERSIONS:
        raise RuntimeError("repository forward-migration inventory is not exactly 20261007..20261018")
    if args.action == "inventory":
        print(json.dumps([path.name for path in FORWARD_MIGRATIONS], indent=2)); return
    if args.action == "status":
        preflight()
        for row in migration_status(): print(row["version"], row["state"])
        return
    if args.action == "branch-state":
        environment(); print(json.dumps(branch_state(), sort_keys=True, indent=2)); return
    print(json.dumps(preflight() if args.action == "preflight" else migrate(), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
