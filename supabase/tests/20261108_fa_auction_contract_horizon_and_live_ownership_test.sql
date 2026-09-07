begin;

do $$
declare lid uuid;tid uuid;other_tid uuid;actor uuid;yr int;rules public.league_rules%rowtype;
 pids text[];term int;pid text;result jsonb;agreement_id uuid;old_agreement_id uuid;
 old_event_count int;old_dead_cap_count int;
begin
 select ls.league_id,ls.season into lid,yr from public.league_seasons ls where ls.is_active order by ls.created_at desc limit 1;
 select * into strict rules from public.league_rules where league_id=lid;
 select id into tid from public.league_teams where league_id=lid order by id limit 1;
 select id into other_tid from public.league_teams where league_id=lid and id<>tid order by id limit 1;
 select user_id into actor from public.league_memberships where league_id=lid and lower(role)='commissioner' order by id limit 1;
 select array_agg(sleeper_id order by sleeper_id) into pids from (
  select sleeper_id from public.player_universe p
  where not public.player_has_live_contract_private(lid,p.sleeper_id,yr)
  order by sleeper_id limit 6
 ) q;
 if cardinality(pids)<6 or tid is null or other_tid is null or actor is null then raise exception 'auction regression fixture is incomplete';end if;
 perform set_config('request.jwt.claim.sub',actor::text,true);

 for term in 1..rules.max_contract_years loop
  pid:=pids[term];
  result:=public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,'salary',100,'years',term,
   'acquisition_type','fa_auction','idempotency_key','disposable:auction-horizon:'||term));
  agreement_id:=(result->>'contract_agreement_id')::uuid;
  if (select count(*) from public.contract_seasons where contract_id=agreement_id)<>term
   or (select count(*) from public.contract_seasons where contract_id=agreement_id and season=yr and obligation_status='active')<>1
   or (select count(*) from public.contract_seasons where contract_id=agreement_id and season between yr+1 and yr+term-1 and obligation_status='scheduled')<>term-1
   or exists(select 1 from generate_series(yr,yr+term-1) y where not exists(
    select 1 from public.contract_seasons s where s.contract_id=agreement_id and s.season=y))
  then raise exception 'auction term % obligations are incorrect',term;end if;
  result:=public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,'salary',100,'years',term,
   'acquisition_type','fa_auction','idempotency_key','disposable:auction-horizon:'||term));
  if coalesce((result->>'idempotent')::boolean,false) is not true
   or (select count(*) from public.contract_agreements where league_id=lid and player_id=pid and status in('active','scheduled'))<>1
  then raise exception 'auction term % replay duplicated ownership',term;end if;
 end loop;

 begin
  perform public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id',pids[5],'season',yr,'salary',100,
   'years',rules.max_contract_years+1,'acquisition_type','fa_auction','idempotency_key','disposable:auction-too-long'));
  raise exception 'above-max auction term accepted';
 exception when others then if sqlerrm='above-max auction term accepted' then raise;end if;end;
 begin
  perform public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id',pids[5],'season',yr,'salary',rules.min_3_year_bid-1,
   'years',3,'acquisition_type','fa_auction','idempotency_key','disposable:auction-below-minimum'));
  raise exception 'below-minimum auction salary accepted';
 exception when others then if sqlerrm='below-minimum auction salary accepted' then raise;end if;end;
 if exists(select 1 from public.contract_agreements where league_id=lid and player_id=pids[5]) then raise exception 'invalid auction left partial agreement';end if;

 old_agreement_id:=(select id from public.contract_agreements where league_id=lid and player_id=pids[1] and league_team_id=tid);
 perform public.release_offseason_player_authenticated(jsonb_build_object(
  'league_id',lid,'league_team_id',tid,'player_id',pids[1],'season',yr,'dead_cap',4,
  'idempotency_key','disposable:auction-horizon:drop'));
 select count(*) into old_event_count from public.contract_events where contract_id=old_agreement_id;
 select count(*) into old_dead_cap_count from public.dead_cap_obligations where contract_agreement_id=old_agreement_id;
 if public.player_has_live_contract_private(lid,pids[1],yr) then raise exception 'released player remains live-owned';end if;
 result:=public.acquire_offseason_player_authenticated(jsonb_build_object(
  'league_id',lid,'league_team_id',other_tid,'player_id',pids[1],'season',yr,'salary',100,'years',2,
  'acquisition_type','fa_auction','idempotency_key','disposable:auction-horizon:reacquire'));
 if (select status from public.contract_agreements where id=old_agreement_id)<>'released'
  or (select count(*) from public.contract_events where contract_id=old_agreement_id)<>old_event_count
  or (select count(*) from public.dead_cap_obligations where contract_agreement_id=old_agreement_id)<>old_dead_cap_count
  or (select count(*) from public.contract_agreements where league_id=lid and player_id=pids[1] and status in('active','scheduled'))<>1
  or not exists(select 1 from public.contract_agreements where id=(result->>'contract_agreement_id')::uuid and league_team_id=other_tid)
  or exists(select 1 from public.contract_seasons where contract_id=old_agreement_id and obligation_status in('active','scheduled'))
 then raise exception 'drop history or cross-team reacquisition is incorrect';end if;
end$$;

rollback;
