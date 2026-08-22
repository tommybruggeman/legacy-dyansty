begin;

-- Phase 3B.6B production corrective migration:
-- League teams do not require Legacy user memberships in order to
-- participate in rollover. Existing canonical memberships remain
-- integrity-checked, while season_team_mappings remain the authoritative
-- rollover team/roster mapping.
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

commit;
