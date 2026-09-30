-- NFL data layer for League AI. League-independent; refreshed nightly by
-- .github/workflows/nfl-data-sync.yml with the service role. Members read.
begin;

create table if not exists public.nfl_players (
  sleeper_id text primary key,
  full_name text not null,
  first_name text,
  last_name text,
  search_name text,                       -- lowercased, punctuation stripped, for matching
  position text,
  team text,
  age numeric,
  birthdate date,
  years_exp integer,
  depth_chart_order integer,
  depth_chart_position text,
  injury_status text,
  injury_body_part text,
  injury_notes text,
  status text,
  active boolean,
  college text,
  draft_year integer,
  draft_round integer,
  draft_pick integer,
  draft_ovr integer,
  height text,
  weight integer,
  gsis_id text,
  fantasypros_id text,
  pfr_id text,
  cfbref_id text,
  ktc_id text,
  search_rank integer,
  source text not null default 'sleeper',
  refreshed_at timestamptz not null default now()
);
create index if not exists nfl_players_search_name_idx on public.nfl_players (search_name);
create index if not exists nfl_players_gsis_idx on public.nfl_players (gsis_id);
create index if not exists nfl_players_position_team_idx on public.nfl_players (position, team);

create table if not exists public.nfl_player_stats (
  gsis_id text not null,
  season integer not null,
  week integer not null,
  season_type text not null default 'REG',
  sleeper_id text,
  player_name text,
  position text,
  team text,
  opponent text,
  is_home boolean,
  completions numeric, attempts numeric, passing_yards numeric, passing_tds numeric, interceptions numeric,
  sacks numeric, passing_epa numeric,
  carries numeric, rushing_yards numeric, rushing_tds numeric, rushing_first_downs numeric, rushing_epa numeric,
  targets numeric, receptions numeric, receiving_yards numeric, receiving_tds numeric, receiving_air_yards numeric,
  receiving_yac numeric, receiving_first_downs numeric, receiving_epa numeric,
  target_share numeric, air_yards_share numeric, wopr numeric,
  fumbles_lost numeric,
  offense_snaps numeric, offense_pct numeric,
  fantasy_points numeric, fantasy_points_ppr numeric,
  source text not null default 'nflverse',
  refreshed_at timestamptz not null default now(),
  primary key (gsis_id, season, week, season_type)
);
create index if not exists nfl_player_stats_sleeper_season_idx on public.nfl_player_stats (sleeper_id, season);
create index if not exists nfl_player_stats_season_week_idx on public.nfl_player_stats (season, week);

create table if not exists public.nfl_market_values (
  fantasypros_id text not null,
  scrape_date date not null,
  sleeper_id text,
  player_name text not null,
  position text,
  team text,
  age numeric,
  draft_year integer,
  ecr_1qb numeric, ecr_2qb numeric, ecr_pos numeric,
  value_1qb integer, value_2qb integer,
  source text not null default 'dynastyprocess',
  refreshed_at timestamptz not null default now(),
  primary key (fantasypros_id, scrape_date)
);
create index if not exists nfl_market_values_sleeper_idx on public.nfl_market_values (sleeper_id, scrape_date desc);
create index if not exists nfl_market_values_date_idx on public.nfl_market_values (scrape_date desc);

create table if not exists public.nfl_pick_values (
  pick_label text not null,               -- e.g. "2027 Pick 1.02", "2027 Early 1st", "2028 1st"
  scrape_date date not null,
  draft_year integer,
  round integer,
  slot text,                              -- 'early','mid','late', '1.02' ... when present
  ecr_1qb numeric, ecr_2qb numeric,
  value_1qb integer, value_2qb integer,
  source text not null default 'dynastyprocess',
  refreshed_at timestamptz not null default now(),
  primary key (pick_label, scrape_date)
);

create table if not exists public.nfl_data_sync_log (
  id bigserial primary key,
  loader text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  rows_written integer,
  ok boolean,
  detail text
);

alter table public.nfl_players enable row level security;
alter table public.nfl_player_stats enable row level security;
alter table public.nfl_market_values enable row level security;
alter table public.nfl_pick_values enable row level security;
alter table public.nfl_data_sync_log enable row level security;

do $$
declare t text;
begin
  foreach t in array array['nfl_players','nfl_player_stats','nfl_market_values','nfl_pick_values','nfl_data_sync_log'] loop
    execute format('drop policy if exists %I on public.%I', t||'_authenticated_read', t);
    execute format('create policy %I on public.%I for select to authenticated using (true)', t||'_authenticated_read', t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end$$;

commit;
