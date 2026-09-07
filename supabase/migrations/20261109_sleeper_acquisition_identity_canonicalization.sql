begin;

create or replace function public.ensure_acquirable_sleeper_identity_private(p_player_id text)
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare sleeper_row public.sleeper_players%rowtype;
begin
 select * into sleeper_row from public.sleeper_players
 where sleeper_player_id=p_player_id for share;
 if sleeper_row.sleeper_player_id is null
  or upper(coalesce(sleeper_row.position,'')) not in('QB','RB','WR','TE')
 then raise exception 'canonical acquisition identity invalid';end if;

 insert into public.player_universe(
  sleeper_id,canonical_player_id,player_name,search_name,pos,nfl_team,
  nfl_status,active,updated_at
 ) values(
  sleeper_row.sleeper_player_id,sleeper_row.sleeper_player_id,
  sleeper_row.full_name,sleeper_row.search_name,upper(sleeper_row.position),
  sleeper_row.team,sleeper_row.status,sleeper_row.is_active,clock_timestamp()
 ) on conflict(sleeper_id) do nothing;
end$$;

do $$
declare definition text;
 old_guard text:='if not exists(select 1 from public.league_teams where id=tid and league_id=lid) or not exists(select 1 from public.player_universe where sleeper_id=pid) then raise exception ''canonical acquisition identity invalid'';end if;';
 new_guard text:='if not exists(select 1 from public.league_teams where id=tid and league_id=lid) then raise exception ''canonical acquisition identity invalid'';end if; perform public.ensure_acquirable_sleeper_identity_private(pid);';
begin
 select pg_get_functiondef('public.acquire_offseason_player_private(jsonb,uuid)'::regprocedure)
 into definition;
 if definition like '%ensure_acquirable_sleeper_identity_private(pid)%' then return;end if;
 if definition not like '%'||old_guard||'%' then
  raise exception 'canonical acquisition identity patch point missing';
 end if;
 execute replace(definition,old_guard,new_guard);
end$$;

revoke all on function public.ensure_acquirable_sleeper_identity_private(text)
from public,anon,authenticated,service_role;

commit;
