-- Phase 3B.5D: deterministic commissioner-review engine.
-- Schema and functions only; creates no execution, review, authority, plan, or domain row.

alter table public.rollover_commissioner_reviews add column if not exists review_state text not null default 'pending';
alter table public.rollover_commissioner_reviews add column if not exists outcome text;
alter table public.rollover_commissioner_reviews add column if not exists revision_number integer not null default 0;
alter table public.rollover_commissioner_reviews add column if not exists evidence_fingerprint text;
alter table public.rollover_commissioner_reviews add column if not exists review_fingerprint text;
alter table public.rollover_commissioner_reviews add column if not exists request_fingerprint text;
alter table public.rollover_commissioner_reviews add column if not exists dead_cap_event_id uuid references public.contract_events(id);
alter table public.rollover_commissioner_reviews add column if not exists publication_reference text;
alter table public.rollover_commissioner_reviews add column if not exists retained_agreement_id uuid references public.contract_agreements(id);
alter table public.rollover_commissioner_reviews add column if not exists superseded_at timestamptz;
alter table public.rollover_commissioner_reviews add column if not exists superseded_by uuid references auth.users(id);

do $$ begin
 if not exists(select 1 from pg_constraint where conname='rollover_reviews_review_state_check') then
  alter table public.rollover_commissioner_reviews add constraint rollover_reviews_review_state_check check(review_state in ('pending','under_review','evidence_required','decision_ready','approved','rejected','superseded','blocked','cancelled','executed'));
 end if;
 if not exists(select 1 from pg_constraint where conname='rollover_reviews_outcome_check') then
  alter table public.rollover_commissioner_reviews add constraint rollover_reviews_outcome_check check(outcome is null or outcome in ('preserve_active_liability','approve_publication','reject_publication','approve_termination','reject_termination','approve_dead_cap','reject_dead_cap','retain_contract','require_identity_resolution','require_contract_resolution','require_waiver_resolution','require_rookie_draft_resolution','return_to_owner','blocked','cancelled'));
 end if;
 if not exists(select 1 from pg_constraint where conname='rollover_reviews_revision_number_check') then
  alter table public.rollover_commissioner_reviews add constraint rollover_reviews_revision_number_check check(revision_number>=0);
 end if;
 if not exists(select 1 from pg_constraint where conname='rollover_reviews_fingerprint_format_check') then
  alter table public.rollover_commissioner_reviews add constraint rollover_reviews_fingerprint_format_check check((evidence_fingerprint is null or evidence_fingerprint~'^[0-9a-f]{64}$') and (review_fingerprint is null or review_fingerprint~'^[0-9a-f]{64}$') and (request_fingerprint is null or request_fingerprint~'^[0-9a-f]{64}$'));
 end if;
end $$;

create index if not exists rollover_reviews_state_idx on public.rollover_commissioner_reviews(rollover_execution_id,review_state,review_type);
create index if not exists rollover_reviews_outcome_idx on public.rollover_commissioner_reviews(league_id,outcome) where outcome is not null;

create or replace function public.commissioner_review_outcome_allowed(p_type text,p_outcome text)
returns boolean language sql immutable set search_path=pg_catalog,public as $$
 select case p_type
  when 'active_off_roster_liability' then p_outcome=any(array['preserve_active_liability','approve_termination','reject_termination','retain_contract','require_contract_resolution','blocked','cancelled'])
  when 'expired_unrostered_publication_candidate' then p_outcome=any(array['approve_publication','reject_publication','require_identity_resolution','require_contract_resolution','require_waiver_resolution','require_rookie_draft_resolution','blocked','cancelled'])
  when 'owner_escalation' then p_outcome=any(array['retain_contract','approve_termination','reject_termination','approve_dead_cap','reject_dead_cap','return_to_owner','require_contract_resolution','blocked','cancelled'])
  when 'identity_conflict' then p_outcome=any(array['require_identity_resolution','blocked','cancelled'])
  when 'waiver_conflict' then p_outcome=any(array['require_waiver_resolution','blocked','cancelled'])
  when 'rookie_draft_conflict' then p_outcome=any(array['require_rookie_draft_resolution','blocked','cancelled'])
  when 'contract_conflict' then p_outcome=any(array['require_contract_resolution','retain_contract','approve_termination','reject_termination','approve_dead_cap','reject_dead_cap','blocked','cancelled'])
  else false end
