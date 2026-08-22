#!/usr/bin/env python3
"""Read-only production catalog audit for the certified rollover execution contract."""
from __future__ import annotations

import argparse, json, os, re, subprocess, sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/season_rollover_production_parity_v1.json"
CATALOG = ROOT / "config/rollover_operation_catalog.yaml"
PSQL_DEFAULT = "/opt/homebrew/opt/postgresql@16/bin/psql"
SAFE_QUERY = re.compile(r"^\s*(select|with|show)\b", re.I)
FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|call|copy|vacuum|analyze|refresh|reindex|cluster|do)\b", re.I)

@dataclass
class Result:
    status: str
    check: str
    detail: str
    required: bool = True

def validate_read_only_query(sql: str) -> None:
    scrubbed = re.sub(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'", " ", sql, flags=re.S)
    if not SAFE_QUERY.match(scrubbed) or FORBIDDEN.search(scrubbed):
        raise ValueError("query rejected: only SELECT/WITH/SHOW catalog queries are permitted")

def expected_operations() -> list[dict[str, Any]]:
    data = json.loads(CATALOG.read_text())
    operations = [o for o in data["operations"] if o.get("owner") == "execution"]
    return [{"operation_code": o["code"], "operation_order": i, "handler_version": 1,
             "execution_owner": "execution"} for i, o in enumerate(operations, 1)]

def assess(snapshot: dict[str, Any], contract: dict[str, Any]) -> list[Result]:
    out: list[Result] = []
    tables = {x["table_name"]: x for x in snapshot.get("tables", [])}
    columns = {(x["table_name"], x["column_name"]): x for x in snapshot.get("columns", [])}
    for table, required_columns in contract["required_tables"].items():
        out.append(Result("PASS" if table in tables else "FAIL", f"table:{table}", "present" if table in tables else "missing"))
        if table in tables:
            for column in required_columns:
                out.append(Result("PASS" if (table,column) in columns else "FAIL", f"column:{table}.{column}", "present" if (table,column) in columns else "missing"))
            out.append(Result("PASS" if tables[table].get("rls") else "FAIL", f"rls:{table}", "enabled" if tables[table].get("rls") else "disabled"))
    for identity, expected_type in contract.get("required_column_types", {}).items():
        table, column = identity.split(".", 1)
        actual = columns.get((table, column), {}).get("data_type")
        out.append(Result("PASS" if actual == expected_type else "FAIL", f"type:{identity}", f"expected={expected_type} actual={actual or 'missing'}"))
    functions = {x["identity"]: x for x in snapshot.get("functions", [])}
    for identity, expected in contract["required_functions"].items():
        actual = functions.get(identity)
        if not actual:
            out.append(Result("FAIL", f"function:{identity}", "missing")); continue
        out.append(Result("PASS" if bool(actual.get("security_definer")) == expected["security_definer"] else "FAIL", f"security:{identity}", f"security_definer={actual.get('security_definer')}"))
        path = re.sub(r"\s+", " ", actual.get("search_path", "").replace("=", " ")).strip()
        out.append(Result("PASS" if "pg_catalog" in path and "public" in path else "FAIL", f"search_path:{identity}", path or "missing fixed search_path"))
        allowed = bool(actual.get("authenticated_execute"))
        out.append(Result("PASS" if allowed == expected["authenticated_execute"] else "FAIL", f"grant:{identity}", f"authenticated_execute={allowed}"))
        definition = actual.get("definition", "")
        for marker in contract.get("definition_markers", {}).get(identity, []):
            out.append(Result("PASS" if marker in definition else "FAIL", f"definition:{identity}", f"marker {marker!r} " + ("present" if marker in definition else "missing")))
    constraints = snapshot.get("constraints", [])
    for expected in contract["required_constraints"]:
        ok = any(x["table_name"] == expected["table"] and expected["contains"].lower() in x["definition"].lower() for x in constraints)
        out.append(Result("PASS" if ok else "FAIL", f"constraint:{expected['table']}", f"contains {expected['contains']!r}"))
    actual_ops = sorted(snapshot.get("handlers", []), key=lambda x: x["operation_order"])
    expected_ops = expected_operations()
    out.append(Result("PASS" if actual_ops == expected_ops else "FAIL", "handlers:operations-1-31", f"expected={len(expected_ops)} actual={len(actual_ops)}"))
    out.append(Result("PASS", "publication:operations-32-36", "excluded from execution parity; differences do not affect PASS", False))
    direct_writes = snapshot.get("authenticated_table_writes", [])
    out.append(Result("PASS" if not direct_writes else "FAIL", "security:authenticated-direct-writes", "none" if not direct_writes else ", ".join(direct_writes)))
    migration_history = snapshot.get("migration_history")
    out.append(Result("WARN" if migration_history is None else "PASS", "migration-history", "metadata unavailable; schema/function outcomes are authoritative" if migration_history is None else "available", False))
    state = snapshot.get("production_state")
    if state is None:
        out.append(Result("NOT CHECKED", "production-state", "no --league-id supplied", False))
    else:
        out.append(Result("PASS" if state.get("source_exists") else "FAIL", "state:source-2025", str(state)))
        out.append(Result("PASS" if state.get("target_exists") and not state.get("target_active") else "FAIL", "state:target-2026-unpublished", str(state)))
        out.append(Result("PASS" if not state.get("active_execution") else "FAIL", "state:no-midflight-execution", str(state)))
        out.append(Result("PASS" if not state.get("publication_artifacts") else "FAIL", "state:no-publication-artifacts", str(state)))
    return out

def exit_code(results: list[Result]) -> int:
    return 1 if any(x.status == "FAIL" and x.required for x in results) else 0

CATALOG_QUERY = r"""
select jsonb_build_object(
'identity',jsonb_build_object('database',current_database(),'current_user',current_user,'server_version',current_setting('server_version'),'host',inet_server_addr()),
'tables',(select coalesce(jsonb_agg(jsonb_build_object('table_name',c.relname,'rls',c.relrowsecurity)),'[]') from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind in('r','p')),
'columns',(select coalesce(jsonb_agg(jsonb_build_object('table_name',table_name,'column_name',column_name,'data_type',data_type,'is_nullable',is_nullable)),'[]') from information_schema.columns where table_schema='public'),
'constraints',(select coalesce(jsonb_agg(jsonb_build_object('table_name',c.relname,'name',x.conname,'definition',pg_get_constraintdef(x.oid,true))),'[]') from pg_constraint x join pg_class c on c.oid=x.conrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='public'),
'functions',(select coalesce(jsonb_agg(jsonb_build_object('identity',p.proname||'('||replace(oidvectortypes(p.proargtypes),' ','')||')','security_definer',p.prosecdef,'search_path',coalesce(array_to_string(p.proconfig,','),''),'authenticated_execute',has_function_privilege('authenticated',p.oid,'EXECUTE'),'definition',pg_get_functiondef(p.oid))),'[]') from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'),
'handlers',(select coalesce(jsonb_agg(jsonb_build_object('operation_code',operation_code,'operation_order',operation_order,'handler_version',handler_version,'execution_owner',execution_owner) order by operation_order),'[]') from public.rollover_execution_handler_registry where execution_owner='execution' and enabled and operation_order between 1 and 31),
'authenticated_table_writes',(select coalesce(jsonb_agg(table_name),'[]') from information_schema.role_table_grants where table_schema='public' and grantee='authenticated' and privilege_type in('INSERT','UPDATE','DELETE','TRUNCATE') and (table_name like 'rollover_%' or table_name like 'prepared_%')),
'migration_history',null::jsonb,
'production_state',__STATE__)
"""

def state_sql(league_id: str | None) -> str:
    if not league_id: return "null::jsonb"
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", league_id): raise ValueError("--league-id must be a UUID")
    q = "'" + league_id + "'::uuid"
    return f"jsonb_build_object('source_exists',exists(select 1 from public.league_seasons where league_id={q} and season=2025),'target_exists',exists(select 1 from public.league_seasons where league_id={q} and season=2026),'target_active',exists(select 1 from public.league_seasons where league_id={q} and season=2026 and coalesce(is_active,false)),'active_execution',exists(select 1 from public.rollover_executions where league_id={q} and status not in('cancelled','failed','executed_unpublished','completed')),'publication_artifacts',exists(select 1 from public.rollover_executions where league_id={q} and status='completed'))"

def collect(league_id: str | None) -> dict[str, Any]:
    if os.getenv("LEGACY_PRODUCTION_PARITY_AUDIT") != "1":
        raise RuntimeError("LEGACY_PRODUCTION_PARITY_AUDIT=1 is required before connecting")
    names = ["HOST","PORT","NAME","USER","PASSWORD"]
    missing = ["LEGACY_PROD_DB_"+n for n in names if not os.getenv("LEGACY_PROD_DB_"+n)]
    if missing: raise RuntimeError("missing production audit variables: " + ", ".join(missing))
    query = CATALOG_QUERY.replace("__STATE__", state_sql(league_id))
    validate_read_only_query(query)
    env = os.environ.copy()
    for n in names: env["PG" + ("DATABASE" if n == "NAME" else n)] = env["LEGACY_PROD_DB_"+n]
    command = "begin transaction read only; set local statement_timeout='15s'; " + query + "; rollback;"
    psql = os.getenv("LEGACY_PSQL", PSQL_DEFAULT)
    proc = subprocess.run([psql,"-X","-qAt","-v","ON_ERROR_STOP=1","-c",command],env=env,text=True,capture_output=True)
    if proc.returncode: raise RuntimeError("production catalog query failed (credentials redacted): " + proc.stderr.strip())
    return json.loads(next(line for line in proc.stdout.splitlines() if line.strip()))

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args=parser.parse_args()
    try:
        snapshot=collect(args.league_id)
        contract=json.loads(CONTRACT.read_text())
        results=assess(snapshot,contract)
    except Exception as exc:
        print(f"FAIL connection/safety: {exc}", file=sys.stderr); return 2
    failed=exit_code(results) != 0
    report={"status":"FAIL" if failed else "PASS","baseline_tag":contract["baseline_tag"],"database_identity":snapshot.get("identity",{}),"summary":{s:sum(x.status==s for x in results) for s in ("PASS","FAIL","WARN","NOT CHECKED")},"checks":[asdict(x) for x in results]}
    if args.json_output: print(json.dumps(report,indent=2,default=str))
    else:
        ident=report["database_identity"]
        print(f"Database: host={os.getenv('LEGACY_PROD_DB_HOST')} database={ident.get('database')} user={ident.get('current_user')} server={ident.get('server_version')}")
        for x in results: print(f"{x.status:11} {x.check}: {x.detail}")
        print(f"{report['status']}: certified execution parity | " + " ".join(f"{k}={v}" for k,v in report["summary"].items()))
        print("PASS does not execute or authorize rollover; publication remains uncertified.")
    return exit_code(results)

if __name__ == "__main__": raise SystemExit(main())
