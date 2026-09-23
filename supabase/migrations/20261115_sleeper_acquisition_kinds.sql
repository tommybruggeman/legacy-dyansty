-- Allow Sleeper-sourced acquisition kinds.
--
-- acquire_offseason_player_private accepted only commissioner_manual_add,
-- fa_auction and rookie_draft. The Sleeper sync signs players with
-- sleeper_waiver and sleeper_free_agent so that contract provenance stays
-- honest -- an automated waiver pickup is not an in-app FA auction, and
-- collapsing them would make the contract_events history unable to tell the
-- two apart.
--
-- This is a verbatim copy of the existing function with those two values added
-- to the guard. Nothing else is changed.
begin;
set local search_path = pg_catalog, public;

create or replace function public.acquire_offseason_player_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'league_team_id')::uuid;
 pid text:=p_request->>'player_id';yr int:=(p_request->>'season')::int;term int:=(p_request->>'years')::int;
 amount numeric:=(p_request->>'salary')::numeric;kind text:=p_request->>'acquisition_type';ikey text:=p_request->>'idempotency_key';
 agreement_id uuid;season_row public.league_seasons%rowtype;active_season int;i int;required_count int;existing public.contract_events%rowtype;
 material jsonb;request_fp text;agreement_status text;first_obligation_status text;
begin
 if kind not in('commissioner_manual_add','fa_auction','rookie_draft','sleeper_waiver','sleeper_free_agent') or amount<0 or term<1 or nullif(ikey,'') is null then raise exception 'invalid offseason acquisition request';end if;
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
 select season into active_season from public.league_seasons where league_id=lid and is_active and status='active' for share;
 if active_season is null then raise exception 'acquisition season is not authorized by canonical season authority';end if;
 if public.player_has_live_contract_private(lid,pid,active_season) then raise exception 'player already has canonical active ownership';end if;
 if active_season is null or (kind<>'rookie_draft' and yr<>active_season) or (kind='rookie_draft' and yr not in(active_season,active_season+1)) then raise exception 'acquisition season is not authorized by canonical season authority';end if;
 if yr=active_season+1 and not exists(select 1 from public.league_seasons where league_id=lid and season=yr and status='scheduled' and not is_active) then raise exception 'upcoming rookie draft season is not canonical scheduled season';end if;
 select count(*) into required_count from public.league_seasons where league_id=lid and season between yr and yr+term-1
  and ((season=active_season and status='active' and is_active) or (season>active_season and status='scheduled' and not is_active));
 if required_count<>term then raise exception 'complete canonical contract season schedule required';end if;
 perform 1 from public.league_seasons where league_id=lid and season between yr and yr+term-1 order by season for share;
 agreement_status:=case when yr=active_season then 'active' else 'scheduled' end;
 first_obligation_status:=case when yr=active_season then 'active' else 'scheduled' end;
 insert into public.contract_agreements(league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,signed_season,start_season,end_season,status)
 values(lid,tid,pid,pid,case when kind='rookie_draft' then 'rookie' else 'veteran' end,
 case when kind='commissioner_manual_add' then 'commissioner_adjustment' else 'signed' end,yr,yr,yr+term-1,agreement_status) returning id into agreement_id;
 for i in 0..term-1 loop
  select * into season_row from public.league_seasons where league_id=lid and season=yr+i;
  insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,guaranteed_salary,cap_hit,dead_cap_if_released,obligation_status,source)
  values(agreement_id,season_row.id,lid,tid,pid,yr+i,amount,0,amount,amount,case when i=0 then first_obligation_status else 'scheduled' end,kind);
 end loop;
 insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,new_values,metadata,idempotency_key)
 values(agreement_id,lid,tid,pid,'signed',yr,kind,p_actor,jsonb_build_object('salary',amount,'years',term,'team',tid),jsonb_build_object('notes',coalesce(p_request->>'notes',''),'acquisition_type',kind,'request_fingerprint',request_fp,'request_material',material),ikey);
 return jsonb_build_object('idempotent',false,'contract_agreement_id',agreement_id,'league_team_id',tid,'player_id',pid);
end$$;

commit;