$$;

create or replace function public.validate_commissioner_review_evidence(
 p_review public.rollover_commissioner_reviews,p_outcome text,p_evidence jsonb,p_termination_event uuid,p_dead_cap_event uuid,p_publication_reference text,p_retained_agreement uuid
) returns void language plpgsql stable set search_path=pg_catalog,public as $$
declare agreement public.contract_agreements%rowtype; amount numeric;
begin
 if not public.commissioner_review_outcome_allowed(p_review.review_type,p_outcome) then raise exception 'Outcome is not allowed for review type'; end if;
 select * into agreement from public.contract_agreements where id=p_review.agreement_id;
 if p_outcome='approve_termination' then
  if p_termination_event is null or nullif(p_evidence->>'termination_authority','') is null or nullif(p_evidence->>'termination_reason','') is null or coalesce((p_evidence->>'effective_season')::integer,-1)<>p_review.target_season or coalesce((p_evidence->>'validated_agreement_state')::boolean,false)=false then raise exception 'Complete validated termination evidence required'; end if;
  if not exists(select 1 from public.contract_events e where e.id=p_termination_event and e.contract_id=p_review.agreement_id and e.player_id=p_review.player_id and e.league_team_id=p_review.league_team_id and e.event_type in ('released','voided','superseded')) then raise exception 'Termination event does not match source agreement'; end if;
 end if;
 amount:=coalesce((p_evidence->>'dead_cap_amount')::numeric,0);
 if p_outcome='approve_dead_cap' or amount>0 then
  if coalesce(p_dead_cap_event,p_termination_event) is null or nullif(p_evidence->>'penalty_rule','') is null or nullif(p_evidence->>'dead_cap_calculation_fingerprint','') is null or coalesce((p_evidence->>'dead_cap_season')::integer,-1)<>p_review.target_season or amount<=0 then raise exception 'Qualifying event and complete dead-cap calculation evidence required'; end if;
  if p_dead_cap_event is not null and not exists(select 1 from public.contract_events e where e.id=p_dead_cap_event and e.contract_id=p_review.agreement_id and e.event_type='dead_cap_created') then raise exception 'Dead-cap event does not qualify for source agreement'; end if;
 end if;
 if p_outcome='approve_publication' then
  if agreement.status='active' or coalesce((p_evidence->>'publication_eligible')::boolean,false)=false or coalesce((p_evidence->>'acquisition_blocked')::boolean,false) or coalesce((p_evidence->>'second_agreement_blocked')::boolean,false) then raise exception 'Publication eligibility or conflict validation failed'; end if;
  if p_publication_reference is null then raise exception 'Planned publication reference required'; end if;
 end if;
 if p_outcome in ('retain_contract','preserve_active_liability') then
  if agreement.id is null or (p_retained_agreement is not null and p_retained_agreement<>agreement.id) then raise exception 'Retention must reference the valid source agreement'; end if;
  if coalesce((p_evidence->>'duplicate_active_agreement')::boolean,false) then raise exception 'Retention conflicts with duplicate active agreement'; end if;
 end if;
 if p_review.review_type='active_off_roster_liability' and p_outcome='approve_publication' then raise exception 'Active off-roster liability cannot be published'; end if;
end $$;

create or replace function public.enforce_commissioner_review_state()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare legal boolean:=false;
begin
 if tg_op='UPDATE' then
  if old.review_state='executed' then raise exception 'Executed commissioner review is immutable'; end if;
  if exists(select 1 from public.rollover_execution_plans p where p.rollover_execution_id=old.rollover_execution_id and p.status='approved') and (new.review_state,new.outcome,new.evidence) is distinct from (old.review_state,old.outcome,old.evidence) then raise exception 'Commissioner review cannot change after final plan approval'; end if;
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

drop trigger if exists rollover_commissioner_review_state_guard on public.rollover_commissioner_reviews;
create trigger rollover_commissioner_review_state_guard before insert or update on public.rollover_commissioner_reviews for each row execute function public.enforce_commissioner_review_state();

