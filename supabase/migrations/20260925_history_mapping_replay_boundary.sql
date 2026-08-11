begin;

-- The hosted initiation readiness check requires the canonical source-season
-- mapping to exist before a first history capture.  History capture freezes
-- that same mapping.  Accept an insert replay only when every material field
-- is identical; a changed mapping continues to fail on the existing unique
-- constraints and can never overwrite frozen history.
create or replace function public.accept_identical_history_mapping_replay()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  existing public.season_team_mappings%rowtype;
begin
  select * into existing
  from public.season_team_mappings
  where league_season_id = new.league_season_id
    and (league_team_id = new.league_team_id
         or sleeper_roster_id = new.sleeper_roster_id)
  for share;

  if existing.id is null then
    return new;
  end if;

  if existing.league_team_id = new.league_team_id
     and existing.sleeper_roster_id = new.sleeper_roster_id
     and existing.sleeper_owner_id is not distinct from new.sleeper_owner_id
     and existing.sleeper_user_id is not distinct from new.sleeper_user_id
     and existing.team_name_snapshot is not distinct from new.team_name_snapshot
     and existing.owner_name_snapshot is not distinct from new.owner_name_snapshot
     and existing.mapping_source = new.mapping_source
     and existing.mapping_confidence = new.mapping_confidence then
    return null;
  end if;

  return new;
end
$$;

drop trigger if exists season_team_mappings_identical_insert_replay
  on public.season_team_mappings;
create trigger season_team_mappings_identical_insert_replay
before insert on public.season_team_mappings
for each row execute function public.accept_identical_history_mapping_replay();

revoke all on function public.accept_identical_history_mapping_replay()
  from public, anon, authenticated, service_role;

commit;
