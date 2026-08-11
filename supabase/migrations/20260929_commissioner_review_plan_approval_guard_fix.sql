begin;

-- Align the commissioner-review immutability guard with the current
-- rollover_execution_plans schema. Reviews become immutable once the
-- canonical plan is approved for execution.
create or replace function public.enforce_commissioner_review_state()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare legal boolean:=false;
begin
 if tg_op='UPDATE' then
  if old.review_state='executed' then raise exception 'Executed commissioner review is immutable'; end if;
  if exists(select 1 from public.rollover_execution_plans p where p.rollover_execution_id=old.rollover_execution_id and p.approved_for_execution=true) and (new.review_state,new.outcome,new.evidence) is distinct from (old.review_state,old.outcome,old.evidence) then raise exception 'Commissioner review cannot change after final plan approval'; end if;
  legal:=new.review_state=old.review_state
   or (old.review_state='pending' and new.review_state in ('under_review','cancelled'))
   or (old.review_state='under_review' and new.review_state in ('evidence_required','decision_ready','blocked','cancelled'))
   or (old.review_state='evidence_required' and new.review_state in ('under_review','blocked','cancelled'))
   or (old.review_state='decision_ready' and new.review_state in ('approved','rejected','blocked','cancelled'))
   or (old.review_state in ('approved','rejected') and new.review_state in ('superseded','executed'))
   or (old.review_state='superseded' and new.review_state in ('under_review','cancelled'))
   or (old.review_state='blocked' and new.review_state in ('under_review','cancelled'));
  if not legal then raise exception 'Illegal commissioner review transition: % -> %',old.review_state,new.review_state; end if;
  if new.revision_number<old.revision_number then raise exception 'Commissioner review revision cannot decrease'; end if;
 end if;
 if new.review_state in ('approved','rejected') and new.outcome is null then raise exception 'Final review state requires an outcome'; end if;
 if new.outcome is not null and not public.commissioner_review_outcome_allowed(new.review_type,new.outcome) then raise exception 'Outcome is not allowed for review type'; end if;
 return new;
end $$;

comment on function public.enforce_commissioner_review_state() is
'Enforces commissioner-review transitions and prevents material changes after a plan is approved for execution.';

commit;
