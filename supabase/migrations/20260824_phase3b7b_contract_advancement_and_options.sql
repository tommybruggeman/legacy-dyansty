begin;

alter table public.rollover_execution_handler_registry drop constraint rollover_execution_handler_registry_mutation_class_check;
alter table public.rollover_execution_handler_registry add constraint rollover_execution_handler_registry_mutation_class_check check(mutation_class in('read_only','contract_domain'));

insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,result_schema_version,execution_owner,mutation_class,metadata) values
('ADVANCE_CONTRACT_SEASON_OBLIGATIONS',10,1,'phase3b7b-advance-input-v1','phase3b7b-advance-result-v1','execution','contract_domain',jsonb_build_object('phase','3B.7B')),
('EXERCISE_VALID_OWNER_OPTIONS',11,1,'phase3b7b-exercise-input-v1','phase3b7b-exercise-result-v1','execution','contract_domain',jsonb_build_object('phase','3B.7B')),
('DECLINE_OR_EXPIRE_OWNER_OPTIONS',12,1,'phase3b7b-nonexercise-input-v1','phase3b7b-nonexercise-result-v1','execution','contract_domain',jsonb_build_object('phase','3B.7B'));

alter table public.contract_seasons add column rollover_execution_id uuid references public.rollover_executions(id),
 add column rollover_operation_code text,
 add column rollover_final_outcome_id uuid references public.rollover_owner_option_final_outcomes(id),
 add column rollover_evidence_hash text;
alter table public.contract_seasons add constraint contract_seasons_rollover_provenance_check check(
 (rollover_execution_id is null and rollover_operation_code is null and rollover_final_outcome_id is null and rollover_evidence_hash is null)
 or (rollover_execution_id is not null and rollover_operation_code in('ADVANCE_CONTRACT_SEASON_OBLIGATIONS','EXERCISE_VALID_OWNER_OPTIONS','DECLINE_OR_EXPIRE_OWNER_OPTIONS') and rollover_evidence_hash~'^[0-9a-f]{64}$'));
alter table public.contract_agreements add column rollover_pending_disposition text,
 add column rollover_execution_id uuid references public.rollover_executions(id),
 add column rollover_final_outcome_id uuid references public.rollover_owner_option_final_outcomes(id);
alter table public.contract_agreements add constraint contract_agreements_rollover_pending_check check(
 (rollover_pending_disposition is null and rollover_execution_id is null and rollover_final_outcome_id is null)
 or (rollover_pending_disposition in('pending_release','pending_release_to_commissioner_hold') and rollover_execution_id is not null and rollover_final_outcome_id is not null));

alter table public.contract_events drop constraint if exists contract_events_event_type_check;
alter table public.contract_events add constraint contract_events_event_type_check check(event_type in(
 'imported','signed','extended','restructured','salary_changed','option_exercised','option_declined','traded','released','expired','voided','superseded','dead_cap_created','season_obligation_advanced','option_nonexercise_applied'));
create unique index contract_events_phase3b7b_execution_operation_agreement_uidx on public.contract_events(
 ((metadata->>'rollover_execution_id')),((metadata->>'operation_code')),contract_id)
 where metadata?'rollover_execution_id' and metadata?'operation_code';
revoke insert,update,delete,truncate,references,trigger on public.contract_agreements,public.contract_seasons,public.contract_events from public,anon,authenticated;

create or replace function public.execute_rollover_typed_handler_phase3b7b_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 code text:=p_operation->>'operation_type';snap public.rollover_owner_option_snapshot_v2%rowtype;
 c public.rollover_owner_option_snapshot_v2_cases%rowtype;f public.rollover_owner_option_final_outcomes%rowtype;
 a public.contract_agreements%rowtype;src public.contract_seasons%rowtype;tgt public.contract_seasons%rowtype;player public.player_universe%rowtype;
 current_fp text;event_fp text;result_hash text;eligible int:=0;continuing int:=0;pending int:=0;reused int:=0;
 mutations int:=0;events_written int:=0;exercise_count int:=0;third_count int:=0;non_count int:=0;decline_count int:=0;
 default_count int:=0;blocked_count int:=0;salary_before numeric:=0;salary_after numeric:=0;guarantee_total numeric:=0;
