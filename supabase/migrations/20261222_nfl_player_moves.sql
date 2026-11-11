-- NFL team moves (trades, signings, releases) seen by the hourly ESPN roster check and Sleeper's daily file.
-- The Front Office AI reads it to say "traded from LV to MIN on Nov 10". Safe to run more than once.
create table if not exists public.nfl_player_moves (
  id bigserial primary key,
  sleeper_id text not null,
  full_name text,
  position text,
  from_team text not null,   -- 'FA' = no team
  to_team text not null,
  source text not null,      -- espn_roster | sleeper
  seen_at timestamptz not null default now(),
  unique (sleeper_id, from_team, to_team)
);
create index if not exists nfl_player_moves_seen_idx on public.nfl_player_moves (seen_at desc);
create index if not exists nfl_player_moves_player_idx on public.nfl_player_moves (sleeper_id, seen_at desc);
alter table public.nfl_player_moves enable row level security;
drop policy if exists nfl_player_moves_authenticated_read on public.nfl_player_moves;
create policy nfl_player_moves_authenticated_read on public.nfl_player_moves for select to authenticated using (true);
grant select on public.nfl_player_moves to authenticated;
