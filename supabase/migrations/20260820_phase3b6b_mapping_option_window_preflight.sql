begin;

-- Phase 3B.6B adds exactly two read-only typed preflight handlers.
insert into public.rollover_execution_handler_registry
 (operation_code,operation_order,handler_version,input_schema_version,result_schema_version,
  execution_owner,mutation_class,metadata)
values
 ('VERIFY_TEAM_ROSTER_MAPPINGS',4,1,'phase3b6b-mapping-input-v1','phase3b6b-mapping-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6B','domain','roster_mapping')),
 ('VERIFY_OPTION_WINDOW_CLOSED',5,1,'phase3b6b-option-window-input-v1','phase3b6b-option-window-result-v1',
  'execution','read_only',jsonb_build_object('phase','3B.6B','domain','owner_options'));

create or replace function public.raise_rollover_preflight_failure_phase3b6b(
 p_failure_code text,p_operation_code text,p_details jsonb default '{}'::jsonb
) returns void
language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 raise exception using
  errcode='P0001',
  message=p_failure_code,
  detail=jsonb_build_object(
   'operation_code',p_operation_code,
   'failure_code',p_failure_code,
   'details',coalesce(p_details,'{}'::jsonb)
  )::text;
end $$;

create or replace function public.execute_rollover_typed_handler_phase3b6b_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;
 p public.rollover_execution_plans%rowtype;
 target_season public.league_seasons%rowtype;
 registry_row public.rollover_execution_handler_registry%rowtype;
 code text:=p_operation->>'operation_type';
 expected_count integer;
 expected_option_count integer;
 actual_team_count integer;
 active_membership_count integer;
 primary_owner_count integer;
 co_owner_count integer;
 target_roster_count integer;
 resolved_mapping_count integer;
 missing_team_ids jsonb;
 duplicate_team_mappings jsonb;
 duplicate_roster_mappings jsonb;
 cross_league_evidence jsonb;
 unknown_team_references jsonb;
 mapping_evidence jsonb;
 mapping_fingerprint text;
 notice_expected timestamptz;
 deadline_expected timestamptz;
 derived_deadline timestamptz;
 verified_at timestamptz:=public.rollover_effective_now();
 total_options integer;
 exercise_count integer;
 decline_count integer;
 nonresponse_count integer;
 commissioner_count integer;
 unresolved_count integer;
 invalid_taxi_count integer;
 duplicate_decision_count integer;
 decision_evidence jsonb;
 decision_fingerprint text;
 evidence_count integer;
 result_material jsonb;
