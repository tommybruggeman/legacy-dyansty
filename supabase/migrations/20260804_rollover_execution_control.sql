-- Phase 3B.5B: durable rollover execution control only.
-- Schema only: creates no execution, decision, review, plan, lock, validation,
-- authority, notice, deadline, roster, contract, cap, season, or publication data.

create table if not exists public.rollover_executions (
 id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id),
 source_season integer not null, target_season integer not null, policy_id uuid not null references public.league_rollover_policies(id),
 policy_fingerprint text not null, version integer not null default 1 check(version>0),
 status text not null check(status in ('draft','preflight_ready','notice_open','decision_window_open','decision_window_closed','authority_initializing','authority_ready','plan_ready','awaiting_execution_approval','execution_ready','executing','committed','validating','completed','failed_precommit','failed_postcommit_validation','cancelled')),
 approval_status text not null default 'not_required' check(approval_status in ('not_required','pending','approved','rejected','superseded')),
 notice_timestamp timestamptz, owner_deadline timestamptz, decision_population_fingerprint text, preflight_fingerprint text,
 execution_plan_fingerprint text, before_state_fingerprint text, planned_after_state_fingerprint text, actual_after_state_fingerprint text, execution_hash text,
 approved_by uuid references auth.users(id), approved_at timestamptz, started_at timestamptz, committed_at timestamptz, validated_at timestamptz, completed_at timestamptz, cancelled_at timestamptz,
 failure_code text, failure_stage text, failure_details jsonb not null default '{}'::jsonb, retry_count integer not null default 0 check(retry_count>=0),
 warnings jsonb not null default '[]'::jsonb, metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 check(target_season=source_season+1), check(owner_deadline is null or notice_timestamp is not null), check(owner_deadline is null or owner_deadline>notice_timestamp),
 check((approval_status='approved' and approved_by is not null and approved_at is not null) or (approval_status<>'approved' and approved_by is null and approved_at is null)),
 check(committed_at is null or (started_at is not null and committed_at>=started_at)), check(validated_at is null or (committed_at is not null and validated_at>=committed_at)), check(completed_at is null or (committed_at is not null and validated_at is not null and completed_at>=validated_at)),
 check(cancelled_at is null or (committed_at is null and completed_at is null)),
 check((status='completed')=(completed_at is not null)), check(status<>'cancelled' or cancelled_at is not null),
 check(status not in ('failed_precommit','failed_postcommit_validation') or (failure_code is not null and failure_stage is not null)),
 unique(league_id,source_season,target_season,version), unique(execution_hash)
);
create unique index if not exists rollover_executions_one_boundary_uidx on public.rollover_executions(league_id,source_season,target_season) where status<>'cancelled';
create index if not exists rollover_executions_league_status_idx on public.rollover_executions(league_id,status,target_season);

