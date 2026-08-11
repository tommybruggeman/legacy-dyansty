begin;
insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,result_schema_version,execution_owner,mutation_class,metadata) values
('RESOLVE_OWNER_OPTION_OUTCOMES',8,1,'phase3b7a-resolution-input-v1','phase3b7a-resolution-result-v1','execution','read_only',jsonb_build_object('phase','3B.7A')),
('VALIDATE_COMMISSIONER_REVIEW_OUTCOMES',9,1,'phase3b7a-review-input-v1','phase3b7a-review-result-v1','execution','read_only',jsonb_build_object('phase','3B.7A'));

create table public.rollover_owner_option_resolutions(
 id uuid primary key default gen_random_uuid(),rollover_execution_id uuid not null references public.rollover_executions(id),
 owner_option_snapshot_v2_id uuid not null references public.rollover_owner_option_snapshot_v2(id),option_case_id uuid not null,
 league_id uuid not null,closing_season_id uuid not null,target_season_id uuid not null,contract_agreement_id uuid not null,
 player_id text not null,league_team_id uuid not null,decision_id uuid not null,final_revision_id uuid,
 outcome_code text not null check(outcome_code in('exercise','decline','default_release_to_commissioner_hold','blocked_invalid_exercise','requires_commissioner_review')),
 reason_code text not null,review_required boolean not null,taxi_eligibility jsonb not null,response_timestamp timestamptz,
 deadline_timestamp timestamptz not null,option_classification text not null,rookie_draft_round integer,is_third_round boolean not null,
 frozen_current_salary numeric not null,frozen_guaranteed_salary numeric not null,frozen_option_term integer not null,
 proposed_option_salary numeric,salary_formula_inputs jsonb not null,source_case_hash text not null check(source_case_hash~'^[0-9a-f]{64}$'),
 schema_version text not null check(schema_version='phase3b7a-resolution-v1'),resolution_hash text not null check(resolution_hash~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default clock_timestamp(),unique(rollover_execution_id,option_case_id));
create table public.rollover_owner_option_final_outcomes(
 id uuid primary key default gen_random_uuid(),rollover_execution_id uuid not null references public.rollover_executions(id),
 owner_option_snapshot_v2_id uuid not null references public.rollover_owner_option_snapshot_v2(id),resolution_id uuid not null references public.rollover_owner_option_resolutions(id),
 option_case_id uuid not null,league_id uuid not null,closing_season_id uuid not null,target_season_id uuid not null,
 contract_agreement_id uuid not null,player_id text not null,league_team_id uuid not null,policy_resolution_code text not null,
 final_disposition_code text not null check(final_disposition_code in('confirm_policy_resolution','reject_invalid_exercise','confirm_release_to_commissioner_hold','approve_policy_supported_exercise','approve_policy_supported_decline','correct_administrative_invalid_response')),
 final_reason_code text not null,review_required boolean not null,review_id uuid,reviewer_id uuid,review_timestamp timestamptz,
 authority_event_id uuid,final_proposed_salary numeric,final_option_term integer not null,release_to_hold boolean not null,
 source_resolution_hash text not null,source_review_hash text,schema_version text not null check(schema_version='phase3b7a-final-v1'),
 final_outcome_hash text not null check(final_outcome_hash~'^[0-9a-f]{64}$'),created_at timestamptz not null default clock_timestamp(),
 unique(rollover_execution_id,option_case_id));

create or replace function public.reject_phase3b7a_mutation() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$begin raise exception 'Phase 3B.7A outcome evidence is immutable';end$$;
create trigger rollover_owner_option_resolutions_immutable before update or delete on public.rollover_owner_option_resolutions for each row execute function public.reject_phase3b7a_mutation();
create trigger rollover_owner_option_final_outcomes_immutable before update or delete on public.rollover_owner_option_final_outcomes for each row execute function public.reject_phase3b7a_mutation();
alter table public.rollover_owner_option_resolutions enable row level security;alter table public.rollover_owner_option_final_outcomes enable row level security;
revoke all on public.rollover_owner_option_resolutions,public.rollover_owner_option_final_outcomes from public,anon,authenticated;
grant select,insert on public.rollover_owner_option_resolutions,public.rollover_owner_option_final_outcomes to service_role;
create policy phase3b7a_resolution_read on public.rollover_owner_option_resolutions for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_owner_option_resolutions.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy phase3b7a_final_read on public.rollover_owner_option_final_outcomes for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_owner_option_final_outcomes.league_id and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.phase3b7a_round_half_up(v numeric) returns numeric language sql immutable set search_path=pg_catalog,public as $$select floor(v+0.5)$$;

create or replace function public.execute_rollover_typed_handler_phase3b7a_private(p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare code text:=p_operation->>'operation_type';s public.rollover_owner_option_snapshot_v2%rowtype;c public.rollover_owner_option_snapshot_v2_cases%rowtype;r public.rollover_owner_option_resolutions%rowtype;rv public.rollover_owner_option_snapshot_v2_reviews%rowtype;
 material jsonb;h text;salary numeric;outcome text;reason text;review_required boolean;count_all int:=0;count_ex int:=0;count_dec int:=0;count_def int:=0;count_block int:=0;count_review int:=0;written int:=0;set_hash text;no_review_count int:=0;valid_review_count int:=0;confirmed_count int:=0;corrected_count int:=0;release_count int:=0;rejected_count int:=0;
begin
 if code not in('RESOLVE_OWNER_OPTION_OUTCOMES','VALIDATE_COMMISSIONER_REVIEW_OUTCOMES') then return public.execute_rollover_typed_handler_phase3b6c1_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);end if;
 if not exists(select 1 from public.rollover_execution_handler_registry where operation_code=code and operation_order=(p_operation->>'operation_index')::integer and handler_version=1) then perform public.raise_phase3b6c1_failure('unsupported_handler_version',jsonb_build_object('operation_code',code));end if;
 select * into s from public.rollover_owner_option_snapshot_v2 where rollover_execution_id=p_rollover_execution_id for share;
 if s.id is null then perform public.raise_phase3b6c1_failure(case when code='RESOLVE_OWNER_OPTION_OUTCOMES' then 'owner_option_snapshot_v2_required' else 'snapshot_v2_required' end,'{}');end if;
 if code='RESOLVE_OWNER_OPTION_OUTCOMES' then
  for c in select * from public.rollover_owner_option_snapshot_v2_cases where owner_option_snapshot_v2_id=s.id order by eligible_option_case_id for share loop
   count_all:=count_all+1;review_required:=c.commissioner_review_id is not null or coalesce(c.submitted_choice='commissioner_review',false);salary:=null;
   if review_required then outcome:='requires_commissioner_review';reason:='frozen_review_required';count_review:=count_review+1;
   elsif c.submitted_choice='recontract' and (not c.response_before_deadline or not c.option_exercise_eligible) then outcome:='blocked_invalid_exercise';reason:=case when not c.option_exercise_eligible then 'third_round_taxi_prohibited' else 'exercise_after_deadline' end;count_block:=count_block+1;
   elsif c.submitted_choice='recontract' then outcome:='exercise';reason:='valid_frozen_exercise';count_ex:=count_ex+1;if c.is_third_round then salary:=public.phase3b7a_round_half_up(7*(c.salary_rule_linkage->>'salary_cap')::numeric/225);if c.frozen_guaranteed_salary<>1 or salary<0 then perform public.raise_phase3b6c1_failure('salary_calculation_invalid','{}');end if;end if;
   elsif c.submitted_choice='decline' then outcome:='decline';reason:='valid_frozen_decline';count_dec:=count_dec+1;
   elsif c.is_default_nonresponse then outcome:='default_release_to_commissioner_hold';reason:='policy_default_nonresponse';count_def:=count_def+1;
   else perform public.raise_phase3b6c1_failure('resolution_incomplete',jsonb_build_object('case_id',c.eligible_option_case_id));end if;
   material:=jsonb_build_object('schema','phase3b7a-resolution-v1','case_hash',c.case_fingerprint,'outcome',outcome,'reason',reason,'salary',salary,'term',c.option_term);h:=public.rollover_material_fingerprint(material);
   insert into public.rollover_owner_option_resolutions(rollover_execution_id,owner_option_snapshot_v2_id,option_case_id,league_id,closing_season_id,target_season_id,contract_agreement_id,player_id,league_team_id,decision_id,final_revision_id,outcome_code,reason_code,review_required,taxi_eligibility,response_timestamp,deadline_timestamp,option_classification,rookie_draft_round,is_third_round,frozen_current_salary,frozen_guaranteed_salary,frozen_option_term,proposed_option_salary,salary_formula_inputs,source_case_hash,schema_version,resolution_hash)
   values(p_rollover_execution_id,s.id,c.eligible_option_case_id,c.league_id,c.closing_season_id,c.target_season_id,c.contract_agreement_id,c.player_id,c.league_team_id,c.decision_id,c.latest_revision_id,outcome,reason,review_required,jsonb_build_object('status',c.taxi_status,'eligible',c.option_exercise_eligible),c.submitted_at,c.deadline_timestamp,c.option_eligibility_type,c.rookie_draft_round,c.is_third_round,c.current_contract_salary,c.guaranteed_salary,c.option_term,salary,c.salary_rule_linkage,c.case_fingerprint,'phase3b7a-resolution-v1',h);written:=written+1;
  end loop;
  if count_all<>s.case_count then perform public.raise_phase3b6c1_failure('resolution_incomplete','{}');end if;
  select public.rollover_material_fingerprint(jsonb_agg(jsonb_build_object('case',option_case_id,'hash',resolution_hash) order by option_case_id)) into set_hash from public.rollover_owner_option_resolutions where rollover_execution_id=p_rollover_execution_id;
  return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('eligible_case_count',count_all,'exercise_count',count_ex,'decline_count',count_dec,'default_release_to_hold_count',count_def,'blocked_invalid_exercise_count',count_block,'review_required_count',count_review,'unresolved_count',0,'duplicate_conflict_count',0,'third_round_salary_calculation_count',(select count(*) from public.rollover_owner_option_resolutions where rollover_execution_id=p_rollover_execution_id and is_third_round and proposed_option_salary is not null),'resolution_set_hash',set_hash,'durable_resolution_rows_written',written,'domain_mutation_count',0));
 end if;
 for r in select * from public.rollover_owner_option_resolutions where rollover_execution_id=p_rollover_execution_id order by option_case_id for share loop
  rv:=null;select * into rv from public.rollover_owner_option_snapshot_v2_reviews where owner_option_snapshot_v2_id=s.id and eligible_option_case_id=r.option_case_id;
  if r.review_required then
   if rv.id is null then perform public.raise_phase3b6c1_failure('commissioner_review_missing','{}');end if;
   if not rv.authorized_at_review_time then perform public.raise_phase3b6c1_failure('review_authority_history_missing','{}');end if;
   if rv.superseded then perform public.raise_phase3b6c1_failure('commissioner_review_superseded','{}');end if;
   outcome:=case rv.disposition when 'retain_contract' then 'approve_policy_supported_exercise' when 'approve_termination' then 'confirm_release_to_commissioner_hold' when 'reject_termination' then 'reject_invalid_exercise' else null end;
   if outcome is null then perform public.raise_phase3b6c1_failure('commissioner_disposition_unsupported',jsonb_build_object('disposition',rv.disposition));end if;
   valid_review_count:=valid_review_count+1;
  else outcome:='confirm_policy_resolution';no_review_count:=no_review_count+1;end if;
  confirmed_count:=confirmed_count+1;if outcome='correct_administrative_invalid_response' then corrected_count:=corrected_count+1;end if;if outcome='confirm_release_to_commissioner_hold' then release_count:=release_count+1;end if;if outcome='reject_invalid_exercise' then rejected_count:=rejected_count+1;end if;
  material:=jsonb_build_object('schema','phase3b7a-final-v1','resolution_hash',r.resolution_hash,'review_hash',rv.review_fingerprint,'disposition',outcome);h:=public.rollover_material_fingerprint(material);
  insert into public.rollover_owner_option_final_outcomes(rollover_execution_id,owner_option_snapshot_v2_id,resolution_id,option_case_id,league_id,closing_season_id,target_season_id,contract_agreement_id,player_id,league_team_id,policy_resolution_code,final_disposition_code,final_reason_code,review_required,review_id,reviewer_id,review_timestamp,authority_event_id,final_proposed_salary,final_option_term,release_to_hold,source_resolution_hash,source_review_hash,schema_version,final_outcome_hash)
  values(p_rollover_execution_id,s.id,r.id,r.option_case_id,r.league_id,r.closing_season_id,r.target_season_id,r.contract_agreement_id,r.player_id,r.league_team_id,r.outcome_code,outcome,case when r.review_required then rv.reason_code else r.reason_code end,r.review_required,rv.review_id,rv.reviewer_user_id,rv.review_timestamp,rv.authority_event_id,r.proposed_option_salary,r.frozen_option_term,r.outcome_code='default_release_to_commissioner_hold' or outcome='confirm_release_to_commissioner_hold',r.resolution_hash,rv.review_fingerprint,'phase3b7a-final-v1',h);written:=written+1;
 end loop;
 if written<>(select count(*) from public.rollover_owner_option_resolutions where rollover_execution_id=p_rollover_execution_id) then perform public.raise_phase3b6c1_failure('final_outcome_incomplete','{}');end if;
 select public.rollover_material_fingerprint(jsonb_agg(jsonb_build_object('case',option_case_id,'hash',final_outcome_hash) order by option_case_id)) into set_hash from public.rollover_owner_option_final_outcomes where rollover_execution_id=p_rollover_execution_id;
 return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('total_resolution_count',written,'review_required_count',valid_review_count,'no_review_finalization_count',no_review_count,'valid_review_count',valid_review_count,'confirmed_count',confirmed_count,'corrected_count',corrected_count,'release_to_hold_count',release_count,'rejected_invalid_exercise_count',rejected_count,'missing_review_count',0,'authority_history_missing_count',0,'unauthorized_review_count',0,'duplicate_conflicting_review_count',0,'incompatible_disposition_count',0,'final_outcome_count',written,'final_outcome_set_hash',set_hash,'durable_final_rows_written',written,'domain_mutation_count',0));