begin
 if code not in('ADVANCE_CONTRACT_SEASON_OBLIGATIONS','EXERCISE_VALID_OWNER_OPTIONS','DECLINE_OR_EXPIRE_OWNER_OPTIONS') then
  return public.execute_rollover_typed_handler_phase3b7a_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 end if;
 if not exists(select 1 from public.rollover_execution_handler_registry where operation_code=code and operation_order=(p_operation->>'operation_index')::integer and handler_version=1) then
  perform public.raise_phase3b6c1_failure('unsupported_handler_version',jsonb_build_object('operation_code',code));
 end if;
 select * into snap from public.rollover_owner_option_snapshot_v2 where rollover_execution_id=p_rollover_execution_id for share;
 if snap.id is null then perform public.raise_phase3b6c1_failure('owner_option_snapshot_v2_required','{}');end if;
 if (select count(*) from public.rollover_owner_option_final_outcomes where rollover_execution_id=p_rollover_execution_id)<>snap.case_count then
  perform public.raise_phase3b6c1_failure('final_option_outcome_missing','{}');end if;
 perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);

 for c in select * from public.rollover_owner_option_snapshot_v2_cases where owner_option_snapshot_v2_id=snap.id order by contract_agreement_id for share loop
  eligible:=eligible+1;
  select * into f from public.rollover_owner_option_final_outcomes where rollover_execution_id=p_rollover_execution_id and option_case_id=c.eligible_option_case_id;
  if f.id is null or f.contract_agreement_id<>c.contract_agreement_id or f.player_id<>c.player_id or f.league_team_id<>c.league_team_id then
   perform public.raise_phase3b6c1_failure('final_option_outcome_missing',jsonb_build_object('case_id',c.eligible_option_case_id));end if;
  if code='EXERCISE_VALID_OWNER_OPTIONS' and not(f.policy_resolution_code='exercise' or f.final_disposition_code='approve_policy_supported_exercise') then continue;end if;
  if code='DECLINE_OR_EXPIRE_OWNER_OPTIONS' and (f.policy_resolution_code='exercise' or f.final_disposition_code='approve_policy_supported_exercise') then continue;end if;
  select * into a from public.contract_agreements where id=c.contract_agreement_id for update;
  if a.id is null then perform public.raise_phase3b6c1_failure('contract_agreement_missing','{}');end if;
  if a.league_id<>c.league_id then perform public.raise_phase3b6c1_failure('contract_cross_league','{}');end if;
  if a.player_id<>c.player_id or a.league_team_id<>c.league_team_id then perform public.raise_phase3b6c1_failure('contract_owner_mismatch','{}');end if;
  select * into src from public.contract_seasons where contract_id=a.id and season=c.closing_season for update;
  if src.id is null then perform public.raise_phase3b6c1_failure('source_contract_season_missing','{}');end if;
  if src.league_season_id<>c.closing_season_id or src.player_id<>c.player_id or src.league_team_id<>c.league_team_id then perform public.raise_phase3b6c1_failure('contract_changed_after_freeze','{}');end if;
  select * into tgt from public.contract_seasons where contract_id=a.id and season=c.target_season for update;
  if tgt.id is null then perform public.raise_phase3b6c1_failure('exercise_target_season_missing','{}');end if;
  if tgt.league_season_id<>c.target_season_id or tgt.league_id<>c.league_id or tgt.player_id<>c.player_id or tgt.league_team_id<>c.league_team_id or not tgt.is_option_year or tgt.option_type<>c.option_type then
   perform public.raise_phase3b6c1_failure('target_contract_season_conflict','{}');end if;
  select * into player from public.player_universe where sleeper_id=c.player_id;
  current_fp:=public.rollover_material_fingerprint(jsonb_build_object('agreement',(to_jsonb(a)-'created_at'-'updated_at')||jsonb_build_object('rollover_pending_disposition',null,'rollover_execution_id',null,'rollover_final_outcome_id',null),'source_obligation',(to_jsonb(src)-'created_at'-'updated_at')||jsonb_build_object('rollover_execution_id',null,'rollover_operation_code',null,'rollover_final_outcome_id',null,'rollover_evidence_hash',null),'option_obligation',(to_jsonb(tgt)-'created_at'-'updated_at')||jsonb_build_object('rollover_execution_id',null,'rollover_operation_code',null,'rollover_final_outcome_id',null,'rollover_evidence_hash',null),'player_classification',jsonb_build_object('rookie_class_year',player.rookie_class_year,'draft_year',player.draft_year,'draft_round',player.draft_round,'is_rookie_contract',player.is_rookie_contract)));
  if current_fp<>c.source_agreement_fingerprint then perform public.raise_phase3b6c1_failure('contract_changed_after_freeze','{}');end if;
  salary_before:=salary_before+src.salary;salary_after:=salary_after+coalesce(f.final_proposed_salary,tgt.salary);guarantee_total:=guarantee_total+coalesce(tgt.guaranteed_salary,0);

  if code='ADVANCE_CONTRACT_SEASON_OBLIGATIONS' then
   pending:=pending+1;
   if tgt.rollover_execution_id is null then
    event_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7b-contract-event-v1','execution',p_rollover_execution_id,'operation',code,'agreement',a.id,'source',src.id,'target',tgt.id,'final_outcome',f.id));
    update public.contract_seasons set rollover_execution_id=p_rollover_execution_id,rollover_operation_code=code,rollover_final_outcome_id=f.id,rollover_evidence_hash=event_fp,updated_at=clock_timestamp() where id=tgt.id;
    insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
    values(a.id,a.league_id,a.league_team_id,a.player_id,'season_obligation_advanced',c.target_season,'phase3b7b',p_actor,jsonb_build_object('obligation_status',tgt.obligation_status),jsonb_build_object('obligation_status',tgt.obligation_status),jsonb_build_object('rollover_execution_id',p_rollover_execution_id,'operation_code',code,'source_contract_season_id',src.id,'target_contract_season_id',tgt.id,'final_outcome_id',f.id,'event_fingerprint',event_fp),format('phase3b7b:%s:%s:%s',p_rollover_execution_id,code,a.id));
    mutations:=mutations+1;events_written:=events_written+1;
   elsif tgt.rollover_execution_id=p_rollover_execution_id and tgt.rollover_operation_code=code then reused:=reused+1;
   else perform public.raise_phase3b6c1_failure('target_contract_season_conflict','{}');end if;
  elsif code='EXERCISE_VALID_OWNER_OPTIONS' and (f.policy_resolution_code='exercise' or f.final_disposition_code='approve_policy_supported_exercise') then
   exercise_count:=exercise_count+1;if c.is_third_round then third_count:=third_count+1;if f.final_proposed_salary is null or f.final_proposed_salary<>public.phase3b7a_round_half_up(7*(c.salary_rule_linkage->>'salary_cap')::numeric/225) or c.guaranteed_salary<>1 then perform public.raise_phase3b6c1_failure('exercise_salary_mismatch','{}');end if;end if;
   if not c.option_exercise_eligible then perform public.raise_phase3b6c1_failure('invalid_taxi_exercise_reached_application','{}');end if;
   if tgt.rollover_execution_id<>p_rollover_execution_id or tgt.obligation_status<>'scheduled' then perform public.raise_phase3b6c1_failure('exercise_target_state_conflict','{}');end if;
   event_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7b-contract-event-v1','execution',p_rollover_execution_id,'operation',code,'agreement',a.id,'final_hash',f.final_outcome_hash,'salary',coalesce(f.final_proposed_salary,tgt.salary),'term',f.final_option_term));
   update public.contract_seasons set obligation_status='active',salary=coalesce(f.final_proposed_salary,salary),cap_hit=coalesce(f.final_proposed_salary,cap_hit),guaranteed_salary=c.guaranteed_salary,rollover_operation_code=code,rollover_final_outcome_id=f.id,rollover_evidence_hash=event_fp,updated_at=clock_timestamp() where id=tgt.id;
   insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
   values(a.id,a.league_id,a.league_team_id,a.player_id,'option_exercised',c.target_season,'phase3b7b',p_actor,jsonb_build_object('obligation_status','scheduled','salary',tgt.salary,'guaranteed_salary',tgt.guaranteed_salary),jsonb_build_object('obligation_status','active','salary',coalesce(f.final_proposed_salary,tgt.salary),'guaranteed_salary',c.guaranteed_salary),jsonb_build_object('rollover_execution_id',p_rollover_execution_id,'operation_code',code,'source_contract_season_id',src.id,'target_contract_season_id',tgt.id,'final_outcome_id',f.id,'final_outcome_hash',f.final_outcome_hash,'event_fingerprint',event_fp),format('phase3b7b:%s:%s:%s',p_rollover_execution_id,code,a.id));
   mutations:=mutations+1;events_written:=events_written+1;
  elsif code='DECLINE_OR_EXPIRE_OWNER_OPTIONS' and not(f.policy_resolution_code='exercise' or f.final_disposition_code='approve_policy_supported_exercise') then
   non_count:=non_count+1;if f.policy_resolution_code='decline' then decline_count:=decline_count+1;elsif f.policy_resolution_code='default_release_to_commissioner_hold' then default_count:=default_count+1;elsif f.policy_resolution_code='blocked_invalid_exercise' then blocked_count:=blocked_count+1;else perform public.raise_phase3b6c1_failure('nonexercise_disposition_unsupported','{}');end if;
   if tgt.rollover_execution_id<>p_rollover_execution_id or tgt.obligation_status<>'scheduled' then perform public.raise_phase3b6c1_failure('nonexercise_target_state_conflict','{}');end if;
   event_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7b-contract-event-v1','execution',p_rollover_execution_id,'operation',code,'agreement',a.id,'final_hash',f.final_outcome_hash,'disposition',f.policy_resolution_code));
   update public.contract_seasons set obligation_status='expired',rollover_operation_code=code,rollover_final_outcome_id=f.id,rollover_evidence_hash=event_fp,updated_at=clock_timestamp() where id=tgt.id;
   update public.contract_agreements set rollover_pending_disposition=case when f.release_to_hold then 'pending_release_to_commissioner_hold' else 'pending_release' end,rollover_execution_id=p_rollover_execution_id,rollover_final_outcome_id=f.id,updated_at=clock_timestamp() where id=a.id;
   insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
   values(a.id,a.league_id,a.league_team_id,a.player_id,'option_nonexercise_applied',c.target_season,'phase3b7b',p_actor,jsonb_build_object('obligation_status','scheduled','agreement_status',a.status),jsonb_build_object('obligation_status','expired','agreement_status',a.status,'pending_disposition',case when f.release_to_hold then 'pending_release_to_commissioner_hold' else 'pending_release' end),jsonb_build_object('rollover_execution_id',p_rollover_execution_id,'operation_code',code,'source_contract_season_id',src.id,'target_contract_season_id',tgt.id,'final_outcome_id',f.id,'final_outcome_hash',f.final_outcome_hash,'event_fingerprint',event_fp,'release_deferred_to_operation',13,'hold_deferred_to_operation',14),format('phase3b7b:%s:%s:%s',p_rollover_execution_id,code,a.id));
   mutations:=mutations+2;events_written:=events_written+1;
  end if;
 end loop;
 if eligible<>snap.case_count then perform public.raise_phase3b6c1_failure('contract_population_incomplete','{}');end if;
 result_hash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7b-result-v1','operation',code,'execution',p_rollover_execution_id,'eligible',eligible,'mutations',mutations,'events',events_written,'exercise',exercise_count,'nonexercise',non_count));
 if code='ADVANCE_CONTRACT_SEASON_OBLIGATIONS' then return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('eligible_agreement_count',eligible,'continuing_contract_count',continuing,'option_year_pending_count',pending,'target_rows_inserted',0,'compatible_target_rows_reused',reused,'conflicting_target_rows',0,'skipped_terminal_contracts',0,'source_season_count',eligible,'target_season_count',eligible,'salary_total_before',salary_before,'proposed_target_salary_total',salary_after,'guaranteed_salary_total',guarantee_total,'operation_mutation_count',mutations+events_written,'postcondition_count',eligible,'validation_codes',jsonb_build_array('snapshot_v2','final_outcomes','frozen_contract_fingerprint'),'deterministic_result_hash',result_hash));end if;
 if code='EXERCISE_VALID_OWNER_OPTIONS' then return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('final_exercise_outcome_count',exercise_count,'exercised_agreement_count',exercise_count,'target_seasons_updated',mutations,'contract_events_written',events_written,'third_round_exercise_count',third_count,'exercised_salary_total',(select coalesce(sum(salary),0) from public.contract_seasons where rollover_execution_id=p_rollover_execution_id and rollover_operation_code=code),'guaranteed_salary_total',(select coalesce(sum(guaranteed_salary),0) from public.contract_seasons where rollover_execution_id=p_rollover_execution_id and rollover_operation_code=code),'compatible_replay_count',0,'conflict_count',0,'operation_mutation_count',mutations+events_written,'validation_codes',jsonb_build_array('final_outcome','salary','term','taxi'),'deterministic_result_hash',result_hash));end if;
 return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('non_exercise_outcome_count',non_count,'decline_count',decline_count,'expiration_count',0,'default_release_to_hold_count',default_count,'blocked_invalid_exercise_count',blocked_count,'target_obligations_deactivated_or_terminal',non_count,'agreements_marked_pending_release',non_count,'contract_events_written',events_written,'compatible_replay_count',0,'conflict_count',0,'operation_mutation_count',mutations+events_written,'validation_codes',jsonb_build_array('final_outcome','pending_release','release_deferred'),'deterministic_result_hash',result_hash));
