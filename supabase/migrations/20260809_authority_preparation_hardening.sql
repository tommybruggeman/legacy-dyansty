begin;

do $$ begin
  if exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_authority_preparations' and column_name='status')
     and not exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_authority_preparations' and column_name='authority_status') then
    alter table public.rollover_authority_preparations rename column status to authority_status;
  end if;
end $$;
alter table public.rollover_authority_preparations add column if not exists approved_by uuid references auth.users(id) on delete restrict;
alter table public.rollover_authority_preparations add column if not exists approved_at timestamptz;
alter table public.rollover_authority_preparations add column if not exists activated_at timestamptz;

drop index if exists public.rollover_authority_preparations_one_current;
alter table public.rollover_authority_preparations drop constraint if exists rollover_authority_preparations_status_check;
alter table public.rollover_authority_preparations drop constraint if exists authority_preparation_supersession_shape;
alter table public.rollover_authority_preparations drop constraint if exists authority_preparation_cancel_shape;
alter table public.rollover_authority_preparations drop constraint if exists authority_preparation_status_shape;
alter table public.rollover_authority_preparations add constraint rollover_authority_preparations_status_check
  check(authority_status in ('uninitialized','preparation_required','prepared','blocked','approved_for_execution','active','superseded','cancelled'));
alter table public.rollover_authority_preparations add constraint authority_preparation_status_shape check(
  (authority_status not in ('prepared','blocked') or (approved_by is null and approved_at is null and activated_at is null and superseded_at is null and superseded_by is null and cancelled_at is null)) and
  (authority_status<>'approved_for_execution' or (approved_by is not null and approved_at is not null and activated_at is null and superseded_at is null and superseded_by is null and cancelled_at is null)) and
  (authority_status<>'active' or (approved_by is not null and approved_at is not null and activated_at is not null and superseded_at is null and superseded_by is null and cancelled_at is null)) and
  ((authority_status='superseded')=(superseded_at is not null and superseded_by is not null)) and
  ((authority_status='cancelled')=(cancelled_at is not null))
);
create unique index rollover_authority_preparations_one_current
  on public.rollover_authority_preparations(rollover_execution_id,authority_type)
  where authority_status in ('prepared','blocked','approved_for_execution','active');

alter table public.rollover_authority_preparations drop constraint if exists rollover_authority_preparations_superseded_by_fkey;
alter table public.rollover_authority_preparations add constraint rollover_authority_preparations_superseded_by_fkey
 foreign key(superseded_by) references public.rollover_authority_preparations(id) on delete restrict deferrable initially deferred;

create or replace function public.enforce_rollover_authority_preparation_immutability()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if old.authority_status in ('active','superseded','cancelled') then raise exception 'terminal authority preparation is immutable'; end if;
 if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id
 or new.source_season<>old.source_season or new.target_season<>old.target_season or new.authority_type<>old.authority_type
 or new.version<>old.version or new.policy_id<>old.policy_id or new.policy_fingerprint<>old.policy_fingerprint
 or new.owner_population_fingerprint<>old.owner_population_fingerprint
 or new.commissioner_population_fingerprint<>old.commissioner_population_fingerprint
 or new.evidence_fingerprint<>old.evidence_fingerprint or new.authority_fingerprint<>old.authority_fingerprint
 or new.preparation_fingerprint<>old.preparation_fingerprint or new.preparation_payload<>old.preparation_payload
 or new.blockers<>old.blockers or new.warnings<>old.warnings or new.prepared_by<>old.prepared_by
 or new.prepared_at<>old.prepared_at or new.metadata<>old.metadata or new.created_at<>old.created_at then
   raise exception 'authority preparation material is immutable';
 end if;
 if old.authority_status in ('prepared','blocked') and new.authority_status not in ('approved_for_execution','superseded','cancelled') then raise exception 'illegal authority preparation transition'; end if;
 if old.authority_status='approved_for_execution' and new.authority_status not in ('superseded','cancelled','active') then raise exception 'illegal approved authority transition'; end if;
 -- Activation is deliberately impossible through the three Phase 3B.5E.1 RPCs.
 return new;
end $$;

create or replace function public.prepare_rollover_authorities_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; x public.rollover_executions%rowtype; p public.league_rollover_policies%rowtype;
 k text; material jsonb; fp text; retry jsonb; result jsonb; domain jsonb; inserted jsonb:='[]'::jsonb;
 supplied jsonb; supplied_types text[]; owner_count integer; review_count integer; review_fp text;
