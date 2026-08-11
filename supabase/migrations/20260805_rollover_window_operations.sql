-- Phase 3B.5C: controlled rollover preflight/window operations.
-- Defines functions only. Creates no execution, notice, decision, review, plan,
-- lock, authority, contract, roster, cap, season, publication, or transaction data.

create unique index if not exists rollover_executions_creation_key_uidx on public.rollover_executions((metadata->>'creation_idempotency_key')) where metadata ? 'creation_idempotency_key';
create unique index if not exists rollover_executions_notice_key_uidx on public.rollover_executions((metadata->>'notice_idempotency_key')) where metadata ? 'notice_idempotency_key';
create unique index if not exists rollover_executions_close_key_uidx on public.rollover_executions((metadata->>'close_idempotency_key')) where metadata ? 'close_idempotency_key';

create or replace function public.create_rollover_execution(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare p public.league_rollover_policies%rowtype; s public.league_seasons%rowtype; t public.league_seasons%rowtype; x public.rollover_executions%rowtype; k text; begin
 k=nullif(p_request->>'idempotency_key',''); if k is null then raise exception 'idempotency_key required'; end if;
 select * into x from public.rollover_executions where metadata->>'creation_idempotency_key'=k;
 if found then if x.league_id::text<>p_request->>'league_id' or x.source_season<>(p_request->>'source_season')::int or x.target_season<>(p_request->>'target_season')::int or x.policy_fingerprint<>p_request->>'expected_policy_fingerprint' or x.preflight_fingerprint<>p_request->>'expected_preflight_fingerprint' then raise exception 'Idempotency key conflicts with existing execution'; end if; return jsonb_build_object('idempotent',true,'execution',to_jsonb(x)); end if;
 select * into p from public.league_rollover_policies where id=(p_request->>'policy_id')::uuid for share;
 select * into s from public.league_seasons where league_id=(p_request->>'league_id')::uuid and season=(p_request->>'source_season')::int for share;
 select * into t from public.league_seasons where league_id=(p_request->>'league_id')::uuid and season=(p_request->>'target_season')::int for share;
 if p.id is null or p.status<>'approved' or p.effective_at is not null or p.fingerprint<>p_request->>'expected_policy_fingerprint' or p.league_id<>s.league_id or p.source_season<>s.season or p.target_season<>t.season then raise exception 'Approved policy mismatch'; end if;
 if not s.is_active or s.status<>'active' or t.is_active or t.status<>'scheduled' or t.season<>s.season+1 then raise exception 'Season authority mismatch'; end if;
 if nullif(p_request->>'expected_preflight_fingerprint','') is null or nullif(p_request->>'before_state_fingerprint','') is null then raise exception 'Complete preflight fingerprints required'; end if;
 if exists(select 1 from public.rollover_executions where league_id=p.league_id and source_season=p.source_season and target_season=p.target_season and status<>'cancelled') then raise exception 'Non-cancelled rollover execution already exists'; end if;
 insert into public.rollover_executions(league_id,source_season,target_season,policy_id,policy_fingerprint,version,status,approval_status,preflight_fingerprint,before_state_fingerprint,metadata)
 values(p.league_id,p.source_season,p.target_season,p.id,p.fingerprint,1,'preflight_ready','not_required',p_request->>'expected_preflight_fingerprint',p_request->>'before_state_fingerprint',coalesce(p_request->'metadata','{}')||jsonb_build_object('creation_idempotency_key',k,'requested_by',p_request->>'requested_by')) returning * into x;
 return jsonb_build_object('idempotent',false,'execution',to_jsonb(x)); end $$;

create or replace function public.open_rollover_notice_window(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; p public.league_rollover_policies%rowtype; n timestamptz; d timestamptz; k text; supplied jsonb; c jsonb; inserted_count int; begin
 k=nullif(p_request->>'idempotency_key',''); n=(p_request->>'official_notice_timestamp')::timestamptz; supplied=p_request->'owner_population';
 if k is null or n is null or jsonb_typeof(supplied)<>'array' then raise exception 'Notice, idempotency key, and owner population required'; end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'Execution not found'; end if;
 if x.metadata->>'notice_idempotency_key'=k then return jsonb_build_object('idempotent',true,'execution',to_jsonb(x),'owner_count',(select count(*) from public.rollover_owner_decisions where rollover_execution_id=x.id)); end if;
 if x.status<>'preflight_ready' or x.preflight_fingerprint<>p_request->>'expected_preflight_fingerprint' then raise exception 'Execution state or preflight fingerprint mismatch'; end if;
 select * into p from public.league_rollover_policies where id=x.policy_id for share;
 if p.status<>'approved' or p.effective_at is not null or p.fingerprint<>x.policy_fingerprint then raise exception 'Policy is no longer approved and inactive'; end if;
 if nullif(p_request->>'expected_owner_population_fingerprint','') is null or p_request->>'calculated_owner_population_fingerprint'<>p_request->>'expected_owner_population_fingerprint' then raise exception 'Owner population fingerprint mismatch'; end if;
 if jsonb_array_length(supplied)<>(p_request->>'expected_owner_count')::int or jsonb_array_length(supplied)=0 then raise exception 'Owner population count differs from frozen preflight'; end if; d=n+interval '7 days';
 update public.rollover_executions set status='notice_open',notice_timestamp=n,owner_deadline=d,decision_population_fingerprint=p_request->>'expected_owner_population_fingerprint',metadata=metadata||jsonb_build_object('notice_idempotency_key',k,'notice_requested_by',p_request->>'requested_by') where id=x.id returning * into x;
 for c in select value from jsonb_array_elements(supplied) loop
  if c->>'league_id'<>x.league_id::text or (c->>'source_season')::int<>x.source_season or (c->>'target_season')::int<>x.target_season then raise exception 'Owner case boundary mismatch'; end if;
  if not exists(select 1 from public.contract_agreements a join public.season_roster_assignments r on r.sleeper_player_id=c->>'player_id' join public.league_seasons ls on ls.id=r.league_season_id where a.id=(c->>'agreement_id')::uuid and a.league_id=x.league_id and a.status='expired' and r.league_team_id=(c->>'league_team_id')::uuid and ls.league_id=x.league_id and ls.season=x.source_season) then raise exception 'Owner case is not supported by expired agreement and source roster evidence'; end if;
  insert into public.rollover_owner_decisions(rollover_execution_id,league_id,source_season,target_season,league_team_id,player_id,agreement_id,initial_roster_status,initial_roster_slot,decision_status,execution_status,deadline,evidence,metadata)
  values(x.id,x.league_id,x.source_season,x.target_season,(c->>'league_team_id')::uuid,c->>'player_id',(c->>'agreement_id')::uuid,c->>'rostered_status',c->>'roster_slot','waiting_for_owner','pending',d,coalesce(c->'evidence','{}'),jsonb_build_object('evidence_fingerprint',c->>'evidence_fingerprint','decision_fingerprint',c->>'evidence_fingerprint'));
 end loop;
 insert into public.rollover_owner_decision_revisions(owner_decision_id,rollover_execution_id,revision_number,new_status,changed_by,reason,evidence,idempotency_key)
 select od.id,x.id,1,'waiting_for_owner',(p_request->>'requested_by')::uuid,'population frozen',od.evidence,format('owner-initial:%s:%s',x.id,od.id) from public.rollover_owner_decisions od where od.rollover_execution_id=x.id;
 update public.rollover_executions set status='decision_window_open' where id=x.id returning * into x; select count(*) into inserted_count from public.rollover_owner_decisions where rollover_execution_id=x.id;
 return jsonb_build_object('idempotent',false,'execution',to_jsonb(x),'owner_count',inserted_count,'deadline',d); end $$;

create or replace function public.submit_rollover_owner_decision(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; d public.rollover_owner_decisions%rowtype; m record; prior record; k text; choice text; next_status text; rev int; begin
 k=nullif(p_request->>'idempotency_key','');choice=p_request->>'choice'; if k is null then raise exception 'idempotency_key required'; end if;
 select * into prior from public.rollover_owner_decision_revisions where idempotency_key=k;
 if found then if prior.owner_decision_id<>(p_request->>'owner_decision_id')::uuid or prior.new_choice is distinct from choice then raise exception 'Idempotency key payload conflict'; end if; select * into d from public.rollover_owner_decisions where id=prior.owner_decision_id; return jsonb_build_object('idempotent',true,'decision',to_jsonb(d),'revision',to_jsonb(prior)); end if;
 select * into d from public.rollover_owner_decisions where id=(p_request->>'owner_decision_id')::uuid for update; select * into x from public.rollover_executions where id=d.rollover_execution_id for share;
 if x.id is null or x.status<>'decision_window_open' or now()>=d.deadline or d.locked_at is not null or d.execution_status in ('executing','executed','cancelled') then raise exception 'Decision window is not mutable'; end if;
 if nullif(p_request->>'expected_decision_fingerprint','') is null or d.metadata->>'decision_fingerprint' is distinct from p_request->>'expected_decision_fingerprint' then raise exception 'Stale owner decision fingerprint'; end if;
 select * into m from public.league_memberships where league_id=d.league_id and user_id=(p_request->>'submitted_by')::uuid;
 if m is null or not(lower(m.role) in ('owner','co_owner','co-owner','commissioner','admin','host')) or (lower(m.role) not in ('commissioner','admin','host') and m.league_team_id<>d.league_team_id) then raise exception 'Owner authorization failed'; end if;
 select coalesce(max(revision_number),0)+1 into rev from public.rollover_owner_decision_revisions where owner_decision_id=d.id;
 if rev-1<>(p_request->>'expected_revision_number')::int then raise exception 'Stale owner decision revision'; end if;
 next_status=case choice when 'recontract' then 'recontract_submitted' when 'decline' then 'decline_submitted' when 'commissioner_review' then 'commissioner_review_requested' else null end; if next_status is null then raise exception 'Unsupported owner choice'; end if;
 if choice='recontract' and (nullif(p_request->>'recontract_agreement_id','') is null or nullif(p_request->>'recontract_event_id','') is null) then raise exception 'Recontract normalized references required'; end if;
 if choice<>'recontract' and (nullif(p_request->>'recontract_agreement_id','') is not null or nullif(p_request->>'recontract_event_id','') is not null) then raise exception 'Non-recontract choice cannot carry recontract references'; end if;
 insert into public.rollover_owner_decision_revisions(owner_decision_id,rollover_execution_id,revision_number,prior_status,new_status,prior_choice,new_choice,changed_by,reason,evidence,request_id,idempotency_key)
 values(d.id,x.id,rev,d.decision_status,next_status,d.owner_choice,choice,(p_request->>'submitted_by')::uuid,p_request->>'reason',coalesce(p_request->'evidence','{}'),p_request->>'request_id',k);
 update public.rollover_owner_decisions set decision_status=next_status,owner_choice=choice,submitted_by=(p_request->>'submitted_by')::uuid,submitted_at=now(),recontract_agreement_id=case when choice='recontract' then (p_request->>'recontract_agreement_id')::uuid else null end,recontract_event_id=case when choice='recontract' then (p_request->>'recontract_event_id')::uuid else null end,metadata=metadata||jsonb_build_object('decision_fingerprint',p_request->>'decision_fingerprint','revision_number',rev) where id=d.id returning * into d;
 if choice='commissioner_review' then insert into public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,league_team_id,review_type,review_status,execution_status,evidence,metadata) values(x.id,d.league_id,d.source_season,d.target_season,d.player_id,d.agreement_id,d.league_team_id,'owner_escalation','review_required','pending',coalesce(p_request->'evidence','{}'),jsonb_build_object('owner_decision_id',d.id)) on conflict(rollover_execution_id,player_id,review_type) do nothing; end if;
 return jsonb_build_object('idempotent',false,'decision',to_jsonb(d),'revision_number',rev); end $$;

create or replace function public.close_rollover_decision_window(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; d record; k text; rev int; next_status text; summary jsonb; begin
 k=nullif(p_request->>'idempotency_key','');select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.metadata->>'close_idempotency_key'=k and x.status='decision_window_closed' then return jsonb_build_object('idempotent',true,'execution',to_jsonb(x)); end if;
 if x.status<>'decision_window_open' or x.owner_deadline is null or (p_request->>'effective_close_timestamp')::timestamptz<x.owner_deadline or now()<x.owner_deadline then raise exception 'Early close is not authorized'; end if;
 if x.decision_population_fingerprint<>p_request->>'expected_population_fingerprint' then raise exception 'Population drift detected'; end if;
 for d in select * from public.rollover_owner_decisions where rollover_execution_id=x.id for update loop
  next_status=case d.decision_status when 'waiting_for_owner' then 'no_response' when 'decline_submitted' then 'planned_release' when 'recontract_validated' then 'planned_retention' else d.decision_status end;
  if next_status<>d.decision_status then select coalesce(max(revision_number),0)+1 into rev from public.rollover_owner_decision_revisions where owner_decision_id=d.id; insert into public.rollover_owner_decision_revisions(owner_decision_id,rollover_execution_id,revision_number,prior_status,new_status,prior_choice,new_choice,changed_by,reason,evidence,idempotency_key) values(d.id,x.id,rev,d.decision_status,next_status,d.owner_choice,d.owner_choice,(p_request->>'requested_by')::uuid,'decision window closed',d.evidence,format('owner-close:%s:%s:%s',x.id,d.id,rev)); update public.rollover_owner_decisions set decision_status=next_status,locked_at=(p_request->>'effective_close_timestamp')::timestamptz,planned_outcome=case when next_status in ('no_response','planned_release') then 'release_at_rollover_to_commissioner_hold' when next_status='planned_retention' then 'retain' else planned_outcome end,execution_status=case when next_status in ('planned_release','planned_retention') then 'ready' else execution_status end where id=d.id; else update public.rollover_owner_decisions set locked_at=(p_request->>'effective_close_timestamp')::timestamptz where id=d.id; end if;
 end loop;
 update public.rollover_executions set status='decision_window_closed',metadata=metadata||jsonb_build_object('close_idempotency_key',k,'closed_by',p_request->>'requested_by') where id=x.id returning * into x;
 select jsonb_object_agg(decision_status,cnt) into summary from (select decision_status,count(*) cnt from public.rollover_owner_decisions where rollover_execution_id=x.id group by decision_status) q; return jsonb_build_object('idempotent',false,'execution',to_jsonb(x),'summary',coalesce(summary,'{}')); end $$;

create or replace function public.cancel_rollover_execution(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; m record; begin if nullif(p_request->>'reason','') is null or nullif(p_request->>'idempotency_key','') is null then raise exception 'Cancellation reason and idempotency key required'; end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update; if x.status='cancelled' then return jsonb_build_object('idempotent',true,'execution',to_jsonb(x)); end if;
 select * into m from public.league_memberships where league_id=x.league_id and user_id=(p_request->>'requested_by')::uuid and lower(role) in ('commissioner','admin','host'); if m is null then raise exception 'Commissioner authorization required'; end if;
 if x.status in ('executing','committed','validating','completed','failed_postcommit_validation') or x.committed_at is not null then raise exception 'Execution cannot be cancelled after core execution begins'; end if;
 update public.rollover_owner_decisions set execution_status='cancelled',locked_at=coalesce(locked_at,now()),metadata=metadata||jsonb_build_object('cancelled_with_execution',true) where rollover_execution_id=x.id and decision_status not in ('executed_retained','commissioner_hold');
 update public.rollover_commissioner_reviews set execution_status='cancelled',metadata=metadata||jsonb_build_object('cancelled_with_execution',true) where rollover_execution_id=x.id and review_status<>'executed';
 update public.rollover_executions set status='cancelled',cancelled_at=now(),metadata=metadata||jsonb_build_object('cancellation_idempotency_key',p_request->>'idempotency_key','cancellation_reason',p_request->>'reason','cancelled_by',p_request->>'requested_by') where id=x.id returning * into x; return jsonb_build_object('idempotent',false,'execution',to_jsonb(x)); end $$;

revoke all on function public.create_rollover_execution(jsonb),public.open_rollover_notice_window(jsonb),public.submit_rollover_owner_decision(jsonb),public.close_rollover_decision_window(jsonb),public.cancel_rollover_execution(jsonb) from public,anon,authenticated;
grant execute on function public.create_rollover_execution(jsonb),public.open_rollover_notice_window(jsonb),public.submit_rollover_owner_decision(jsonb),public.close_rollover_decision_window(jsonb),public.cancel_rollover_execution(jsonb) to service_role;
