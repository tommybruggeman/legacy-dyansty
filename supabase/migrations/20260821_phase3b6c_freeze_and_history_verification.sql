begin;

insert into public.rollover_execution_handler_registry
 (operation_code,operation_order,handler_version,input_schema_version,result_schema_version,
  execution_owner,mutation_class,metadata)
values
 ('FREEZE_FINAL_EXECUTION_INPUTS',6,1,'phase3b6c-freeze-input-v1','phase3b6c-freeze-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6C','domain','execution_control')),
 ('VERIFY_IMMUTABLE_HISTORY_CAPTURE',7,1,'phase3b6c-history-input-v1','phase3b6c-history-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6C','domain','history'));

create table public.rollover_execution_input_snapshots (
 id uuid primary key default gen_random_uuid(),
 rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),
 approval_id uuid not null references public.rollover_execution_plan_approvals(id),
 execution_plan_id uuid not null references public.rollover_execution_plans(id),
 execution_plan_version integer not null check(execution_plan_version>0),
 source_plan_fingerprint text not null check(source_plan_fingerprint~'^[0-9a-f]{64}$'),
 snapshot_schema_version text not null check(snapshot_schema_version='phase3b6c-snapshot-v1'),
 component_count integer not null check(component_count=9),
 aggregate_snapshot_fingerprint text not null check(aggregate_snapshot_fingerprint~'^[0-9a-f]{64}$'),
 mapping_fingerprint text not null check(mapping_fingerprint~'^[0-9a-f]{64}$'),
 option_decision_fingerprint text not null check(option_decision_fingerprint~'^[0-9a-f]{64}$'),
 historical_capture_execution_id uuid not null references public.historical_capture_executions(id),
 frozen_team_count integer not null check(frozen_team_count>0),
 frozen_option_decision_count integer not null check(frozen_option_decision_count>=0),
 payload_bytes integer not null check(payload_bytes between 1 and 524288),
 created_by uuid not null,
 created_at timestamptz not null default clock_timestamp(),
 metadata jsonb not null default '{}'::jsonb check(jsonb_typeof(metadata)='object')
);

create table public.rollover_execution_input_snapshot_components (
 id uuid primary key default gen_random_uuid(),
 snapshot_id uuid not null references public.rollover_execution_input_snapshots(id),
 component_name text not null check(component_name in(
  'execution_identity','season_authority','team_mapping','owner_options','league_rules',
  'history_reference','rollover_policy','handler_catalog','execution_boundary'
 )),
 component_schema_version text not null check(component_schema_version~'^phase3b6c-[a-z_]+-v1$'),
 canonical_payload jsonb not null check(jsonb_typeof(canonical_payload)='object'),
 component_fingerprint text not null check(component_fingerprint~'^[0-9a-f]{64}$'),
 source_fingerprint text check(source_fingerprint is null or source_fingerprint~'^[0-9a-f]{64}$'),
 record_count integer not null check(record_count>=0),
 payload_bytes integer not null check(payload_bytes between 1 and 131072),
 created_at timestamptz not null default clock_timestamp(),
 unique(snapshot_id,component_name)
);
create index rollover_input_snapshot_components_snapshot_idx
 on public.rollover_execution_input_snapshot_components(snapshot_id,component_name);
create index rollover_input_snapshots_scope_idx
 on public.rollover_execution_input_snapshots(league_id,execution_plan_id);

create or replace function public.reject_rollover_input_snapshot_mutation()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin raise exception 'execution input snapshot evidence is immutable';end $$;
create trigger rollover_execution_input_snapshots_immutable
before update or delete on public.rollover_execution_input_snapshots
for each row execute function public.reject_rollover_input_snapshot_mutation();
create trigger rollover_execution_input_snapshot_components_immutable
before update or delete on public.rollover_execution_input_snapshot_components
for each row execute function public.reject_rollover_input_snapshot_mutation();

create or replace function public.reject_finalized_historical_capture_mutation()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if old.status in('validated','finalized') then
  raise exception 'validated historical capture execution is immutable';
 end if;
 return case when tg_op='DELETE' then old else new end;
end $$;
create trigger historical_capture_executions_finalized_immutable
before update or delete on public.historical_capture_executions
for each row execute function public.reject_finalized_historical_capture_mutation();

alter table public.rollover_execution_input_snapshots enable row level security;
alter table public.rollover_execution_input_snapshot_components enable row level security;
revoke all on public.rollover_execution_input_snapshots,
 public.rollover_execution_input_snapshot_components from public,anon,authenticated;
