begin;

alter table public.rollover_execution_handler_registry
  drop constraint rollover_execution_handler_registry_mutation_class_check;
alter table public.rollover_execution_handler_registry
  add constraint rollover_execution_handler_registry_mutation_class_check check (
    mutation_class in (
      'read_only','contract_domain','roster_domain','taxi_domain',
      'taxi_eligibility_domain','ir_domain','draft_inventory_domain',
      'rookie_authority_domain','rookie_eligibility_domain','dead_cap_domain',
      'team_cap_domain','free_agent_domain'
    )
  );

insert into public.rollover_execution_handler_registry(
  operation_code,operation_order,handler_version,input_schema_version,
  result_schema_version,execution_owner,mutation_class,metadata
) values (
  'RECONCILE_FREE_AGENT_ELIGIBILITY',24,1,
  'phase3b10c-free-agent-input-v1','phase3b10c-free-agent-result-v1',
  'execution','free_agent_domain',
  jsonb_build_object('phase','3B.10C','publication',false,'external_calls',false)
);

do $$
declare d text; sig regprocedure;
begin
  foreach sig in array array[
    'public.guard_contract_write_during_rollover()'::regprocedure,
    'public.guard_season_roster_assignment_insert_phase3b8a()'::regprocedure,
    'public.guard_phase3b9a_draft_inventory()'::regprocedure,
    'public.guard_phase3b9b_rookie_authority()'::regprocedure,
    'public.guard_phase3b9c_rookie_eligibility()'::regprocedure
  ] loop
    select pg_get_functiondef(sig) into d;
    d := replace(d,'''phase3b10b-v1'')','''phase3b10b-v1'',''phase3b10c-v1'')');
    execute d;
  end loop;
end
$$;

create table public.prepared_free_agent_eligibility_sets (
  id uuid primary key default gen_random_uuid(),
  rollover_execution_id uuid not null unique references public.rollover_executions(id),
  league_id uuid not null references public.leagues(id),
  target_season_id uuid not null references public.league_seasons(id),
  source_snapshot_hash text not null check(source_snapshot_hash ~ '^[0-9a-f]{64}$'),
  release_evidence_hash text not null check(release_evidence_hash ~ '^[0-9a-f]{64}$'),
  commissioner_hold_evidence_hash text not null check(commissioner_hold_evidence_hash ~ '^[0-9a-f]{64}$'),
  target_roster_set_hash text not null check(target_roster_set_hash ~ '^[0-9a-f]{64}$'),
  contract_evidence_hash text not null check(contract_evidence_hash ~ '^[0-9a-f]{64}$'),
  prepared_cap_set_hash text not null check(prepared_cap_set_hash ~ '^[0-9a-f]{64}$'),
  expected_player_count integer not null check(expected_player_count >= 0),
  aggregate_set_hash text not null check(aggregate_set_hash ~ '^[0-9a-f]{64}$'),
  status text not null check(status = 'prepared'),
  published_at timestamptz,
  schema_version text not null check(schema_version = 'phase3b10c-free-agent-v1'),
  created_at timestamptz not null default clock_timestamp(),
  unique(league_id,target_season_id),
  check(published_at is null)
);