create or replace function public.initialize_rollover_commissioner_reviews_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; x public.rollover_executions%rowtype; k text; supplied jsonb; c jsonb; material jsonb; fp text; retry jsonb; result jsonb; review public.rollover_commissioner_reviews%rowtype; actual integer;
begin
 actor:=public.require_authenticated_user(); k:=nullif(btrim(p_request->>'idempotency_key'),''); if k is null then raise exception 'idempotency_key required'; end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update; if x.id is null then raise exception 'Execution not found'; end if; perform public.require_commissioner_authority(x.league_id);
 if x.status<>'decision_window_closed' then raise exception 'Commissioner reviews require a closed owner decision window'; end if;
 supplied:=p_request->'commissioner_population'; if jsonb_typeof(supplied)<>'array' or jsonb_array_length(supplied)=0 then raise exception 'Commissioner population required'; end if;
 if p_request->>'calculated_commissioner_population_fingerprint' is distinct from p_request->>'expected_commissioner_population_fingerprint' then raise exception 'Commissioner population fingerprint mismatch'; end if;
 material:=jsonb_build_object('operation','initialize_commissioner_reviews','execution_id',x.id::text,'population',supplied,'expected_population_fingerprint',p_request->>'expected_commissioner_population_fingerprint','actor',actor::text,'material_metadata',coalesce(p_request->'material_metadata','null'::jsonb));
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'initialize_commissioner_reviews',k,fp); if retry is not null then return retry; end if;
 for c in select value from jsonb_array_elements(supplied) loop
  if c->>'league_id'<>x.league_id::text or (c->>'source_season')::integer<>x.source_season or (c->>'target_season')::integer<>x.target_season then raise exception 'Commissioner case boundary mismatch'; end if;
  if c->>'review_type' not in ('active_off_roster_liability','expired_unrostered_publication_candidate','owner_escalation','identity_conflict','waiver_conflict','rookie_draft_conflict','contract_conflict') then raise exception 'Unsupported commissioner review type'; end if;
  if c->>'review_type' in ('active_off_roster_liability','expired_unrostered_publication_candidate') and not exists(select 1 from public.contract_agreements a where a.id=(c->>'agreement_id')::uuid and a.league_id=x.league_id and a.player_id=c->>'player_id' and a.league_team_id=(c->>'league_team_id')::uuid and ((c->>'review_type'='active_off_roster_liability' and a.status='active') or (c->>'review_type'='expired_unrostered_publication_candidate' and a.status='expired'))) then raise exception 'Commissioner case contract evidence mismatch'; end if;
  insert into public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,league_team_id,review_type,review_status,review_state,execution_status,evidence,evidence_fingerprint,review_fingerprint,revision_number,metadata)
  values(x.id,x.league_id,x.source_season,x.target_season,c->>'player_id',(c->>'agreement_id')::uuid,(c->>'league_team_id')::uuid,c->>'review_type','review_required','pending','pending',coalesce(c->'evidence','{}'),c->>'evidence_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('execution',x.id,'player',c->>'player_id','agreement',c->>'agreement_id','type',c->>'review_type','state','pending','evidence_fingerprint',c->>'evidence_fingerprint')),0,jsonb_build_object('population_fingerprint',p_request->>'expected_commissioner_population_fingerprint'))
  on conflict(rollover_execution_id,player_id,review_type) do update set evidence_fingerprint=coalesce(public.rollover_commissioner_reviews.evidence_fingerprint,excluded.evidence_fingerprint),review_fingerprint=coalesce(public.rollover_commissioner_reviews.review_fingerprint,excluded.review_fingerprint),metadata=public.rollover_commissioner_reviews.metadata||excluded.metadata where public.rollover_commissioner_reviews.review_state='pending' returning * into review;
 end loop;
 insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,new_status,performed_by,reason,evidence,idempotency_key,metadata)
 select r.id,x.id,'review_initialized','pending',actor,'commissioner population frozen',r.evidence,format('commissioner-initial:%s:%s',x.id,r.id),jsonb_build_object('review_fingerprint',r.review_fingerprint)
 from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id on conflict(idempotency_key) do nothing;
 select count(*) into actual from public.rollover_commissioner_reviews where rollover_execution_id=x.id;
 if actual<>jsonb_array_length(supplied) then raise exception 'Frozen commissioner review count mismatch'; end if;
 result:=jsonb_build_object('execution_id',x.id,'review_count',actual,'population_fingerprint',p_request->>'expected_commissioner_population_fingerprint');
 return public.record_rollover_operation(x.league_id,x.id,'initialize_commissioner_reviews',k,fp,actor,'authenticated_commissioner',x.id,result,'{}');
