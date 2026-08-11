begin;

-- Phase 3B.5H authorizes one immutable plan and reserves its cutover boundary.
-- It does not execute any ordered operation or mutate any domain table.
create table if not exists public.rollover_execution_plan_approvals (
 id uuid primary key default gen_random_uuid(),
 rollover_execution_id uuid not null references public.rollover_executions(id) on delete restrict,
 league_id uuid not null references public.leagues(id) on delete restrict,
 source_season integer not null,target_season integer not null,
 execution_plan_id uuid not null references public.rollover_execution_plans(id) on delete restrict,
 execution_plan_version integer not null,simulation_id uuid not null references public.rollover_dry_run_simulations(id) on delete restrict,
 simulation_version integer not null,approval_version integer not null,
 approval_status text not null,execution_status_at_approval text not null,plan_status_at_approval text not null,
 plan_input_fingerprint text not null,plan_fingerprint text not null,simulation_input_fingerprint text not null,
 simulation_result_fingerprint text not null,preflight_fingerprint text not null,policy_fingerprint text not null,
 owner_population_fingerprint text not null,commissioner_population_fingerprint text not null,
 authority_preparation_fingerprint text not null,operation_count integer not null,operation_fingerprints jsonb not null,
 approval_fingerprint text not null,approval_statement_code text not null,approval_statement_version integer not null,
 approval_statement text not null,approved_by uuid not null references auth.users(id),approved_at timestamptz not null default now(),
 revoked_at timestamptz,revoked_by uuid references auth.users(id),revocation_reason text,
 superseded_at timestamptz,superseded_by uuid references public.rollover_execution_plan_approvals(id) deferrable initially deferred,
 metadata jsonb not null default '{}'::jsonb,created_at timestamptz not null default now(),
 constraint rollover_execution_approvals_seasons check(target_season=source_season+1),
 constraint rollover_execution_approvals_versions check(execution_plan_version>0 and simulation_version>0 and approval_version>0),
 constraint rollover_execution_approvals_status check(approval_status in ('approved','revoked','stale','superseded','cancelled')),
 constraint rollover_execution_approvals_fingerprints check(
  plan_input_fingerprint~'^[0-9a-f]{64}$' and plan_fingerprint~'^[0-9a-f]{64}$' and simulation_input_fingerprint~'^[0-9a-f]{64}$'
  and simulation_result_fingerprint~'^[0-9a-f]{64}$' and preflight_fingerprint~'^[0-9a-f]{64}$' and policy_fingerprint~'^[0-9a-f]{64}$'
  and owner_population_fingerprint~'^[0-9a-f]{64}$' and commissioner_population_fingerprint~'^[0-9a-f]{64}$'
  and authority_preparation_fingerprint~'^[0-9a-f]{64}$' and approval_fingerprint~'^[0-9a-f]{64}$'),
 constraint rollover_execution_approvals_json check(jsonb_typeof(operation_fingerprints)='array' and jsonb_typeof(metadata)='object' and operation_count=jsonb_array_length(operation_fingerprints)),
 constraint rollover_execution_approvals_statement check(approval_statement_code='ROLLOVER_EXECUTION_PLAN_APPROVED' and approval_statement_version=1 and length(btrim(approval_statement))>0),
 constraint rollover_execution_approvals_revocation check((approval_status='revoked')=(revoked_at is not null and revoked_by is not null and length(btrim(revocation_reason))>0)),
 constraint rollover_execution_approvals_supersession check((approval_status='superseded')=(superseded_at is not null and superseded_by is not null)),
 unique(rollover_execution_id,approval_version),unique(rollover_execution_id,approval_fingerprint)
);
create unique index if not exists rollover_execution_approvals_one_current_execution_uidx on public.rollover_execution_plan_approvals(rollover_execution_id) where approval_status='approved';
create unique index if not exists rollover_execution_approvals_one_current_plan_uidx on public.rollover_execution_plan_approvals(execution_plan_id) where approval_status='approved';
create index if not exists rollover_execution_approvals_simulation_idx on public.rollover_execution_plan_approvals(simulation_id,simulation_version);