create table if not exists public.rollover_owner_decisions (
 id uuid primary key default gen_random_uuid(), rollover_execution_id uuid not null references public.rollover_executions(id), league_id uuid not null references public.leagues(id), source_season integer not null, target_season integer not null,
 league_team_id uuid not null references public.league_teams(id), player_id text not null references public.player_universe(sleeper_id), agreement_id uuid not null references public.contract_agreements(id),
 initial_roster_status text, initial_roster_slot text, decision_status text not null check(decision_status in ('waiting_for_owner','recontract_submitted','recontract_invalid','recontract_validated','decline_submitted','commissioner_review_requested','no_response','planned_retention','planned_release','blocked','execution_ready','executed_retained','executed_released','commissioner_hold','cancelled')),
 owner_choice text check(owner_choice is null or owner_choice in ('recontract','decline','commissioner_review')), submitted_by uuid references auth.users(id), submitted_at timestamptz, locked_at timestamptz, deadline timestamptz,
 recontract_agreement_id uuid references public.contract_agreements(id), recontract_event_id uuid references public.contract_events(id), planned_outcome text,
 execution_status text not null default 'pending' check(execution_status in ('pending','blocked','ready','executing','executed','cancelled')), executed_at timestamptz,
 evidence jsonb not null default '{}'::jsonb, warnings jsonb not null default '[]'::jsonb, metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 check(target_season=source_season+1), check(decision_status not in ('recontract_validated','planned_retention') or (recontract_agreement_id is not null and recontract_event_id is not null)),
 check((owner_choice is distinct from 'recontract' or decision_status in ('recontract_submitted','recontract_invalid','recontract_validated','planned_retention')) and (decision_status not in ('recontract_submitted','recontract_invalid','recontract_validated','planned_retention') or owner_choice='recontract')),
 check(decision_status not in ('decline_submitted') or owner_choice='decline'), check(decision_status<>'commissioner_review_requested' or owner_choice='commissioner_review'),
 check(decision_status not in ('decline_submitted','planned_release','no_response') or (recontract_agreement_id is null and recontract_event_id is null)),
 check(decision_status not in ('executed_retained','executed_released','commissioner_hold') or executed_at is not null),
 check(execution_status<>'executed' or executed_at is not null), unique(rollover_execution_id,agreement_id), unique(rollover_execution_id,player_id,agreement_id)
);
create index if not exists rollover_owner_decisions_execution_status_idx on public.rollover_owner_decisions(rollover_execution_id,decision_status,execution_status);
create index if not exists rollover_owner_decisions_team_idx on public.rollover_owner_decisions(league_id,league_team_id,deadline);

create table if not exists public.rollover_owner_decision_revisions (
 id uuid primary key default gen_random_uuid(), owner_decision_id uuid not null references public.rollover_owner_decisions(id), rollover_execution_id uuid not null references public.rollover_executions(id),
 revision_number integer not null check(revision_number>0), prior_status text, new_status text not null, prior_choice text, new_choice text, changed_by uuid references auth.users(id), changed_at timestamptz not null default now(), reason text,
 evidence jsonb not null default '{}'::jsonb, request_id text, idempotency_key text not null unique, metadata jsonb not null default '{}'::jsonb, unique(owner_decision_id,revision_number)
);
create index if not exists rollover_owner_revisions_execution_idx on public.rollover_owner_decision_revisions(rollover_execution_id,changed_at);

create table if not exists public.rollover_commissioner_reviews (
 id uuid primary key default gen_random_uuid(), rollover_execution_id uuid not null references public.rollover_executions(id), league_id uuid not null references public.leagues(id), source_season integer not null, target_season integer not null,
 player_id text not null references public.player_universe(sleeper_id), agreement_id uuid references public.contract_agreements(id), league_team_id uuid references public.league_teams(id),
 review_type text not null check(review_type in ('active_off_roster_liability','expired_unrostered_publication_candidate','owner_escalation','identity_conflict','waiver_conflict','rookie_draft_conflict','contract_conflict','commissioner_override')),
 review_status text not null check(review_status in ('review_required','evidence_incomplete','decision_pending','retain_liability','approve_release','approve_publication_hold','block_publication','approve_termination','action_validated','execution_ready','executed','cancelled')),
 proposed_action text, approved_action text, decision_by uuid references auth.users(id), decision_at timestamptz, evidence_complete boolean not null default false, action_validated boolean not null default false,
 execution_status text not null default 'pending' check(execution_status in ('pending','blocked','ready','executing','executed','cancelled')), executed_at timestamptz,
 qualifying_termination_event_id uuid references public.contract_events(id), planned_dead_cap_amount numeric(12,2) check(planned_dead_cap_amount is null or planned_dead_cap_amount>=0),
 evidence jsonb not null default '{}'::jsonb, blockers jsonb not null default '[]'::jsonb, warnings jsonb not null default '[]'::jsonb, metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 check(target_season=source_season+1), check(review_status<>'approve_termination' or (evidence_complete and action_validated)),
 check(review_status not in ('retain_liability','approve_release','approve_publication_hold','block_publication','approve_termination','action_validated','execution_ready','executed') or approved_action is not null),
 check(coalesce(planned_dead_cap_amount,0)=0 or qualifying_termination_event_id is not null), check(review_status<>'executed' or executed_at is not null),
 check(execution_status<>'executed' or executed_at is not null),
 check(review_type<>'active_off_roster_liability' or approved_action is distinct from 'publish'), unique(rollover_execution_id,player_id,review_type)
);
create index if not exists rollover_reviews_execution_status_idx on public.rollover_commissioner_reviews(rollover_execution_id,review_status,execution_status);

