begin;

-- contract_transition_executions records the legacy, already-applied contract
-- mutation. It cannot be a prerequisite for rollover operations 10-12, which
-- own the target option mutation. This read-only helper mirrors the corrected
-- preflight invariant and creates no authority or execution evidence.
create or replace function public.rollover_contract_preflight_readiness_private(
  p_league_id uuid, p_source_season integer, p_target_season integer
) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public
as $$
declare
  source_id uuid;
  target_id uuid;
  agreement_count integer;
  source_count integer;
  prepared_option_count integer;
  rostered_expired_count integer;
  active_target_count integer;
  prior_transition_count integer;
  duplicate_count integer;
  ownership_conflict_count integer;
  blockers jsonb := '[]'::jsonb;
  material jsonb;
begin
  if p_target_season <> p_source_season + 1 then
    blockers := blockers || '"contract_season_boundary_invalid"'::jsonb;
  end if;
  select id into source_id from public.league_seasons
   where league_id=p_league_id and season=p_source_season and is_active and status='active';
  select id into target_id from public.league_seasons
   where league_id=p_league_id and season=p_target_season and not is_active and status='scheduled';
  if source_id is null then blockers:=blockers||'"contract_source_season_authority_missing"'::jsonb;end if;
  if target_id is null then blockers:=blockers||'"contract_target_season_authority_missing"'::jsonb;end if;

  select count(*) into agreement_count from public.contract_agreements where league_id=p_league_id;
  select count(*) into source_count from public.contract_seasons where league_id=p_league_id and season=p_source_season;
  select count(*) into active_target_count from public.contract_seasons where league_id=p_league_id and season=p_target_season and obligation_status='active';
  select count(*) into prior_transition_count from public.contract_transition_executions where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season;
  select count(*) into duplicate_count from (
    select contract_id,season from public.contract_seasons where league_id=p_league_id group by contract_id,season having count(*)<>1
  ) q;
  select count(*) into ownership_conflict_count
  from public.contract_seasons s join public.contract_agreements a on a.id=s.contract_id
  where a.league_id=p_league_id and (s.league_id<>a.league_id or s.league_team_id<>a.league_team_id or s.player_id<>a.player_id);
  select count(*) into rostered_expired_count
  from public.contract_agreements a
  where a.league_id=p_league_id and a.status='expired'
    and exists(select 1 from public.season_roster_assignments r where r.league_season_id=source_id and r.sleeper_player_id=a.player_id);
  select count(*) into prepared_option_count
  from public.contract_agreements a
  where a.league_id=p_league_id and a.status='expired'
    and exists(select 1 from public.season_roster_assignments r where r.league_season_id=source_id and r.sleeper_player_id=a.player_id)
    and exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season=p_target_season
      and s.obligation_status='scheduled' and s.is_option_year and s.option_type is not null);

  if agreement_count=0 then blockers:=blockers||'"normalized_contract_agreements_missing"'::jsonb;end if;
  if source_count<>agreement_count then blockers:=blockers||'"contract_source_obligation_population_mismatch"'::jsonb;end if;
  if prepared_option_count<>rostered_expired_count then blockers:=blockers||'"prepared_target_option_population_mismatch"'::jsonb;end if;
  if active_target_count<>0 then blockers:=blockers||'"target_contract_authority_already_activated"'::jsonb;end if;
  if prior_transition_count<>0 then blockers:=blockers||'"prior_contract_transition_conflicts_with_rollover"'::jsonb;end if;
  if duplicate_count<>0 then blockers:=blockers||'"contract_obligation_duplicate"'::jsonb;end if;
  if ownership_conflict_count<>0 then blockers:=blockers||'"contract_ownership_mismatch"'::jsonb;end if;

  material:=jsonb_build_object('schema','rollover-contract-preflight-v1','league_id',p_league_id,
    'source_season',p_source_season,'target_season',p_target_season,'agreement_count',agreement_count,
    'source_count',source_count,'rostered_expired_count',rostered_expired_count,
    'prepared_option_count',prepared_option_count,'active_target_count',active_target_count,
    'prior_transition_count',prior_transition_count,'duplicate_count',duplicate_count,
    'ownership_conflict_count',ownership_conflict_count,'blockers',blockers);
  return material||jsonb_build_object('ready',jsonb_array_length(blockers)=0,
    'deterministic_fingerprint',public.rollover_material_fingerprint(material));
end
$$;

create or replace function public.get_rollover_contract_preflight_readiness_service(
  p_league_id uuid, p_source_season integer, p_target_season integer
) returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select public.rollover_contract_preflight_readiness_private(p_league_id,p_source_season,p_target_season)
$$;

revoke all on function public.rollover_contract_preflight_readiness_private(uuid,integer,integer),
  public.get_rollover_contract_preflight_readiness_service(uuid,integer,integer)
  from public, anon, authenticated, service_role;
grant execute on function public.get_rollover_contract_preflight_readiness_service(uuid,integer,integer) to service_role;

comment on function public.get_rollover_contract_preflight_readiness_service(uuid,integer,integer)
is 'Read-only Option 2 preflight invariant; creates no contract transition or rollover evidence.';

commit;
