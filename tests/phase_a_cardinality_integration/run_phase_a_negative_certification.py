#!/usr/bin/env python3
"""Disposable-only, rollback-only Phase A negative certification."""
from __future__ import annotations
import os,sys,json
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL,uuid5

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from season_engine.history.repositories import paginated_rows
from tests.season_rollover_hosted_integration.psql_client import PsqlSession
from tests.fixtures.certification_sentinel import count_sql, expected_sentinel

SENTINEL=count_sql(expected_sentinel("rollover-cardinality-certification"))
def uid(x):return str(uuid5(NAMESPACE_URL,"legacy-phase-a-negative:"+x))
def assert_sentinel(s):
 if s.command(SENTINEL)!=["1:1"]:raise RuntimeError("disposable sentinel mismatch")

def sql_for(n:int)->str:
 label=f"phasea-negative-{n}";actor,league,source,target,foreign=[uid(label+x) for x in (':actor',':league',':source',':target',':foreign')]
 return f"""
create temp table phasea_negative_results(label text primary key,error text) on commit drop;
insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data) values('{actor}','authenticated','authenticated','{label}@invalid.example','{{"provider":"email","providers":["email"]}}','{{}}');
insert into public.leagues(id,name,created_by,sleeper_league_id) values('{league}','{label}','{actor}','{label}'),('{foreign}','{label}-foreign','{actor}','{label}-foreign');
insert into public.league_seasons(id,league_id,season,sleeper_league_id,is_active,status) values('{source}','{league}',2025,'{label}',true,'active'),('{target}','{league}',2026,'{label}-target',false,'scheduled');
insert into public.league_teams(id,league_id,owner_name,team_name,sleeper_roster_id,sleeper_user_id)
 select md5('{label}-team-'||i)::uuid,'{league}','Owner '||i,'Team '||i,i,'user-'||i from generate_series(1,{n})i;
insert into public.league_teams(id,league_id,owner_name,team_name,sleeper_roster_id,sleeper_user_id)
 values(md5('{label}-foreign-team')::uuid,'{foreign}','Foreign','Foreign',999999,'foreign-user');
create temp table phasea_plan(p jsonb) on commit drop;
insert into phasea_plan select jsonb_build_object('league_id','{league}','league_season_id','{source}','season',2025,'sleeper_league_id','{label}','source_fingerprint',repeat('a',64),'idempotency_key','{label}-base','canonical_team_count',{n},
 'source_roster_identifiers',(select jsonb_agg(jsonb_build_object('sleeper_roster_id',sleeper_roster_id,'sleeper_owner_id',sleeper_user_id) order by sleeper_roster_id) from public.league_teams where league_id='{league}'),
 'team_mappings',(select jsonb_agg(jsonb_build_object('league_season_id','{source}','league_team_id',id,'sleeper_roster_id',sleeper_roster_id,'sleeper_owner_id',sleeper_user_id,'sleeper_user_id',sleeper_user_id,'team_name_snapshot',team_name,'owner_name_snapshot',owner_name,'mapping_source','league_teams.sleeper_roster_id','mapping_confidence','exact') order by id) from public.league_teams where league_id='{league}'),
 'standings',(select jsonb_agg(jsonb_build_object('league_season_id','{source}','league_team_id',id,'wins',0,'losses',0,'ties',0,'points_for',0,'points_against',0,'regular_season_rank',sleeper_roster_id,'streak',null,'source_payload','{{}}'::jsonb) order by id) from public.league_teams where league_id='{league}'),
 'brackets',jsonb_build_array(jsonb_build_object('league_season_id','{source}','bracket_type','winner','round',1,'sleeper_bracket_match_id',1,'team_1_id',(select id from public.league_teams where league_id='{league}' order by id limit 1),'team_2_id',null,'winner_league_team_id',(select id from public.league_teams where league_id='{league}' order by id limit 1),'loser_league_team_id',null,'placement',1,'source_payload','{{}}'::jsonb)),'matchups','[]'::jsonb,'roster_assignments','[]'::jsonb,'warnings','[]'::jsonb);
create function pg_temp.phasea_rehash(x jsonb) returns jsonb language plpgsql as $$begin return x||jsonb_build_object('canonical_team_set_fingerprint',public.phasea_history_canonical_team_fingerprint_private('{league}'),'source_roster_set_fingerprint',public.phasea_history_source_roster_fingerprint_private(x->'source_roster_identifiers'),'mapping_set_fingerprint',public.phasea_history_mapping_fingerprint_private(x->'team_mappings'),'standings_set_fingerprint',public.phasea_history_standings_fingerprint_private(x->'standings'));end$$;
create function pg_temp.phasea_expect(label text,x jsonb,expected text default null) returns void language plpgsql as $$declare failed bool:=false;message text;before_h bigint;before_e bigint;before_p bigint;begin
 select count(*) into before_h from public.historical_capture_executions;select count(*) into before_e from public.rollover_executions;select count(*) into before_p from public.free_agent_publications;
 begin perform public.capture_pre_rollover_history(x);exception when others then failed:=true;message:=sqlerrm;end;
 if not failed then raise exception 'negative accepted:%',label;end if;if expected is not null and position(expected in message)=0 then raise exception 'wrong error:%:%',label,message;end if;
 if (select count(*) from public.historical_capture_executions)<>before_h or (select count(*) from public.rollover_executions)<>before_e or (select count(*) from public.free_agent_publications)<>before_p then raise exception 'partial state:%',label;end if;
 if ({SENTINEL})<>'1:1' then raise exception 'sentinel changed:%',label;end if;insert into phasea_negative_results values(label,message);end$$;
"""