end $$;

create or replace function public.execute_rollover_plan_phase3b7b_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;domain_mutations integer:=0;failed_op jsonb;failure_sqlstate text;failure_message text;
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
  jsonb_build_object('engine_version','phase3b7b-v1','typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then raise exception 'ordered operation sequence mismatch';end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE',
    'VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED','FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE','RESOLVE_OWNER_OPTION_OUTCOMES','VALIDATE_COMMISSIONER_REVIEW_OUTCOMES','ADVANCE_CONTRACT_SEASON_OBLIGATIONS','EXERCISE_VALID_OWNER_OPTIONS','DECLINE_OR_EXPIRE_OWNER_OPTIONS') then
    handler_result:=public.execute_rollover_typed_handler_phase3b7b_private(op,x.id,p.id,a.id,p_actor);
    domain_mutations:=domain_mutations+coalesce((handler_result#>>'{result,operation_mutation_count}')::integer,0);
   else raise exception 'unsupported Phase 3B.7B operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,
    operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint','completed',op_started,
    clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),handler_result,
    jsonb_build_object('domain_mutations',coalesce((handler_result#>>'{result,operation_mutation_count}')::integer,0),'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
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
  'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',domain_mutations,'live_external_call_performed',false,'publication_performed',false));
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
 return public.execute_rollover_plan_phase3b7b_private(p_request,actor);
end $$;


revoke all on function public.execute_rollover_typed_handler_phase3b7b_private(jsonb,uuid,uuid,uuid,uuid) from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_phase3b7b_private(jsonb,uuid),public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;
commit;