alter table public.rollover_execution_locks add column if not exists source_season integer;
alter table public.rollover_execution_locks add column if not exists target_season integer;
alter table public.rollover_execution_locks add column if not exists execution_plan_id uuid references public.rollover_execution_plans(id) on delete restrict;
alter table public.rollover_execution_locks add column if not exists execution_plan_version integer;
alter table public.rollover_execution_locks add column if not exists approval_id uuid references public.rollover_execution_plan_approvals(id) on delete restrict;
alter table public.rollover_execution_locks add column if not exists lock_type text;
alter table public.rollover_execution_locks add column if not exists plan_fingerprint text;
alter table public.rollover_execution_locks add column if not exists simulation_result_fingerprint text;
alter table public.rollover_execution_locks add column if not exists policy_fingerprint text;
alter table public.rollover_execution_locks add column if not exists authority_preparation_fingerprint text;
alter table public.rollover_execution_locks add column if not exists released_by uuid references auth.users(id);
alter table public.rollover_execution_locks add column if not exists release_reason text;
alter table public.rollover_execution_locks drop constraint if exists rollover_execution_locks_status_check;
alter table public.rollover_execution_locks add constraint rollover_execution_locks_status_check check(status in ('pending','active','released','expired','cancelled','superseded','consumed'));
alter table public.rollover_execution_locks add constraint rollover_execution_locks_cutover_shape check(lock_type is null or (lock_type='cutover' and lock_scope='rollover_global' and source_season is not null and target_season=source_season+1 and execution_plan_id is not null and execution_plan_version>0 and approval_id is not null and plan_fingerprint~'^[0-9a-f]{64}$' and simulation_result_fingerprint~'^[0-9a-f]{64}$' and policy_fingerprint~'^[0-9a-f]{64}$' and authority_preparation_fingerprint~'^[0-9a-f]{64}$'));
alter table public.rollover_execution_locks add constraint rollover_execution_locks_release_shape check(status not in ('released','superseded','cancelled','consumed') or (released_at is not null and released_by is not null and length(btrim(release_reason))>0));
create unique index if not exists rollover_execution_locks_one_active_cutover_execution_uidx on public.rollover_execution_locks(rollover_execution_id) where status='active' and lock_type='cutover';
create unique index if not exists rollover_execution_locks_one_active_approval_uidx on public.rollover_execution_locks(approval_id) where status='active' and lock_type='cutover';

create or replace function public.phase3b5h_execution_not_locked(p_execution_id uuid) returns void language plpgsql stable security definer set search_path=pg_catalog,public as $$begin if exists(select 1 from public.rollover_execution_plan_approvals a where a.rollover_execution_id=p_execution_id and a.approval_status='approved') or exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=p_execution_id and l.status='active' and l.lock_type='cutover') then raise exception 'revoke execution-plan approval and release cutover lock first';end if;end$$;

create or replace function public.validate_rollover_execution_transition() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare ok boolean:=false;
begin
 if old.status='completed' then raise exception 'Completed rollover executions are immutable.';end if;
 if old.status='cancelled' and new.status<>old.status then raise exception 'Cancelled rollover executions cannot resume.';end if;
 if old.status in ('committed','validating','failed_postcommit_validation') and new.status='cancelled' then raise exception 'Committed rollover executions cannot be cancelled.';end if;
 if new.status='cancelled' then perform public.phase3b5h_execution_not_locked(old.id);end if;
 ok:=new.status=old.status
 or (old.status='draft' and new.status in ('preflight_ready','cancelled','failed_precommit'))
 or (old.status='preflight_ready' and new.status in ('notice_open','cancelled','failed_precommit'))
 or (old.status='notice_open' and new.status in ('decision_window_open','cancelled','failed_precommit'))
 or (old.status='decision_window_open' and new.status in ('decision_window_closed','cancelled','failed_precommit'))
 or (old.status='decision_window_closed' and new.status in ('authority_initializing','cancelled','failed_precommit'))
 or (old.status='authority_initializing' and new.status in ('authority_ready','cancelled','failed_precommit'))
 or (old.status='authority_ready' and new.status in ('plan_ready','execution_ready','cancelled','failed_precommit'))
 or (old.status='plan_ready' and new.status in ('awaiting_execution_approval','execution_ready','cancelled','failed_precommit'))
 or (old.status='awaiting_execution_approval' and new.status in ('execution_ready','plan_ready','authority_ready','cancelled','failed_precommit'))
 or (old.status='execution_ready' and new.status in ('executing','plan_ready','authority_ready','failed_precommit'))
 or (old.status='executing' and new.status in ('committed','failed_precommit')) or (old.status='committed' and new.status='validating')
 or (old.status='validating' and new.status in ('completed','failed_postcommit_validation')) or (old.status='failed_postcommit_validation' and new.status='validating');
 if not ok then raise exception 'Illegal rollover execution transition: % -> %',old.status,new.status;end if;new.updated_at=now();return new;
