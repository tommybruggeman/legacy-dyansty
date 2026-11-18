-- Three years of tradable draft picks + cap space traded across several seasons (2026-11-17).
--
-- Picks: every league keeps its next three drafts fully stocked (every team, every round), each with a
-- scheduled draft lifecycle so the picks can be traded. In season 2026 that is the 2027, 2028 and 2029 drafts.
-- It matches the rollover's four-class horizon (this year's draft + three ahead), so the June rollover only
-- appends the next year as before. Safe to run again: it only adds what is missing.
--
-- Cap: an offer's cap items can now carry a season, so a team can send e.g. $5 in 2026 and $3 in 2027.
-- Allowed seasons are this season and the next three. Items without a season mean this season (older app builds).
-- One amount per side per season. When accepted, each year posts its own cap_adjustments 'trade_carryover' rows
-- for that season, which the cap-by-year views already read.
begin;

-- ---- picks: keep the next three drafts stocked ---------------------------------------------------
create or replace function public.ensure_draft_pick_horizon_private(p_league_id uuid) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare season_now int;team_count int;y int;r int;t public.league_teams%rowtype;c public.draft_inventory_classes%rowtype;
        lifecycles int:=0;classes int:=0;picks int:=0;
begin
 select ls.season into season_now from public.league_seasons ls where ls.league_id=p_league_id and ls.is_active order by ls.season desc limit 1;
 select count(*) into team_count from public.league_teams where league_id=p_league_id;
 if season_now is null or team_count=0 then return jsonb_build_object('skipped',true);end if;
 if public.has_active_rollover_cutover_lock(p_league_id) then return jsonb_build_object('skipped',true,'reason','rollover');end if;
 perform pg_advisory_xact_lock(hashtextextended('draft-pick-horizon:'||p_league_id::text,0));
 for y in season_now+1..season_now+3 loop
  if exists(select 1 from public.league_draft_lifecycles where league_id=p_league_id and season=y and status='completed') then continue;end if;
  if not exists(select 1 from public.league_draft_lifecycles where league_id=p_league_id and season=y) then
   insert into public.league_draft_lifecycles(league_id,season,status,expected_pick_count) values(p_league_id,y,'scheduled',team_count*3);
   lifecycles:=lifecycles+1;
  end if;
  select * into c from public.draft_inventory_classes where league_id=p_league_id and draft_year=y;
  if c.id is null then
   insert into public.draft_inventory_classes(league_id,draft_year,horizon_status,publication_status,provenance,deterministic_class_hash)
   values(p_league_id,y,'visible_prepared','unpublished',jsonb_build_object('source','pick_horizon','evidence_id','pick-horizon-v1'),
          public.rollover_material_fingerprint(jsonb_build_object('schema','canonical-draft-inventory-class-v1','league_id',p_league_id,'season',y,'team_count',team_count,'round_count',3)))
   returning * into c;
   classes:=classes+1;
  end if;
  for t in select * from public.league_teams where league_id=p_league_id order by id loop
   for r in 1..3 loop
    if not exists(select 1 from public.draft_pick_assets a where a.league_id=p_league_id and a.draft_year=y and a.round_number=r and a.original_league_team_id=t.id) then
     insert into public.draft_pick_assets(stable_pick_id,class_id,league_id,draft_year,round_number,original_league_team_id,current_owner_league_team_id,asset_status,publication_status,generation_provenance,deterministic_asset_hash)
     values(public.phase3b9a_stable_pick_id(p_league_id,y,r,t.id),c.id,p_league_id,y,r,t.id,t.id,'tradable','unpublished',
            jsonb_build_object('source','pick_horizon','evidence_id','pick-horizon-v1'),
            public.rollover_material_fingerprint(jsonb_build_object('league',p_league_id,'year',y,'round',r,'original_team',t.id)));
     picks:=picks+1;
    end if;
   end loop;
  end loop;
 end loop;
 return jsonb_build_object('lifecycles_added',lifecycles,'classes_added',classes,'picks_added',picks);
end$$;

-- Every league at once (the sync job calls this; idempotent, a no-op once stocked).
create or replace function public.ensure_draft_pick_horizons() returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid;added int:=0;res jsonb;
begin
 for lid in select distinct league_id from public.league_seasons where is_active loop
  res:=public.ensure_draft_pick_horizon_private(lid);
  added:=added+coalesce((res->>'picks_added')::int,0);
 end loop;
 return jsonb_build_object('picks_added',added);
end$$;

revoke all on function public.ensure_draft_pick_horizon_private(uuid),public.ensure_draft_pick_horizons() from public,anon,authenticated;
grant execute on function public.ensure_draft_pick_horizons() to service_role;