grant select on public.rollover_execution_input_snapshots,
 public.rollover_execution_input_snapshot_components to authenticated;
grant select,insert on public.rollover_execution_input_snapshots,
 public.rollover_execution_input_snapshot_components to service_role;

create policy rollover_input_snapshots_commissioner_read
 on public.rollover_execution_input_snapshots for select to authenticated
 using(exists(
  select 1 from public.league_memberships m
  where m.league_id=rollover_execution_input_snapshots.league_id
   and m.user_id=auth.uid() and m.role='commissioner'
 ));
create policy rollover_input_snapshot_components_commissioner_read
 on public.rollover_execution_input_snapshot_components for select to authenticated
 using(exists(
  select 1 from public.rollover_execution_input_snapshots s
  join public.league_memberships m on m.league_id=s.league_id
  where s.id=snapshot_id and m.user_id=auth.uid() and m.role='commissioner'
 ));

create or replace function public.phase3b6c_history_manifest(p_league_season_id uuid)
returns jsonb language sql security definer set search_path=pg_catalog,public stable as $$
 select jsonb_build_object(
  'team_mappings',jsonb_build_object(
   'row_count',(select count(*) from public.season_team_mappings where league_season_id=p_league_season_id),
   'fingerprint',public.rollover_material_fingerprint(coalesce((select jsonb_agg(jsonb_build_object(
    'id',id,'league_team_id',league_team_id,'sleeper_roster_id',sleeper_roster_id,
    'sleeper_owner_id',sleeper_owner_id,'sleeper_user_id',sleeper_user_id,
    'team_name_snapshot',team_name_snapshot,'owner_name_snapshot',owner_name_snapshot,
    'mapping_source',mapping_source,'mapping_confidence',mapping_confidence) order by id)
    from public.season_team_mappings where league_season_id=p_league_season_id),'[]'::jsonb))),
  'matchups',jsonb_build_object(
   'row_count',(select count(*) from public.season_matchups where league_season_id=p_league_season_id),
   'fingerprint',public.rollover_material_fingerprint(coalesce((select jsonb_agg(jsonb_build_object(
    'id',id,'week',week,'sleeper_matchup_id',sleeper_matchup_id,
    'league_team_1_id',league_team_1_id,'league_team_2_id',league_team_2_id,
    'sleeper_roster_1_id',sleeper_roster_1_id,'sleeper_roster_2_id',sleeper_roster_2_id,
    'team_1_points',team_1_points,'team_2_points',team_2_points,
    'winner_league_team_id',winner_league_team_id,'result',result,'phase',phase,
    'source_payload',source_payload) order by id)
    from public.season_matchups where league_season_id=p_league_season_id),'[]'::jsonb))),
  'standings',jsonb_build_object(
   'row_count',(select count(*) from public.season_standings where league_season_id=p_league_season_id),
   'fingerprint',public.rollover_material_fingerprint(coalesce((select jsonb_agg(jsonb_build_object(
    'id',id,'league_team_id',league_team_id,'wins',wins,'losses',losses,'ties',ties,
    'points_for',points_for,'points_against',points_against,'standing_points',standing_points,
    'regular_season_rank',regular_season_rank,'playoff_seed',playoff_seed,'final_finish',final_finish,
    'is_champion',is_champion,'streak',streak,'source_payload',source_payload) order by id)
    from public.season_standings where league_season_id=p_league_season_id),'[]'::jsonb))),
  'playoff_brackets',jsonb_build_object(
   'row_count',(select count(*) from public.season_playoff_brackets where league_season_id=p_league_season_id),
   'fingerprint',public.rollover_material_fingerprint(coalesce((select jsonb_agg(jsonb_build_object(
    'id',id,'bracket_type',bracket_type,'round',round,
    'sleeper_bracket_match_id',sleeper_bracket_match_id,'team_1_id',team_1_id,
    'team_2_id',team_2_id,'winner_league_team_id',winner_league_team_id,
    'loser_league_team_id',loser_league_team_id,'placement',placement,'source_payload',source_payload) order by id)
    from public.season_playoff_brackets where league_season_id=p_league_season_id),'[]'::jsonb))),
  'roster_assignments',jsonb_build_object(
   'row_count',(select count(*) from public.season_roster_assignments where league_season_id=p_league_season_id),
   'fingerprint',public.rollover_material_fingerprint(coalesce((select jsonb_agg(jsonb_build_object(
    'id',id,'league_team_id',league_team_id,'canonical_player_id',canonical_player_id,
    'sleeper_player_id',sleeper_player_id,'player_name_snapshot',player_name_snapshot,
    'roster_designation',roster_designation,'source',source) order by id)
    from public.season_roster_assignments where league_season_id=p_league_season_id),'[]'::jsonb)))
 )