end $$;

create or replace function public.enforce_rollover_execution_approval_immutability() returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if tg_op='DELETE' then raise exception 'execution approvals cannot be deleted';end if;
 if old.approval_status<>'approved' then raise exception 'terminal execution approval is immutable';end if;
 if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id or new.source_season<>old.source_season or new.target_season<>old.target_season or new.execution_plan_id<>old.execution_plan_id or new.execution_plan_version<>old.execution_plan_version or new.simulation_id<>old.simulation_id or new.simulation_version<>old.simulation_version or new.approval_version<>old.approval_version or new.execution_status_at_approval<>old.execution_status_at_approval or new.plan_status_at_approval<>old.plan_status_at_approval or new.plan_input_fingerprint<>old.plan_input_fingerprint or new.plan_fingerprint<>old.plan_fingerprint or new.simulation_input_fingerprint<>old.simulation_input_fingerprint or new.simulation_result_fingerprint<>old.simulation_result_fingerprint or new.preflight_fingerprint<>old.preflight_fingerprint or new.policy_fingerprint<>old.policy_fingerprint or new.owner_population_fingerprint<>old.owner_population_fingerprint or new.commissioner_population_fingerprint<>old.commissioner_population_fingerprint or new.authority_preparation_fingerprint<>old.authority_preparation_fingerprint or new.operation_count<>old.operation_count or new.operation_fingerprints<>old.operation_fingerprints or new.approval_fingerprint<>old.approval_fingerprint or new.approval_statement_code<>old.approval_statement_code or new.approval_statement_version<>old.approval_statement_version or new.approval_statement<>old.approval_statement or new.approved_by<>old.approved_by or new.approved_at<>old.approved_at or new.metadata<>old.metadata or new.created_at<>old.created_at then raise exception 'execution approval evidence is immutable';end if;
 if new.approval_status not in ('revoked','stale','superseded','cancelled') then raise exception 'illegal execution approval transition';end if;return new;
end $$;
drop trigger if exists rollover_execution_approval_immutability on public.rollover_execution_plan_approvals;
create trigger rollover_execution_approval_immutability before update or delete on public.rollover_execution_plan_approvals for each row execute function public.enforce_rollover_execution_approval_immutability();

create or replace function public.enforce_phase3b5h_plan_immutability() returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if old.plan_status='approved_for_execution' then
  if new.plan_status not in ('valid','cancelled','superseded') then raise exception 'approved execution plan is immutable';end if;
  if exists(select 1 from public.rollover_execution_plan_approvals a where a.execution_plan_id=old.id and a.approval_status='approved') then raise exception 'revoke approval before changing approved plan';end if;
 elsif old.plan_status in ('superseded','cancelled') then raise exception 'terminal execution plan is immutable';end if;
 if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id or new.source_season<>old.source_season or new.target_season<>old.target_season or new.plan_version<>old.plan_version or new.planner_version<>old.planner_version or new.simulation_id<>old.simulation_id or new.simulation_version<>old.simulation_version or new.simulator_version<>old.simulator_version or new.validator_version<>old.validator_version or new.simulation_input_fingerprint<>old.simulation_input_fingerprint or new.simulation_result_fingerprint<>old.simulation_result_fingerprint or new.preflight_fingerprint<>old.preflight_fingerprint or new.policy_fingerprint<>old.policy_fingerprint or new.owner_population_fingerprint<>old.owner_population_fingerprint or new.commissioner_population_fingerprint<>old.commissioner_population_fingerprint or new.authority_preparation_fingerprint<>old.authority_preparation_fingerprint or new.plan_input_fingerprint<>old.plan_input_fingerprint or new.plan_fingerprint<>old.plan_fingerprint or new.operation_count<>old.operation_count or new.operation_summary<>old.operation_summary or new.ordered_operations<>old.ordered_operations or new.validation_payload<>old.validation_payload or new.blockers<>old.blockers or new.warnings<>old.warnings or new.executable<>old.executable or new.generated_by<>old.generated_by or new.generated_at<>old.generated_at or new.metadata<>old.metadata or new.created_at<>old.created_at then raise exception 'execution plan material is immutable';end if;
 if old.plan_status='valid' and new.plan_status not in ('superseded','cancelled','approved_for_execution') then raise exception 'illegal execution plan transition';end if;return new;
