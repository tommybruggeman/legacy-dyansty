-- Injury returns and chance to play for League AI.
-- nfl_injury_episodes: every past absence (games missed, the injury, role before, first games back).
-- nfl_injury_play_rates: how often a listed player actually played, by game status and practice status.
-- nfl_players gets Sleeper's injury start date and practice participation.
-- Built nightly by pipeline/nfl_data/injury_history.py. Members read.

create table if not exists public.nfl_injury_episodes (
  gsis_id text not null,
  last_game_season integer not null,
  last_game_week integer not null,
  sleeper_id text,
  player_name text,
  position text,
  team text,
  injury text,                    -- body part from the weekly reports: knee, hamstring, ankle ...
  return_season integer,
  return_week integer,
  games_missed integer not null,
  returned boolean not null default false,
  return_game_status text,        -- his game status the week he came back (Questionable ...)
  pre_games integer, pre_snap_pct numeric, pre_opps numeric, pre_ppr numeric,
  post1_snap_pct numeric, post1_opps numeric, post1_ppr numeric,
  post3_games integer, post3_snap_pct numeric, post3_opps numeric, post3_ppr numeric,
  refreshed_at timestamptz not null default now(),
  primary key (gsis_id, last_game_season, last_game_week)
);
create index if not exists nfl_injury_episodes_lookup_idx on public.nfl_injury_episodes (position, injury);
create index if not exists nfl_injury_episodes_sleeper_idx on public.nfl_injury_episodes (sleeper_id);

create table if not exists public.nfl_injury_play_rates (
  scope text not null,            -- 'all', 'pos:RB', 'injury:hamstring'
  game_status text not null,      -- 'Out', 'Doubtful', 'Questionable' or '' (no game status)
  practice text not null,         -- 'DNP', 'Limited', 'Full' or '' (any)
  listings integer not null,
  played integer not null,
  rate numeric not null,
  refreshed_at timestamptz not null default now(),
  primary key (scope, game_status, practice)
);

alter table public.nfl_players add column if not exists injury_start_date text;   -- as Sleeper sends it
alter table public.nfl_players add column if not exists practice_participation text;
alter table public.nfl_players add column if not exists practice_description text;
alter table public.nfl_players add column if not exists news_updated bigint;

alter table public.nfl_injury_episodes enable row level security;
alter table public.nfl_injury_play_rates enable row level security;
drop policy if exists nfl_injury_episodes_authenticated_read on public.nfl_injury_episodes;
create policy nfl_injury_episodes_authenticated_read on public.nfl_injury_episodes for select to authenticated using (true);
drop policy if exists nfl_injury_play_rates_authenticated_read on public.nfl_injury_play_rates;
create policy nfl_injury_play_rates_authenticated_read on public.nfl_injury_play_rates for select to authenticated using (true);
grant select on public.nfl_injury_episodes to authenticated;
grant select on public.nfl_injury_play_rates to authenticated;