create table if not exists public.rollover_commissioner_review_events (
 id uuid primary key default gen_random_uuid(), commissioner_review_id uuid not null references public.rollover_commissioner_reviews(id), rollover_execution_id uuid not null references public.rollover_executions(id),
 event_type text not null, prior_status text, new_status text, performed_by uuid references auth.users(id), performed_at timestamptz not null default now(), reason text,
 evidence jsonb not null default '{}'::jsonb, idempotency_key text not null unique, metadata jsonb not null default '{}'::jsonb
);
create index if not exists rollover_review_events_execution_idx on public.rollover_commissioner_review_events(rollover_execution_id,performed_at);

create table if not exists public.rollover_execution_plans (
 id uuid primary key default gen_random_uuid(), rollover_execution_id uuid not null references public.rollover_executions(id), league_id uuid not null references public.leagues(id), source_season integer not null, target_season integer not null,
 plan_version integer not null check(plan_version>0), status text not null check(status in ('draft','invalid','ready','approved','superseded','executed')),
 policy_fingerprint text not null, decision_population_fingerprint text not null, authority_fingerprint text not null, preflight_fingerprint text not null, execution_plan_fingerprint text not null, planned_after_state_fingerprint text not null,
 plan_payload jsonb not null, blockers jsonb not null default '[]'::jsonb, warnings jsonb not null default '[]'::jsonb, generated_at timestamptz not null default now(), generated_by uuid references auth.users(id), approved_by uuid references auth.users(id), approved_at timestamptz, superseded_at timestamptz, metadata jsonb not null default '{}'::jsonb,
 check(target_season=source_season+1), check(status<>'approved' or (approved_by is not null and approved_at is not null)), unique(rollover_execution_id,plan_version), unique(rollover_execution_id,execution_plan_fingerprint)
);
create unique index if not exists rollover_plans_one_approved_uidx on public.rollover_execution_plans(rollover_execution_id) where status='approved';
create index if not exists rollover_plans_execution_status_idx on public.rollover_execution_plans(rollover_execution_id,status);

create table if not exists public.rollover_execution_locks (
 id uuid primary key default gen_random_uuid(), rollover_execution_id uuid not null references public.rollover_executions(id), league_id uuid not null references public.leagues(id),
 lock_scope text not null check(lock_scope in ('contracts','rosters','roster_sync','sleeper_sync','transactions','trades','free_agents','waivers','taxi','ir','cap_adjustments','league_rules','season_authority','commissioner_overrides','rollover_global')),
 status text not null check(status in ('pending','active','released','expired','cancelled')), acquired_at timestamptz, acquired_by uuid references auth.users(id), released_at timestamptz, expires_at timestamptz,
 lock_token text not null unique, reason text, metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 check(status<>'active' or (acquired_at is not null and acquired_by is not null)), check(status<>'released' or released_at is not null), check(released_at is null or acquired_at is not null), check(expires_at is null or acquired_at is null or expires_at>acquired_at)
);
create unique index if not exists rollover_locks_one_active_scope_uidx on public.rollover_execution_locks(league_id,lock_scope) where status='active';
create unique index if not exists rollover_locks_one_active_global_uidx on public.rollover_execution_locks(league_id) where status='active' and lock_scope='rollover_global';
create index if not exists rollover_locks_execution_status_idx on public.rollover_execution_locks(rollover_execution_id,status);