end $$;
drop trigger if exists rollover_execution_plan_immutability on public.rollover_execution_plans;
create trigger rollover_execution_plan_immutability before update on public.rollover_execution_plans for each row execute function public.enforce_phase3b5h_plan_immutability();

create or replace function public.enforce_phase3b5h_lock_transition() returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if tg_op='UPDATE' and old.lock_type='cutover' then
  if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id or new.source_season<>old.source_season or new.target_season<>old.target_season or new.execution_plan_id<>old.execution_plan_id or new.execution_plan_version<>old.execution_plan_version or new.approval_id<>old.approval_id or new.lock_type<>old.lock_type or new.lock_scope<>old.lock_scope or new.plan_fingerprint<>old.plan_fingerprint or new.simulation_result_fingerprint<>old.simulation_result_fingerprint or new.policy_fingerprint<>old.policy_fingerprint or new.authority_preparation_fingerprint<>old.authority_preparation_fingerprint or new.acquired_by<>old.acquired_by or new.acquired_at<>old.acquired_at or new.lock_token<>old.lock_token then raise exception 'cutover lock identity is immutable';end if;
  if old.status<>'active' or new.status not in ('released','superseded','cancelled','consumed') then raise exception 'illegal cutover lock transition';end if;
 elsif tg_op='UPDATE' and not(new.status=old.status or (old.status='pending' and new.status in ('active','cancelled')) or (old.status='active' and new.status in ('released','expired'))) then raise exception 'illegal rollover lock transition';end if;
 if new.status='active' and (new.acquired_at is null or new.acquired_by is null) then raise exception 'active lock requires acquisition data';end if;
 if new.status='released' and new.released_at is null then raise exception 'released lock requires released_at';end if;new.updated_at=now();return new;
end $$;
drop trigger if exists rollover_lock_guard on public.rollover_execution_locks;
create trigger rollover_lock_guard before insert or update on public.rollover_execution_locks for each row execute function public.enforce_phase3b5h_lock_transition();

alter table public.rollover_execution_plan_approvals enable row level security;
revoke all on table public.rollover_execution_plan_approvals from public,anon,authenticated;
grant select on table public.rollover_execution_plan_approvals to authenticated;
grant select,insert,update on table public.rollover_execution_plan_approvals to service_role;
create policy rollover_execution_plan_approvals_league_member_select on public.rollover_execution_plan_approvals for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_execution_plan_approvals.league_id and m.user_id=auth.uid()));