begin
 actor:=public.require_authenticated_user();
 if p_request ? 'actor_user_id' or p_request ? 'requested_by' then raise exception 'actor spoofing forbidden'; end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),''); if k is null then raise exception 'idempotency_key required'; end if;
 select * into x from public.rollover_executions where id=(p_request->>'execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found'; end if; perform public.require_commissioner_authority(x.league_id);
 if x.league_id::text is distinct from p_request->>'league_id' or x.source_season<>(p_request->>'source_season')::integer
 or x.target_season<>(p_request->>'target_season')::integer then raise exception 'execution boundary drift'; end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'decision_window_closed' then raise exception 'execution state not eligible for preparation'; end if;
 select * into p from public.league_rollover_policies where id=x.policy_id for share;
 if p.id is null or p.status<>'approved' or p.effective_at is not null or p.fingerprint is distinct from p_request->>'expected_policy_fingerprint'
 or x.policy_fingerprint is distinct from p.fingerprint then raise exception 'stale policy fingerprint'; end if;
 if x.decision_population_fingerprint is distinct from p_request->>'expected_owner_population_fingerprint' then raise exception 'stale owner population fingerprint'; end if;
 select count(*) into owner_count from public.rollover_owner_decisions d where d.rollover_execution_id=x.id;
 if owner_count=0 or exists(select 1 from public.rollover_owner_decisions d where d.rollover_execution_id=x.id and d.decision_status not in ('planned_retention','planned_release','commissioner_review_requested','no_response','execution_ready')) then raise exception 'unresolved owner decisions'; end if;
 select count(*),min(r.metadata->>'population_fingerprint') into review_count,review_fp from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id;
 if review_count=0 or exists(select 1 from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id and r.review_state not in ('approved','rejected')) then raise exception 'unresolved commissioner reviews'; end if;
 if review_fp is distinct from p_request->>'expected_commissioner_population_fingerprint'
 or exists(select 1 from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id and r.metadata->>'population_fingerprint' is distinct from review_fp) then raise exception 'stale commissioner population fingerprint'; end if;
 supplied:=p_request->'authority_preparations'; if jsonb_typeof(supplied)<>'array' or jsonb_array_length(supplied)<>3 then raise exception 'exactly three authority domains required'; end if;
 select array_agg(value->>'authority_type' order by value->>'authority_type') into supplied_types from jsonb_array_elements(supplied);
 if supplied_types<>array['dead_cap','publication','salary_cap']::text[] then raise exception 'authority type count drift'; end if;
 for domain in select value from jsonb_array_elements(supplied) loop
   if domain->>'evidence_fingerprint' is distinct from p_request->>(case domain->>'authority_type' when 'publication' then 'expected_publication_evidence_fingerprint' when 'dead_cap' then 'expected_dead_cap_evidence_fingerprint' else 'expected_cap_evidence_fingerprint' end)
   or domain->>'preparation_fingerprint' is distinct from p_request->>'expected_aggregate_preparation_fingerprint'
   or domain->>'authority_fingerprint' !~ '^[0-9a-f]{64}$' then raise exception 'stale domain evidence or aggregate preparation fingerprint'; end if;
 end loop;
 material:=jsonb_build_object('operation','authority_preparation_prepare','execution_id',x.id,'league_id',x.league_id,'source_season',x.source_season,'target_season',x.target_season,'expected_execution_status',p_request->>'expected_execution_status','authority_preparations',supplied,'policy_id',p.id,'expected_policy_fingerprint',p_request->>'expected_policy_fingerprint','expected_owner_population_fingerprint',p_request->>'expected_owner_population_fingerprint','expected_commissioner_population_fingerprint',p_request->>'expected_commissioner_population_fingerprint','expected_publication_evidence_fingerprint',p_request->>'expected_publication_evidence_fingerprint','expected_dead_cap_evidence_fingerprint',p_request->>'expected_dead_cap_evidence_fingerprint','expected_cap_evidence_fingerprint',p_request->>'expected_cap_evidence_fingerprint','expected_aggregate_preparation_fingerprint',p_request->>'expected_aggregate_preparation_fingerprint','material_metadata',coalesce(p_request->'material_metadata','{}'),'actor',actor);
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'authority_preparation_prepare',k,fp); if retry is not null then return retry; end if;
 perform 1 from public.rollover_authority_preparations a where a.rollover_execution_id=x.id for update;
 if exists(select 1 from public.rollover_authority_preparations a where a.rollover_execution_id=x.id and a.authority_status in ('prepared','blocked','approved_for_execution','active')) then raise exception 'conflicting current authority preparation'; end if;
 for domain in select value from jsonb_array_elements(supplied) loop
   insert into public.rollover_authority_preparations(rollover_execution_id,league_id,source_season,target_season,authority_type,authority_status,version,policy_id,policy_fingerprint,owner_population_fingerprint,commissioner_population_fingerprint,evidence_fingerprint,authority_fingerprint,preparation_fingerprint,preparation_payload,blockers,warnings,prepared_by,metadata)
   values(x.id,x.league_id,x.source_season,x.target_season,domain->>'authority_type',case when jsonb_array_length(coalesce(domain->'blockers','[]'))=0 then 'prepared' else 'blocked' end,coalesce((domain->>'version')::integer,1),p.id,p.fingerprint,p_request->>'expected_owner_population_fingerprint',p_request->>'expected_commissioner_population_fingerprint',domain->>'evidence_fingerprint',domain->>'authority_fingerprint',domain->>'preparation_fingerprint',domain->'preparation_payload',coalesce(domain->'blockers','[]'),coalesce(domain->'warnings','[]'),actor,coalesce(domain->'metadata','{}'));
 end loop;
 select coalesce(jsonb_agg(to_jsonb(a) order by a.authority_type),'[]') into inserted from public.rollover_authority_preparations a where a.rollover_execution_id=x.id and a.authority_status in ('prepared','blocked');
 if jsonb_array_length(inserted)<>3 then raise exception 'partial authority preparation forbidden'; end if;
 result:=jsonb_build_object('operation','authority_preparation_prepare','execution_id',x.id,'preparations',inserted,'preparation_fingerprint',p_request->>'expected_aggregate_preparation_fingerprint');
 return public.record_rollover_operation(x.league_id,x.id,'authority_preparation_prepare',k,fp,actor,'authenticated_commissioner',x.id,result,coalesce(p_request->'material_metadata','{}'));