-- ---- cap space across seasons ---------------------------------------------------------------------
create or replace function public.trade_offer_request_private(o public.trade_offers) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare item jsonb;agreement public.contract_agreements%rowtype;pick public.draft_pick_assets%rowtype;players jsonb:='[]';picks jsonb:='[]';cap jsonb:='[]';season_now int;yr int;
begin
 select ls.season into season_now from public.league_seasons ls where ls.league_id=o.league_id and ls.is_active order by ls.season desc limit 1;
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
 -- Cap space: one amount per side per season (this season or the next three), more than $0, in dollars and cents.
 for item in select value from jsonb_array_elements(o.cap) loop
  if (item->>'from_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or (item->>'to_league_team_id')::uuid not in(o.from_league_team_id,o.to_league_team_id)
     or item->>'from_league_team_id'=item->>'to_league_team_id' then raise exception 'offer_side_invalid';end if;
  if coalesce((item->>'amount')::numeric,0)<=0 or (item->>'amount')::numeric<>round((item->>'amount')::numeric,2) then raise exception 'offer_cap_amount_invalid';end if;
  yr:=coalesce(nullif(item->>'season','')::int,season_now);
  if season_now is null or yr not between season_now and season_now+3 then raise exception 'offer_cap_season_invalid';end if;
  cap:=cap||jsonb_build_object('from_league_team_id',item->>'from_league_team_id','to_league_team_id',item->>'to_league_team_id','amount',round((item->>'amount')::numeric,2),'season',yr);
 end loop;
 if (select count(distinct (x->>'from_league_team_id')||':'||(x->>'season')) from jsonb_array_elements(cap) x)<>jsonb_array_length(cap) then raise exception 'offer_cap_amount_invalid';end if;
 if jsonb_array_length(players)+jsonb_array_length(picks)=0 then raise exception 'offer_empty';end if;
 if not exists(select 1 from jsonb_array_elements(players||picks||cap) x where x->>'to_league_team_id'=o.to_league_team_id::text)
    or not exists(select 1 from jsonb_array_elements(players||picks||cap) x where x->>'to_league_team_id'=o.from_league_team_id::text)
  then raise exception 'offer_one_sided';end if;
 return jsonb_build_object('league_id',o.league_id,'idempotency_key','offer:'||o.id,'participant_team_ids',jsonb_build_array(o.from_league_team_id,o.to_league_team_id),
                           'player_movements',players,'draft_pick_movements',picks,'notes','Accepted trade offer '||o.id,'cap',cap);
end$$;

create or replace function public.respond_trade_offer_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();o public.trade_offers%rowtype;me uuid;act text:=p_request->>'action';result jsonb;req jsonb;item jsonb;yr int;sender text;receiver text;amt numeric;
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
 -- Cap space: charge the sender, credit the receiver, each in its own season.
 for item in select value from jsonb_array_elements(req->'cap') loop
  amt:=(item->>'amount')::numeric;yr:=(item->>'season')::int;
  select owner_name into sender from public.league_teams where id=(item->>'from_league_team_id')::uuid;
  select owner_name into receiver from public.league_teams where id=(item->>'to_league_team_id')::uuid;
  insert into public.cap_adjustments(league_id,owner_name,player_name,season,adjustment_type,amount,counterparty_owner,note)
  values(o.league_id,sender,null,yr,'trade_carryover',amt,receiver,format('Sends $%s of %s cap space to %s in app trade %s',amt,yr,receiver,result->>'trade_id')),
        (o.league_id,receiver,null,yr,'trade_carryover',-amt,sender,format('Receives $%s of %s cap space from %s in app trade %s',amt,yr,sender,result->>'trade_id'));
 end loop;
 update public.trade_offers set status='accepted',decided_by=actor,decided_at=clock_timestamp(),canonical_trade_id=(result->>'trade_id')::uuid where id=o.id;
 update public.trade_offers x set status='void',void_reason='assets_moved',decided_at=clock_timestamp()
  where x.league_id=o.league_id and x.status='pending' and x.id<>o.id and (
   exists(select 1 from jsonb_array_elements(x.players) a join jsonb_array_elements(o.players) b on a->>'contract_id'=b->>'contract_id')
   or exists(select 1 from jsonb_array_elements(x.picks) a join jsonb_array_elements(o.picks) b on a->>'draft_pick_id'=b->>'draft_pick_id'));
 return jsonb_build_object('offer_id',o.id,'status','accepted','trade_id',result->>'trade_id');
end$$;

revoke all on function public.trade_offer_request_private(public.trade_offers) from public,anon,authenticated,service_role;
revoke all on function public.respond_trade_offer_authenticated(jsonb) from public,anon,service_role;
grant execute on function public.respond_trade_offer_authenticated(jsonb) to authenticated;

-- Stock the next three drafts now.
select public.ensure_draft_pick_horizons();

commit;
