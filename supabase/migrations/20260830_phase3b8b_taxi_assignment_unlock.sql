begin;

alter table public.rollover_execution_handler_registry drop constraint rollover_execution_handler_registry_mutation_class_check;
alter table public.rollover_execution_handler_registry add constraint rollover_execution_handler_registry_mutation_class_check
 check(mutation_class in('read_only','contract_domain','roster_domain','taxi_domain'));
insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,
 result_schema_version,execution_owner,mutation_class,metadata) values(
 'UNLOCK_TAXI_ASSIGNMENTS',16,1,'phase3b8b-taxi-unlock-input-v1','phase3b8b-taxi-unlock-result-v1',
 'execution','taxi_domain',jsonb_build_object('phase','3B.8B','eligibility_enforced',false,'publication',false));

create table public.rollover_taxi_unlock_sets(
 id uuid primary key default gen_random_uuid(),rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),source_league_season_id uuid not null references public.league_seasons(id),
 target_league_season_id uuid not null references public.league_seasons(id),target_assignment_set_id uuid not null references public.rollover_target_roster_assignment_sets(id),
 source_snapshot_id uuid not null references public.rollover_execution_input_snapshots(id),source_snapshot_hash text not null check(source_snapshot_hash~'^[0-9a-f]{64}$'),
 source_taxi_count integer not null check(source_taxi_count>=0),target_unlocked_count integer not null check(target_unlocked_count>=0),
 ordinary_release_count integer not null check(ordinary_release_count>=0),commissioner_hold_count integer not null check(commissioner_hold_count>=0),
 aggregate_unlock_hash text not null check(aggregate_unlock_hash~'^[0-9a-f]{64}$'),status text not null check(status='complete_unpublished'),
 created_by uuid not null,created_at timestamptz not null default clock_timestamp(),unique(league_id,target_league_season_id),
 check(source_taxi_count=target_unlocked_count+ordinary_release_count+commissioner_hold_count));
