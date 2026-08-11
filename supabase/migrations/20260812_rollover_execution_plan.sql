begin;

-- Phase 3B.5G plans instructions only. Production control tables are empty;
-- this migration creates no execution, simulation, plan, operation, or domain row.
do $$ begin
 if exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_execution_plans' and column_name='status') then alter table public.rollover_execution_plans rename column status to plan_status;end if;
 if exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_execution_plans' and column_name='decision_population_fingerprint') then alter table public.rollover_execution_plans rename column decision_population_fingerprint to owner_population_fingerprint;end if;
 if exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_execution_plans' and column_name='authority_fingerprint') then alter table public.rollover_execution_plans rename column authority_fingerprint to authority_preparation_fingerprint;end if;
 if exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_execution_plans' and column_name='execution_plan_fingerprint') then alter table public.rollover_execution_plans rename column execution_plan_fingerprint to plan_fingerprint;end if;
 if exists(select 1 from information_schema.columns where table_schema='public' and table_name='rollover_execution_plans' and column_name='plan_payload') then alter table public.rollover_execution_plans rename column plan_payload to ordered_operations;end if;
end $$;

alter table public.rollover_execution_plans drop column if exists planned_after_state_fingerprint;
alter table public.rollover_execution_plans add column if not exists planner_version text;
alter table public.rollover_execution_plans add column if not exists simulation_id uuid references public.rollover_dry_run_simulations(id) on delete restrict;
alter table public.rollover_execution_plans add column if not exists simulation_version integer;
alter table public.rollover_execution_plans add column if not exists simulator_version text;
alter table public.rollover_execution_plans add column if not exists validator_version text;
alter table public.rollover_execution_plans add column if not exists simulation_input_fingerprint text;
alter table public.rollover_execution_plans add column if not exists simulation_result_fingerprint text;
alter table public.rollover_execution_plans add column if not exists commissioner_population_fingerprint text;
alter table public.rollover_execution_plans add column if not exists plan_input_fingerprint text;
alter table public.rollover_execution_plans add column if not exists operation_count integer;
alter table public.rollover_execution_plans add column if not exists operation_summary jsonb;
alter table public.rollover_execution_plans add column if not exists validation_payload jsonb;
alter table public.rollover_execution_plans add column if not exists executable boolean;
alter table public.rollover_execution_plans add column if not exists approved_for_execution boolean not null default false;
alter table public.rollover_execution_plans add column if not exists cancelled_at timestamptz;
alter table public.rollover_execution_plans add column if not exists superseded_by uuid references public.rollover_execution_plans(id) on delete restrict deferrable initially deferred;

do $$ begin
 if exists(select 1 from public.rollover_execution_plans) then raise exception 'Phase 3B.5G requires explicit reconciliation of existing plan rows';end if;
end $$;
alter table public.rollover_execution_plans alter column planner_version set not null;
alter table public.rollover_execution_plans alter column simulation_id set not null;
alter table public.rollover_execution_plans alter column simulation_version set not null;
alter table public.rollover_execution_plans alter column simulator_version set not null;
alter table public.rollover_execution_plans alter column validator_version set not null;
alter table public.rollover_execution_plans alter column simulation_input_fingerprint set not null;
alter table public.rollover_execution_plans alter column simulation_result_fingerprint set not null;
alter table public.rollover_execution_plans alter column commissioner_population_fingerprint set not null;
alter table public.rollover_execution_plans alter column plan_input_fingerprint set not null;
alter table public.rollover_execution_plans alter column operation_count set not null;
alter table public.rollover_execution_plans alter column operation_summary set not null;
alter table public.rollover_execution_plans alter column validation_payload set not null;
alter table public.rollover_execution_plans alter column executable set not null;

