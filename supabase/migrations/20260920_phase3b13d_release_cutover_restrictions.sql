begin;

insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,result_schema_version,execution_owner,mutation_class,metadata)
values('RELEASE_CUTOVER_RESTRICTIONS',35,1,'phase3b13d-cutover-release-input-v1','phase3b13d-cutover-release-result-v1','publication','finalization_domain',jsonb_build_object('phase','3B.13D','football_mutation',false,'cache_refresh',false,'ui_ai_refresh',false,'external_sync',false));

do $$declare d text;begin
 select pg_get_functiondef('public.validate_rollover_execution_transition()'::regprocedure) into d;
 d:=replace(d,'or (old.status=''failed_postcommit_validation'' and new.status=''validating'')','or (old.status=''failed_postcommit_validation'' and new.status=''validating'') or (old.status=''executed_unpublished'' and new.status=''completed'')');
 execute d;
end$$;

alter table public.rollover_executions drop constraint rollover_executed_unpublished_evidence_check;
alter table public.rollover_executions add constraint rollover_executed_unpublished_evidence_check check(
 (status='executed_unpublished' and executed_unpublished_at is not null and post_validation_report_id is not null and prepared_artifact_aggregate_hash is not null and prepared_publication_eligible is not null and prepared_publication_blocker_count is not null)
 or (status='completed' and ((executed_unpublished_at is null and post_validation_report_id is null and prepared_artifact_aggregate_hash is null and prepared_publication_eligible is null and prepared_publication_blocker_count is null) or (executed_unpublished_at is not null and post_validation_report_id is not null and prepared_artifact_aggregate_hash is not null and prepared_publication_eligible is not null and prepared_publication_blocker_count is not null)))
 or (status not in('executed_unpublished','completed') and executed_unpublished_at is null and post_validation_report_id is null and prepared_artifact_aggregate_hash is null and prepared_publication_eligible is null and prepared_publication_blocker_count is null)
);
alter table public.rollover_executions drop constraint rollover_executions_check6;
alter table public.rollover_executions add constraint rollover_executions_check6 check(
 completed_at is null or (committed_at is not null and validated_at is not null and completed_at>=validated_at) or (executed_unpublished_at is not null and completed_at>=executed_unpublished_at)
);

create table public.rollover_cutover_release_publications(
 id uuid primary key default gen_random_uuid(),
 rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),
 target_season_id uuid not null references public.league_seasons(id),
 season_publication_id uuid not null unique references public.rollover_target_season_authority_publications(id),
 cap_publication_id uuid not null unique references public.rollover_target_cap_authority_publications(id),
 market_publication_id uuid not null unique references public.rollover_target_market_visibility_publications(id),
 finalization_id uuid not null references public.rollover_executed_unpublished_finalizations(id),
 validation_report_id uuid not null references public.rollover_post_execution_validation_reports(id),
 cutover_lock_id uuid not null unique references public.rollover_execution_locks(id),
 season_publication_fingerprint text not null check(season_publication_fingerprint~'^[0-9a-f]{64}$'),
 cap_publication_fingerprint text not null check(cap_publication_fingerprint~'^[0-9a-f]{64}$'),
 market_publication_fingerprint text not null check(market_publication_fingerprint~'^[0-9a-f]{64}$'),
 finalization_fingerprint text not null check(finalization_fingerprint~'^[0-9a-f]{64}$'),
 validation_report_hash text not null check(validation_report_hash~'^[0-9a-f]{64}$'),
 lock_before_fingerprint text not null check(lock_before_fingerprint~'^[0-9a-f]{64}$'),
 lock_after_fingerprint text not null check(lock_after_fingerprint~'^[0-9a-f]{64}$'),
 release_fingerprint text not null check(release_fingerprint~'^[0-9a-f]{64}$'),
 idempotency_key text not null unique check(length(btrim(idempotency_key))>0),
 request_fingerprint text not null check(request_fingerprint~'^[0-9a-f]{64}$'),
 released_by uuid not null references auth.users(id),released_at timestamptz not null,
 release_reason text not null check(length(btrim(release_reason)) between 1 and 256),
 execution_status text not null check(execution_status='completed'),
 operation_index integer not null default 35 check(operation_index=35),
 operation_code text not null default 'RELEASE_CUTOVER_RESTRICTIONS' check(operation_code='RELEASE_CUTOVER_RESTRICTIONS'),
 schema_version text not null default 'phase3b13d-cutover-release-v1' check(schema_version='phase3b13d-cutover-release-v1')
);
alter table public.rollover_cutover_release_publications enable row level security;
revoke all on public.rollover_cutover_release_publications from public,anon,authenticated;
grant select,insert on public.rollover_cutover_release_publications to service_role;
grant select on public.rollover_cutover_release_publications to authenticated;
create policy cutover_release_member_read on public.rollover_cutover_release_publications for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_cutover_release_publications.league_id and m.user_id=auth.uid()));

