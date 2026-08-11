begin;

-- Phase 3B.6A: typed, versioned, read-only authority preflight handlers.
-- Phase 3B.5H approval material and the Phase 3B.5I private engine remain
-- unchanged. No publication or production-domain DML is introduced.
create table public.rollover_execution_handler_registry (
 operation_code text primary key,
 operation_order integer not null unique check(operation_order > 0),
 handler_version integer not null check(handler_version > 0),
 input_schema_version text not null check(length(btrim(input_schema_version)) > 0),
 result_schema_version text not null check(length(btrim(result_schema_version)) > 0),
 execution_owner text not null check(execution_owner = 'execution'),
 mutation_class text not null check(mutation_class = 'read_only'),
 enabled boolean not null default true,
 registered_at timestamptz not null default clock_timestamp(),
 metadata jsonb not null default '{}'::jsonb check(jsonb_typeof(metadata) = 'object')
);

insert into public.rollover_execution_handler_registry
 (operation_code,operation_order,handler_version,input_schema_version,result_schema_version,
  execution_owner,mutation_class,metadata)
values
 ('VERIFY_CLOSING_SEASON_AUTHORITY',1,1,'phase3b6a-authority-input-v1','phase3b6a-authority-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6A','domain','season_authority')),
 ('VERIFY_TARGET_SEASON_AUTHORITY',2,1,'phase3b6a-authority-input-v1','phase3b6a-authority-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6A','domain','season_authority')),
 ('VERIFY_TARGET_SLEEPER_LINKAGE',3,1,'phase3b6a-sleeper-input-v1','phase3b6a-sleeper-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6A','domain','external_sync'));

create or replace function public.reject_rollover_handler_registry_mutation()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 raise exception 'rollover execution handler registration is immutable; use an additive versioned migration';
end $$;
create trigger rollover_execution_handler_registry_immutable
before update or delete on public.rollover_execution_handler_registry
for each row execute function public.reject_rollover_handler_registry_mutation();

alter table public.rollover_execution_handler_registry enable row level security;
create policy rollover_execution_handler_registry_authenticated_read
 on public.rollover_execution_handler_registry for select to authenticated using(true);
revoke all on public.rollover_execution_handler_registry from public,anon,authenticated;
grant select on public.rollover_execution_handler_registry to authenticated;
grant select,insert on public.rollover_execution_handler_registry to service_role;

create or replace function public.execute_rollover_typed_handler_phase3b6a_private(
 p_operation jsonb,
 p_rollover_execution_id uuid,
 p_execution_plan_id uuid
) returns jsonb
language plpgsql
security definer
set search_path=pg_catalog,public
as $$
declare
 x public.rollover_executions%rowtype;
 p public.rollover_execution_plans%rowtype;
 source_row public.league_seasons%rowtype;
 target_row public.league_seasons%rowtype;
 league_row public.leagues%rowtype;
 registry_row public.rollover_execution_handler_registry%rowtype;
 code text:=p_operation->>'operation_type';
 source_count integer;
 target_count integer;
 active_count integer;
 result_material jsonb;