alter table public.rollover_execution_plans drop constraint if exists rollover_execution_plans_status_check;
alter table public.rollover_execution_plans drop constraint if exists rollover_execution_plans_check;
alter table public.rollover_execution_plans drop constraint if exists rollover_execution_plans_plan_status_check;
alter table public.rollover_execution_plans add constraint rollover_execution_plans_plan_status_check check(plan_status in ('generated','blocked','valid','superseded','cancelled','approved_for_execution'));
alter table public.rollover_execution_plans add constraint rollover_execution_plans_positive_version check(plan_version>0 and simulation_version>0);
alter table public.rollover_execution_plans add constraint rollover_execution_plans_sequential_seasons check(target_season=source_season+1);
alter table public.rollover_execution_plans add constraint rollover_execution_plans_fingerprints check(
 policy_fingerprint~'^[0-9a-f]{64}$' and preflight_fingerprint~'^[0-9a-f]{64}$'
 and owner_population_fingerprint~'^[0-9a-f]{64}$' and commissioner_population_fingerprint~'^[0-9a-f]{64}$'
 and authority_preparation_fingerprint~'^[0-9a-f]{64}$' and simulation_input_fingerprint~'^[0-9a-f]{64}$'
 and simulation_result_fingerprint~'^[0-9a-f]{64}$' and plan_input_fingerprint~'^[0-9a-f]{64}$'
 and plan_fingerprint~'^[0-9a-f]{64}$');
alter table public.rollover_execution_plans add constraint rollover_execution_plans_json_shape check(
 jsonb_typeof(ordered_operations)='array' and jsonb_typeof(operation_summary)='object'
 and jsonb_typeof(validation_payload)='object' and jsonb_typeof(blockers)='array'
 and jsonb_typeof(warnings)='array' and jsonb_typeof(metadata)='object');
alter table public.rollover_execution_plans add constraint rollover_execution_plans_operation_count check(operation_count=jsonb_array_length(ordered_operations));
alter table public.rollover_execution_plans add constraint rollover_execution_plans_eligibility check(
 (not approved_for_execution or executable) and (plan_status<>'valid' or (executable and jsonb_array_length(blockers)=0))
 and (plan_status<>'approved_for_execution' or (approved_for_execution and executable and jsonb_array_length(blockers)=0)));
alter table public.rollover_execution_plans add constraint rollover_execution_plans_supersession_shape check((plan_status='superseded')=(superseded_at is not null and superseded_by is not null));
alter table public.rollover_execution_plans add constraint rollover_execution_plans_cancellation_shape check((plan_status='cancelled')=(cancelled_at is not null));

drop index if exists public.rollover_plans_one_approved_uidx;
create unique index if not exists rollover_execution_plans_one_current_uidx on public.rollover_execution_plans(rollover_execution_id) where plan_status not in ('superseded','cancelled');
create unique index if not exists rollover_execution_plans_execution_version_uidx on public.rollover_execution_plans(rollover_execution_id,plan_version);
create unique index if not exists rollover_execution_plans_execution_fingerprint_uidx on public.rollover_execution_plans(rollover_execution_id,plan_fingerprint);
create index if not exists rollover_execution_plans_simulation_idx on public.rollover_execution_plans(simulation_id,simulation_version);

create or replace function public.enforce_rollover_execution_plan_immutability() returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if old.plan_status in ('superseded','cancelled','approved_for_execution') then raise exception 'terminal execution plan is immutable';end if;
 if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id
 or new.source_season<>old.source_season or new.target_season<>old.target_season or new.plan_version<>old.plan_version
 or new.planner_version<>old.planner_version or new.simulation_id<>old.simulation_id or new.simulation_version<>old.simulation_version
 or new.simulator_version<>old.simulator_version or new.validator_version<>old.validator_version
 or new.simulation_input_fingerprint<>old.simulation_input_fingerprint or new.simulation_result_fingerprint<>old.simulation_result_fingerprint
 or new.preflight_fingerprint<>old.preflight_fingerprint or new.policy_fingerprint<>old.policy_fingerprint
 or new.owner_population_fingerprint<>old.owner_population_fingerprint or new.commissioner_population_fingerprint<>old.commissioner_population_fingerprint
 or new.authority_preparation_fingerprint<>old.authority_preparation_fingerprint or new.plan_input_fingerprint<>old.plan_input_fingerprint
 or new.plan_fingerprint<>old.plan_fingerprint or new.operation_count<>old.operation_count or new.operation_summary<>old.operation_summary
 or new.ordered_operations<>old.ordered_operations or new.validation_payload<>old.validation_payload or new.blockers<>old.blockers
 or new.warnings<>old.warnings or new.executable<>old.executable or new.generated_by<>old.generated_by or new.generated_at<>old.generated_at
 or new.metadata<>old.metadata or new.created_at<>old.created_at then raise exception 'execution plan material is immutable';end if;
 if new.plan_status not in ('superseded','cancelled','approved_for_execution') then raise exception 'illegal execution plan transition';end if;
 return new;