begin
 if code in(
  'VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE'
 ) then
  return public.execute_rollover_typed_handler_phase3b6a_private(
   p_operation,p_rollover_execution_id,p_execution_plan_id
  );
 end if;
 if jsonb_typeof(p_operation) is distinct from 'object' then
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'typed_handler_operation_invalid',coalesce(code,''),'{}'
  );
 end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into p from public.rollover_execution_plans
  where id=p_execution_plan_id and rollover_execution_id=p_rollover_execution_id;
 if x.id is null or p.id is null then
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'typed_handler_execution_identity_invalid',coalesce(code,''),'{}'
  );
 end if;
 select * into registry_row from public.rollover_execution_handler_registry
  where operation_code=code and enabled;
 if registry_row.operation_code is null then
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'unsupported_operation',coalesce(code,''),'{}'
  );
 end if;
 if (p_operation->>'operation_index')::integer<>registry_row.operation_order then
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'typed_handler_operation_order_mismatch',code,
   jsonb_build_object('expected',registry_row.operation_order,'actual',p_operation->>'operation_index')
  );
 end if;
 if not p_operation?'handler_version'
    or (p_operation->>'handler_version')::integer<>registry_row.handler_version then
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'typed_handler_version_mismatch',code,
   jsonb_build_object('expected',registry_row.handler_version)
  );
 end if;
 if not p_operation?'input_schema_version'
    or p_operation->>'input_schema_version'<>registry_row.input_schema_version then
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'typed_handler_input_schema_mismatch',code,
   jsonb_build_object('expected',registry_row.input_schema_version)
  );
 end if;

 if code='VERIFY_TEAM_ROSTER_MAPPINGS' then
  begin
   expected_count:=(p_operation->>'expected_team_count')::integer;
  exception when others then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'canonical_team_count_mismatch',code,jsonb_build_object('reason','invalid_expected_team_count')
   );
  end;
  if expected_count<=0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'canonical_team_count_mismatch',code,jsonb_build_object('reason','invalid_expected_team_count')
   );
  end if;
  select * into target_season from public.league_seasons
   where league_id=x.league_id and season=p.target_season for share;
  if target_season.id is null then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'target_roster_mapping_missing',code,jsonb_build_object('reason','target_season_missing')
   );
  end if;
  perform 1 from public.league_teams where league_id=x.league_id for share;
  perform 1 from public.league_memberships where league_id=x.league_id for share;
  perform 1 from public.season_team_mappings where league_season_id=target_season.id for share;
  perform 1 from public.contract_agreements where league_id=x.league_id for share;
  perform 1 from public.season_roster_assignments r
   join public.league_seasons s on s.id=r.league_season_id
   where s.league_id=x.league_id and s.season in(p.source_season,p.target_season) for share of r;

  select count(*) into actual_team_count from public.league_teams where league_id=x.league_id;
  if actual_team_count<>expected_count then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'canonical_team_count_mismatch',code,
    jsonb_build_object('expected',expected_count,'actual',actual_team_count)
   );
  end if;
  select count(*) into active_membership_count from public.league_memberships
   where league_id=x.league_id and league_team_id is not null;
  select count(*) into primary_owner_count from public.league_teams t
   join public.league_memberships m on m.league_id=x.league_id
    and m.league_team_id=t.id and m.user_id=t.user_id
   where t.league_id=x.league_id;
  select count(*) into co_owner_count from public.league_memberships m
   join public.league_teams t on t.id=m.league_team_id and t.league_id=x.league_id
   where m.league_id=x.league_id and m.user_id is distinct from t.user_id;

  select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into cross_league_evidence from(
   select m.id membership_id,m.league_team_id,t.league_id team_league_id
   from public.league_memberships m left join public.league_teams t on t.id=m.league_team_id
   where m.league_id=x.league_id and m.league_team_id is not null
     and (m.league_team_id is null or t.id is null or t.league_id<>x.league_id)
   order by m.id
  ) q;
  if jsonb_array_length(cross_league_evidence)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    case when exists(
     select 1 from jsonb_array_elements(cross_league_evidence) e
     where e->>'team_league_id' is not null
    ) then 'membership_team_cross_league' else 'membership_team_missing' end,
    code,jsonb_build_object('evidence',cross_league_evidence)
   );
  end if;
  select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into duplicate_team_mappings from(
   select t.user_id,count(*) canonical_team_count
   from public.league_teams t where t.league_id=x.league_id and t.user_id is not null
   group by t.user_id having count(*)>1 order by t.user_id
  ) q;
  if jsonb_array_length(duplicate_team_mappings)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'primary_owner_conflict',code,jsonb_build_object('evidence',duplicate_team_mappings)
   );
  end if;
  select coalesce(jsonb_agg(team_id order by team_id),'[]'::jsonb) into missing_team_ids from(
   select t.id team_id from public.league_teams t
   left join public.league_memberships m
    on m.league_id=x.league_id and m.league_team_id=t.id and m.user_id=t.user_id
   where t.league_id=x.league_id and (t.user_id is null or m.id is null)
  ) q;
  if jsonb_array_length(missing_team_ids)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'membership_team_missing',code,jsonb_build_object('missing_team_ids',missing_team_ids)
   );
  end if;

  select count(*),count(distinct sleeper_roster_id) into resolved_mapping_count,target_roster_count
  from public.season_team_mappings where league_season_id=target_season.id;
  select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into duplicate_team_mappings from(
   select league_team_id,count(*) mapping_count from public.season_team_mappings
   where league_season_id=target_season.id group by league_team_id having count(*)>1
  ) q;
  if jsonb_array_length(duplicate_team_mappings)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'canonical_team_mapping_duplicate',code,jsonb_build_object('evidence',duplicate_team_mappings)
   );
  end if;
  select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into duplicate_roster_mappings from(
   select sleeper_roster_id,count(*) mapping_count from public.season_team_mappings
   where league_season_id=target_season.id group by sleeper_roster_id having count(*)>1
  ) q;
  if jsonb_array_length(duplicate_roster_mappings)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'sleeper_roster_mapping_duplicate',code,jsonb_build_object('evidence',duplicate_roster_mappings)
   );
  end if;
  select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into cross_league_evidence from(
   select m.id mapping_id,m.league_team_id,t.league_id team_league_id
   from public.season_team_mappings m left join public.league_teams t on t.id=m.league_team_id
   where m.league_season_id=target_season.id
    and (t.id is null or t.league_id<>x.league_id)
  ) q;
  if jsonb_array_length(cross_league_evidence)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'target_roster_mapping_cross_league',code,jsonb_build_object('evidence',cross_league_evidence)
   );
  end if;
  if resolved_mapping_count<expected_count then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'target_roster_mapping_missing',code,
    jsonb_build_object('expected',expected_count,'actual',resolved_mapping_count)
   );
  end if;
  if resolved_mapping_count<>expected_count or target_roster_count<>expected_count then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'mapping_count_mismatch',code,
    jsonb_build_object('expected',expected_count,'mappings',resolved_mapping_count,'rosters',target_roster_count)
   );
  end if;

  select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) into unknown_team_references from(
   select 'contract_agreements' source,a.id reference_id,a.league_team_id
   from public.contract_agreements a left join public.league_teams t on t.id=a.league_team_id
   where a.league_id=x.league_id and (t.id is null or t.league_id<>x.league_id)
   union all
   select 'season_roster_assignments',r.id,r.league_team_id
   from public.season_roster_assignments r
   join public.league_seasons s on s.id=r.league_season_id
   left join public.league_teams t on t.id=r.league_team_id
   where s.league_id=x.league_id and s.season in(p.source_season,p.target_season)
    and (t.id is null or t.league_id<>x.league_id)
  ) q;
  if exists(select 1 from jsonb_array_elements(unknown_team_references) e
            where e->>'source'='contract_agreements') then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'unknown_contract_owner_team',code,jsonb_build_object('evidence',unknown_team_references)
   );
  end if;
  if jsonb_array_length(unknown_team_references)>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'unknown_roster_team_reference',code,jsonb_build_object('evidence',unknown_team_references)
   );
  end if;

  select jsonb_build_object(
   'teams',coalesce((select jsonb_agg(jsonb_build_object(
    'id',t.id,'sleeper_roster_id',t.sleeper_roster_id) order by t.id)
    from public.league_teams t where t.league_id=x.league_id),'[]'::jsonb),
   'memberships',coalesce((select jsonb_agg(jsonb_build_object(
    'id',m.id,'user_id',m.user_id,'role',lower(m.role),'league_team_id',m.league_team_id) order by m.id)
    from public.league_memberships m where m.league_id=x.league_id
     and m.league_team_id is not null),'[]'::jsonb),
   'target_mappings',coalesce((select jsonb_agg(jsonb_build_object(
    'id',m.id,'league_team_id',m.league_team_id,'sleeper_roster_id',m.sleeper_roster_id,
    'mapping_source',m.mapping_source,'mapping_confidence',m.mapping_confidence) order by m.id)
    from public.season_team_mappings m where m.league_season_id=target_season.id),'[]'::jsonb)
  ) into mapping_evidence;
  mapping_fingerprint:=public.rollover_material_fingerprint(jsonb_build_object(
   'league_id',x.league_id,'target_season',p.target_season,'expected_team_count',expected_count,
   'evidence',mapping_evidence
  ));
  if p_operation->>'evidence_fingerprint' is distinct from mapping_fingerprint then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'mapping_changed_after_approval',code,
    jsonb_build_object('expected_fingerprint',p_operation->>'evidence_fingerprint',
     'actual_fingerprint',mapping_fingerprint)
   );
  end if;
  evidence_count:=actual_team_count+active_membership_count+resolved_mapping_count;
  result_material:=jsonb_build_object(
   'operation_code',code,'league_id',x.league_id,'expected_canonical_team_count',expected_count,
   'actual_canonical_team_count',actual_team_count,'active_membership_count',active_membership_count,
   'resolved_primary_owner_count',primary_owner_count,'co_owner_count',co_owner_count,
   'target_sleeper_roster_count',target_roster_count,'resolved_target_mapping_count',resolved_mapping_count,
   'missing_canonical_team_identifiers','[]'::jsonb,
   'duplicate_canonical_team_mappings','[]'::jsonb,
   'duplicate_sleeper_roster_mappings','[]'::jsonb,
   'cross_league_evidence','[]'::jsonb,'unknown_team_references','[]'::jsonb,
   'mapping_fingerprint',mapping_fingerprint,'validation_outcome','passed',
   'validation_codes','[]'::jsonb,'evidence_count',evidence_count,
   'live_external_call_performed',false,'domain_mutation_count',0
  );
 elsif code='VERIFY_OPTION_WINDOW_CLOSED' then
  begin
   expected_option_count:=(p_operation->>'expected_eligible_option_count')::integer;
   notice_expected:=(p_operation->>'expected_notice_timestamp')::timestamptz;
   deadline_expected:=(p_operation->>'expected_deadline_timestamp')::timestamptz;
  exception when others then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_deadline_invalid',code,jsonb_build_object('reason','invalid_typed_input')
   );
  end;
  perform 1 from public.rollover_owner_decisions where rollover_execution_id=x.id for share;
  perform 1 from public.rollover_owner_decision_revisions where rollover_execution_id=x.id for share;
  perform 1 from public.rollover_commissioner_reviews where rollover_execution_id=x.id for share;
  perform 1 from public.contract_agreements where league_id=x.league_id for share;
  if x.notice_timestamp is null then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'rollover_notice_missing',code,'{}'
   );
  end if;
  if x.notice_timestamp is distinct from notice_expected then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'rollover_notice_changed',code,
    jsonb_build_object('expected',notice_expected,'actual',x.notice_timestamp)
   );
  end if;
  derived_deadline:=x.notice_timestamp+interval '168 hours';
  if x.owner_deadline is null or x.owner_deadline is distinct from derived_deadline
     or x.owner_deadline is distinct from deadline_expected then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_deadline_invalid',code,
    jsonb_build_object('derived',derived_deadline,'actual',x.owner_deadline)
   );
  end if;
  if verified_at<x.owner_deadline then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_window_not_closed',code,
    jsonb_build_object('deadline',x.owner_deadline,'verified_at',verified_at)
   );
  end if;
  select count(*) into total_options from public.rollover_owner_decisions
   where rollover_execution_id=x.id;
  if total_options<expected_option_count then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_decision_missing',code,
    jsonb_build_object('expected',expected_option_count,'actual',total_options)
   );
  elsif total_options>expected_option_count then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_decision_duplicate',code,
    jsonb_build_object('expected',expected_option_count,'actual',total_options)
   );
  end if;
  if exists(select 1 from public.rollover_owner_decisions d
   left join public.league_teams t on t.id=d.league_team_id
   where d.rollover_execution_id=x.id and(
    d.league_id<>x.league_id or t.id is null or t.league_id<>x.league_id
   )) then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_decision_cross_league',code,'{}'
   );
  end if;
  if exists(select 1 from public.rollover_owner_decisions d
   where d.rollover_execution_id=x.id and(
    d.source_season<>p.source_season or d.target_season<>p.target_season
    or d.deadline is distinct from x.owner_deadline
   )) then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_season_mismatch',code,'{}'
   );
  end if;
  if exists(select 1 from public.rollover_owner_decisions d
   left join public.contract_agreements a on a.id=d.agreement_id
   where d.rollover_execution_id=x.id and(
    a.id is null or a.league_id<>x.league_id or a.league_team_id<>d.league_team_id
    or a.player_id<>d.player_id
   )) then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_contract_mismatch',code,'{}'
   );
  end if;
  select count(*) into duplicate_decision_count from(
   select player_id from public.rollover_owner_decisions where rollover_execution_id=x.id
   group by player_id having count(*)>1
  ) q;
  if duplicate_decision_count>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_decision_conflict',code,
    jsonb_build_object('conflicting_player_count',duplicate_decision_count)
   );
  end if;
  select
   count(*) filter(where owner_choice='recontract' and decision_status in('planned_retention','execution_ready')),
   count(*) filter(where owner_choice='decline' and decision_status in('planned_release','execution_ready')),
   count(*) filter(where decision_status='no_response'
    and planned_outcome='release_at_rollover_to_commissioner_hold'),
   count(*) filter(where decision_status='commissioner_review_requested'),
   count(*) filter(where owner_choice='recontract'
    and lower(coalesce(initial_roster_slot,initial_roster_status,''))='taxi')
  into exercise_count,decline_count,nonresponse_count,commissioner_count,invalid_taxi_count
  from public.rollover_owner_decisions where rollover_execution_id=x.id;
  if invalid_taxi_count>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'invalid_taxi_option_exercise',code,
    jsonb_build_object('count',invalid_taxi_count)
   );
  end if;
  select count(*) into unresolved_count from public.rollover_owner_decisions d
   where d.rollover_execution_id=x.id and not(
    (d.owner_choice='recontract' and d.decision_status in('planned_retention','execution_ready')
      and d.recontract_agreement_id is not null and d.recontract_event_id is not null)
    or (d.owner_choice='decline' and d.decision_status in('planned_release','execution_ready'))
    or (d.decision_status='no_response'
      and d.planned_outcome='release_at_rollover_to_commissioner_hold')
    or (d.decision_status='commissioner_review_requested' and exists(
      select 1 from public.rollover_commissioner_reviews r
      where r.rollover_execution_id=x.id and r.player_id=d.player_id
       and r.league_id=x.league_id and r.source_season=p.source_season
       and r.target_season=p.target_season and r.action_validated
       and r.review_status in('action_validated','execution_ready')
    ))
   );
  if unresolved_count>0 then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_decision_unresolved',code,jsonb_build_object('count',unresolved_count)
   );
  end if;
  select coalesce(jsonb_agg(jsonb_build_object(
   'id',d.id,'league_team_id',d.league_team_id,'player_id',d.player_id,
   'agreement_id',d.agreement_id,'decision_status',d.decision_status,
   'owner_choice',d.owner_choice,'planned_outcome',d.planned_outcome,
   'deadline',d.deadline,'locked_at',d.locked_at,'updated_at',d.updated_at,
   'revision_count',(select count(*) from public.rollover_owner_decision_revisions r
    where r.owner_decision_id=d.id)
  ) order by d.id),'[]'::jsonb) into decision_evidence
  from public.rollover_owner_decisions d where d.rollover_execution_id=x.id;
  decision_fingerprint:=public.rollover_material_fingerprint(jsonb_build_object(
   'execution_id',x.id,'league_id',x.league_id,'source_season',p.source_season,
   'target_season',p.target_season,'notice_timestamp',x.notice_timestamp,
   'owner_deadline',x.owner_deadline,'decisions',decision_evidence
  ));
  if p_operation->>'evidence_fingerprint' is distinct from decision_fingerprint then
   perform public.raise_rollover_preflight_failure_phase3b6b(
    'option_decision_changed_after_approval',code,
    jsonb_build_object('expected_fingerprint',p_operation->>'evidence_fingerprint',
     'actual_fingerprint',decision_fingerprint)
   );
  end if;
  evidence_count:=total_options+coalesce((select count(*) from public.rollover_owner_decision_revisions
   where rollover_execution_id=x.id),0)+coalesce((select count(*) from public.rollover_commissioner_reviews
   where rollover_execution_id=x.id),0);
  result_material:=jsonb_build_object(
   'operation_code',code,'league_id',x.league_id,'closing_season',p.source_season,
   'target_season',p.target_season,'official_notice_identifier',x.id,
   'official_notice_timestamp',x.notice_timestamp,'derived_deadline_timestamp',derived_deadline,
   'current_verification_timestamp',verified_at,'total_eligible_option_count',total_options,
   'explicit_exercise_count',exercise_count,'explicit_decline_count',decline_count,
   'default_nonresponse_count',nonresponse_count,
   'commissioner_review_required_count',commissioner_count,'unresolved_count',unresolved_count,
   'invalid_taxi_exercise_count',invalid_taxi_count,
   'duplicate_conflicting_decision_count',duplicate_decision_count,
   'decision_set_fingerprint',decision_fingerprint,'validation_outcome','passed',
   'validation_codes','[]'::jsonb,'evidence_count',evidence_count,'domain_mutation_count',0
  );
 else
  perform public.raise_rollover_preflight_failure_phase3b6b(
   'unsupported_operation',coalesce(code,''),'{}'
  );
 end if;

 return jsonb_build_object(
  'operation_code',code,'handler_version',registry_row.handler_version,
  'input_schema_version',registry_row.input_schema_version,
  'result_schema_version',registry_row.result_schema_version,
  'read_only',true,'domain_mutations',0,
  'authority_fingerprint',public.rollover_material_fingerprint(result_material),
  'result',result_material
 );
