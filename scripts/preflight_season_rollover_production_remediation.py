#!/usr/bin/env python3
"""Read-only eligibility preflight for the four certified parity migrations."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/"config/season_rollover_remediation_preflight_v1.json"
PSQL_DEFAULT="/opt/homebrew/opt/postgresql@16/bin/psql"
SAFE=re.compile(r"^\s*(select|with|show)\b",re.I)
FORBIDDEN=re.compile(r"\b(insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|call|copy|vacuum|analyze|refresh|reindex|cluster|do)\b",re.I)
TERMINAL={"cancelled","failed_precommit","failed_postcommit_validation","executed_unpublished","completed"}

OLD_DEAD="public.rollover_material_fingerprint((select jsonb_agg(deterministic_fingerprint order by contract_agreement_id) from public.rollover_dead_cap_obligations where rollover_execution_id=x.id))"
NEW_DEAD="public.rollover_material_fingerprint(coalesce((select jsonb_agg(deterministic_fingerprint order by contract_agreement_id) from public.rollover_dead_cap_obligations where rollover_execution_id=x.id),'[]'::jsonb))"
OLD_CAP="select coalesce(sum(c.salary),0),count(*) into active,ac from public.season_roster_assignments r join public.contract_seasons c on c.id=r.target_contract_season_id where r.assignment_set_id=aset.id and r.league_team_id=t.id and c.obligation_status='active';"
NEW_CAP="select coalesce(sum(z.amount),0),count(*) into active,ac from (select c.salary amount from public.season_roster_assignments r join public.contract_seasons c on c.id=r.target_contract_season_id where r.assignment_set_id=aset.id and r.league_team_id=t.id and c.obligation_status='active' union all select c.cap_hit amount from public.contract_agreements a join public.contract_seasons c on c.contract_id=a.id and c.league_season_id=aset.target_league_season_id where a.league_id=x.league_id and a.league_team_id=t.id and a.status='active' and public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id)) z;"

def validate_query(sql:str)->None:
    scrub=re.sub(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'"," ",sql,flags=re.S)
    if not SAFE.match(scrub) or FORBIDDEN.search(scrub): raise ValueError("only SELECT/WITH/SHOW inspection is permitted")

def state(old:bool,new:bool)->str:
    if old and not new:return "REQUIRED"
    if new and not old:return "ALREADY SATISFIED"
    return "UNSAFE / UNKNOWN"

def classify(snapshot:dict[str,Any],contract:dict[str,Any])->dict[str,Any]:
    issues=[]; tables=set(snapshot.get("tables",[])); roles=set(snapshot.get("roles",[])); columns=set(snapshot.get("columns",[]))
    funcs={x["identity"]:x for x in snapshot.get("functions",[])}
    for x in contract["required_tables"]:
        if x not in tables:issues.append("missing table "+x)
    for x in contract["required_roles"]:
        if x not in roles:issues.append("missing role "+x)
    for x in contract["required_columns"]:
        if x not in columns:issues.append("missing column "+x)
    for x in contract["required_functions"]:
        if x not in funcs:issues.append("missing helper function "+x)
    active=[x for x in snapshot.get("executions",[]) if x.get("status") not in TERMINAL]
    if active:issues.append("nonterminal rollover executions exist: "+",".join(f"{x.get('id')}:{x.get('status')}" for x in active))

    private=funcs.get("approve_canonical_rollover_policy_private(jsonb,uuid)")
    auth=funcs.get("approve_canonical_rollover_policy_authenticated(jsonb)")
    private_ok=bool(private and all(m in private["definition"] for m in ("certified rollover policy boundary required","exactly one canonical commissioner membership required","rollover_material_fingerprint")))
    auth_ok=bool(auth and "approve_canonical_rollover_policy_private" in auth["definition"] and auth.get("authenticated_execute"))
    if not private and not auth: s922="REQUIRED"
    elif private_ok and not auth: s922="REQUIRED"
    elif private_ok and auth_ok:s922="ALREADY SATISFIED"
    else:s922="UNSAFE / UNKNOWN"

    pred=funcs.get("phase3b8a_is_preserved_off_roster_liability(uuid,uuid,text,uuid)")
    val=funcs.get("validate_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid)")
    writer=funcs.get("write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid)")
    corrected=bool(pred and val and writer and all(m in pred["definition"] for m in ("active_off_roster_liability","preserve_active_liability","review_fingerprint")) and all(m in val["definition"] for m in ("target_roster_owner_mismatch","preserved_off_roster_source_assignment_conflict","phase3b8a_is_preserved_off_roster_liability")) and all(m in writer["definition"] for m in ("intentional_exclusion","preserved_off_roster_liability_count","phase3b8a_is_preserved_off_roster_liability")))
    predecessor=bool(not pred and val and writer and "target_roster_owner_mismatch" in val["definition"] and "phase3b8a_is_preserved_off_roster_liability" not in val["definition"] and "aggregate_assignment_set_hash" in writer["definition"] and "preserved_off_roster_liability_count" not in writer["definition"])
    s1001="ALREADY SATISFIED" if corrected else "REQUIRED" if predecessor else "UNSAFE / UNKNOWN"
    cap=funcs.get("write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)",{}).get("definition","")
    s1002=state(OLD_DEAD in cap,NEW_DEAD in cap)
    s1003=state(OLD_CAP in cap,NEW_CAP in cap)
    migrations={"20260922_season_rollover_trusted_boundaries.sql":s922,"20261001_phase3b8a_preserved_off_roster_liability.sql":s1001,"20261002_phase3b10b_empty_dead_cap_evidence.sql":s1002,"20261003_phase3b10b_preserved_liability_caps.sql":s1003}
    if s1003 in {"REQUIRED","ALREADY SATISFIED"} and s1001=="UNSAFE / UNKNOWN":issues.append("20261003 dependency on 20261001 is unrecognized")
    for name,value in migrations.items():
        if value=="UNSAFE / UNKNOWN":issues.append(name+" has an unrecognized deployed definition")
    eligible=not issues
    return {"status":"PASS" if eligible else "FAIL","eligible":eligible,"migrations":migrations,"issues":issues,"active_executions":active,"affected_function_grants":snapshot.get("grants",[]),"identity":snapshot.get("identity",{})}

QUERY=r"""select jsonb_build_object(
'identity',jsonb_build_object('database',current_database(),'current_user',current_user,'server_version',current_setting('server_version'),'address',inet_server_addr()),
'tables',(select coalesce(jsonb_agg(c.relname),'[]') from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind in('r','p')),
'columns',(select coalesce(jsonb_agg(table_name||'.'||column_name),'[]') from information_schema.columns where table_schema='public'),
'roles',(select coalesce(jsonb_agg(rolname),'[]') from pg_roles),
'functions',(select coalesce(jsonb_agg(jsonb_build_object('identity',p.proname||'('||replace(oidvectortypes(p.proargtypes),' ','')||')','definition',pg_get_functiondef(p.oid),'security_definer',p.prosecdef,'authenticated_execute',has_function_privilege('authenticated',p.oid,'EXECUTE'))),'[]') from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'),
'executions',(select coalesce(jsonb_agg(jsonb_build_object('id',id,'status',status)),'[]') from public.rollover_executions),
'grants',(select coalesce(jsonb_agg(jsonb_build_object('function',p.proname||'('||replace(oidvectortypes(p.proargtypes),' ','')||')','public',has_function_privilege('public',p.oid,'EXECUTE'),'anon',has_function_privilege('anon',p.oid,'EXECUTE'),'authenticated',has_function_privilege('authenticated',p.oid,'EXECUTE'),'service_role',has_function_privilege('service_role',p.oid,'EXECUTE'))),'[]') from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname in('approve_canonical_rollover_policy_private','approve_canonical_rollover_policy_authenticated','phase3b8a_is_preserved_off_roster_liability','validate_target_roster_assignment_set_phase3b8a_private','write_target_roster_assignment_set_phase3b8a_private','write_prepared_caps_phase3b10b_private'))
)"""

def build_read_only_command(query:str=QUERY)->str:
    validate_query(query)
    return "begin transaction read only; set local statement_timeout='15s'; "+query+"; rollback;"

def collect()->dict[str,Any]:
    if os.getenv("LEGACY_PRODUCTION_PARITY_AUDIT")!="1":raise RuntimeError("LEGACY_PRODUCTION_PARITY_AUDIT=1 is required")
    names=["HOST","PORT","NAME","USER","PASSWORD"]
    missing=["LEGACY_PROD_DB_"+x for x in names if not os.getenv("LEGACY_PROD_DB_"+x)]
    if missing:raise RuntimeError("missing production variables: "+", ".join(missing))
    command=build_read_only_command();env=os.environ.copy()
    for x in names:env["PG"+("DATABASE" if x=="NAME" else x)]=env["LEGACY_PROD_DB_"+x]
    proc=subprocess.run([os.getenv("LEGACY_PSQL",PSQL_DEFAULT),"-X","-qAt","-v","ON_ERROR_STOP=1","-c",command],env=env,text=True,capture_output=True)
    if proc.returncode:raise RuntimeError("preflight catalog query failed (credentials redacted): "+proc.stderr.strip())
    return json.loads(next(x for x in proc.stdout.splitlines() if x.strip()))

def verify_files(contract:dict[str,Any])->list[str]:
    errors=[]
    for item in contract["migrations"]:
        path=ROOT/"supabase/migrations"/item["name"]
        actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        if actual!=item["sha256"]:errors.append(f"certified migration hash mismatch: {item['name']}")
    return errors

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--json",action="store_true");args=parser.parse_args()
    contract=json.loads(CONTRACT.read_text());file_errors=verify_files(contract)
    try:snapshot=collect();report=classify(snapshot,contract)
    except Exception as exc:print(f"FAIL: {exc}",file=sys.stderr);return 2
    report["issues"][:0]=file_errors
    if file_errors:report["status"]="FAIL";report["eligible"]=False
    if args.json:print(json.dumps(report,indent=2,default=str))
    else:
        i=report["identity"];print(f"Database: host={os.getenv('LEGACY_PROD_DB_HOST')} database={i.get('database')} user={i.get('current_user')} server={i.get('server_version')}")
        for name,value in report["migrations"].items():print(f"{value:19} {name}")
        for issue in report["issues"]:print("FAIL                "+issue)
        print(f"{report['status']}: remediation eligibility={'YES' if report['eligible'] else 'NO'}; no changes performed")
    return 0 if report["eligible"] else 1

if __name__=="__main__":raise SystemExit(main())