def cases_sql(n:int)->str:
 base="(select p from phasea_plan)";foreign="md5('phasea-negative-%d-foreign-team')::uuid"%n
 common=[]
 if n==10:
  common += [
   ("missing_canonical_team",f"jsonb_set({base},'{{team_mappings}}',(select jsonb_agg(value) from jsonb_array_elements({base}->'team_mappings') with ordinality q(value,ord) where ord<{n}))"),
   ("duplicate_canonical_team",f"jsonb_set({base},'{{team_mappings}}',({base}->'team_mappings')||jsonb_build_array({base}->'team_mappings'->0))"),
   ("duplicate_sleeper_roster",f"jsonb_set({base},'{{team_mappings,1,sleeper_roster_id}}',({base}->'team_mappings'->0->'sleeper_roster_id'))"),
   ("ambiguous_canonical_mapping",f"jsonb_set({base},'{{team_mappings}}',({base}->'team_mappings')||jsonb_build_array(jsonb_set({base}->'team_mappings'->0,'{{sleeper_roster_id}}','999998'::jsonb)))"),]
 elif n==32:
  common += [
   ("source_roster_missing",f"jsonb_set({base},'{{source_roster_identifiers}}',(select jsonb_agg(value) from jsonb_array_elements({base}->'source_roster_identifiers') with ordinality q(value,ord) where ord<{n}))"),
   ("foreign_source_roster",f"jsonb_set(jsonb_set({base},'{{source_roster_identifiers,{n-1},sleeper_roster_id}}','999999'::jsonb),'{{source_roster_identifiers,{n-1},sleeper_owner_id}}',to_jsonb('foreign-user'::text))"),
   ("missing_standings_team",f"jsonb_set({base},'{{standings}}',(select jsonb_agg(value) from jsonb_array_elements({base}->'standings') with ordinality q(value,ord) where ord<{n}))"),
   ("duplicate_standings_team",f"jsonb_set({base},'{{standings}}',({base}->'standings')||jsonb_build_array({base}->'standings'->0))"),]
 else:
  replacement=f"jsonb_set(jsonb_set({base}->'team_mappings'->{n-1},'{{league_team_id}}',to_jsonb(({foreign})::text)),'{{sleeper_roster_id}}','999999'::jsonb)"
  common += [
   ("extra_foreign_team",f"jsonb_set({base},'{{team_mappings}}',({base}->'team_mappings')||jsonb_build_array({replacement}))"),
   ("equal_count_substituted_team",f"jsonb_set({base},'{{team_mappings,{n-1}}}',{replacement})"),
   ("cross_league_team_identity",f"jsonb_set({base},'{{team_mappings,0,league_team_id}}',to_jsonb(({foreign})::text))"),]
 sql=[]
 for label,expr in common:sql.append(f"select pg_temp.phasea_expect('{label}',pg_temp.phasea_rehash(({expr})||jsonb_build_object('idempotency_key','negative-{n}-{label}')));")
 if n==100:
  for name in ("canonical_team_set_fingerprint","source_roster_set_fingerprint","mapping_set_fingerprint","standings_set_fingerprint"):
   sql += [f"select pg_temp.phasea_expect('missing_{name}',pg_temp.phasea_rehash({base})-'{name}','phasea_history_fingerprint_missing:{name}');",
           f"select pg_temp.phasea_expect('malformed_{name}',jsonb_set(pg_temp.phasea_rehash({base}),array['{name}'],to_jsonb('ABC'::text)),'phasea_history_fingerprint_malformed:{name}');",
           f"select pg_temp.phasea_expect('mismatched_{name}',jsonb_set(pg_temp.phasea_rehash({base}),array['{name}'],to_jsonb(repeat('0',64))),'phasea_history_fingerprint_mismatch:{name}');"]
 return "\n".join(sql)

class BrokenQuery:
 def __init__(self,client):self.client=client
 def select(self,*a,**k):return self
 def eq(self,*a):return self
 def order(self,*a):return self
 def range(self,*a):return self
 def execute(self):
  self.client.page+=1
  if self.client.mode=="missing_count":return SimpleNamespace(count=None,data=[])
  if self.client.mode=="duplicate":return SimpleNamespace(count=2000,data=[{"id":str(i)} for i in range(500)])
  return SimpleNamespace(count=2000,data=[{"id":str(i)} for i in range(500)] if self.client.page==1 else [])
class BrokenClient:
 def __init__(self,mode):self.mode,self.page=mode,0
 def table(self,*a):return BrokenQuery(self)

def main():
 if any(k.startswith('LEGACY_PROD_DB_') for k in os.environ):raise SystemExit('production variables must be absent')
 s=PsqlSession();summary={}
 try:
  for n in (10,32,100):
   assert_sentinel(s);s.command('begin');s.command(sql_for(n));s.command(cases_sql(n));summary[n]=int(s.command('select count(*) from phasea_negative_results')[-1]);s.command('rollback');assert_sentinel(s)
  pagination=[]
  for label in ("early","missing_count","duplicate"):
   client=BrokenClient(label)
   try:paginated_rows(client,'league_teams',page_size=500);raise RuntimeError(label+' pagination accepted')
   except RuntimeError as exc:
    if 'accepted' in str(exc):raise
    pagination.append(label)
  print(json.dumps({'sentinel':'PASS','negative_cases':summary,'pagination_failures':pagination,'rows_left':0,'publication':False,'external_calls':False},sort_keys=True))
 finally:
  try:s.command('rollback')
  except Exception:pass
  s.close()
if __name__=='__main__':main()
