-- Every contract move for one player in one league, for the app's player page.
-- contract_events has no read policy, so league members read it through this
-- checked function, the same way the Teams tab reads league state.
create or replace function public.read_player_contract_history_authenticated(p_league_id uuid, p_player_id text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare v_actor uuid:=public.require_authenticated_user();
begin
 if p_league_id is null or nullif(btrim(coalesce(p_player_id,'')),'') is null then raise exception 'player_history_request_invalid';end if;
 if not exists(select 1 from public.league_memberships where league_id=p_league_id and user_id=v_actor) then raise exception 'player_history_membership_required';end if;
 return coalesce((select jsonb_agg(to_jsonb(x) order by x.effective_at desc, x.created_at desc) from(
  select e.event_type,e.source,e.effective_season,e.effective_at,e.created_at,
   e.new_values,e.previous_values,e.metadata->>'acquisition_type' acquisition_type,
   ca.contract_type,ca.origin,ca.start_season,ca.end_season,ca.status contract_status,
   lt.id::text league_team_id,lt.owner_name
  from public.contract_events e
  join public.contract_agreements ca on ca.id=e.contract_id
  join public.league_teams lt on lt.id=e.league_team_id and lt.league_id=e.league_id
  where e.league_id=p_league_id and e.player_id=p_player_id
   and e.event_type in('imported','signed','extended','option_exercised','option_declined','traded','released','expired')
  order by e.effective_at desc limit 40)x),'[]'::jsonb);
end$$;
revoke all on function public.read_player_contract_history_authenticated(uuid,text) from public,anon,authenticated,service_role;
grant execute on function public.read_player_contract_history_authenticated(uuid,text) to authenticated;