end $$;

create or replace function public.supersede_rollover_authority_preparation_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; oldrow public.rollover_authority_preparations%rowtype; newrow public.rollover_authority_preparations%rowtype; x public.rollover_executions%rowtype; k text; material jsonb; fp text; retry jsonb; result jsonb; newid uuid:=gen_random_uuid();
begin
 actor:=public.require_authenticated_user(); if p_request ? 'actor_user_id' or p_request ? 'requested_by' then raise exception 'actor spoofing forbidden'; end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),''); if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'reason and idempotency_key required'; end if;
 select * into oldrow from public.rollover_authority_preparations where id=(p_request->>'preparation_id')::uuid for update; if oldrow.id is null then raise exception 'preparation not found'; end if;
 select * into x from public.rollover_executions where id=oldrow.rollover_execution_id for update; perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','authority_preparation_supersede','execution_id',x.id,'preparation_id',oldrow.id,'expected_current_version',(p_request->>'expected_current_version')::integer,'expected_current_authority_status',p_request->>'expected_current_authority_status','expected_current_authority_fingerprint',p_request->>'expected_current_authority_fingerprint','expected_current_preparation_fingerprint',p_request->>'expected_current_preparation_fingerprint','replacement',p_request->'replacement','reason',p_request->>'reason','material_metadata',coalesce(p_request->'material_metadata','{}'),'actor',actor);
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'authority_preparation_supersede',k,fp); if retry is not null then return retry; end if;
 if oldrow.authority_status not in ('prepared','blocked','approved_for_execution') or oldrow.version<>(p_request->>'expected_current_version')::integer or oldrow.authority_status is distinct from p_request->>'expected_current_authority_status' or oldrow.authority_fingerprint is distinct from p_request->>'expected_current_authority_fingerprint' or oldrow.preparation_fingerprint is distinct from p_request->>'expected_current_preparation_fingerprint' then raise exception 'stale authority preparation'; end if;
 if exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.status='active') then raise exception 'locked execution cannot supersede authority'; end if;
 update public.rollover_authority_preparations set authority_status='superseded',superseded_at=clock_timestamp(),superseded_by=newid where id=oldrow.id;
 insert into public.rollover_authority_preparations(id,rollover_execution_id,league_id,source_season,target_season,authority_type,authority_status,version,policy_id,policy_fingerprint,owner_population_fingerprint,commissioner_population_fingerprint,evidence_fingerprint,authority_fingerprint,preparation_fingerprint,preparation_payload,blockers,warnings,prepared_by,metadata)
 values(newid,oldrow.rollover_execution_id,oldrow.league_id,oldrow.source_season,oldrow.target_season,oldrow.authority_type,case when jsonb_array_length(coalesce(p_request->'replacement'->'blockers','[]'))=0 then 'prepared' else 'blocked' end,oldrow.version+1,oldrow.policy_id,oldrow.policy_fingerprint,oldrow.owner_population_fingerprint,oldrow.commissioner_population_fingerprint,p_request->'replacement'->>'evidence_fingerprint',p_request->'replacement'->>'authority_fingerprint',p_request->'replacement'->>'preparation_fingerprint',p_request->'replacement'->'preparation_payload',coalesce(p_request->'replacement'->'blockers','[]'),coalesce(p_request->'replacement'->'warnings','[]'),actor,coalesce(p_request->'replacement'->'metadata','{}')) returning * into newrow;
 result:=jsonb_build_object('operation','authority_preparation_supersede','execution_id',x.id,'preparations',jsonb_build_array(to_jsonb(newrow)),'superseded_preparation_id',oldrow.id);
 return public.record_rollover_operation(x.league_id,x.id,'authority_preparation_supersede',k,fp,actor,'authenticated_commissioner',newrow.id,result,coalesce(p_request->'material_metadata','{}'));
