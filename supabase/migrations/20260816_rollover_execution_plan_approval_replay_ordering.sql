begin;

-- Additive Phase 3B.5H correction. This migration performs no data writes and
-- invokes no rollover RPC. It restores exact same-key replay before mutable
-- status assertions while retaining authentication, authorization, request
-- integrity, actor/league scope, and material-conflict checks.
create or replace function public.approve_rollover_execution_plan_authenticated(p_request jsonb)
returns jsonb
language plpgsql
security definer
set search_path=pg_catalog,public
as $$
declare
 actor uuid:=public.require_authenticated_user();
 x public.rollover_executions%rowtype;
 p public.rollover_execution_plans%rowtype;
 k text;
 material jsonb;
 fp text;
 retry jsonb;
begin
 if p_request?'actor_user_id' or p_request?'requested_by' or p_request?'approved_by' then
  raise exception 'actor spoofing forbidden';
 end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),'');
 if k is null then raise exception 'idempotency key required';end if;
 if nullif(btrim(p_request->>'expected_execution_status'),'') is null
    or nullif(btrim(p_request->>'expected_plan_status'),'') is null then
  raise exception 'expected execution and plan statuses required';
 end if;
 if p_request->>'approval_statement_code'<>'ROLLOVER_EXECUTION_PLAN_APPROVED'
    or coalesce((p_request->>'approval_statement_version')::integer,0)<>1
    or nullif(btrim(p_request->>'approval_statement'),'') is null then
  raise exception 'valid approval statement required';
 end if;

 perform pg_advisory_xact_lock(hashtextextended(p_request->>'rollover_execution_id',0));
 select * into x from public.rollover_executions
  where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);

 -- Approval keys are not portable across league scope. This also prevents a
 -- privileged multi-league caller from presenting an existing key as new.
 if exists(
  select 1 from public.rollover_operation_requests r
  where r.operation_type='execution_plan_approve'
   and r.idempotency_key=k and r.league_id<>x.league_id
 ) then raise exception 'Idempotency key material request conflict';end if;

 material:=jsonb_build_object(
  'operation','execution_plan_approve','execution_id',x.id,'league_id',x.league_id,
  'request',p_request-'idempotency_key','actor',actor
 );
 fp:=public.rollover_material_fingerprint(material);
 retry:=public.rollover_operation_retry(x.league_id,'execution_plan_approve',k,fp);
 if retry is not null then return retry;end if;

 -- Only a genuinely new operation is subject to current-state assertions.
 if x.status is distinct from p_request->>'expected_execution_status' then
  raise exception 'stale execution status';
 end if;
 select * into p from public.rollover_execution_plans
  where id=(p_request->>'execution_plan_id')::uuid
   and rollover_execution_id=x.id for update;
 if p.id is null then raise exception 'execution plan not found';end if;
 if p.plan_status is distinct from p_request->>'expected_plan_status' then
  raise exception 'stale execution plan status';
 end if;
 return public.approve_rollover_execution_plan_authenticated_phase3b5h_base(p_request);
end
$$;

revoke all on function public.approve_rollover_execution_plan_authenticated(jsonb)
 from public,anon,authenticated,service_role;
grant execute on function public.approve_rollover_execution_plan_authenticated(jsonb)
 to authenticated;

comment on function public.approve_rollover_execution_plan_authenticated(jsonb) is
 'Phase 3B.5H authenticated approval boundary: exact material replay precedes mutable-state assertions; approval executes zero plan operations.';

commit;