$$;

create or replace function public.raise_rollover_preflight_failure_phase3b6c(
 p_failure_code text,p_operation_code text,p_details jsonb default '{}'::jsonb
) returns void language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 raise exception using errcode='P0001',message=p_failure_code,
  detail=jsonb_build_object('operation_code',p_operation_code,'failure_code',p_failure_code,
   'details',coalesce(p_details,'{}'::jsonb))::text;
end $$;

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

-- Additive engine version preserving the certified Phase 3B.5I transaction,
-- approval, cutover lock, replay, ordered dispatch, rollback, and audit model.
create or replace function public.execute_rollover_plan_phase3b6c_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;
 p public.rollover_execution_plans%rowtype;l public.rollover_execution_locks%rowtype;
 prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;failed_op jsonb;
 failure_sqlstate text;failure_message text;failure_detail text;failure_hint text;failure_context text;result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;
 if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null or nullif(p_request->>'approval_id','') is null
    or nullif(p_request->>'execution_plan_id','') is null or nullif(p_request->>'expected_plan_fingerprint','') is null
    or nullif(p_request->>'expected_execution_status','') is null or nullif(p_request->>'expected_approval_status','') is null then
  raise exception 'complete execution assertions required';
 end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6c','execution_id',x.id,
  'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then
  if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;
  return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);
 end if;
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::integer
    or p.plan_version<>a.execution_plan_version or p.plan_status<>'approved_for_execution' or not p.approved_for_execution
    or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint'
    or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then
  raise exception 'stale or invalid approved execution plan';
 end if;
 select * into l from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=a.id
  and execution_plan_id=p.id and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint
  and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;
 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,
  execution_plan_version,plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,
  started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',
  p.operation_count,run_started,p_actor,jsonb_build_object('engine_version','phase3b6c-v1',
   'typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted
      or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then
    raise exception 'ordered operation sequence mismatch';
   end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY',
    'VERIFY_TARGET_SLEEPER_LINKAGE','VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED',
    'FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE') then
    handler_result:=public.execute_rollover_typed_handler_phase3b6c_private(op,x.id,p.id,a.id,p_actor);
   else raise exception 'unsupported Phase 3B.6C operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
    operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint',
    'completed',op_started,clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),
    handler_result,jsonb_build_object('domain_mutations',0,'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
   completed:=completed+1;
  end loop;
 exception when others then
  get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,
   failure_detail=pg_exception_detail,failure_hint=pg_exception_hint,failure_context=pg_exception_context;
 end;
 if failure_message is not null then
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,
   operation_index,operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),
   coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),
   'failed',coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),
   jsonb_build_object('failure_code',failure_message,'sqlstate',failure_sqlstate,
    'detail',left(coalesce(failure_detail,''),4096),'hint',left(coalesce(failure_hint,''),1024),
    'context',left(coalesce(failure_context,''),4096),'rolled_back_operations',completed,
    'domain_mutations_committed',0,'live_external_call_performed',false),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,
   'operation_count',p.operation_count,'operations_attempted',attempted,'operations_completed',0,
   'success',false,'failure_code',failure_message,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0,
    'live_external_call_performed',false,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,
   operations_completed=0,finished_at=clock_timestamp(),
   duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;
 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,
  'operation_count',p.operation_count,'operations_attempted',attempted,'operations_completed',completed,
  'success',true,'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',0,
   'live_external_call_performed',false,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,
  operations_completed=completed,finished_at=clock_timestamp(),
  duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end $$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 return public.execute_rollover_plan_phase3b6c_private(p_request,actor);
end $$;

revoke all on function public.phase3b6c_history_manifest(uuid),
 public.raise_rollover_preflight_failure_phase3b6c(text,text,jsonb),
 public.execute_rollover_typed_handler_phase3b6c_private(jsonb,uuid,uuid,uuid,uuid),
 public.execute_rollover_plan_phase3b6c_private(jsonb,uuid),
 public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

comment on table public.rollover_execution_input_snapshots is
 'Immutable execution-scoped Phase 3B.6C frozen input snapshot headers.';
comment on table public.rollover_execution_input_snapshot_components is
 'Immutable fixed-name canonical components used by later rollover handlers.';
comment on function public.execute_rollover_plan_phase3b6c_private(jsonb,uuid) is
 'Phase 3B.6C engine: exactly operations 1-7, executed-unpublished, no football-domain mutations.';

commit;
