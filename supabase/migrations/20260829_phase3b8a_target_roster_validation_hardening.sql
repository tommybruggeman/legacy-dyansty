begin;

create or replace function public.validate_target_roster_assignment_set_phase3b8a_private(
 p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid)
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;target_id uuid;source_id uuid;s public.rollover_execution_input_snapshots%rowtype;
begin
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 if x.id is null then perform public.raise_phase3b6c1_failure('target_roster_player_missing','{}');end if;
 if not exists(select 1 from public.rollover_execution_locks where rollover_execution_id=x.id
   and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id and league_id=x.league_id
   and lock_type='cutover' and lock_scope='rollover_global' and status='active' for update) then
  perform public.raise_phase3b6c1_failure('target_roster_cutover_lock_missing','{}');end if;
 select id into source_id from public.league_seasons where league_id=x.league_id and season=x.source_season;
 select id into target_id from public.league_seasons where league_id=x.league_id and season=x.target_season;
 if source_id is null or target_id is null then perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=x.id
  and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id for share;
 if s.id is null or s.source_plan_fingerprint<>(select plan_fingerprint from public.rollover_execution_plans where id=p_execution_plan_id)
    or s.mapping_fingerprint is distinct from (select op->>'evidence_fingerprint'
      from public.rollover_execution_plans p cross join lateral jsonb_array_elements(p.ordered_operations) op
      where p.id=p_execution_plan_id and op->>'operation_type'='VERIFY_TEAM_ROSTER_MAPPINGS') then
  perform public.raise_phase3b6c1_failure('target_roster_hash_mismatch','{}');end if;

 -- Minimal global lock order: contract state, release/hold evidence, teams,
 -- source assignments, set header, then existing target rows.
 perform 1 from public.contract_agreements a where a.league_id=x.league_id
  order by a.player_id,a.id for update;
 perform 1 from public.contract_seasons cs where cs.league_id=x.league_id and cs.league_season_id=target_id
  order by cs.player_id,cs.contract_id,cs.id for update;
 perform 1 from public.rollover_contract_releases where rollover_execution_id=x.id order by player_id,id for share;
 perform 1 from public.rollover_commissioner_holds where rollover_execution_id=x.id order by player_id,id for share;
 perform 1 from public.league_teams where league_id=x.league_id order by id for share;
 perform 1 from public.season_roster_assignments where league_season_id=source_id order by sleeper_player_id,league_team_id,id for share;
 perform 1 from public.rollover_target_roster_assignment_sets where league_id=x.league_id and target_league_season_id=target_id for update;
 perform 1 from public.season_roster_assignments where league_season_id=target_id order by sleeper_player_id,league_team_id,id for share;

 if exists(select 1 from public.contract_agreements a
   join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active' and not exists(
    select 1 from public.contract_seasons cs where cs.contract_id=a.id and cs.league_season_id=target_id and cs.obligation_status='active')) then
  perform public.raise_phase3b6c1_failure('target_contract_obligation_missing','{}');end if;
 if exists(select 1 from public.contract_agreements a left join public.player_universe p on p.sleeper_id=a.player_id
   where a.league_id=x.league_id and a.status='active' and p.sleeper_id is null) then
  perform public.raise_phase3b6c1_failure('target_roster_player_missing','{}');end if;
 if exists(select 1 from public.contract_agreements a left join public.league_teams t on t.id=a.league_team_id
   where a.league_id=x.league_id and a.status='active' and (t.id is null or t.league_id<>x.league_id)) then
  perform public.raise_phase3b6c1_failure('target_roster_team_cross_league','{}');end if;
 if exists(select 1 from public.contract_agreements a where a.league_id=x.league_id and a.status='active'
   and not exists(select 1 from public.league_memberships m where m.league_id=x.league_id and m.league_team_id=a.league_team_id)) then
  perform public.raise_phase3b6c1_failure('target_roster_unknown_owner','{}');end if;
 if exists(select 1 from public.contract_agreements a where a.league_id=x.league_id and a.status='active'
   and not exists(select 1 from public.season_team_mappings m where m.league_season_id=target_id and m.league_team_id=a.league_team_id)) then
  perform public.raise_phase3b6c1_failure('target_roster_team_missing','{}');end if;
 if (select count(*) from public.season_team_mappings m join public.league_teams t on t.id=m.league_team_id
    where m.league_season_id=target_id and t.league_id=x.league_id)<>s.frozen_team_count then
  perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
 if exists(select 1 from public.contract_agreements a
   left join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active' and (src.id is null or src.league_team_id<>a.league_team_id)) then
  perform public.raise_phase3b6c1_failure('target_roster_owner_mismatch','{}');end if;
 if exists(select player_id from public.contract_agreements where league_id=x.league_id and status='active'
   group by player_id having count(*)>1) then perform public.raise_phase3b6c1_failure('target_roster_duplicate_player','{}');end if;
 if exists(select 1 from public.contract_agreements a join public.rollover_contract_releases r
   on r.rollover_execution_id=x.id and r.player_id=a.player_id where a.league_id=x.league_id and a.status='active') then
  perform public.raise_phase3b6c1_failure('target_roster_release_conflict','{}');end if;
 if exists(select 1 from public.contract_agreements a join public.rollover_commissioner_holds h
   on h.rollover_execution_id=x.id and h.player_id=a.player_id and h.hold_status='active'
   where a.league_id=x.league_id and a.status='active') then
  perform public.raise_phase3b6c1_failure('target_roster_hold_conflict','{}');end if;
 if exists(select sleeper_player_id from public.season_roster_assignments where league_season_id=target_id
   group by sleeper_player_id having count(*)>1) then perform public.raise_phase3b6c1_failure('target_roster_duplicate_assignment','{}');end if;
 if exists(select 1 from public.season_roster_assignments r join public.league_teams t on t.id=r.league_team_id
   where r.league_season_id=target_id and t.league_id<>x.league_id) then
  perform public.raise_phase3b6c1_failure('target_roster_cross_league','{}');end if;
end$$;

create or replace function public.execute_rollover_typed_handler_phase3b8a_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare code text:=p_operation->>'operation_type';result jsonb;
begin
 if code<>'RECONCILE_TARGET_ROSTER_ASSIGNMENTS' then
  return public.execute_rollover_typed_handler_phase3b7c_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 end if;
 if (p_operation->>'operation_index')::integer<>15 or not exists(select 1 from public.rollover_execution_handler_registry
   where operation_code=code and operation_order=15 and handler_version=1) then
  perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}');end if;
 perform public.validate_target_roster_assignment_set_phase3b8a_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id);
 result:=public.write_target_roster_assignment_set_phase3b8a_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 return jsonb_build_object('operation_code',code,'handler_version',1,'result',result||jsonb_build_object(
  'operation_mutation_count',coalesce((result->>'mutation_count')::integer,0),
  'deterministic_result_hash',result->>'aggregate_assignment_set_hash'));
end$$;

revoke all on function public.validate_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid),
 public.execute_rollover_typed_handler_phase3b8a_private(jsonb,uuid,uuid,uuid,uuid)
 from public,anon,authenticated,service_role;

commit;
