begin;

-- Forward-only Phase A correction. The historical 20260730 migration remains immutable.
-- Capture completeness is the exact canonical team/Sleeper-roster set, never a fixture count.
create or replace function public.capture_pre_rollover_history(p_plan jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  v_season public.league_seasons%rowtype;
  v_existing public.historical_capture_executions%rowtype;
  v_counts jsonb;
  v_champions integer;
  v_team_count integer;
  v_canonical_team_fingerprint text;
  v_source_roster_fingerprint text;
  v_mapping_fingerprint text;
  v_standings_fingerprint text;
begin
  if jsonb_typeof(p_plan) is distinct from 'object'
     or jsonb_typeof(p_plan->'team_mappings') is distinct from 'array'
     or jsonb_typeof(p_plan->'standings') is distinct from 'array'
     or jsonb_typeof(p_plan->'source_roster_identifiers') is distinct from 'array'
     or jsonb_typeof(p_plan->'matchups') is distinct from 'array'
     or jsonb_typeof(p_plan->'brackets') is distinct from 'array'
     or jsonb_typeof(p_plan->'roster_assignments') is distinct from 'array' then
    raise exception 'Historical capture payload is malformed.';
  end if;

  select * into v_season from public.league_seasons
  where id = (p_plan->>'league_season_id')::uuid for share;
  if not found or v_season.league_id <> (p_plan->>'league_id')::uuid
     or v_season.season <> (p_plan->>'season')::integer or not v_season.is_active
     or v_season.sleeper_league_id <> p_plan->>'sleeper_league_id' then
    raise exception 'Historical capture source is not the authoritative active LeagueSeason.';
  end if;
  if v_season.season <> 2025 then raise exception 'Initial pre-rollover capture must source active 2025.'; end if;
  if exists(select 1 from public.league_seasons where league_id=v_season.league_id and season=2026 and is_active) then
    raise exception '2026 must remain upcoming during Phase 2.';
  end if;

  select * into v_existing from public.historical_capture_executions
  where idempotency_key=p_plan->>'idempotency_key';
  if found then
    if v_existing.source_fingerprint <> p_plan->>'source_fingerprint' then
      raise exception 'Existing capture fingerprint conflicts with current source.';
    end if;
    return jsonb_build_object('idempotent',true,'execution_id',v_existing.id,'row_counts',v_existing.row_counts);
  end if;

  select count(*) into v_team_count from public.league_teams where league_id=v_season.league_id;
  if v_team_count=0 then raise exception 'Historical capture canonical team set is empty.'; end if;
  if exists(select 1 from public.league_teams where league_id=v_season.league_id and sleeper_roster_id is null) then
    raise exception 'Historical capture canonical team is missing Sleeper roster identity.';
  end if;
  if exists(select 1 from public.league_teams where league_id=v_season.league_id group by sleeper_roster_id having count(*)<>1) then
    raise exception 'Historical capture canonical Sleeper roster identity is ambiguous.';
  end if;

  if exists(select 1 from jsonb_array_elements(p_plan->'source_roster_identifiers') x
            group by (x->>'sleeper_roster_id')::integer having count(*)<>1) then
    raise exception 'Historical capture source contains duplicate Sleeper roster identities.';
  end if;
  if exists(select 1 from jsonb_array_elements(p_plan->'team_mappings') x
            group by (x->>'league_team_id')::uuid having count(*)<>1)
     or exists(select 1 from jsonb_array_elements(p_plan->'team_mappings') x
               group by (x->>'sleeper_roster_id')::integer having count(*)<>1) then
    raise exception 'Historical capture contains duplicate or ambiguous team mappings.';
  end if;
  if exists(select 1 from jsonb_array_elements(p_plan->'standings') x
            group by (x->>'league_team_id')::uuid having count(*)<>1) then
    raise exception 'Historical capture contains duplicate standings teams.';
  end if;

  -- Bidirectional EXCEPT checks reject missing, foreign, and equal-count substitutions.
  if exists(
       (select sleeper_roster_id from public.league_teams where league_id=v_season.league_id
        except select (x->>'sleeper_roster_id')::integer from jsonb_array_elements(p_plan->'source_roster_identifiers') x)
       union all
       (select (x->>'sleeper_roster_id')::integer from jsonb_array_elements(p_plan->'source_roster_identifiers') x
        except select sleeper_roster_id from public.league_teams where league_id=v_season.league_id)
     ) then raise exception 'Historical capture source roster set does not equal the canonical roster set.'; end if;
  if exists(
       (select id from public.league_teams where league_id=v_season.league_id
        except select (x->>'league_team_id')::uuid from jsonb_array_elements(p_plan->'team_mappings') x)
       union all
       (select (x->>'league_team_id')::uuid from jsonb_array_elements(p_plan->'team_mappings') x
        except select id from public.league_teams where league_id=v_season.league_id)
     ) then raise exception 'Historical capture mapping team set does not equal the canonical team set.'; end if;
  if exists(
       (select sleeper_roster_id from public.league_teams where league_id=v_season.league_id
        except select (x->>'sleeper_roster_id')::integer from jsonb_array_elements(p_plan->'team_mappings') x)
       union all
       (select (x->>'sleeper_roster_id')::integer from jsonb_array_elements(p_plan->'team_mappings') x
        except select sleeper_roster_id from public.league_teams where league_id=v_season.league_id)
     ) then raise exception 'Historical capture mapping roster set does not equal the canonical roster set.'; end if;
  if exists(
       (select id from public.league_teams where league_id=v_season.league_id
        except select (x->>'league_team_id')::uuid from jsonb_array_elements(p_plan->'standings') x)
       union all
       (select (x->>'league_team_id')::uuid from jsonb_array_elements(p_plan->'standings') x
        except select id from public.league_teams where league_id=v_season.league_id)
     ) then raise exception 'Historical capture standings set does not equal the canonical team set.'; end if;

  if (p_plan->>'canonical_team_count')::integer<>v_team_count then
    raise exception 'Historical capture canonical team count diagnostic conflicts with authoritative set.';
  end if;
  if exists(
    select 1 from jsonb_array_elements(p_plan->'team_mappings') x
    left join public.league_teams t on t.id=(x->>'league_team_id')::uuid
    where (x->>'league_season_id')::uuid<>v_season.id or t.id is null
       or t.league_id<>v_season.league_id or t.sleeper_roster_id<>(x->>'sleeper_roster_id')::integer
  ) then raise exception 'Capture contains a cross-league, wrong-season, or substituted team mapping.'; end if;
  if exists(
    select 1 from jsonb_array_elements(p_plan->'standings') x
    left join public.league_teams t on t.id=(x->>'league_team_id')::uuid
    where (x->>'league_season_id')::uuid<>v_season.id or t.id is null or t.league_id<>v_season.league_id
  ) then raise exception 'Capture contains a cross-league or wrong-season standings row.'; end if;
  if exists(
    select 1 from jsonb_array_elements(p_plan->'roster_assignments') x
    left join public.league_teams t on t.id=(x->>'league_team_id')::uuid
    where (x->>'league_season_id')::uuid<>v_season.id or t.id is null or t.league_id<>v_season.league_id
  ) then raise exception 'Capture contains a cross-league or wrong-season roster assignment.'; end if;

  select count(*) into v_champions from jsonb_array_elements(p_plan->'brackets') b
  where b->>'bracket_type'='winner' and (b->>'placement')::integer=1
    and b->>'winner_league_team_id' is not null;
  if v_champions<>1 then raise exception 'Capture requires exactly one champion.'; end if;

  select public.rollover_material_fingerprint(coalesce(jsonb_agg(jsonb_build_object(
    'league_team_id',t.id,'sleeper_roster_id',t.sleeper_roster_id,'sleeper_user_id',t.sleeper_user_id)
    order by t.id,t.sleeper_roster_id,t.sleeper_user_id),'[]'::jsonb)) into v_canonical_team_fingerprint
  from public.league_teams t where t.league_id=v_season.league_id;
  select public.rollover_material_fingerprint(coalesce(jsonb_agg(jsonb_build_object(
    'sleeper_roster_id',(x->>'sleeper_roster_id')::integer,'sleeper_owner_id',x->>'sleeper_owner_id')
    order by (x->>'sleeper_roster_id')::integer,x->>'sleeper_owner_id'),'[]'::jsonb)) into v_source_roster_fingerprint
  from jsonb_array_elements(p_plan->'source_roster_identifiers') x;
  select public.rollover_material_fingerprint(coalesce(jsonb_agg(jsonb_build_object(
    'league_team_id',(x->>'league_team_id')::uuid,'sleeper_roster_id',(x->>'sleeper_roster_id')::integer,
    'sleeper_user_id',x->>'sleeper_user_id') order by (x->>'league_team_id')::uuid,(x->>'sleeper_roster_id')::integer),'[]'::jsonb))
    into v_mapping_fingerprint from jsonb_array_elements(p_plan->'team_mappings') x;
  select public.rollover_material_fingerprint(coalesce(jsonb_agg(jsonb_build_object(
    'league_team_id',(x->>'league_team_id')::uuid) order by (x->>'league_team_id')::uuid),'[]'::jsonb))
    into v_standings_fingerprint from jsonb_array_elements(p_plan->'standings') x;

  v_counts=jsonb_build_object(
    'team_mappings',jsonb_array_length(p_plan->'team_mappings'),'matchups',jsonb_array_length(p_plan->'matchups'),
    'standings',jsonb_array_length(p_plan->'standings'),'brackets',jsonb_array_length(p_plan->'brackets'),
    'roster_assignments',jsonb_array_length(p_plan->'roster_assignments'),'canonical_team_count',v_team_count,
    'canonical_team_set_fingerprint',v_canonical_team_fingerprint,'source_roster_set_fingerprint',v_source_roster_fingerprint,
    'mapping_set_fingerprint',v_mapping_fingerprint,'standings_set_fingerprint',v_standings_fingerprint);

  insert into public.historical_capture_executions
    (league_season_id,capture_type,idempotency_key,source,source_fingerprint,status,warnings,row_counts)
  values(v_season.id,'pre_rollover_snapshot',p_plan->>'idempotency_key','sleeper',p_plan->>'source_fingerprint',
         'validating',coalesce(p_plan->'warnings','[]'::jsonb),v_counts);
  insert into public.season_team_mappings
    (league_season_id,league_team_id,sleeper_roster_id,sleeper_owner_id,sleeper_user_id,team_name_snapshot,owner_name_snapshot,mapping_source,mapping_confidence)
  select (x->>'league_season_id')::uuid,(x->>'league_team_id')::uuid,(x->>'sleeper_roster_id')::integer,
    x->>'sleeper_owner_id',x->>'sleeper_user_id',x->>'team_name_snapshot',x->>'owner_name_snapshot',x->>'mapping_source',x->>'mapping_confidence'
  from jsonb_array_elements(p_plan->'team_mappings') x;
  insert into public.season_matchups
    (league_season_id,week,sleeper_matchup_id,league_team_1_id,league_team_2_id,sleeper_roster_1_id,sleeper_roster_2_id,team_1_points,team_2_points,winner_league_team_id,result,phase,source_payload)
  select (x->>'league_season_id')::uuid,(x->>'week')::integer,(x->>'sleeper_matchup_id')::integer,(x->>'league_team_1_id')::uuid,
    (x->>'league_team_2_id')::uuid,(x->>'sleeper_roster_1_id')::integer,(x->>'sleeper_roster_2_id')::integer,
    (x->>'team_1_points')::numeric,(x->>'team_2_points')::numeric,nullif(x->>'winner_league_team_id','')::uuid,
    x->>'result',x->>'phase',x->'source_payload' from jsonb_array_elements(p_plan->'matchups') x;
  insert into public.season_standings
    (league_season_id,league_team_id,wins,losses,ties,points_for,points_against,regular_season_rank,streak,source_payload)
  select (x->>'league_season_id')::uuid,(x->>'league_team_id')::uuid,(x->>'wins')::integer,(x->>'losses')::integer,
    (x->>'ties')::integer,(x->>'points_for')::numeric,(x->>'points_against')::numeric,
    (x->>'regular_season_rank')::integer,x->>'streak',x->'source_payload' from jsonb_array_elements(p_plan->'standings') x;
  insert into public.season_playoff_brackets
    (league_season_id,bracket_type,round,sleeper_bracket_match_id,team_1_id,team_2_id,winner_league_team_id,loser_league_team_id,placement,source_payload)
  select (x->>'league_season_id')::uuid,x->>'bracket_type',(x->>'round')::integer,(x->>'sleeper_bracket_match_id')::integer,
    nullif(x->>'team_1_id','')::uuid,nullif(x->>'team_2_id','')::uuid,nullif(x->>'winner_league_team_id','')::uuid,
    nullif(x->>'loser_league_team_id','')::uuid,nullif(x->>'placement','')::integer,x->'source_payload'
  from jsonb_array_elements(p_plan->'brackets') x;
  insert into public.season_roster_assignments
    (league_season_id,league_team_id,canonical_player_id,sleeper_player_id,player_name_snapshot,roster_designation,source)
  select (x->>'league_season_id')::uuid,(x->>'league_team_id')::uuid,x->>'canonical_player_id',x->>'sleeper_player_id',
    x->>'player_name_snapshot',x->>'roster_designation',x->>'source' from jsonb_array_elements(p_plan->'roster_assignments') x;

  update public.historical_capture_executions set status='validated',completed_at=now(),row_counts=v_counts
  where idempotency_key=p_plan->>'idempotency_key';
  return jsonb_build_object('idempotent',false,'row_counts',v_counts,'status','validated');
end $$;

revoke all on function public.capture_pre_rollover_history(jsonb) from public, anon, authenticated;
grant execute on function public.capture_pre_rollover_history(jsonb) to service_role;

comment on function public.capture_pre_rollover_history(jsonb) is
'Service-role-only atomic history capture using exact canonical team, Sleeper roster, mapping, and standings set equality.';

commit;
