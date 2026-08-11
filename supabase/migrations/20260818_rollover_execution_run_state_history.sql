begin;

-- Additive Phase 3B.5I audit correction. Records attempt-state transitions only;
-- it invokes no execution RPC and performs no production-domain mutation.
create table public.rollover_execution_run_state_events (
 id bigint generated always as identity primary key,
 execution_run_id uuid not null references public.rollover_execution_runs(id) on delete restrict,
 rollover_execution_id uuid not null references public.rollover_executions(id) on delete restrict,
 from_status text,
 to_status text not null check(to_status in ('execution_started','executing','executed_successfully','execution_failed')),
 transitioned_at timestamptz not null default clock_timestamp(),
 diagnostics jsonb not null default '{}'::jsonb check(jsonb_typeof(diagnostics)='object'),
 check((from_status is null and to_status='execution_started') or from_status is not null)
);
create unique index rollover_execution_run_state_events_transition_uidx
 on public.rollover_execution_run_state_events(execution_run_id,to_status);
create index rollover_execution_run_state_events_execution_idx
 on public.rollover_execution_run_state_events(rollover_execution_id,transitioned_at);

create or replace function public.record_rollover_execution_run_state_event()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if tg_op='INSERT' then
  insert into public.rollover_execution_run_state_events(execution_run_id,rollover_execution_id,from_status,to_status,diagnostics)
  values(new.id,new.rollover_execution_id,null,new.run_status,jsonb_build_object('source','run_insert'));
 elsif new.run_status is distinct from old.run_status then
  insert into public.rollover_execution_run_state_events(execution_run_id,rollover_execution_id,from_status,to_status,diagnostics)
  values(new.id,new.rollover_execution_id,old.run_status,new.run_status,jsonb_build_object('source','run_update'));
 end if;
 return new;
end $$;
create trigger rollover_execution_run_state_history
after insert or update of run_status on public.rollover_execution_runs
for each row execute function public.record_rollover_execution_run_state_event();

alter table public.rollover_execution_run_state_events enable row level security;
create policy rollover_execution_run_state_events_member_read on public.rollover_execution_run_state_events
 for select to authenticated using(exists(
  select 1 from public.rollover_execution_runs r
  join public.league_memberships m on m.league_id=r.league_id and m.user_id=auth.uid()
  where r.id=rollover_execution_run_state_events.execution_run_id
 ));
revoke all on public.rollover_execution_run_state_events from public,anon,authenticated;
grant select on public.rollover_execution_run_state_events to authenticated;
grant select,insert on public.rollover_execution_run_state_events to service_role;
revoke all on function public.record_rollover_execution_run_state_event() from public,anon,authenticated,service_role;

comment on table public.rollover_execution_run_state_events is 'Phase 3B.5I append-only durable execution-attempt state transition evidence.';
commit;
