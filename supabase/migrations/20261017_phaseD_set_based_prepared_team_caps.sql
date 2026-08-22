begin;

-- Phase D: derive the complete prepared-cap population in one relational pass.
-- The v1 row and set fingerprint material is intentionally unchanged.
create or replace function public.phase3b10b_derive_team_caps_private(
  p_rollover_execution_id uuid,
  p_assignment_set_id uuid,
  p_snapshot_id uuid,
  p_cap_limit numeric,
  p_assignment_set_hash text
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare
  execution_row public.rollover_executions%rowtype;
  assignment_set_row public.rollover_target_roster_assignment_sets%rowtype;
  result_rows jsonb;
  expected_teams jsonb;
  actual_teams jsonb;
begin
  select * into execution_row from public.rollover_executions
   where id=p_rollover_execution_id;
  select * into assignment_set_row from public.rollover_target_roster_assignment_sets
   where id=p_assignment_set_id and rollover_execution_id=p_rollover_execution_id;
  if execution_row.id is null or assignment_set_row.id is null then
    perform public.raise_phase3b6c1_failure('cap_assignment_set_mismatch','{}');
  end if;
  if p_cap_limit is null or p_cap_limit<=0 or p_assignment_set_hash is null
     or p_assignment_set_hash!~'^[0-9a-f]{64}$' then
    perform public.raise_phase3b6c1_failure('cap_source_financial_row_malformed','{}');
  end if;
  if assignment_set_row.aggregate_assignment_set_hash<>p_assignment_set_hash then
    perform public.raise_phase3b6c1_failure('cap_assignment_fingerprint_mismatch','{}');
  end if;
  if not exists(select 1 from public.rollover_execution_input_snapshots snapshot_row
    where snapshot_row.id=p_snapshot_id
      and snapshot_row.rollover_execution_id=execution_row.id) then
    perform public.raise_phase3b6c1_failure('cap_snapshot_mismatch','{}');
  end if;
  if not exists(select 1 from public.league_teams where league_id=execution_row.league_id) then
    perform public.raise_phase3b6c1_failure('cap_canonical_team_set_empty','{}');
  end if;

  -- Reject invalid source identity instead of allowing a left join to hide it.
  if exists(
    select 1 from public.season_roster_assignments roster_row
    left join public.league_teams team_row on team_row.id=roster_row.league_team_id
    left join public.contract_seasons season_row on season_row.id=roster_row.target_contract_season_id
    where roster_row.assignment_set_id=assignment_set_row.id and (
      team_row.id is null or team_row.league_id<>execution_row.league_id
      or season_row.id is null or season_row.league_id<>execution_row.league_id
      or season_row.league_team_id<>roster_row.league_team_id
      or season_row.league_season_id<>assignment_set_row.target_league_season_id
      or season_row.salary is null or season_row.salary<0
    )
  ) then perform public.raise_phase3b6c1_failure('cap_rostered_source_identity_invalid','{}'); end if;
  if exists(
    select 1 from public.rollover_dead_cap_obligations dead_row
    left join public.league_teams team_row on team_row.id=dead_row.league_team_id
    where dead_row.rollover_execution_id=execution_row.id and (
      dead_row.league_id<>execution_row.league_id
      or dead_row.target_season_id<>assignment_set_row.target_league_season_id
      or team_row.id is null or team_row.league_id<>execution_row.league_id
      or dead_row.amount is null or dead_row.amount<=0
    )
  ) then perform public.raise_phase3b6c1_failure('cap_dead_source_identity_invalid','{}'); end if;
  if exists(
    select 1 from public.contract_agreements agreement_row
    join public.contract_seasons season_row
      on season_row.contract_id=agreement_row.id
     and season_row.league_season_id=assignment_set_row.target_league_season_id
    left join public.league_teams team_row on team_row.id=agreement_row.league_team_id
    where agreement_row.status='active'
      and public.phase3b8a_is_preserved_off_roster_liability(
        p_snapshot_id,agreement_row.id,agreement_row.player_id,agreement_row.league_team_id)
      and (agreement_row.league_id<>execution_row.league_id
        or season_row.league_id<>execution_row.league_id
        or season_row.league_team_id<>agreement_row.league_team_id
        or team_row.id is null or team_row.league_id<>execution_row.league_id
        or season_row.cap_hit is null or season_row.cap_hit<0)
  ) then perform public.raise_phase3b6c1_failure('cap_preserved_source_identity_invalid','{}'); end if;

  with canonical_teams as materialized (
    select team_row.id as league_team_id
    from public.league_teams team_row
    where team_row.league_id=execution_row.league_id
  ), active_sources as materialized (
    select roster_row.league_team_id,season_row.salary::numeric as amount
    from public.season_roster_assignments roster_row
    join public.contract_seasons season_row on season_row.id=roster_row.target_contract_season_id
    where roster_row.assignment_set_id=assignment_set_row.id
      and season_row.obligation_status='active'
    union all
    select agreement_row.league_team_id,season_row.cap_hit::numeric as amount
    from public.contract_agreements agreement_row
    join public.contract_seasons season_row
      on season_row.contract_id=agreement_row.id
     and season_row.league_season_id=assignment_set_row.target_league_season_id
    where agreement_row.league_id=execution_row.league_id
      and agreement_row.status='active'
      and public.phase3b8a_is_preserved_off_roster_liability(
        p_snapshot_id,agreement_row.id,agreement_row.player_id,agreement_row.league_team_id)
  ), active_by_team as (
    select source_row.league_team_id,coalesce(sum(source_row.amount),0) active_salary,
           count(*)::integer active_count
    from active_sources source_row group by source_row.league_team_id
  ), dead_by_team as (
    select dead_row.league_team_id,coalesce(sum(dead_row.amount),0) dead_cap,
           count(*)::integer dead_count
    from public.rollover_dead_cap_obligations dead_row
    where dead_row.rollover_execution_id=execution_row.id
    group by dead_row.league_team_id
  ), cap_rows as (
    select canonical_team.league_team_id,
      coalesce(active_team.active_salary,0) active_salary,
      coalesce(dead_team.dead_cap,0) dead_cap,
      coalesce(active_team.active_count,0)::integer active_count,
      coalesce(dead_team.dead_count,0)::integer dead_count
    from canonical_teams canonical_team
    left join active_by_team active_team using(league_team_id)
    left join dead_by_team dead_team using(league_team_id)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'team',cap_row.league_team_id,'cap',p_cap_limit,'active',cap_row.active_salary,
    'dead',cap_row.dead_cap,'contracts',cap_row.active_count,'obligations',cap_row.dead_count,
    'assignment_hash',p_assignment_set_hash,'hash',public.rollover_material_fingerprint(
      jsonb_build_object('team',cap_row.league_team_id,'cap',p_cap_limit,
        'active',cap_row.active_salary,'dead',cap_row.dead_cap,'contracts',cap_row.active_count,
        'obligations',cap_row.dead_count,'assignment_hash',p_assignment_set_hash))
  ) order by cap_row.league_team_id),'[]'::jsonb) into result_rows from cap_rows cap_row;

  select jsonb_agg(team_row.id order by team_row.id) into expected_teams
    from public.league_teams team_row where team_row.league_id=execution_row.league_id;
  select jsonb_agg((case_row.value->>'team')::uuid order by (case_row.value->>'team')::uuid)
    into actual_teams from jsonb_array_elements(result_rows) case_row(value);
  if expected_teams is null or actual_teams is distinct from expected_teams
     or jsonb_array_length(result_rows)<>(select count(distinct case_row.value->>'team') from jsonb_array_elements(result_rows) case_row(value)) then
    perform public.raise_phase3b6c1_failure('cap_canonical_team_set_mismatch','{}');
  end if;
  return result_rows;