create table public.prepared_free_agent_eligibilities (
  id uuid primary key default gen_random_uuid(),
  eligibility_set_id uuid not null references public.prepared_free_agent_eligibility_sets(id),
  league_id uuid not null references public.leagues(id),
  target_season_id uuid not null references public.league_seasons(id),
  player_id text not null references public.player_universe(sleeper_id),
  eligibility_status text not null check(eligibility_status in(
    'prepared_free_agent','rostered_ineligible','held_ineligible',
    'active_contract_ineligible','released_publication_blocked','conflicting_evidence'
  )),
  eligibility_reason_code text not null,
  release_id uuid references public.rollover_contract_releases(id),
  commissioner_hold_id uuid references public.rollover_commissioner_holds(id),
  target_roster_assignment_id uuid references public.season_roster_assignments(id),
  active_target_contract_id uuid references public.contract_agreements(id),
  cap_publication_blocked boolean not null default false,
  cap_blocking_team_id uuid references public.league_teams(id),
  source_snapshot_hash text not null check(source_snapshot_hash ~ '^[0-9a-f]{64}$'),
  release_evidence_hash text not null check(release_evidence_hash ~ '^[0-9a-f]{64}$'),
  hold_evidence_hash text not null check(hold_evidence_hash ~ '^[0-9a-f]{64}$'),
  roster_evidence_hash text not null check(roster_evidence_hash ~ '^[0-9a-f]{64}$'),
  contract_evidence_hash text not null check(contract_evidence_hash ~ '^[0-9a-f]{64}$'),
  cap_evidence_hash text not null check(cap_evidence_hash ~ '^[0-9a-f]{64}$'),
  deterministic_row_fingerprint text not null check(deterministic_row_fingerprint ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  unique(eligibility_set_id,player_id),
  check((cap_publication_blocked and cap_blocking_team_id is not null) or not cap_publication_blocked),
  check(eligibility_status <> 'prepared_free_agent' or (
    release_id is not null and commissioner_hold_id is null
    and target_roster_assignment_id is null and active_target_contract_id is null
  )),
  check(eligibility_status <> 'held_ineligible' or commissioner_hold_id is not null),
  check(eligibility_status <> 'rostered_ineligible' or target_roster_assignment_id is not null),
  check(eligibility_status <> 'active_contract_ineligible' or active_target_contract_id is not null)
);

create index prepared_free_agent_eligibilities_status_idx
  on public.prepared_free_agent_eligibilities(league_id,target_season_id,eligibility_status,player_id);

alter table public.prepared_free_agent_eligibility_sets enable row level security;
alter table public.prepared_free_agent_eligibilities enable row level security;
revoke all on public.prepared_free_agent_eligibility_sets,
  public.prepared_free_agent_eligibilities from public,anon,authenticated;
grant select,insert on public.prepared_free_agent_eligibility_sets,
  public.prepared_free_agent_eligibilities to service_role;
create policy prepared_free_agent_set_commissioner_read
  on public.prepared_free_agent_eligibility_sets for select to authenticated
  using(exists(select 1 from public.league_memberships m
    where m.league_id=prepared_free_agent_eligibility_sets.league_id
      and m.user_id=auth.uid() and m.role='commissioner'));
create policy prepared_free_agent_row_commissioner_read
  on public.prepared_free_agent_eligibilities for select to authenticated
  using(exists(select 1 from public.league_memberships m
    where m.league_id=prepared_free_agent_eligibilities.league_id
      and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.guard_phase3b10c_prepared_free_agents()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);
begin
  if tg_op in ('UPDATE','DELETE') then raise exception 'prepared_free_agent_evidence_immutable'; end if;
  if public.has_active_rollover_cutover_lock(lid)
     and coalesce(current_setting('app.rollover_typed_execution',true),'') <> 'phase3b10c-v1'
  then raise exception 'rollover_cutover_free_agent_writes_blocked'; end if;
  return new;
end
$$;
create trigger prepared_free_agent_sets_guard before insert or update or delete
  on public.prepared_free_agent_eligibility_sets for each row
  execute function public.guard_phase3b10c_prepared_free_agents();
create trigger prepared_free_agent_rows_guard before insert or update or delete
  on public.prepared_free_agent_eligibilities for each row
  execute function public.guard_phase3b10c_prepared_free_agents();

create or replace function public.guard_phase3b10c_legacy_free_agent_visibility()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);
begin
  if public.has_active_rollover_cutover_lock(lid)
     and coalesce(current_setting('app.rollover_typed_execution',true),'') <> 'phase3b10c-v1'
  then raise exception 'rollover_cutover_free_agent_visibility_blocked'; end if;
  return case when tg_op='DELETE' then old else new end;
end
$$;
create trigger free_agent_publications_cutover_guard before insert or update or delete
  on public.free_agent_publications for each row
  execute function public.guard_phase3b10c_legacy_free_agent_visibility();

create or replace function public.write_prepared_free_agents_phase3b10c_private(
  p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
  x public.rollover_executions%rowtype;
  target public.league_seasons%rowtype;
  snap public.rollover_execution_input_snapshots%rowtype;
  aset public.rollover_target_roster_assignment_sets%rowtype;
  capset public.prepared_team_cap_sets%rowtype;
  existing public.prepared_free_agent_eligibility_sets%rowtype;
  r record; state text; reason text; row_hash text;
  release_hash text; hold_hash text; contract_hash text; set_hash text;
  material jsonb:='[]'::jsonb; expected_count int:=0; written int:=0;
  prepared_count int:=0; rostered_count int:=0; held_count int:=0;
  active_count int:=0; blocked_count int:=0; conflict_count int:=0;
begin
  if p_actor is null then perform public.raise_phase3b6c1_failure('free_agent_authenticated_actor_required','{}'); end if;
  select * into x from public.rollover_executions where id=p_rollover_execution_id;
  select * into target from public.league_seasons where league_id=x.league_id and season=x.target_season for share;
  if target.id is null then perform public.raise_phase3b6c1_failure('free_agent_target_season_missing','{}'); end if;
  if target.league_id<>x.league_id then perform public.raise_phase3b6c1_failure('free_agent_target_season_cross_league','{}'); end if;
  if not exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id
    and l.execution_plan_id=p_execution_plan_id and l.approval_id=p_approval_id and l.status='active'
    and l.lock_type='cutover' and l.lock_scope='rollover_global' for update)
  then perform public.raise_phase3b6c1_failure('free_agent_cutover_lock_missing','{}'); end if;
  select * into snap from public.rollover_execution_input_snapshots where rollover_execution_id=x.id
    and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id for share;
  select * into aset from public.rollover_target_roster_assignment_sets where rollover_execution_id=x.id for share;
  if aset.id is null then perform public.raise_phase3b6c1_failure('free_agent_roster_set_missing','{}'); end if;
  if aset.league_id<>x.league_id or aset.target_league_season_id<>target.id or aset.status<>'complete_unpublished'
    or aset.source_snapshot_id<>snap.id or aset.source_snapshot_hash<>snap.aggregate_snapshot_fingerprint
  then perform public.raise_phase3b6c1_failure('free_agent_source_hash_mismatch','{}'); end if;
  if aset.expected_row_count<>(select count(*) from public.season_roster_assignments where assignment_set_id=aset.id)
  then perform public.raise_phase3b6c1_failure('free_agent_roster_set_incomplete','{}'); end if;
  select * into capset from public.prepared_team_cap_sets where rollover_execution_id=x.id for share;
  if capset.id is null or capset.league_id<>x.league_id or capset.target_season_id<>target.id
    or capset.frozen_snapshot_hash<>snap.aggregate_snapshot_fingerprint or capset.status<>'prepared_unpublished'
  then perform public.raise_phase3b6c1_failure('free_agent_cap_set_mismatch','{}'); end if;

  perform 1 from public.rollover_contract_releases where rollover_execution_id=x.id order by player_id,id for share;
  perform 1 from public.rollover_commissioner_holds where rollover_execution_id=x.id order by player_id,id for share;
  perform 1 from public.season_roster_assignments where assignment_set_id=aset.id order by sleeper_player_id,id for share;
  perform 1 from public.contract_seasons where league_season_id=target.id and obligation_status='active' order by player_id,contract_id,id for share;
  perform 1 from public.rollover_taxi_unlock_dispositions where rollover_execution_id=x.id order by player_id,id for share;
  perform 1 from public.rollover_taxi_eligibility_authorities where rollover_execution_id=x.id order by player_id,id for share;
  perform 1 from public.rollover_ir_reconciliations where rollover_execution_id=x.id order by player_id,id for share;
  perform 1 from public.prepared_team_cap_sets where id=capset.id for share;
  perform 1 from public.prepared_free_agent_eligibility_sets where rollover_execution_id=x.id for update;

  if exists(select 1 from public.rollover_contract_releases q where q.rollover_execution_id=x.id
    and (q.league_id<>x.league_id or q.target_season_id<>target.id))
    or exists(select 1 from public.rollover_commissioner_holds q where q.rollover_execution_id=x.id
      and (q.league_id<>x.league_id or q.target_season_id<>target.id))
    or exists(select 1 from public.season_roster_assignments q join public.league_teams t on t.id=q.league_team_id
      where q.assignment_set_id=aset.id and t.league_id<>x.league_id)
    or exists(select 1 from public.contract_seasons q where q.league_season_id=target.id and q.obligation_status='active'
      and (q.league_id<>x.league_id or not exists(select 1 from public.league_teams t where t.id=q.league_team_id and t.league_id=x.league_id)))
  then perform public.raise_phase3b6c1_failure('free_agent_cross_league_evidence','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases rel
    join public.contract_seasons cs on cs.contract_id=rel.contract_agreement_id and cs.league_season_id=target.id and cs.obligation_status='active'
    where rel.rollover_execution_id=x.id)
  then perform public.raise_phase3b6c1_failure('free_agent_release_contract_conflict','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases rel join public.season_roster_assignments a
    on a.assignment_set_id=aset.id and a.sleeper_player_id=rel.player_id where rel.rollover_execution_id=x.id)
  then perform public.raise_phase3b6c1_failure('free_agent_release_roster_conflict','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases rel join public.rollover_commissioner_holds h
    on h.rollover_execution_id=x.id and h.player_id=rel.player_id and h.hold_status='active'
    where rel.rollover_execution_id=x.id and rel.release_disposition<>'release_to_commissioner_hold')
  then perform public.raise_phase3b6c1_failure('free_agent_release_hold_conflict','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases rel join public.rollover_taxi_unlock_dispositions d
    on d.rollover_execution_id=x.id and d.player_id=rel.player_id
    where rel.rollover_execution_id=x.id and d.disposition='unlocked_to_active_pool')
  then perform public.raise_phase3b6c1_failure('free_agent_taxi_conflict','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases rel join public.rollover_ir_reconciliations i
    on i.rollover_execution_id=x.id and i.player_id=rel.player_id
    where rel.rollover_execution_id=x.id and i.resulting_ir_status='administrative_carry_forward_unvalidated')
  then perform public.raise_phase3b6c1_failure('free_agent_ir_conflict','{}'); end if;
  if exists(select 1 from public.rollover_contract_releases rel where rel.rollover_execution_id=x.id
    and rel.release_disposition='release_to_commissioner_hold'
    and not exists(select 1 from public.rollover_commissioner_holds h where h.rollover_execution_id=x.id
      and h.release_id=rel.id and h.hold_status='active'))
  then perform public.raise_phase3b6c1_failure('free_agent_hold_population_incomplete','{}'); end if;

  release_hash:=public.rollover_material_fingerprint(coalesce((select jsonb_agg(release_fingerprint order by player_id,id)
    from public.rollover_contract_releases where rollover_execution_id=x.id),'[]'::jsonb));
  hold_hash:=public.rollover_material_fingerprint(coalesce((select jsonb_agg(creation_fingerprint order by player_id,id)
    from public.rollover_commissioner_holds where rollover_execution_id=x.id and hold_status='active'),'[]'::jsonb));
  contract_hash:=public.rollover_material_fingerprint(coalesce((select jsonb_agg(to_jsonb(cs)-'created_at'-'updated_at' order by cs.player_id,cs.contract_id,cs.id)
    from public.contract_seasons cs where cs.league_season_id=target.id and cs.obligation_status='active'),'[]'::jsonb));

  for r in
    with players as (
      select player_id from public.rollover_contract_releases where rollover_execution_id=x.id
      union select player_id from public.rollover_commissioner_holds where rollover_execution_id=x.id and hold_status='active'
      union select sleeper_player_id from public.season_roster_assignments where assignment_set_id=aset.id
      union select player_id from public.contract_seasons where league_season_id=target.id and obligation_status='active'
      union select player_id from public.rollover_taxi_unlock_dispositions where rollover_execution_id=x.id
      union select player_id from public.rollover_ir_reconciliations where rollover_execution_id=x.id
    )
    select p.player_id,rel.id release_id,rel.release_disposition,h.id hold_id,a.id assignment_id,
      ca.id contract_id,coalesce(pc.publication_blocked,false) cap_blocked,pc.league_team_id cap_team_id,
      exists(select 1 from public.free_agent_publications f where f.league_id=x.league_id
        and f.player_id=p.player_id and f.season=x.target_season and f.publication_status not in('unpublished','ineligible')) legacy_block
    from players p
    left join public.rollover_contract_releases rel on rel.rollover_execution_id=x.id and rel.player_id=p.player_id
    left join public.rollover_commissioner_holds h on h.rollover_execution_id=x.id and h.player_id=p.player_id and h.hold_status='active'
    left join public.season_roster_assignments a on a.assignment_set_id=aset.id and a.sleeper_player_id=p.player_id
    left join public.contract_seasons cs on cs.league_season_id=target.id and cs.player_id=p.player_id and cs.obligation_status='active'
    left join public.contract_agreements ca on ca.id=cs.contract_id
    left join public.prepared_team_caps pc on pc.cap_set_id=capset.id and pc.league_team_id=coalesce(a.league_team_id,ca.league_team_id,rel.source_league_team_id)
    order by p.player_id
  loop
    expected_count:=expected_count+1;
    if r.hold_id is not null then state:='held_ineligible';reason:='active_commissioner_hold';held_count:=held_count+1;
    elsif r.release_id is not null and r.legacy_block then state:='released_publication_blocked';reason:='legacy_visibility_conflict';blocked_count:=blocked_count+1;
    elsif r.release_id is not null then state:='prepared_free_agent';reason:='certified_ordinary_release';prepared_count:=prepared_count+1;
    elsif r.assignment_id is not null then state:='rostered_ineligible';reason:='canonical_target_roster_assignment';rostered_count:=rostered_count+1;
    elsif r.contract_id is not null then state:='active_contract_ineligible';reason:='active_target_contract';active_count:=active_count+1;
    else state:='conflicting_evidence';reason:='unresolved_taxi_or_ir_evidence';conflict_count:=conflict_count+1; end if;
    row_hash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b10c-free-agent-row-v1','execution',x.id,
      'player',r.player_id,'status',state,'reason',reason,'release',r.release_id,'hold',r.hold_id,
      'assignment',r.assignment_id,'contract',r.contract_id,'cap_blocked',r.cap_blocked,'cap_team',r.cap_team_id,
      'snapshot',snap.aggregate_snapshot_fingerprint,'release_hash',release_hash,'hold_hash',hold_hash,
      'roster_hash',aset.aggregate_assignment_set_hash,'contract_hash',contract_hash,'cap_hash',capset.aggregate_cap_set_hash));
    material:=material||jsonb_build_array(jsonb_build_object('player_id',r.player_id,'row_hash',row_hash));
  end loop;
  set_hash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b10c-free-agent-set-v1','execution',x.id,'rows',material));
  select * into existing from public.prepared_free_agent_eligibility_sets where league_id=x.league_id and target_season_id=target.id for update;
  if found then
    if existing.rollover_execution_id<>x.id or existing.aggregate_set_hash<>set_hash or existing.expected_player_count<>expected_count
    then perform public.raise_phase3b6c1_failure('free_agent_set_conflict','{}'); end if;
    if (select count(*) from public.prepared_free_agent_eligibilities where eligibility_set_id=existing.id)<>expected_count
    then perform public.raise_phase3b6c1_failure('free_agent_set_hash_mismatch','{}'); end if;
    return jsonb_build_object('evaluated_player_count',expected_count,'prepared_free_agent_count',prepared_count,
      'rostered_ineligible_count',rostered_count,'held_ineligible_count',held_count,
      'active_contract_ineligible_count',active_count,'released_publication_blocked_count',blocked_count,
      'conflicting_evidence_count',conflict_count,'eligibility_rows_written',0,'compatible_replay_count',1,
      'mutation_count',0,'postcondition_count',12,'aggregate_eligibility_set_hash',set_hash,
      'validation_codes',jsonb_build_array('snapshot','release','hold','roster','contracts','taxi','ir','caps','cutover_lock','unpublished'));
  end if;
  perform set_config('app.rollover_typed_execution','phase3b10c-v1',true);
  insert into public.prepared_free_agent_eligibility_sets(rollover_execution_id,league_id,target_season_id,
    source_snapshot_hash,release_evidence_hash,commissioner_hold_evidence_hash,target_roster_set_hash,
    contract_evidence_hash,prepared_cap_set_hash,expected_player_count,aggregate_set_hash,status,schema_version)
  values(x.id,x.league_id,target.id,snap.aggregate_snapshot_fingerprint,release_hash,hold_hash,
    aset.aggregate_assignment_set_hash,contract_hash,capset.aggregate_cap_set_hash,expected_count,set_hash,'prepared','phase3b10c-free-agent-v1')
  returning * into existing;
  for r in
    with players as (
      select player_id from public.rollover_contract_releases where rollover_execution_id=x.id
      union select player_id from public.rollover_commissioner_holds where rollover_execution_id=x.id and hold_status='active'
      union select sleeper_player_id from public.season_roster_assignments where assignment_set_id=aset.id
      union select player_id from public.contract_seasons where league_season_id=target.id and obligation_status='active'
      union select player_id from public.rollover_taxi_unlock_dispositions where rollover_execution_id=x.id
      union select player_id from public.rollover_ir_reconciliations where rollover_execution_id=x.id
    )
    select p.player_id,rel.id release_id,h.id hold_id,a.id assignment_id,ca.id contract_id,
      coalesce(pc.publication_blocked,false) cap_blocked,pc.league_team_id cap_team_id,
      exists(select 1 from public.free_agent_publications f where f.league_id=x.league_id
        and f.player_id=p.player_id and f.season=x.target_season and f.publication_status not in('unpublished','ineligible')) legacy_block
    from players p
    left join public.rollover_contract_releases rel on rel.rollover_execution_id=x.id and rel.player_id=p.player_id
    left join public.rollover_commissioner_holds h on h.rollover_execution_id=x.id and h.player_id=p.player_id and h.hold_status='active'
    left join public.season_roster_assignments a on a.assignment_set_id=aset.id and a.sleeper_player_id=p.player_id
    left join public.contract_seasons cs on cs.league_season_id=target.id and cs.player_id=p.player_id and cs.obligation_status='active'
    left join public.contract_agreements ca on ca.id=cs.contract_id
    left join public.prepared_team_caps pc on pc.cap_set_id=capset.id and pc.league_team_id=coalesce(a.league_team_id,ca.league_team_id,rel.source_league_team_id)
    order by p.player_id
  loop
    if r.hold_id is not null then state:='held_ineligible';reason:='active_commissioner_hold';
    elsif r.release_id is not null and r.legacy_block then state:='released_publication_blocked';reason:='legacy_visibility_conflict';
    elsif r.release_id is not null then state:='prepared_free_agent';reason:='certified_ordinary_release';
    elsif r.assignment_id is not null then state:='rostered_ineligible';reason:='canonical_target_roster_assignment';
    elsif r.contract_id is not null then state:='active_contract_ineligible';reason:='active_target_contract';
    else state:='conflicting_evidence';reason:='unresolved_taxi_or_ir_evidence'; end if;
    row_hash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b10c-free-agent-row-v1','execution',x.id,
      'player',r.player_id,'status',state,'reason',reason,'release',r.release_id,'hold',r.hold_id,
      'assignment',r.assignment_id,'contract',r.contract_id,'cap_blocked',r.cap_blocked,'cap_team',r.cap_team_id,
      'snapshot',snap.aggregate_snapshot_fingerprint,'release_hash',release_hash,'hold_hash',hold_hash,
      'roster_hash',aset.aggregate_assignment_set_hash,'contract_hash',contract_hash,'cap_hash',capset.aggregate_cap_set_hash));
    insert into public.prepared_free_agent_eligibilities(eligibility_set_id,league_id,target_season_id,player_id,
      eligibility_status,eligibility_reason_code,release_id,commissioner_hold_id,target_roster_assignment_id,
      active_target_contract_id,cap_publication_blocked,cap_blocking_team_id,source_snapshot_hash,
      release_evidence_hash,hold_evidence_hash,roster_evidence_hash,contract_evidence_hash,cap_evidence_hash,
      deterministic_row_fingerprint)
    values(existing.id,x.league_id,target.id,r.player_id,state,reason,r.release_id,r.hold_id,r.assignment_id,
      r.contract_id,r.cap_blocked,case when r.cap_blocked then r.cap_team_id end,snap.aggregate_snapshot_fingerprint,
      release_hash,hold_hash,aset.aggregate_assignment_set_hash,contract_hash,capset.aggregate_cap_set_hash,row_hash);
    written:=written+1;
  end loop;
  if written<>expected_count then perform public.raise_phase3b6c1_failure('free_agent_population_incomplete','{}'); end if;
  return jsonb_build_object('evaluated_player_count',expected_count,'prepared_free_agent_count',prepared_count,
    'rostered_ineligible_count',rostered_count,'held_ineligible_count',held_count,
    'active_contract_ineligible_count',active_count,'released_publication_blocked_count',blocked_count,
    'conflicting_evidence_count',conflict_count,'eligibility_rows_written',written,'compatible_replay_count',0,
    'mutation_count',written+1,'postcondition_count',12,'aggregate_eligibility_set_hash',set_hash,
    'validation_codes',jsonb_build_array('snapshot','release','hold','roster','contracts','taxi','ir','caps','cutover_lock','unpublished'));
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
  result:=public.write_prepared_free_agents_phase3b10c_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
  return jsonb_build_object('operation_code',code,'handler_version',1,
    'result',result||jsonb_build_object('operation_mutation_count',(result->>'mutation_count')::int));
