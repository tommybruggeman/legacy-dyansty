begin;

create or replace function public.player_has_live_contract_private(
 p_league_id uuid,p_player_id text,p_active_season integer)
returns boolean language sql stable security definer set search_path=pg_catalog,public as $$
 select exists(
  select 1
  from public.contract_agreements a
  where a.league_id=p_league_id and a.player_id=p_player_id
   and a.status in('active','scheduled') and a.superseded_by_contract_id is null
   and exists(
    select 1 from public.contract_seasons s
    where s.contract_id=a.id and s.season>=p_active_season
     and s.obligation_status in('active','scheduled')
   )
 )
$$;

-- Materialize the legal contract horizon without replacing any existing
-- season identity. Each insert is replay-safe and preserves the predecessor
-- chain used by canonical season authority.
do $$
declare active_row public.league_seasons%rowtype;target_year integer;
 prior_season_id uuid;maximum_term integer;
begin
 for active_row in
  select * from public.league_seasons where is_active order by league_id
 loop
  select max_contract_years into strict maximum_term
  from public.league_rules where league_id=active_row.league_id;
  if maximum_term<1 then raise exception 'league contract horizon is invalid';end if;
  for target_year in active_row.season+1..active_row.season+maximum_term-1 loop
   select id into prior_season_id from public.league_seasons
   where league_id=active_row.league_id and season=target_year-1;
   if prior_season_id is null then raise exception 'complete canonical contract season predecessor required';end if;
   insert into public.league_seasons(league_id,season,is_active,status,previous_league_season_id)
   values(active_row.league_id,target_year,false,'scheduled',prior_season_id)
   on conflict(league_id,season) do nothing;
  end loop;
 end loop;
end$$;

create or replace function public.acquire_offseason_player_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'league_team_id')::uuid;
 pid text:=p_request->>'player_id';yr int:=(p_request->>'season')::int;term int:=(p_request->>'years')::int;
 amount numeric:=(p_request->>'salary')::numeric;kind text:=p_request->>'acquisition_type';ikey text:=p_request->>'idempotency_key';
 agreement_id uuid;season_row public.league_seasons%rowtype;active_season int;i int;existing public.contract_events%rowtype;
 prior_season_id uuid;material jsonb;request_fp text;agreement_status text;first_obligation_status text;
begin
 if kind not in('commissioner_manual_add','fa_auction','rookie_draft') or amount<0 or term<1 or nullif(ikey,'') is null then raise exception 'invalid offseason acquisition request';end if;
 material:=jsonb_build_object('operation','offseason_acquisition_v1','league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,'salary',amount,'years',term,'acquisition_type',kind,'notes',coalesce(p_request->>'notes',''));
 request_fp:=public.rollover_material_fingerprint(material);
 perform pg_advisory_xact_lock(hashtextextended('offseason-event:'||ikey,0));
 select * into existing from public.contract_events where idempotency_key=ikey;
 if existing.id is not null then
  if existing.event_type<>'signed' or existing.league_id<>lid or existing.league_team_id<>tid or existing.player_id<>pid
   or existing.source<>kind or existing.metadata->>'request_fingerprint' is distinct from request_fp then raise exception 'offseason idempotency key conflict';end if;
  return jsonb_build_object('idempotent',true,'contract_agreement_id',existing.contract_id,'league_team_id',tid,'player_id',pid);
 end if;
 perform public.assert_no_active_rollover_cutover_lock(lid);
 perform pg_advisory_xact_lock(hashtextextended('offseason-player:'||lid||':'||pid,0));
 if not exists(select 1 from public.league_teams where id=tid and league_id=lid) or not exists(select 1 from public.player_universe where sleeper_id=pid) then raise exception 'canonical acquisition identity invalid';end if;
 select season into active_season from public.league_seasons where league_id=lid and is_active for share;
 if active_season is null or (kind<>'rookie_draft' and yr<>active_season) or (kind='rookie_draft' and yr not in(active_season,active_season+1)) then raise exception 'acquisition season is not authorized by canonical season authority';end if;
 if kind='fa_auction' then perform public.resolve_offseason_contract_terms_private(lid,'fa_auction',amount,term);end if;
 if public.player_has_live_contract_private(lid,pid,active_season) then raise exception 'player already has canonical active ownership';end if;
 if yr=active_season+1 and not exists(select 1 from public.league_seasons where league_id=lid and season=yr and status='scheduled' and not is_active) then raise exception 'upcoming rookie draft season is not canonical scheduled season';end if;

 -- A legal contract defines future financial obligations. Provision the missing
 -- scheduled season identities atomically so the FK can represent the full term.
 for i in active_season+1..yr+term-1 loop
  select id into prior_season_id from public.league_seasons where league_id=lid and season=i-1;
  if prior_season_id is null then raise exception 'complete canonical contract season predecessor required';end if;
  insert into public.league_seasons(league_id,season,is_active,status,previous_league_season_id)
  values(lid,i,false,'scheduled',prior_season_id)
  on conflict(league_id,season) do nothing;
 end loop;
 perform 1 from public.league_seasons where league_id=lid and season between yr and yr+term-1
  and ((season=active_season and is_active) or (season>active_season and status='scheduled' and not is_active))
  order by season for share;
 if not found or (select count(*) from public.league_seasons where league_id=lid and season between yr and yr+term-1
  and ((season=active_season and is_active) or (season>active_season and status='scheduled' and not is_active)))<>term
 then raise exception 'complete canonical contract season schedule required';end if;
 agreement_status:=case when yr=active_season then 'active' else 'scheduled' end;
 first_obligation_status:=case when yr=active_season then 'active' else 'scheduled' end;
 insert into public.contract_agreements(league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,signed_season,start_season,end_season,status)
 values(lid,tid,pid,pid,case when kind='rookie_draft' then 'rookie' else 'veteran' end,
 case when kind='commissioner_manual_add' then 'commissioner_adjustment' else 'signed' end,yr,yr,yr+term-1,agreement_status) returning id into agreement_id;
 for i in 0..term-1 loop
  select * into strict season_row from public.league_seasons where league_id=lid and season=yr+i;
  insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,guaranteed_salary,cap_hit,dead_cap_if_released,obligation_status,source)
  values(agreement_id,season_row.id,lid,tid,pid,yr+i,amount,0,amount,amount,case when i=0 then first_obligation_status else 'scheduled' end,kind);
 end loop;
 insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,new_values,metadata,idempotency_key)
 values(agreement_id,lid,tid,pid,'signed',yr,kind,p_actor,jsonb_build_object('salary',amount,'years',term,'team',tid),jsonb_build_object('notes',coalesce(p_request->>'notes',''),'acquisition_type',kind,'request_fingerprint',request_fp,'request_material',material),ikey);
 return jsonb_build_object('idempotent',false,'contract_agreement_id',agreement_id,'league_team_id',tid,'player_id',pid);
end$$;

revoke all on function public.player_has_live_contract_private(uuid,text,integer),
 public.acquire_offseason_player_private(jsonb,uuid) from public,anon,authenticated,service_role;

commit;
