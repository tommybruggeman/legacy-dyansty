-- Commissioner lineups for app-run weeks: the commissioner can set any team's lineup (an owner who's away), under the
-- same rules and kickoff locks as the owner. Scores are never edited by hand: stat corrections flow in automatically
-- from the official stats (scripts/final_scores.py), and the league gets a push when one changes a score.

-- Lineup rules shared by owners and the commissioner (both keep the kickoff locks: nobody changes a score after the fact).
create or replace function public.save_team_lineup_private(lid uuid,tid uuid,yr int,wk int,wanted jsonb,actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare sid uuid;config text[];base jsonb;i int;pid text;old text;pos text;seen text[]:='{}';
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=lid),false) then raise exception 'transactions_not_enabled';end if;
 select id into sid from public.league_seasons where league_id=lid and season=yr;
 if sid is null or wk is null or wk not between 1 and 18 then raise exception 'lineup_week_invalid';end if;
 if exists(select 1 from public.nfl_games where season=yr and week=wk)
    and not exists(select 1 from public.nfl_games where season=yr and week=wk
                   and (gameday is null or gametime is null or ((gameday+gametime::time) at time zone 'America/New_York')>now()))
  then raise exception 'lineup_week_closed';end if;

 select array_agg(s order by n) into config
   from public.league_ai_settings x, jsonb_array_elements_text(x.roster_positions) with ordinality as r(s,n)
  where x.league_id=lid and s not in('BN','TAXI','IR');
 if config is null then raise exception 'lineup_slots_missing';end if;
 if jsonb_typeof(wanted)<>'array' or jsonb_array_length(wanted)<>array_length(config,1) then raise exception 'lineup_shape_invalid';end if;

 select slots into base from public.league_lineups
  where league_id=lid and league_team_id=tid and season=yr and week<=wk order by week desc limit 1;

 for i in 0..array_length(config,1)-1 loop
  if wanted->i->>'slot' is distinct from config[i+1] then raise exception 'lineup_shape_invalid';end if;
  pid:=nullif(wanted->i->>'player_id','');
  old:=case when base is not null and base->i->>'slot'=config[i+1] then nullif(base->i->>'player_id','') end;
  if pid is not null then
   if pid=any(seen) then raise exception 'lineup_duplicate';end if;
   seen:=seen||pid;
   if not public.team_holds_player_private(lid,tid,pid,yr) then raise exception 'lineup_player_not_yours';end if;
   if exists(select 1 from public.season_roster_assignments a where a.league_season_id=sid and a.sleeper_player_id=pid and a.roster_designation in('taxi','ir'))
      or exists(select 1 from public.cap_adjustments c where c.league_id=lid and c.season=yr and c.adjustment_type='ir_adjustment' and c.sleeper_player_id=pid)
    then raise exception 'lineup_player_inactive';end if;
   select p.position into pos from public.nfl_players p where p.sleeper_id=pid;
   if not public.lineup_slot_fits_private(config[i+1],coalesce(pos,case when pid ~ '^[A-Z]{2,3}$' then 'DEF' end)) then raise exception 'lineup_slot_mismatch';end if;
  end if;
  if pid is distinct from old then
   if (pid is not null and public.player_locked_for_week_private(pid,yr,wk)) or (old is not null and public.player_locked_for_week_private(old,yr,wk))
    then raise exception 'lineup_player_locked';end if;
  end if;
 end loop;

 insert into public.league_lineups(league_id,league_team_id,season,week,slots,updated_by)
 values(lid,tid,yr,wk,wanted,actor)
 on conflict(league_id,league_team_id,season,week) do update set slots=excluded.slots,updated_by=excluded.updated_by,updated_at=clock_timestamp();
 return jsonb_build_object('week',wk,'slots',wanted);
end$$;

-- Owners: {league_id, season, week, slots} for their own team (same rules as before, now shared).
create or replace function public.set_lineup_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;me uuid;
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=lid),false) then raise exception 'transactions_not_enabled';end if;
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
 return public.save_team_lineup_private(lid,me,(p_request->>'season')::int,(p_request->>'week')::int,coalesce(p_request->'slots','[]'),actor);
end$$;

-- Commissioner: {league_id, team_id, season, week, slots} for any team (an owner who's away), same locks.
create or replace function public.commissioner_set_lineup_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'team_id')::uuid;
begin
 perform public.require_commissioner_authority(lid);
 if not exists(select 1 from public.league_teams where id=tid and league_id=lid) then raise exception 'lineup_team_invalid';end if;
 return public.save_team_lineup_private(lid,tid,(p_request->>'season')::int,(p_request->>'week')::int,coalesce(p_request->'slots','[]'),actor);
end$$;

revoke all on function public.save_team_lineup_private(uuid,uuid,int,int,jsonb,uuid) from public,anon,authenticated;
revoke all on function public.commissioner_set_lineup_authenticated(jsonb) from public,anon;
revoke all on function public.set_lineup_authenticated(jsonb) from public,anon;
grant execute on function public.commissioner_set_lineup_authenticated(jsonb) to authenticated;
grant execute on function public.set_lineup_authenticated(jsonb) to authenticated;

-- Stat corrections found by the daily final-scores job: one row per league per correction, shown on Activity and
-- pushed to the whole league. changes = [{team_id, roster_id, old, new, players: [{player_id, old, new, stats}],
-- results: ['win'|'loss'|'tie'|'top5'|'not_top5']}]. Written by the job (service role) only.
create table if not exists public.stat_corrections(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 season int not null,
 week int not null check(week between 1 and 18),
 changes jsonb not null check(jsonb_typeof(changes)='array'),
 created_at timestamptz not null default clock_timestamp()
);
create index if not exists stat_corrections_league on public.stat_corrections(league_id,season,created_at desc);
alter table public.stat_corrections enable row level security;
revoke all on public.stat_corrections from public,anon,authenticated;
grant select on public.stat_corrections to authenticated;
grant select,insert on public.stat_corrections to service_role;
drop policy if exists stat_corrections_member_read on public.stat_corrections;
create policy stat_corrections_member_read on public.stat_corrections for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=stat_corrections.league_id and m.user_id=auth.uid()));
