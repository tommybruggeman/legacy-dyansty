begin;

alter table public.rollover_execution_handler_registry
 drop constraint rollover_execution_handler_registry_mutation_class_check;
alter table public.rollover_execution_handler_registry
 add constraint rollover_execution_handler_registry_mutation_class_check
 check(mutation_class in('read_only','contract_domain','roster_domain'));

insert into public.rollover_execution_handler_registry(
 operation_code,operation_order,handler_version,input_schema_version,result_schema_version,
 execution_owner,mutation_class,metadata
) values (
 'RECONCILE_TARGET_ROSTER_ASSIGNMENTS',15,1,'phase3b8a-target-roster-input-v1',
 'phase3b8a-target-roster-result-v1','execution','roster_domain',
 jsonb_build_object('phase','3B.8A','publication',false)
);

create table public.rollover_target_roster_assignment_sets(
 id uuid primary key default gen_random_uuid(),
 rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),
 target_league_season_id uuid not null references public.league_seasons(id),
 source_snapshot_id uuid not null references public.rollover_execution_input_snapshots(id),
 source_snapshot_hash text not null check(source_snapshot_hash~'^[0-9a-f]{64}$'),
 mapping_fingerprint text not null check(mapping_fingerprint~'^[0-9a-f]{64}$'),
 expected_row_count integer not null check(expected_row_count>=0),
 aggregate_assignment_set_hash text not null check(aggregate_assignment_set_hash~'^[0-9a-f]{64}$'),
 status text not null check(status='complete_unpublished'),
 created_by uuid not null,
 created_at timestamptz not null default clock_timestamp(),
 unique(league_id,target_league_season_id)
);

alter table public.season_roster_assignments
 add column assignment_set_id uuid references public.rollover_target_roster_assignment_sets(id),
 add column contract_agreement_id uuid references public.contract_agreements(id),
 add column target_contract_season_id uuid references public.contract_seasons(id),
 add column source_assignment_id uuid references public.season_roster_assignments(id),
 add column roster_status text,
 add column provenance jsonb,
 add column deterministic_row_hash text;

alter table public.season_roster_assignments add constraint season_roster_target_metadata_complete check(
 (assignment_set_id is null and contract_agreement_id is null and target_contract_season_id is null
  and source_assignment_id is null and roster_status is null and provenance is null and deterministic_row_hash is null)
 or
 (assignment_set_id is not null and contract_agreement_id is not null and target_contract_season_id is not null
  and source_assignment_id is not null and roster_status='pending_unpublished'
  and jsonb_typeof(provenance)='object' and deterministic_row_hash~'^[0-9a-f]{64}$')
);
revoke insert,update,delete,truncate,references,trigger on public.season_roster_assignments
 from public,anon,authenticated;
create unique index season_roster_assignment_set_player_uidx
 on public.season_roster_assignments(assignment_set_id,sleeper_player_id)
 where assignment_set_id is not null;
create unique index season_roster_assignment_set_team_player_uidx
 on public.season_roster_assignments(assignment_set_id,league_team_id,sleeper_player_id)
 where assignment_set_id is not null;

create or replace function public.reject_phase3b8a_assignment_set_mutation()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin raise exception 'Phase 3B.8A target roster assignment-set evidence is immutable';end$$;
create trigger rollover_target_roster_assignment_sets_immutable before update or delete
 on public.rollover_target_roster_assignment_sets for each row
 execute function public.reject_phase3b8a_assignment_set_mutation();

alter table public.rollover_target_roster_assignment_sets enable row level security;
revoke all on public.rollover_target_roster_assignment_sets from public,anon,authenticated;
grant select,insert on public.rollover_target_roster_assignment_sets to service_role;
create policy phase3b8a_target_roster_set_read on public.rollover_target_roster_assignment_sets
 for select to authenticated using(exists(
  select 1 from public.league_memberships m where m.league_id=rollover_target_roster_assignment_sets.league_id
   and m.user_id=auth.uid() and m.role='commissioner'));

