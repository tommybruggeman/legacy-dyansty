begin;

do $$
declare lid uuid:='b03edc51-bec1-4064-9201-72e48ba413f9';yr integer:=2026;
 tid uuid;actor uuid;pid text;term integer;result jsonb;agreement_id uuid;
 prior_universe_count integer;after_universe_count integer;
begin
 select id into strict tid from public.league_teams where league_id=lid order by id limit 1;
 select user_id into strict actor from public.league_memberships
 where league_id=lid and lower(role)='commissioner' order by id limit 1;
 perform set_config('request.jwt.claim.sub',actor::text,true);

 foreach pid in array array['11581','4017'] loop
  if not exists(select 1 from public.sleeper_players where sleeper_player_id=pid
   and upper(position) in('QB','RB','WR','TE')) then
   raise exception 'named Sleeper acquisition fixture % is invalid',pid;
  end if;
  select count(*) into prior_universe_count from public.player_universe where sleeper_id=pid;
  term:=case pid when '11581' then 3 else 1 end;
  result:=public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,
   'salary',42,'years',term,'acquisition_type','fa_auction',
   'idempotency_key','disposable:sleeper-identity:'||pid));
  agreement_id:=(result->>'contract_agreement_id')::uuid;
  select count(*) into after_universe_count from public.player_universe where sleeper_id=pid;
  if after_universe_count<>1 or (prior_universe_count=1 and after_universe_count<>prior_universe_count)
   or (select count(*) from public.contract_seasons where contract_id=agreement_id)<>term
   or not public.player_has_live_contract_private(lid,pid,yr)
  then raise exception 'Sleeper-only acquisition failed for %',pid;end if;
  result:=public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,
   'salary',42,'years',term,'acquisition_type','fa_auction',
   'idempotency_key','disposable:sleeper-identity:'||pid));
  if coalesce((result->>'idempotent')::boolean,false) is not true
   or (select count(*) from public.contract_agreements where league_id=lid and player_id=pid and status in('active','scheduled'))<>1
  then raise exception 'Sleeper-only acquisition replay duplicated %',pid;end if;
 end loop;

 begin
  perform public.acquire_offseason_player_authenticated(jsonb_build_object(
   'league_id',lid,'league_team_id',tid,'player_id','does-not-exist-sleeper','season',yr,
   'salary',42,'years',1,'acquisition_type','fa_auction',
   'idempotency_key','disposable:sleeper-identity:invalid'));
  raise exception 'invalid Sleeper ID accepted';
 exception when others then if sqlerrm='invalid Sleeper ID accepted' then raise;end if;end;
 if exists(select 1 from public.player_universe where sleeper_id='does-not-exist-sleeper')
  or exists(select 1 from public.contract_events where idempotency_key='disposable:sleeper-identity:invalid')
 then raise exception 'invalid Sleeper acquisition left partial identity or event';end if;
end$$;

rollback;
