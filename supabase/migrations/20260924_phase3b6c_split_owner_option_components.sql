begin;

-- Corrective rollover migration:
-- split the oversized Phase 3B.6C owner-option input component into
-- decisions, revision history, and commissioner-review components.
--
-- Safety limits remain unchanged:
--   131072 bytes per component
--   524288 bytes total
--
-- Existing snapshot-v1 rows remain valid and readable.


-- Snapshot v1 used 9 components.
-- Snapshot v2 replaces owner_options with three components, yielding 11.
do $$
declare r record;
begin
 for r in
  select conname
  from pg_constraint
  where conrelid=
        'public.rollover_execution_input_snapshots'::regclass
    and contype='c'
    and pg_get_constraintdef(oid) ilike '%component_count%'
 loop
  execute format(
   'alter table public.rollover_execution_input_snapshots drop constraint %I',
   r.conname
  );
 end loop;
end $$;

alter table public.rollover_execution_input_snapshots
 add constraint rollover_execution_input_snapshots_component_count_check
 check(component_count in (9,11));

do $$
declare r record;
begin
 for r in
  select conname
  from pg_constraint
  where conrelid=
        'public.rollover_execution_input_snapshot_components'::regclass
    and contype='c'
    and pg_get_constraintdef(oid) ilike '%component_name%'
 loop
  execute format(
   'alter table public.rollover_execution_input_snapshot_components drop constraint %I',
   r.conname
  );
 end loop;
end $$;

