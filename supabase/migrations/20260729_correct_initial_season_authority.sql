-- Correct the initial pre-rollover Legacy state.
-- Idempotent: preserve both season rows and both Sleeper league identities.

alter table public.league_seasons
  add column if not exists status text;

alter table public.league_seasons
  add column if not exists previous_league_season_id uuid
    references public.league_seasons(id);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.league_seasons'::regclass
      and conname = 'league_seasons_status_check'
  ) then
    alter table public.league_seasons add constraint league_seasons_status_check
      check (status in ('active', 'scheduled', 'completed'));
  end if;
end $$;

update public.league_seasons
set is_active = false,
    status = 'scheduled'
where league_id = '9838a0a1-97c6-4cab-bb88-af177317abfe'
  and season = 2026;

update public.league_seasons
set is_active = true,
    status = 'active'
where league_id = '9838a0a1-97c6-4cab-bb88-af177317abfe'
  and season = 2025;

update public.league_seasons upcoming
set previous_league_season_id = active.id
from public.league_seasons active
where upcoming.league_id = '9838a0a1-97c6-4cab-bb88-af177317abfe'
  and upcoming.season = 2026
  and active.league_id = upcoming.league_id
  and active.season = 2025;

update public.leagues
set sleeper_league_id = '1257435354890260480'
where id = '9838a0a1-97c6-4cab-bb88-af177317abfe';

do $$
begin
  if (select count(*) from public.league_seasons
      where league_id = '9838a0a1-97c6-4cab-bb88-af177317abfe' and is_active) <> 1 then
    raise exception 'Season correction must leave exactly one active season.';
  end if;
end $$;
