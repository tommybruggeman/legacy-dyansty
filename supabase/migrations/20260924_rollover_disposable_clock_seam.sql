begin;

create table if not exists public.rollover_disposable_clock_override (
 singleton boolean primary key default true check(singleton),
 effective_now timestamptz not null,
 set_at timestamptz not null default clock_timestamp(),
 set_by name not null default current_user
);
alter table public.rollover_disposable_clock_override enable row level security;
revoke all on public.rollover_disposable_clock_override from public,anon,authenticated,service_role;

create or replace function public.rollover_is_approved_disposable()
returns boolean language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare matched integer:=0; total integer:=0;
begin
 if to_regclass('public.environment_identity') is null then return false; end if;
 execute 'select count(*),count(*) filter(where singleton and environment_name=$1 and environment_type=$2 and parent_project=$3) from public.environment_identity'
 into total,matched using 'phase3b5h-testing','disposable_test','Legacy-Dynasty';
 return total=1 and matched=1;
end $$;

create or replace function public.rollover_effective_now()
returns timestamptz language plpgsql volatile security definer set search_path=pg_catalog,public as $$
declare override_now timestamptz;
begin
 select effective_now into override_now from public.rollover_disposable_clock_override where singleton;
 if override_now is null then return clock_timestamp(); end if;
 if not public.rollover_is_approved_disposable() then raise exception 'rollover test clock rejected outside approved disposable environment'; end if;
 return override_now;
end $$;

create or replace function public.set_rollover_disposable_clock_private(p_effective_now timestamptz)
returns timestamptz language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if not public.rollover_is_approved_disposable() then raise exception 'approved disposable sentinel required'; end if;
 if p_effective_now is null then raise exception 'effective test time required'; end if;
 insert into public.rollover_disposable_clock_override(singleton,effective_now)
 values(true,p_effective_now)
 on conflict(singleton) do update set effective_now=excluded.effective_now,set_at=clock_timestamp(),set_by=current_user;
 return p_effective_now;
end $$;

create or replace function public.reset_rollover_disposable_clock_private()
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if not public.rollover_is_approved_disposable() then raise exception 'approved disposable sentinel required'; end if;
 delete from public.rollover_disposable_clock_override;
end $$;

-- Additive replacement of the current implementation: only the real-time
-- deadline comparison is routed through the production-inert clock seam.
create or replace function public.close_rollover_decision_window(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; d record; k text; rev int; next_status text; summary jsonb;
begin
 k=nullif(p_request->>'idempotency_key','');
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.metadata->>'close_idempotency_key'=k and x.status='decision_window_closed' then return jsonb_build_object('idempotent',true,'execution',to_jsonb(x)); end if;
 if x.status<>'decision_window_open' or x.owner_deadline is null
    or (p_request->>'effective_close_timestamp')::timestamptz<x.owner_deadline
    or public.rollover_effective_now()<x.owner_deadline then raise exception 'Early close is not authorized'; end if;
 if x.decision_population_fingerprint<>p_request->>'expected_population_fingerprint' then raise exception 'Population drift detected'; end if;
 for d in select * from public.rollover_owner_decisions where rollover_execution_id=x.id for update loop
  next_status=case d.decision_status when 'waiting_for_owner' then 'no_response' when 'decline_submitted' then 'planned_release' when 'recontract_validated' then 'planned_retention' else d.decision_status end;
  if next_status<>d.decision_status then
   select coalesce(max(revision_number),0)+1 into rev from public.rollover_owner_decision_revisions where owner_decision_id=d.id;
   insert into public.rollover_owner_decision_revisions(owner_decision_id,rollover_execution_id,revision_number,prior_status,new_status,prior_choice,new_choice,changed_by,reason,evidence,idempotency_key)
   values(d.id,x.id,rev,d.decision_status,next_status,d.owner_choice,d.owner_choice,(p_request->>'requested_by')::uuid,'decision window closed',d.evidence,format('owner-close:%s:%s:%s',x.id,d.id,rev));
   update public.rollover_owner_decisions set decision_status=next_status,locked_at=(p_request->>'effective_close_timestamp')::timestamptz,
    planned_outcome=case when next_status in ('no_response','planned_release') then 'release_at_rollover_to_commissioner_hold' when next_status='planned_retention' then 'retain' else planned_outcome end,
    execution_status=case when next_status in ('planned_release','planned_retention') then 'ready' else execution_status end where id=d.id;
  else update public.rollover_owner_decisions set locked_at=(p_request->>'effective_close_timestamp')::timestamptz where id=d.id; end if;
 end loop;
 update public.rollover_executions set status='decision_window_closed',metadata=metadata||jsonb_build_object('close_idempotency_key',k,'closed_by',p_request->>'requested_by') where id=x.id returning * into x;
 select jsonb_object_agg(decision_status,cnt) into summary from(select decision_status,count(*) cnt from public.rollover_owner_decisions where rollover_execution_id=x.id group by decision_status)q;
 return jsonb_build_object('idempotent',false,'execution',to_jsonb(x),'summary',coalesce(summary,'{}'));
end $$;

revoke all on function public.rollover_is_approved_disposable(),public.rollover_effective_now(),
 public.set_rollover_disposable_clock_private(timestamptz),public.reset_rollover_disposable_clock_private()
 from public,anon,authenticated,service_role;
revoke all on function public.close_rollover_decision_window(jsonb) from public,anon,authenticated;
grant execute on function public.close_rollover_decision_window(jsonb) to service_role;

-- Preserve the certified preparation implementation privately and add the
-- missing hosted state transition around it.
alter function public.prepare_rollover_authorities_authenticated(jsonb)
 rename to prepare_rollover_authorities_phase3b5e_base;
revoke all on function public.prepare_rollover_authorities_phase3b5e_base(jsonb)
 from public,anon,authenticated,service_role;

create or replace function public.prepare_rollover_authorities_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=auth.uid(); execution_id uuid; result jsonb; prepared_count integer; blocked_count integer;
begin
 if actor is null then raise exception 'authentication required'; end if;
 if p_request ? 'actor_user_id' or p_request ? 'requested_by' then raise exception 'actor spoofing forbidden'; end if;
 execution_id:=(p_request->>'execution_id')::uuid;
 perform public.require_commissioner_authority((select league_id from public.rollover_executions where id=execution_id));
 update public.rollover_executions set status='authority_initializing'
 where id=execution_id and status='decision_window_closed';
 if not found then raise exception 'execution state not eligible for hosted preparation'; end if;
 -- The certified base requires the pre-transition state in its request and row.
 update public.rollover_executions set status='decision_window_closed' where id=execution_id;
 result:=public.prepare_rollover_authorities_phase3b5e_base(p_request);
 select count(*) filter(where authority_status='prepared'),count(*) filter(where authority_status='blocked')
 into prepared_count,blocked_count from public.rollover_authority_preparations
 where rollover_execution_id=execution_id and superseded_at is null and cancelled_at is null;
 if prepared_count<>3 or blocked_count<>0 then raise exception 'hosted authority preparation did not produce three prepared domains'; end if;
 update public.rollover_executions set status='authority_initializing' where id=execution_id and status='decision_window_closed';
 update public.rollover_executions set status='authority_ready' where id=execution_id and status='authority_initializing';
 return result;
end $$;
revoke all on function public.prepare_rollover_authorities_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.prepare_rollover_authorities_authenticated(jsonb) to authenticated;

commit;
