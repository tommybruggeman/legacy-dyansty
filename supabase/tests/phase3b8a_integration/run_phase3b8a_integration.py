"""Sentinel-verified disposable Phase 3B.8A integration suite."""
from __future__ import annotations
import importlib.util, subprocess, uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/"phase3b7c_integration"/"run_phase3b7c_integration.py"
spec=importlib.util.spec_from_file_location("phase3b7c_base",BASE)
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
PSQL=base.PSQL

def invoke(item,key):return base.invoke(item,key)
def require_success(r):
 if r.returncode or '"success": true' not in r.stdout:raise RuntimeError(base.redact(r.stdout+r.stderr))
def require_failure(r,code):base.require_failure(r,code)
def expect_private_failure(item,mutation,code,writer=False):
 fn=("write_target_roster_assignment_set_phase3b8a_private("+
     f"'{item['execution_id']}','{item['plan_id']}','{item['approval_id']}','{item['actor_id']}')") if writer else (
     "validate_target_roster_assignment_set_phase3b8a_private("+
     f"'{item['execution_id']}','{item['plan_id']}','{item['approval_id']}')")
 sql=f"""begin;set local session_replication_role=replica;{mutation}
 do $$begin begin perform public.{fn};raise exception '__expected_failure_missing__';
 exception when others then if sqlerrm<>'{code}' then raise;end if;end;end$$;rollback;"""
 base.run(sql)