-- Target rows are append-only, league-scoped, and may only be inserted by the
-- transaction-local Phase 3B.8A writer while a matching cutover lock is active.
create or replace function public.guard_season_roster_assignment_insert_phase3b8a()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare season_row public.league_seasons%rowtype;team_row public.league_teams%rowtype;
begin
 select * into season_row from public.league_seasons where id=new.league_season_id;
 select * into team_row from public.league_teams where id=new.league_team_id;
 if season_row.id is null or team_row.id is null or season_row.league_id<>team_row.league_id then
  raise exception 'target_roster_team_cross_league';
 end if;
 if public.has_active_rollover_cutover_lock(season_row.league_id)
    and current_setting('app.rollover_typed_execution',true)<>'phase3b8a-v1' then
  raise exception 'rollover_cutover_roster_writes_blocked';
 end if;
 return new;
end$$;
create trigger season_roster_assignments_cutover_insert_guard before insert
 on public.season_roster_assignments for each row
 execute function public.guard_season_roster_assignment_insert_phase3b8a();

-- Preserve the certified contract guard while permitting the upgraded inner
-- transaction marker. External and legacy writers remain blocked.
create or replace function public.guard_contract_write_during_rollover()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);
begin
 if coalesce(current_setting('app.rollover_typed_execution',true),'') not in('phase3b7c-v1','phase3b8a-v1') then
  perform public.assert_no_active_rollover_cutover_lock(lid);
 end if;
 return case when tg_op='DELETE' then old else new end;
end$$;

-- Legacy roster tables are not canonical, but destructive name-based sync must
-- still fail closed during any active cutover. JSON extraction keeps this guard
-- compatible with legacy table shapes that do not carry league_id.
create or replace function public.guard_legacy_roster_write_during_rollover_phase3b8a()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare payload jsonb:=case when tg_op='DELETE' then to_jsonb(old) else to_jsonb(new) end;lid uuid;
begin
 begin lid:=nullif(payload->>'league_id','')::uuid;exception when others then lid:=null;end;
 if (lid is not null and public.has_active_rollover_cutover_lock(lid))
    or (lid is null and exists(select 1 from public.rollover_execution_locks
      where lock_type='cutover' and lock_scope='rollover_global' and status='active')) then
  raise exception 'rollover_cutover_roster_writes_blocked';
 end if;
 return case when tg_op='DELETE' then old else new end;
end$$;
do $$declare t text;begin
 foreach t in array array['roster','rosters_current','team_roster_map','team_roster_state'] loop
  if to_regclass('public.'||t) is not null then
   execute format('drop trigger if exists %I on public.%I',t||'_rollover_cutover_guard',t);
   execute format('create trigger %I before insert or update or delete on public.%I for each row execute function public.guard_legacy_roster_write_during_rollover_phase3b8a()',t||'_rollover_cutover_guard',t);
  end if;
 end loop;
end$$;

