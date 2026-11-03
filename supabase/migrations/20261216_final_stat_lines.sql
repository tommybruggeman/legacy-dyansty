-- Official final stat lines, our own copy (scripts/final_scores.py: nflverse's free stats, ESPN as backup),
-- one row per NFL week. Re-saved daily for stat corrections, then locked. Public NFL stats: any signed-in
-- owner may read; only the daily job (service role) writes.
create table if not exists public.final_stat_lines (
  season integer not null,
  week integer not null,
  lines jsonb not null default '{}'::jsonb,
  source text not null default 'nflverse',
  locked boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (season, week)
);

alter table public.final_stat_lines enable row level security;

drop policy if exists final_stat_lines_select on public.final_stat_lines;
create policy final_stat_lines_select on public.final_stat_lines for select to authenticated using (true);
