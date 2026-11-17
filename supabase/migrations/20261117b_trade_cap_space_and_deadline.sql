-- Cap space in trades + the trade deadline enforced inside the database (2026-11-17).
-- Cap space: an offer can send cap space (this season) from one team to the other, any amount (no limit).
-- When the offer is accepted, the sender is charged and the receiver credited (cap_adjustments 'trade_carryover',
-- the same rows a traded-FAAB Sleeper trade posts). The trade deadline: offers can't be sent or accepted after the
-- league's deadline week (Settings > trade deadline) until the season's games are over.
begin;

alter table public.trade_offers add column if not exists cap jsonb not null default '[]';
alter table public.trade_offers drop constraint if exists trade_offers_cap_is_array;
alter table public.trade_offers add constraint trade_offers_cap_is_array check(jsonb_typeof(cap)='array');

-- True after the deadline week: the current NFL week (the week of the first game on or after this Tuesday,
-- Eastern) is past the deadline week from Settings. Once the season's games are over there is no current week,
-- so trades reopen.
create or replace function public.trade_window_closed_private(p_league_id uuid) returns boolean
language sql stable security definer set search_path=pg_catalog,public as $$
 with s as (
  select nullif(substring(coalesce(trade_deadline,'') from '\d+'),'')::int as deadline
  from public.league_ai_settings where league_id=p_league_id
 ), wk as (
  select (date_trunc('day',now() at time zone 'America/New_York')
          - make_interval(days=>((extract(isodow from now() at time zone 'America/New_York')::int-2+7)%7)))::date as tuesday
 ), cur as (
  select min(g.week) as week from public.nfl_games g, wk
  where g.season=(select max(season) from public.nfl_games) and g.gameday>=wk.tuesday and g.week<=18
 )
 select coalesce((select deadline from s) < (select week from cur), false)
$$;