create or replace function public.write_target_roster_assignment_set_phase3b8a_private(
 p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;s public.rollover_execution_input_snapshots%rowtype;
 target_season public.league_seasons%rowtype;existing public.rollover_target_roster_assignment_sets%rowtype;
 set_id uuid:=gen_random_uuid();r record;row_fp text;aggregate_fp text;rows_material jsonb:='[]'::jsonb;
 candidates int;continuing int;ordinary int;held int;intentional int:=0;missing_count int:=0;
 duplicate_count int:=0;cross_count int:=0;written int:=0;mapping_count int;source_count int;
 validation_codes jsonb:=jsonb_build_array('snapshot_identity','mapping_fingerprint','canonical_membership_team',
  'target_contract_obligation','source_assignment','release_exclusion','hold_exclusion','population_complete','cutover_lock');
begin
 if p_actor is null then perform public.raise_phase3b6c1_failure('authenticated_actor_required','{}');end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 if x.id is null then perform public.raise_phase3b6c1_failure('target_roster_player_missing','{}');end if;
 if not exists(select 1 from public.rollover_execution_locks where rollover_execution_id=x.id
   and approval_id=p_approval_id and execution_plan_id=p_execution_plan_id and league_id=x.league_id
   and status='active' and lock_type='cutover' and lock_scope='rollover_global' for update) then
  perform public.raise_phase3b6c1_failure('target_roster_cutover_lock_missing','{}');
 end if;
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=x.id
  and approval_id=p_approval_id and execution_plan_id=p_execution_plan_id for share;
 if s.id is null or s.source_plan_fingerprint<>(select plan_fingerprint from public.rollover_execution_plans where id=p_execution_plan_id)
    or s.mapping_fingerprint is distinct from (select op->>'evidence_fingerprint'
      from public.rollover_execution_plans p cross join lateral jsonb_array_elements(p.ordered_operations) op
      where p.id=p_execution_plan_id and op->>'operation_type'='VERIFY_TEAM_ROSTER_MAPPINGS') then
  perform public.raise_phase3b6c1_failure('target_roster_hash_mismatch','{}');
 end if;
 select * into target_season from public.league_seasons where league_id=x.league_id and season=x.target_season for share;
 if target_season.id is null then perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
 select count(*) into mapping_count from public.season_team_mappings m join public.league_teams t on t.id=m.league_team_id
  where m.league_season_id=target_season.id and t.league_id=x.league_id;
 if mapping_count<>s.frozen_team_count or exists(select 1 from public.season_team_mappings m
   left join public.league_teams t on t.id=m.league_team_id
   where m.league_season_id=target_season.id and (t.id is null or t.league_id<>x.league_id)) then
  perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');
 end if;
 select count(distinct sleeper_player_id) into source_count from public.season_roster_assignments
  where league_season_id=(select id from public.league_seasons where league_id=x.league_id and season=x.source_season);
 if source_count<>(select count(*) from public.season_roster_assignments
   where league_season_id=(select id from public.league_seasons where league_id=x.league_id and season=x.source_season)) then
  perform public.raise_phase3b6c1_failure('target_roster_duplicate_player','{}');
 end if;
 select count(distinct player_id) into candidates from (
  select a.player_id from public.contract_agreements a where a.league_id=x.league_id
   and exists(select 1 from public.contract_seasons cs where cs.contract_id=a.id and cs.league_season_id=target_season.id)
  union select player_id from public.rollover_contract_releases where rollover_execution_id=x.id
  union select player_id from public.rollover_commissioner_holds where rollover_execution_id=x.id
 ) q;
 select count(*) into ordinary from public.rollover_contract_releases where rollover_execution_id=x.id and release_disposition='ordinary_release';
 select count(*) into held from public.rollover_commissioner_holds where rollover_execution_id=x.id and hold_status='active';
 select count(*) into continuing from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
  where a.league_id=x.league_id and a.status='active' and cs.league_season_id=target_season.id and cs.obligation_status='active';
 if candidates<>continuing+ordinary+held+intentional then
  perform public.raise_phase3b6c1_failure('target_roster_population_incomplete','{}');
 end if;
 for r in
  select a.id agreement_id,a.league_team_id,a.player_id,cs.id contract_season_id,
   src.id source_assignment_id,src.roster_designation,pu.player_name
  from public.contract_agreements a
  join public.contract_seasons cs on cs.contract_id=a.id and cs.league_season_id=target_season.id and cs.obligation_status='active'
  left join public.player_universe pu on pu.sleeper_id=a.player_id
  left join public.season_roster_assignments src on src.league_season_id=(select id from public.league_seasons where league_id=x.league_id and season=x.source_season) and src.sleeper_player_id=a.player_id
  where a.league_id=x.league_id and a.status='active'
  order by a.player_id,a.league_team_id,a.id
 loop
  if r.player_name is null then perform public.raise_phase3b6c1_failure('target_roster_player_missing','{}');end if;
  if not exists(select 1 from public.league_teams t where t.id=r.league_team_id and t.league_id=x.league_id) then
   perform public.raise_phase3b6c1_failure('target_roster_team_cross_league','{}');end if;
  if not exists(select 1 from public.league_memberships m where m.league_id=x.league_id and m.league_team_id=r.league_team_id) then
   perform public.raise_phase3b6c1_failure('target_roster_unknown_owner','{}');end if;
  if not exists(select 1 from public.season_team_mappings m where m.league_season_id=target_season.id and m.league_team_id=r.league_team_id) then
   perform public.raise_phase3b6c1_failure('target_roster_team_missing','{}');end if;
  if r.source_assignment_id is null then perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
  if (select league_team_id from public.season_roster_assignments where id=r.source_assignment_id)<>r.league_team_id then
   perform public.raise_phase3b6c1_failure('target_roster_owner_mismatch','{}');end if;
  if exists(select 1 from public.rollover_contract_releases where rollover_execution_id=x.id and player_id=r.player_id) then
   perform public.raise_phase3b6c1_failure('target_roster_release_conflict','{}');end if;
  if exists(select 1 from public.rollover_commissioner_holds where rollover_execution_id=x.id and player_id=r.player_id and hold_status='active') then
   perform public.raise_phase3b6c1_failure('target_roster_hold_conflict','{}');end if;
  row_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8a-target-roster-row-v1',
   'execution',x.id,'league',x.league_id,'target_season',target_season.id,'team',r.league_team_id,
   'player',r.player_id,'agreement',r.agreement_id,'contract_season',r.contract_season_id,
   'source_assignment',r.source_assignment_id,'roster_status','pending_unpublished'));
  rows_material:=rows_material||jsonb_build_array(jsonb_build_object('player_id',r.player_id,'team_id',r.league_team_id,'row_hash',row_fp));
 end loop;
 if jsonb_array_length(rows_material)<>continuing then perform public.raise_phase3b6c1_failure('target_roster_population_incomplete','{}');end if;
 aggregate_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8a-target-roster-set-v1',
  'execution',x.id,'league',x.league_id,'target_season',target_season.id,'source_snapshot_hash',s.aggregate_snapshot_fingerprint,
  'mapping_fingerprint',s.mapping_fingerprint,'rows',rows_material));
 select * into existing from public.rollover_target_roster_assignment_sets where league_id=x.league_id and target_league_season_id=target_season.id for update;
 if found then
  if existing.rollover_execution_id<>x.id or existing.source_snapshot_hash<>s.aggregate_snapshot_fingerprint
    or existing.aggregate_assignment_set_hash<>aggregate_fp or existing.expected_row_count<>continuing then
   perform public.raise_phase3b6c1_failure('target_roster_set_conflict','{}');
  end if;
  if (select count(*) from public.season_roster_assignments where assignment_set_id=existing.id)<>continuing then
   perform public.raise_phase3b6c1_failure('target_roster_hash_mismatch','{}');end if;
  return jsonb_build_object('candidate_player_count',candidates,'continuing_contract_count',continuing,
   'target_assigned_player_count',continuing,'ordinary_release_exclusion_count',ordinary,
   'commissioner_hold_exclusion_count',held,'intentional_exclusion_count',intentional,'missing_assignment_count',0,
   'duplicate_assignment_count',0,'cross_league_conflict_count',0,'assignment_rows_written',0,
   'compatible_replay_count',1,'aggregate_assignment_set_hash',aggregate_fp,'assignment_set_id',existing.id,
   'mutation_count',0,'postcondition_count',9,'validation_codes',validation_codes,'publication_performed',false);
 end if;
 if exists(select 1 from public.season_roster_assignments where league_season_id=target_season.id) then
  perform public.raise_phase3b6c1_failure('target_roster_set_conflict','{}');end if;
 insert into public.rollover_target_roster_assignment_sets(id,rollover_execution_id,league_id,target_league_season_id,
  source_snapshot_id,source_snapshot_hash,mapping_fingerprint,expected_row_count,aggregate_assignment_set_hash,status,created_by)
 values(set_id,x.id,x.league_id,target_season.id,s.id,s.aggregate_snapshot_fingerprint,s.mapping_fingerprint,
  continuing,aggregate_fp,'complete_unpublished',p_actor);
 perform set_config('app.rollover_typed_execution','phase3b8a-v1',true);
 for r in
  select a.id agreement_id,a.league_team_id,a.player_id,cs.id contract_season_id,
   src.id source_assignment_id,src.roster_designation,pu.player_name
  from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
   and cs.league_season_id=target_season.id and cs.obligation_status='active'
  join public.player_universe pu on pu.sleeper_id=a.player_id
  join public.season_roster_assignments src on src.league_season_id=(select id from public.league_seasons where league_id=x.league_id and season=x.source_season) and src.sleeper_player_id=a.player_id
  where a.league_id=x.league_id and a.status='active' order by a.player_id,a.league_team_id,a.id
 loop
  row_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8a-target-roster-row-v1',
   'execution',x.id,'league',x.league_id,'target_season',target_season.id,'team',r.league_team_id,
   'player',r.player_id,'agreement',r.agreement_id,'contract_season',r.contract_season_id,
   'source_assignment',r.source_assignment_id,'roster_status','pending_unpublished'));
  insert into public.season_roster_assignments(league_season_id,league_team_id,canonical_player_id,sleeper_player_id,
   player_name_snapshot,roster_designation,source,finalized_at,assignment_set_id,contract_agreement_id,
   target_contract_season_id,source_assignment_id,roster_status,provenance,deterministic_row_hash)
  values(target_season.id,r.league_team_id,r.player_id,r.player_id,r.player_name,'other','phase3b8a',clock_timestamp(),
   set_id,r.agreement_id,r.contract_season_id,r.source_assignment_id,'pending_unpublished',
   jsonb_build_object('rollover_execution_id',x.id,'source_roster_designation',r.roster_designation,
    'authorization_authority','league_memberships.league_team_id','sleeper_authoritative',false),row_fp);
  written:=written+1;
 end loop;
 if written<>continuing or (select count(*) from public.season_roster_assignments where assignment_set_id=set_id)<>continuing then
  perform public.raise_phase3b6c1_failure('target_roster_population_incomplete','{}');end if;
 return jsonb_build_object('candidate_player_count',candidates,'continuing_contract_count',continuing,
  'target_assigned_player_count',written,'ordinary_release_exclusion_count',ordinary,
  'commissioner_hold_exclusion_count',held,'intentional_exclusion_count',intentional,'missing_assignment_count',missing_count,
  'duplicate_assignment_count',duplicate_count,'cross_league_conflict_count',cross_count,'assignment_rows_written',written,
  'compatible_replay_count',0,'aggregate_assignment_set_hash',aggregate_fp,'assignment_set_id',set_id,
  'mutation_count',written+1,'postcondition_count',9,'validation_codes',validation_codes,'publication_performed',false);
