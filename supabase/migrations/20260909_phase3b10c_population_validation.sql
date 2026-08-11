begin;

create or replace function public.validate_prepared_free_agent_population_phase3b10c_private(
  p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid
) returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; target public.league_seasons%rowtype;
  snap public.rollover_execution_input_snapshots%rowtype;
  aset public.rollover_target_roster_assignment_sets%rowtype;
begin
  select * into x from public.rollover_executions where id=p_rollover_execution_id;
  select * into target from public.league_seasons where league_id=x.league_id and season=x.target_season;
  if target.id is null then perform public.raise_phase3b6c1_failure('free_agent_target_season_missing','{}'); end if;
  select * into snap from public.rollover_execution_input_snapshots where rollover_execution_id=x.id
    and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id;
  select * into aset from public.rollover_target_roster_assignment_sets where rollover_execution_id=x.id;
  if aset.id is null then perform public.raise_phase3b6c1_failure('free_agent_roster_set_missing','{}'); end if;
  if aset.expected_row_count<>(select count(*) from public.season_roster_assignments where assignment_set_id=aset.id)
  then perform public.raise_phase3b6c1_failure('free_agent_roster_set_incomplete','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases r
    left join public.contract_agreements a on a.id=r.contract_agreement_id
    where r.rollover_execution_id=x.id and (
      a.id is null or a.league_id<>x.league_id or a.player_id<>r.player_id
      or r.target_season_id<>target.id or r.resulting_agreement_status<>'released' or a.status<>'released'))
  then perform public.raise_phase3b6c1_failure('free_agent_release_population_incomplete','{}'); end if;
  if exists(select 1 from public.rollover_commissioner_holds h
    left join public.rollover_contract_releases r on r.id=h.release_id
    where h.rollover_execution_id=x.id and h.hold_status='active' and (
      r.id is null or r.rollover_execution_id<>x.id or r.player_id<>h.player_id
      or r.release_disposition<>'release_to_commissioner_hold' or h.target_season_id<>target.id))
  then perform public.raise_phase3b6c1_failure('free_agent_hold_population_incomplete','{}'); end if;
  if exists(select 1 from public.contract_seasons cs
    where cs.league_season_id=target.id and cs.obligation_status='active'
    group by cs.player_id having count(*)<>1)
  then perform public.raise_phase3b6c1_failure('free_agent_contract_population_incomplete','{}'); end if;
  if exists(select 1 from public.contract_seasons cs
    left join public.contract_agreements a on a.id=cs.contract_id
    left join public.league_teams t on t.id=cs.league_team_id
    where cs.league_season_id=target.id and cs.obligation_status='active'
      and (a.id is null or a.league_id<>x.league_id or a.player_id<>cs.player_id
        or cs.league_id<>x.league_id or t.league_id<>x.league_id))
  then perform public.raise_phase3b6c1_failure('free_agent_cross_league_evidence','{}'); end if;
  if snap.id is null or aset.source_snapshot_id<>snap.id
    or aset.source_snapshot_hash<>snap.aggregate_snapshot_fingerprint
  then perform public.raise_phase3b6c1_failure('free_agent_source_hash_mismatch','{}'); end if;
end
$$;

create or replace function public.execute_rollover_typed_handler_phase3b10c_private(
  p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare code text:=p_operation->>'operation_type'; result jsonb;
begin
  if code<>'RECONCILE_FREE_AGENT_ELIGIBILITY' then
    return public.execute_rollover_typed_handler_phase3b10b_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
  end if;
  if (p_operation->>'operation_index')::int<>24 then perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}'); end if;
  perform public.validate_prepared_free_agent_population_phase3b10c_private(
    p_rollover_execution_id,p_execution_plan_id,p_approval_id);
  result:=public.write_prepared_free_agents_phase3b10c_private(
    p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
  return jsonb_build_object('operation_code',code,'handler_version',1,
    'result',result||jsonb_build_object('operation_mutation_count',(result->>'mutation_count')::int));
end
$$;

revoke all on function
  public.validate_prepared_free_agent_population_phase3b10c_private(uuid,uuid,uuid),
  public.execute_rollover_typed_handler_phase3b10c_private(jsonb,uuid,uuid,uuid,uuid)
from public,anon,authenticated,service_role;

commit;