end$$;

create or replace function public.execute_rollover_plan_phase3b7a_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;failed_op jsonb;failure_sqlstate text;failure_message text;
 failure_detail text;failure_hint text;failure_context text;result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null or nullif(p_request->>'approval_id','') is null
  or nullif(p_request->>'execution_plan_id','') is null or nullif(p_request->>'expected_plan_fingerprint','') is null
  or nullif(p_request->>'expected_execution_status','') is null or nullif(p_request->>'expected_approval_status','') is null then raise exception 'complete execution assertions required';end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 -- Retain the certified request-material operation label so a same-key replay
 -- of a completed v1 execution has the identical fingerprint and returns it.
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6c','execution_id',x.id,'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);end if;
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::integer or p.plan_version<>a.execution_plan_version
  or p.plan_status<>'approved_for_execution' or not p.approved_for_execution or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint'
  or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then raise exception 'stale or invalid approved execution plan';end if;
 select * into l from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=a.id and execution_plan_id=p.id
  and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;
 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,execution_plan_version,
  plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',p.operation_count,run_started,p_actor,
  jsonb_build_object('engine_version','phase3b7a-v1','typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then raise exception 'ordered operation sequence mismatch';end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE',
    'VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED','FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE','RESOLVE_OWNER_OPTION_OUTCOMES','VALIDATE_COMMISSIONER_REVIEW_OUTCOMES') then
    handler_result:=public.execute_rollover_typed_handler_phase3b7a_private(op,x.id,p.id,a.id,p_actor);
   else raise exception 'unsupported Phase 3B.7A operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,
    operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint','completed',op_started,
    clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),handler_result,
    jsonb_build_object('domain_mutations',0,'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
   completed:=completed+1;
  end loop;
 exception when others then get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,
  failure_detail=pg_exception_detail,failure_hint=pg_exception_hint,failure_context=pg_exception_context;end;
 if failure_message is not null then
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,operation_type,
   operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),
   coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),'failed',
   coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),
   jsonb_build_object('failure_code',failure_message,'sqlstate',failure_sqlstate,'detail',left(coalesce(failure_detail,''),4096),
    'hint',left(coalesce(failure_hint,''),1024),'context',left(coalesce(failure_context,''),4096),'rolled_back_operations',completed,
    'domain_mutations_committed',0,'live_external_call_performed',false),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
   'operations_attempted',attempted,'operations_completed',0,'success',false,'failure_code',failure_message,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,operations_completed=0,
   finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;
 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
  'operations_attempted',attempted,'operations_completed',completed,'success',true,
  'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,operations_completed=completed,
  finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end $$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 return public.execute_rollover_plan_phase3b7a_private(p_request,actor);
end $$;

revoke all on function public.capture_commissioner_authority_event_phase3b6c1(),public.raise_phase3b6c1_failure(text,jsonb),
 public.execute_rollover_typed_handler_phase3b6c1_private(jsonb,uuid,uuid,uuid,uuid),
 public.execute_rollover_plan_phase3b7a_private(jsonb,uuid),public.execute_rollover_plan_authenticated(jsonb)
 from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;


revoke all on function public.phase3b7a_round_half_up(numeric),public.execute_rollover_typed_handler_phase3b7a_private(jsonb,uuid,uuid,uuid,uuid) from public,anon,authenticated,service_role;
commit;