alter table public.rollover_execution_input_snapshot_components
 add constraint rollover_execution_input_snapshot_components_name_check
 check(
  component_name in(
   'execution_identity',
   'season_authority',
   'team_mapping',
   'owner_options',
   'owner_option_decisions',
   'owner_option_revisions',
   'owner_option_reviews',
   'league_rules',
   'history_reference',
   'rollover_policy',
   'handler_catalog',
   'execution_boundary'
  )
 );


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
  components:=components||jsonb_build_array(jsonb_build_object(
   'name','owner_option_decisions',
   'version','phase3b6c-owner_option_decisions-v2',
   'count',decision_count,
   'source',decision_fp,
   'payload',jsonb_build_object(
    'notice_identifier',x.id,
    'notice_timestamp',to_char(x.notice_timestamp at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'owner_deadline',to_char(x.owner_deadline at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'decision_fingerprint',decision_fp,
    'decisions',(
     select coalesce(
      jsonb_agg(
       jsonb_build_object(
        'id',d.id,
        'league_team_id',d.league_team_id,
        'player_id',d.player_id,
        'agreement_id',d.agreement_id,
        'initial_roster_status',d.initial_roster_status,
        'initial_roster_slot',d.initial_roster_slot,
        'decision_status',d.decision_status,
        'owner_choice',d.owner_choice,
        'planned_outcome',d.planned_outcome,
        'recontract_agreement_id',d.recontract_agreement_id,
        'recontract_event_id',d.recontract_event_id,
        'deadline',d.deadline,
        'locked_at',d.locked_at,
        'updated_at',d.updated_at
       )
       order by d.id
      ),
      '[]'::jsonb
     )
     from public.rollover_owner_decisions d
     where d.rollover_execution_id=x.id
    )
   )
  ));

  components:=components||jsonb_build_array(jsonb_build_object(
   'name','owner_option_revisions',
   'version','phase3b6c-owner_option_revisions-v2',
   'count',(
    select count(*)
    from public.rollover_owner_decision_revisions r
    where r.rollover_execution_id=x.id
   ),
   'source',decision_fp,
   'payload',jsonb_build_object(
    'revisions',(
     select coalesce(
      jsonb_agg(to_jsonb(r) order by r.id),
      '[]'::jsonb
     )
     from public.rollover_owner_decision_revisions r
     where r.rollover_execution_id=x.id
    )
   )
  ));

  components:=components||jsonb_build_array(jsonb_build_object(
   'name','owner_option_reviews',
   'version','phase3b6c-owner_option_reviews-v2',
   'count',(
    select count(*)
    from public.rollover_commissioner_reviews r
    where r.rollover_execution_id=x.id
   ),
   'source',decision_fp,
   'payload',jsonb_build_object(
    'commissioner_reviews',(
     select coalesce(
      jsonb_agg(to_jsonb(r) order by r.id),
      '[]'::jsonb
     )
     from public.rollover_commissioner_reviews r
     where r.rollover_execution_id=x.id
    )
   )
  ));

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

  if jsonb_array_length(components)<>11 then
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
   'snapshot_schema_version','phase3b6c-snapshot-v2',
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
   'phase3b6c-snapshot-v2',11,aggregate_fp,mapping_fp,decision_fp,capture_row.id,
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
   'snapshot_id',snapshot_id,'snapshot_schema_version','phase3b6c-snapshot-v2',
   'component_count',11,'aggregate_snapshot_hash',aggregate_fp,'source_plan_hash',p.plan_fingerprint,
   'mapping_fingerprint',mapping_fp,'option_decision_fingerprint',decision_fp,
   'historical_capture_identifier',capture_row.id,'frozen_team_count',team_count,
   'frozen_option_decision_count',decision_count,'frozen_rule_version_input_summary',jsonb_build_object(
    'salary_cap',(select salary_cap from public.league_rules where league_id=x.league_id),
    'denominator',225,'base_option',7,'guarantee',1,'rounding','round_half_up',
    'draft_horizon',4,'draft_rounds',3),'validation_outcome','passed',
   'validation_codes','[]'::jsonb,'evidence_count',team_count+decision_count+11,
   'durable_snapshot_rows_written',12,'football_domain_mutation_count',0);
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

create or replace function public.execute_rollover_typed_handler_phase3b6c1_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 base_result jsonb;s public.rollover_execution_input_snapshots%rowtype;
 v2 public.rollover_owner_option_snapshot_v2%rowtype;c public.rollover_execution_input_snapshot_components%rowtype;c_rev public.rollover_execution_input_snapshot_components%rowtype;c_review public.rollover_execution_input_snapshot_components%rowtype;
 x public.rollover_executions%rowtype;src public.league_seasons%rowtype;tgt public.league_seasons%rowtype;
 d jsonb;live_d jsonb;r jsonb;rv jsonb;cases jsonb:='[]';reviews jsonb:='[]';case_payload jsonb;review_payload jsonb;
 agreement public.contract_agreements%rowtype;source_obligation public.contract_seasons%rowtype;
 option_obligation public.contract_seasons%rowtype;player public.player_universe%rowtype;
 latest_revision jsonb;review_row jsonb;authority public.league_membership_authority_events%rowtype;
 draft_round integer;is_third boolean;option_term integer;submitted_choice text;submitted_at timestamptz;
 submitted_by uuid;defaulted boolean;before_deadline boolean;taxi_status text;eligible boolean;
 case_fp text;review_fp text;case_set_fp text;review_set_fp text;aggregate_fp text;v2_id uuid:=gen_random_uuid();
 payload_size integer;review_count integer:=0;component_fp text;rules jsonb;owner_payload jsonb;
begin
 if p_operation->>'operation_type'<>'FREEZE_FINAL_EXECUTION_INPUTS' then
  return public.execute_rollover_typed_handler_phase3b6c_private(
   p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 end if;
 base_result:=public.execute_rollover_typed_handler_phase3b6c_private(
  p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=p_rollover_execution_id;
 select * into v2 from public.rollover_owner_option_snapshot_v2 where snapshot_id=s.id;
 if v2.id is not null then
  return base_result||jsonb_build_object('owner_option_snapshot_v2',jsonb_build_object(
   'id',v2.id,'schema_version',v2.schema_version,'case_count',v2.case_count,
   'review_count',v2.review_count,'aggregate_fingerprint',v2.aggregate_fingerprint,'rows_written',0));
 end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into src from public.league_seasons where league_id=x.league_id and season=x.source_season;
 select * into tgt from public.league_seasons where league_id=x.league_id and season=x.target_season;
 -- Backwards-compatible reader:
 -- old snapshots use one owner_options component;
 -- snapshot-v2 uses three bounded immutable components.
 select * into c
 from public.rollover_execution_input_snapshot_components
 where snapshot_id=s.id and component_name='owner_options';

 if c.id is not null then
  if c.component_schema_version<>'phase3b6c-owner_options-v1' then
   perform public.raise_phase3b6c1_failure(
    'option_snapshot_schema_unsupported',
    jsonb_build_object(
     'component','owner_options',
     'schema',c.component_schema_version
    )
   );
  end if;

  owner_payload:=c.canonical_payload;
  component_fp:=c.component_fingerprint;

 else
  select * into c
  from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id
    and component_name='owner_option_decisions';

  select * into c_rev
  from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id
    and component_name='owner_option_revisions';

  select * into c_review
  from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id
    and component_name='owner_option_reviews';

  if c.id is null
     or c_rev.id is null
     or c_review.id is null
     or c.component_schema_version
        <>'phase3b6c-owner_option_decisions-v2'
     or c_rev.component_schema_version
        <>'phase3b6c-owner_option_revisions-v2'
     or c_review.component_schema_version
        <>'phase3b6c-owner_option_reviews-v2'
  then
   perform public.raise_phase3b6c1_failure(
    'option_snapshot_schema_unsupported',
    jsonb_build_object(
     'reason','split_owner_option_component_missing_or_invalid'
    )
   );
  end if;

  owner_payload :=
      c.canonical_payload
      || c_rev.canonical_payload
      || c_review.canonical_payload;

  component_fp :=
   public.rollover_material_fingerprint(
    jsonb_build_object(
     'schema_version',
       'phase3b6c-owner-options-split-v2',
     'decisions_component_fingerprint',
       c.component_fingerprint,
     'revisions_component_fingerprint',
       c_rev.component_fingerprint,
     'reviews_component_fingerprint',
       c_review.component_fingerprint
    )
   );
 end if;
 select canonical_payload into rules from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id and component_name='rollover_policy';

 perform 1 from public.contract_agreements a join jsonb_array_elements(owner_payload->'decisions') q(value)
  on a.id=(q.value->>'agreement_id')::uuid order by a.id for share of a;
 perform 1 from public.contract_seasons cs join jsonb_array_elements(owner_payload->'decisions') q(value)
  on cs.contract_id=(q.value->>'agreement_id')::uuid order by cs.id for share of cs;
 perform 1 from public.player_universe u join jsonb_array_elements(owner_payload->'decisions') q(value)
  on u.sleeper_id=q.value->>'player_id' order by u.sleeper_id for share of u;
 perform 1 from public.league_membership_authority_events e where e.league_id=x.league_id order by e.effective_at,e.id for share;

 for d in select value from jsonb_array_elements(owner_payload->'decisions') q(value) order by (value->>'id')::uuid loop
  if nullif(d->>'id','') is null or nullif(d->>'agreement_id','') is null
   or nullif(d->>'player_id','') is null or nullif(d->>'league_team_id','') is null then
   perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('reason','option_case_identity_missing'));
  end if;
  select * into agreement from public.contract_agreements where id=(d->>'agreement_id')::uuid;
  select to_jsonb(od) into live_d from public.rollover_owner_decisions od
   where od.id=(d->>'id')::uuid and od.rollover_execution_id=x.id;
  select * into source_obligation from public.contract_seasons where contract_id=agreement.id and season=x.source_season;
  select * into option_obligation from public.contract_seasons where contract_id=agreement.id and season=x.target_season and is_option_year;
  select * into player from public.player_universe where sleeper_id=d->>'player_id';
  if agreement.id is null or agreement.league_id<>x.league_id or agreement.league_team_id<>(d->>'league_team_id')::uuid
    or agreement.player_id<>d->>'player_id' or agreement.contract_type='unknown' then
   perform public.raise_phase3b6c1_failure('option_contract_classification_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  if option_obligation.id is null or nullif(option_obligation.option_type,'') is null then
   perform public.raise_phase3b6c1_failure('option_type_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  if source_obligation.id is null then
   perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('reason','source_salary_missing'));
  end if;
  draft_round:=player.draft_round;
  if agreement.contract_type='rookie' and draft_round is null then
   perform public.raise_phase3b6c1_failure('rookie_draft_round_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  is_third:=agreement.contract_type='rookie' and draft_round=3;
  if is_third and (player.is_rookie_contract is false or player.is_rookie_contract is null) then
   perform public.raise_phase3b6c1_failure('third_round_classification_ambiguous',jsonb_build_object('decision_id',d->>'id'));
  end if;
  option_term:=(select count(*) from public.contract_seasons where contract_id=agreement.id and season>=x.target_season and is_option_year);
  if option_term=0 then perform public.raise_phase3b6c1_failure('option_term_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if option_obligation.guaranteed_salary is null or (is_third and option_obligation.guaranteed_salary<>1) then
   perform public.raise_phase3b6c1_failure('guaranteed_salary_evidence_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  latest_revision:=(select value from jsonb_array_elements(owner_payload->'revisions') q(value)
   where value->>'owner_decision_id'=d->>'id' order by (value->>'revision_number')::integer desc,(value->>'id')::uuid desc limit 1);
  if exists(select 1 from jsonb_array_elements(owner_payload->'revisions') q(value)
   where value->>'owner_decision_id'=d->>'id' group by value->>'revision_number' having count(*)>1) then
   perform public.raise_phase3b6c1_failure('owner_response_evidence_incomplete',jsonb_build_object('reason','revision_conflict'));
  end if;
  if live_d is null then perform public.raise_phase3b6c1_failure(
   'option_snapshot_v2_incomplete',jsonb_build_object('reason','decision_identity_missing'));end if;
  submitted_choice:=live_d->>'owner_choice';submitted_at:=nullif(live_d->>'submitted_at','')::timestamptz;
  submitted_by:=nullif(live_d->>'submitted_by','')::uuid;
  defaulted:=submitted_choice is null and live_d->>'decision_status'='no_response';
  if not defaulted and submitted_choice is null then perform public.raise_phase3b6c1_failure('owner_response_identity_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if not defaulted and submitted_at is null then perform public.raise_phase3b6c1_failure('owner_response_timestamp_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if not defaulted and submitted_by is null then perform public.raise_phase3b6c1_failure('owner_response_identity_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if submitted_by is not null and not exists(select 1 from (select value from jsonb_array_elements(
   (select canonical_payload->'memberships' from public.rollover_execution_input_snapshot_components where snapshot_id=s.id and component_name='team_mapping')) q(value)) z
   where z.value->>'user_id'=submitted_by::text and z.value->>'league_team_id'=d->>'league_team_id')
   and not exists(select 1 from public.league_membership_authority_events e
    where e.league_id=x.league_id and e.user_id=submitted_by and e.event_type='authority_granted'
     and e.effective_at<=submitted_at and not exists(select 1 from public.league_membership_authority_events z
      where z.league_id=e.league_id and z.user_id=e.user_id and z.event_type='authority_revoked'
       and z.effective_at>e.effective_at and z.effective_at<=submitted_at)) then
   perform public.raise_phase3b6c1_failure('owner_response_actor_mismatch',jsonb_build_object('decision_id',d->>'id'));
  end if;
  before_deadline:=coalesce(submitted_at<=nullif(d->>'deadline','')::timestamptz,false);
  taxi_status:=lower(coalesce(d->>'initial_roster_slot',d->>'initial_roster_status','unknown'));
  eligible:=not(is_third and taxi_status='taxi');
  review_row:=(select value from jsonb_array_elements(owner_payload->'commissioner_reviews') q(value)
   where value->>'player_id'=d->>'player_id' and value->>'agreement_id'=d->>'agreement_id'
   order by (value->>'revision_number')::integer desc,(value->>'id')::uuid desc limit 1);
  case_payload:=jsonb_build_object(
   'eligible_option_case_id',d->>'id','league_id',x.league_id,'closing_season_id',src.id,'closing_season',x.source_season,
   'target_season_id',tgt.id,'target_season',x.target_season,'contract_agreement_id',agreement.id,
   'player_id',agreement.player_id,'league_team_id',agreement.league_team_id,'decision_id',d->>'id',
   'latest_revision_id',latest_revision->>'id','commissioner_review_id',review_row->>'id',
   'contract_type',agreement.contract_type,'option_type',option_obligation.option_type,
   'option_eligibility_type',case when is_third then 'third_round_rookie_owner_option' else 'other_owner_option' end,
   'rookie_class_year',player.rookie_class_year,'rookie_draft_year',player.draft_year,
   'rookie_draft_round',draft_round,'is_third_round',is_third,'option_term',option_term,
   'option_exercise_season',x.target_season,'guaranteed_salary',option_obligation.guaranteed_salary,
   'current_contract_salary',source_obligation.salary,
   'source_agreement_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('agreement',to_jsonb(agreement)-'created_at'-'updated_at','source_obligation',to_jsonb(source_obligation)-'created_at'-'updated_at','option_obligation',to_jsonb(option_obligation)-'created_at'-'updated_at','player_classification',jsonb_build_object('rookie_class_year',player.rookie_class_year,'draft_year',player.draft_year,'draft_round',player.draft_round,'is_rookie_contract',player.is_rookie_contract))),
   'submitted_choice',submitted_choice,'submitted_at',submitted_at,'submitted_by',submitted_by,
   'submitting_league_team_id',case when submitted_by is null then null else d->>'league_team_id' end,
   'response_source',case when latest_revision is null then 'rollover_owner_decisions' else 'rollover_owner_decision_revisions' end,
   'response_status',live_d->>'decision_status','response_before_deadline',before_deadline,
   'is_default_nonresponse',defaulted,'notice_timestamp',owner_payload->>'notice_timestamp',
   'deadline_timestamp',owner_payload->>'owner_deadline',
   'response_reason_code',case when defaulted then 'no_response_default' else 'frozen_owner_response' end,
   'response_evidence',jsonb_build_object('decision_evidence',coalesce(live_d->'evidence','{}'::jsonb),'latest_revision',latest_revision),
   'revision_history',coalesce((select jsonb_agg(value order by (value->>'revision_number')::integer,(value->>'id')::uuid) from jsonb_array_elements(owner_payload->'revisions') q(value) where value->>'owner_decision_id'=d->>'id'),'[]'::jsonb),
   'duplicate_conflict_evidence',jsonb_build_object('duplicate_decision_count',1,'revision_conflict',false),
   'taxi_status',taxi_status,'taxi_source','frozen_initial_roster_slot','taxi_cutoff_timestamp',owner_payload->>'owner_deadline',
   'taxi_evidence_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('decision_id',d->>'id','slot',taxi_status,'cutoff',owner_payload->>'owner_deadline')),
   'option_exercise_eligible',eligible,'exercise_eligibility_reason_code',case when eligible then 'eligible' else 'third_round_taxi_prohibited' end,
   'salary_rule_linkage',jsonb_build_object('applies',is_third,'salary_cap',rules->>'current_salary_cap','denominator',225,'base_option',7,'guarantee',1,'rounding','round_half_up','no_compounding',true));
  case_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-option-case-v2','payload',case_payload));
  cases:=cases||jsonb_build_array(case_payload||jsonb_build_object('case_fingerprint',case_fp));

  if review_row is not null then
   if nullif(review_row->>'decision_by','') is null or nullif(review_row->>'decision_at','') is null then
    perform public.raise_phase3b6c1_failure('review_authority_history_missing',jsonb_build_object('review_id',review_row->>'id'));
   end if;
   select * into authority from public.league_membership_authority_events e
    where e.league_id=x.league_id and e.user_id=(review_row->>'decision_by')::uuid
     and e.event_type='authority_granted' and e.effective_at<=(review_row->>'decision_at')::timestamptz
     and not exists(select 1 from public.league_membership_authority_events z
      where z.league_id=e.league_id and z.user_id=e.user_id and z.event_type='authority_revoked'
       and z.effective_at>e.effective_at and z.effective_at<=(review_row->>'decision_at')::timestamptz)
    order by e.effective_at desc,e.id desc limit 1;
   if authority.id is null then perform public.raise_phase3b6c1_failure('reviewer_not_authorized_at_review_time',jsonb_build_object('review_id',review_row->>'id'));end if;
   review_payload:=jsonb_build_object('review_id',review_row->>'id','eligible_option_case_id',d->>'id',
    'reviewer_user_id',review_row->>'decision_by','reviewer_membership_id',authority.membership_id,
    'reviewer_league_team_id',null,'review_timestamp',review_row->>'decision_at',
    'disposition',coalesce(review_row->>'outcome',review_row->>'approved_action',review_row->>'review_status'),
    'review_state',review_row->>'review_state','superseded',review_row->>'review_state'='superseded',
    'reason_code',coalesce(review_row#>>'{evidence,reason_code}',review_row#>>'{metadata,reason_code}','frozen_commissioner_review'),
    'reason_explanation',coalesce(review_row#>>'{evidence,reason}',review_row#>>'{metadata,reason}','frozen reviewed disposition'),
    'decision_id',d->>'id','contract_agreement_id',agreement.id,'player_id',agreement.player_id,
    'authority_event_id',authority.id,'authority_source_version','league-membership-authority-events-v1',
    'authorized_at_review_time',true,'review_payload',review_row);
   review_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-review-v2','payload',review_payload));
   reviews:=reviews||jsonb_build_array(review_payload||jsonb_build_object('review_fingerprint',review_fp));review_count:=review_count+1;
  end if;
 end loop;
 if jsonb_array_length(cases)<>c.record_count then perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('expected',c.record_count,'actual',jsonb_array_length(cases)));end if;
 payload_size:=octet_length(cases::text)+octet_length(reviews::text);
 if payload_size>524288 or lower((cases||reviews)::text)~'"(password|secret|token|credential)[^"]*"[[:space:]]*:' then
  perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('reason','payload_safety'));
 end if;
 case_set_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-option-case-set-v2','cases',cases));
 review_set_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-review-set-v2','reviews',reviews));
 aggregate_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-owner-options-v2','source_v1_component_fingerprint',component_fp,'case_set_fingerprint',case_set_fp,'review_set_fingerprint',review_set_fp));
 insert into public.rollover_owner_option_snapshot_v2(id,snapshot_id,rollover_execution_id,league_id,schema_version,
  source_v1_component_fingerprint,case_count,review_count,case_set_fingerprint,review_set_fingerprint,
  aggregate_fingerprint,payload_bytes,created_by)
 values(v2_id,s.id,x.id,x.league_id,'phase3b6c1-owner-options-v2',component_fp,jsonb_array_length(cases),
  review_count,case_set_fp,review_set_fp,aggregate_fp,payload_size,p_actor);
 for case_payload in select value from jsonb_array_elements(cases) q(value) order by (value->>'eligible_option_case_id')::uuid loop
  insert into public.rollover_owner_option_snapshot_v2_cases(
   owner_option_snapshot_v2_id,eligible_option_case_id,league_id,closing_season_id,closing_season,target_season_id,target_season,
   contract_agreement_id,player_id,league_team_id,decision_id,latest_revision_id,commissioner_review_id,
   contract_type,option_type,option_eligibility_type,rookie_class_year,rookie_draft_year,rookie_draft_round,is_third_round,
   option_term,option_exercise_season,guaranteed_salary,current_contract_salary,source_agreement_fingerprint,
   submitted_choice,submitted_at,submitted_by,submitting_league_team_id,response_source,response_status,response_before_deadline,
   is_default_nonresponse,notice_timestamp,deadline_timestamp,response_reason_code,response_evidence,revision_history,
   duplicate_conflict_evidence,taxi_status,taxi_source,taxi_cutoff_timestamp,taxi_evidence_fingerprint,
   option_exercise_eligible,exercise_eligibility_reason_code,salary_rule_linkage,case_fingerprint,payload_bytes)
  values(v2_id,(case_payload->>'eligible_option_case_id')::uuid,x.league_id,(case_payload->>'closing_season_id')::uuid,
   (case_payload->>'closing_season')::integer,(case_payload->>'target_season_id')::uuid,(case_payload->>'target_season')::integer,
   (case_payload->>'contract_agreement_id')::uuid,case_payload->>'player_id',(case_payload->>'league_team_id')::uuid,
   (case_payload->>'decision_id')::uuid,nullif(case_payload->>'latest_revision_id','')::uuid,nullif(case_payload->>'commissioner_review_id','')::uuid,
   case_payload->>'contract_type',case_payload->>'option_type',case_payload->>'option_eligibility_type',
   nullif(case_payload->>'rookie_class_year','')::integer,nullif(case_payload->>'rookie_draft_year','')::integer,
   nullif(case_payload->>'rookie_draft_round','')::integer,(case_payload->>'is_third_round')::boolean,
   (case_payload->>'option_term')::integer,(case_payload->>'option_exercise_season')::integer,
   (case_payload->>'guaranteed_salary')::numeric,(case_payload->>'current_contract_salary')::numeric,
   case_payload->>'source_agreement_fingerprint',case_payload->>'submitted_choice',nullif(case_payload->>'submitted_at','')::timestamptz,
   nullif(case_payload->>'submitted_by','')::uuid,nullif(case_payload->>'submitting_league_team_id','')::uuid,
   case_payload->>'response_source',case_payload->>'response_status',(case_payload->>'response_before_deadline')::boolean,
   (case_payload->>'is_default_nonresponse')::boolean,(case_payload->>'notice_timestamp')::timestamptz,
   (case_payload->>'deadline_timestamp')::timestamptz,case_payload->>'response_reason_code',case_payload->'response_evidence',
   case_payload->'revision_history',case_payload->'duplicate_conflict_evidence',case_payload->>'taxi_status',case_payload->>'taxi_source',
   (case_payload->>'taxi_cutoff_timestamp')::timestamptz,case_payload->>'taxi_evidence_fingerprint',
   (case_payload->>'option_exercise_eligible')::boolean,case_payload->>'exercise_eligibility_reason_code',
   case_payload->'salary_rule_linkage',case_payload->>'case_fingerprint',octet_length(case_payload::text));
 end loop;
 for review_payload in select value from jsonb_array_elements(reviews) q(value) order by (value->>'review_id')::uuid loop
  insert into public.rollover_owner_option_snapshot_v2_reviews(
   owner_option_snapshot_v2_id,review_id,eligible_option_case_id,reviewer_user_id,reviewer_membership_id,
   reviewer_league_team_id,review_timestamp,disposition,review_state,superseded,reason_code,reason_explanation,
   decision_id,contract_agreement_id,player_id,authority_event_id,authority_source_version,
   authorized_at_review_time,review_payload,review_fingerprint,payload_bytes)
  values(v2_id,(review_payload->>'review_id')::uuid,(review_payload->>'eligible_option_case_id')::uuid,
   (review_payload->>'reviewer_user_id')::uuid,(review_payload->>'reviewer_membership_id')::uuid,
   nullif(review_payload->>'reviewer_league_team_id','')::uuid,(review_payload->>'review_timestamp')::timestamptz,
   review_payload->>'disposition',review_payload->>'review_state',(review_payload->>'superseded')::boolean,
   review_payload->>'reason_code',review_payload->>'reason_explanation',(review_payload->>'decision_id')::uuid,
   (review_payload->>'contract_agreement_id')::uuid,review_payload->>'player_id',(review_payload->>'authority_event_id')::uuid,
   review_payload->>'authority_source_version',(review_payload->>'authorized_at_review_time')::boolean,
   review_payload->'review_payload',review_payload->>'review_fingerprint',octet_length(review_payload::text));
 end loop;
 return base_result||jsonb_build_object('owner_option_snapshot_v2',jsonb_build_object(
  'id',v2_id,'schema_version','phase3b6c1-owner-options-v2','case_count',jsonb_array_length(cases),
  'review_count',review_count,'case_set_fingerprint',case_set_fp,'review_set_fingerprint',review_set_fp,
  'aggregate_fingerprint',aggregate_fp,'rows_written',1+jsonb_array_length(cases)+review_count));
end $$;

commit;