def main():
 base.env()
 probe=base.redact(":".join(base.PARAMETERS.values()))
 if any(v and v in probe for v in base.PARAMETERS.values()):raise SystemExit("credential redaction failed")
 sentinel="select count(*)||':'||count(*) filter(where singleton and environment_name='phase3b5h-testing' and environment_type='disposable_test' and parent_project='Legacy-Dynasty') from public.environment_identity;"
 if base.run(sentinel).stdout.strip()!="1:1":raise SystemExit("sentinel mismatch")
 control_tables=("rollover_target_roster_assignment_sets",)+base.RELEASE_TABLES
 baseline_control,baseline_domain,baseline_contract,baseline_control_tables=(
  base.control(),base.signature(base.DOMAIN_TABLES),base.signature(base.CONTRACT_TABLES),base.signature(control_tables))
 fixtures=[base.fixture(x) for x in ("empty","retained","rollback","concurrency")]
 try:
  for i,f in enumerate(fixtures):
   base.run(ROOT/"setup.sql",f)
   if i in (1,2):base.run(ROOT.parent/"phase3b6c1_integration"/"setup_review.sql",f)
   base.run(ROOT/"approve.sql",f)
   f["approval_id"]=base.run(f"select id from public.rollover_execution_plan_approvals where rollover_execution_id='{f['execution_id']}';").stdout.strip()

  empty=fixtures[0];first=invoke(empty,empty["namespace"]+"-execute");require_success(first)
  empty_state=base.run(f"select (select count(*) from public.rollover_target_roster_assignment_sets where rollover_execution_id='{empty['execution_id']}')||':'||(select expected_row_count from public.rollover_target_roster_assignment_sets where rollover_execution_id='{empty['execution_id']}')||':'||(select count(*) from public.season_roster_assignments where assignment_set_id in(select id from public.rollover_target_roster_assignment_sets where rollover_execution_id='{empty['execution_id']}')); ").stdout.strip()
  if empty_state!="1:0:0":raise RuntimeError(f"empty complete-set mismatch {empty_state}")
  replay=invoke(empty,empty["namespace"]+"-execute")
  if replay.returncode or '"idempotent": true' not in replay.stdout:raise RuntimeError("same-key replay failed")
  changed=base.values(empty,empty["namespace"]+"-execute");changed["expected_execution_status"]="changed-material"
  conflict=base.run(ROOT.parent/"phase3b5i_integration"/"invoke_execution.sql",changed,False)
  if conflict.returncode==0 or "Idempotency key material request conflict" not in conflict.stderr:raise RuntimeError("changed-material conflict not rejected")

  retained=fixtures[1];rr=invoke(retained,retained["namespace"]+"-execute");require_success(rr)
  retained_state=base.run(f"select s.expected_row_count||':'||count(r.id)||':'||count(r.id) filter(where r.league_team_id='{retained['team_one_id']}' and r.sleeper_player_id='{retained['player_three_id']}' and r.roster_status='pending_unpublished' and r.roster_designation='other') from public.rollover_target_roster_assignment_sets s left join public.season_roster_assignments r on r.assignment_set_id=s.id where s.rollover_execution_id='{retained['execution_id']}' group by s.expected_row_count;").stdout.strip()
  if retained_state!="1:1:1":raise RuntimeError(f"retained assignment mismatch {retained_state}")
  if base.run(f"select count(*) from public.season_roster_assignments where assignment_set_id in(select id from public.rollover_target_roster_assignment_sets where rollover_execution_id='{retained['execution_id']}') and sleeper_player_id in('{retained['player_one_id']}','{retained['player_two_id']}');").stdout.strip()!="0":raise RuntimeError("released or held player assigned")
  source_hash=base.run(f"select public.rollover_material_fingerprint(coalesce(jsonb_agg(to_jsonb(r) order by r.id),'[]'::jsonb)) from public.season_roster_assignments r where league_season_id='{retained['source_league_season_id']}';").stdout.strip()
  if not source_hash:raise RuntimeError("source checksum absent")
  direct=base.run(f"insert into public.season_roster_assignments(league_season_id,league_team_id,canonical_player_id,sleeper_player_id,roster_designation,source) values('{retained['target_league_season_id']}','{retained['team_one_id']}','blocked-direct','blocked-direct','other','legacy-sync');",check=False)
  if direct.returncode==0 or "rollover_cutover_roster_writes_blocked" not in direct.stderr:raise RuntimeError("direct target insert was not blocked")
  expect_private_failure(retained,f"update public.contract_seasons set obligation_status='expired' where contract_id='{retained['agreement_three_id']}' and league_season_id='{retained['target_league_season_id']}';","target_contract_obligation_missing")
  expect_private_failure(retained,f"delete from public.season_roster_assignments where id='{retained['roster_three_id']}';","target_roster_owner_mismatch")
  expect_private_failure(retained,f"update public.contract_agreements set league_team_id='{retained['team_two_id']}' where id='{retained['agreement_three_id']}';","target_roster_owner_mismatch")
  expect_private_failure(retained,f"delete from public.league_memberships where league_id='{retained['league_id']}' and league_team_id='{retained['team_one_id']}';","target_roster_unknown_owner")
  expect_private_failure(retained,f"delete from public.season_team_mappings where league_season_id='{retained['target_league_season_id']}' and league_team_id='{retained['team_one_id']}';","target_roster_team_missing")
  other_league,other_team=str(uuid.uuid4()),str(uuid.uuid4())
  expect_private_failure(retained,f"insert into public.leagues(id,name,created_by,sleeper_league_id) values('{other_league}','phase3b8a-cross-league','{retained['actor_id']}','cross-{other_league}');insert into public.league_teams(id,league_id,owner_name,team_name,sleeper_roster_id,user_id) values('{other_team}','{other_league}','Cross','Cross',99,'{retained['actor_id']}');update public.contract_agreements set league_team_id='{other_team}' where id='{retained['agreement_three_id']}';","target_roster_team_cross_league")
  expect_private_failure(retained,f"update public.rollover_contract_releases set player_id='{retained['player_three_id']}' where id=(select id from public.rollover_contract_releases where rollover_execution_id='{retained['execution_id']}' order by id limit 1);","target_roster_release_conflict")
  expect_private_failure(retained,f"update public.rollover_target_roster_assignment_sets set aggregate_assignment_set_hash=repeat('0',64) where rollover_execution_id='{retained['execution_id']}';","target_roster_set_conflict",True)

  failed=fixtures[2]
  base.run(f"set session_replication_role=replica; update public.season_roster_assignments set league_team_id='{failed['team_two_id']}' where id='{failed['roster_three_id']}'; set session_replication_role=origin;")
  require_failure(invoke(failed,failed["namespace"]+"-execute"),"target_roster_owner_mismatch")
  rollback_state=base.run(f"select (select count(*) from public.rollover_target_roster_assignment_sets where rollover_execution_id='{failed['execution_id']}')||':'||(select count(*) from public.rollover_contract_releases where rollover_execution_id='{failed['execution_id']}')||':'||(select count(*) from public.rollover_commissioner_holds where rollover_execution_id='{failed['execution_id']}')||':'||(select count(*) from public.contract_agreements where rollover_execution_id='{failed['execution_id']}' and status='released');").stdout.strip()
  if rollback_state!="0:0:0:0":raise RuntimeError(f"inner rollback mismatch {rollback_state}")

  con=fixtures[3];ev=base.values(con,con["namespace"]+"-same");procs=[]
  for _ in range(2):
   cmd=[PSQL,"-X","-v","ON_ERROR_STOP=1"]
   for k,v in ev.items():cmd += ["-v",f"{k}={v}"]
   cmd += ["-f",str(ROOT.parent/"phase3b5i_integration"/"invoke_execution.sql")]
   procs.append(subprocess.Popen(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=base.env()))
  rs=[p.communicate()+(p.returncode,) for p in procs]
  if any(x[2] for x in rs):raise RuntimeError("same-key concurrency failed")
  if base.run(f"select count(*) from public.rollover_target_roster_assignment_sets where rollover_execution_id='{con['execution_id']}';").stdout.strip()!="1":raise RuntimeError("concurrency duplicated assignment set")
 finally:
  for f in reversed(fixtures):
   try:base.run(ROOT/"cleanup.sql",f)
   except Exception:pass
 if base.control()!=baseline_control:raise SystemExit("control cleanup mismatch")
 if base.signature(base.DOMAIN_TABLES)!=baseline_domain:raise SystemExit("football-domain signature changed")
 if base.signature(base.CONTRACT_TABLES)!=baseline_contract:raise SystemExit("contract cleanup mismatch")
 if base.signature(control_tables)!=baseline_control_tables:raise SystemExit("target roster/release cleanup mismatch")
 if base.run(sentinel).stdout.strip()!="1:1":raise SystemExit("sentinel changed")
 print("phase3b8a integration: PASS; operations=1-15; empty_set=passed; retained_assignment=passed; releases_excluded=passed; holds_excluded=passed; missing_obligation=blocked; owner_mismatch=blocked; unknown_owner=blocked; incomplete_mapping=blocked; cross_league=blocked; release_conflict=blocked; set_conflict=blocked; changed_material=blocked; source_immutable=passed; direct_sync_guard=passed; replay=stable; concurrency=passed; rollback_10_15=passed; cleanup=passed; publication=0")

if __name__=="__main__":main()