begin
 if jsonb_typeof(p_operation) is distinct from 'object' then
  raise exception 'typed handler operation object required';
 end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into p from public.rollover_execution_plans
  where id=p_execution_plan_id and rollover_execution_id=p_rollover_execution_id;
 if x.id is null or p.id is null then raise exception 'typed handler execution plan identity invalid';end if;

 select * into registry_row from public.rollover_execution_handler_registry
  where operation_code=code and enabled;
 if registry_row.operation_code is null then raise exception 'unsupported Phase 3B.6A typed operation: %',coalesce(code,'');end if;
 if (p_operation->>'operation_index')::integer<>registry_row.operation_order then
  raise exception 'typed handler operation order mismatch for %',code;
 end if;
 if p_operation?'handler_version' and (p_operation->>'handler_version')::integer<>registry_row.handler_version then
  raise exception 'typed handler version mismatch for %',code;
 end if;
 if p_operation?'input_schema_version'
    and p_operation->>'input_schema_version'<>registry_row.input_schema_version then
  raise exception 'typed handler input schema mismatch for %',code;
 end if;

 select count(*) into source_count from public.league_seasons
  where league_id=x.league_id and season=p.source_season;
 select count(*) into target_count from public.league_seasons
  where league_id=x.league_id and season=p.target_season;
 select count(*) into active_count from public.league_seasons
  where league_id=x.league_id and is_active;
 select * into source_row from public.league_seasons
  where league_id=x.league_id and season=p.source_season for share;
 select * into target_row from public.league_seasons
  where league_id=x.league_id and season=p.target_season for share;

 if code='VERIFY_CLOSING_SEASON_AUTHORITY' then
  if p.source_season<>x.source_season or p.target_season<>x.target_season then
   raise exception 'closing season plan/execution boundary mismatch';
  end if;
  if source_count<>1 or active_count<>1 or source_row.id is null or not source_row.is_active then
   raise exception 'closing season authority must be the exactly one active league season';
  end if;
  result_material:=jsonb_build_object(
   'operation_code',code,'league_id',x.league_id,'league_season_id',source_row.id,
   'season',source_row.season,'is_active',source_row.is_active,
   'sleeper_league_id',source_row.sleeper_league_id,
   'source_row_count',source_count,'active_row_count',active_count);
 elsif code='VERIFY_TARGET_SEASON_AUTHORITY' then
  if p.target_season<>p.source_season+1 or p.source_season<>x.source_season
     or p.target_season<>x.target_season then
   raise exception 'target season must be the approved source plus one boundary';
  end if;
  if target_count<>1 or target_row.id is null or target_row.is_active then
   raise exception 'target season authority must exist exactly once and remain inactive';
  end if;
  result_material:=jsonb_build_object(
   'operation_code',code,'league_id',x.league_id,'league_season_id',target_row.id,
   'source_season',p.source_season,'target_season',target_row.season,
   'is_active',target_row.is_active,'target_row_count',target_count);
 elsif code='VERIFY_TARGET_SLEEPER_LINKAGE' then
  if source_count<>1 or target_count<>1 or target_row.id is null then
   raise exception 'source and target season authority required for Sleeper linkage';
  end if;
  if nullif(btrim(target_row.sleeper_league_id),'') is null then
   raise exception 'target Sleeper league linkage required';
  end if;
  if target_row.sleeper_league_id=source_row.sleeper_league_id then
   raise exception 'target Sleeper league must be the renewed target-season league';
  end if;
  if exists(
   select 1 from public.league_seasons s
   where s.league_id<>x.league_id and s.sleeper_league_id=target_row.sleeper_league_id
  ) then raise exception 'target Sleeper league is linked to another Legacy league';end if;
  select * into league_row from public.leagues where id=x.league_id;
  if league_row.id is null then raise exception 'Legacy league identity missing';end if;
  if nullif(btrim(league_row.sleeper_league_id),'') is not null
     and league_row.sleeper_league_id is distinct from source_row.sleeper_league_id
     and league_row.sleeper_league_id is distinct from target_row.sleeper_league_id then
   raise exception 'legacy Sleeper pointer conflicts with approved season authorities';
  end if;
  result_material:=jsonb_build_object(
   'operation_code',code,'league_id',x.league_id,'target_league_season_id',target_row.id,
   'target_season',target_row.season,'target_sleeper_league_id',target_row.sleeper_league_id,
   'source_sleeper_league_id',source_row.sleeper_league_id,
   'legacy_pointer_classification',case
    when league_row.sleeper_league_id=target_row.sleeper_league_id then 'target'
    when league_row.sleeper_league_id=source_row.sleeper_league_id then 'source'
    else 'unset' end);
 else
  raise exception 'unsupported Phase 3B.6A typed operation: %',code;
 end if;

 return jsonb_build_object(
  'operation_code',code,
  'handler_version',registry_row.handler_version,
  'input_schema_version',registry_row.input_schema_version,
  'result_schema_version',registry_row.result_schema_version,
  'read_only',true,
  'domain_mutations',0,
  'authority_fingerprint',public.rollover_material_fingerprint(result_material),
  'result',result_material
 );
end $$;

