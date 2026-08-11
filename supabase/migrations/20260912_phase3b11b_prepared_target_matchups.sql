begin;

alter table public.rollover_execution_handler_registry drop constraint rollover_execution_handler_registry_mutation_class_check;
alter table public.rollover_execution_handler_registry add constraint rollover_execution_handler_registry_mutation_class_check check(mutation_class in(
 'read_only','contract_domain','roster_domain','taxi_domain','taxi_eligibility_domain','ir_domain','draft_inventory_domain','rookie_authority_domain','rookie_eligibility_domain','dead_cap_domain','team_cap_domain','free_agent_domain','expiring_contract_domain','standings_domain','matchup_domain'));
insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,result_schema_version,execution_owner,mutation_class,metadata)
values('INITIALIZE_TARGET_MATCHUPS',27,1,'phase3b11b-matchup-input-v1','phase3b11b-matchup-result-v1','execution','matchup_domain',jsonb_build_object('phase','3B.11B','expected_matchup_count',0,'publication',false,'schedule_generation',false,'sleeper_sync',false));

do $$declare d text;sig regprocedure;begin foreach sig in array array[
 'public.guard_contract_write_during_rollover()'::regprocedure,
 'public.guard_season_roster_assignment_insert_phase3b8a()'::regprocedure,
 'public.guard_phase3b9a_draft_inventory()'::regprocedure,
 'public.guard_phase3b9b_rookie_authority()'::regprocedure,
 'public.guard_phase3b9c_rookie_eligibility()'::regprocedure]
 loop select pg_get_functiondef(sig) into d;d:=replace(d,'''phase3b11a-v1'')','''phase3b11a-v1'',''phase3b11b-v1'')');execute d;end loop;end$$;

create table public.prepared_target_matchup_sets(
 id uuid primary key default gen_random_uuid(),rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),target_season_id uuid not null references public.league_seasons(id),
 source_snapshot_hash text not null check(source_snapshot_hash~'^[0-9a-f]{64}$'),prepared_standings_hash text not null check(prepared_standings_hash~'^[0-9a-f]{64}$'),
 expected_matchup_count integer not null check(expected_matchup_count=0),actual_matchup_count integer not null check(actual_matchup_count=0),
 aggregate_matchup_hash text not null check(aggregate_matchup_hash~'^[0-9a-f]{64}$'),deterministic_fingerprint text not null check(deterministic_fingerprint~'^[0-9a-f]{64}$'),
 status text not null check(status='prepared_empty'),published_at timestamptz,schema_version text not null check(schema_version='phase3b11b-matchup-v1'),
 created_at timestamptz not null default clock_timestamp(),unique(league_id,target_season_id),check(published_at is null));

alter table public.prepared_target_matchup_sets enable row level security;
revoke all on public.prepared_target_matchup_sets from public,anon,authenticated;
grant select,insert on public.prepared_target_matchup_sets to service_role;
create policy prepared_target_matchup_set_commissioner_read on public.prepared_target_matchup_sets for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=prepared_target_matchup_sets.league_id and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.guard_phase3b11b_prepared_matchups() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);begin if tg_op in('UPDATE','DELETE') then raise exception 'prepared_target_matchup_set_immutable';end if;if public.has_active_rollover_cutover_lock(lid) and coalesce(current_setting('app.rollover_typed_execution',true),'')<>'phase3b11b-v1' then raise exception 'rollover_cutover_matchup_writes_blocked';end if;return new;end$$;
create trigger prepared_target_matchup_sets_guard before insert or update or delete on public.prepared_target_matchup_sets for each row execute function public.guard_phase3b11b_prepared_matchups();

