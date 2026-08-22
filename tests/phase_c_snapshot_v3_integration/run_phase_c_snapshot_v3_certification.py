#!/usr/bin/env python3
"""Disposable-only database certification for snapshot v3 primitives and wiring."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PSQL = Path("/opt/homebrew/opt/postgresql@16/bin/psql")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.fixtures.certification_sentinel import ENVIRONMENT_VARIABLES, expected_sentinel
REQUIRED_SENTINEL = expected_sentinel("rollover-cardinality-certification")


def database_env() -> dict[str, str]:
    required = ("PHASE3B5H_TEST_DB_HOST", "PHASE3B5H_TEST_DB_PORT", "PHASE3B5H_TEST_DB_NAME",
                "PHASE3B5H_TEST_DB_USER", "PHASE3B5H_TEST_DB_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing disposable database variables: " + ",".join(missing))
    if any(os.environ.get(name) for name in os.environ if name.startswith("LEGACY_PROD_DB_")):
        raise RuntimeError("production variables must be absent")
    return {**os.environ, "PGHOST": os.environ[required[0]], "PGPORT": os.environ[required[1]],
            "PGDATABASE": os.environ[required[2]], "PGUSER": os.environ[required[3]],
            "PGPASSWORD": os.environ[required[4]]}


def psql(env: dict[str, str], *, file: Path | None = None, sql: str | None = None) -> str:
    command = [str(PSQL), "-X", "-v", "ON_ERROR_STOP=1", "-At"]
    if file is not None:
        for name, value in zip(ENVIRONMENT_VARIABLES, REQUIRED_SENTINEL, strict=True):
            command += ["-v", name.lower().removeprefix("phase3b5h_expected_") + "=" + value]
        command += ["-f", str(file)]
    else:
        command += ["-c", sql or ""]
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip().replace(env["PGPASSWORD"], "[REDACTED]"))
    return completed.stdout.strip()


def main() -> int:
    env = database_env()
    sentinel = psql(env, sql="SELECT environment_name||'|'||environment_type||'|'||parent_project FROM public.environment_identity WHERE singleton")
    if tuple(sentinel.split("|")) != REQUIRED_SENTINEL:
        raise RuntimeError("disposable sentinel mismatch")
    before = psql(env, sql="SELECT jsonb_build_object('snapshots',count(*),'chunks',(SELECT count(*) FROM public.rollover_execution_input_snapshot_component_chunks),'executions',(SELECT count(*) FROM public.rollover_execution_runs),'publication',false)::text FROM public.rollover_execution_input_snapshots")
    psql(env, file=ROOT / "supabase/tests/20261016_rollover_snapshot_v3_chunked_evidence_test.sql")
    psql(env, file=ROOT / "supabase/verification/verify_rollover_snapshot_v3_chunked_evidence.sql")
    after = psql(env, sql="SELECT jsonb_build_object('snapshots',count(*),'chunks',(SELECT count(*) FROM public.rollover_execution_input_snapshot_component_chunks),'executions',(SELECT count(*) FROM public.rollover_execution_runs),'publication',false)::text FROM public.rollover_execution_input_snapshots")
    if json.loads(before) != json.loads(after):
        raise RuntimeError(f"rollback state mismatch: before={before} after={after}")
    print(json.dumps({"sentinel": "PASS", "vectors": [0, 1, 2, 100, 2000], "rollback": True,
                      "execution": False, "publication": False, "external_calls": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