end$$;

create or replace function public.write_prepared_caps_phase3b10b_private(
  p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
  execution_row public.rollover_executions%rowtype;
  snapshot_row public.rollover_execution_input_snapshots%rowtype;
  assignment_set_row public.rollover_target_roster_assignment_sets%rowtype;
  rules jsonb; cap_limit numeric; cap_rows jsonb; set_hash text;
  cap_set_row public.prepared_team_cap_sets%rowtype;
  over_count integer; written integer;
begin
  select * into execution_row from public.rollover_executions where id=p_rollover_execution_id;
  if execution_row.id is null then perform public.raise_phase3b6c1_failure('cap_execution_missing','{}'); end if;
  if not exists(select 1 from public.rollover_execution_locks lock_row
    where lock_row.rollover_execution_id=execution_row.id and lock_row.execution_plan_id=p_execution_plan_id
      and lock_row.approval_id=p_approval_id and lock_row.status='active' and lock_row.lock_type='cutover' for update)
  then perform public.raise_phase3b6c1_failure('cap_cutover_lock_missing','{}'); end if;
  select * into snapshot_row from public.rollover_execution_input_snapshots
    where rollover_execution_id=execution_row.id and execution_plan_id=p_execution_plan_id
      and approval_id=p_approval_id for share;
  if snapshot_row.id is null then perform public.raise_phase3b6c1_failure('cap_snapshot_mismatch','{}'); end if;
  select canonical_payload into rules from public.rollover_execution_input_snapshot_components
    where snapshot_id=snapshot_row.id and component_name='league_rules';
  cap_limit:=coalesce((rules->>'salary_cap')::numeric,(rules->>'current_salary_cap')::numeric);
  if cap_limit is null or cap_limit<=0 then perform public.raise_phase3b6c1_failure('cap_limit_missing','{}'); end if;
  select * into assignment_set_row from public.rollover_target_roster_assignment_sets
    where rollover_execution_id=execution_row.id for share;
  if assignment_set_row.id is null then perform public.raise_phase3b6c1_failure('cap_assignment_set_mismatch','{}'); end if;

  perform 1 from public.league_teams where league_id=execution_row.league_id order by id for share;
  perform 1 from public.season_roster_assignments where assignment_set_id=assignment_set_row.id order by league_team_id,sleeper_player_id for share;
  perform 1 from public.rollover_dead_cap_obligations where rollover_execution_id=execution_row.id order by league_team_id,contract_agreement_id for share;
  cap_rows:=public.phase3b10b_derive_team_caps_private(execution_row.id,assignment_set_row.id,
    snapshot_row.id,cap_limit,assignment_set_row.aggregate_assignment_set_hash);
  set_hash:=public.rollover_material_fingerprint((select jsonb_agg(
    jsonb_build_object('team',(cap_row.value->>'team')::uuid,'hash',cap_row.value->>'hash')
    order by (cap_row.value->>'team')::uuid) from jsonb_array_elements(cap_rows) cap_row(value)));
  over_count:=(select count(*) from jsonb_array_elements(cap_rows) cap_row(value)
    where (cap_row.value->>'active')::numeric+(cap_row.value->>'dead')::numeric>cap_limit);

  insert into public.prepared_team_cap_sets(rollover_execution_id,league_id,target_season_id,
    frozen_snapshot_hash,target_assignment_set_hash,contract_evidence_hash,dead_cap_evidence_hash,
    cap_limit,cap_limit_fingerprint,canonical_team_count,aggregate_cap_set_hash,status,schema_version)
  values(execution_row.id,execution_row.league_id,assignment_set_row.target_league_season_id,
    snapshot_row.aggregate_snapshot_fingerprint,assignment_set_row.aggregate_assignment_set_hash,
    public.rollover_material_fingerprint(jsonb_build_object('assignment',assignment_set_row.aggregate_assignment_set_hash)),
    public.rollover_material_fingerprint(coalesce((select jsonb_agg(dead_row.deterministic_fingerprint order by dead_row.contract_agreement_id)
      from public.rollover_dead_cap_obligations dead_row where dead_row.rollover_execution_id=execution_row.id),'[]'::jsonb)),
    cap_limit,public.rollover_material_fingerprint(rules),jsonb_array_length(cap_rows),set_hash,
    'prepared_unpublished','phase3b10b-cap-v1') returning * into cap_set_row;

  insert into public.prepared_team_caps(cap_set_id,league_id,target_season_id,league_team_id,
    salary_cap_limit,active_target_salary,prepared_dead_cap,total_cap_charge,cap_space,is_over_cap,
    over_cap_amount,publication_blocked,blocking_reason_codes,active_contract_count,
    dead_cap_obligation_count,contract_evidence_hash,dead_cap_evidence_hash,deterministic_row_fingerprint)
  select cap_set_row.id,execution_row.league_id,assignment_set_row.target_league_season_id,
    (cap_row.value->>'team')::uuid,cap_limit,(cap_row.value->>'active')::numeric,
    (cap_row.value->>'dead')::numeric,(cap_row.value->>'active')::numeric+(cap_row.value->>'dead')::numeric,
    cap_limit-(cap_row.value->>'active')::numeric-(cap_row.value->>'dead')::numeric,
    (cap_row.value->>'active')::numeric+(cap_row.value->>'dead')::numeric>cap_limit,
    greatest((cap_row.value->>'active')::numeric+(cap_row.value->>'dead')::numeric-cap_limit,0),
    (cap_row.value->>'active')::numeric+(cap_row.value->>'dead')::numeric>cap_limit,
    case when (cap_row.value->>'active')::numeric+(cap_row.value->>'dead')::numeric>cap_limit
      then array['hard_cap_exceeded'] else '{}'::text[] end,
    (cap_row.value->>'contracts')::integer,(cap_row.value->>'obligations')::integer,
    cap_set_row.contract_evidence_hash,cap_set_row.dead_cap_evidence_hash,cap_row.value->>'hash'
  from jsonb_array_elements(cap_rows) cap_row(value)
  order by (cap_row.value->>'team')::uuid;
  get diagnostics written=row_count;
  if written<>jsonb_array_length(cap_rows) then perform public.raise_phase3b6c1_failure('cap_canonical_team_set_mismatch','{}'); end if;
  return jsonb_build_object('team_count',written,'over_cap_team_count',over_count,
    'publication_blocked_team_count',over_count,'cap_limit',cap_limit,
    'aggregate_cap_set_hash',set_hash,'mutation_count',written+1,'postcondition_count',8);
end$$;

revoke all on function public.phase3b10b_derive_team_caps_private(uuid,uuid,uuid,numeric,text)
  from public,anon,authenticated,service_role;
revoke all on function public.write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)
  from public,anon,authenticated,service_role;

commit;
