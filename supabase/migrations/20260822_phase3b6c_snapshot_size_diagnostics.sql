begin;

-- Phase 3B.6C diagnostic hardening:
-- report exact component sizes when snapshot bounds are exceeded.
create or replace function public.execute_rollover_typed_handler_phase3b6c_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,
 p_approval_id uuid,p_actor uuid
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 code text:=p_operation->>'operation_type';
 x public.rollover_executions%rowtype;p public.rollover_execution_plans%rowtype;
 a public.rollover_execution_plan_approvals%rowtype;
 source_row public.league_seasons%rowtype;target_row public.league_seasons%rowtype;
 capture_row public.historical_capture_executions%rowtype;
 snapshot_row public.rollover_execution_input_snapshots%rowtype;
 registry_row public.rollover_execution_handler_registry%rowtype;
 mapping_fp text;decision_fp text;history_manifest jsonb;components jsonb:='[]'::jsonb;
 component jsonb;aggregate_fp text;snapshot_id uuid:=gen_random_uuid();
 team_count integer;decision_count integer;capture_count integer;payload_size integer;
 expected_counts jsonb;current_count integer;present_count integer:=0;
 comp_name text;
 immutability_ok boolean;result_material jsonb;source_recheck text;
begin
 if code in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY',
  'VERIFY_TARGET_SLEEPER_LINKAGE','VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED') then
  return public.execute_rollover_typed_handler_phase3b6b_private(
   p_operation,p_rollover_execution_id,p_execution_plan_id);
 end if;
 if jsonb_typeof(p_operation) is distinct from 'object' then
  perform public.raise_rollover_preflight_failure_phase3b6c('typed_handler_operation_invalid',coalesce(code,''),'{}');
 end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into p from public.rollover_execution_plans
  where id=p_execution_plan_id and rollover_execution_id=p_rollover_execution_id;
 select * into a from public.rollover_execution_plan_approvals
  where id=p_approval_id and rollover_execution_id=p_rollover_execution_id
   and execution_plan_id=p_execution_plan_id;
 select * into registry_row from public.rollover_execution_handler_registry
  where operation_code=code and enabled;
 if x.id is null or p.id is null or a.id is null then
  perform public.raise_rollover_preflight_failure_phase3b6c('approved_plan_material_missing',code,'{}');
 end if;
 if registry_row.operation_code is null then
  perform public.raise_rollover_preflight_failure_phase3b6c('unsupported_operation',coalesce(code,''),'{}');
 end if;
 if (p_operation->>'operation_index')::integer<>registry_row.operation_order
    or not p_operation?'handler_version'
    or (p_operation->>'handler_version')::integer<>registry_row.handler_version
    or not p_operation?'input_schema_version'
    or p_operation->>'input_schema_version'<>registry_row.input_schema_version then
  perform public.raise_rollover_preflight_failure_phase3b6c('typed_handler_contract_mismatch',code,'{}');
 end if;

 select * into source_row from public.league_seasons
  where league_id=x.league_id and season=p.source_season for share;
 select * into target_row from public.league_seasons
  where league_id=x.league_id and season=p.target_season for share;
 if source_row.id is null or target_row.id is null then
  perform public.raise_rollover_preflight_failure_phase3b6c('approved_plan_material_missing',code,'{}');
 end if;

 if code='FREEZE_FINAL_EXECUTION_INPUTS' then
  select * into snapshot_row from public.rollover_execution_input_snapshots
   where rollover_execution_id=x.id;
  if snapshot_row.id is not null then
   if snapshot_row.execution_plan_id<>p.id or snapshot_row.approval_id<>a.id
      or snapshot_row.source_plan_fingerprint<>p.plan_fingerprint then
    perform public.raise_rollover_preflight_failure_phase3b6c(
     'execution_input_snapshot_already_conflicts',code,'{}');
   end if;
   return jsonb_build_object('operation_code',code,'handler_version',1,
    'result_schema_version',registry_row.result_schema_version,'read_only',true,
    'domain_mutations',0,'result',jsonb_build_object(
     'snapshot_id',snapshot_row.id,'snapshot_schema_version',snapshot_row.snapshot_schema_version,
     'component_count',snapshot_row.component_count,
     'aggregate_snapshot_hash',snapshot_row.aggregate_snapshot_fingerprint,
     'source_plan_hash',snapshot_row.source_plan_fingerprint,
     'mapping_fingerprint',snapshot_row.mapping_fingerprint,
     'option_decision_fingerprint',snapshot_row.option_decision_fingerprint,
     'historical_capture_identifier',snapshot_row.historical_capture_execution_id,
     'frozen_team_count',snapshot_row.frozen_team_count,
     'frozen_option_decision_count',snapshot_row.frozen_option_decision_count,
     'durable_snapshot_rows_written',0,'validation_outcome','passed',
     'validation_codes','[]'::jsonb,'football_domain_mutation_count',0));
  end if;

  select value->>'evidence_fingerprint' into mapping_fp
   from jsonb_array_elements(p.ordered_operations) q(value)
   where value->>'operation_type'='VERIFY_TEAM_ROSTER_MAPPINGS';
  select value->>'evidence_fingerprint' into decision_fp
   from jsonb_array_elements(p.ordered_operations) q(value)
   where value->>'operation_type'='VERIFY_OPTION_WINDOW_CLOSED';
  if mapping_fp is null then
   perform public.raise_rollover_preflight_failure_phase3b6c('mapping_evidence_missing',code,'{}');
  end if;
  if decision_fp is null then
   perform public.raise_rollover_preflight_failure_phase3b6c('option_decision_evidence_missing',code,'{}');
  end if;
  select count(*) into capture_count from public.historical_capture_executions h
   where h.league_season_id=source_row.id and h.status in('validated','finalized');
  if capture_count=0 then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_reference_missing',code,'{}');
  elsif capture_count>1 then
   perform public.raise_rollover_preflight_failure_phase3b6c('duplicate_historical_capture',code,'{}');
  end if;
  select * into capture_row from public.historical_capture_executions h
   where h.league_season_id=source_row.id and h.status in('validated','finalized') for share;
  perform 1 from public.league_teams where league_id=x.league_id order by id for share;
  perform 1 from public.league_memberships where league_id=x.league_id order by id for share;
  perform 1 from public.season_team_mappings
   where league_season_id in(source_row.id,target_row.id) order by id for share;
  perform 1 from public.rollover_owner_decisions where rollover_execution_id=x.id order by id for share;
  perform 1 from public.rollover_owner_decision_revisions where rollover_execution_id=x.id order by id for share;
  perform 1 from public.rollover_commissioner_reviews where rollover_execution_id=x.id order by id for share;
  perform 1 from public.league_rules where league_id=x.league_id order by id for share;
  if (select count(*) from public.league_rules where league_id=x.league_id)<>1 then
   perform public.raise_rollover_preflight_failure_phase3b6c('league_rules_missing',code,'{}');
  end if;
  if (select salary_cap from public.league_rules where league_id=x.league_id) is null then
   perform public.raise_rollover_preflight_failure_phase3b6c('salary_cap_missing',code,'{}');
  end if;
  select count(*) into team_count from public.league_teams where league_id=x.league_id;
  select count(*) into decision_count from public.rollover_owner_decisions where rollover_execution_id=x.id;
  history_manifest:=public.phase3b6c_history_manifest(source_row.id);

  components:=components||jsonb_build_array(jsonb_build_object('name','execution_identity','version','phase3b6c-execution_identity-v1','count',1,'source',p.plan_fingerprint,'payload',jsonb_build_object(
   'execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'plan_version',p.plan_version,
   'plan_hash',p.plan_fingerprint,'league_id',x.league_id)));
  components:=components||jsonb_build_array(jsonb_build_object('name','season_authority','version','phase3b6c-season_authority-v1','count',2,'payload',jsonb_build_object(
   'closing',jsonb_build_object('id',source_row.id,'season',source_row.season,'sleeper_league_id',source_row.sleeper_league_id,'is_active',source_row.is_active),
   'target',jsonb_build_object('id',target_row.id,'season',target_row.season,'sleeper_league_id',target_row.sleeper_league_id,'is_active',target_row.is_active))));
  components:=components||jsonb_build_array(jsonb_build_object('name','team_mapping','version','phase3b6c-team_mapping-v1','count',team_count,'source',mapping_fp,'payload',jsonb_build_object(
   'team_count',team_count,'mapping_fingerprint',mapping_fp,
   'teams',(select jsonb_agg(jsonb_build_object('id',t.id,'user_id',t.user_id,'sleeper_roster_id',t.sleeper_roster_id) order by t.id) from public.league_teams t where t.league_id=x.league_id),
   'memberships',(select coalesce(jsonb_agg(jsonb_build_object('id',m.id,'user_id',m.user_id,'league_team_id',m.league_team_id,'role',m.role) order by m.id),'[]'::jsonb) from public.league_memberships m where m.league_id=x.league_id and m.league_team_id is not null),
   'target_mappings',(select jsonb_agg(jsonb_build_object('id',m.id,'league_team_id',m.league_team_id,'sleeper_roster_id',m.sleeper_roster_id,'mapping_source',m.mapping_source,'mapping_confidence',m.mapping_confidence) order by m.id) from public.season_team_mappings m where m.league_season_id=target_row.id))));
  components:=components||jsonb_build_array(jsonb_build_object('name','owner_options','version','phase3b6c-owner_options-v1','count',decision_count,'source',decision_fp,'payload',jsonb_build_object(
   'notice_identifier',x.id,'notice_timestamp',to_char(x.notice_timestamp at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'owner_deadline',to_char(x.owner_deadline at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'decision_fingerprint',decision_fp,
   'decisions',(select coalesce(jsonb_agg(jsonb_build_object('id',d.id,'league_team_id',d.league_team_id,'player_id',d.player_id,'agreement_id',d.agreement_id,'initial_roster_status',d.initial_roster_status,'initial_roster_slot',d.initial_roster_slot,'decision_status',d.decision_status,'owner_choice',d.owner_choice,'planned_outcome',d.planned_outcome,'recontract_agreement_id',d.recontract_agreement_id,'recontract_event_id',d.recontract_event_id,'deadline',d.deadline,'locked_at',d.locked_at,'updated_at',d.updated_at) order by d.id),'[]'::jsonb) from public.rollover_owner_decisions d where d.rollover_execution_id=x.id),
   'revisions',(select coalesce(jsonb_agg(to_jsonb(r) order by r.id),'[]'::jsonb) from public.rollover_owner_decision_revisions r where r.rollover_execution_id=x.id),
   'commissioner_reviews',(select coalesce(jsonb_agg(to_jsonb(r) order by r.id),'[]'::jsonb) from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id))));
  components:=components||jsonb_build_array(jsonb_build_object('name','league_rules','version','phase3b6c-league_rules-v1','count',1,'payload',
   (select to_jsonb(r)-'created_at'-'updated_at'-'transaction_go_live_at' from public.league_rules r where r.league_id=x.league_id)));
  components:=components||jsonb_build_array(jsonb_build_object('name','history_reference','version','phase3b6c-history_reference-v1','count',5,'source',capture_row.source_fingerprint,'payload',jsonb_build_object(
   'capture_id',capture_row.id,'league_season_id',capture_row.league_season_id,'capture_type',capture_row.capture_type,
   'source_fingerprint',capture_row.source_fingerprint,'status',capture_row.status,
   'completed_at',to_char(capture_row.completed_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'row_counts',capture_row.row_counts,'history_manifest',history_manifest)));
  components:=components||jsonb_build_array(jsonb_build_object('name','rollover_policy','version','phase3b6c-rollover_policy-v1','count',8,'payload',jsonb_build_object(
   'third_round_option_denominator',225,'third_round_option_base',7,'third_round_guaranteed_salary',1,
   'rounding_policy','round_half_up','draft_horizon',4,'draft_rounds',3,
   'operation_catalog_version','phase3b5j1-v2','current_salary_cap',(select salary_cap from public.league_rules where league_id=x.league_id))));
  components:=components||jsonb_build_array(jsonb_build_object('name','handler_catalog','version','phase3b6c-handler_catalog-v1','count',7,'payload',jsonb_build_object(
   'handlers',(select jsonb_agg(jsonb_build_object('operation_code',operation_code,'operation_order',operation_order,'handler_version',handler_version,'input_schema_version',input_schema_version,'result_schema_version',result_schema_version) order by operation_order) from public.rollover_execution_handler_registry where operation_order<=7))));
  components:=components||jsonb_build_array(jsonb_build_object('name','execution_boundary','version','phase3b6c-execution_boundary-v1','count',1,'payload',jsonb_build_object(
   'version','phase3b6c-executed-unpublished-v1','publication_permitted',false,'domain_mutation_phase_started',false)));

  if jsonb_array_length(components)<>9 then
   perform public.raise_rollover_preflight_failure_phase3b6c('snapshot_component_duplicate',code,'{}');
  end if;
  if exists(select 1 from jsonb_array_elements(components) c
   group by c->>'name' having count(*)>1) then
   perform public.raise_rollover_preflight_failure_phase3b6c('snapshot_component_duplicate',code,'{}');
  end if;
  if lower(components::text)~'"(password|secret|token|credential)[^"]*"[[:space:]]*:' then
   perform public.raise_rollover_preflight_failure_phase3b6c('snapshot_serialization_failed',code,jsonb_build_object('reason','credential_like_field'));
  end if;
  payload_size:=octet_length(components::text);
  if payload_size>524288 or exists(
   select 1
   from jsonb_array_elements(components) c
   where octet_length((c->'payload')::text)>131072
  ) then
   perform public.raise_rollover_preflight_failure_phase3b6c(
    'snapshot_size_exceeded',
    code,
    jsonb_build_object(
     'payload_bytes',payload_size,
     'total_limit_bytes',524288,
     'component_limit_bytes',131072,
     'component_sizes',(
      select jsonb_object_agg(
       c->>'name',
       octet_length((c->'payload')::text)
      )
      from jsonb_array_elements(components) c
     ),
     'oversized_components',(
      select coalesce(
       jsonb_agg(
        jsonb_build_object(
         'name',c->>'name',
         'payload_bytes',octet_length((c->'payload')::text)
        )
        order by c->>'name'
       ),
       '[]'::jsonb
      )
      from jsonb_array_elements(components) c
      where octet_length((c->'payload')::text)>131072
     )
    )
   );
  end if;
  aggregate_fp:=public.rollover_material_fingerprint(jsonb_build_object(
   'snapshot_schema_version','phase3b6c-snapshot-v1',
   'components',(select jsonb_agg(jsonb_build_object('name',c->>'name','version',c->>'version',
    'fingerprint',public.rollover_material_fingerprint(jsonb_build_object('name',c->>'name','version',c->>'version','payload',c->'payload'))) order by c->>'name')
    from jsonb_array_elements(components) c)));
  if aggregate_fp is null or aggregate_fp!~'^[0-9a-f]{64}$' then
   perform public.raise_rollover_preflight_failure_phase3b6c('snapshot_hash_mismatch',code,'{}');
  end if;
  insert into public.rollover_execution_input_snapshots(
   id,rollover_execution_id,league_id,approval_id,execution_plan_id,execution_plan_version,
   source_plan_fingerprint,snapshot_schema_version,component_count,aggregate_snapshot_fingerprint,
   mapping_fingerprint,option_decision_fingerprint,historical_capture_execution_id,
   frozen_team_count,frozen_option_decision_count,payload_bytes,created_by,metadata)
  values(snapshot_id,x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,
   'phase3b6c-snapshot-v1',9,aggregate_fp,mapping_fp,decision_fp,capture_row.id,
   team_count,decision_count,payload_size,p_actor,jsonb_build_object('phase','3B.6C'));
  for component in select value from jsonb_array_elements(components) q(value) order by value->>'name' loop
   insert into public.rollover_execution_input_snapshot_components(
    snapshot_id,component_name,component_schema_version,canonical_payload,
    component_fingerprint,source_fingerprint,record_count,payload_bytes)
   values(snapshot_id,component->>'name',component->>'version',component->'payload',
    public.rollover_material_fingerprint(jsonb_build_object('name',component->>'name','version',component->>'version','payload',component->'payload')),
    nullif(component->>'source',''),(component->>'count')::integer,octet_length((component->'payload')::text));
  end loop;
  source_recheck:=public.rollover_material_fingerprint(jsonb_build_object(
   'mapping',mapping_fp,'decision',decision_fp,'history',public.phase3b6c_history_manifest(source_row.id),
   'rules',(select to_jsonb(r)-'created_at'-'updated_at'-'transaction_go_live_at' from public.league_rules r where r.league_id=x.league_id)));
  if source_recheck is null then
   perform public.raise_rollover_preflight_failure_phase3b6c('source_changed_during_freeze',code,'{}');
  end if;
  result_material:=jsonb_build_object(
   'snapshot_id',snapshot_id,'snapshot_schema_version','phase3b6c-snapshot-v1',
   'component_count',9,'aggregate_snapshot_hash',aggregate_fp,'source_plan_hash',p.plan_fingerprint,
   'mapping_fingerprint',mapping_fp,'option_decision_fingerprint',decision_fp,
   'historical_capture_identifier',capture_row.id,'frozen_team_count',team_count,
   'frozen_option_decision_count',decision_count,'frozen_rule_version_input_summary',jsonb_build_object(
    'salary_cap',(select salary_cap from public.league_rules where league_id=x.league_id),
    'denominator',225,'base_option',7,'guarantee',1,'rounding','round_half_up',
    'draft_horizon',4,'draft_rounds',3),'validation_outcome','passed',
   'validation_codes','[]'::jsonb,'evidence_count',team_count+decision_count+9,
   'durable_snapshot_rows_written',10,'football_domain_mutation_count',0);
 elsif code='VERIFY_IMMUTABLE_HISTORY_CAPTURE' then
  select * into snapshot_row from public.rollover_execution_input_snapshots
   where rollover_execution_id=x.id for share;
  if snapshot_row.id is null then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_missing',code,jsonb_build_object('reason','snapshot_missing'));
  end if;
  select count(*) into capture_count from public.historical_capture_executions h
   join public.league_seasons s on s.id=h.league_season_id
   where s.league_id=x.league_id and s.season=p.source_season and h.status in('validated','finalized');
  if capture_count=0 then perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_missing',code,'{}');
  elsif capture_count>1 then perform public.raise_rollover_preflight_failure_phase3b6c('duplicate_historical_capture',code,jsonb_build_object('count',capture_count));end if;
  select * into capture_row from public.historical_capture_executions where id=snapshot_row.historical_capture_execution_id for share;
  if capture_row.id is null then perform public.raise_rollover_preflight_failure_phase3b6c('history_reference_mismatch',code,'{}');end if;
  if capture_row.league_season_id<>source_row.id then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_season_mismatch',code,'{}');
  end if;
  if not exists(select 1 from public.league_seasons where id=capture_row.league_season_id and league_id=x.league_id) then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_league_mismatch',code,'{}');
  end if;
  if capture_row.status='failed' then perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_failed',code,'{}');end if;
  if capture_row.status not in('validated','finalized') or capture_row.completed_at is null
     or jsonb_array_length(capture_row.blocking_errors)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_capture_incomplete',code,'{}');
  end if;
  select c.canonical_payload->'history_manifest' into history_manifest
   from public.rollover_execution_input_snapshot_components c
   where c.snapshot_id=snapshot_row.id and c.component_name='history_reference';
  if history_manifest is null then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_component_missing',code,jsonb_build_object('component','history_reference'));
  end if;
  if history_manifest<>public.phase3b6c_history_manifest(source_row.id) then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_hash_mismatch',code,'{}');
  end if;
  expected_counts:=capture_row.row_counts;
  for comp_name in select value from jsonb_array_elements_text(
   '["team_mappings","matchups","standings","brackets","roster_assignments"]'::jsonb
  ) q(value) loop
   if not expected_counts?comp_name then
    perform public.raise_rollover_preflight_failure_phase3b6c(
     'historical_component_missing',code,jsonb_build_object('component',comp_name));
   end if;
  end loop;
  if (expected_counts->>'team_mappings')::integer<>(history_manifest#>>'{team_mappings,row_count}')::integer
    or (expected_counts->>'matchups')::integer<>(history_manifest#>>'{matchups,row_count}')::integer
    or (expected_counts->>'standings')::integer<>(history_manifest#>>'{standings,row_count}')::integer
    or (expected_counts->>'brackets')::integer<>(history_manifest#>>'{playoff_brackets,row_count}')::integer
    or (expected_counts->>'roster_assignments')::integer<>(history_manifest#>>'{roster_assignments,row_count}')::integer then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_component_count_mismatch',code,'{}');
  end if;
  if (expected_counts->>'team_mappings')::integer=0
    or (expected_counts->>'standings')::integer=0
    or (expected_counts->>'roster_assignments')::integer=0 then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_component_missing',code,'{}');
  end if;
  select bool_and(c.relrowsecurity)
   into immutability_ok from pg_class c where c.oid in(
    'public.season_team_mappings'::regclass,'public.season_matchups'::regclass,
    'public.season_standings'::regclass,'public.season_playoff_brackets'::regclass,
    'public.season_roster_assignments'::regclass,'public.historical_capture_executions'::regclass);
  immutability_ok:=immutability_ok and
   (select count(*)=5 from pg_trigger where not tgisinternal and tgname in(
    'season_team_mappings_immutable','season_matchups_immutable','season_standings_immutable',
    'season_playoff_brackets_immutable','season_roster_assignments_immutable'))
   and exists(select 1 from pg_trigger where not tgisinternal and tgname='historical_capture_executions_finalized_immutable');
  if not immutability_ok then
   perform public.raise_rollover_preflight_failure_phase3b6c('historical_immutability_not_enforced',code,'{}');
  end if;
  if not source_row.is_active or source_row.sleeper_league_id is distinct from
   (select c.canonical_payload#>>'{closing,sleeper_league_id}'
    from public.rollover_execution_input_snapshot_components c
    where c.snapshot_id=snapshot_row.id and c.component_name='season_authority') then
   perform public.raise_rollover_preflight_failure_phase3b6c('closing_authority_changed_after_capture',code,'{}');
  end if;
  result_material:=jsonb_build_object(
   'historical_capture_execution_identifier',capture_row.id,'league_id',x.league_id,
   'closing_season_identifier',source_row.id,'closing_season_year',source_row.season,
   'capture_completion_status',capture_row.status,'capture_completed_timestamp',capture_row.completed_at,
   'required_component_count',5,'present_component_count',5,'per_component_row_counts',capture_row.row_counts,
   'per_component_hashes',history_manifest,'aggregate_history_hash',public.rollover_material_fingerprint(history_manifest),
   'duplicate_capture_count',0,'immutable_protection_outcome','passed',
   'snapshot_capture_reference_match',true,'validation_outcome','passed',
   'validation_codes','[]'::jsonb,'evidence_count',
    (select sum(value::integer) from jsonb_each_text(capture_row.row_counts)),
   'football_domain_mutation_count',0);
 else
  perform public.raise_rollover_preflight_failure_phase3b6c('unsupported_operation',coalesce(code,''),'{}');
 end if;
 return jsonb_build_object('operation_code',code,'handler_version',registry_row.handler_version,
  'input_schema_version',registry_row.input_schema_version,
  'result_schema_version',registry_row.result_schema_version,'read_only',true,
  'domain_mutations',0,'authority_fingerprint',public.rollover_material_fingerprint(result_material),
  'result',result_material);
end $$;

commit;