end $$;
drop trigger if exists rollover_plan_guard on public.rollover_execution_plans;
drop trigger if exists rollover_execution_plan_immutability on public.rollover_execution_plans;
create trigger rollover_execution_plan_immutability before update on public.rollover_execution_plans for each row execute function public.enforce_rollover_execution_plan_immutability();

alter table public.rollover_execution_plans enable row level security;
revoke all on table public.rollover_execution_plans from public,anon,authenticated;
grant select on table public.rollover_execution_plans to authenticated;
grant select,insert,update on table public.rollover_execution_plans to service_role;
drop policy if exists rollover_execution_plans_league_member_select on public.rollover_execution_plans;
create policy rollover_execution_plans_league_member_select on public.rollover_execution_plans for select to authenticated using(
 exists(select 1 from public.league_memberships m where m.league_id=rollover_execution_plans.league_id and m.user_id=auth.uid()));

create or replace function public.assert_rollover_plan_commissioner_authenticated(p_execution_id uuid)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin select * into x from public.rollover_executions where id=p_execution_id;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);return jsonb_build_object('authorized',true,'actor_user_id',actor,'execution_id',x.id,'league_id',x.league_id);end $$;

create or replace function public.persist_rollover_execution_plan_service(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid;k text;x public.rollover_executions%rowtype;s public.rollover_dry_run_simulations%rowtype;
 plan jsonb;prior public.rollover_execution_plans%rowtype;saved public.rollover_execution_plans%rowtype;
 material jsonb;fp text;retry jsonb;newid uuid;next_version integer:=1;
begin
 if coalesce(current_setting('request.jwt.claim.role',true),'')<>'service_role' then raise exception 'trusted service role required';end if;
 if p_request?'actor_user_id' or p_request?'requested_by' then raise exception 'transport actor fields forbidden';end if;
 actor:=(p_request->>'trusted_actor_user_id')::uuid;k:=nullif(btrim(p_request->>'idempotency_key'),'');if actor is null or k is null then raise exception 'trusted actor and idempotency key required';end if;
 if p_request ?| array['ordered_operations','operation_summary','blockers','warnings','executable','plan_status','plan_fingerprint','plan_input_fingerprint','operation_count','validation_payload'] then raise exception 'caller-authoritative plan fields rejected';end if;
 select * into x from public.rollover_executions where id=(p_request->>'execution_id')::uuid for update;if x.id is null then raise exception 'execution not found';end if;
 if x.status<>'authority_ready' or x.target_season<>x.source_season+1 then raise exception 'execution not eligible for plan generation';end if;
 if exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.status='active') then raise exception 'active execution lock';end if;
 select * into s from public.rollover_dry_run_simulations where id=(p_request->>'simulation_id')::uuid and rollover_execution_id=x.id for update;
 if s.id is null or s.league_id<>x.league_id or s.source_season<>x.source_season or s.target_season<>x.target_season then raise exception 'simulation boundary mismatch';end if;
 if s.simulation_status<>'valid' or not s.valid or not s.executable or not s.plan_eligible or jsonb_array_length(s.blockers)<>0 then raise exception 'simulation is not plan eligible';end if;
 if s.simulation_version<>(p_request->>'expected_simulation_version')::integer or s.input_fingerprint is distinct from p_request->>'expected_simulation_input_fingerprint' or s.result_fingerprint is distinct from p_request->>'expected_simulation_result_fingerprint' or s.preflight_fingerprint is distinct from p_request->>'expected_preflight_fingerprint' or s.policy_fingerprint is distinct from p_request->>'expected_policy_fingerprint' or s.owner_population_fingerprint is distinct from p_request->>'expected_owner_population_fingerprint' or s.commissioner_population_fingerprint is distinct from p_request->>'expected_commissioner_population_fingerprint' or s.authority_preparation_fingerprint is distinct from p_request->>'expected_authority_preparation_fingerprint' then raise exception 'stale simulation evidence';end if;
 plan:=p_request->'plan';if jsonb_typeof(plan)<>'object' or jsonb_typeof(plan->'ordered_operations')<>'array' or jsonb_typeof(plan->'operation_summary')<>'object' or jsonb_typeof(plan->'validation_payload')<>'object' then raise exception 'trusted canonical plan required';end if;
 if plan->>'rollover_execution_id' is distinct from x.id::text or plan->>'simulation_id' is distinct from s.id::text or plan->>'simulation_result_fingerprint' is distinct from s.result_fingerprint or plan->>'preflight_fingerprint' is distinct from s.preflight_fingerprint or (plan->>'operation_count')::integer<>jsonb_array_length(plan->'ordered_operations') or (plan->>'approved_for_execution')::boolean then raise exception 'canonical plan provenance mismatch';end if;
 if plan->>'plan_status' not in ('valid','blocked') or plan->>'plan_fingerprint' !~ '^[0-9a-f]{64}$' or plan->>'plan_input_fingerprint' !~ '^[0-9a-f]{64}$' then raise exception 'invalid canonical plan conclusion';end if;
 material:=jsonb_build_object('operation','execution_plan_generate_trusted','execution_id',x.id,'league_id',x.league_id,'simulation_id',s.id,'simulation_version',s.simulation_version,'simulation_result_fingerprint',s.result_fingerprint,'preflight_fingerprint',s.preflight_fingerprint,'planner_version',plan->>'planner_version','plan_input_fingerprint',plan->>'plan_input_fingerprint','plan_fingerprint',plan->>'plan_fingerprint','supersede_plan_id',p_request->>'supersede_plan_id','expected_current_plan_version',p_request->>'expected_current_plan_version','expected_current_plan_fingerprint',p_request->>'expected_current_plan_fingerprint','metadata',coalesce(p_request->'material_metadata','{}'),'actor',actor);
 fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'execution_plan_generate_trusted',k,fp);if retry is not null then return retry;end if;
 newid:=(plan->>'id')::uuid;
 if p_request->>'supersede_plan_id' is not null then
  select * into prior from public.rollover_execution_plans where id=(p_request->>'supersede_plan_id')::uuid and rollover_execution_id=x.id for update;
  if prior.id is null or prior.plan_status in ('superseded','cancelled','approved_for_execution') or prior.plan_version<>(p_request->>'expected_current_plan_version')::integer or prior.plan_fingerprint is distinct from p_request->>'expected_current_plan_fingerprint' or prior.simulation_id<>s.id or prior.simulation_result_fingerprint<>s.result_fingerprint then raise exception 'stale current execution plan';end if;
  next_version:=prior.plan_version+1;if (plan->>'plan_version')::integer<>next_version then raise exception 'replacement plan version mismatch';end if;
  update public.rollover_execution_plans set plan_status='superseded',superseded_at=clock_timestamp(),superseded_by=newid where id=prior.id;
 elsif exists(select 1 from public.rollover_execution_plans p where p.rollover_execution_id=x.id and p.plan_status not in ('superseded','cancelled')) then raise exception 'current execution plan exists';end if;
 insert into public.rollover_execution_plans(id,rollover_execution_id,league_id,source_season,target_season,plan_version,planner_version,plan_status,simulation_id,simulation_version,simulator_version,validator_version,simulation_input_fingerprint,simulation_result_fingerprint,preflight_fingerprint,policy_fingerprint,owner_population_fingerprint,commissioner_population_fingerprint,authority_preparation_fingerprint,plan_input_fingerprint,plan_fingerprint,operation_count,operation_summary,ordered_operations,validation_payload,blockers,warnings,executable,approved_for_execution,generated_by,metadata)
 values(newid,x.id,x.league_id,x.source_season,x.target_season,(plan->>'plan_version')::integer,plan->>'planner_version',plan->>'plan_status',s.id,s.simulation_version,s.simulator_version,plan->>'validator_version',s.input_fingerprint,s.result_fingerprint,s.preflight_fingerprint,s.policy_fingerprint,s.owner_population_fingerprint,s.commissioner_population_fingerprint,s.authority_preparation_fingerprint,plan->>'plan_input_fingerprint',plan->>'plan_fingerprint',(plan->>'operation_count')::integer,plan->'operation_summary',plan->'ordered_operations',plan->'validation_payload',plan->'blockers',plan->'warnings',(plan->>'executable')::boolean,false,actor,coalesce(plan->'metadata','{}')) returning * into saved;
 return public.record_rollover_operation(x.league_id,x.id,'execution_plan_generate_trusted',k,fp,actor,'internal_service',saved.id,jsonb_build_object('plan',to_jsonb(saved)),'{}');