create table if not exists public.rollover_validation_results (
 id uuid primary key default gen_random_uuid(), rollover_execution_id uuid not null references public.rollover_executions(id), execution_plan_id uuid references public.rollover_execution_plans(id),
 validation_run_type text not null check(validation_run_type in ('dry_run','precommit','postcommit','visible_cutover')), validation_status text not null check(validation_status in ('pending','passed','failed','warning','blocked')),
 invariant_name text not null check(length(btrim(invariant_name))>0), invariant_domain text not null, severity text not null, passed boolean not null, expected jsonb, actual jsonb, details jsonb not null default '{}'::jsonb,
 evidence_fingerprint text, validated_at timestamptz not null default now(), validator_version text not null, metadata jsonb not null default '{}'::jsonb
);
create index if not exists rollover_validation_execution_run_idx on public.rollover_validation_results(rollover_execution_id,validation_run_type,validation_status);
create index if not exists rollover_validation_invariant_idx on public.rollover_validation_results(invariant_name,validated_at);

create or replace function public.validate_rollover_execution_policy_boundary() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare p public.league_rollover_policies%rowtype; begin select * into p from public.league_rollover_policies where id=new.policy_id;
 if p.id is null or p.league_id<>new.league_id or p.source_season<>new.source_season or p.target_season<>new.target_season or p.fingerprint<>new.policy_fingerprint or p.status not in ('approved','active') then raise exception 'Rollover execution policy boundary or fingerprint mismatch.'; end if; return new; end $$;

create or replace function public.validate_rollover_execution_transition() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare ok boolean:=false; begin
 if old.status='completed' then raise exception 'Completed rollover executions are immutable.'; end if;
 if old.status='cancelled' and new.status<>old.status then raise exception 'Cancelled rollover executions cannot resume.'; end if;
 if old.status in ('committed','validating','failed_postcommit_validation') and new.status='cancelled' then raise exception 'Committed rollover executions cannot be cancelled.'; end if;
 ok:=new.status=old.status or (old.status='draft' and new.status in ('preflight_ready','cancelled','failed_precommit')) or (old.status='preflight_ready' and new.status in ('notice_open','cancelled','failed_precommit')) or (old.status='notice_open' and new.status in ('decision_window_open','cancelled','failed_precommit')) or (old.status='decision_window_open' and new.status in ('decision_window_closed','cancelled','failed_precommit')) or (old.status='decision_window_closed' and new.status in ('authority_initializing','cancelled','failed_precommit')) or (old.status='authority_initializing' and new.status in ('authority_ready','cancelled','failed_precommit')) or (old.status='authority_ready' and new.status in ('plan_ready','cancelled','failed_precommit')) or (old.status='plan_ready' and new.status in ('awaiting_execution_approval','cancelled','failed_precommit')) or (old.status='awaiting_execution_approval' and new.status in ('execution_ready','plan_ready','cancelled','failed_precommit')) or (old.status='execution_ready' and new.status in ('executing','plan_ready','failed_precommit')) or (old.status='executing' and new.status in ('committed','failed_precommit')) or (old.status='committed' and new.status='validating') or (old.status='validating' and new.status in ('completed','failed_postcommit_validation')) or (old.status='failed_postcommit_validation' and new.status='validating');
 if not ok then raise exception 'Illegal rollover execution transition: % -> %',old.status,new.status; end if; new.updated_at=now(); return new; end $$;

create or replace function public.validate_rollover_owner_decision() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype; ok boolean:=false; begin select * into x from public.rollover_executions where id=new.rollover_execution_id;
 if x.id is null or x.league_id<>new.league_id or x.source_season<>new.source_season or x.target_season<>new.target_season then raise exception 'Owner decision boundary mismatch.'; end if;
 if new.deadline is distinct from x.owner_deadline then raise exception 'Owner decision deadline must match parent execution.'; end if;
 if new.decision_status='no_response' and (x.owner_deadline is null or now()<x.owner_deadline) then raise exception 'No-response cannot be assigned before deadline.'; end if;
 if tg_op='UPDATE' then
 if old.decision_status in ('executed_retained','commissioner_hold') then raise exception 'Executed owner outcome is immutable.'; end if;
  if old.decision_status='commissioner_review_requested' and new.decision_status='no_response' then raise exception 'Commissioner review cannot become no-response.'; end if;
  ok:=new.decision_status=old.decision_status or (old.decision_status='waiting_for_owner' and new.decision_status in ('recontract_submitted','decline_submitted','commissioner_review_requested','no_response','cancelled')) or (old.decision_status='recontract_submitted' and new.decision_status in ('recontract_invalid','recontract_validated','cancelled')) or (old.decision_status='recontract_invalid' and new.decision_status in ('waiting_for_owner','blocked','cancelled')) or (old.decision_status='recontract_validated' and new.decision_status in ('planned_retention','blocked','cancelled')) or (old.decision_status='decline_submitted' and new.decision_status in ('planned_release','cancelled')) or (old.decision_status='no_response' and new.decision_status='planned_release') or (old.decision_status in ('planned_retention','planned_release') and new.decision_status in ('execution_ready','blocked','cancelled')) or (old.decision_status='execution_ready' and new.decision_status in ('executed_retained','executed_released','blocked')) or (old.decision_status='executed_released' and new.decision_status='commissioner_hold') or (old.decision_status='blocked' and new.decision_status in ('execution_ready','cancelled'));
  if not ok then raise exception 'Illegal owner-decision transition: % -> %',old.decision_status,new.decision_status; end if;
 end if; new.updated_at=now(); return new; end $$;

