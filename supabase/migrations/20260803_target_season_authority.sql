-- Phase 3B.4E: commissioner policy and target-season authority.
-- Schema only: no policy approval, backfill, publication, dead-cap initialization,
-- cap activation, roster action, or rollover execution occurs in this migration.

create table if not exists public.league_rollover_policies (
  id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id),
  source_season integer not null, target_season integer not null, version integer not null check(version>0),
  status text not null check(status in ('draft','pending_approval','approved','active','superseded')),
  rostered_expired_policy text, off_roster_active_policy text, free_agent_publication_policy text,
  waiver_policy text, extension_deadline timestamptz, taxi_policy text, ir_policy text,
  dead_cap_policy text, early_termination_policy text, cap_adjustment_policy text, draft_rookie_policy text,
  effective_at timestamptz, created_by uuid references auth.users(id), approved_by uuid references auth.users(id),
  approved_at timestamptz, metadata jsonb not null default '{}'::jsonb, fingerprint text not null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(league_id,source_season,target_season,version), unique(league_id,target_season,fingerprint),
  check(target_season=source_season+1),
  check(status not in ('approved','active') or (approved_by is not null and approved_at is not null))
);
create unique index if not exists league_rollover_policies_one_active_uidx on public.league_rollover_policies(league_id,target_season) where status='active';

create table if not exists public.free_agent_publications (
  id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id),
  player_id text not null references public.player_universe(sleeper_id), sleeper_player_id text not null,
  season integer not null, publication_status text not null check(publication_status in ('pending','published','unpublished','commissioner_hold','waiver_locked','draft_locked','rookie_locked','ineligible')),
  acquisition_status text not null check(acquisition_status in ('eligible','ineligible','waiver_required','commissioner_approval_required','contract_conflict','unknown')),
  publication_reason text not null, source_contract_agreement_id uuid references public.contract_agreements(id),
  source_roster_event_id text, source_transaction_id text, waiver_status text not null default 'none',
  commissioner_hold boolean not null default false, available_at timestamptz, waiver_expires_at timestamptz,
  published_at timestamptz, unpublished_at timestamptz, created_by uuid references auth.users(id),
  metadata jsonb not null default '{}'::jsonb, source_fingerprint text not null, idempotency_key text not null unique,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(league_id,player_id,season), check(player_id=sleeper_player_id),
  check(acquisition_status<>'eligible' or publication_status='published'),
  check(acquisition_status<>'eligible' or not commissioner_hold)
);
create index if not exists free_agent_publications_lookup_idx on public.free_agent_publications(league_id,season,publication_status,acquisition_status);

create table if not exists public.dead_cap_obligations (
  id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id),
  league_team_id uuid not null references public.league_teams(id), player_id text references public.player_universe(sleeper_id),
  contract_agreement_id uuid references public.contract_agreements(id), season integer not null,
  amount numeric(12,2) not null check(amount>=0), source_event_id uuid references public.contract_events(id),
  termination_type text not null check(termination_type in ('early_termination','commissioner_adjustment')),
  calculation_rule text not null, status text not null check(status in ('planned','active','satisfied','voided')),
  created_by uuid references auth.users(id), metadata jsonb not null default '{}'::jsonb,
  idempotency_key text not null unique, created_at timestamptz not null default now(),
  unique(league_id,league_team_id,player_id,contract_agreement_id,season)
);
create index if not exists dead_cap_obligations_team_season_idx on public.dead_cap_obligations(league_id,season,league_team_id,status);

create table if not exists public.dead_cap_season_authorities (
  id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id), season integer not null,
  status text not null check(status in ('uninitialized','initializing','initialized','validated','historical')),
  expected_team_count integer not null check(expected_team_count>0), completed_team_ids uuid[] not null default '{}',
  obligation_count integer not null default 0 check(obligation_count>=0), total_amount numeric(12,2) not null default 0 check(total_amount>=0),
  source_fingerprint text not null, idempotency_key text not null unique, initialized_at timestamptz,
  validated_at timestamptz, created_by uuid references auth.users(id), metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(), unique(league_id,season),
  check(cardinality(completed_team_ids)<=expected_team_count)
);