end $$;

create or replace function public.generate_rollover_execution_plan_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$ begin perform public.require_authenticated_user();if p_request?'actor_user_id' or p_request?'requested_by' then raise exception 'actor spoofing forbidden';end if;raise exception 'execution plan generation requires trusted canonical planner service';end $$;
create or replace function public.supersede_rollover_execution_plan_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$ begin perform public.require_authenticated_user();if p_request?'actor_user_id' or p_request?'requested_by' then raise exception 'actor spoofing forbidden';end if;raise exception 'execution plan supersession requires trusted canonical planner service';end $$;

create or replace function public.cancel_rollover_execution_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;p public.rollover_execution_plans%rowtype;k text;material jsonb;fp text;retry jsonb;result jsonb;
begin
 if p_request?'actor_user_id' or p_request?'requested_by' then raise exception 'actor spoofing forbidden';end if;k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'reason and idempotency key required';end if;
 select * into x from public.rollover_executions where id=(p_request->>'execution_id')::uuid for update;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 if exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.status='active') then raise exception 'active execution lock';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'plan_id')::uuid and rollover_execution_id=x.id for update;if p.id is null then raise exception 'execution plan not found';end if;
 material:=jsonb_build_object('operation','execution_plan_cancel','actor',actor,'execution_id',x.id,'league_id',x.league_id,'plan_id',p.id,'expected_plan_version',p_request->>'expected_plan_version','expected_plan_status',p_request->>'expected_plan_status','expected_plan_input_fingerprint',p_request->>'expected_plan_input_fingerprint','expected_plan_fingerprint',p_request->>'expected_plan_fingerprint','expected_simulation_result_fingerprint',p_request->>'expected_simulation_result_fingerprint','reason',p_request->>'reason','metadata',coalesce(p_request->'material_metadata','{}'));
 fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'execution_plan_cancel',k,fp);if retry is not null then return retry;end if;
 if p.plan_version<>(p_request->>'expected_plan_version')::integer or p.plan_status is distinct from p_request->>'expected_plan_status' or p.plan_input_fingerprint is distinct from p_request->>'expected_plan_input_fingerprint' or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint' or p.simulation_result_fingerprint is distinct from p_request->>'expected_simulation_result_fingerprint' or p.plan_status in ('superseded','cancelled','approved_for_execution') then raise exception 'stale or terminal execution plan';end if;
 update public.rollover_execution_plans set plan_status='cancelled',cancelled_at=clock_timestamp() where id=p.id returning * into p;result:=jsonb_build_object('plan',to_jsonb(p));return public.record_rollover_operation(x.league_id,x.id,'execution_plan_cancel',k,fp,actor,'authenticated_commissioner',p.id,result,'{}');
end $$;

revoke all on function public.assert_rollover_plan_commissioner_authenticated(uuid),public.persist_rollover_execution_plan_service(jsonb),public.generate_rollover_execution_plan_authenticated(jsonb),public.supersede_rollover_execution_plan_authenticated(jsonb),public.cancel_rollover_execution_plan_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.assert_rollover_plan_commissioner_authenticated(uuid),public.generate_rollover_execution_plan_authenticated(jsonb),public.supersede_rollover_execution_plan_authenticated(jsonb),public.cancel_rollover_execution_plan_authenticated(jsonb) to authenticated;
grant execute on function public.persist_rollover_execution_plan_service(jsonb) to service_role;

comment on table public.rollover_execution_plans is 'Immutable deterministic Phase 3B.5G instruction plans. Rows do not execute rollover operations.';
commit;