end $$;

create or replace function public.begin_rollover_commissioner_review_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; r public.rollover_commissioner_reviews%rowtype; k text; material jsonb; fp text; retry jsonb; result jsonb; prior text; next_revision integer;
begin
 actor:=public.require_authenticated_user();k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null then raise exception 'idempotency_key required'; end if;
 select * into r from public.rollover_commissioner_reviews where id=(p_request->>'review_id')::uuid for update;if r.id is null then raise exception 'Review not found';end if;perform public.require_commissioner_authority(r.league_id);
 material:=jsonb_build_object('operation','begin_commissioner_review','execution_id',r.rollover_execution_id::text,'review_id',r.id::text,'actor',actor::text,'expected_revision_number',(p_request->>'expected_revision_number')::integer,'expected_review_fingerprint',p_request->>'expected_review_fingerprint','reason',p_request->>'reason');fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(r.league_id,'begin_commissioner_review',k,fp);if retry is not null then return retry;end if;
 if r.revision_number<>(p_request->>'expected_revision_number')::integer or r.review_fingerprint is distinct from p_request->>'expected_review_fingerprint' then raise exception 'Stale commissioner review';end if;prior:=r.review_state;next_revision:=r.revision_number+1;
 update public.rollover_commissioner_reviews set review_state='under_review',review_status='decision_pending',revision_number=next_revision,decision_by=actor,updated_at=now(),review_fingerprint=public.rollover_material_fingerprint(jsonb_build_object('review',r.id,'state','under_review','revision',next_revision,'actor',actor)) where id=r.id returning * into r;
 insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,prior_status,new_status,performed_by,reason,evidence,idempotency_key,metadata) values(r.id,r.rollover_execution_id,'review_started',prior,'under_review',actor,p_request->>'reason',coalesce(p_request->'evidence','{}'),k,jsonb_build_object('revision_number',next_revision,'request_fingerprint',fp));
 result:=jsonb_build_object('review',to_jsonb(r));return public.record_rollover_operation(r.league_id,r.rollover_execution_id,'begin_commissioner_review',k,fp,actor,'authenticated_commissioner',r.id,result,'{}');
end $$;

