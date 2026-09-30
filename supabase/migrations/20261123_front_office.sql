-- Front Office: nightly power-ranking snapshots and cached weekly briefs.
begin;

create table if not exists public.league_power_rankings (
  league_id uuid not null references public.leagues(id) on delete cascade,
  computed_on date not null,
  league_team_id uuid not null,
  owner_name text,
  now_rank integer, now_score numeric,
  dynasty_rank integer, dynasty_score numeric,
  weakest_slot text, strongest_slot text,
  created_by uuid default auth.uid(),
  created_at timestamptz not null default now(),
  primary key (league_id, computed_on, league_team_id)
);

create table if not exists public.league_ai_briefs (
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null,
  league_team_id uuid,
  season integer not null,
  week integer not null,
  sections jsonb not null,
  generated_at timestamptz not null default now(),
  primary key (league_id, user_id, season, week)
);

alter table public.league_power_rankings enable row level security;
alter table public.league_ai_briefs enable row level security;

drop policy if exists league_power_rankings_member on public.league_power_rankings;
create policy league_power_rankings_member on public.league_power_rankings for all to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_power_rankings.league_id and lm.user_id = auth.uid()))
with check (exists (select 1 from public.league_memberships lm where lm.league_id = league_power_rankings.league_id and lm.user_id = auth.uid()));

drop policy if exists league_ai_briefs_own on public.league_ai_briefs;
create policy league_ai_briefs_own on public.league_ai_briefs for all to authenticated
using (user_id = auth.uid()) with check (user_id = auth.uid());

grant select, insert, update on public.league_power_rankings to authenticated;
grant select, insert, update, delete on public.league_ai_briefs to authenticated;

commit;
