-- Phase 2: immutable, season-scoped pre-rollover history.
-- Capturing these rows never completes or activates a league_seasons row.

create table if not exists public.season_team_mappings (
  id uuid primary key default gen_random_uuid(),
  league_season_id uuid not null references public.league_seasons(id),
  league_team_id uuid not null references public.league_teams(id),
  sleeper_roster_id integer not null,
  sleeper_owner_id text, sleeper_user_id text,
  team_name_snapshot text, owner_name_snapshot text,
  mapping_source text not null, mapping_confidence text not null,
  captured_at timestamptz not null default now(), finalized_at timestamptz,
  created_at timestamptz not null default now(),
  unique (league_season_id, league_team_id), unique (league_season_id, sleeper_roster_id)
);

create table if not exists public.season_matchups (
  id uuid primary key default gen_random_uuid(),
  league_season_id uuid not null references public.league_seasons(id),
  week integer not null check (week > 0), sleeper_matchup_id integer not null,
  league_team_1_id uuid not null references public.league_teams(id),
  league_team_2_id uuid not null references public.league_teams(id),
  sleeper_roster_1_id integer not null, sleeper_roster_2_id integer not null,
  team_1_points numeric not null, team_2_points numeric not null,
  winner_league_team_id uuid references public.league_teams(id), result text not null,
  phase text not null check (phase in ('regular','playoff','consolation')),
  source_payload jsonb not null default '{}'::jsonb,
  captured_at timestamptz not null default now(), finalized_at timestamptz,
  created_at timestamptz not null default now(),
  unique (league_season_id, week, sleeper_matchup_id),
  check (league_team_1_id <> league_team_2_id), check (sleeper_roster_1_id < sleeper_roster_2_id)
);

create table if not exists public.season_standings (
  id uuid primary key default gen_random_uuid(),
  league_season_id uuid not null references public.league_seasons(id),
  league_team_id uuid not null references public.league_teams(id),
  wins integer not null, losses integer not null, ties integer not null,
  points_for numeric not null, points_against numeric not null,
  standing_points numeric, regular_season_rank integer, playoff_seed integer,
  final_finish integer, is_champion boolean not null default false, streak text,
  source_payload jsonb not null default '{}'::jsonb,
  captured_at timestamptz not null default now(), finalized_at timestamptz,
  created_at timestamptz not null default now(), unique (league_season_id, league_team_id)
);

create table if not exists public.season_playoff_brackets (
  id uuid primary key default gen_random_uuid(),
  league_season_id uuid not null references public.league_seasons(id),
  bracket_type text not null check (bracket_type in ('winner','consolation')),
  round integer not null, sleeper_bracket_match_id integer not null,
  team_1_id uuid references public.league_teams(id), team_2_id uuid references public.league_teams(id),
  winner_league_team_id uuid references public.league_teams(id), loser_league_team_id uuid references public.league_teams(id),
  placement integer, source_payload jsonb not null default '{}'::jsonb,
  captured_at timestamptz not null default now(), finalized_at timestamptz,
  created_at timestamptz not null default now(),
  unique (league_season_id, bracket_type, sleeper_bracket_match_id)
);

create table if not exists public.season_roster_assignments (
  id uuid primary key default gen_random_uuid(),
  league_season_id uuid not null references public.league_seasons(id),
  league_team_id uuid not null references public.league_teams(id),
  canonical_player_id text, sleeper_player_id text not null, player_name_snapshot text,
  roster_designation text not null check (roster_designation in ('active','bench','taxi','ir','other')),
  source text not null, captured_at timestamptz not null default now(), finalized_at timestamptz,
  created_at timestamptz not null default now(), unique (league_season_id, sleeper_player_id)
);

create table if not exists public.historical_capture_executions (
  id uuid primary key default gen_random_uuid(), league_season_id uuid not null references public.league_seasons(id),
  capture_type text not null, idempotency_key text not null unique, source text not null,
  source_fingerprint text not null, status text not null check (status in ('planned','validating','captured','validated','finalized','failed')),
  started_at timestamptz not null default now(), completed_at timestamptz,
  row_counts jsonb not null default '{}'::jsonb, warnings jsonb not null default '[]'::jsonb,
  blocking_errors jsonb not null default '[]'::jsonb, created_by uuid, created_at timestamptz not null default now()
);

