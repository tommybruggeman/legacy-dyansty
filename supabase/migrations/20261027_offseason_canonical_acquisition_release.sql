begin;

create or replace function public.acquire_offseason_player_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'league_team_id')::uuid;
 pid text:=p_request->>'player_id';yr int:=(p_request->>'season')::int;term int:=(p_request->>'years')::int;
 amount numeric:=(p_request->>'salary')::numeric;kind text:=p_request->>'acquisition_type';ikey text:=p_request->>'idempotency_key';
 agreement_id uuid;season_row public.league_seasons%rowtype;active_season int;i int;required_count int;existing public.contract_events%rowtype;
 material jsonb;request_fp text;agreement_status text;first_obligation_status text;
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
 if exists(select 1 from public.contract_agreements where league_id=lid and player_id=pid and status in('active','scheduled') and superseded_by_contract_id is null) then raise exception 'player already has canonical active ownership';end if;
 select season into active_season from public.league_seasons where league_id=lid and is_active and status='active' for share;
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

-- Incoming rookie contracts whose canonical start is the scheduled target
-- season are valid target-only authority. They are excluded from source-season
-- provenance cardinality, then activated by operation 10 before target-roster
-- preparation. No league-season publication occurs here.
create or replace function public.rollover_contract_preflight_readiness_private(p_league_id uuid,p_source_season integer,p_target_season integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare agreement_count int;source_count int;prepared_option_count int;option_eligible_count int;active_target_count int;prior_transition_count int;ordinary_count int;taxi_count int;unresolved_count int;incoming_count int;blockers jsonb:='[]';material jsonb;
begin
 select count(*) into incoming_count from public.contract_agreements a join public.contract_seasons s on s.contract_id=a.id and s.season=p_target_season join public.rookie_draft_board_assignments b on b.league_id=a.league_id and b.player_id=a.player_id and b.original_league_team_id=a.league_team_id and b.draft_year=p_target_season and b.rookie_contract_provenance where a.league_id=p_league_id and a.start_season=p_target_season and a.status='scheduled' and s.obligation_status='scheduled' and a.origin='signed' and a.contract_type='rookie';
 select count(*) into agreement_count from public.contract_agreements a where a.league_id=p_league_id and not exists(select 1 from public.contract_seasons s join public.rookie_draft_board_assignments b on b.league_id=a.league_id and b.player_id=a.player_id and b.original_league_team_id=a.league_team_id and b.draft_year=p_target_season and b.rookie_contract_provenance where s.contract_id=a.id and s.season=p_target_season and s.obligation_status='scheduled' and a.start_season=p_target_season and a.status='scheduled' and a.origin='signed' and a.contract_type='rookie');
 select count(*) into source_count from public.contract_seasons where league_id=p_league_id and season=p_source_season;
 select count(*) into prepared_option_count from public.contract_rollover_classifications c join public.contract_seasons s on s.contract_id=c.contract_agreement_id and s.season=p_target_season and s.obligation_status='scheduled' and s.is_option_year and s.option_type is not null where c.league_id=p_league_id and c.source_season=p_source_season and c.target_season=p_target_season and c.classification='rookie_option_eligible';
 select count(*) into option_eligible_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season and classification='rookie_option_eligible';
 select count(*) into ordinary_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season and classification='ordinary_expiration';
 select count(*) into taxi_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season and classification='rookie_initial_taxi_paused';
 select agreement_count-count(*) into unresolved_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season;
 select count(*) into active_target_count from public.contract_seasons where league_id=p_league_id and season=p_target_season and obligation_status='active';
 select count(*) into prior_transition_count from public.contract_transition_executions t where t.league_id=p_league_id and t.source_season=p_source_season and t.target_season=p_target_season and not exists(select 1 from public.contract_transition_reconciliations r where r.legacy_transition_id=t.id and r.reconciliation_status='certified');
 if p_target_season<>p_source_season+1 then blockers:=blockers||'"contract_season_boundary_invalid"';end if;if source_count<>agreement_count then blockers:=blockers||'"contract_source_obligation_population_mismatch"';end if;if prepared_option_count<>option_eligible_count then blockers:=blockers||'"prepared_target_option_population_mismatch"';end if;if active_target_count<>0 then blockers:=blockers||'"target_contract_authority_already_activated"';end if;if prior_transition_count<>0 then blockers:=blockers||'"prior_contract_transition_conflicts_with_rollover"';end if;if unresolved_count<>0 then blockers:=blockers||'"contract_provenance_unresolved"';end if;
 material:=jsonb_build_object('schema','rollover-contract-preflight-v3','league_id',p_league_id,'source_season',p_source_season,'target_season',p_target_season,'agreement_count',agreement_count,'incoming_target_rookie_count',incoming_count,'source_count',source_count,'prepared_option_count',prepared_option_count,'option_eligible_count',option_eligible_count,'ordinary_expiration_count',ordinary_count,'taxi_paused_count',taxi_count,'active_target_count',active_target_count,'prior_transition_count',prior_transition_count,'unresolved_provenance_count',unresolved_count,'blockers',blockers);
 return material||jsonb_build_object('ready',jsonb_array_length(blockers)=0,'deterministic_fingerprint',public.rollover_material_fingerprint(material));
end$$;

do $$declare d text;old_fragment text;new_fragment text;bad_query text;fixed_query text;begin
 select pg_get_functiondef('public.execute_rollover_typed_handler_phase3b7b_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure) into d;
 bad_query:='for continuing_case in select a.id contract_agreement_id from public.rollover_executions x join public.contract_agreements a on a.league_id=x.league_id and a.start_season=x.target_season and a.status=''scheduled'' join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.target_season and cs.obligation_status=''scheduled'' join public.rookie_draft_board_assignments b on b.league_id=a.league_id and b.player_id=a.player_id and b.original_league_team_id=a.league_team_id and b.draft_year=x.target_season and b.rookie_contract_provenance where x.id=p_rollover_execution_id order by a.id loop';
 fixed_query:='for continuing_case in select agr.id contract_agreement_id from public.rollover_executions x join public.contract_agreements agr on agr.league_id=x.league_id and agr.start_season=x.target_season and agr.status=''scheduled'' join public.contract_seasons cs on cs.contract_id=agr.id and cs.season=x.target_season and cs.obligation_status=''scheduled'' join public.rookie_draft_board_assignments b on b.league_id=agr.league_id and b.player_id=agr.player_id and b.original_league_team_id=agr.league_team_id and b.draft_year=x.target_season and b.rookie_contract_provenance where x.id=p_rollover_execution_id order by agr.id loop';
 if d like '%'||bad_query||'%' then execute replace(d,bad_query,fixed_query);return;end if;
 if d like '%phase3b7b_incoming_rookie%' then return;end if;
 old_fragment:=E' end loop;\n end if;\n result_hash:=';
 new_fragment:=E' end loop;\n  for continuing_case in select a.id contract_agreement_id from public.rollover_executions x join public.contract_agreements a on a.league_id=x.league_id and a.start_season=x.target_season and a.status=''scheduled'' join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.target_season and cs.obligation_status=''scheduled'' join public.rookie_draft_board_assignments b on b.league_id=a.league_id and b.player_id=a.player_id and b.original_league_team_id=a.league_team_id and b.draft_year=x.target_season and b.rookie_contract_provenance where x.id=p_rollover_execution_id order by a.id loop\n   select * into a from public.contract_agreements where id=continuing_case.contract_agreement_id for update;\n   select * into tgt from public.contract_seasons where contract_id=a.id and season=(select target_season from public.rollover_executions where id=p_rollover_execution_id) for update;\n   if a.status<>''scheduled'' or tgt.obligation_status<>''scheduled'' then perform public.raise_phase3b6c1_failure(''incoming_rookie_contract_authority_conflict'',''{}'');end if;\n   event_fp:=public.rollover_material_fingerprint(jsonb_build_object(''schema'',''phase3b7b-incoming-rookie-v1'',''execution'',p_rollover_execution_id,''agreement'',a.id,''target'',tgt.id));\n   update public.contract_agreements set status=''active'',updated_at=clock_timestamp() where id=a.id;\n   update public.contract_seasons set obligation_status=''active'',rollover_execution_id=p_rollover_execution_id,rollover_operation_code=code,rollover_final_outcome_id=null,rollover_evidence_hash=event_fp,updated_at=clock_timestamp() where id=tgt.id;\n   insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key) values(a.id,a.league_id,a.league_team_id,a.player_id,''season_obligation_advanced'',tgt.season,''phase3b7b_incoming_rookie'',p_actor,jsonb_build_object(''agreement_status'',''scheduled'',''obligation_status'',''scheduled''),jsonb_build_object(''agreement_status'',''active'',''obligation_status'',''active''),jsonb_build_object(''rollover_execution_id'',p_rollover_execution_id,''operation_code'',code,''target_contract_season_id'',tgt.id,''event_fingerprint'',event_fp,''rookie_draft_provenance'',true),format(''phase3b7b:%s:%s:incoming-rookie:%s'',p_rollover_execution_id,code,a.id));\n   continuing:=continuing+1;mutations:=mutations+2;events_written:=events_written+1;\n  end loop;\n end if;\n result_hash:=';
 new_fragment:=replace(new_fragment,bad_query,fixed_query);
 if d not like '%'||old_fragment||'%' then raise exception 'incoming rookie operation-10 patch point missing';end if;
 execute replace(d,old_fragment,new_fragment);
end$$;

do $$declare d text;old_fragment text;new_fragment text;owner_fragment text;owner_replacement text;begin
 select pg_get_functiondef('public.write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid)'::regprocedure) into d;
 owner_fragment:='if (select league_team_id from public.season_roster_assignments where id=r.source_assignment_id)<>r.league_team_id then';
 owner_replacement:='if r.source_assignment_id is not null and (select league_team_id from public.season_roster_assignments where id=r.source_assignment_id)<>r.league_team_id then';
 if d like '%'||owner_fragment||'%' then d:=replace(d,owner_fragment,owner_replacement);end if;
 if d like '%rookie_draft_board_assignments.original_league_team_id%' then execute d;return;end if;
 old_fragment:='if r.source_assignment_id is null and not exists(select 1 from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=r.player_id and ta.league_team_id=r.league_team_id) then';
 new_fragment:='if r.source_assignment_id is null and not exists(select 1 from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=r.player_id and ta.league_team_id=r.league_team_id) and not exists(select 1 from public.rookie_draft_board_assignments b where b.league_id=x.league_id and b.draft_year=x.target_season and b.player_id=r.player_id and b.original_league_team_id=r.league_team_id and b.rookie_contract_provenance) then';
 if d not like '%'||old_fragment||'%' then raise exception 'incoming rookie target-roster validation patch point missing';end if;
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:='''authorization_authority'',''league_memberships.league_team_id''';
 new_fragment:='''authorization_authority'',case when r.source_assignment_id is null then ''rookie_draft_board_assignments.original_league_team_id'' else ''league_memberships.league_team_id'' end';
 if d not like '%'||old_fragment||'%' then raise exception 'incoming rookie target-roster provenance patch point missing';end if;
 execute replace(d,old_fragment,new_fragment);
end$$;

do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.validate_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid)'::regprocedure) into d;
 if d like '%b.draft_year=x.target_season and b.player_id=a.player_id%' then return;end if;
 old_fragment:='or exists(select 1 from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=a.player_id and ta.league_team_id=a.league_team_id)))) then';
 new_fragment:='or exists(select 1 from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=a.player_id and ta.league_team_id=a.league_team_id) or exists(select 1 from public.rookie_draft_board_assignments b where b.league_id=x.league_id and b.draft_year=x.target_season and b.player_id=a.player_id and b.original_league_team_id=a.league_team_id and b.rookie_contract_provenance)))) then';
 if d not like '%'||old_fragment||'%' then raise exception 'incoming rookie target-roster validator patch point missing';end if;
 execute replace(d,old_fragment,new_fragment);
end$$;

alter table public.season_roster_assignments drop constraint if exists season_roster_target_metadata_complete;
alter table public.season_roster_assignments add constraint season_roster_target_metadata_complete check(
 (assignment_set_id is null and contract_agreement_id is null and target_contract_season_id is null
  and source_assignment_id is null and roster_status is null and provenance is null and deterministic_row_hash is null)
 or
 (assignment_set_id is not null and contract_agreement_id is not null and target_contract_season_id is not null
  and (source_assignment_id is not null or nullif(provenance->>'taxi_authority_id','') is not null
   or provenance->>'authorization_authority'='rookie_draft_board_assignments.original_league_team_id')
  and roster_status='pending_unpublished' and jsonb_typeof(provenance)='object'
  and deterministic_row_hash~'^[0-9a-f]{64}$')
);

create or replace function public.acquire_offseason_player_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;
begin
 perform public.require_commissioner_authority(lid);
 if p_request->>'acquisition_type'='rookie_draft' then raise exception 'rookie draft acquisitions require canonical draft-board persistence';end if;
 return public.acquire_offseason_player_private(p_request,actor);
end$$;

create or replace function public.release_offseason_player_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'league_team_id')::uuid;
 pid text:=p_request->>'player_id';yr int:=(p_request->>'season')::int;ikey text:=p_request->>'idempotency_key';dead numeric:=coalesce((p_request->>'dead_cap')::numeric,0);
 agreement public.contract_agreements%rowtype;existing public.contract_events%rowtype;release_event_id uuid;dead_event_id uuid;active_season int;
 material jsonb;request_fp text;
begin
 perform public.require_commissioner_authority(lid);perform public.assert_no_active_rollover_cutover_lock(lid);
 if dead<0 or nullif(ikey,'') is null then raise exception 'invalid offseason release request';end if;
 material:=jsonb_build_object('operation','offseason_release_v1','league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,'dead_cap',dead,'notes',coalesce(p_request->>'notes',''));
 request_fp:=public.rollover_material_fingerprint(material);
 perform pg_advisory_xact_lock(hashtextextended('offseason-event:'||ikey,0));
 select * into existing from public.contract_events where idempotency_key=ikey;
 if existing.id is not null then
  if existing.event_type<>'released' or existing.league_id<>lid or existing.league_team_id<>tid or existing.player_id<>pid
   or existing.source<>'commissioner_manual_drop' or existing.metadata->>'request_fingerprint' is distinct from request_fp then raise exception 'offseason idempotency key conflict';end if;
  return jsonb_build_object('idempotent',true,'contract_agreement_id',existing.contract_id,'player_id',pid,'dead_cap',dead);
 end if;
 select season into active_season from public.league_seasons where league_id=lid and is_active and status='active' for share;
 if active_season is null or yr<>active_season then raise exception 'release season is not canonical active season';end if;
 select * into agreement from public.contract_agreements where league_id=lid and league_team_id=tid and player_id=pid and status in('active','scheduled') and superseded_by_contract_id is null for update;
 if agreement.id is null then raise exception 'canonical active ownership not found for team';end if;
 update public.contract_agreements set status='released',updated_at=clock_timestamp() where id=agreement.id;
 perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
 update public.contract_seasons set obligation_status=case when season=yr and obligation_status='active' then 'released' else 'voided' end,updated_at=clock_timestamp()
  where contract_id=agreement.id and obligation_status in('active','scheduled');
 insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
 values(agreement.id,lid,tid,pid,'released',yr,'commissioner_manual_drop',actor,jsonb_build_object('status',agreement.status),jsonb_build_object('status','released','dead_cap',dead),jsonb_build_object('notes',coalesce(p_request->>'notes',''),'dead_cap',dead,'request_fingerprint',request_fp,'request_material',material),ikey) returning id into release_event_id;
 if dead>0 then
  insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,new_values,metadata,idempotency_key)
  values(agreement.id,lid,tid,pid,'dead_cap_created',yr,'commissioner_manual_drop',actor,jsonb_build_object('dead_cap_amount',dead),jsonb_build_object('dead_cap_amount',dead,'penalty_rule','commissioner_manual_drop','release_event_id',release_event_id,'request_fingerprint',request_fp),ikey||':dead-cap-event') returning id into dead_event_id;
  insert into public.dead_cap_obligations(league_id,league_team_id,player_id,contract_agreement_id,season,amount,source_event_id,termination_type,calculation_rule,status,created_by,metadata,idempotency_key)
  values(lid,tid,pid,agreement.id,yr,dead,dead_event_id,'commissioner_adjustment','commissioner_supplied_manual_drop','active',actor,jsonb_build_object('notes',coalesce(p_request->>'notes',''),'release_event_id',release_event_id),ikey||':dead-cap');
 end if;
 return jsonb_build_object('idempotent',false,'contract_agreement_id',agreement.id,'player_id',pid,'dead_cap',dead);
end$$;

-- Preserve board provenance and acquire each drafted player through the same
-- canonical contract authority used by every other offseason acquisition.
create or replace function public.persist_rookie_draft_board_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'draft_year')::int;p jsonb;written int:=0;board_id uuid;
 existing public.rookie_draft_board_assignments%rowtype;overall int;request_key text:=nullif(btrim(p_request->>'idempotency_key'),'');
begin
 perform public.require_commissioner_authority(lid);
 if request_key is null or jsonb_typeof(p_request->'picks')<>'array' then raise exception 'rookie board picks and idempotency key required';end if;
 for p in select value from jsonb_array_elements(p_request->'picks') loop
  if not exists(select 1 from public.league_teams where id=(p->>'league_team_id')::uuid and league_id=lid)
   or not exists(select 1 from public.player_universe u where u.sleeper_id=p->>'player_id'
    and coalesce(u.rookie_class_year,u.draft_year)=yr and upper(coalesce(u.pos,'')) in('QB','RB','WR','TE')
    and upper(coalesce(u.nfl_status,'')) not in('RETIRED','DECEASED')
    and (coalesce(u.draft_pick,0)>0 or upper(coalesce(u.nfl_status,''))='PROSPECT' or upper(coalesce(u.market_pool,''))='ROOKIE_PROSPECT'
     or nullif(btrim(u.nfl_team),'') is not null or (u.active is true and upper(coalesce(u.nfl_status,'')) not in('INACTIVE','PERMANENTLY_INACTIVE'))))
   then raise exception 'rookie board canonical identity or Rookie Class eligibility invalid';end if;
  overall:=((p->>'draft_round')::int-1)*10+(p->>'round_pick')::int;
  perform pg_advisory_xact_lock(hashtextextended('rookie-board:'||lid||':'||yr||':'||overall,0));
  select * into existing from public.rookie_draft_board_assignments where league_id=lid and draft_year=yr and overall_pick=overall;
  if existing.id is not null then
   if existing.player_id is distinct from p->>'player_id' or existing.original_league_team_id is distinct from (p->>'league_team_id')::uuid
    or existing.draft_round<>(p->>'draft_round')::int or existing.round_pick<>(p->>'round_pick')::int or not existing.rookie_contract_provenance
    or existing.original_salary<>(p->>'original_salary')::numeric or existing.original_contract_term<>(p->>'original_contract_term')::int
    or existing.one_time_option_salary<>(p->>'one_time_option_salary')::numeric or existing.one_time_option_term<>1 then raise exception 'rookie draft assignment conflict';end if;
   board_id:=existing.id;
  else
   if exists(select 1 from public.rookie_draft_board_assignments where league_id=lid and draft_year=yr and player_id=p->>'player_id') then raise exception 'rookie draft player already assigned to another pick';end if;
   insert into public.rookie_draft_board_assignments(league_id,player_id,original_league_team_id,draft_year,draft_round,round_pick,overall_pick,rookie_contract_provenance,original_salary,original_contract_term,one_time_option_salary,one_time_option_term,option_consumed,source_type,source_event,deterministic_fingerprint)
   values(lid,p->>'player_id',(p->>'league_team_id')::uuid,yr,(p->>'draft_round')::int,(p->>'round_pick')::int,overall,true,(p->>'original_salary')::numeric,(p->>'original_contract_term')::int,(p->>'one_time_option_salary')::numeric,1,false,'rookie_draft_board_assignment',jsonb_build_object('actor',actor,'request_id',request_key),public.rollover_material_fingerprint(jsonb_build_object('league',lid,'year',yr,'round',p->>'draft_round','pick',p->>'round_pick','player',p->>'player_id','team',p->>'league_team_id','salary',p->>'original_salary','term',p->>'original_contract_term','option_salary',p->>'one_time_option_salary','option_term',1))) returning id into board_id;
  end if;
  perform public.acquire_offseason_player_private(jsonb_build_object('league_id',lid,'player_id',p->>'player_id','league_team_id',p->>'league_team_id','season',yr,'salary',p->>'original_salary','years',p->>'original_contract_term','acquisition_type','rookie_draft','idempotency_key',(p_request->>'idempotency_key')||':'||(p->>'draft_round')||':'||(p->>'round_pick'),'notes','rookie draft board assignment '||board_id),actor);
  written:=written+1;
 end loop;
 return jsonb_build_object('rows_written',written,'draft_year',yr);
end$$;

revoke all on function public.acquire_offseason_player_private(jsonb,uuid),public.acquire_offseason_player_authenticated(jsonb),public.release_offseason_player_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.acquire_offseason_player_authenticated(jsonb),public.release_offseason_player_authenticated(jsonb) to authenticated;
revoke all on function public.persist_rookie_draft_board_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.persist_rookie_draft_board_authenticated(jsonb) to authenticated;

revoke all on function public.rollover_contract_preflight_readiness_private(uuid,integer,integer)
from public,anon,authenticated,service_role;

commit;
