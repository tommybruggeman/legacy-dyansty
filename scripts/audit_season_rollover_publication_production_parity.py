#!/usr/bin/env python3
"""Read-only production catalog audit for the season rollover publication contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/season_rollover_publication_production_parity_v1.json"

PSQL_DEFAULT = "/opt/homebrew/opt/postgresql@16/bin/psql"

SAFE_QUERY = re.compile(r"^\s*(select|with|show)\b", re.I)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|"
    r"call|copy|vacuum|analyze|refresh|reindex|cluster|do)\b",
    re.I,
)


@dataclass
class Result:
    status: str
    check: str
    detail: str
    required: bool = True


def validate_read_only_query(sql: str) -> None:
    scrubbed = re.sub(
        r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'",
        " ",
        sql,
        flags=re.S,
    )
    if not SAFE_QUERY.match(scrubbed) or FORBIDDEN.search(scrubbed):
        raise ValueError(
            "query rejected: only SELECT/WITH/SHOW catalog queries are permitted"
        )


def expected_operations(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return contract["publication_operations"]


def assess(
    snapshot: dict[str, Any],
    contract: dict[str, Any],
) -> list[Result]:
    out: list[Result] = []

    tables = {x["table_name"]: x for x in snapshot.get("tables", [])}

    for table in contract["required_tables"]:
        actual = tables.get(table)

        if not actual:
            out.append(Result("FAIL", f"table:{table}", "missing"))
            continue

        out.append(Result("PASS", f"table:{table}", "present"))

        out.append(
            Result(
                "PASS" if actual.get("rls") else "FAIL",
                f"rls:{table}",
                "enabled" if actual.get("rls") else "disabled",
            )
        )

    functions = {
        x["identity"]: x
        for x in snapshot.get("functions", [])
    }

    for identity in contract["required_authenticated_wrappers"]:
        actual = functions.get(identity)

        if not actual:
            out.append(Result("FAIL", f"wrapper:{identity}", "missing"))
            continue

        out.append(Result("PASS", f"wrapper:{identity}", "present"))

        out.append(
            Result(
                "PASS" if actual.get("security_definer") else "FAIL",
                f"security:{identity}",
                f"security_definer={actual.get('security_definer')}",
            )
        )

        path = re.sub(
            r"\s+",
            " ",
            actual.get("search_path", "").replace("=", " "),
        ).strip()

        out.append(
            Result(
                "PASS" if "pg_catalog" in path and "public" in path else "FAIL",
                f"search_path:{identity}",
                path or "missing fixed search_path",
            )
        )

        allowed = bool(actual.get("authenticated_execute"))

        out.append(
            Result(
                "PASS" if allowed else "FAIL",
                f"grant:{identity}",
                f"authenticated_execute={allowed}",
            )
        )

    for identity in contract["required_private_functions"]:
        actual = functions.get(identity)

        if not actual:
            out.append(Result("FAIL", f"private:{identity}", "missing"))
            continue

        out.append(Result("PASS", f"private:{identity}", "present"))

        out.append(
            Result(
                "PASS" if actual.get("security_definer") else "FAIL",
                f"private-security:{identity}",
                f"security_definer={actual.get('security_definer')}",
            )
        )

        path = re.sub(
            r"\s+",
            " ",
            actual.get("search_path", "").replace("=", " "),
        ).strip()

        out.append(
            Result(
                "PASS" if "pg_catalog" in path and "public" in path else "FAIL",
                f"private-search_path:{identity}",
                path or "missing fixed search_path",
            )
        )

        authenticated_execute = bool(actual.get("authenticated_execute"))

        out.append(
            Result(
                "PASS" if not authenticated_execute else "FAIL",
                f"private-grant:{identity}",
                f"authenticated_execute={authenticated_execute}",
            )
        )

    actual_ops = sorted(
        snapshot.get("handlers", []),
        key=lambda x: x["operation_order"],
    )

    expected_ops = expected_operations(contract)

    out.append(
        Result(
            "PASS" if actual_ops == expected_ops else "FAIL",
            "handlers:publication-operations-32-36",
            f"expected={len(expected_ops)} actual={len(actual_ops)}",
        )
    )

    direct_writes = snapshot.get(
        "authenticated_publication_table_writes",
        [],
    )

    out.append(
        Result(
            "PASS" if not direct_writes else "FAIL",
            "security:authenticated-publication-direct-writes",
            "none" if not direct_writes else ", ".join(direct_writes),
        )
    )

    migrations = set(snapshot.get("migration_versions", []))

    for version in contract["required_migrations"]:
        out.append(
            Result(
                "PASS" if version in migrations else "FAIL",
                f"migration:{version}",
                "present" if version in migrations else "missing",
            )
        )

    state = snapshot.get("production_state")

    if state is None:
        out.append(
            Result(
                "NOT CHECKED",
                "production-state",
                "no --league-id supplied",
                False,
            )
        )
    else:
        out.append(
            Result(
                "PASS" if state.get("source_exists") else "FAIL",
                "state:source-2025",
                str(state),
            )
        )

        out.append(
            Result(
                "PASS"
                if state.get("target_exists")
                and not state.get("target_active")
                else "FAIL",
                "state:target-2026-unpublished",
                str(state),
            )
        )

        out.append(
            Result(
                "PASS"
                if not state.get("publication_artifacts")
                else "FAIL",
                "state:no-publication-artifacts",
                str(state),
            )
        )

    return out


def exit_code(results: list[Result]) -> int:
    return 1 if any(
        x.status == "FAIL" and x.required
        for x in results
    ) else 0


CATALOG_QUERY = r"""
select jsonb_build_object(
  'identity',
    jsonb_build_object(
      'database',current_database(),
      'current_user',current_user,
      'server_version',current_setting('server_version'),
      'host',inet_server_addr()
    ),

  'tables',
    (
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'table_name',c.relname,
            'rls',c.relrowsecurity
          )
        ),
        '[]'
      )
      from pg_class c
      join pg_namespace n on n.oid=c.relnamespace
      where n.nspname='public'
        and c.relkind in('r','p')
    ),

  'functions',
    (
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'identity',
              p.proname || '(' ||
              replace(oidvectortypes(p.proargtypes),' ','') ||
              ')',
            'security_definer',p.prosecdef,
            'search_path',
              coalesce(array_to_string(p.proconfig,','),''),
            'authenticated_execute',
              has_function_privilege(
                'authenticated',
                p.oid,
                'EXECUTE'
              )
          )
        ),
        '[]'
      )
      from pg_proc p
      join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='public'
    ),

  'handlers',
    (
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'operation_code',operation_code,
            'operation_order',operation_order,
            'handler_version',handler_version,
            'execution_owner',execution_owner
          )
          order by operation_order
        ),
        '[]'
      )
      from public.rollover_execution_handler_registry
      where execution_owner='publication'
        and enabled
        and operation_order between 32 and 36
    ),

  'authenticated_publication_table_writes',
    (
      select coalesce(
        jsonb_agg(table_name),
        '[]'
      )
      from information_schema.role_table_grants
      where table_schema='public'
        and grantee='authenticated'
        and privilege_type in(
          'INSERT',
          'UPDATE',
          'DELETE',
          'TRUNCATE'
        )
        and table_name in(
          'rollover_target_season_authority_publications',
          'rollover_target_cap_authority_publications',
          'rollover_target_market_visibility_publications',
          'rollover_target_market_visibility_rows',
          'rollover_cutover_release_publications',
          'publication_context_generations'
        )
    ),

  'migration_versions',
    (
      select coalesce(
        jsonb_agg(version::text),
        '[]'
      )
      from supabase_migrations.schema_migrations
      where version::text in(
        '20260917',
        '20260918',
        '20260919',
        '20260920',
        '20260921'
      )
    ),

  'production_state',
    __STATE__
)
"""


def state_sql(league_id: str | None) -> str:
    if not league_id:
        return "null::jsonb"

    if not re.fullmatch(r"[0-9a-fA-F-]{36}", league_id):
        raise ValueError("--league-id must be a UUID")

    q = "'" + league_id + "'::uuid"

    return (
        "jsonb_build_object("
        "'source_exists',exists("
        "select 1 from public.league_seasons "
        f"where league_id={q} and season=2025"
        "),"
        "'target_exists',exists("
        "select 1 from public.league_seasons "
        f"where league_id={q} and season=2026"
        "),"
        "'target_active',exists("
        "select 1 from public.league_seasons "
        f"where league_id={q} and season=2026 "
        "and coalesce(is_active,false)"
        "),"
        "'publication_artifacts',("
        "exists(select 1 "
        "from public.rollover_target_season_authority_publications "
        f"where league_id={q}) "
        "or exists(select 1 "
        "from public.rollover_target_cap_authority_publications "
        f"where league_id={q}) "
        "or exists(select 1 "
        "from public.rollover_target_market_visibility_publications "
        f"where league_id={q}) "
        "or exists(select 1 "
        "from public.rollover_cutover_release_publications "
        f"where league_id={q}) "
        "or exists(select 1 "
        "from public.publication_context_generations "
        f"where league_id={q})"
        ")"
        ")"
    )


def collect(league_id: str | None) -> dict[str, Any]:
    if os.getenv("LEGACY_PRODUCTION_PUBLICATION_PARITY_AUDIT") != "1":
        raise RuntimeError(
            "LEGACY_PRODUCTION_PUBLICATION_PARITY_AUDIT=1 "
            "is required before connecting"
        )

    names = ["HOST", "PORT", "NAME", "USER", "PASSWORD"]

    missing = [
        "LEGACY_PROD_DB_" + n
        for n in names
        if not os.getenv("LEGACY_PROD_DB_" + n)
    ]

    if missing:
        raise RuntimeError(
            "missing production audit variables: " +
            ", ".join(missing)
        )

    query = CATALOG_QUERY.replace(
        "__STATE__",
        state_sql(league_id),
    )

    validate_read_only_query(query)

    env = os.environ.copy()

    for n in names:
        env[
            "PG" + ("DATABASE" if n == "NAME" else n)
        ] = env["LEGACY_PROD_DB_" + n]

    command = (
        "begin transaction read only; "
        "set local statement_timeout='15s'; "
        + query +
        "; rollback;"
    )

    psql = os.getenv("LEGACY_PSQL", PSQL_DEFAULT)

    proc = subprocess.run(
        [
            psql,
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            command,
        ],
        env=env,
        text=True,
        capture_output=True,
    )

    if proc.returncode:
        raise RuntimeError(
            "production publication catalog query failed "
            "(credentials redacted): " +
            proc.stderr.strip()
        )

    return json.loads(
        next(
            line
            for line in proc.stdout.splitlines()
            if line.strip()
        )
    )


def self_test() -> None:
    contract = json.loads(CONTRACT.read_text())

    good = {
        "tables": [
            {"table_name": name, "rls": True}
            for name in contract["required_tables"]
        ],
        "functions": (
            [
                {
                    "identity": name,
                    "security_definer": True,
                    "search_path": "search_path=pg_catalog,public",
                    "authenticated_execute": True,
                }
                for name in contract["required_authenticated_wrappers"]
            ]
            +
            [
                {
                    "identity": name,
                    "security_definer": True,
                    "search_path": "search_path=pg_catalog,public",
                    "authenticated_execute": False,
                }
                for name in contract["required_private_functions"]
            ]
        ),
        "handlers": contract["publication_operations"],
        "authenticated_publication_table_writes": [],
        "migration_versions": contract["required_migrations"],
        "production_state": None,
    }

    results = assess(good, contract)

    if exit_code(results) != 0:
        for result in results:
            print(result)
        raise RuntimeError("positive self-test failed")

    bad = json.loads(json.dumps(good))
    bad["authenticated_publication_table_writes"] = [
        "rollover_target_season_authority_publications"
    ]

    results = assess(bad, contract)

    if exit_code(results) == 0:
        raise RuntimeError(
            "negative direct-write self-test failed"
        )

    validate_read_only_query("select 1")

    try:
        validate_read_only_query(
            "select 1; update public.foo set x=1"
        )
    except ValueError:
        pass
    else:
        raise RuntimeError(
            "unsafe-query self-test failed"
        )

    print("PASS: publication auditor offline self-test")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--self-test", action="store_true")

    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    try:
        snapshot = collect(args.league_id)
        contract = json.loads(CONTRACT.read_text())
        results = assess(snapshot, contract)
    except Exception as exc:
        print(
            f"FAIL connection/safety: {exc}",
            file=sys.stderr,
        )
        return 2

    failed = exit_code(results) != 0

    report = {
        "status": "FAIL" if failed else "PASS",
        "contract_name": contract["contract_name"],
        "database_identity": snapshot.get("identity", {}),
        "summary": {
            s: sum(x.status == s for x in results)
            for s in ("PASS", "FAIL", "WARN", "NOT CHECKED")
        },
        "checks": [asdict(x) for x in results],
    }

    if args.json_output:
        print(json.dumps(report, indent=2, default=str))
    else:
        ident = report["database_identity"]

        print(
            "Database: "
            f"host={os.getenv('LEGACY_PROD_DB_HOST')} "
            f"database={ident.get('database')} "
            f"user={ident.get('current_user')} "
            f"server={ident.get('server_version')}"
        )

        for result in results:
            print(
                f"{result.status:11} "
                f"{result.check}: "
                f"{result.detail}"
            )

        print(
            f"{report['status']}: publication production parity | "
            + " ".join(
                f"{k}={v}"
                for k, v in report["summary"].items()
            )
        )

        print(
            "PASS is read-only evidence only; "
            "it does not execute or authorize publication."
        )

    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
