begin;
alter table public.rollover_dry_run_simulations add column if not exists preflight_fingerprint text;
do $$ begin if exists(select 1 from public.rollover_dry_run_simulations where preflight_fingerprint is null) then raise exception 'existing simulations require explicit preflight reconciliation';end if;end $$;
alter table public.rollover_dry_run_simulations alter column preflight_fingerprint set not null;
alter table public.rollover_dry_run_simulations drop constraint if exists rollover_dry_run_preflight_fingerprint_check;
alter table public.rollover_dry_run_simulations add constraint rollover_dry_run_preflight_fingerprint_check check(preflight_fingerprint~'^[0-9a-f]{64}$');

create or replace function public.enforce_rollover_dry_run_immutability() returns trigger language plpgsql set search_path=pg_catalog,public as $$ begin
 if old.simulation_status in ('superseded','cancelled','approved_for_plan') then raise exception 'terminal simulation immutable';end if;
 if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id or new.source_season<>old.source_season or new.target_season<>old.target_season or new.simulation_version<>old.simulation_version or new.simulator_version<>old.simulator_version or new.input_fingerprint<>old.input_fingerprint or new.result_fingerprint<>old.result_fingerprint or new.policy_fingerprint<>old.policy_fingerprint or new.preflight_fingerprint<>old.preflight_fingerprint or new.owner_population_fingerprint<>old.owner_population_fingerprint or new.commissioner_population_fingerprint<>old.commissioner_population_fingerprint or new.authority_preparation_fingerprint<>old.authority_preparation_fingerprint or new.result_payload<>old.result_payload or new.blockers<>old.blockers or new.warnings<>old.warnings or new.valid<>old.valid or new.executable<>old.executable or new.plan_eligible<>old.plan_eligible or new.generated_by<>old.generated_by or new.generated_at<>old.generated_at then raise exception 'simulation material immutable';end if;
 if new.simulation_status not in ('superseded','cancelled') then raise exception 'only supersession or cancellation allowed';end if;return new;end $$;

-- User-scoped authorization assertion. It performs no writes and returns no evidence payload.
create or replace function public.assert_rollover_dry_run_commissioner_authenticated(p_execution_id uuid)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin select * into x from public.rollover_executions where id=p_execution_id;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);return jsonb_build_object('authorized',true,'actor_user_id',actor,'execution_id',x.id,'league_id',x.league_id);end $$;

