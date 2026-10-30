-- Owner-to-owner trade offers for the app (built now, switched on per league after the league leaves Sleeper).
--
-- An owner proposes an offer to another team; the other owner accepts, declines or counters; the sender can cancel.
-- Accepting runs the same canonical trade engine the commissioner uses (20261103), so contracts, contract
-- seasons, events and pick ownership move exactly as they do for a commissioner-entered trade. Trades go
-- through the moment they're accepted (no review). A player whose NFL game has kicked off this week
-- (Tuesday to Monday, Eastern time) can't be offered or traded until the week turns over.
begin;

-- 1. The trade engine without the commissioner check, for server-side callers that have done their own check.
create or replace function public.execute_canonical_trade_private(p_request jsonb,p_actor uuid) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=p_actor;lid uuid:=(p_request->>'league_id')::uuid;idem text:=nullif(btrim(p_request->>'idempotency_key'),'');notes text:=nullif(btrim(p_request->>'notes'),'');participants jsonb:=p_request->'participant_team_ids';players jsonb:=coalesce(p_request->'player_movements','[]');picks jsonb:=coalesce(p_request->'draft_pick_movements','[]');retentions jsonb:=coalesce(p_request->'retained_salary','[]');fp text;trade public.canonical_trades%rowtype;item jsonb;season_item jsonb;agreement public.contract_agreements%rowtype;pick public.draft_pick_assets%rowtype;obligation public.contract_seasons%rowtype;from_id uuid;to_id uuid;v_pick_id uuid;v_contract_id uuid;amount numeric;yr int;movement_count int:=0;retained_count int:=0;
begin
 if idem is null or jsonb_typeof(participants)<>'array' or jsonb_array_length(participants) not between 2 and 4 or jsonb_typeof(players)<>'array' or jsonb_typeof(picks)<>'array' or jsonb_typeof(retentions)<>'array' then raise exception 'trade_request_invalid';end if;
 if(select count(distinct value) from jsonb_array_elements_text(participants))<>jsonb_array_length(participants) then raise exception 'trade_participants_duplicate';end if;
 if exists(select 1 from jsonb_array_elements_text(participants)p(value) left join public.league_teams t on t.id=p.value::uuid and t.league_id=lid where t.id is null) then raise exception 'trade_participant_not_in_league';end if;
 fp:=encode(extensions.digest(convert_to((p_request-'notes')::text,'UTF8'),'sha256'),'hex');
 perform pg_advisory_xact_lock(hashtextextended('canonical-trade:'||lid::text||':'||idem,0));
 select * into trade from public.canonical_trades where league_id=lid and idempotency_key=idem;
 if trade.id is not null then if trade.request_fingerprint<>fp then raise exception 'trade_idempotency_conflict';end if;return jsonb_build_object('trade_id',trade.id,'status',trade.status,'idempotent',true);end if;
 if exists(select 1 from jsonb_array_elements(players||picks)x group by coalesce(x->>'contract_id',x->>'draft_pick_id') having count(*)>1) then raise exception 'trade_asset_duplicated';end if;
 insert into public.canonical_trades(league_id,status,idempotency_key,request_fingerprint,notes,created_by)values(lid,'completed',idem,fp,notes,actor)returning * into trade;
 insert into public.canonical_trade_participants(trade_id,league_id,league_team_id)select trade.id,lid,value::uuid from jsonb_array_elements_text(participants);
 for item in select value from jsonb_array_elements(players)loop
  v_contract_id:=(item->>'contract_id')::uuid;from_id:=(item->>'from_league_team_id')::uuid;to_id:=(item->>'to_league_team_id')::uuid;
  if from_id=to_id or not(participants ? from_id::text)or not(participants ? to_id::text)then raise exception 'trade_player_participant_invalid';end if;
  select * into agreement from public.contract_agreements where id=v_contract_id and league_id=lid for update;
  if agreement.id is null or agreement.league_team_id<>from_id or agreement.status not in('active','scheduled') or agreement.superseded_by_contract_id is not null then raise exception 'trade_player_ownership_invalid';end if;
  insert into public.canonical_trade_asset_movements(trade_id,league_id,asset_type,contract_agreement_id,player_id,from_league_team_id,to_league_team_id,asset_snapshot)values(trade.id,lid,'player',agreement.id,agreement.player_id,from_id,to_id,to_jsonb(agreement)-'updated_at');
  update public.contract_agreements set league_team_id=to_id,updated_at=clock_timestamp() where id=agreement.id;
  update public.contract_seasons set league_team_id=to_id,updated_at=clock_timestamp() where contract_id=agreement.id and obligation_status in('active','scheduled');
  insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)values(agreement.id,lid,to_id,agreement.player_id,'traded',(select season from public.league_seasons where league_id=lid and is_active order by season desc limit 1),'canonical_trade',actor,jsonb_build_object('league_team_id',from_id),jsonb_build_object('league_team_id',to_id),jsonb_build_object('trade_id',trade.id),format('trade:%s:player:%s',trade.id,agreement.id));
  movement_count:=movement_count+1;
 end loop;
 for item in select value from jsonb_array_elements(picks)loop
  v_pick_id:=(item->>'draft_pick_id')::uuid;from_id:=(item->>'from_league_team_id')::uuid;to_id:=(item->>'to_league_team_id')::uuid;
  if from_id=to_id or not(participants ? from_id::text)or not(participants ? to_id::text)then raise exception 'trade_pick_participant_invalid';end if;
  select * into pick from public.draft_pick_assets where stable_pick_id=v_pick_id and league_id=lid for update;
  if pick.stable_pick_id is null or pick.current_owner_league_team_id<>from_id or pick.asset_status<>'tradable' then raise exception 'trade_pick_ownership_or_status_invalid';end if;
  if not exists(select 1 from public.league_draft_lifecycles d where d.league_id=lid and d.season=pick.draft_year and d.status in('scheduled','in_progress'))then raise exception 'trade_pick_lifecycle_not_tradeable';end if;
  insert into public.canonical_trade_asset_movements(trade_id,league_id,asset_type,draft_pick_id,from_league_team_id,to_league_team_id,asset_snapshot)values(trade.id,lid,'draft_pick',pick.stable_pick_id,from_id,to_id,to_jsonb(pick));
  update public.draft_pick_assets set current_owner_league_team_id=to_id,trade_provenance=trade_provenance||jsonb_build_array(jsonb_build_object('trade_id',trade.id,'from',from_id,'to',to_id,'at',clock_timestamp()))where stable_pick_id=pick.stable_pick_id;
  movement_count:=movement_count+1;
 end loop;
 for item in select value from jsonb_array_elements(retentions)loop
  v_contract_id:=(item->>'contract_id')::uuid;from_id:=(item->>'retaining_league_team_id')::uuid;to_id:=(item->>'receiving_league_team_id')::uuid;
  if jsonb_typeof(item->'seasons')<>'array' or jsonb_array_length(item->'seasons') not between 1 and 4 then raise exception 'retained_salary_horizon_invalid';end if;
  if not exists(select 1 from public.canonical_trade_asset_movements m where m.trade_id=trade.id and m.contract_agreement_id=v_contract_id and m.from_league_team_id=from_id and m.to_league_team_id=to_id)then raise exception 'retained_salary_player_movement_missing';end if;
  for season_item in select value from jsonb_array_elements(item->'seasons')loop
   yr:=(season_item->>'season')::int;amount:=(season_item->>'amount')::numeric;if amount<=0 then raise exception 'retained_salary_amount_invalid';end if;
   select * into obligation from public.contract_seasons where contract_id=v_contract_id and league_id=lid and season=yr and obligation_status in('active','scheduled') for update;
   if obligation.id is null then raise exception 'retained_salary_contract_season_invalid';end if;
   if coalesce((select sum(r.amount)from public.trade_retained_salary_obligations r where r.contract_season_id=obligation.id and r.status='active'),0)+amount>obligation.cap_hit then raise exception 'retained_salary_exceeds_contract_cap_hit';end if;
   insert into public.trade_retained_salary_obligations(league_id,trade_id,player_id,contract_agreement_id,contract_season_id,retaining_league_team_id,receiving_league_team_id_at_creation,season,amount,status,reason,source,created_by)values(lid,trade.id,obligation.player_id,v_contract_id,obligation.id,from_id,to_id,yr,amount,'active','trade_retained_salary','canonical_trade_rpc',actor);retained_count:=retained_count+1;
  end loop;
 end loop;
 if movement_count=0 then raise exception 'trade_has_no_assets';end if;
 return jsonb_build_object('trade_id',trade.id,'status','completed','idempotent',false,'participant_count',jsonb_array_length(participants),'movement_count',movement_count,'retained_obligation_count',retained_count);