create or replace function public.execute_rollover_plan_phase3b6a_private(p_request jsonb,p_actor uuid)
returns jsonb
language plpgsql
security definer
set search_path=pg_catalog,public
as $$
declare
 x public.rollover_executions%rowtype;
 a public.rollover_execution_plan_approvals%rowtype;
 p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;
 prior public.rollover_execution_runs%rowtype;
 runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;
 op_started timestamptz;
 run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');
 material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;
 failed_op jsonb;
 failure_sqlstate text;failure_message text;failure_detail text;failure_hint text;failure_context text;
 result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;
 if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null
    or nullif(p_request->>'approval_id','') is null
    or nullif(p_request->>'execution_plan_id','') is null
    or nullif(p_request->>'expected_plan_fingerprint','') is null
    or nullif(p_request->>'expected_execution_status','') is null
    or nullif(p_request->>'expected_approval_status','') is null then
  raise exception 'complete execution assertions required';
 end if;

 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6a','execution_id',x.id,
  'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);

 select * into prior from public.rollover_execution_runs
  where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then
  if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;
  return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);
 end if;
 select * into prior from public.rollover_execution_runs
  where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then
  return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);
 end if;

 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals
  where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans
  where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::integer
    or p.plan_version<>a.execution_plan_version or p.plan_status<>'approved_for_execution'
    or not p.approved_for_execution or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint'
    or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then
  raise exception 'stale or invalid approved execution plan';
 end if;
 select * into l from public.rollover_execution_locks
  where rollover_execution_id=x.id and approval_id=a.id and execution_plan_id=p.id
   and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint
   and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;

 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,
  execution_plan_version,plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,
  started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',
  p.operation_count,run_started,p_actor,jsonb_build_object('engine_version','phase3b6a-v1','typed_handlers',true,'publication_performed',false))
 returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;

 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted
      or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then
    raise exception 'ordered operation sequence mismatch';
   end if;
   if op->>'operation_type' in(
    'VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE'
   ) then
    handler_result:=public.execute_rollover_typed_handler_phase3b6a_private(op,x.id,p.id);
   elsif op->>'operation_type' in('verify_execution_boundary','phase3b5i_noop') then
    handler_result:=jsonb_build_object('synthetic_noop',true,'operation_index',attempted,'domain_mutations',0);
   elsif op->>'operation_type'='phase3b5i_fail' then
    raise exception 'synthetic blocking operation failure at index %',attempted;
   elsif op->>'operation_type'='phase3b5i_fail_once' then
    if not exists(select 1 from public.rollover_execution_runs r
     where r.rollover_execution_id=x.id and r.run_status='execution_failed' and r.id<>runrow.id) then
     raise exception 'synthetic recoverable operation failure at index %',attempted;
    end if;
    handler_result:=jsonb_build_object('synthetic_noop',true,'recovered_after_prior_failure',true,'domain_mutations',0);
   else
    raise exception 'unsupported Phase 3B.6A operation type: %',op->>'operation_type';
   end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
    operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint',
    'completed',op_started,clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),
    handler_result,jsonb_build_object('domain_mutations',0,'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
   completed:=completed+1;
  end loop;
 exception when others then
  get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,failure_detail=pg_exception_detail,
   failure_hint=pg_exception_hint,failure_context=pg_exception_context;
 end;

 if failure_message is not null then
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
   operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),
   coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),
   'failed',coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),
   jsonb_build_object('sqlstate',failure_sqlstate,'detail',coalesce(failure_detail,''),'hint',coalesce(failure_hint,''),
    'context',coalesce(failure_context,''),'rolled_back_operations',completed,'domain_mutations_committed',0),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
   'operations_attempted',attempted,'operations_completed',0,'success',false,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,
   operations_completed=0,finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;

 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
  'operations_attempted',attempted,'operations_completed',completed,'success',true,'diagnostics',
  jsonb_build_object('typed_handlers',true,'domain_mutations_committed',0,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,
  operations_completed=completed,finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end $$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb
language plpgsql
security definer
set search_path=pg_catalog,public
as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 return public.execute_rollover_plan_phase3b6a_private(p_request,actor);
end $$;

revoke all on function public.reject_rollover_handler_registry_mutation(),
 public.execute_rollover_typed_handler_phase3b6a_private(jsonb,uuid,uuid),
 public.execute_rollover_plan_phase3b6a_private(jsonb,uuid),
 public.execute_rollover_plan_authenticated(jsonb)
 from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

comment on table public.rollover_execution_handler_registry is
 'Immutable Phase 3B.6A typed handler registration metadata; first three handlers are read-only.';
comment on function public.execute_rollover_typed_handler_phase3b6a_private(jsonb,uuid,uuid) is
 'Private typed read-only dispatcher for Phase 3B.6A authority and Sleeper preflight operations.';
comment on function public.execute_rollover_plan_phase3b6a_private(jsonb,uuid) is
 'Phase 3B.6A engine preserving Phase 3B.5I admission, locking, replay, rollback, audit, and fail-closed dispatch.';
comment on function public.execute_rollover_plan_authenticated(jsonb) is
 'Authenticated rollover execution wrapper; Phase 3B.6A supports exactly three real read-only handlers and no publication.';

commit;
