begin;

-- A preserved liability is a contract/cap disposition, not roster authority.
-- The only authority for this exclusion is the immutable operation-6 snapshot.
create or replace function public.phase3b8a_is_preserved_off_roster_liability(
 p_snapshot_id uuid,p_agreement_id uuid,p_player_id text,p_league_team_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,public as $$
 select count(*)=1
 from public.rollover_execution_input_snapshot_components c
 cross join lateral jsonb_array_elements(c.canonical_payload->'commissioner_reviews') q(review)
 where c.snapshot_id=p_snapshot_id
  and ((c.component_name='owner_option_reviews' and c.component_schema_version='phase3b6c-owner_option_reviews-v2')
    or (c.component_name='owner_options' and c.component_schema_version='phase3b6c-owner_options-v1'))
  and review->>'agreement_id'=p_agreement_id::text
  and review->>'player_id'=p_player_id
  and review->>'league_team_id'=p_league_team_id::text
  and review->>'review_type'='active_off_roster_liability'
  and review->>'review_state'='approved'
  and review->>'outcome'='preserve_active_liability'
  and coalesce((review->>'evidence_complete')::boolean,false)
  and coalesce((review->>'action_validated')::boolean,false)
  and review->>'evidence_fingerprint'~'^[0-9a-f]{64}$'
  and review->>'review_fingerprint'~'^[0-9a-f]{64}$'
$$;

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
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=x.id
  and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id for share;
 if source_id is null or target_id is null or s.id is null
   or s.source_plan_fingerprint<>(select plan_fingerprint from public.rollover_execution_plans where id=p_execution_plan_id)
   or s.mapping_fingerprint is distinct from (select op->>'evidence_fingerprint' from public.rollover_execution_plans p
    cross join lateral jsonb_array_elements(p.ordered_operations) op where p.id=p_execution_plan_id
    and op->>'operation_type'='VERIFY_TEAM_ROSTER_MAPPINGS') then
  perform public.raise_phase3b6c1_failure('target_roster_hash_mismatch','{}');end if;
 perform 1 from public.contract_agreements where league_id=x.league_id order by player_id,id for update;
 perform 1 from public.contract_seasons where league_id=x.league_id and league_season_id=target_id order by player_id,contract_id,id for update;
 perform 1 from public.rollover_contract_releases where rollover_execution_id=x.id order by player_id,id for share;
 perform 1 from public.rollover_commissioner_holds where rollover_execution_id=x.id order by player_id,id for share;
 perform 1 from public.season_roster_assignments where league_season_id=source_id order by sleeper_player_id,league_team_id,id for share;
 if exists(select 1 from public.contract_agreements a
   join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active' and not exists(select 1 from public.contract_seasons cs
    where cs.contract_id=a.id and cs.league_season_id=target_id and cs.obligation_status='active')) then
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
 -- Generic ownership validation remains strict. Only exact immutable preserved-liability evidence exempts a missing source row.
 if exists(select 1 from public.contract_agreements a
   left join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active'
   and (src.id is null or src.league_team_id<>a.league_team_id)
   and not (src.id is null and public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id))) then
  perform public.raise_phase3b6c1_failure('target_roster_owner_mismatch','{}');end if;
 if exists(select 1 from public.contract_agreements a join public.season_roster_assignments src
   on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active'
   and public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id)) then
  perform public.raise_phase3b6c1_failure('preserved_off_roster_source_assignment_conflict','{}');end if;
 if exists(select player_id from public.contract_agreements where league_id=x.league_id and status='active' group by player_id having count(*)>1) then
  perform public.raise_phase3b6c1_failure('target_roster_duplicate_player','{}');end if;
 if exists(select 1 from public.contract_agreements a join public.rollover_contract_releases r on r.rollover_execution_id=x.id and r.player_id=a.player_id
   where a.league_id=x.league_id and a.status='active') then perform public.raise_phase3b6c1_failure('target_roster_release_conflict','{}');end if;
 if exists(select 1 from public.contract_agreements a join public.rollover_commissioner_holds h on h.rollover_execution_id=x.id
   and h.player_id=a.player_id and h.hold_status='active' where a.league_id=x.league_id and a.status='active') then
  perform public.raise_phase3b6c1_failure('target_roster_hold_conflict','{}');end if;
 if exists(select sleeper_player_id from public.season_roster_assignments where league_season_id=target_id group by sleeper_player_id having count(*)>1) then
  perform public.raise_phase3b6c1_failure('target_roster_duplicate_assignment','{}');end if;
end$$;