end $$;

-- Additive engine version. Phase 3B.5I and 3B.6A private functions remain intact.
create or replace function public.execute_rollover_plan_phase3b6b_private(p_request jsonb,p_actor uuid)
returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;
 a public.rollover_execution_plan_approvals%rowtype;
 p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;
 prior public.rollover_execution_runs%rowtype;
 runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;
 op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');
 material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;failed_op jsonb;
 failure_sqlstate text;failure_message text;failure_detail text;failure_hint text;failure_context text;
 result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;
 if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null
    or nullif(p_request->>'approval_id','') is null
    or nullif(p_request->>'execution_plan_id','') is null
    or nullif(p_request->>'expected_plan_fingerprint','') is null
    or nullif(p_request->>'expected_execution_status','') is null
    or nullif(p_request->>'expected_approval_status','') is null then
  raise exception 'complete execution assertions required';
 end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6b','execution_id',x.id,
  'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);
 select * into prior from public.rollover_execution_runs
  where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then
  if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;
  return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);
 end if;
 select * into prior from public.rollover_execution_runs
  where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then
  return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);
 end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals
  where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans
  where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::integer
    or p.plan_version<>a.execution_plan_version or p.plan_status<>'approved_for_execution'
    or not p.approved_for_execution or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint'
    or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then
  raise exception 'stale or invalid approved execution plan';
 end if;
 select * into l from public.rollover_execution_locks
  where rollover_execution_id=x.id and approval_id=a.id and execution_plan_id=p.id
   and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint
   and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;
 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,
  execution_plan_version,plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,
  started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',
  p.operation_count,run_started,p_actor,jsonb_build_object('engine_version','phase3b6b-v1',
   'typed_handlers',true,'publication_performed',false))
 returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted
      or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then
    raise exception 'ordered operation sequence mismatch';
   end if;
   if op->>'operation_type' in(
    'VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY',
    'VERIFY_TARGET_SLEEPER_LINKAGE','VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED'
   ) then
    handler_result:=public.execute_rollover_typed_handler_phase3b6b_private(op,x.id,p.id);
   elsif op->>'operation_type' in('verify_execution_boundary','phase3b5i_noop') then
    handler_result:=jsonb_build_object('synthetic_noop',true,'operation_index',attempted,'domain_mutations',0);
   elsif op->>'operation_type'='phase3b5i_fail' then
    raise exception 'synthetic blocking operation failure at index %',attempted;
   elsif op->>'operation_type'='phase3b5i_fail_once' then
    if not exists(select 1 from public.rollover_execution_runs r
     where r.rollover_execution_id=x.id and r.run_status='execution_failed' and r.id<>runrow.id) then
     raise exception 'synthetic recoverable operation failure at index %',attempted;
    end if;
    handler_result:=jsonb_build_object('synthetic_noop',true,'recovered_after_prior_failure',true,'domain_mutations',0);
   else
    raise exception 'unsupported Phase 3B.6B operation type: %',op->>'operation_type';
   end if;
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
    'detail',coalesce(failure_detail,''),'hint',coalesce(failure_hint,''),
    'context',coalesce(failure_context,''),'rolled_back_operations',completed,
    'domain_mutations_committed',0,'live_external_call_performed',false),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,
   'operation_count',p.operation_count,'operations_attempted',attempted,'operations_completed',0,
   'success',false,'failure_code',failure_message,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,
    'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,
   operations_completed=0,finished_at=clock_timestamp(),
   duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',
   failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;
 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,
  'operation_count',p.operation_count,'operations_attempted',attempted,'operations_completed',completed,
  'success',true,'diagnostics',jsonb_build_object('typed_handlers',true,
   'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,
  operations_completed=completed,finished_at=clock_timestamp(),
  duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end $$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;
 perform public.require_commissioner_authority(x.league_id);
 return public.execute_rollover_plan_phase3b6b_private(p_request,actor);
end $$;

revoke all on function public.raise_rollover_preflight_failure_phase3b6b(text,text,jsonb),
 public.execute_rollover_typed_handler_phase3b6b_private(jsonb,uuid,uuid),
 public.execute_rollover_plan_phase3b6b_private(jsonb,uuid),
 public.execute_rollover_plan_authenticated(jsonb)
 from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

comment on function public.execute_rollover_typed_handler_phase3b6b_private(jsonb,uuid,uuid) is
 'Private Phase 3B.6B dispatcher for five typed read-only preflight operations.';
comment on function public.execute_rollover_plan_phase3b6b_private(jsonb,uuid) is
 'Phase 3B.6B engine preserving certified approval, replay, locking, rollback, audit, security, and fail-closed dispatch.';
comment on function public.execute_rollover_plan_authenticated(jsonb) is
 'Authenticated rollover execution wrapper; Phase 3B.6B supports exactly five real read-only handlers and no publication.';

commit;
