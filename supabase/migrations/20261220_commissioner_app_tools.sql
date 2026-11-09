-- Commissioner tools in the app's Settings: fix a contract, take an owner off a team, and the league switch that turns
-- on in-app trades, waivers and lineups. Every call checks that the signed-in person is this league's commissioner.
begin;
set local search_path = pg_catalog, public;

create or replace function public.is_league_commissioner_private(p_league_id uuid)
returns boolean language sql stable security definer set search_path = pg_catalog, public as $$
  select exists (select 1 from public.league_memberships where league_id = p_league_id and user_id = auth.uid() and role = 'commissioner');
$$;
revoke all on function public.is_league_commissioner_private(uuid) from public, anon, authenticated;

-- 1. Fix a contract: new salary for this season and later, and/or a new last season.
--    Past seasons never change. Shortening voids the seasons cut off; lengthening needs those league seasons set up.
create or replace function public.commissioner_fix_contract_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare cid uuid := (p_request->>'contract_id')::uuid; new_salary numeric := (p_request->>'salary')::numeric;
  new_end int := (p_request->>'end_season')::int; note text := coalesce(p_request->>'notes', '');
  c public.contract_agreements%rowtype; active_season int; s int; ls_id uuid; before jsonb; ratio numeric;
begin
  select * into c from public.contract_agreements where id = cid for update;
  if c.id is null or c.status not in ('active', 'scheduled') or c.superseded_by_contract_id is not null then raise exception 'contract_not_live'; end if;
  if not public.is_league_commissioner_private(c.league_id) then raise exception 'Commissioner authority required'; end if;
  perform public.assert_no_active_rollover_cutover_lock(c.league_id);
  select season into active_season from public.league_seasons where league_id = c.league_id and is_active and status = 'active';
  if active_season is null then raise exception 'contract_season_missing'; end if;
  if new_salary is not null and (new_salary < 0 or new_salary > 1000) then raise exception 'contract_salary_invalid'; end if;
  if new_end is not null and (new_end < greatest(active_season, c.start_season) or new_end > active_season + 10) then raise exception 'contract_end_invalid'; end if;
  select jsonb_build_object('end_season', c.end_season, 'seasons', coalesce(jsonb_agg(jsonb_build_object('season', season, 'salary', salary, 'cap_hit', cap_hit) order by season), '[]'))
    into before from public.contract_seasons where contract_id = cid;

  perform set_config('app.contract_transition_execution', 'contract-transition-executor-v1', true);
  if new_salary is not null then
    -- cap hit keeps its share of salary (IR/taxi rate), so a fix doesn't undo a reduced cap charge
    update public.contract_seasons set
      cap_hit = case when salary > 0 then round(new_salary * (cap_hit / salary), 2) else new_salary end,
      salary = new_salary, updated_at = now()
    where contract_id = cid and season >= active_season and obligation_status in ('active', 'scheduled');
  end if;
  if new_end is not null and new_end < c.end_season then
    update public.contract_seasons set obligation_status = 'voided', updated_at = now()
    where contract_id = cid and season > new_end and obligation_status in ('active', 'scheduled');
  elsif new_end is not null and new_end > c.end_season then
    for s in c.end_season + 1 .. new_end loop
      select id into ls_id from public.league_seasons where league_id = c.league_id and season = s;
      if ls_id is null then raise exception 'contract_season_missing'; end if;
      insert into public.contract_seasons (contract_id, league_season_id, league_id, league_team_id, player_id, season, salary, cap_hit,
                                           obligation_status, source)
      select cid, ls_id, c.league_id, c.league_team_id, c.player_id, s, coalesce(new_salary, last.salary), coalesce(new_salary, last.salary),
             'scheduled', 'commissioner_adjustment'
      from public.contract_seasons last where last.contract_id = cid order by last.season desc limit 1
      on conflict (contract_id, season) do update set obligation_status = 'scheduled', salary = excluded.salary, cap_hit = excluded.cap_hit, updated_at = now();
    end loop;
  end if;
  if new_end is not null then
    update public.contract_agreements set end_season = new_end, updated_at = now() where id = cid;
  end if;

  insert into public.contract_events (contract_id, league_id, league_team_id, player_id, event_type, effective_season, source,
                                      actor_user_id, previous_values, new_values, metadata, idempotency_key)
  values (cid, c.league_id, c.league_team_id, c.player_id, 'restructured', active_season, 'commissioner_adjustment', auth.uid(), before,
          jsonb_build_object('salary', new_salary, 'end_season', coalesce(new_end, c.end_season)), jsonb_build_object('notes', note),
          'commish-fix:' || cid || ':' || extract(epoch from clock_timestamp())::text);
  return jsonb_build_object('contract_id', cid, 'salary', new_salary, 'end_season', coalesce(new_end, c.end_season));
end $$;
revoke all on function public.commissioner_fix_contract_authenticated(jsonb) from public, anon;
grant execute on function public.commissioner_fix_contract_authenticated(jsonb) to authenticated;

-- 2. Owners: everyone in the league with their team and email (commissioner only), and taking someone off a team
--    (to hand the team to a new owner, invite them after).
create or replace function public.commissioner_league_people_authenticated(p_league_id uuid)
returns jsonb language plpgsql stable security definer set search_path = pg_catalog, public as $$
begin
  if not public.is_league_commissioner_private(p_league_id) then raise exception 'Commissioner authority required'; end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('user_id', m.user_id, 'league_team_id', m.league_team_id, 'role', m.role, 'email', u.email)), '[]')
          from public.league_memberships m left join auth.users u on u.id = m.user_id where m.league_id = p_league_id);
end $$;
revoke all on function public.commissioner_league_people_authenticated(uuid) from public, anon;
grant execute on function public.commissioner_league_people_authenticated(uuid) to authenticated;

create or replace function public.commissioner_remove_member_authenticated(p_league_id uuid, p_user_id uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare gone int;
begin
  if not public.is_league_commissioner_private(p_league_id) then raise exception 'Commissioner authority required'; end if;
  if p_user_id = auth.uid() then raise exception 'member_is_you'; end if;
  delete from public.league_memberships where league_id = p_league_id and user_id = p_user_id and role <> 'commissioner';
  get diagnostics gone = row_count;
  if gone = 0 then raise exception 'member_not_found'; end if;
  return jsonb_build_object('removed', p_user_id);
end $$;
revoke all on function public.commissioner_remove_member_authenticated(uuid, uuid) from public, anon;
grant execute on function public.commissioner_remove_member_authenticated(uuid, uuid) to authenticated;

-- 3. The league switch: in-app trades, waivers and lineups (and our own scoring from own_from_week).
create or replace function public.commissioner_set_app_transactions_authenticated(p_league_id uuid, p_enabled boolean, p_own_from_week int)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  if not public.is_league_commissioner_private(p_league_id) then raise exception 'Commissioner authority required'; end if;
  insert into public.league_app_transactions as t (league_id, enabled, own_from_week, updated_at)
  values (p_league_id, p_enabled, p_own_from_week, clock_timestamp())
  on conflict (league_id) do update set enabled = excluded.enabled, own_from_week = excluded.own_from_week, updated_at = clock_timestamp();
  return (select to_jsonb(t) from public.league_app_transactions t where league_id = p_league_id);
end $$;
revoke all on function public.commissioner_set_app_transactions_authenticated(uuid, boolean, int) from public, anon;
grant execute on function public.commissioner_set_app_transactions_authenticated(uuid, boolean, int) to authenticated;

commit;