-- Database-boundary completeness checks supplement (and do not duplicate) the
-- canonical Python simulator. Incomplete instructions are rejected; complete
-- inputs with legitimate modeled blockers remain eligible for simulation.
create or replace function public.validate_rollover_dry_run_canonical_input(p_input jsonb)
returns void language plpgsql immutable set search_path=pg_catalog,public as $$
declare item jsonb;amount numeric;seen text[]:='{}';
begin
 if jsonb_typeof(p_input)<>'object' then raise exception 'canonical input must be an object';end if;
 if jsonb_typeof(p_input->'team_cap_projections')<>'array' then raise exception 'team cap projections required';end if;
 for item in select value from jsonb_array_elements(p_input->'team_cap_projections') loop
  if nullif(item->>'league_team_id','') is null then raise exception 'cap projection team identity required';end if;
  if item->>'retained_salary_total' is null then raise exception 'retained target-season salary required';end if;
  if item->>'recontract_salary_total' is null then raise exception 'recontract salary total required';end if;
  if item->>'cap_adjustments' is null or item->>'cap_credits_in' is null or item->>'cap_credits_out' is null then raise exception 'cap adjustment and credit amounts required';end if;
 end loop;
 if jsonb_typeof(p_input->'publication_instructions')<>'array' then raise exception 'publication instructions required';end if;
 for item in select value from jsonb_array_elements(p_input->'publication_instructions') loop
  if nullif(item->>'player_id','') is null then raise exception 'publication player identity required';end if;
  if item->>'publication_action' not in ('hold','plan_publication_at_execution','do_not_publish') then raise exception 'unsupported publication classification';end if;
  if item->>'publication_action'='plan_publication_at_execution' then
   if nullif(item->>'agreement_id','') is null or nullif(item->>'league_team_id','') is null then raise exception 'publication agreement and former-team identity required';end if;
   if item->>'source_status'='active' then raise exception 'active agreement publication forbidden';end if;
   if item->>'planned_contract_outcome' in ('retain_liability','preserve_active_liability','natural_expiration') then raise exception 'liability or natural expiration cannot auto-publish';end if;
   if coalesce(jsonb_array_length(item->'publication_blockers'),0)>0 then raise exception 'unresolved publication instruction';end if;
  end if;
  if (item->>'player_id')=any(seen) then raise exception 'duplicate publication instruction';end if;seen:=array_append(seen,item->>'player_id');
 end loop;
 seen:='{}';
 if jsonb_typeof(p_input->'dead_cap_instructions')<>'array' then raise exception 'dead-cap instructions required';end if;
 for item in select value from jsonb_array_elements(p_input->'dead_cap_instructions') loop
  if nullif(item->>'player_id','') is null or item->>'target_season' is null then raise exception 'dead-cap player and target season required';end if;
  amount:=coalesce((item->>'calculated_amount')::numeric,0);
  if amount<>0 and (nullif(item->>'qualifying_event_id','') is null or nullif(item->>'penalty_rule','') is null or item->>'salary_basis' is null) then raise exception 'nonzero dead cap requires event, rule, and salary basis';end if;
  if amount<>0 and item->>'planned_action' not in ('create_dead_cap','planned_dead_cap') then raise exception 'unsupported dead-cap calculation method';end if;
  if amount<>0 and (item->>'salary_basis')::numeric<=1 then raise exception '$1 dead-cap exemption violated';end if;
  if amount<>0 and item->>'penalty_rule' in ('natural_expiration','decline_only','no_response_only') then raise exception 'unsupported nonzero dead-cap evidence';end if;
  if (item->>'player_id')=any(seen) then raise exception 'duplicate dead-cap instruction';end if;seen:=array_append(seen,item->>'player_id');
 end loop;
end $$;