end$$;



create or replace function public.execute_canonical_trade_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();
begin
 perform public.require_commissioner_authority((p_request->>'league_id')::uuid);
 return public.execute_canonical_trade_private(p_request,actor);
end$$;
revoke all on function public.execute_canonical_trade_private(jsonb,uuid) from public,anon,authenticated,service_role;
revoke all on function public.execute_canonical_trade_authenticated(jsonb) from public,anon,service_role;
grant execute on function public.execute_canonical_trade_authenticated(jsonb) to authenticated;

-- 2. Per-league switch: owner transactions stay off until the commissioner turns them on.
create table if not exists public.league_app_transactions(
 league_id uuid primary key references public.leagues(id),
 enabled boolean not null default false,
 updated_at timestamptz not null default clock_timestamp()
);
alter table public.league_app_transactions enable row level security;
revoke all on public.league_app_transactions from public,anon,authenticated;
grant select on public.league_app_transactions to authenticated;
grant select,insert,update on public.league_app_transactions to service_role;
create policy league_app_transactions_member_read on public.league_app_transactions for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=league_app_transactions.league_id and m.user_id=auth.uid()));

-- 3. Offers. players: [{contract_id, player_id, from_league_team_id, to_league_team_id}]; picks: [{draft_pick_id, from_league_team_id, to_league_team_id}].
create table if not exists public.trade_offers(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 from_league_team_id uuid not null references public.league_teams(id),
 to_league_team_id uuid not null references public.league_teams(id),
 status text not null default 'pending' check(status in('pending','accepted','declined','cancelled','countered','void')),
 players jsonb not null default '[]',
 picks jsonb not null default '[]',
 note text,
 parent_offer_id uuid references public.trade_offers(id),
 canonical_trade_id uuid references public.canonical_trades(id),
 void_reason text,
 created_by uuid not null references auth.users(id),
 created_at timestamptz not null default clock_timestamp(),
 decided_by uuid references auth.users(id),
 decided_at timestamptz,
 check(from_league_team_id<>to_league_team_id),
 check(jsonb_typeof(players)='array' and jsonb_typeof(picks)='array')
);
create index if not exists trade_offers_league_status_idx on public.trade_offers(league_id,status);
alter table public.trade_offers enable row level security;
revoke all on public.trade_offers from public,anon,authenticated;
grant select on public.trade_offers to authenticated;
grant select,insert,update on public.trade_offers to service_role;
-- Only the two teams in an offer can see it while it's open; once accepted it shows in league activity.
create policy trade_offers_party_read on public.trade_offers for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=trade_offers.league_id and m.user_id=auth.uid()
              and m.league_team_id in(trade_offers.from_league_team_id,trade_offers.to_league_team_id)));

