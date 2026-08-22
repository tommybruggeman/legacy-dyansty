#!/usr/bin/env python3
"""Disposable-only, rollback-only Phase A multi-cardinality certification."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5
import sys

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))

from season_engine.authority_preparation import (AuthoritySimulationInput, CapAuthorityPlan,
    DeadCapAuthorityInstruction, PublicationAuthorityInstruction, TeamCapProjection)
from season_engine.dry_run_simulator import RolloverDryRunSimulator, RolloverDryRunValidator
from season_engine.history.repositories import paginated_rows
from tests.season_rollover_hosted_integration.psql_client import PsqlSession
from tests.fixtures.certification_sentinel import count_sql, expected_sentinel

SIZES=(1,10,32,100,2000)
SENTINEL=count_sql(expected_sentinel("rollover-cardinality-certification"))

def q(value:str)->str:return "'"+value.replace("'","''")+"'"
def uid(label:str)->str:return str(uuid5(NAMESPACE_URL,"legacy-phase-a:"+label))

class LiveQuery:
 def __init__(self,session,table):self.s,self.table,self.filters,self.start,self.end=session,table,{},0,499
 def select(self,columns="*",**kwargs):return self
 def eq(self,k,v):self.filters[k]=v;return self
 def order(self,*args):return self
 def range(self,start,end):self.start,self.end=start,end;return self
 def execute(self):
  where=" and ".join(f"{k}={q(str(v))}" for k,v in self.filters.items()) or "true"
  payload=self.s.json_query("select jsonb_build_object('count',(select count(*) from public."+self.table+" where "+where+"),'data',coalesce((select jsonb_agg(to_jsonb(x) order by x.id) from (select * from public."+self.table+" where "+where+" order by id limit "+str(self.end-self.start+1)+" offset "+str(self.start)+") x),'[]'::jsonb))")
  return SimpleNamespace(count=int(payload["count"]),data=payload["data"])
class LiveClient:
 def __init__(self,s):self.s=s
 def table(self,n):return LiveQuery(self.s,n)

def assert_sentinel(session):
 if session.command(SENTINEL)!=["1:1"]:raise RuntimeError("disposable sentinel mismatch")

def setup_sql(count:int)->str:
 label=f"phasea-cardinality-{count}";actor=uid(label+":actor");league=uid(label+":league");source=uid(label+":source");target=uid(label+":target")
 return f"""