create table public.rollover_taxi_unlock_dispositions(
 id uuid primary key default gen_random_uuid(),taxi_unlock_set_id uuid not null references public.rollover_taxi_unlock_sets(id),
 rollover_execution_id uuid not null references public.rollover_executions(id),league_id uuid not null references public.leagues(id),
 player_id text not null references public.player_universe(sleeper_id),source_assignment_id uuid not null references public.season_roster_assignments(id),
 target_assignment_id uuid references public.season_roster_assignments(id),release_id uuid references public.rollover_contract_releases(id),
 commissioner_hold_id uuid references public.rollover_commissioner_holds(id),disposition text not null check(disposition in('unlocked_to_active_pool','ordinary_release','commissioner_hold')),
 source_roster_designation text not null check(source_roster_designation='taxi'),target_roster_designation text,
 provenance jsonb not null check(jsonb_typeof(provenance)='object'),deterministic_row_hash text not null check(deterministic_row_hash~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default clock_timestamp(),unique(taxi_unlock_set_id,player_id),unique(taxi_unlock_set_id,source_assignment_id),
 check((disposition='unlocked_to_active_pool' and target_assignment_id is not null and release_id is null and commissioner_hold_id is null and target_roster_designation<>'taxi')
  or(disposition='ordinary_release' and target_assignment_id is null and release_id is not null and commissioner_hold_id is null and target_roster_designation is null)
  or(disposition='commissioner_hold' and target_assignment_id is null and release_id is not null and commissioner_hold_id is not null and target_roster_designation is null)));

create or replace function public.reject_phase3b8b_taxi_unlock_mutation() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin raise exception 'Phase 3B.8B taxi unlock evidence is immutable';end$$;
create trigger rollover_taxi_unlock_sets_immutable before update or delete on public.rollover_taxi_unlock_sets for each row execute function public.reject_phase3b8b_taxi_unlock_mutation();
create trigger rollover_taxi_unlock_dispositions_immutable before update or delete on public.rollover_taxi_unlock_dispositions for each row execute function public.reject_phase3b8b_taxi_unlock_mutation();
alter table public.rollover_taxi_unlock_sets enable row level security;alter table public.rollover_taxi_unlock_dispositions enable row level security;
revoke all on public.rollover_taxi_unlock_sets,public.rollover_taxi_unlock_dispositions from public,anon,authenticated;
grant select,insert on public.rollover_taxi_unlock_sets,public.rollover_taxi_unlock_dispositions to service_role;
create policy phase3b8b_taxi_set_read on public.rollover_taxi_unlock_sets for select to authenticated using(exists(
 select 1 from public.league_memberships m where m.league_id=rollover_taxi_unlock_sets.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy phase3b8b_taxi_disposition_read on public.rollover_taxi_unlock_dispositions for select to authenticated using(exists(
 select 1 from public.league_memberships m where m.league_id=rollover_taxi_unlock_dispositions.league_id and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.guard_contract_write_during_rollover() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);begin
 if coalesce(current_setting('app.rollover_typed_execution',true),'') not in('phase3b7c-v1','phase3b8a-v1','phase3b8b-v1') then perform public.assert_no_active_rollover_cutover_lock(lid);end if;
 return case when tg_op='DELETE' then old else new end;end$$;
create or replace function public.guard_season_roster_assignment_insert_phase3b8a() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare season_row public.league_seasons%rowtype;team_row public.league_teams%rowtype;begin
 select * into season_row from public.league_seasons where id=new.league_season_id;select * into team_row from public.league_teams where id=new.league_team_id;
 if season_row.id is null or team_row.id is null or season_row.league_id<>team_row.league_id then raise exception 'target_roster_team_cross_league';end if;
 if public.has_active_rollover_cutover_lock(season_row.league_id) and coalesce(current_setting('app.rollover_typed_execution',true),'') not in('phase3b8a-v1','phase3b8b-v1') then raise exception 'rollover_cutover_roster_writes_blocked';end if;return new;end$$;

create or replace function public.write_taxi_unlock_set_phase3b8b_private(p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;s public.rollover_execution_input_snapshots%rowtype;aset public.rollover_target_roster_assignment_sets%rowtype;
 existing public.rollover_taxi_unlock_sets%rowtype;source_id uuid;target_id uuid;set_id uuid:=gen_random_uuid();r record;disp text;target_row uuid;rel uuid;hold uuid;
 row_fp text;aggregate_fp text;material jsonb:='[]';taxi_count int:=0;unlocked int:=0;ordinary int:=0;held int:=0;written int:=0;
begin
 if p_actor is null then perform public.raise_phase3b6c1_failure('taxi_unlock_authenticated_actor_required','{}');end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 if x.id is null then perform public.raise_phase3b6c1_failure('taxi_unlock_execution_missing','{}');end if;
 if not exists(select 1 from public.rollover_execution_locks where rollover_execution_id=x.id and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id and status='active' and lock_type='cutover' and lock_scope='rollover_global' for update) then perform public.raise_phase3b6c1_failure('taxi_unlock_cutover_lock_missing','{}');end if;
 select id into source_id from public.league_seasons where league_id=x.league_id and season=x.source_season;select id into target_id from public.league_seasons where league_id=x.league_id and season=x.target_season;
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=x.id and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id for share;
 select * into aset from public.rollover_target_roster_assignment_sets where rollover_execution_id=x.id and target_league_season_id=target_id for share;
 if s.id is null or aset.id is null or aset.source_snapshot_id<>s.id or aset.source_snapshot_hash<>s.aggregate_snapshot_fingerprint or aset.status<>'complete_unpublished' then perform public.raise_phase3b6c1_failure('taxi_unlock_target_set_missing','{}');end if;
 perform 1 from public.season_roster_assignments where league_season_id=source_id and roster_designation='taxi' order by sleeper_player_id,id for share;
 perform 1 from public.season_roster_assignments where assignment_set_id=aset.id order by sleeper_player_id,id for share;
 perform 1 from public.rollover_contract_releases where rollover_execution_id=x.id order by player_id,id for share;
 perform 1 from public.rollover_commissioner_holds where rollover_execution_id=x.id order by player_id,id for share;
 for r in select * from public.season_roster_assignments where league_season_id=source_id and roster_designation='taxi' order by sleeper_player_id,id loop
  taxi_count:=taxi_count+1;target_row:=null;rel:=null;hold:=null;disp:=null;
  select id into target_row from public.season_roster_assignments where assignment_set_id=aset.id and sleeper_player_id=r.sleeper_player_id;
  select id into rel from public.rollover_contract_releases where rollover_execution_id=x.id and player_id=r.sleeper_player_id;
  select id into hold from public.rollover_commissioner_holds where rollover_execution_id=x.id and player_id=r.sleeper_player_id and hold_status='active';
  if target_row is not null and rel is null and hold is null then disp:='unlocked_to_active_pool';unlocked:=unlocked+1;
  elsif target_row is null and rel is not null and hold is null and exists(select 1 from public.rollover_contract_releases where id=rel and release_disposition='ordinary_release') then disp:='ordinary_release';ordinary:=ordinary+1;
  elsif target_row is null and rel is not null and hold is not null then disp:='commissioner_hold';held:=held+1;
  else perform public.raise_phase3b6c1_failure('taxi_unlock_population_conflict','{}');end if;
  if target_row is not null and exists(select 1 from public.season_roster_assignments where id=target_row and roster_designation in('taxi','ir')) then perform public.raise_phase3b6c1_failure('taxi_unlock_target_designation_conflict','{}');end if;
  row_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8b-taxi-unlock-row-v1','execution',x.id,'player',r.sleeper_player_id,'source_assignment',r.id,'target_assignment',target_row,'release',rel,'hold',hold,'disposition',disp));
  material:=material||jsonb_build_array(jsonb_build_object('player_id',r.sleeper_player_id,'row_hash',row_fp));
 end loop;
 aggregate_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8b-taxi-unlock-set-v1','execution',x.id,'assignment_set',aset.id,'snapshot_hash',s.aggregate_snapshot_fingerprint,'rows',material));
 select * into existing from public.rollover_taxi_unlock_sets where league_id=x.league_id and target_league_season_id=target_id for update;
 if found then
  if existing.rollover_execution_id<>x.id or existing.aggregate_unlock_hash<>aggregate_fp or existing.source_taxi_count<>taxi_count then perform public.raise_phase3b6c1_failure('taxi_unlock_set_conflict','{}');end if;
  return jsonb_build_object('source_taxi_count',taxi_count,'target_unlocked_count',unlocked,'ordinary_release_count',ordinary,'commissioner_hold_count',held,'ir_rows_mutated',0,'taxi_eligibility_enforced',false,'assignment_rows_mutated',0,'compatible_replay_count',1,'taxi_unlock_set_id',existing.id,'aggregate_unlock_hash',aggregate_fp,'mutation_count',0,'validation_codes',jsonb_build_array('source_taxi','target_complete_set','release_hold_exclusions','ir_separation','cutover_lock'));
 end if;
 insert into public.rollover_taxi_unlock_sets(id,rollover_execution_id,league_id,source_league_season_id,target_league_season_id,target_assignment_set_id,source_snapshot_id,source_snapshot_hash,source_taxi_count,target_unlocked_count,ordinary_release_count,commissioner_hold_count,aggregate_unlock_hash,status,created_by)
 values(set_id,x.id,x.league_id,source_id,target_id,aset.id,s.id,s.aggregate_snapshot_fingerprint,taxi_count,unlocked,ordinary,held,aggregate_fp,'complete_unpublished',p_actor);
 for r in select * from public.season_roster_assignments where league_season_id=source_id and roster_designation='taxi' order by sleeper_player_id,id loop
  select id into target_row from public.season_roster_assignments where assignment_set_id=aset.id and sleeper_player_id=r.sleeper_player_id;select id into rel from public.rollover_contract_releases where rollover_execution_id=x.id and player_id=r.sleeper_player_id;select id into hold from public.rollover_commissioner_holds where rollover_execution_id=x.id and player_id=r.sleeper_player_id and hold_status='active';
  disp:=case when target_row is not null then 'unlocked_to_active_pool' when hold is not null then 'commissioner_hold' else 'ordinary_release' end;
  row_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8b-taxi-unlock-row-v1','execution',x.id,'player',r.sleeper_player_id,'source_assignment',r.id,'target_assignment',target_row,'release',rel,'hold',hold,'disposition',disp));
  insert into public.rollover_taxi_unlock_dispositions(taxi_unlock_set_id,rollover_execution_id,league_id,player_id,source_assignment_id,target_assignment_id,release_id,commissioner_hold_id,disposition,source_roster_designation,target_roster_designation,provenance,deterministic_row_hash)
  values(set_id,x.id,x.league_id,r.sleeper_player_id,r.id,target_row,rel,hold,disp,'taxi',(select roster_designation from public.season_roster_assignments where id=target_row),jsonb_build_object('operation','UNLOCK_TAXI_ASSIGNMENTS','eligibility_enforced',false,'automatic_taxi_return',false,'ir_mutated',false),row_fp);written:=written+1;
 end loop;
 if written<>taxi_count or exists(select 1 from public.season_roster_assignments where assignment_set_id=aset.id and roster_designation='taxi') then perform public.raise_phase3b6c1_failure('taxi_unlock_postcondition_failed','{}');end if;
 return jsonb_build_object('source_taxi_count',taxi_count,'target_unlocked_count',unlocked,'ordinary_release_count',ordinary,'commissioner_hold_count',held,'ir_rows_mutated',0,'taxi_eligibility_enforced',false,'assignment_rows_mutated',0,'disposition_rows_written',written,'compatible_replay_count',0,'taxi_unlock_set_id',set_id,'aggregate_unlock_hash',aggregate_fp,'mutation_count',written+1,'validation_codes',jsonb_build_array('source_taxi','target_complete_set','release_hold_exclusions','ir_separation','cutover_lock'));
end$$;

create or replace function public.execute_rollover_typed_handler_phase3b8b_private(p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare code text:=p_operation->>'operation_type';result jsonb;begin
 if code<>'UNLOCK_TAXI_ASSIGNMENTS' then return public.execute_rollover_typed_handler_phase3b8a_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);end if;
 if (p_operation->>'operation_index')::int<>16 or not exists(select 1 from public.rollover_execution_handler_registry where operation_code=code and operation_order=16 and handler_version=1) then perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}');end if;
 result:=public.write_taxi_unlock_set_phase3b8b_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 return jsonb_build_object('operation_code',code,'handler_version',1,'result',result||jsonb_build_object('operation_mutation_count',coalesce((result->>'mutation_count')::int,0),'deterministic_result_hash',result->>'aggregate_unlock_hash'));end$$;

-- The Phase 3B.8B dispatcher is defined below by the same certified outer-run,
-- inner-savepoint pattern, extended only to operation 16.
create or replace function public.execute_rollover_plan_phase3b8b_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;p public.rollover_execution_plans%rowtype;l public.rollover_execution_locks%rowtype;
 prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;attempted int:=0;completed int:=0;domain_mutations int:=0;failed_op jsonb;
 failure_sqlstate text;failure_message text;failure_detail text;failure_hint text;failure_context text;result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null or nullif(p_request->>'approval_id','') is null or nullif(p_request->>'execution_plan_id','') is null or nullif(p_request->>'expected_plan_fingerprint','') is null or nullif(p_request->>'expected_execution_status','') is null or nullif(p_request->>'expected_approval_status','') is null then raise exception 'complete execution assertions required';end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6c','execution_id',x.id,'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);request_fp:=public.rollover_material_fingerprint(material);
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);end if;
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::int or p.plan_version<>a.execution_plan_version or p.plan_status<>'approved_for_execution' or not p.approved_for_execution or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint' or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then raise exception 'stale or invalid approved execution plan';end if;
 select * into l from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=a.id and execution_plan_id=p.id and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;
 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,execution_plan_version,plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',p.operation_count,run_started,p_actor,jsonb_build_object('engine_version','phase3b8b-v1','typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  perform set_config('app.rollover_typed_execution','phase3b8b-v1',true);
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::int is distinct from attempted or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then raise exception 'ordered operation sequence mismatch';end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE','VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED','FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE','RESOLVE_OWNER_OPTION_OUTCOMES','VALIDATE_COMMISSIONER_REVIEW_OUTCOMES','ADVANCE_CONTRACT_SEASON_OBLIGATIONS','EXERCISE_VALID_OWNER_OPTIONS','DECLINE_OR_EXPIRE_OWNER_OPTIONS','RELEASE_EXPIRED_CONTRACTS','APPLY_COMMISSIONER_HOLDS','RECONCILE_TARGET_ROSTER_ASSIGNMENTS','UNLOCK_TAXI_ASSIGNMENTS') then
    handler_result:=public.execute_rollover_typed_handler_phase3b8b_private(op,x.id,p.id,a.id,p_actor);domain_mutations:=domain_mutations+coalesce((handler_result#>>'{result,operation_mutation_count}')::int,0);
   else raise exception 'unsupported Phase 3B.8B operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint','completed',op_started,clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),handler_result,jsonb_build_object('domain_mutations',coalesce((handler_result#>>'{result,operation_mutation_count}')::int,0),'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));completed:=completed+1;
  end loop;
 exception when others then get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,failure_detail=pg_exception_detail,failure_hint=pg_exception_hint,failure_context=pg_exception_context;end;
 if failure_message is not null then
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),'failed',coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),jsonb_build_object('failure_code',failure_message,'sqlstate',failure_sqlstate,'detail',left(coalesce(failure_detail,''),4096),'hint',left(coalesce(failure_hint,''),1024),'context',left(coalesce(failure_context,''),4096),'rolled_back_operations',completed,'domain_mutations_committed',0,'live_external_call_performed',false),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,'operations_attempted',attempted,'operations_completed',0,'success',false,'failure_code',failure_message,'failure_reason',failure_message,'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,operations_completed=0,finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);end if;
 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,'operations_attempted',attempted,'operations_completed',completed,'success',true,'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',domain_mutations,'live_external_call_performed',false,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,operations_completed=completed,finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);end$$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);return public.execute_rollover_plan_phase3b8b_private(p_request,actor);end$$;

revoke all on function public.write_taxi_unlock_set_phase3b8b_private(uuid,uuid,uuid,uuid),public.execute_rollover_typed_handler_phase3b8b_private(jsonb,uuid,uuid,uuid,uuid),public.execute_rollover_plan_phase3b8b_private(jsonb,uuid),public.reject_phase3b8b_taxi_unlock_mutation() from public,anon,authenticated,service_role;
revoke all on function public.guard_contract_write_during_rollover(),public.guard_season_roster_assignment_insert_phase3b8a() from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

commit;