create or replace function public.reject_frozen_history_mutation() returns trigger language plpgsql
set search_path = pg_catalog, public as $$
begin raise exception 'Captured season history is immutable; create a reviewed correction instead.'; end $$;

do $$ declare t text;
begin
  foreach t in array array['season_team_mappings','season_matchups','season_standings','season_playoff_brackets','season_roster_assignments'] loop
    execute format('drop trigger if exists %I on public.%I', t || '_immutable', t);
    execute format('create trigger %I before update or delete on public.%I for each row execute function public.reject_frozen_history_mutation()', t || '_immutable', t);
  end loop;
end $$;

create or replace function public.capture_pre_rollover_history(p_plan jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  v_season public.league_seasons%rowtype; v_existing public.historical_capture_executions%rowtype;
  v_counts jsonb; v_champions integer;
begin
  select * into v_season from public.league_seasons where id = (p_plan->>'league_season_id')::uuid for share;
  if not found or v_season.league_id <> (p_plan->>'league_id')::uuid or v_season.season <> (p_plan->>'season')::integer
     or not v_season.is_active or v_season.sleeper_league_id <> p_plan->>'sleeper_league_id' then
    raise exception 'Historical capture source is not the authoritative active LeagueSeason.';
  end if;
  if v_season.season <> 2025 then raise exception 'Initial pre-rollover capture must source active 2025.'; end if;
  if exists (select 1 from public.league_seasons where league_id=v_season.league_id and season=2026 and is_active) then
    raise exception '2026 must remain upcoming during Phase 2.';
  end if;
  select * into v_existing from public.historical_capture_executions where idempotency_key=p_plan->>'idempotency_key';
  if found then
    if v_existing.source_fingerprint <> p_plan->>'source_fingerprint' then
      raise exception 'Existing capture fingerprint conflicts with current source.';
    end if;
    return jsonb_build_object('idempotent',true,'execution_id',v_existing.id,'row_counts',v_existing.row_counts);
  end if;
  if jsonb_array_length(p_plan->'team_mappings') <> 10 or jsonb_array_length(p_plan->'standings') <> 10 then
    raise exception 'Capture requires exactly 10 team mappings and standings rows.';
  end if;
  if exists (
    select 1 from jsonb_array_elements(p_plan->'team_mappings') x
    left join public.league_teams t on t.id=(x->>'league_team_id')::uuid
    where (x->>'league_season_id')::uuid <> v_season.id
       or t.id is null or t.league_id <> v_season.league_id
  ) then raise exception 'Capture contains a cross-league or wrong-season team mapping.'; end if;
  if exists (
    select 1 from jsonb_array_elements(p_plan->'roster_assignments') x
    left join public.league_teams t on t.id=(x->>'league_team_id')::uuid
    where (x->>'league_season_id')::uuid <> v_season.id
       or t.id is null or t.league_id <> v_season.league_id
  ) then raise exception 'Capture contains a cross-league or wrong-season roster assignment.'; end if;
  select count(*) into v_champions from jsonb_array_elements(p_plan->'brackets') b
   where b->>'bracket_type'='winner' and (b->>'placement')::integer=1 and b->>'winner_league_team_id' is not null;
  if v_champions <> 1 then raise exception 'Capture requires exactly one champion.'; end if;

  insert into public.historical_capture_executions
    (league_season_id,capture_type,idempotency_key,source,source_fingerprint,status,warnings)
  values (v_season.id,'pre_rollover_snapshot',p_plan->>'idempotency_key','sleeper',p_plan->>'source_fingerprint','validating',coalesce(p_plan->'warnings','[]'::jsonb));

  insert into public.season_team_mappings
    (league_season_id,league_team_id,sleeper_roster_id,sleeper_owner_id,sleeper_user_id,team_name_snapshot,owner_name_snapshot,mapping_source,mapping_confidence)
  select (x->>'league_season_id')::uuid,(x->>'league_team_id')::uuid,(x->>'sleeper_roster_id')::integer,x->>'sleeper_owner_id',x->>'sleeper_user_id',x->>'team_name_snapshot',x->>'owner_name_snapshot',x->>'mapping_source',x->>'mapping_confidence'
  from jsonb_array_elements(p_plan->'team_mappings') x;
  insert into public.season_matchups
    (league_season_id,week,sleeper_matchup_id,league_team_1_id,league_team_2_id,sleeper_roster_1_id,sleeper_roster_2_id,team_1_points,team_2_points,winner_league_team_id,result,phase,source_payload)
  select (x->>'league_season_id')::uuid,(x->>'week')::integer,(x->>'sleeper_matchup_id')::integer,(x->>'league_team_1_id')::uuid,(x->>'league_team_2_id')::uuid,(x->>'sleeper_roster_1_id')::integer,(x->>'sleeper_roster_2_id')::integer,(x->>'team_1_points')::numeric,(x->>'team_2_points')::numeric,nullif(x->>'winner_league_team_id','')::uuid,x->>'result',x->>'phase',x->'source_payload'
  from jsonb_array_elements(p_plan->'matchups') x;
  insert into public.season_standings
    (league_season_id,league_team_id,wins,losses,ties,points_for,points_against,regular_season_rank,streak,source_payload)
  select (x->>'league_season_id')::uuid,(x->>'league_team_id')::uuid,(x->>'wins')::integer,(x->>'losses')::integer,(x->>'ties')::integer,(x->>'points_for')::numeric,(x->>'points_against')::numeric,(x->>'regular_season_rank')::integer,x->>'streak',x->'source_payload'
  from jsonb_array_elements(p_plan->'standings') x;
  insert into public.season_playoff_brackets
    (league_season_id,bracket_type,round,sleeper_bracket_match_id,team_1_id,team_2_id,winner_league_team_id,loser_league_team_id,placement,source_payload)
  select (x->>'league_season_id')::uuid,x->>'bracket_type',(x->>'round')::integer,(x->>'sleeper_bracket_match_id')::integer,nullif(x->>'team_1_id','')::uuid,nullif(x->>'team_2_id','')::uuid,nullif(x->>'winner_league_team_id','')::uuid,nullif(x->>'loser_league_team_id','')::uuid,nullif(x->>'placement','')::integer,x->'source_payload'
  from jsonb_array_elements(p_plan->'brackets') x;
  insert into public.season_roster_assignments
    (league_season_id,league_team_id,canonical_player_id,sleeper_player_id,player_name_snapshot,roster_designation,source)
  select (x->>'league_season_id')::uuid,(x->>'league_team_id')::uuid,x->>'canonical_player_id',x->>'sleeper_player_id',x->>'player_name_snapshot',x->>'roster_designation',x->>'source'
  from jsonb_array_elements(p_plan->'roster_assignments') x;

  v_counts=jsonb_build_object('team_mappings',jsonb_array_length(p_plan->'team_mappings'),'matchups',jsonb_array_length(p_plan->'matchups'),'standings',jsonb_array_length(p_plan->'standings'),'brackets',jsonb_array_length(p_plan->'brackets'),'roster_assignments',jsonb_array_length(p_plan->'roster_assignments'));
  update public.historical_capture_executions set status='validated',completed_at=now(),row_counts=v_counts where idempotency_key=p_plan->>'idempotency_key';
  return jsonb_build_object('idempotent',false,'row_counts',v_counts,'status','validated');
end $$;

revoke all on function public.capture_pre_rollover_history(jsonb) from public;
grant execute on function public.capture_pre_rollover_history(jsonb) to service_role;

-- Phase 2 history is service-role-only until an explicitly authorized historical UI is designed.
do $$ declare t text;
begin
  foreach t in array array['season_team_mappings','season_matchups','season_standings','season_playoff_brackets','season_roster_assignments','historical_capture_executions'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from anon, authenticated', t);
    execute format('grant all on table public.%I to service_role', t);
  end loop;
end $$;