create or replace function public.write_target_roster_assignment_set_phase3b8a_private(
 p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;s public.rollover_execution_input_snapshots%rowtype;
 target_season public.league_seasons%rowtype;source_id uuid;existing public.rollover_target_roster_assignment_sets%rowtype;
 set_id uuid:=gen_random_uuid();r record;row_fp text;aggregate_fp text;rows_material jsonb:='[]';exclusions jsonb:='[]';
 candidates int;continuing int;assigned int;ordinary int;held int;intentional int;written int:=0;mapping_count int;source_count int;
 validation_codes jsonb:=jsonb_build_array('snapshot_identity','mapping_fingerprint','canonical_membership_team','target_contract_obligation',
  'source_assignment','preserved_off_roster_liability','release_exclusion','hold_exclusion','population_complete','cutover_lock');
begin
 if p_actor is null then perform public.raise_phase3b6c1_failure('authenticated_actor_required','{}');end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=x.id and approval_id=p_approval_id and execution_plan_id=p_execution_plan_id for share;
 select * into target_season from public.league_seasons where league_id=x.league_id and season=x.target_season for share;
 select id into source_id from public.league_seasons where league_id=x.league_id and season=x.source_season;
 if x.id is null or s.id is null or target_season.id is null then perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
 if not exists(select 1 from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=p_approval_id
   and execution_plan_id=p_execution_plan_id and league_id=x.league_id and status='active' and lock_type='cutover' and lock_scope='rollover_global' for update) then
  perform public.raise_phase3b6c1_failure('target_roster_cutover_lock_missing','{}');end if;
 if s.source_plan_fingerprint<>(select plan_fingerprint from public.rollover_execution_plans where id=p_execution_plan_id) then
  perform public.raise_phase3b6c1_failure('target_roster_hash_mismatch','{}');end if;
 select count(*) into mapping_count from public.season_team_mappings m join public.league_teams t on t.id=m.league_team_id
  where m.league_season_id=target_season.id and t.league_id=x.league_id;
 if mapping_count<>s.frozen_team_count then perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
 select count(distinct sleeper_player_id),count(*) into source_count,candidates from public.season_roster_assignments where league_season_id=source_id;
 if source_count<>candidates then perform public.raise_phase3b6c1_failure('target_roster_duplicate_player','{}');end if;
 select count(distinct player_id) into candidates from(
  select a.player_id from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
   where a.league_id=x.league_id and a.status='active' and cs.league_season_id=target_season.id
    and (cs.obligation_status='active' or public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id))
  union select player_id from public.rollover_contract_releases where rollover_execution_id=x.id
  union select player_id from public.rollover_commissioner_holds where rollover_execution_id=x.id)q;
 select count(*) into ordinary from public.rollover_contract_releases where rollover_execution_id=x.id and release_disposition='ordinary_release';
 select count(*) into held from public.rollover_commissioner_holds where rollover_execution_id=x.id and hold_status='active';
 select count(*) into continuing from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
  where a.league_id=x.league_id and a.status='active' and cs.league_season_id=target_season.id and cs.obligation_status='active';
 select count(*) into intentional from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
  where a.league_id=x.league_id and a.status='active' and cs.league_season_id=target_season.id
   and public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id);
 assigned:=continuing;
 if candidates<>assigned+ordinary+held+intentional then perform public.raise_phase3b6c1_failure('target_roster_population_accounting_mismatch','{}');end if;
 for r in select a.id agreement_id,a.league_team_id,a.player_id,cs.id contract_season_id,src.id source_assignment_id,
   src.roster_designation,pu.player_name from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
   and cs.league_season_id=target_season.id left join public.player_universe pu on pu.sleeper_id=a.player_id
   left join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active'
    and (cs.obligation_status='active' or public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id))
   order by a.player_id,a.league_team_id,a.id loop
  if public.phase3b8a_is_preserved_off_roster_liability(s.id,r.agreement_id,r.player_id,r.league_team_id) then
   exclusions:=exclusions||jsonb_build_array(jsonb_build_object('agreement_id',r.agreement_id,'player_id',r.player_id,
    'league_team_id',r.league_team_id,'disposition','preserve_active_liability','roster_disposition','intentional_exclusion'));
   continue;
  end if;
  if r.source_assignment_id is null then perform public.raise_phase3b6c1_failure('target_roster_mapping_incomplete','{}');end if;
  if (select league_team_id from public.season_roster_assignments where id=r.source_assignment_id)<>r.league_team_id then
   perform public.raise_phase3b6c1_failure('target_roster_owner_mismatch','{}');end if;
  row_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8a-target-roster-row-v2','execution',x.id,
   'league',x.league_id,'target_season',target_season.id,'team',r.league_team_id,'player',r.player_id,'agreement',r.agreement_id,
   'contract_season',r.contract_season_id,'source_assignment',r.source_assignment_id,'roster_status','pending_unpublished'));
  rows_material:=rows_material||jsonb_build_array(jsonb_build_object('player_id',r.player_id,'team_id',r.league_team_id,'row_hash',row_fp));
 end loop;
 if jsonb_array_length(rows_material)<>assigned or jsonb_array_length(exclusions)<>intentional then
  perform public.raise_phase3b6c1_failure('target_roster_exclusion_material_mismatch','{}');end if;
 aggregate_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8a-target-roster-set-v2','execution',x.id,
  'league',x.league_id,'target_season',target_season.id,'source_snapshot_hash',s.aggregate_snapshot_fingerprint,
  'mapping_fingerprint',s.mapping_fingerprint,'rows',rows_material,'intentional_exclusions',exclusions));
 select * into existing from public.rollover_target_roster_assignment_sets where league_id=x.league_id and target_league_season_id=target_season.id for update;
 if found then
  if existing.rollover_execution_id<>x.id or existing.source_snapshot_hash<>s.aggregate_snapshot_fingerprint
   or existing.aggregate_assignment_set_hash<>aggregate_fp or existing.expected_row_count<>assigned then
   perform public.raise_phase3b6c1_failure('target_roster_set_conflict','{}');end if;
  if(select count(*) from public.season_roster_assignments where assignment_set_id=existing.id)<>assigned then
   perform public.raise_phase3b6c1_failure('target_roster_hash_mismatch','{}');end if;
  return jsonb_build_object('candidate_player_count',candidates,'continuing_contract_count',continuing,'target_assigned_player_count',assigned,
   'ordinary_release_exclusion_count',ordinary,'commissioner_hold_exclusion_count',held,'intentional_exclusion_count',intentional,
   'preserved_off_roster_liability_count',intentional,'assignment_rows_written',0,'compatible_replay_count',1,
   'aggregate_assignment_set_hash',aggregate_fp,'assignment_set_id',existing.id,'mutation_count',0,'postcondition_count',10,
   'validation_codes',validation_codes,'publication_performed',false);
 end if;
 if exists(select 1 from public.season_roster_assignments where league_season_id=target_season.id) then
  perform public.raise_phase3b6c1_failure('target_roster_set_conflict','{}');end if;
 insert into public.rollover_target_roster_assignment_sets(id,rollover_execution_id,league_id,target_league_season_id,source_snapshot_id,
  source_snapshot_hash,mapping_fingerprint,expected_row_count,aggregate_assignment_set_hash,status,created_by)
 values(set_id,x.id,x.league_id,target_season.id,s.id,s.aggregate_snapshot_fingerprint,s.mapping_fingerprint,assigned,aggregate_fp,'complete_unpublished',p_actor);
 perform set_config('app.rollover_typed_execution','phase3b8a-v1',true);
 for r in select a.id agreement_id,a.league_team_id,a.player_id,cs.id contract_season_id,src.id source_assignment_id,
   src.roster_designation,pu.player_name from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
   and cs.league_season_id=target_season.id and cs.obligation_status='active' join public.player_universe pu on pu.sleeper_id=a.player_id
   join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id
   where a.league_id=x.league_id and a.status='active'
    and not public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id)
   order by a.player_id,a.league_team_id,a.id loop
  row_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b8a-target-roster-row-v2','execution',x.id,
   'league',x.league_id,'target_season',target_season.id,'team',r.league_team_id,'player',r.player_id,'agreement',r.agreement_id,
   'contract_season',r.contract_season_id,'source_assignment',r.source_assignment_id,'roster_status','pending_unpublished'));
  insert into public.season_roster_assignments(league_season_id,league_team_id,canonical_player_id,sleeper_player_id,player_name_snapshot,
   roster_designation,source,finalized_at,assignment_set_id,contract_agreement_id,target_contract_season_id,source_assignment_id,
   roster_status,provenance,deterministic_row_hash) values(target_season.id,r.league_team_id,r.player_id,r.player_id,r.player_name,
   'other','phase3b8a',clock_timestamp(),set_id,r.agreement_id,r.contract_season_id,r.source_assignment_id,'pending_unpublished',
   jsonb_build_object('rollover_execution_id',x.id,'source_roster_designation',r.roster_designation,
    'authorization_authority','league_memberships.league_team_id','sleeper_authoritative',false),row_fp);
  written:=written+1;
 end loop;
 if written<>assigned then perform public.raise_phase3b6c1_failure('target_roster_assignment_write_count_mismatch','{}');end if;
 return jsonb_build_object('candidate_player_count',candidates,'continuing_contract_count',continuing,'target_assigned_player_count',written,
  'ordinary_release_exclusion_count',ordinary,'commissioner_hold_exclusion_count',held,'intentional_exclusion_count',intentional,
  'preserved_off_roster_liability_count',intentional,'assignment_rows_written',written,'compatible_replay_count',0,
  'aggregate_assignment_set_hash',aggregate_fp,'assignment_set_id',set_id,'mutation_count',written+1,'postcondition_count',10,
  'validation_codes',validation_codes,'publication_performed',false);
end$$;

revoke all on function public.phase3b8a_is_preserved_off_roster_liability(uuid,uuid,text,uuid),
 public.validate_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid),
 public.write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid)
 from public,anon,authenticated,service_role;

commit;
