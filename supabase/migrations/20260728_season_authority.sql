-- Phase 1: one authoritative active season per Legacy league.
-- This does not implement or execute season rollover.

create unique index if not exists league_seasons_league_season_uidx
  on public.league_seasons (league_id, season);

create unique index if not exists league_seasons_one_active_per_league_uidx
  on public.league_seasons (league_id)
  where is_active is true;

comment on table public.league_seasons is
  'Canonical operational season authority. Application runtime must not use league_settings.season_current.';

comment on column public.league_seasons.is_active is
  'Exactly one row per league is active. Inactive earlier rows may be completed; inactive later rows are upcoming.';
