-- Commissioner scoring tools for app-run weeks: fix a score, set any team's lineup, finalize a week.
-- Only matter while the league's switch (league_app_transactions.enabled) is on: those weeks are scored by our own
-- engine (api/own_league.py), and these rows are applied on top of it.

-- A commissioner score fix for one team in one week.
--   player_id set:  that player's points this week become `points` (a stat the feed got wrong).
--   player_id null: `points` is added to the team's total (a ruling, a penalty, a bonus).
create table if not exists public.league_score_fixes(
 league_id uuid not null references public.leagues(id),
 season int not null,
 week int not null check(week between 1 and 18),
 league_team_id uuid not null references public.league_teams(id),
 player_id text not null default '',  -- '' = team adjustment
 points numeric(8,2) not null,
 note text,
 created_by uuid references auth.users(id),
 created_at timestamptz not null default clock_timestamp(),
 primary key(league_id,season,week,league_team_id,player_id)
);
alter table public.league_score_fixes enable row level security;
revoke all on public.league_score_fixes from public,anon,authenticated;
grant select on public.league_score_fixes to authenticated;
grant select,insert,update,delete on public.league_score_fixes to service_role;
drop policy if exists league_score_fixes_member_read on public.league_score_fixes;
create policy league_score_fixes_member_read on public.league_score_fixes for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=league_score_fixes.league_id and m.user_id=auth.uid()));

-- A finalized week: its scores are frozen. Finalizing saves the week's matchup rows as scored at that moment (before
-- score fixes); later stat corrections, roster moves and lineup edits don't change it. Score fixes still apply on
-- top. Deleting the row (un-finalize) lets the week be scored from the stats again.
create table if not exists public.league_week_finals(
 league_id uuid not null references public.leagues(id),
 season int not null,
 week int not null check(week between 1 and 18),
 rows jsonb not null check(jsonb_typeof(rows)='array'),
 finalized_by uuid references auth.users(id),
 finalized_at timestamptz not null default clock_timestamp(),
 primary key(league_id,season,week)
);
alter table public.league_week_finals enable row level security;
revoke all on public.league_week_finals from public,anon,authenticated;
grant select on public.league_week_finals to authenticated;
grant select,insert,update,delete on public.league_week_finals to service_role;
drop policy if exists league_week_finals_member_read on public.league_week_finals;
create policy league_week_finals_member_read on public.league_week_finals for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=league_week_finals.league_id and m.user_id=auth.uid()));

-- {league_id, season, week, team_id, player_id|null, points|null (null removes the fix), note}
create or replace function public.commissioner_fix_score_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'season')::int;wk int:=(p_request->>'week')::int;
 tid uuid:=(p_request->>'team_id')::uuid;pid text:=coalesce(nullif(p_request->>'player_id',''),'');pts numeric:=(p_request->>'points')::numeric;
begin
 perform public.require_commissioner_authority(lid);
 if wk is null or wk not between 1 and 18 or yr is null then raise exception 'fix_week_invalid';end if;
 if not exists(select 1 from public.league_teams where id=tid and league_id=lid) then raise exception 'fix_team_invalid';end if;
 if pts is null then
  delete from public.league_score_fixes where league_id=lid and season=yr and week=wk and league_team_id=tid and player_id=pid;
  return jsonb_build_object('removed',true);
 end if;
 if abs(pts)>500 then raise exception 'fix_points_invalid';end if;
 insert into public.league_score_fixes(league_id,season,week,league_team_id,player_id,points,note,created_by)
 values(lid,yr,wk,tid,pid,pts,nullif(left(coalesce(p_request->>'note',''),200),''),auth.uid())
 on conflict(league_id,season,week,league_team_id,player_id) do update
  set points=excluded.points,note=excluded.note,created_by=excluded.created_by,created_at=clock_timestamp();
 return jsonb_build_object('week',wk,'team_id',tid,'player_id',nullif(pid,''),'points',pts);
end$$;

-- {league_id, season, week, final: true|false, rows: [matchup rows as scored now] (with final=true)}
create or replace function public.commissioner_finalize_week_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'season')::int;wk int:=(p_request->>'week')::int;
begin
 perform public.require_commissioner_authority(lid);
 if wk is null or wk not between 1 and 18 or yr is null then raise exception 'fix_week_invalid';end if;
 if coalesce((p_request->>'final')::boolean,true) then
  -- The official stats (final_stat_lines, saved by the daily final-scores job the morning after the week's last game).
  if not exists(select 1 from public.final_stat_lines where season=yr and week=wk) then raise exception 'finalize_week_no_official_stats';end if;
  if jsonb_typeof(p_request->'rows') is distinct from 'array' or jsonb_array_length(p_request->'rows')=0 then raise exception 'finalize_rows_missing';end if;
  insert into public.league_week_finals(league_id,season,week,rows,finalized_by) values(lid,yr,wk,p_request->'rows',auth.uid())
  on conflict(league_id,season,week) do nothing;
 else
  delete from public.league_week_finals where league_id=lid and season=yr and week=wk;
 end if;
 return jsonb_build_object('week',wk,'final',coalesce((p_request->>'final')::boolean,true));
end$$;

-- Lineup rules shared by owners and the commissioner. p_locks=false (commissioner) skips the kickoff locks and the
-- week-closed check, so a commissioner can fix a lineup after the fact; a finalized week can't change either way.
create or replace function public.save_team_lineup_private(lid uuid,tid uuid,yr int,wk int,wanted jsonb,actor uuid,p_locks boolean)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare sid uuid;config text[];base jsonb;i int;pid text;old text;pos text;seen text[]:='{}';
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=lid),false) then raise exception 'transactions_not_enabled';end if;
 select id into sid from public.league_seasons where league_id=lid and season=yr;
 if sid is null or wk is null or wk not between 1 and 18 then raise exception 'lineup_week_invalid';end if;
 if exists(select 1 from public.league_week_finals where league_id=lid and season=yr and week=wk) then raise exception 'lineup_week_finalized';end if;
 if p_locks and exists(select 1 from public.nfl_games where season=yr and week=wk)
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
  if p_locks and pid is distinct from old then
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
 return public.save_team_lineup_private(lid,me,(p_request->>'season')::int,(p_request->>'week')::int,coalesce(p_request->'slots','[]'),actor,true);
end$$;

-- Commissioner: {league_id, team_id, season, week, slots} for any team, locks skipped.
create or replace function public.commissioner_set_lineup_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'team_id')::uuid;
begin
 perform public.require_commissioner_authority(lid);
 if not exists(select 1 from public.league_teams where id=tid and league_id=lid) then raise exception 'fix_team_invalid';end if;
 return public.save_team_lineup_private(lid,tid,(p_request->>'season')::int,(p_request->>'week')::int,coalesce(p_request->'slots','[]'),actor,false);
end$$;

revoke all on function public.save_team_lineup_private(uuid,uuid,int,int,jsonb,uuid,boolean) from public,anon,authenticated;
revoke all on function public.commissioner_fix_score_authenticated(jsonb) from public,anon;
revoke all on function public.commissioner_finalize_week_authenticated(jsonb) from public,anon;
revoke all on function public.commissioner_set_lineup_authenticated(jsonb) from public,anon;
revoke all on function public.set_lineup_authenticated(jsonb) from public,anon;
grant execute on function public.commissioner_fix_score_authenticated(jsonb) to authenticated;
grant execute on function public.commissioner_finalize_week_authenticated(jsonb) to authenticated;
grant execute on function public.commissioner_set_lineup_authenticated(jsonb) to authenticated;
grant execute on function public.set_lineup_authenticated(jsonb) to authenticated;
