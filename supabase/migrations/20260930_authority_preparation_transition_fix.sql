begin;

-- Keep the execution in decision_window_closed while the certified base
-- validates and writes the three authority preparations. Only after all
-- three domains are prepared does the hosted wrapper advance forward:
-- decision_window_closed -> authority_initializing -> authority_ready.
create or replace function public.prepare_rollover_authorities_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=auth.uid(); execution_id uuid; result jsonb; prepared_count integer; blocked_count integer;
begin
 if actor is null then raise exception 'authentication required'; end if;
 if p_request ? 'actor_user_id' or p_request ? 'requested_by' then raise exception 'actor spoofing forbidden'; end if;
 execution_id:=(p_request->>'execution_id')::uuid;
 perform public.require_commissioner_authority((select league_id from public.rollover_executions where id=execution_id));
 if not exists(
  select 1
  from public.rollover_executions
  where id=execution_id
    and status='decision_window_closed'
 ) then
  raise exception 'execution state not eligible for hosted preparation';
 end if;
 result:=public.prepare_rollover_authorities_phase3b5e_base(p_request);
 select count(*) filter(where authority_status='prepared'),count(*) filter(where authority_status='blocked')
 into prepared_count,blocked_count from public.rollover_authority_preparations
 where rollover_execution_id=execution_id and superseded_at is null and cancelled_at is null;
 if prepared_count<>3 or blocked_count<>0 then raise exception 'hosted authority preparation did not produce three prepared domains'; end if;
 update public.rollover_executions set status='authority_initializing' where id=execution_id and status='decision_window_closed';
 update public.rollover_executions set status='authority_ready' where id=execution_id and status='authority_initializing';
 return result;
end $$;

comment on function public.prepare_rollover_authorities_authenticated(jsonb) is
'Prepares three canonical authority domains before advancing the execution forward to authority_ready; no reverse state transition is used.';

commit;