-- 4. Helpers.
create or replace function public.my_league_team_private(p_league_id uuid) returns uuid
language sql stable security definer set search_path=pg_catalog,public as $$
 select m.league_team_id from public.league_memberships m where m.league_id=p_league_id and m.user_id=auth.uid() and m.league_team_id is not null limit 1
$$;

-- True once the player's NFL team has kicked off this week (weeks run Tuesday to Monday, Eastern time).
create or replace function public.player_locked_this_week_private(p_player_id text) returns boolean
language sql stable security definer set search_path=pg_catalog,public as $$
 with wk as (
  select (date_trunc('day',now() at time zone 'America/New_York')
          - make_interval(days=>((extract(isodow from now() at time zone 'America/New_York')::int-2+7)%7)))::timestamp as start_et
 )
 select exists(
  select 1 from public.nfl_players p join public.nfl_games g on p.team in(g.home_team,g.away_team), wk
  where p.sleeper_id=p_player_id and g.gameday is not null and g.gametime is not null
    and (g.gameday+g.gametime::time) >= wk.start_et
    and ((g.gameday+g.gametime::time) at time zone 'America/New_York') <= now()
 )
$$;

-- Checks every asset still belongs to the side giving it, nothing is locked, and builds the engine request.
create or replace function public.trade_offer_request_private(o public.trade_offers) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare item jsonb;agreement public.contract_agreements%rowtype;pick public.draft_pick_assets%rowtype;players jsonb:='[]';picks jsonb:='[]';
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
 if jsonb_array_length(players)+jsonb_array_length(picks)=0 then raise exception 'offer_empty';end if;
 if not exists(select 1 from jsonb_array_elements(players||picks) x where x->>'to_league_team_id'=o.to_league_team_id::text)
    or not exists(select 1 from jsonb_array_elements(players||picks) x where x->>'to_league_team_id'=o.from_league_team_id::text)
  then raise exception 'offer_one_sided';end if;
 return jsonb_build_object('league_id',o.league_id,'idempotency_key','offer:'||o.id,'participant_team_ids',jsonb_build_array(o.from_league_team_id,o.to_league_team_id),
                           'player_movements',players,'draft_pick_movements',picks,'notes','Accepted trade offer '||o.id);
