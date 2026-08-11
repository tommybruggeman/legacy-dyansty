begin;

-- Align the owner-decision validation trigger with the certified rollover
-- effective clock. Production continues to use real database time because
-- rollover_effective_now() only returns an override in the sentinel-approved
-- disposable environment.
create or replace function public.validate_rollover_owner_decision() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; ok boolean:=false; begin select * into x from public.rollover_executions where id=new.rollover_execution_id;
 if x.id is null or x.league_id<>new.league_id or x.source_season<>new.source_season or x.target_season<>new.target_season then raise exception 'Owner decision boundary mismatch.'; end if;
 if new.deadline is distinct from x.owner_deadline then raise exception 'Owner decision deadline must match parent execution.'; end if;
 if new.decision_status='no_response' and (x.owner_deadline is null or public.rollover_effective_now()<x.owner_deadline) then raise exception 'No-response cannot be assigned before deadline.'; end if;
 if tg_op='UPDATE' then
 if old.decision_status in ('executed_retained','commissioner_hold') then raise exception 'Executed owner outcome is immutable.'; end if;
  if old.decision_status='commissioner_review_requested' and new.decision_status='no_response' then raise exception 'Commissioner review cannot become no-response.'; end if;
  ok:=new.decision_status=old.decision_status or (old.decision_status='waiting_for_owner' and new.decision_status in ('recontract_submitted','decline_submitted','commissioner_review_requested','no_response','cancelled')) or (old.decision_status='recontract_submitted' and new.decision_status in ('recontract_invalid','recontract_validated','cancelled')) or (old.decision_status='recontract_invalid' and new.decision_status in ('waiting_for_owner','blocked','cancelled')) or (old.decision_status='recontract_validated' and new.decision_status in ('planned_retention','blocked','cancelled')) or (old.decision_status='decline_submitted' and new.decision_status in ('planned_release','cancelled')) or (old.decision_status='no_response' and new.decision_status='planned_release') or (old.decision_status in ('planned_retention','planned_release') and new.decision_status in ('execution_ready','blocked','cancelled')) or (old.decision_status='execution_ready' and new.decision_status in ('executed_retained','executed_released','blocked')) or (old.decision_status='executed_released' and new.decision_status='commissioner_hold') or (old.decision_status='blocked' and new.decision_status in ('execution_ready','cancelled'));
  if not ok then raise exception 'Illegal owner-decision transition: % -> %',old.decision_status,new.decision_status; end if;
 end if; new.updated_at=now(); return new; end $$;

comment on function public.validate_rollover_owner_decision() is
'Validates rollover owner decisions; no-response deadline checks use the production-inert rollover effective clock.';

commit;