end$$;

create or replace function public.execute_rollover_typed_handler_phase3b8a_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare code text:=p_operation->>'operation_type';result jsonb;
begin
 if code<>'RECONCILE_TARGET_ROSTER_ASSIGNMENTS' then
  return public.execute_rollover_typed_handler_phase3b7c_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 end if;
 if (p_operation->>'operation_index')::integer<>15 or not exists(select 1 from public.rollover_execution_handler_registry
   where operation_code=code and operation_order=15 and handler_version=1) then
  perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}');end if;
 result:=public.write_target_roster_assignment_set_phase3b8a_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 return jsonb_build_object('operation_code',code,'handler_version',1,'result',result||jsonb_build_object(
  'operation_mutation_count',coalesce((result->>'mutation_count')::integer,0),
  'deterministic_result_hash',result->>'aggregate_assignment_set_hash'));
end$$;

-- Extend only the certified dispatcher boundary. The inner exception block
-- preserves atomic rollback of operations 10-15 and durable outer diagnostics.
create or replace function public.execute_rollover_plan_phase3b8a_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;domain_mutations integer:=0;failed_op jsonb;failure_sqlstate text;failure_message text;
 failure_detail text;failure_hint text;failure_context text;result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null or nullif(p_request->>'approval_id','') is null
  or nullif(p_request->>'execution_plan_id','') is null or nullif(p_request->>'expected_plan_fingerprint','') is null
  or nullif(p_request->>'expected_execution_status','') is null or nullif(p_request->>'expected_approval_status','') is null then raise exception 'complete execution assertions required';end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6c','execution_id',x.id,'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);end if;
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::integer or p.plan_version<>a.execution_plan_version
  or p.plan_status<>'approved_for_execution' or not p.approved_for_execution or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint'
  or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then raise exception 'stale or invalid approved execution plan';end if;
 select * into l from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=a.id and execution_plan_id=p.id
  and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;
 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,execution_plan_version,
  plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',p.operation_count,run_started,p_actor,
  jsonb_build_object('engine_version','phase3b8a-v1','typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  perform set_config('app.rollover_typed_execution','phase3b8a-v1',true);
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then raise exception 'ordered operation sequence mismatch';end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE',
    'VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED','FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE',
    'RESOLVE_OWNER_OPTION_OUTCOMES','VALIDATE_COMMISSIONER_REVIEW_OUTCOMES','ADVANCE_CONTRACT_SEASON_OBLIGATIONS',
    'EXERCISE_VALID_OWNER_OPTIONS','DECLINE_OR_EXPIRE_OWNER_OPTIONS','RELEASE_EXPIRED_CONTRACTS','APPLY_COMMISSIONER_HOLDS',
    'RECONCILE_TARGET_ROSTER_ASSIGNMENTS') then
    handler_result:=public.execute_rollover_typed_handler_phase3b8a_private(op,x.id,p.id,a.id,p_actor);
    domain_mutations:=domain_mutations+coalesce((handler_result#>>'{result,operation_mutation_count}')::integer,0);
   else raise exception 'unsupported Phase 3B.8A operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,
    operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint','completed',op_started,
    clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),handler_result,
    jsonb_build_object('domain_mutations',coalesce((handler_result#>>'{result,operation_mutation_count}')::integer,0),'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
   completed:=completed+1;
  end loop;
 exception when others then get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,
  failure_detail=pg_exception_detail,failure_hint=pg_exception_hint,failure_context=pg_exception_context;end;
 if failure_message is not null then
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,operation_type,
   operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),
   coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),'failed',
   coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),
   jsonb_build_object('failure_code',failure_message,'sqlstate',failure_sqlstate,'detail',left(coalesce(failure_detail,''),4096),
    'hint',left(coalesce(failure_hint,''),1024),'context',left(coalesce(failure_context,''),4096),'rolled_back_operations',completed,
    'domain_mutations_committed',0,'live_external_call_performed',false),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
   'operations_attempted',attempted,'operations_completed',0,'success',false,'failure_code',failure_message,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,operations_completed=0,
   finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;
 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
  'operations_attempted',attempted,'operations_completed',completed,'success',true,
  'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',domain_mutations,'live_external_call_performed',false,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,operations_completed=completed,
  finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end$$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 return public.execute_rollover_plan_phase3b8a_private(p_request,actor);
end$$;

revoke all on function public.write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid),
 public.execute_rollover_typed_handler_phase3b8a_private(jsonb,uuid,uuid,uuid,uuid),
 public.execute_rollover_plan_phase3b8a_private(jsonb,uuid) from public,anon,authenticated,service_role;
revoke all on function public.guard_season_roster_assignment_insert_phase3b8a(),
 public.guard_legacy_roster_write_during_rollover_phase3b8a(),
 public.reject_phase3b8a_assignment_set_mutation() from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

commit;
