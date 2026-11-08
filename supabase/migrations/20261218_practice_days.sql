-- Day-by-day practice participation (DNP / Limited / Full) for the Front Office AI and the player page.
-- One row per player, report day and source: the official report (via nflverse) and ESPN's injury notes.
-- Readers prefer the official line. Filled by the NFL data sync (loader "practice"). Members read.
begin;

create table if not exists public.nfl_practice_days (
  gsis_id text not null,
  season integer not null,
  week integer not null,
  practice_date date not null,
  source text not null,          -- nflverse (official report) | espn_news
  day text,                      -- Mon..Sun
  days_to_game integer,
  practice_status text,          -- DNP | Limited | Full
  game_status text,              -- Out / Doubtful / Questionable once the final report is in
  injury text,
  note text,
  player_name text,
  position text,
  team text,
  sleeper_id text,
  refreshed_at timestamptz not null default now(),
  primary key (gsis_id, season, week, practice_date, source)
);
create index if not exists nfl_practice_days_week_idx on public.nfl_practice_days (season, week);

alter table public.nfl_practice_days enable row level security;
drop policy if exists nfl_practice_days_authenticated_read on public.nfl_practice_days;
create policy nfl_practice_days_authenticated_read on public.nfl_practice_days for select to authenticated using (true);
grant select on public.nfl_practice_days to authenticated;

commit;
