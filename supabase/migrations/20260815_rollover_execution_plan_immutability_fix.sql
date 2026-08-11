begin;

-- Additive Phase 3B.5H correction. The deployed table has generated_at but no
-- created_at. Compare the complete row after removing only the documented
-- control-state columns so every plan identity/evidence field remains frozen.
create or replace function public.enforce_phase3b5h_plan_immutability()
returns trigger
language plpgsql
set search_path=pg_catalog,public
as $$
begin
 if tg_op='DELETE' then
  raise exception 'execution plans cannot be deleted';
 end if;

 if old.plan_status in ('superseded','cancelled') then
  raise exception 'terminal execution plan is immutable';
 end if;

 if (to_jsonb(new)-array['plan_status','approved_for_execution','approved_by','approved_at','superseded_at','superseded_by','cancelled_at'])
    is distinct from
    (to_jsonb(old)-array['plan_status','approved_for_execution','approved_by','approved_at','superseded_at','superseded_by','cancelled_at']) then
  raise exception 'execution plan material is immutable';
 end if;

 if old.plan_status='approved_for_execution' then
  if new.plan_status not in ('valid','cancelled','superseded') then
   raise exception 'approved execution plan is immutable';
  end if;
  if exists(
   select 1 from public.rollover_execution_plan_approvals a
   where a.execution_plan_id=old.id and a.approval_status='approved'
  ) then
   raise exception 'revoke approval before changing approved plan';
  end if;
 elsif old.plan_status='valid' then
  if new.plan_status not in ('approved_for_execution','cancelled','superseded') then
   raise exception 'illegal execution plan transition';
  end if;
 elsif old.plan_status in ('generated','blocked') then
  if new.plan_status not in ('cancelled','superseded') then
   raise exception 'illegal execution plan transition';
  end if;
 else
  raise exception 'illegal execution plan transition';
 end if;

 if new.plan_status='approved_for_execution' and
    (not new.approved_for_execution or new.approved_by is null or new.approved_at is null) then
  raise exception 'approved execution plan requires approval audit state';
 end if;
 if new.plan_status<>'approved_for_execution' and
    (new.approved_for_execution or new.approved_by is not null or new.approved_at is not null) then
  raise exception 'non-approved execution plan cannot retain approval audit state';
 end if;

 return new;
end
$$;

drop trigger if exists rollover_execution_plan_immutability on public.rollover_execution_plans;
create trigger rollover_execution_plan_immutability
before update or delete on public.rollover_execution_plans
for each row execute function public.enforce_phase3b5h_plan_immutability();

revoke all on function public.enforce_phase3b5h_plan_immutability() from public,anon,authenticated;

comment on function public.enforce_phase3b5h_plan_immutability() is
 'Phase 3B.5H plan guard: permits documented control transitions while freezing all plan material; corrected to use the actual table column set.';

commit;
