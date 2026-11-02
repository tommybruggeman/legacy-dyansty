-- Live NFL stat lines from our game-day watcher (scripts/live_watcher.py, ESPN's free feed): one row per NFL game,
-- so each save sends only the game that changed. The row with game_id '_watcher' is the watcher's heartbeat.
-- Public NFL stats, so any signed-in owner may read them; only the watcher (service role) writes.
create table if not exists public.live_stat_lines (
  season integer not null,
  week integer not null,
  game_id text not null,
  lines jsonb not null default '{}'::jsonb,
  games jsonb not null default '{}'::jsonb,
  source text not null default 'main',
  updated_at timestamptz not null default now(),
  primary key (season, week, game_id)
);

alter table public.live_stat_lines enable row level security;

drop policy if exists live_stat_lines_select on public.live_stat_lines;
create policy live_stat_lines_select on public.live_stat_lines for select to authenticated using (true);