create or replace function public.write_prepared_target_matchups_phase3b11b_private(p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;target public.league_seasons%rowtype;snap public.rollover_execution_input_snapshots%rowtype;standings public.prepared_target_standings_sets%rowtype;existing public.prepared_target_matchup_sets%rowtype;aggregatehash text;fingerprint text;
begin
 if p_actor is null then perform public.raise_phase3b6c1_failure('matchup_authenticated_actor_required','{}');end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into target from public.league_seasons where league_id=x.league_id and season=x.target_season for share;
 if target.id is null then perform public.raise_phase3b6c1_failure('matchup_target_season_missing','{}');end if;
 if target.league_id<>x.league_id or target.is_active or target.status<>'scheduled' then perform public.raise_phase3b6c1_failure('matchup_target_season_invalid','{}');end if;
 if not exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.execution_plan_id=p_execution_plan_id and l.approval_id=p_approval_id and l.status='active' and l.lock_type='cutover' and l.lock_scope='rollover_global' for update) then perform public.raise_phase3b6c1_failure('matchup_cutover_lock_missing','{}');end if;
 select * into snap from public.rollover_execution_input_snapshots where rollover_execution_id=x.id and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id for share;
 if snap.id is null or snap.league_id<>x.league_id then perform public.raise_phase3b6c1_failure('matchup_source_hash_mismatch','{}');end if;
 select * into standings from public.prepared_target_standings_sets where rollover_execution_id=x.id for share;
 if standings.id is null then perform public.raise_phase3b6c1_failure('matchup_prepared_standings_missing','{}');end if;
 if standings.league_id<>x.league_id or standings.target_season_id<>target.id or standings.source_snapshot_hash<>snap.aggregate_snapshot_fingerprint or standings.status<>'prepared' or standings.published_at is not null or(select count(*) from public.prepared_target_standings where standings_set_id=standings.id)<>standings.expected_team_count then perform public.raise_phase3b6c1_failure('matchup_prepared_standings_incomplete','{}');end if;
 if exists(select 1 from public.season_matchups where league_season_id=target.id) then perform public.raise_phase3b6c1_failure('matchup_target_not_empty','{}');end if;
 perform 1 from public.prepared_target_matchup_sets where rollover_execution_id=x.id for update;
 aggregatehash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b11b-matchup-empty-v1','execution',x.id,'league',x.league_id,'target_season',target.id,'snapshot',snap.aggregate_snapshot_fingerprint,'prepared_standings_hash',standings.aggregate_standings_hash,'matchups','[]'::jsonb));
 fingerprint:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b11b-matchup-set-v1','execution',x.id,'league',x.league_id,'target_season',target.id,'expected',0,'actual',0,'aggregate_hash',aggregatehash,'status','prepared_empty','published',false));
 select * into existing from public.prepared_target_matchup_sets where league_id=x.league_id and target_season_id=target.id for update;
 if found then if existing.rollover_execution_id<>x.id or existing.expected_matchup_count<>0 or existing.actual_matchup_count<>0 or existing.aggregate_matchup_hash<>aggregatehash or existing.deterministic_fingerprint<>fingerprint then perform public.raise_phase3b6c1_failure('matchup_set_conflict','{}');end if;return jsonb_build_object('prepared_matchup_set_id',existing.id,'expected_matchup_count',0,'actual_matchup_count',0,'matchup_rows_written',0,'compatible_replay_count',1,'mutation_count',0,'postcondition_count',9,'aggregate_matchup_hash',aggregatehash,'validation_codes',jsonb_build_array('target_scheduled','prepared_standings','explicit_empty','no_schedule','no_opponents','no_results','no_playoffs','unpublished','cutover_lock'));end if;
 perform set_config('app.rollover_typed_execution','phase3b11b-v1',true);
 insert into public.prepared_target_matchup_sets(rollover_execution_id,league_id,target_season_id,source_snapshot_hash,prepared_standings_hash,expected_matchup_count,actual_matchup_count,aggregate_matchup_hash,deterministic_fingerprint,status,schema_version)
 values(x.id,x.league_id,target.id,snap.aggregate_snapshot_fingerprint,standings.aggregate_standings_hash,0,0,aggregatehash,fingerprint,'prepared_empty','phase3b11b-matchup-v1') returning * into existing;
 return jsonb_build_object('prepared_matchup_set_id',existing.id,'expected_matchup_count',0,'actual_matchup_count',0,'matchup_rows_written',0,'compatible_replay_count',0,'mutation_count',1,'postcondition_count',9,'aggregate_matchup_hash',aggregatehash,'validation_codes',jsonb_build_array('target_scheduled','prepared_standings','explicit_empty','no_schedule','no_opponents','no_results','no_playoffs','unpublished','cutover_lock'));
end$$;

create or replace function public.execute_rollover_typed_handler_phase3b11b_private(p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare code text:=p_operation->>'operation_type';result jsonb;begin if code<>'INITIALIZE_TARGET_MATCHUPS' then return public.execute_rollover_typed_handler_phase3b11a_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);end if;if(p_operation->>'operation_index')::int<>27 then perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}');end if;result:=public.write_prepared_target_matchups_phase3b11b_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);return jsonb_build_object('operation_code',code,'handler_version',1,'result',result||jsonb_build_object('operation_mutation_count',(result->>'mutation_count')::int));end$$;
do $$declare d text;begin select pg_get_functiondef('public.execute_rollover_plan_phase3b11a_private(jsonb,uuid)'::regprocedure) into d;d:=replace(d,'execute_rollover_plan_phase3b11a_private','execute_rollover_plan_phase3b11b_private');d:=replace(d,'execute_rollover_typed_handler_phase3b11a_private','execute_rollover_typed_handler_phase3b11b_private');d:=replace(d,'''INITIALIZE_TARGET_STANDINGS'')','''INITIALIZE_TARGET_STANDINGS'',''INITIALIZE_TARGET_MATCHUPS'')');d:=replace(d,'Phase 3B.11A','Phase 3B.11B');d:=replace(d,'phase3b11a-v1','phase3b11b-v1');execute d;end$$;
create or replace function public.execute_rollover_plan_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;begin if p_request?'actor_user_id' or p_request?'executed_by' or p_request?'matchups' or p_request?'weeks' or p_request?'opponents' or p_request?'schedule' or p_request?'scores' then raise exception 'actor or matchup material spoofing forbidden';end if;select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);return public.execute_rollover_plan_phase3b11b_private(p_request,actor);end$$;
revoke all on function public.write_prepared_target_matchups_phase3b11b_private(uuid,uuid,uuid,uuid),public.execute_rollover_typed_handler_phase3b11b_private(jsonb,uuid,uuid,uuid,uuid),public.execute_rollover_plan_phase3b11b_private(jsonb,uuid),public.guard_phase3b11b_prepared_matchups() from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;
commit;