create or replace function public.submit_rollover_commissioner_review_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid;r public.rollover_commissioner_reviews%rowtype;k text;outcome_name text;material jsonb;fp text;retry jsonb;result jsonb;final_state text;ready_revision integer;final_revision integer;evidence_value jsonb;
begin
 actor:=public.require_authenticated_user();k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null then raise exception 'idempotency_key required';end if;outcome_name:=p_request->>'proposed_outcome';evidence_value:=coalesce(p_request->'evidence','{}');
 select * into r from public.rollover_commissioner_reviews where id=(p_request->>'review_id')::uuid for update;if r.id is null then raise exception 'Review not found';end if;perform public.require_commissioner_authority(r.league_id);
 material:=jsonb_build_object('operation','submit_commissioner_review','execution_id',r.rollover_execution_id::text,'review_id',r.id::text,'actor',actor::text,'outcome',outcome_name,'reason',p_request->>'reason','evidence',evidence_value,'termination_event_id',public.canonical_optional_uuid(p_request->>'termination_event_id'),'dead_cap_event_id',public.canonical_optional_uuid(p_request->>'dead_cap_event_id'),'publication_reference',p_request->>'publication_reference','retained_agreement_id',public.canonical_optional_uuid(p_request->>'retained_agreement_id'),'expected_revision_number',(p_request->>'expected_revision_number')::integer,'expected_review_fingerprint',p_request->>'expected_review_fingerprint');fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(r.league_id,'submit_commissioner_review',k,fp);if retry is not null then return retry;end if;
 if nullif(btrim(p_request->>'reason'),'') is null then raise exception 'Decision reason required';end if;if r.revision_number<>(p_request->>'expected_revision_number')::integer or r.review_fingerprint is distinct from p_request->>'expected_review_fingerprint' then raise exception 'Stale commissioner review';end if;if r.review_state not in ('under_review','evidence_required','superseded') then raise exception 'Review is not ready for decision validation';end if;
 perform public.validate_commissioner_review_evidence(r,outcome_name,evidence_value,(p_request->>'termination_event_id')::uuid,(p_request->>'dead_cap_event_id')::uuid,p_request->>'publication_reference',(p_request->>'retained_agreement_id')::uuid);
 final_state:=case when outcome_name in ('reject_publication','reject_termination','reject_dead_cap') then 'rejected' when outcome_name in ('blocked','require_identity_resolution','require_contract_resolution','require_waiver_resolution','require_rookie_draft_resolution') then 'blocked' else 'approved' end;
 ready_revision:=r.revision_number+1;update public.rollover_commissioner_reviews set review_state='decision_ready',review_status=case when outcome_name in ('preserve_active_liability','retain_contract') then 'retain_liability' when outcome_name='approve_publication' then 'approve_publication_hold' when outcome_name='approve_termination' then 'approve_termination' else 'block_publication' end,approved_action=outcome_name,revision_number=ready_revision,action_validated=true,evidence_complete=true,updated_at=now() where id=r.id returning * into r;
 insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,prior_status,new_status,performed_by,reason,evidence,idempotency_key,metadata) values(r.id,r.rollover_execution_id,'review_validated','under_review','decision_ready',actor,p_request->>'reason',evidence_value,k||':validated',jsonb_build_object('revision_number',ready_revision,'request_fingerprint',fp));
 final_revision:=ready_revision+1;update public.rollover_commissioner_reviews set review_state=final_state,review_status='action_validated',outcome=outcome_name,approved_action=outcome_name,decision_by=actor,decision_at=now(),evidence=evidence_value,qualifying_termination_event_id=(p_request->>'termination_event_id')::uuid,dead_cap_event_id=(p_request->>'dead_cap_event_id')::uuid,publication_reference=p_request->>'publication_reference',retained_agreement_id=(p_request->>'retained_agreement_id')::uuid,revision_number=final_revision,request_fingerprint=fp,evidence_fingerprint=public.rollover_material_fingerprint(evidence_value),review_fingerprint=public.rollover_material_fingerprint(jsonb_build_object('review',r.id,'state',final_state,'outcome',outcome_name,'revision',final_revision,'evidence',evidence_value,'actor',actor)),updated_at=now() where id=r.id returning * into r;
 insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,prior_status,new_status,performed_by,reason,evidence,idempotency_key,metadata) values(r.id,r.rollover_execution_id,'review_decided','decision_ready',final_state,actor,p_request->>'reason',evidence_value,k,jsonb_build_object('outcome',outcome_name,'revision_number',final_revision,'request_fingerprint',fp,'review_fingerprint',r.review_fingerprint));
 result:=jsonb_build_object('review',to_jsonb(r));return public.record_rollover_operation(r.league_id,r.rollover_execution_id,'submit_commissioner_review',k,fp,actor,'authenticated_commissioner',r.id,result,'{}');
end $$;

create or replace function public.supersede_rollover_commissioner_review_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid;r public.rollover_commissioner_reviews%rowtype;k text;material jsonb;fp text;retry jsonb;result jsonb;next_revision integer;
begin actor:=public.require_authenticated_user();k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'Supersession reason and idempotency key required';end if;select * into r from public.rollover_commissioner_reviews where id=(p_request->>'review_id')::uuid for update;if r.id is null then raise exception 'Review not found';end if;perform public.require_commissioner_authority(r.league_id);
 material:=jsonb_build_object('operation','supersede_commissioner_review','execution_id',r.rollover_execution_id::text,'review_id',r.id::text,'actor',actor::text,'reason',p_request->>'reason','expected_revision_number',(p_request->>'expected_revision_number')::integer,'expected_review_fingerprint',p_request->>'expected_review_fingerprint');fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(r.league_id,'supersede_commissioner_review',k,fp);if retry is not null then return retry;end if;
 if r.review_state not in ('approved','rejected') or r.revision_number<>(p_request->>'expected_revision_number')::integer or r.review_fingerprint is distinct from p_request->>'expected_review_fingerprint' then raise exception 'Only the current final review may be superseded';end if;if exists(select 1 from public.rollover_execution_plans p where p.rollover_execution_id=r.rollover_execution_id and p.status='approved') then raise exception 'Review cannot be superseded after final plan approval';end if;next_revision:=r.revision_number+1;
 update public.rollover_commissioner_reviews set review_state='superseded',review_status='decision_pending',outcome=null,approved_action=null,superseded_at=now(),superseded_by=actor,revision_number=next_revision,request_fingerprint=fp,review_fingerprint=public.rollover_material_fingerprint(jsonb_build_object('review',r.id,'state','superseded','revision',next_revision,'actor',actor,'reason',p_request->>'reason')),updated_at=now() where id=r.id returning * into r;
 insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,prior_status,new_status,performed_by,reason,evidence,idempotency_key,metadata) values(r.id,r.rollover_execution_id,'review_superseded','final','superseded',actor,p_request->>'reason',r.evidence,k,jsonb_build_object('revision_number',next_revision,'request_fingerprint',fp));result:=jsonb_build_object('review',to_jsonb(r));return public.record_rollover_operation(r.league_id,r.rollover_execution_id,'supersede_commissioner_review',k,fp,actor,'authenticated_commissioner',r.id,result,'{}');end $$;

