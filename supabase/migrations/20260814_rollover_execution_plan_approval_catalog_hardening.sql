begin;

-- Additive Phase 3B.5H correction. This migration performs no data writes and
-- invokes no rollover RPC. It hardens concurrency assertions and cutover-lock
-- durability without changing approval, revocation, or execution semantics.

do $$
begin
 if to_regprocedure('public.approve_rollover_execution_plan_authenticated_phase3b5h_base(jsonb)') is null then
  alter function public.approve_rollover_execution_plan_authenticated(jsonb)
   rename to approve_rollover_execution_plan_authenticated_phase3b5h_base;
 end if;
end $$;

revoke all on function public.approve_rollover_execution_plan_authenticated_phase3b5h_base(jsonb)
 from public,anon,authenticated,service_role;

create or replace function public.approve_rollover_execution_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 actor uuid:=public.require_authenticated_user();
 x public.rollover_executions%rowtype;
 p public.rollover_execution_plans%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'requested_by' or p_request?'approved_by' then
  raise exception 'actor spoofing forbidden';
 end if;
 if nullif(btrim(p_request->>'expected_execution_status'),'') is null
    or nullif(btrim(p_request->>'expected_plan_status'),'') is null then
  raise exception 'expected execution and plan statuses required';
 end if;
 perform pg_advisory_xact_lock(hashtextextended(p_request->>'rollover_execution_id',0));
 select * into x from public.rollover_executions
  where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 if x.status is distinct from p_request->>'expected_execution_status' then
  raise exception 'stale execution status';
 end if;
 select * into p from public.rollover_execution_plans
  where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null then raise exception 'execution plan not found';end if;
 if p.plan_status is distinct from p_request->>'expected_plan_status' then
  raise exception 'stale execution plan status';
 end if;
 return public.approve_rollover_execution_plan_authenticated_phase3b5h_base(p_request);
end $$;

create or replace function public.reject_cutover_lock_delete()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if old.lock_type='cutover' then
  raise exception 'durable cutover locks cannot be deleted; use a controlled lifecycle transition';
 end if;
 return old;
end $$;

drop trigger if exists rollover_cutover_lock_delete_guard on public.rollover_execution_locks;
create trigger rollover_cutover_lock_delete_guard
 before delete on public.rollover_execution_locks
 for each row execute function public.reject_cutover_lock_delete();

revoke all on function public.approve_rollover_execution_plan_authenticated(jsonb),
 public.reject_cutover_lock_delete() from public,anon,authenticated,service_role;
grant execute on function public.approve_rollover_execution_plan_authenticated(jsonb) to authenticated;
grant execute on function public.reject_cutover_lock_delete() to service_role;

comment on function public.approve_rollover_execution_plan_authenticated(jsonb) is
 'Phase 3B.5H authenticated concurrency-assertion wrapper; approval executes zero plan operations.';
comment on function public.approve_rollover_execution_plan_authenticated_phase3b5h_base(jsonb) is
 'Phase 3B.5H internal approval implementation; deliberately not executable by API roles.';

commit;