end
$$;

do $$
declare d text;
begin
  select pg_get_functiondef('public.execute_rollover_plan_phase3b10b_private(jsonb,uuid)'::regprocedure) into d;
  d:=replace(d,'execute_rollover_plan_phase3b10b_private','execute_rollover_plan_phase3b10c_private');
  d:=replace(d,'execute_rollover_typed_handler_phase3b10b_private','execute_rollover_typed_handler_phase3b10c_private');
  d:=replace(d,'''RECALCULATE_TARGET_TEAM_CAPS'')','''RECALCULATE_TARGET_TEAM_CAPS'',''RECONCILE_FREE_AGENT_ELIGIBILITY'')');
  d:=replace(d,'Phase 3B.10B','Phase 3B.10C');
  d:=replace(d,'phase3b10b-v1','phase3b10c-v1');
  execute d;
end
$$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
  if p_request?'actor_user_id' or p_request?'executed_by' or p_request?'player_ids'
    or p_request?'eligibility_status' or p_request?'owner_team_id'
  then raise exception 'actor or eligibility material spoofing forbidden'; end if;
  select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
  if x.id is null then raise exception 'execution not found'; end if;
  perform public.require_commissioner_authority(x.league_id);
  return public.execute_rollover_plan_phase3b10c_private(p_request,actor);
end
$$;

revoke all on function
  public.write_prepared_free_agents_phase3b10c_private(uuid,uuid,uuid,uuid),
  public.execute_rollover_typed_handler_phase3b10c_private(jsonb,uuid,uuid,uuid,uuid),
  public.execute_rollover_plan_phase3b10c_private(jsonb,uuid),
  public.guard_phase3b10c_prepared_free_agents(),
  public.guard_phase3b10c_legacy_free_agent_visibility()
from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_authenticated(jsonb)
from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

commit;
