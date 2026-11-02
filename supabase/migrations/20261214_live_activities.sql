-- Lock screen Live Activities: each running activity's push token, so the game-day watcher can send it scores.
-- Owners save and end only their own; the watcher (service role) reads every running one.
begin;

create table if not exists public.live_activities (
  activity_id text primary key,
  user_id uuid not null default auth.uid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  roster_id integer not null,
  week integer not null,
  matchup_id integer not null,
  push_token text not null,
  started_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  ended_at timestamptz
);
create index if not exists live_activities_running_idx on public.live_activities (ended_at) where ended_at is null;

alter table public.live_activities enable row level security;

drop policy if exists live_activities_select on public.live_activities;
create policy live_activities_select on public.live_activities for select to authenticated
using (user_id = auth.uid());

drop policy if exists live_activities_insert on public.live_activities;
create policy live_activities_insert on public.live_activities for insert to authenticated
with check (
  user_id = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = live_activities.league_id and lm.user_id = auth.uid())
);

drop policy if exists live_activities_update on public.live_activities;
create policy live_activities_update on public.live_activities for update to authenticated
using (user_id = auth.uid())
with check (
  user_id = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = live_activities.league_id and lm.user_id = auth.uid())
);

grant select, insert, update on public.live_activities to authenticated;

commit;