end $$;

create or replace function public.cancel_rollover_authority_preparation_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; a public.rollover_authority_preparations%rowtype; x public.rollover_executions%rowtype; k text; material jsonb; fp text; retry jsonb; result jsonb;
begin
 actor:=public.require_authenticated_user(); if p_request ? 'actor_user_id' or p_request ? 'requested_by' then raise exception 'actor spoofing forbidden'; end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),''); if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'reason and idempotency_key required'; end if;
 select * into a from public.rollover_authority_preparations where id=(p_request->>'preparation_id')::uuid for update; if a.id is null then raise exception 'preparation not found'; end if;
 select * into x from public.rollover_executions where id=a.rollover_execution_id for update; perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','authority_preparation_cancel','execution_id',x.id,'preparation_id',a.id,'expected_current_version',(p_request->>'expected_current_version')::integer,'expected_current_authority_status',p_request->>'expected_current_authority_status','expected_current_authority_fingerprint',p_request->>'expected_current_authority_fingerprint','expected_current_preparation_fingerprint',p_request->>'expected_current_preparation_fingerprint','reason',p_request->>'reason','material_metadata',coalesce(p_request->'material_metadata','{}'),'actor',actor);
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'authority_preparation_cancel',k,fp); if retry is not null then return retry; end if;
 if a.authority_status not in ('prepared','blocked','approved_for_execution') or a.version<>(p_request->>'expected_current_version')::integer or a.authority_status is distinct from p_request->>'expected_current_authority_status' or a.authority_fingerprint is distinct from p_request->>'expected_current_authority_fingerprint' or a.preparation_fingerprint is distinct from p_request->>'expected_current_preparation_fingerprint' then raise exception 'stale authority preparation'; end if;
 if exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.status='active') then raise exception 'locked execution cannot cancel authority'; end if;
 update public.rollover_authority_preparations set authority_status='cancelled',cancelled_at=clock_timestamp() where id=a.id returning * into a;
 result:=jsonb_build_object('operation','authority_preparation_cancel','execution_id',x.id,'preparations',jsonb_build_array(to_jsonb(a)));
 return public.record_rollover_operation(x.league_id,x.id,'authority_preparation_cancel',k,fp,actor,'authenticated_commissioner',a.id,result,coalesce(p_request->'material_metadata','{}'));
end $$;

revoke all on function public.prepare_rollover_authorities_authenticated(jsonb),public.supersede_rollover_authority_preparation_authenticated(jsonb),public.cancel_rollover_authority_preparation_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.prepare_rollover_authorities_authenticated(jsonb),public.supersede_rollover_authority_preparation_authenticated(jsonb),public.cancel_rollover_authority_preparation_authenticated(jsonb) to authenticated;
grant execute on function public.prepare_rollover_authorities_authenticated(jsonb),public.supersede_rollover_authority_preparation_authenticated(jsonb),public.cancel_rollover_authority_preparation_authenticated(jsonb) to service_role;
revoke insert,update,delete on public.rollover_authority_preparations from authenticated,anon;

commit;
