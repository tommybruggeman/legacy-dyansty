-- Injury layer for League AI: ESPN current injury report (with return dates) and
-- nflverse weekly practice/game-status reports (history). Members read.
begin;

create table if not exists public.nfl_injuries (
  espn_id text primary key,
  sleeper_id text,
  gsis_id text,
  player_name text not null,
  search_name text,
  position text,
  team text,
  status text,                 -- Out, Doubtful, Questionable, Injured Reserve, Day-To-Day, ...
  injury_type text,            -- Knee, Hamstring, ...
  location text,               -- Leg, Arm, ...
  detail text,                 -- ACL Tear, Sprain, ...
  side text,
  return_date date,            -- ESPN's estimated return
  fantasy_status text,
  short_comment text,
  long_comment text,
  reported_at timestamptz,
  source text not null default 'espn',
  refreshed_at timestamptz not null default now()
);
create index if not exists nfl_injuries_sleeper_idx on public.nfl_injuries (sleeper_id);
create index if not exists nfl_injuries_search_idx on public.nfl_injuries (search_name);

create table if not exists public.nfl_practice_reports (
  gsis_id text not null,
  season integer not null,
  week integer not null,
  sleeper_id text,
  player_name text,
  position text,
  team text,
  report_injury text,          -- official game-status report injury
  report_status text,          -- Out, Doubtful, Questionable
  practice_injury text,
  practice_status text,        -- Did Not Participate / Limited / Full Participation in Practice
  source text not null default 'nflverse',
  refreshed_at timestamptz not null default now(),
  primary key (gsis_id, season, week)
);
create index if not exists nfl_practice_reports_sleeper_idx on public.nfl_practice_reports (sleeper_id, season desc, week desc);

alter table public.nfl_injuries enable row level security;
alter table public.nfl_practice_reports enable row level security;
do $$
declare t text;
begin
  foreach t in array array['nfl_injuries','nfl_practice_reports'] loop
    execute format('drop policy if exists %I on public.%I', t||'_authenticated_read', t);
    execute format('create policy %I on public.%I for select to authenticated using (true)', t||'_authenticated_read', t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end$$;

commit;