create table if not exists public.cap_season_authorities (
  id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id), season integer not null,
  status text not null check(status in ('uninitialized','projected','validated','authoritative','historical','blocked')),
  salary_cap_limit numeric(12,2) not null check(salary_cap_limit>=0), contract_source text not null,
  adjustment_source text not null, dead_cap_source text not null, expected_team_count integer not null check(expected_team_count>0),
  source_fingerprint text not null, readiness_fingerprint text not null, initialized_at timestamptz,
  validated_at timestamptz, activated_at timestamptz, created_by uuid references auth.users(id),
  metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(),
  unique(league_id,season), check(status<>'authoritative' or activated_at is not null)
);
create unique index if not exists cap_season_authorities_one_authoritative_uidx on public.cap_season_authorities(league_id) where status='authoritative';

create or replace function public.reject_activated_rollover_policy_mutation() returns trigger language plpgsql
set search_path=pg_catalog,public as $$ begin
  if old.status in ('approved','active','superseded') then raise exception 'Approved/active rollover policies are immutable; create a superseding version.'; end if;
  if tg_op='DELETE' then return old; end if;
  new.updated_at=now(); return new;
end $$;
drop trigger if exists league_rollover_policy_immutability on public.league_rollover_policies;
create trigger league_rollover_policy_immutability before update or delete on public.league_rollover_policies for each row execute function public.reject_activated_rollover_policy_mutation();

create or replace function public.validate_target_authority_status_transition() returns trigger language plpgsql
set search_path=pg_catalog,public as $$ begin
  if tg_table_name='free_agent_publications' and not (
    old.publication_status=new.publication_status or
    (old.publication_status='pending' and new.publication_status in ('published','unpublished','commissioner_hold','waiver_locked','draft_locked','rookie_locked','ineligible')) or
    (old.publication_status in ('published','commissioner_hold','waiver_locked') and new.publication_status in ('unpublished','commissioner_hold','waiver_locked','published'))
  ) then raise exception 'Invalid free-agent publication status transition.'; end if;
  if tg_table_name='dead_cap_season_authorities' and not (
    old.status=new.status or
    (old.status='uninitialized' and new.status='initializing') or
    (old.status='initializing' and new.status='initialized') or
    (old.status='initialized' and new.status='validated') or
    (old.status='validated' and new.status='historical')
  ) then raise exception 'Invalid dead-cap authority status transition.'; end if;
  if tg_table_name='cap_season_authorities' and not (
    old.status=new.status or
    (old.status='uninitialized' and new.status in ('projected','blocked')) or
    (old.status in ('projected','blocked') and new.status in ('projected','validated','blocked')) or
    (old.status='validated' and new.status in ('authoritative','blocked')) or
    (old.status='authoritative' and new.status='historical')
  ) then raise exception 'Invalid cap authority status transition.'; end if;
  new.updated_at=now(); return new;
end $$;
alter table public.dead_cap_season_authorities add column if not exists updated_at timestamptz not null default now();
alter table public.cap_season_authorities add column if not exists updated_at timestamptz not null default now();
drop trigger if exists free_agent_publication_status_transition on public.free_agent_publications;
create trigger free_agent_publication_status_transition before update on public.free_agent_publications for each row execute function public.validate_target_authority_status_transition();
drop trigger if exists dead_cap_authority_status_transition on public.dead_cap_season_authorities;
create trigger dead_cap_authority_status_transition before update on public.dead_cap_season_authorities for each row execute function public.validate_target_authority_status_transition();
drop trigger if exists cap_authority_status_transition on public.cap_season_authorities;
create trigger cap_authority_status_transition before update on public.cap_season_authorities for each row execute function public.validate_target_authority_status_transition();

do $$ declare t text; begin
  foreach t in array array['league_rollover_policies','free_agent_publications','dead_cap_obligations','dead_cap_season_authorities','cap_season_authorities'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on table public.%I from anon,authenticated',t);
    execute format('grant all on table public.%I to service_role',t);
    execute format('grant select on table public.%I to authenticated',t);
    execute format('drop policy if exists %I on public.%I',t||'_member_select',t);
    execute format('create policy %I on public.%I for select to authenticated using (exists (select 1 from public.league_memberships lm where lm.league_id=%I.league_id and lm.user_id=auth.uid()))',t||'_member_select',t,t);
  end loop;
end $$;

-- Validation queries after manual deployment (must return zero rows):
-- select league_id,target_season,count(*) from public.league_rollover_policies where status='active' group by 1,2 having count(*)>1;
-- select league_id,player_id,season,count(*) from public.free_agent_publications group by 1,2,3 having count(*)>1;
-- select league_id,season from public.dead_cap_season_authorities where status in ('initialized','validated') and cardinality(completed_team_ids)<>expected_team_count;
-- select league_id,season from public.cap_season_authorities where status='authoritative' and activated_at is null;
