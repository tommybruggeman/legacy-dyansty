-- Weekly player write-ups (league_ai/writeups.py). One short recap per NFL player per week, written from our own
-- NFL tables only (no league rosters or owners), so every signed-in member can read every row.
create table if not exists public.player_writeups (
  season integer not null,
  week integer not null,
  gsis_id text not null,
  sleeper_id text,
  position text,
  text text not null,
  chips jsonb not null default '[]'::jsonb,
  model text,
  created_at timestamptz not null default now(),
  primary key (season, week, gsis_id)
);
create index if not exists player_writeups_sleeper_idx on public.player_writeups (sleeper_id, season desc, week desc);

alter table public.player_writeups enable row level security;
drop policy if exists player_writeups_authenticated_read on public.player_writeups;
create policy player_writeups_authenticated_read on public.player_writeups for select to authenticated using (true);
grant select on public.player_writeups to authenticated;