-- Trusted-server persistence boundary. The canonical Python simulator is the only
-- business-rule implementation; authenticated callers cannot execute this function.
create or replace function public.persist_rollover_dry_run_service(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;p public.league_rollover_policies%rowtype;actor uuid;k text;material jsonb;fp text;retry jsonb;sim public.rollover_dry_run_simulations%rowtype;prior public.rollover_dry_run_simulations%rowtype;newid uuid:=gen_random_uuid();next_version integer:=1;
 a record;authority_count integer:=0;owner_count integer;review_count integer;owner_fp text;review_fp text;expected jsonb;canonical jsonb;
begin
 if coalesce(current_setting('request.jwt.claim.role',true),'')<>'service_role' then raise exception 'trusted service role required';end if;
 if p_request ? 'actor_user_id' or p_request ? 'requested_by' then raise exception 'transport actor fields forbidden';end if;
 actor:=(p_request->>'trusted_actor_user_id')::uuid;k:=nullif(btrim(p_request->>'idempotency_key'),'');if actor is null or k is null then raise exception 'trusted actor and idempotency key required';end if;
 select * into x from public.rollover_executions where id=(p_request->>'execution_id')::uuid for update;if x.id is null then raise exception 'execution not found';end if;
 if x.league_id::text is distinct from p_request->>'league_id' or x.source_season<>(p_request->>'source_season')::integer or x.target_season<>(p_request->>'target_season')::integer or x.target_season<>x.source_season+1 then raise exception 'execution boundary drift';end if;
 if x.status<>'authority_ready' or p_request->>'expected_execution_status'<>'authority_ready' then raise exception 'execution not authority_ready';end if;
 if exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.status='active') then raise exception 'active execution lock';end if;
 select * into p from public.league_rollover_policies where id=x.policy_id for share;if p.id is null or p.status<>'approved' or p.effective_at is not null or p.id::text is distinct from p_request->>'expected_policy_id' or p.fingerprint is distinct from p_request->>'expected_policy_fingerprint' or x.policy_fingerprint is distinct from p.fingerprint then raise exception 'stale policy';end if;
 if x.preflight_fingerprint is null or x.preflight_fingerprint is distinct from p_request->>'expected_preflight_fingerprint' then raise exception 'stale or missing preflight';end if;
 if x.decision_population_fingerprint is distinct from p_request->>'expected_owner_population_fingerprint' then raise exception 'stale owner population';end if;
 select count(*) into owner_count from public.rollover_owner_decisions d where d.rollover_execution_id=x.id;
 if owner_count=0 or exists(select 1 from public.rollover_owner_decisions d where d.rollover_execution_id=x.id and d.decision_status not in ('planned_retention','planned_release','commissioner_review_requested','no_response','execution_ready')) then raise exception 'missing or unresolved owner outcome';end if;
 select count(*),min(r.metadata->>'population_fingerprint') into review_count,review_fp from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id;
 if review_count=0 or exists(select 1 from public.rollover_commissioner_reviews r where r.rollover_execution_id=x.id and r.review_state not in ('approved','rejected')) then raise exception 'missing or unresolved commissioner outcome';end if;
 if review_fp is distinct from p_request->>'expected_commissioner_population_fingerprint' then raise exception 'stale commissioner population';end if;
 perform 1 from public.rollover_authority_preparations ap where ap.rollover_execution_id=x.id order by ap.authority_type for update;
 for a in select * from public.rollover_authority_preparations ap where ap.rollover_execution_id=x.id and ap.authority_status='prepared' order by ap.authority_type loop
  authority_count:=authority_count+1;expected:=p_request->'expected_authorities'->a.authority_type;
  if expected is null or expected->>'id' is distinct from a.id::text or (expected->>'version')::integer<>a.version or expected->>'authority_fingerprint' is distinct from a.authority_fingerprint or expected->>'evidence_fingerprint' is distinct from a.evidence_fingerprint or expected->>'preparation_fingerprint' is distinct from a.preparation_fingerprint or a.policy_fingerprint<>p.fingerprint or a.owner_population_fingerprint<>x.decision_population_fingerprint or a.commissioner_population_fingerprint<>review_fp then raise exception 'stale % authority preparation',a.authority_type;end if;
 end loop;
 if authority_count<>3 or not (p_request->'expected_authorities' ?& array['publication','dead_cap','salary_cap']) then raise exception 'exactly three current authorities required';end if;
 canonical:=p_request->'canonical_result';if jsonb_typeof(canonical)<>'object' or jsonb_typeof(p_request->'canonical_input')<>'object' then raise exception 'canonical trusted artifact required';end if;
 perform public.validate_rollover_dry_run_canonical_input(p_request->'canonical_input');
 if p_request ?| array['result_payload','simulation_status'] then raise exception 'legacy caller-authoritative result fields rejected';end if;
 if public.rollover_material_fingerprint(p_request->'canonical_input') is distinct from p_request->>'canonical_input_transport_fingerprint' then raise exception 'canonical input transport fingerprint mismatch';end if;
 if canonical#>>'{simulation,input_fingerprint}' is distinct from p_request->>'input_fingerprint' or canonical#>>'{simulation,result_fingerprint}' is distinct from p_request->>'result_fingerprint' then raise exception 'canonical artifact fingerprint mismatch';end if;
 if p_request->>'expected_input_fingerprint' is not null and p_request->>'expected_input_fingerprint' is distinct from p_request->>'input_fingerprint' then raise exception 'expected input fingerprint mismatch';end if;
 if p_request->>'expected_result_fingerprint' is not null and p_request->>'expected_result_fingerprint' is distinct from p_request->>'result_fingerprint' then raise exception 'expected result fingerprint mismatch';end if;
 material:=jsonb_build_object('operation','dry_run_generate_trusted','execution',x.id,'league',x.league_id,'boundary',jsonb_build_array(x.source_season,x.target_season),'policy',p.fingerprint,'preflight',x.preflight_fingerprint,'owner',x.decision_population_fingerprint,'commissioner',review_fp,'authorities',p_request->'expected_authorities','simulator_version',p_request->>'simulator_version','input',p_request->>'input_fingerprint','result',p_request->>'result_fingerprint','supersede_simulation_id',p_request->>'supersede_simulation_id','expected_current_version',p_request->>'expected_current_version','expected_current_input_fingerprint',p_request->>'expected_current_input_fingerprint','expected_current_result_fingerprint',p_request->>'expected_current_result_fingerprint','reason',p_request->>'reason','material_metadata',coalesce(p_request->'material_metadata','{}'),'actor',actor);fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'dry_run_generate_trusted',k,fp);if retry is not null then return retry;end if;
 if p_request->>'supersede_simulation_id' is not null then
  select * into prior from public.rollover_dry_run_simulations s where s.id=(p_request->>'supersede_simulation_id')::uuid and s.rollover_execution_id=x.id for update;
  if prior.id is null or prior.simulation_status in ('superseded','cancelled','approved_for_plan') or prior.simulation_version<>(p_request->>'expected_current_version')::integer or prior.input_fingerprint is distinct from p_request->>'expected_current_input_fingerprint' or prior.result_fingerprint is distinct from p_request->>'expected_current_result_fingerprint' or prior.preflight_fingerprint is distinct from p_request->>'expected_current_preflight_fingerprint' then raise exception 'stale current simulation';end if;
  next_version:=prior.simulation_version+1;update public.rollover_dry_run_simulations set simulation_status='superseded',superseded_at=clock_timestamp(),superseded_by=newid where id=prior.id;
 elsif exists(select 1 from public.rollover_dry_run_simulations s where s.rollover_execution_id=x.id and s.simulation_status not in ('superseded','cancelled')) then raise exception 'current simulation exists';end if;
 insert into public.rollover_dry_run_simulations(id,rollover_execution_id,league_id,source_season,target_season,simulation_version,simulator_version,simulation_status,input_fingerprint,result_fingerprint,policy_fingerprint,preflight_fingerprint,owner_population_fingerprint,commissioner_population_fingerprint,authority_preparation_fingerprint,result_payload,blockers,warnings,valid,executable,plan_eligible,generated_by,metadata)
 values(newid,x.id,x.league_id,x.source_season,x.target_season,next_version,p_request->>'simulator_version',case when coalesce((p_request->>'plan_eligible')::boolean,false) then 'valid' else 'blocked' end,p_request->>'input_fingerprint',p_request->>'result_fingerprint',p.fingerprint,x.preflight_fingerprint,x.decision_population_fingerprint,review_fp,p_request->>'expected_authority_preparation_fingerprint',canonical,coalesce(p_request->'blockers','[]'),coalesce(p_request->'warnings','[]'),(p_request->>'valid')::boolean,(p_request->>'executable')::boolean,(p_request->>'plan_eligible')::boolean,actor,coalesce(p_request->'material_metadata','{}')) returning * into sim;
 return public.record_rollover_operation(x.league_id,x.id,'dry_run_generate_trusted',k,fp,actor,'internal_service',sim.id,jsonb_build_object('simulation',to_jsonb(sim)),'{}');
