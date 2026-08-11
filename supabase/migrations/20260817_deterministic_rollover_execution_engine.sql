begin;

-- Phase 3B.5I execution framework only. The dispatcher accepts synthetic
-- boundary/no-op operations and deliberately contains no production-domain DML.
create table public.rollover_execution_runs (
 id uuid primary key default gen_random_uuid(),
 rollover_execution_id uuid not null references public.rollover_executions(id) on delete restrict,
 league_id uuid not null references public.leagues(id) on delete restrict,
 approval_id uuid not null references public.rollover_execution_plan_approvals(id) on delete restrict,
 execution_plan_id uuid not null references public.rollover_execution_plans(id) on delete restrict,
 execution_plan_version integer not null check(execution_plan_version>0),
 plan_fingerprint text not null check(plan_fingerprint~'^[0-9a-f]{64}$'),
 idempotency_key text not null check(length(btrim(idempotency_key))>0),
 request_fingerprint text not null check(request_fingerprint~'^[0-9a-f]{64}$'),
 run_status text not null check(run_status in ('execution_started','executing','executed_successfully','execution_failed')),
 operation_count integer not null check(operation_count>=0),
 operations_attempted integer not null default 0 check(operations_attempted>=0),
 operations_completed integer not null default 0 check(operations_completed>=0 and operations_completed<=operations_attempted),
 started_at timestamptz not null default clock_timestamp(),
 finished_at timestamptz,
 duration_ms bigint check(duration_ms is null or duration_ms>=0),
 result_payload jsonb,
 diagnostics jsonb not null default '{}'::jsonb check(jsonb_typeof(diagnostics)='object'),
 failure_reason text,
 executed_by uuid not null references auth.users(id),
 created_at timestamptz not null default clock_timestamp(),
 unique(rollover_execution_id,idempotency_key),
 check((run_status in ('execution_started','executing') and finished_at is null and duration_ms is null)
    or (run_status='executed_successfully' and finished_at is not null and duration_ms is not null and result_payload is not null and failure_reason is null and operations_completed=operation_count)
    or (run_status='execution_failed' and finished_at is not null and duration_ms is not null and failure_reason is not null))
);
create unique index rollover_execution_runs_one_success_uidx
 on public.rollover_execution_runs(rollover_execution_id)
 where run_status='executed_successfully';
create index rollover_execution_runs_plan_idx
 on public.rollover_execution_runs(execution_plan_id,execution_plan_version,created_at);

create table public.rollover_execution_operation_results (
 id uuid primary key default gen_random_uuid(),
 execution_run_id uuid not null references public.rollover_execution_runs(id) on delete restrict,
 rollover_execution_id uuid not null references public.rollover_executions(id) on delete restrict,
 operation_id uuid not null,
 operation_index integer not null check(operation_index>0),
 operation_type text not null,
 operation_fingerprint text not null check(operation_fingerprint~'^[0-9a-f]{64}$'),
 operation_status text not null check(operation_status in ('completed','failed')),
 started_at timestamptz not null,
 finished_at timestamptz not null,
 duration_ms bigint not null check(duration_ms>=0),
 result_payload jsonb,
 diagnostics jsonb not null default '{}'::jsonb check(jsonb_typeof(diagnostics)='object'),
 failure_reason text,
 unique(execution_run_id,operation_index),
 unique(execution_run_id,operation_id),
 check((operation_status='completed' and result_payload is not null and failure_reason is null)
    or (operation_status='failed' and failure_reason is not null))
);
create index rollover_execution_operation_results_execution_idx
 on public.rollover_execution_operation_results(rollover_execution_id,operation_index);

alter table public.rollover_execution_runs enable row level security;
alter table public.rollover_execution_operation_results enable row level security;
create policy rollover_execution_runs_member_read on public.rollover_execution_runs
 for select to authenticated using(exists(
  select 1 from public.league_memberships m
  where m.league_id=rollover_execution_runs.league_id and m.user_id=auth.uid()
 ));
