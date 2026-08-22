-- Stage 3: Assistant Identity, Memory, and Access Hardening
-- Adds explicit league and league-team scope for Coach Condor memory/brain tables.
-- This migration is additive and non-destructive.

alter table if exists public.league_memberships
  add column if not exists league_team_id uuid;

alter table if exists public.gm_user_memory
  add column if not exists league_id uuid,
  add column if not exists league_team_id uuid;

alter table if exists public.team_brain
  add column if not exists league_id uuid,
  add column if not exists league_team_id uuid;

alter table if exists public.league_brain
  add column if not exists league_id uuid;

alter table if exists public.player_strategic_profiles
  add column if not exists league_id uuid,
  add column if not exists league_team_id uuid;

alter table if exists public.league_relative_player_values
  add column if not exists league_id uuid,
  add column if not exists league_team_id uuid;

create unique index if not exists gm_user_memory_user_league_team_uidx
  on public.gm_user_memory (user_id, league_id, league_team_id);

create index if not exists gm_user_memory_league_team_idx
  on public.gm_user_memory (league_id, league_team_id)
  where league_id is not null
    and league_team_id is not null;

create unique index if not exists team_brain_league_team_uidx
  on public.team_brain (league_id, league_team_id);

create unique index if not exists league_brain_league_id_uidx
  on public.league_brain (league_id);

create index if not exists player_strategic_profiles_league_team_idx
  on public.player_strategic_profiles (league_id, league_team_id)
  where league_id is not null
    and league_team_id is not null;

create index if not exists league_relative_player_values_league_team_idx
  on public.league_relative_player_values (league_id, league_team_id)
  where league_id is not null
    and league_team_id is not null;

comment on column public.gm_user_memory.league_id is
  'Assistant memory league scope. New Coach Condor reads/writes must include this with user_id and league_team_id.';

comment on column public.gm_user_memory.league_team_id is
  'Assistant memory league-team scope referencing the canonical league_teams identity used by invitations.';

comment on index public.gm_user_memory_user_league_team_uidx is
  'Prevents Coach Condor memory collisions across users, leagues, and league teams.';

comment on column public.team_brain.league_id is
  'Optional assistant brain league scope for newly rebuilt team brain rows.';

comment on column public.team_brain.league_team_id is
  'Optional assistant brain league-team scope for newly rebuilt team brain rows.';

comment on column public.league_brain.league_id is
  'Optional assistant brain league scope for newly rebuilt league brain rows.';

comment on column public.player_strategic_profiles.league_id is
  'Optional assistant profile league scope for newly rebuilt strategic profile rows.';

comment on column public.player_strategic_profiles.league_team_id is
  'Optional assistant profile league-team scope for newly rebuilt strategic profile rows.';

comment on column public.league_relative_player_values.league_id is
  'Optional assistant value league scope for newly rebuilt league-relative value rows.';

comment on column public.league_relative_player_values.league_team_id is
  'Optional assistant value league-team scope for newly rebuilt league-relative value rows.';

-- Rollback, if needed after review:
-- drop index if exists public.league_relative_player_values_league_team_idx;
-- drop index if exists public.player_strategic_profiles_league_team_idx;
-- drop index if exists public.league_brain_league_id_uidx;
-- drop index if exists public.team_brain_league_team_uidx;
-- drop index if exists public.gm_user_memory_league_team_idx;
-- drop index if exists public.gm_user_memory_user_league_team_uidx;
-- alter table if exists public.league_relative_player_values drop column if exists league_team_id, drop column if exists league_id;
-- alter table if exists public.player_strategic_profiles drop column if exists league_team_id, drop column if exists league_id;
-- alter table if exists public.league_brain drop column if exists league_id;
-- alter table if exists public.team_brain drop column if exists league_team_id, drop column if exists league_id;
-- alter table if exists public.gm_user_memory drop column if exists league_team_id, drop column if exists league_id;