create or replace function public.validate_rollover_review_transition() returns trigger language plpgsql set search_path=pg_catalog,public as $$ declare ok boolean:=false; begin
 if tg_op='UPDATE' and old.review_status='executed' then raise exception 'Executed commissioner review is immutable.'; end if;
 if tg_op='UPDATE' then ok:=new.review_status=old.review_status or (old.review_status='review_required' and new.review_status in ('evidence_incomplete','decision_pending','cancelled')) or (old.review_status='evidence_incomplete' and new.review_status in ('decision_pending','cancelled')) or (old.review_status='decision_pending' and new.review_status in ('retain_liability','approve_release','approve_publication_hold','block_publication','approve_termination','cancelled')) or (old.review_status in ('retain_liability','approve_release','approve_publication_hold','block_publication','approve_termination') and new.review_status in ('action_validated','cancelled')) or (old.review_status='action_validated' and new.review_status in ('execution_ready','decision_pending','cancelled')) or (old.review_status='execution_ready' and new.review_status='executed'); if not ok then raise exception 'Illegal commissioner-review transition: % -> %',old.review_status,new.review_status; end if; end if;
 if new.review_status='approve_termination' and not(new.evidence_complete and new.action_validated) then raise exception 'Termination requires complete validated evidence.'; end if;
 new.updated_at=now(); return new; end $$;
create or replace function public.protect_rollover_plan() returns trigger language plpgsql set search_path=pg_catalog,public as $$ declare x public.rollover_executions%rowtype; begin
 if tg_op='DELETE' and old.status in ('approved','executed') then raise exception 'Approved/executed rollover plans are immutable.'; end if;
 if tg_op='UPDATE' and old.status='executed' then raise exception 'Executed rollover plans are immutable.'; end if;
 if tg_op='UPDATE' and old.status='approved' and not(new.status='superseded' and new.superseded_at is not null and new.plan_payload=old.plan_payload and new.execution_plan_fingerprint=old.execution_plan_fingerprint) then raise exception 'Approved plan payload is immutable; only explicit supersession is allowed.'; end if;
 if tg_op<>'DELETE' then select * into x from public.rollover_executions where id=new.rollover_execution_id; if x.id is null or x.league_id<>new.league_id or x.source_season<>new.source_season or x.target_season<>new.target_season or x.policy_fingerprint<>new.policy_fingerprint then raise exception 'Execution-plan boundary or policy mismatch.'; end if; if new.status='approved' and x.execution_plan_fingerprint is distinct from new.execution_plan_fingerprint then raise exception 'Approved plan fingerprint must match execution ledger.'; end if; end if;
 return case when tg_op='DELETE' then old else new end; end $$;