end$$;

-- 5. Owner actions.
-- Propose (or counter, with parent_offer_id): {league_id, to_league_team_id, players, picks, note?, parent_offer_id?}
create or replace function public.propose_trade_offer_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;me uuid;parent public.trade_offers%rowtype;o public.trade_offers%rowtype;
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=lid),false) then raise exception 'transactions_not_enabled';end if;
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
 if nullif(p_request->>'parent_offer_id','') is not null then
  select * into parent from public.trade_offers where id=(p_request->>'parent_offer_id')::uuid and league_id=lid for update;
  if parent.id is null or parent.status<>'pending' or parent.to_league_team_id<>me then raise exception 'counter_not_allowed';end if;
  update public.trade_offers set status='countered',decided_by=actor,decided_at=clock_timestamp() where id=parent.id;
 end if;
 insert into public.trade_offers(league_id,from_league_team_id,to_league_team_id,players,picks,note,parent_offer_id,created_by)
 values(lid,me,(p_request->>'to_league_team_id')::uuid,coalesce(p_request->'players','[]'),coalesce(p_request->'picks','[]'),nullif(btrim(p_request->>'note'),''),parent.id,actor)
 returning * into o;
 if not exists(select 1 from public.league_teams t where t.id=o.to_league_team_id and t.league_id=lid) then raise exception 'offer_team_invalid';end if;
 perform public.trade_offer_request_private(o);  -- validates ownership, locks and both sides getting something
 return jsonb_build_object('offer_id',o.id,'status',o.status);
end$$;

-- Respond: {offer_id, action: accept | decline | cancel}
create or replace function public.respond_trade_offer_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();o public.trade_offers%rowtype;me uuid;act text:=p_request->>'action';result jsonb;
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
 result:=public.execute_canonical_trade_private(public.trade_offer_request_private(o),actor);
 update public.trade_offers set status='accepted',decided_by=actor,decided_at=clock_timestamp(),canonical_trade_id=(result->>'trade_id')::uuid where id=o.id;
 -- Other open offers that included any asset that just moved can't happen anymore.
 update public.trade_offers x set status='void',void_reason='assets_moved',decided_at=clock_timestamp()
  where x.league_id=o.league_id and x.status='pending' and x.id<>o.id and (
   exists(select 1 from jsonb_array_elements(x.players) a join jsonb_array_elements(o.players) b on a->>'contract_id'=b->>'contract_id')
   or exists(select 1 from jsonb_array_elements(x.picks) a join jsonb_array_elements(o.picks) b on a->>'draft_pick_id'=b->>'draft_pick_id'));
 return jsonb_build_object('offer_id',o.id,'status','accepted','trade_id',result->>'trade_id');
end$$;

revoke all on function public.my_league_team_private(uuid),public.player_locked_this_week_private(text),public.trade_offer_request_private(public.trade_offers) from public,anon,authenticated,service_role;
revoke all on function public.propose_trade_offer_authenticated(jsonb),public.respond_trade_offer_authenticated(jsonb) from public,anon,service_role;
grant execute on function public.propose_trade_offer_authenticated(jsonb),public.respond_trade_offer_authenticated(jsonb) to authenticated;

commit;
