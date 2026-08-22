#!/usr/bin/env python3
"""Disposable-only Phase D certification. It never calls rollover/publication."""
import json
import os
import pathlib
import subprocess
import sys
import time
from numbers import Integral, Number

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
PSQL = "/opt/homebrew/opt/postgresql@16/bin/psql"
REQUIRED = ("PHASE3B5H_TEST_DB_HOST", "PHASE3B5H_TEST_DB_PORT", "PHASE3B5H_TEST_DB_NAME",
            "PHASE3B5H_TEST_DB_USER", "PHASE3B5H_TEST_DB_PASSWORD")
from tests.fixtures.certification_sentinel import expected_sentinel
EXPECTED_SENTINEL = expected_sentinel("rollover-cardinality-certification")


def psql(sql=None, file=None):
    env = os.environ.copy()
    env.update(PGHOST=env[REQUIRED[0]], PGPORT=env[REQUIRED[1]], PGDATABASE=env[REQUIRED[2]],
               PGUSER=env[REQUIRED[3]], PGPASSWORD=env[REQUIRED[4]])
    command = [PSQL, "-X", "-v", "ON_ERROR_STOP=1", "-At"]
    command += ["-f", str(file)] if file else ["-c", sql]
    return subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True).stdout.strip()


def sanitized(raw):
    value = raw
    for name in REQUIRED:
        secret = os.environ.get(name)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def parse_json_output(raw, context):
    """Accept one JSON value plus only known psql transaction status lines."""
    if not raw or not raw.strip():
        raise RuntimeError(f"{context}: blank SQL output")
    values = []
    unexpected = []
    status_lines = {"BEGIN", "COMMIT", "ROLLBACK"}
    for line in raw.splitlines():
        text = line.strip()
        if not text or text in status_lines:
            continue
        try:
            values.append(json.loads(text))
        except json.JSONDecodeError:
            unexpected.append(text)
    if unexpected or len(values) != 1:
        raise RuntimeError(
            f"{context}: expected exactly one JSON result; raw={sanitized(raw)!r}"
        )
    return values[0]


def require_object(value, context, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise RuntimeError(f"{context}: invalid result schema; value={value!r}")
    return value


def require_nonnegative_integer(value, context, field):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise RuntimeError(f"{context}: {field} must be a nonnegative integer; value={value!r}")
    return int(value)


def parse_sentinel(raw):
    value = require_object(parse_json_output(raw, "disposable sentinel"), "disposable sentinel",
                           ("environment_name", "environment_type", "parent_project"))
    if any(not isinstance(value[key], str) or not value[key] for key in value):
        raise RuntimeError(f"disposable sentinel: null/invalid field; value={value!r}")
    return value


def parse_state(raw, context):
    value = require_object(parse_json_output(raw, context), context,
                           ("executions", "cap_sets", "cap_rows"))
    return {key: require_nonnegative_integer(field, context, key) for key, field in value.items()}


def parse_cardinality_result(raw, expected_teams):
    context = f"teams={expected_teams}"
    value = require_object(parse_json_output(raw, context), context,
                           ("teams", "distinct_teams", "total_charge"))
    teams = require_nonnegative_integer(value["teams"], context, "teams")
    distinct = require_nonnegative_integer(value["distinct_teams"], context, "distinct_teams")
    total = value["total_charge"]
    if isinstance(total, bool) or not isinstance(total, Number):
        raise RuntimeError(f"{context}: total_charge must be non-null numeric; value={total!r}")
    if teams != expected_teams or distinct != expected_teams:
        raise RuntimeError(
            f"{context}: coverage mismatch; teams={teams}, distinct_teams={distinct}"
        )
    return value


def main():
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing or any(os.environ.get(name) for name in os.environ if name.startswith("LEGACY_PROD_DB_")):
        raise RuntimeError("test credentials missing or production variables present")
    sentinel = parse_sentinel(psql("select json_build_object('environment_name',environment_name,'environment_type',environment_type,'parent_project',parent_project) from public.environment_identity where singleton"))
    if sentinel != dict(zip(("environment_name", "environment_type", "parent_project"), EXPECTED_SENTINEL, strict=True)):
        raise RuntimeError(f"disposable sentinel mismatch; value={sentinel!r}")
    state_sql = "select json_build_object('executions',(select count(*) from public.rollover_executions),'cap_sets',(select count(*) from public.prepared_team_cap_sets),'cap_rows',(select count(*) from public.prepared_team_caps))"
    before = parse_state(psql(state_sql), "before state")
    psql(file=ROOT / "supabase/tests/20261017_phaseD_set_based_prepared_team_caps_test.sql")
    psql(file=ROOT / "supabase/verification/verify_phaseD_set_based_prepared_team_caps.sql")
    timings = []
    for size in (1, 10, 32, 100, 2000):
        print(f"[Phase D] teams={size}", flush=True)
        started = time.perf_counter()
        result = psql(f"with teams as (select n from generate_series(1,{size}) n), sources as (select n,n%17 active,n%5 dead from teams), caps as (select t.n,coalesce(sum(s.active),0) active,coalesce(sum(s.dead),0) dead from teams t left join sources s using(n) group by t.n) select json_build_object('teams',count(*),'distinct_teams',count(distinct n),'total_charge',sum(active+dead)) from caps")
        parse_cardinality_result(result, size)
        timings.append({"teams": size, "calculation_ms": round((time.perf_counter()-started)*1000, 3), "round_trips": 1})
    after = parse_state(psql(state_sql), "after state")
    if after != before: raise RuntimeError(f"state changed:{before}->{after}")
    print(json.dumps({"sentinel":"PASS","timings":timings,"rows_unchanged":True,
                      "execution":False,"publication":False,"external_calls":False}, indent=2))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(f"PHASE D FAILED: {exc}", file=sys.stderr)
        raise