-- Checks every asset still belongs to the side giving it, nothing is locked, and builds the engine request.
create or replace function public.trade_offer_request_private(o public.trade_offers) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare item jsonb;agreement public.contract_agreements%rowtype;pick public.draft_pick_assets%rowtype;players jsonb:='[]';picks jsonb:='[]';cap jsonb:='[]';
begin
 for item in select value from jsonb_array_elements(o.players) loop
  if (item->>'from_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or (item->>'to_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or item->>'from_league_team_id'=item->>'to_league_team_id' then raise exception 'offer_side_invalid';end if;
  select * into agreement from public.contract_agreements where id=(item->>'contract_id')::uuid and league_id=o.league_id;
  if agreement.id is null or agreement.league_team_id<>(item->>'from_league_team_id')::uuid or agreement.status not in('active','scheduled') or agreement.superseded_by_contract_id is not null
   then raise exception 'offer_player_moved';end if;
  if public.player_locked_this_week_private(agreement.player_id) then raise exception 'offer_player_locked';end if;
  players:=players||jsonb_build_object('contract_id',agreement.id,'from_league_team_id',item->>'from_league_team_id','to_league_team_id',item->>'to_league_team_id');
 end loop;
 for item in select value from jsonb_array_elements(o.picks) loop
  if (item->>'from_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or (item->>'to_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or item->>'from_league_team_id'=item->>'to_league_team_id' then raise exception 'offer_side_invalid';end if;
  select * into pick from public.draft_pick_assets where stable_pick_id=(item->>'draft_pick_id')::uuid and league_id=o.league_id;
  if pick.stable_pick_id is null or pick.current_owner_league_team_id<>(item->>'from_league_team_id')::uuid or pick.asset_status<>'tradable' then raise exception 'offer_pick_moved';end if;
  picks:=picks||jsonb_build_object('draft_pick_id',pick.stable_pick_id,'from_league_team_id',item->>'from_league_team_id','to_league_team_id',item->>'to_league_team_id');
 end loop;
 -- Cap space: at most one amount from each side, more than $0, in whole dollars or cents.
 for item in select value from jsonb_array_elements(o.cap) loop
  if (item->>'from_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or (item->>'to_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or item->>'from_league_team_id'=item->>'to_league_team_id' then raise exception 'offer_side_invalid';end if;
  if coalesce((item->>'amount')::numeric,0)<=0 or (item->>'amount')::numeric<>round((item->>'amount')::numeric,2) then raise exception 'offer_cap_amount_invalid';end if;
  cap:=cap||jsonb_build_object('from_league_team_id',item->>'from_league_team_id','to_league_team_id',item->>'to_league_team_id','amount',round((item->>'amount')::numeric,2));
 end loop;
 if (select count(distinct x->>'from_league_team_id') from jsonb_array_elements(cap) x)<>jsonb_array_length(cap) then raise exception 'offer_cap_amount_invalid';end if;
 if jsonb_array_length(players)+jsonb_array_length(picks)=0 then raise exception 'offer_empty';end if;
 if not exists(select 1 from jsonb_array_elements(players||picks||cap) x where x->>'to_league_team_id'=o.to_league_team_id::text)
    or not exists(select 1 from jsonb_array_elements(players||picks||cap) x where x->>'to_league_team_id'=o.from_league_team_id::text)
  then raise exception 'offer_one_sided';end if;
 return jsonb_build_object('league_id',o.league_id,'idempotency_key','offer:'||o.id,'participant_team_ids',jsonb_build_array(o.from_league_team_id,o.to_league_team_id),
                           'player_movements',players,'draft_pick_movements',picks,'notes','Accepted trade offer '||o.id,'cap',cap);
end$$;

-- Propose (or counter, with parent_offer_id): {league_id, to_league_team_id, players, picks, cap?, note?, parent_offer_id?}
-- cap: [{from_league_team_id, to_league_team_id, amount}]
create or replace function public.propose_trade_offer_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;me uuid;parent public.trade_offers%rowtype;o public.trade_offers%rowtype;
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=lid),false) then raise exception 'transactions_not_enabled';end if;
 if public.trade_window_closed_private(lid) then raise exception 'trade_deadline_passed';end if;
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
 if nullif(p_request->>'parent_offer_id','') is not null then
  select * into parent from public.trade_offers where id=(p_request->>'parent_offer_id')::uuid and league_id=lid for update;
  if parent.id is null or parent.status<>'pending' or parent.to_league_team_id<>me then raise exception 'counter_not_allowed';end if;
  update public.trade_offers set status='countered',decided_by=actor,decided_at=clock_timestamp() where id=parent.id;
 end if;
 insert into public.trade_offers(league_id,from_league_team_id,to_league_team_id,players,picks,cap,note,parent_offer_id,created_by)
 values(lid,me,(p_request->>'to_league_team_id')::uuid,coalesce(p_request->'players','[]'),coalesce(p_request->'picks','[]'),coalesce(p_request->'cap','[]'),
        nullif(btrim(p_request->>'note'),''),parent.id,actor)
 returning * into o;
 if not exists(select 1 from public.league_teams t where t.id=o.to_league_team_id and t.league_id=lid) then raise exception 'offer_team_invalid';end if;
 perform public.trade_offer_request_private(o);  -- validates ownership, locks, cap amounts and both sides getting something
 return jsonb_build_object('offer_id',o.id,'status',o.status);
end$$;

-- Respond: {offer_id, action: accept | decline | cancel}
create or replace function public.respond_trade_offer_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();o public.trade_offers%rowtype;me uuid;act text:=p_request->>'action';result jsonb;req jsonb;item jsonb;season int;sender text;receiver text;amt numeric;
begin
 select * into o from public.trade_offers where id=(p_request->>'offer_id')::uuid for update;
 if o.id is null or o.status<>'pending' then raise exception 'offer_not_open';end if;
 me:=public.my_league_team_private(o.league_id);
 if act='cancel' then
  if me is distinct from o.from_league_team_id then raise exception 'not_your_offer';end if;
  update public.trade_offers set status='cancelled',decided_by=actor,decided_at=clock_timestamp() where id=o.id;
  return jsonb_build_object('offer_id',o.id,'status','cancelled');
 end if;
 if me is distinct from o.to_league_team_id then raise exception 'not_your_offer';end if;
 if act='decline' then
  update public.trade_offers set status='declined',decided_by=actor,decided_at=clock_timestamp() where id=o.id;
  return jsonb_build_object('offer_id',o.id,'status','declined');
 end if;
 if act<>'accept' then raise exception 'offer_action_invalid';end if;
 if not coalesce((select enabled from public.league_app_transactions where league_id=o.league_id),false) then raise exception 'transactions_not_enabled';end if;
 if public.trade_window_closed_private(o.league_id) then raise exception 'trade_deadline_passed';end if;
 req:=public.trade_offer_request_private(o);
 result:=public.execute_canonical_trade_private(req-'cap',actor);
 -- Cap space: charge the sender, credit the receiver (this season), same rows as a traded-FAAB Sleeper trade.
 select ls.season into season from public.league_seasons ls where ls.league_id=o.league_id and ls.is_active order by ls.season desc limit 1;
 for item in select value from jsonb_array_elements(req->'cap') loop
  amt:=(item->>'amount')::numeric;
  select owner_name into sender from public.league_teams where id=(item->>'from_league_team_id')::uuid;
  select owner_name into receiver from public.league_teams where id=(item->>'to_league_team_id')::uuid;
  insert into public.cap_adjustments(league_id,owner_name,player_name,season,adjustment_type,amount,counterparty_owner,note)
  values(o.league_id,sender,null,season,'trade_carryover',amt,receiver,format('Sends $%s cap space to %s in app trade %s',amt,receiver,result->>'trade_id')),
        (o.league_id,receiver,null,season,'trade_carryover',-amt,sender,format('Receives $%s cap space from %s in app trade %s',amt,sender,result->>'trade_id'));
 end loop;
 update public.trade_offers set status='accepted',decided_by=actor,decided_at=clock_timestamp(),canonical_trade_id=(result->>'trade_id')::uuid where id=o.id;
 -- Other open offers that included any asset that just moved can't happen anymore.
 update public.trade_offers x set status='void',void_reason='assets_moved',decided_at=clock_timestamp()
  where x.league_id=o.league_id and x.status='pending' and x.id<>o.id and (
   exists(select 1 from jsonb_array_elements(x.players) a join jsonb_array_elements(o.players) b on a->>'contract_id'=b->>'contract_id')
   or exists(select 1 from jsonb_array_elements(x.picks) a join jsonb_array_elements(o.picks) b on a->>'draft_pick_id'=b->>'draft_pick_id'));
 return jsonb_build_object('offer_id',o.id,'status','accepted','trade_id',result->>'trade_id');
end$$;

revoke all on function public.trade_window_closed_private(uuid),public.trade_offer_request_private(public.trade_offers) from public,anon,authenticated,service_role;
revoke all on function public.propose_trade_offer_authenticated(jsonb),public.respond_trade_offer_authenticated(jsonb) from public,anon,service_role;
grant execute on function public.propose_trade_offer_authenticated(jsonb),public.respond_trade_offer_authenticated(jsonb) to authenticated;

commit;