create policy rollover_execution_operation_results_member_read on public.rollover_execution_operation_results
 for select to authenticated using(exists(
  select 1 from public.rollover_execution_runs r
  join public.league_memberships m on m.league_id=r.league_id and m.user_id=auth.uid()
  where r.id=rollover_execution_operation_results.execution_run_id
 ));
revoke all on public.rollover_execution_runs,public.rollover_execution_operation_results from public,anon,authenticated;
grant select on public.rollover_execution_runs,public.rollover_execution_operation_results to authenticated;
grant select,insert,update on public.rollover_execution_runs,public.rollover_execution_operation_results to service_role;

create or replace function public.execute_rollover_plan_phase3b5i_private(p_request jsonb,p_actor uuid)
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
 op jsonb;
 op_started timestamptz;
 run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');
 material jsonb;
 request_fp text;
 attempted integer:=0;
 completed integer:=0;
 failed_op jsonb;
 failure_sqlstate text;
 failure_message text;
 failure_detail text;
 failure_hint text;
 failure_context text;
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
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b5i','execution_id',x.id,
  'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);

 select * into prior from public.rollover_execution_runs
  where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then
  if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;
  return prior.result_payload || jsonb_build_object('idempotent',true,'execution_run_id',prior.id);
 end if;
 select * into prior from public.rollover_execution_runs
  where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then
  return prior.result_payload || jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);
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
  p.operation_count,run_started,p_actor,jsonb_build_object('engine_version','phase3b5i-v1','synthetic_only',true))
 returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;

 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted
      or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then
    raise exception 'ordered operation sequence mismatch';
   end if;
   if op->>'operation_type' in ('verify_execution_boundary','phase3b5i_noop') then
    insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
     operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
    values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint',
     'completed',op_started,clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),
     jsonb_build_object('synthetic_noop',true,'operation_index',attempted),jsonb_build_object('domain_mutations',0));
    completed:=completed+1;
   elsif op->>'operation_type'='phase3b5i_fail' then
    raise exception 'synthetic blocking operation failure at index %',attempted;
   elsif op->>'operation_type'='phase3b5i_fail_once' then
    if not exists(select 1 from public.rollover_execution_runs r
     where r.rollover_execution_id=x.id and r.run_status='execution_failed' and r.id<>runrow.id) then
     raise exception 'synthetic recoverable operation failure at index %',attempted;
    end if;
    insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
     operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
    values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint',
     'completed',op_started,clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),
     jsonb_build_object('synthetic_noop',true,'recovered_after_prior_failure',true),jsonb_build_object('domain_mutations',0));
    completed:=completed+1;
   else
    raise exception 'unsupported Phase 3B.5I operation type: %',op->>'operation_type';
   end if;
  end loop;
 exception when others then
  get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,failure_detail=pg_exception_detail,
   failure_hint=pg_exception_hint,failure_context=pg_exception_context;
 end;

 if failure_message is not null then
  -- All operation-result writes in the inner block were rolled back. Persist
  -- only failure evidence after rollback; no partial operation is completed.
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
   operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),
   coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),
   'failed',coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),
   jsonb_build_object('sqlstate',failure_sqlstate,'detail',coalesce(failure_detail,''),'hint',coalesce(failure_hint,''),
    'context',coalesce(failure_context,''),'rolled_back_operations',completed,'domain_mutations_committed',0),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
   'operations_attempted',attempted,'operations_completed',0,'success',false,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,
   operations_completed=0,finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;

 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
  'operations_attempted',attempted,'operations_completed',completed,'success',true,'diagnostics',
  jsonb_build_object('synthetic_only',true,'domain_mutations_committed',0,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,
  operations_completed=completed,finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end
$$;

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
 return public.execute_rollover_plan_phase3b5i_private(p_request,actor);
end
$$;

revoke all on function public.execute_rollover_plan_phase3b5i_private(jsonb,uuid),
 public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

comment on table public.rollover_execution_runs is 'Phase 3B.5I durable synthetic execution-attempt evidence; no publication or production-domain mutation.';
comment on function public.execute_rollover_plan_authenticated(jsonb) is 'Phase 3B.5I authenticated synthetic execution wrapper; publication and production-domain operations are not implemented.';

commit;