create function public.guard_phase3b13d_cutover_release() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$begin raise exception 'cutover_release_evidence_immutable';end$$;
create trigger cutover_release_immutable before update or delete on public.rollover_cutover_release_publications for each row execute function public.guard_phase3b13d_cutover_release();

create function public.release_cutover_restrictions_phase3b13d_private(p_request jsonb,p_actor uuid) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;sp public.rollover_target_season_authority_publications%rowtype;cp public.rollover_target_cap_authority_publications%rowtype;mp public.rollover_target_market_visibility_publications%rowtype;f public.rollover_executed_unpublished_finalizations%rowtype;vr public.rollover_post_execution_validation_reports%rowtype;l public.rollover_execution_locks%rowtype;prior public.rollover_cutover_release_publications%rowtype;e public.rollover_cutover_release_publications%rowtype;k text:=nullif(btrim(p_request->>'idempotency_key'),'');v_reason text:=nullif(btrim(p_request->>'release_reason'),'');reqfp text;beforefp text;afterfp text;releasefp text;at timestamptz:=clock_timestamp();rowhash text;
begin
 if p_actor is null or k is null or v_reason is null or length(v_reason)>256 or nullif(p_request->>'rollover_execution_id','') is null then raise exception 'cutover_release_request_invalid';end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b13d:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;if x.id is null then raise exception 'cutover_release_execution_missing';end if;perform public.require_commissioner_authority(x.league_id);
 reqfp:=public.rollover_material_fingerprint(jsonb_build_object('operation','RELEASE_CUTOVER_RESTRICTIONS','execution',x.id,'league',x.league_id,'reason',v_reason,'assertions',p_request-'idempotency_key'-'release_reason','actor',p_actor));
 select * into prior from public.rollover_cutover_release_publications where rollover_execution_id=x.id for share;
 if found then
  if prior.idempotency_key<>k or prior.request_fingerprint<>reqfp then raise exception 'cutover_release_duplicate_conflict';end if;
  if x.status<>'completed' or not exists(select 1 from public.rollover_execution_locks q where q.id=prior.cutover_lock_id and q.status='released' and q.released_at=prior.released_at and q.released_by=prior.released_by) then raise exception 'cutover_release_replay_mismatch';end if;
  return jsonb_build_object('success',true,'idempotent',true,'operation_index',35,'operation_code','RELEASE_CUTOVER_RESTRICTIONS','release_publication_id',prior.id,'execution_id',x.id,'release_fingerprint',prior.release_fingerprint,'cutover_status','released','execution_status','completed','operation_36_eligible',true,'ui_ai_refreshed',false,'cache_refreshed',false,'external_sync_performed',false);
 end if;
 if x.status<>'executed_unpublished' then raise exception 'cutover_release_execution_state_invalid';end if;
 if nullif(p_request->>'season_publication_id','') is null or nullif(p_request->>'cap_publication_id','') is null or nullif(p_request->>'market_publication_id','') is null or nullif(p_request->>'expected_season_publication_fingerprint','') is null or nullif(p_request->>'expected_cap_publication_fingerprint','') is null or nullif(p_request->>'expected_market_publication_fingerprint','') is null then raise exception 'cutover_release_assertions_required';end if;
 select * into sp from public.rollover_target_season_authority_publications where id=(p_request->>'season_publication_id')::uuid and rollover_execution_id=x.id for share;if sp.id is null or sp.publication_fingerprint<>p_request->>'expected_season_publication_fingerprint' then raise exception 'cutover_release_season_publication_stale';end if;
 select * into cp from public.rollover_target_cap_authority_publications where id=(p_request->>'cap_publication_id')::uuid and rollover_execution_id=x.id and season_publication_id=sp.id for share;if cp.id is null or cp.publication_fingerprint<>p_request->>'expected_cap_publication_fingerprint' then raise exception 'cutover_release_cap_publication_stale';end if;
 select * into mp from public.rollover_target_market_visibility_publications where id=(p_request->>'market_publication_id')::uuid and rollover_execution_id=x.id and season_publication_id=sp.id and cap_publication_id=cp.id for share;if mp.id is null or mp.deterministic_fingerprint<>p_request->>'expected_market_publication_fingerprint' then raise exception 'cutover_release_market_publication_stale';end if;
 select * into f from public.rollover_executed_unpublished_finalizations where id=sp.finalization_id and rollover_execution_id=x.id for share;select * into vr from public.rollover_post_execution_validation_reports where id=f.validation_report_id and rollover_execution_id=x.id for share;
 if f.id is null or vr.id is null or f.finalization_status<>'executed_unpublished' or not f.publication_eligible or not vr.publication_eligible then raise exception 'cutover_release_not_publication_eligible';end if;
 if f.publication_blocker_count<>0 or vr.publication_blocker_count<>0 or vr.execution_failure_count<>0 or f.validation_report_hash<>vr.aggregate_validation_hash then raise exception 'cutover_release_publication_blocked';end if;
 if public.phase3b13a_prepared_artifact_hash(x.id)<>f.prepared_artifact_aggregate_hash or x.prepared_artifact_aggregate_hash<>f.prepared_artifact_aggregate_hash then raise exception 'cutover_release_hash_mismatch';end if;
 if cp.operation23_cap_hash<>(select aggregate_cap_set_hash from public.prepared_team_cap_sets where rollover_execution_id=x.id and status='published') or mp.prepared_free_agent_set_hash<>(select aggregate_set_hash from public.prepared_free_agent_eligibility_sets where rollover_execution_id=x.id) or mp.prepared_expiring_set_hash<>(select aggregate_set_hash from public.prepared_expiring_contract_sets where rollover_execution_id=x.id) then raise exception 'cutover_release_hash_mismatch';end if;
 rowhash:=public.rollover_material_fingerprint(jsonb_build_object('publication',mp.deterministic_fingerprint,'rows',coalesce((select jsonb_agg(r.deterministic_fingerprint order by r.market_type,r.player_id,r.source_prepared_row_id) from public.rollover_target_market_visibility_rows r where r.publication_id=mp.id),'[]')));
 if (select count(*) from public.rollover_target_market_visibility_rows r where r.publication_id=mp.id)<>mp.published_free_agent_count+mp.published_expiring_contract_count then raise exception 'cutover_release_market_publication_stale';end if;
 select * into l from public.rollover_execution_locks where id=sp.cutover_lock_id and rollover_execution_id=x.id and status='active' and lock_type='cutover' and lock_scope='rollover_global' for update;if l.id is null then raise exception 'cutover_release_lock_missing';end if;
 beforefp:=public.rollover_material_fingerprint(jsonb_build_object('id',l.id,'status','active','execution',x.id,'plan',l.execution_plan_id,'approval',l.approval_id,'acquired_at',l.acquired_at,'acquired_by',l.acquired_by));
 update public.rollover_execution_locks set status='released',released_at=at,released_by=p_actor,release_reason=v_reason where id=l.id;
 update public.rollover_executions set status='completed',completed_at=at,updated_at=at where id=x.id;
 afterfp:=public.rollover_material_fingerprint(jsonb_build_object('id',l.id,'status','released','execution',x.id,'released_at',at,'released_by',p_actor,'reason',v_reason));
 releasefp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b13d-cutover-release-v1','execution',x.id,'season',sp.publication_fingerprint,'cap',cp.publication_fingerprint,'market',mp.deterministic_fingerprint,'market_rows',rowhash,'finalization',f.deterministic_finalization_fingerprint,'validation',vr.aggregate_validation_hash,'lock_before',beforefp,'lock_after',afterfp));
 insert into public.rollover_cutover_release_publications(rollover_execution_id,league_id,target_season_id,season_publication_id,cap_publication_id,market_publication_id,finalization_id,validation_report_id,cutover_lock_id,season_publication_fingerprint,cap_publication_fingerprint,market_publication_fingerprint,finalization_fingerprint,validation_report_hash,lock_before_fingerprint,lock_after_fingerprint,release_fingerprint,idempotency_key,request_fingerprint,released_by,released_at,release_reason,execution_status) values(x.id,x.league_id,sp.target_season_id,sp.id,cp.id,mp.id,f.id,vr.id,l.id,sp.publication_fingerprint,cp.publication_fingerprint,mp.deterministic_fingerprint,f.deterministic_finalization_fingerprint,vr.aggregate_validation_hash,beforefp,afterfp,releasefp,k,reqfp,p_actor,at,v_reason,'completed') returning * into e;
 return jsonb_build_object('success',true,'idempotent',false,'operation_index',35,'operation_code','RELEASE_CUTOVER_RESTRICTIONS','release_publication_id',e.id,'execution_id',x.id,'release_fingerprint',releasefp,'cutover_status','released','execution_status','completed','operation_36_eligible',true,'ui_ai_refreshed',false,'cache_refreshed',false,'external_sync_performed',false,'football_domain_mutations',0);
end$$;

create function public.release_cutover_restrictions_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare actor uuid:=public.require_authenticated_user();begin if p_request?'actor_user_id' or p_request?'released_by' or p_request?'lock_status' or p_request?'execution_status' then raise exception 'actor or release material spoofing forbidden';end if;return public.release_cutover_restrictions_phase3b13d_private(p_request,actor);end$$;
revoke all on function public.guard_phase3b13d_cutover_release(),public.release_cutover_restrictions_phase3b13d_private(jsonb,uuid),public.release_cutover_restrictions_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.release_cutover_restrictions_authenticated(jsonb) to authenticated;
commit;
