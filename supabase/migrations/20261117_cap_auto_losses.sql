-- Over-the-cap rule (2026-11-17): a team over the cap at Sunday's first kickoff takes an automatic loss that week.
-- The alert job writes one row per team per week; standings and the playoff bracket read it.
create table if not exists public.cap_auto_losses(
 league_id uuid not null references public.leagues(id),
 season int not null,
 week int not null check(week between 1 and 18),
 league_team_id uuid not null references public.league_teams(id),
 over_by numeric(10,2) not null,
 recorded_at timestamptz not null default clock_timestamp(),
 primary key(league_id,season,week,league_team_id)
);
alter table public.cap_auto_losses enable row level security;
drop policy if exists cap_auto_losses_member_read on public.cap_auto_losses;
create policy cap_auto_losses_member_read on public.cap_auto_losses for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=cap_auto_losses.league_id and m.user_id=auth.uid()));
grant select on public.cap_auto_losses to authenticated;