create or replace function public.validate_rollover_lock_transition() returns trigger language plpgsql set search_path=pg_catalog,public as $$ begin
 if tg_op='UPDATE' and not(new.status=old.status or (old.status='pending' and new.status in ('active','cancelled')) or (old.status='active' and new.status in ('released','expired')) ) then raise exception 'Illegal rollover lock transition.'; end if;
 if new.status='active' and (new.acquired_at is null or new.acquired_by is null) then raise exception 'Active lock requires acquisition data.'; end if;
 if new.status='released' and new.released_at is null then raise exception 'Released lock requires released_at.'; end if; new.updated_at=now(); return new; end $$;
create or replace function public.reject_rollover_append_only_mutation() returns trigger language plpgsql set search_path=pg_catalog,public as $$ begin raise exception 'Rollover audit and validation rows are append-only.'; end $$;

drop trigger if exists rollover_execution_policy_boundary on public.rollover_executions; create trigger rollover_execution_policy_boundary before insert or update on public.rollover_executions for each row execute function public.validate_rollover_execution_policy_boundary();
drop trigger if exists rollover_execution_transition on public.rollover_executions; create trigger rollover_execution_transition before update on public.rollover_executions for each row execute function public.validate_rollover_execution_transition();
drop trigger if exists rollover_owner_decision_guard on public.rollover_owner_decisions; create trigger rollover_owner_decision_guard before insert or update on public.rollover_owner_decisions for each row execute function public.validate_rollover_owner_decision();
drop trigger if exists rollover_review_guard on public.rollover_commissioner_reviews; create trigger rollover_review_guard before insert or update on public.rollover_commissioner_reviews for each row execute function public.validate_rollover_review_transition();
drop trigger if exists rollover_plan_guard on public.rollover_execution_plans; create trigger rollover_plan_guard before update or delete on public.rollover_execution_plans for each row execute function public.protect_rollover_plan();
drop trigger if exists rollover_lock_guard on public.rollover_execution_locks; create trigger rollover_lock_guard before insert or update on public.rollover_execution_locks for each row execute function public.validate_rollover_lock_transition();
drop trigger if exists rollover_owner_revision_append_only on public.rollover_owner_decision_revisions; create trigger rollover_owner_revision_append_only before update or delete on public.rollover_owner_decision_revisions for each row execute function public.reject_rollover_append_only_mutation();
drop trigger if exists rollover_review_event_append_only on public.rollover_commissioner_review_events; create trigger rollover_review_event_append_only before update or delete on public.rollover_commissioner_review_events for each row execute function public.reject_rollover_append_only_mutation();
drop trigger if exists rollover_validation_append_only on public.rollover_validation_results; create trigger rollover_validation_append_only before update or delete on public.rollover_validation_results for each row execute function public.reject_rollover_append_only_mutation();

do $$ declare t text; begin
 foreach t in array array['rollover_executions','rollover_owner_decisions','rollover_owner_decision_revisions','rollover_commissioner_reviews','rollover_commissioner_review_events','rollover_execution_plans','rollover_execution_locks','rollover_validation_results'] loop
  execute format('alter table public.%I enable row level security',t); execute format('revoke all on table public.%I from anon,authenticated',t); execute format('grant all on table public.%I to service_role',t); execute format('grant select on table public.%I to authenticated',t);
  execute format('drop policy if exists %I on public.%I',t||'_league_member_select',t);
  if t in ('rollover_owner_decision_revisions','rollover_commissioner_review_events','rollover_execution_plans','rollover_execution_locks','rollover_validation_results') then
   execute format('create policy %I on public.%I for select to authenticated using (exists(select 1 from public.rollover_executions x join public.league_memberships m on m.league_id=x.league_id and m.user_id=auth.uid() where x.id=%I.rollover_execution_id))',t||'_league_member_select',t,t);
  else execute format('create policy %I on public.%I for select to authenticated using (exists(select 1 from public.league_memberships m where m.league_id=%I.league_id and m.user_id=auth.uid()))',t||'_league_member_select',t,t); end if;
 end loop; end $$;

comment on table public.rollover_executions is 'Master control ledger for a future league rollover boundary; schema creation starts no execution.';
comment on table public.rollover_owner_decisions is 'Current owner-decision state; future writes must use validated server-side operations.';
comment on table public.rollover_execution_plans is 'Versioned deterministic rollover plans; approved versions are immutable.';
