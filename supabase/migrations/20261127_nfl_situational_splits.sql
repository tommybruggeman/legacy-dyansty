-- Situational splits for League AI: how defenses play (blitz rate, pressure, stacked boxes)
-- and how QBs and RBs perform against it. Built nightly from nflverse play-by-play joined to
-- FTN charting (pipeline/nfl_data/situational.py). One row per season, kind and player/team.

create table if not exists public.nfl_situational_splits (
  season integer not null,
  kind text not null check (kind in ('defense', 'qb', 'rb')),
  key text not null,              -- defense: team code; qb/rb: gsis player id
  team text,
  name text,
  metrics jsonb not null default '{}'::jsonb,
  refreshed_at timestamptz not null default now(),
  primary key (season, kind, key)
);
create index if not exists nfl_situational_splits_season_kind_idx on public.nfl_situational_splits (season, kind);

alter table public.nfl_situational_splits enable row level security;
drop policy if exists nfl_situational_splits_authenticated_read on public.nfl_situational_splits;
create policy nfl_situational_splits_authenticated_read on public.nfl_situational_splits for select to authenticated using (true);
grant select on public.nfl_situational_splits to authenticated;
