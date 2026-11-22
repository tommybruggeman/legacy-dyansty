-- Prospect Power Rankings: one weekly GM article (shared by every league, no league data) + each owner's starred prospects.
begin;

create table if not exists public.prospect_rankings (
  edition_date date primary key,
  season integer not null,
  number integer not null default 1,
  data jsonb not null,
  model text,
  cost_usd numeric,
  created_at timestamptz not null default now()
);
alter table public.prospect_rankings enable row level security;
drop policy if exists prospect_rankings_read on public.prospect_rankings;
create policy prospect_rankings_read on public.prospect_rankings for select to authenticated using (true);
grant select on public.prospect_rankings to authenticated;

create table if not exists public.prospect_stars (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  athlete_id text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, athlete_id)
);
alter table public.prospect_stars enable row level security;
drop policy if exists prospect_stars_own on public.prospect_stars;
create policy prospect_stars_own on public.prospect_stars for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, delete on public.prospect_stars to authenticated;

commit;
