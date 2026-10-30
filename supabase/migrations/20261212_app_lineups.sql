-- Lineups set in the app (playoffs on, then every week once the league leaves Sleeper).
-- One row per team per week: the starters in the league's slot order (league_ai_settings.roster_positions minus BN/TAXI/IR).
-- Only works while the league's switch (league_app_transactions.enabled) is on. A player locks at his game's kickoff:
-- once his game has started he can't move in or out of the lineup that week.

create table if not exists public.league_lineups(
 league_id uuid not null references public.leagues(id),
 league_team_id uuid not null references public.league_teams(id),
 season int not null,
 week int not null check(week between 1 and 18),
 slots jsonb not null check(jsonb_typeof(slots)='array'),  -- [{slot, player_id|null}]
 updated_by uuid references auth.users(id),
 updated_at timestamptz not null default clock_timestamp(),
 primary key(league_id,league_team_id,season,week)
);
alter table public.league_lineups enable row level security;
revoke all on public.league_lineups from public,anon,authenticated;
grant select on public.league_lineups to authenticated;
grant select,insert,update on public.league_lineups to service_role;
drop policy if exists league_lineups_member_read on public.league_lineups;
create policy league_lineups_member_read on public.league_lineups for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=league_lineups.league_id and m.user_id=auth.uid()));

-- True once the player's NFL team has kicked off in that week.
create or replace function public.player_locked_for_week_private(p_player_id text,p_season int,p_week int) returns boolean
language sql stable security definer set search_path=pg_catalog,public as $$
 select exists(
  select 1 from public.nfl_games g
  where g.season=p_season and g.week=p_week and g.gameday is not null and g.gametime is not null
    and coalesce((select p.team from public.nfl_players p where p.sleeper_id=p_player_id),p_player_id) in(g.home_team,g.away_team)
    and ((g.gameday+g.gametime::time) at time zone 'America/New_York')<=now()
 )
$$;

-- Which positions each lineup slot takes (Sleeper's slot names).
create or replace function public.lineup_slot_fits_private(p_slot text,p_pos text) returns boolean
language sql immutable set search_path=pg_catalog,public as $$
 select case p_slot
  when 'FLEX' then p_pos in('RB','WR','TE')
  when 'SUPER_FLEX' then p_pos in('QB','RB','WR','TE')
  when 'REC_FLEX' then p_pos in('WR','TE')
  when 'WRRB_FLEX' then p_pos in('RB','WR')
  else p_pos=p_slot end
$$;

-- {league_id, season, week, slots: [{slot, player_id|null}]}
create or replace function public.set_lineup_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;
 yr int:=(p_request->>'season')::int;wk int:=(p_request->>'week')::int;me uuid;sid uuid;
 config text[];wanted jsonb:=coalesce(p_request->'slots','[]');base jsonb;i int;pid text;old text;pos text;seen text[]:='{}';
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=lid),false) then raise exception 'transactions_not_enabled';end if;
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
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

 -- What the lineup was before this change: this week's row, else the latest earlier week this season.
 select slots into base from public.league_lineups
  where league_id=lid and league_team_id=me and season=yr and week<=wk order by week desc limit 1;

 for i in 0..array_length(config,1)-1 loop
  if wanted->i->>'slot' is distinct from config[i+1] then raise exception 'lineup_shape_invalid';end if;
  pid:=nullif(wanted->i->>'player_id','');
  old:=case when base is not null and base->i->>'slot'=config[i+1] then nullif(base->i->>'player_id','') end;
  if pid is not null then
   if pid=any(seen) then raise exception 'lineup_duplicate';end if;
   seen:=seen||pid;
   if not exists(select 1 from public.season_roster_assignments a where a.league_season_id=sid and a.league_team_id=me and a.sleeper_player_id=pid)
    then raise exception 'lineup_player_not_yours';end if;
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
 values(lid,me,yr,wk,wanted,actor)
 on conflict(league_id,league_team_id,season,week) do update set slots=excluded.slots,updated_by=excluded.updated_by,updated_at=clock_timestamp();
 return jsonb_build_object('week',wk,'slots',wanted);
end$$;

revoke all on function public.player_locked_for_week_private(text,int,int) from public,anon,authenticated;
revoke all on function public.lineup_slot_fits_private(text,text) from public,anon;
revoke all on function public.set_lineup_authenticated(jsonb) from public,anon;
grant execute on function public.set_lineup_authenticated(jsonb) to authenticated;
grant execute on function public.lineup_slot_fits_private(text,text) to authenticated,service_role;
grant execute on function public.player_locked_for_week_private(text,int,int) to service_role;