create or replace function public.persist_rollover_execution_plan_approval_service(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
begin if coalesce(current_setting('request.jwt.claim.role',true),'')<>'service_role' then raise exception 'trusted service role required';end if;raise exception 'use authenticated approval RPC; trusted persistence is not directly caller-authoritative';end $$;

create or replace function public.approve_rollover_execution_plan_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;p public.rollover_execution_plans%rowtype;s public.rollover_dry_run_simulations%rowtype;a public.rollover_execution_plan_approvals%rowtype;l public.rollover_execution_locks%rowtype;k text;material jsonb;fp text;retry jsonb;ops jsonb;approval_version integer;result jsonb;
begin
 if p_request?'actor_user_id' or p_request?'requested_by' or p_request?'approved_by' then raise exception 'actor spoofing forbidden';end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null then raise exception 'idempotency key required';end if;
 if p_request->>'approval_statement_code'<>'ROLLOVER_EXECUTION_PLAN_APPROVED' or (p_request->>'approval_statement_version')::integer<>1 or nullif(btrim(p_request->>'approval_statement'),'') is null then raise exception 'valid approval statement required';end if;
 perform pg_advisory_xact_lock(hashtextextended(p_request->>'rollover_execution_id',0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','execution_plan_approve','execution_id',x.id,'league_id',x.league_id,'request',p_request-'idempotency_key','actor',actor);
 fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'execution_plan_approve',k,fp);if retry is not null then return retry;end if;
 if x.status not in ('authority_ready','plan_ready','awaiting_execution_approval') or x.started_at is not null or x.committed_at is not null or x.cancelled_at is not null then raise exception 'execution is not in pre-approval state';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;if p.id is null then raise exception 'execution plan not found';end if;
 if p.plan_status<>'valid' or not p.executable or p.approved_for_execution or jsonb_array_length(p.blockers)<>0 or p.plan_version<>(p_request->>'execution_plan_version')::integer or p.plan_input_fingerprint is distinct from p_request->>'expected_plan_input_fingerprint' or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint' or p.operation_count<>(p_request->>'expected_operation_count')::integer then raise exception 'stale or ineligible execution plan';end if;
 ops:=(select coalesce(jsonb_agg(o->>'operation_fingerprint' order by ord),'[]') from jsonb_array_elements(p.ordered_operations) with ordinality q(o,ord));if jsonb_array_length(ops)<>p.operation_count or exists(select 1 from jsonb_array_elements_text(ops) f where f !~ '^[0-9a-f]{64}$') then raise exception 'invalid operation fingerprint sequence';end if;
 select * into s from public.rollover_dry_run_simulations where id=(p_request->>'simulation_id')::uuid and rollover_execution_id=x.id for update;if s.id is null or s.id<>p.simulation_id or s.simulation_version<>p.simulation_version or s.simulation_status<>'valid' or not s.valid or not s.executable or not s.plan_eligible or jsonb_array_length(s.blockers)<>0 then raise exception 'simulation is not approval eligible';end if;
 if s.input_fingerprint<>p.simulation_input_fingerprint or s.result_fingerprint<>p.simulation_result_fingerprint or s.preflight_fingerprint<>p.preflight_fingerprint or s.policy_fingerprint<>p.policy_fingerprint or s.owner_population_fingerprint<>p.owner_population_fingerprint or s.commissioner_population_fingerprint<>p.commissioner_population_fingerprint or s.authority_preparation_fingerprint<>p.authority_preparation_fingerprint then raise exception 'plan and simulation provenance mismatch';end if;
 if p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint' or s.result_fingerprint is distinct from p_request->>'expected_simulation_result_fingerprint' or s.input_fingerprint is distinct from p_request->>'expected_simulation_input_fingerprint' or s.preflight_fingerprint is distinct from p_request->>'expected_preflight_fingerprint' or s.policy_fingerprint is distinct from p_request->>'expected_policy_fingerprint' or s.owner_population_fingerprint is distinct from p_request->>'expected_owner_population_fingerprint' or s.commissioner_population_fingerprint is distinct from p_request->>'expected_commissioner_population_fingerprint' or s.authority_preparation_fingerprint is distinct from p_request->>'expected_authority_preparation_fingerprint' then raise exception 'stale approval evidence';end if;
 if x.policy_fingerprint<>p.policy_fingerprint or x.preflight_fingerprint<>p.preflight_fingerprint or x.decision_population_fingerprint<>p.owner_population_fingerprint then raise exception 'execution evidence changed';end if;
 if (select count(*) from public.rollover_authority_preparations ap where ap.rollover_execution_id=x.id and ap.authority_status='prepared' and ap.policy_fingerprint=p.policy_fingerprint and ap.owner_population_fingerprint=p.owner_population_fingerprint and ap.commissioner_population_fingerprint=p.commissioner_population_fingerprint)<>3 then raise exception 'authority preparations changed';end if;
 if exists(select 1 from public.rollover_execution_plan_approvals ca where ca.rollover_execution_id=x.id and ca.approval_status='approved' for update) or exists(select 1 from public.rollover_execution_locks cl where cl.rollover_execution_id=x.id and cl.status='active' for update) then raise exception 'current approval or active execution lock exists';end if;
 approval_version:=coalesce((select max(z.approval_version)+1 from public.rollover_execution_plan_approvals z where z.rollover_execution_id=x.id),1);
 fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','rollover-execution-approval-v1','execution_id',x.id,'league_id',x.league_id,'source_season',x.source_season,'target_season',x.target_season,'plan_id',p.id,'plan_version',p.plan_version,'plan_input_fingerprint',p.plan_input_fingerprint,'plan_fingerprint',p.plan_fingerprint,'simulation_id',s.id,'simulation_version',s.simulation_version,'simulation_input_fingerprint',s.input_fingerprint,'simulation_result_fingerprint',s.result_fingerprint,'preflight_fingerprint',s.preflight_fingerprint,'policy_fingerprint',s.policy_fingerprint,'owner_population_fingerprint',s.owner_population_fingerprint,'commissioner_population_fingerprint',s.commissioner_population_fingerprint,'authority_preparation_fingerprint',s.authority_preparation_fingerprint,'operation_count',p.operation_count,'operation_fingerprints',ops,'statement_code',p_request->>'approval_statement_code','statement_version',1,'statement',btrim(p_request->>'approval_statement'),'approval_version',approval_version,'metadata',coalesce(p_request->'material_metadata','{}')));
 insert into public.rollover_execution_plan_approvals(rollover_execution_id,league_id,source_season,target_season,execution_plan_id,execution_plan_version,simulation_id,simulation_version,approval_version,approval_status,execution_status_at_approval,plan_status_at_approval,plan_input_fingerprint,plan_fingerprint,simulation_input_fingerprint,simulation_result_fingerprint,preflight_fingerprint,policy_fingerprint,owner_population_fingerprint,commissioner_population_fingerprint,authority_preparation_fingerprint,operation_count,operation_fingerprints,approval_fingerprint,approval_statement_code,approval_statement_version,approval_statement,approved_by,metadata) values(x.id,x.league_id,x.source_season,x.target_season,p.id,p.plan_version,s.id,s.simulation_version,approval_version,'approved',x.status,p.plan_status,p.plan_input_fingerprint,p.plan_fingerprint,s.input_fingerprint,s.result_fingerprint,s.preflight_fingerprint,s.policy_fingerprint,s.owner_population_fingerprint,s.commissioner_population_fingerprint,s.authority_preparation_fingerprint,p.operation_count,ops,fp,p_request->>'approval_statement_code',1,btrim(p_request->>'approval_statement'),actor,coalesce(p_request->'material_metadata','{}')) returning * into a;
 update public.rollover_execution_plans set plan_status='approved_for_execution',approved_for_execution=true,approved_by=actor,approved_at=clock_timestamp() where id=p.id;
 insert into public.rollover_execution_locks(rollover_execution_id,league_id,source_season,target_season,execution_plan_id,execution_plan_version,approval_id,lock_type,lock_scope,status,plan_fingerprint,simulation_result_fingerprint,policy_fingerprint,authority_preparation_fingerprint,acquired_at,acquired_by,lock_token,reason,metadata) values(x.id,x.league_id,x.source_season,x.target_season,p.id,p.plan_version,a.id,'cutover','rollover_global','active',p.plan_fingerprint,s.result_fingerprint,s.policy_fingerprint,s.authority_preparation_fingerprint,clock_timestamp(),actor,'cutover:'||a.id::text,'approved immutable execution plan',coalesce(p_request->'material_metadata','{}')) returning * into l;
 update public.rollover_executions set status='execution_ready',approval_status='approved',approved_by=actor,approved_at=clock_timestamp(),execution_plan_fingerprint=p.plan_fingerprint where id=x.id;
 result:=jsonb_build_object('approval',to_jsonb(a),'lock',to_jsonb(l),'plan_status','approved_for_execution','execution_status','execution_ready','operations_executed',0);
 return public.record_rollover_operation(x.league_id,x.id,'execution_plan_approve',k,public.rollover_material_fingerprint(material),actor,'authenticated_commissioner',a.id,result,'{}');
end $$;

create or replace function public.revoke_rollover_execution_plan_approval_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;p public.rollover_execution_plans%rowtype;a public.rollover_execution_plan_approvals%rowtype;l public.rollover_execution_locks%rowtype;k text;material jsonb;fp text;retry jsonb;result jsonb;
begin
 if p_request?'actor_user_id' or p_request?'requested_by' or p_request?'revoked_by' then raise exception 'actor spoofing forbidden';end if;k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'idempotency key and reason required';end if;
 perform pg_advisory_xact_lock(hashtextextended(p_request->>'rollover_execution_id',0));select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 material:=jsonb_build_object('operation','execution_plan_approval_revoke','execution_id',x.id,'approval_id',p_request->>'approval_id','expected_approval_fingerprint',p_request->>'expected_approval_fingerprint','expected_plan_fingerprint',p_request->>'expected_plan_fingerprint','expected_simulation_result_fingerprint',p_request->>'expected_simulation_result_fingerprint','reason',p_request->>'reason','metadata',coalesce(p_request->'material_metadata','{}'),'actor',actor);fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'execution_plan_approval_revoke',k,fp);if retry is not null then return retry;end if;
 if x.status<>'execution_ready' or x.started_at is not null or x.committed_at is not null then raise exception 'approval cannot be revoked after execution start';end if;
 select * into a from public.rollover_execution_plan_approvals where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;if a.id is null or a.approval_status<>'approved' or a.approval_fingerprint<>p_request->>'expected_approval_fingerprint' or a.plan_fingerprint<>p_request->>'expected_plan_fingerprint' or a.simulation_result_fingerprint<>p_request->>'expected_simulation_result_fingerprint' then raise exception 'stale approval evidence';end if;
 select * into p from public.rollover_execution_plans where id=a.execution_plan_id for update;select * into l from public.rollover_execution_locks where approval_id=a.id and status='active' for update;if p.id is null or p.plan_status<>'approved_for_execution' or l.id is null then raise exception 'approved plan or cutover lock missing';end if;
 update public.rollover_execution_plan_approvals set approval_status='revoked',revoked_at=clock_timestamp(),revoked_by=actor,revocation_reason=btrim(p_request->>'reason') where id=a.id returning * into a;
 update public.rollover_execution_locks set status='released',released_at=clock_timestamp(),released_by=actor,release_reason=btrim(p_request->>'reason') where id=l.id returning * into l;
 update public.rollover_execution_plans set plan_status='valid',approved_for_execution=false,approved_by=null,approved_at=null where id=p.id;
 update public.rollover_executions set status='authority_ready',approval_status='pending',approved_by=null,approved_at=null,execution_plan_fingerprint=null where id=x.id;
 result:=jsonb_build_object('approval',to_jsonb(a),'lock',to_jsonb(l),'plan_status','valid','execution_status','authority_ready','operations_executed',0);return public.record_rollover_operation(x.league_id,x.id,'execution_plan_approval_revoke',k,fp,actor,'authenticated_commissioner',a.id,result,'{}');
end $$;

revoke all on function public.persist_rollover_execution_plan_approval_service(jsonb),public.approve_rollover_execution_plan_authenticated(jsonb),public.revoke_rollover_execution_plan_approval_authenticated(jsonb),public.phase3b5h_execution_not_locked(uuid) from public,anon,authenticated,service_role;
grant execute on function public.approve_rollover_execution_plan_authenticated(jsonb),public.revoke_rollover_execution_plan_approval_authenticated(jsonb) to authenticated;
grant execute on function public.persist_rollover_execution_plan_approval_service(jsonb),public.phase3b5h_execution_not_locked(uuid) to service_role;
comment on table public.rollover_execution_plan_approvals is 'Immutable commissioner authorization for one exact plan; approval performs no rollover operation.';
commit;