end $$;

-- Disable the caller-authoritative generate/supersede paths. Cancellation remains
-- authenticated because it never authors or replaces simulation conclusions.
create or replace function public.generate_rollover_dry_run_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$ begin raise exception 'dry-run generation requires the trusted canonical simulator service';end $$;
create or replace function public.supersede_rollover_dry_run_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$ begin raise exception 'dry-run supersession requires trusted canonical regeneration';end $$;

create or replace function public.cancel_rollover_dry_run_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid;x public.rollover_executions%rowtype;s public.rollover_dry_run_simulations%rowtype;
 k text;reason text;material jsonb;fp text;retry jsonb;result jsonb;
begin
 actor:=public.require_authenticated_user();
 if p_request?'actor_user_id' or p_request?'requested_by' then raise exception 'actor spoofing forbidden';end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),'');reason:=nullif(btrim(p_request->>'reason'),'');
 if k is null or reason is null then raise exception 'reason and idempotency_key required';end if;
 select * into x from public.rollover_executions where id=(p_request->>'execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 if exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.status='active') then raise exception 'active lock blocks cancellation';end if;
 select * into s from public.rollover_dry_run_simulations where id=(p_request->>'simulation_id')::uuid for update;
 if s.id is null or s.rollover_execution_id<>x.id or s.league_id<>x.league_id then raise exception 'simulation execution or league mismatch';end if;
 material:=jsonb_build_object('operation','dry_run_cancel','actor',actor,'execution_id',x.id,'league_id',x.league_id,
  'simulation_id',s.id,'expected_simulation_version',p_request->>'expected_simulation_version',
  'expected_simulation_status',p_request->>'expected_simulation_status','expected_input_fingerprint',p_request->>'expected_input_fingerprint',
  'expected_result_fingerprint',p_request->>'expected_result_fingerprint','expected_preflight_fingerprint',p_request->>'expected_preflight_fingerprint',
  'reason',reason,'material_metadata',coalesce(p_request->'material_metadata','{}'::jsonb));
 fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'dry_run_cancel',k,fp);if retry is not null then return retry;end if;
 if s.simulation_version<>(p_request->>'expected_simulation_version')::integer
  or s.simulation_status is distinct from p_request->>'expected_simulation_status'
  or s.input_fingerprint is distinct from p_request->>'expected_input_fingerprint'
  or s.result_fingerprint is distinct from p_request->>'expected_result_fingerprint'
  or s.preflight_fingerprint is distinct from p_request->>'expected_preflight_fingerprint'
  or s.preflight_fingerprint is distinct from x.preflight_fingerprint then raise exception 'stale simulation or preflight identity';end if;
 if s.simulation_status in ('approved_for_plan','superseded','cancelled') then raise exception 'simulation is terminal';end if;
 update public.rollover_dry_run_simulations set simulation_status='cancelled',cancelled_at=clock_timestamp() where id=s.id returning * into s;
 result:=jsonb_build_object('operation','dry_run_cancel','simulation',to_jsonb(s));
 return public.record_rollover_operation(x.league_id,x.id,'dry_run_cancel',k,fp,actor,'authenticated_commissioner',s.id,result,'{}');
end $$;

revoke all on function public.persist_rollover_dry_run_service(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.persist_rollover_dry_run_service(jsonb) to service_role;
revoke all on function public.assert_rollover_dry_run_commissioner_authenticated(uuid) from public,anon,authenticated,service_role;
grant execute on function public.assert_rollover_dry_run_commissioner_authenticated(uuid) to authenticated;
revoke all on function public.validate_rollover_dry_run_canonical_input(jsonb) from public,anon,authenticated;
grant execute on function public.validate_rollover_dry_run_canonical_input(jsonb) to service_role;
revoke all on function public.generate_rollover_dry_run_authenticated(jsonb),public.supersede_rollover_dry_run_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.generate_rollover_dry_run_authenticated(jsonb),public.supersede_rollover_dry_run_authenticated(jsonb) to authenticated;
revoke all on function public.cancel_rollover_dry_run_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.cancel_rollover_dry_run_authenticated(jsonb) to authenticated;
commit;