create or replace function public.cancel_rollover_commissioner_review_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid;r public.rollover_commissioner_reviews%rowtype;k text;material jsonb;fp text;retry jsonb;result jsonb;prior text;next_revision integer;
begin actor:=public.require_authenticated_user();k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'Cancellation reason and idempotency key required';end if;select * into r from public.rollover_commissioner_reviews where id=(p_request->>'review_id')::uuid for update;if r.id is null then raise exception 'Review not found';end if;perform public.require_commissioner_authority(r.league_id);
 material:=jsonb_build_object('operation','cancel_commissioner_review','execution_id',r.rollover_execution_id::text,'review_id',r.id::text,'actor',actor::text,'reason',p_request->>'reason','expected_revision_number',(p_request->>'expected_revision_number')::integer,'expected_review_fingerprint',p_request->>'expected_review_fingerprint');fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(r.league_id,'cancel_commissioner_review',k,fp);if retry is not null then return retry;end if;if r.review_state in ('approved','rejected','executed','cancelled') then raise exception 'Final review cannot be cancelled';end if;if r.revision_number<>(p_request->>'expected_revision_number')::integer or r.review_fingerprint is distinct from p_request->>'expected_review_fingerprint' then raise exception 'Stale commissioner review';end if;prior:=r.review_state;next_revision:=r.revision_number+1;
 update public.rollover_commissioner_reviews set review_state='cancelled',review_status='cancelled',outcome='cancelled',execution_status='cancelled',revision_number=next_revision,request_fingerprint=fp,review_fingerprint=public.rollover_material_fingerprint(jsonb_build_object('review',r.id,'state','cancelled','revision',next_revision,'actor',actor,'reason',p_request->>'reason')),updated_at=now() where id=r.id returning * into r;insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,prior_status,new_status,performed_by,reason,evidence,idempotency_key,metadata) values(r.id,r.rollover_execution_id,'review_cancelled',prior,'cancelled',actor,p_request->>'reason',r.evidence,k,jsonb_build_object('revision_number',next_revision,'request_fingerprint',fp));result:=jsonb_build_object('review',to_jsonb(r));return public.record_rollover_operation(r.league_id,r.rollover_execution_id,'cancel_commissioner_review',k,fp,actor,'authenticated_commissioner',r.id,result,'{}');end $$;

revoke all on function public.initialize_rollover_commissioner_reviews_authenticated(jsonb),public.begin_rollover_commissioner_review_authenticated(jsonb),public.submit_rollover_commissioner_review_authenticated(jsonb),public.supersede_rollover_commissioner_review_authenticated(jsonb),public.cancel_rollover_commissioner_review_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.initialize_rollover_commissioner_reviews_authenticated(jsonb),public.begin_rollover_commissioner_review_authenticated(jsonb),public.submit_rollover_commissioner_review_authenticated(jsonb),public.supersede_rollover_commissioner_review_authenticated(jsonb),public.cancel_rollover_commissioner_review_authenticated(jsonb) to authenticated;
revoke all on function public.commissioner_review_outcome_allowed(text,text),public.validate_commissioner_review_evidence(public.rollover_commissioner_reviews,text,jsonb,uuid,uuid,text,uuid) from public,anon,authenticated,service_role;

comment on function public.initialize_rollover_commissioner_reviews_authenticated(jsonb) is 'Atomically freezes deterministic commissioner cases only after the owner decision window closes.';
comment on function public.submit_rollover_commissioner_review_authenticated(jsonb) is 'Authenticated commissioner decision; validates evidence and appends immutable events without executing domain actions.';