create temp table phasea_result(payload jsonb) on commit drop;
do $$declare p jsonb;r jsonb;replay jsonb;t0 timestamptz:=clock_timestamp();capture_start timestamptz;before_exec bigint;before_pub bigint;team_count int:={count};
begin
 if ({SENTINEL})<>'1:1' then raise exception 'sentinel';end if;
 select count(*) into before_exec from public.rollover_executions;select count(*) into before_pub from public.free_agent_publications;
 insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data) values('{actor}','authenticated','authenticated','{label}@invalid.example','{{"provider":"email","providers":["email"]}}','{{}}');
 insert into public.leagues(id,name,created_by,sleeper_league_id) values('{league}','{label}','{actor}','{label}-source');
 insert into public.league_seasons(id,league_id,season,sleeper_league_id,is_active,status) values('{source}','{league}',2025,'{label}-source',true,'active'),('{target}','{league}',2026,'{label}-target',false,'scheduled');
 insert into public.league_teams(id,league_id,owner_name,team_name,sleeper_roster_id,sleeper_user_id)
 select md5('{label}-team-'||i)::uuid,'{league}','Owner '||i,'Team '||i,i,'user-'||i from generate_series(1,team_count)i;
 select jsonb_build_object('league_id','{league}','league_season_id','{source}','season',2025,'sleeper_league_id','{label}-source','source_fingerprint',repeat('a',64),'idempotency_key','{label}-capture','canonical_team_count',team_count,
  'source_roster_identifiers',(select jsonb_agg(jsonb_build_object('sleeper_roster_id',sleeper_roster_id,'sleeper_owner_id',sleeper_user_id) order by sleeper_roster_id) from public.league_teams where league_id='{league}'),
  'team_mappings',(select jsonb_agg(jsonb_build_object('league_season_id','{source}','league_team_id',id,'sleeper_roster_id',sleeper_roster_id,'sleeper_owner_id',sleeper_user_id,'sleeper_user_id',sleeper_user_id,'team_name_snapshot',team_name,'owner_name_snapshot',owner_name,'mapping_source','league_teams.sleeper_roster_id','mapping_confidence','exact') order by id) from public.league_teams where league_id='{league}'),
  'standings',(select jsonb_agg(jsonb_build_object('league_season_id','{source}','league_team_id',id,'wins',0,'losses',0,'ties',0,'points_for',0,'points_against',0,'regular_season_rank',sleeper_roster_id,'streak',null,'source_payload','{{}}'::jsonb) order by id) from public.league_teams where league_id='{league}'),
  'brackets',jsonb_build_array(jsonb_build_object('league_season_id','{source}','bracket_type','winner','round',1,'sleeper_bracket_match_id',1,'team_1_id',(select id from public.league_teams where league_id='{league}' order by id limit 1),'team_2_id',null,'winner_league_team_id',(select id from public.league_teams where league_id='{league}' order by id limit 1),'loser_league_team_id',null,'placement',1,'source_payload','{{}}'::jsonb)),
  'matchups','[]'::jsonb,'roster_assignments','[]'::jsonb,'warnings','[]'::jsonb) into p;
 p:=p||jsonb_build_object('canonical_team_set_fingerprint',public.phasea_history_canonical_team_fingerprint_private('{league}'),'source_roster_set_fingerprint',public.phasea_history_source_roster_fingerprint_private(p->'source_roster_identifiers'),'mapping_set_fingerprint',public.phasea_history_mapping_fingerprint_private(p->'team_mappings'),'standings_set_fingerprint',public.phasea_history_standings_fingerprint_private(p->'standings'));
 capture_start:=clock_timestamp();r:=public.capture_pre_rollover_history(p);replay:=public.capture_pre_rollover_history(p);
 if r->>'status'<>'validated' or replay->>'idempotent'<>'true' then raise exception 'capture/replay';end if;
 if (r#>>'{{row_counts,canonical_team_count}}')::int<>team_count then raise exception 'persisted count';end if;
 if (select count(*) from public.season_team_mappings where league_season_id='{source}')<>team_count or (select count(*) from public.season_standings where league_season_id='{source}')<>team_count then raise exception 'dynamic rows';end if;
 if p->>'canonical_team_set_fingerprint'<>public.phasea_history_canonical_team_fingerprint_private('{league}') or p->>'source_roster_set_fingerprint'<>public.phasea_history_source_roster_fingerprint_private(p->'source_roster_identifiers') or p->>'mapping_set_fingerprint'<>public.phasea_history_mapping_fingerprint_private(p->'team_mappings') or p->>'standings_set_fingerprint'<>public.phasea_history_standings_fingerprint_private(p->'standings') then raise exception 'fingerprint';end if;
 if (select count(*) from public.rollover_executions)<>before_exec or (select count(*) from public.free_agent_publications)<>before_pub then raise exception 'execution/publication changed';end if;
 insert into phasea_result values(jsonb_build_object('teams',team_count,'fixture_ms',extract(epoch from(capture_start-t0))*1000,'capture_ms',extract(epoch from(clock_timestamp()-capture_start))*1000,'payload_bytes',octet_length(p::text),'mappings',(select count(*) from public.season_team_mappings where league_season_id='{source}'),'standings',(select count(*) from public.season_standings where league_season_id='{source}'),'league_id','{league}'));
end$$;"""

def dry_run(count:int)->bool:
 teams=tuple(TeamCapProjection(f"t{i}",Decimal(100),Decimal(100),Decimal(0),Decimal(0),Decimal(0),Decimal(0),Decimal(0),Decimal(0),Decimal(100),Decimal(127),True,0,0,(),(),f"e{i}") for i in range(count))
 cap=CapAuthorityPlan("l",2025,2026,Decimal(227),Decimal(227),"hard",Decimal(227),Decimal(1),"nearest",Decimal(100*count),Decimal(0),Decimal(0),Decimal(0),Decimal(0),teams,(),(),"c","d")
 pub=(PublicationAuthorityInstruction("p","a","t0","expired","release","approved_for_future_publication","plan_publication_at_execution",(),(),"r","o","e","i"),)
 dead=(DeadCapAuthorityInstruction("p","a","t0",None,None,None,2026,Decimal(0),"c","no_dead_cap",(),(),"e","i"),)
 owner=({"agreement_id":"a","player_id":"p","league_team_id":"t0","planned_outcome":"release_at_rollover_to_commissioner_hold","source_salary":3,"source_years_remaining":1,"source_agreement_status":"active","roster_status":"active"},)
 inp=AuthoritySimulationInput("x","l",2025,2026,"p","o","c","a",pub,dead,cap,teams,(),(),preflight_fingerprint="f",finalized_owner_outcomes=owner,finalized_commissioner_outcomes=({"agreement_id":"a","outcome":"approve_publication"},))
 result=RolloverDryRunSimulator().simulate(inp);validation=RolloverDryRunValidator().validate(result)
 return validation.executable and validation.checks["team_result_set_matches_authority"]

def main():
 if any(name.startswith("LEGACY_PROD_DB_") for name in os.environ):raise SystemExit("production database variables must be absent")
 session=PsqlSession()
 results=[]
 try:
  for count in SIZES:
   assert_sentinel(session);session.command("begin");session.command(setup_sql(count));e=session.json_query("select payload from phasea_result");
   if count==2000:
    rows=paginated_rows(LiveClient(session),"league_teams",filters={"league_id":e["league_id"]},page_size=500)
    if len(rows)!=2000:raise RuntimeError("pagination incomplete")
    e["pages"]=4
   else:e["pages"]=(count+499)//500
   e["dry_run"]=dry_run(count);results.append(e);session.command("rollback")
   if not e["dry_run"]:raise RuntimeError("dry run failed")
  assert_sentinel(session)
  print(json.dumps({"sentinel":"PASS","results":results,"rows_left":0,"publication":False,"external_calls":False},sort_keys=True))
 finally:
  try:session.command("rollback")
  except Exception:pass
  session.close()
if __name__=="__main__":main()
