-- Narrow Phase 3A source correction. This does not deploy the normalized contract model.
-- The transaction archives complete source evidence before removing two proven erroneous duplicates.

create table if not exists public.contract_source_corrections (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id),
  sleeper_player_id text not null,
  affected_row_id uuid not null,
  canonical_surviving_row_id uuid not null,
  reason text not null,
  evidence jsonb not null,
  actor text not null,
  previous_values jsonb not null,
  resulting_values jsonb not null,
  idempotency_key text not null unique,
  corrected_at timestamptz not null default now()
);

alter table public.contract_source_corrections enable row level security;
revoke all on table public.contract_source_corrections from anon, authenticated;
grant select, insert on table public.contract_source_corrections to service_role;

do $$
declare
  v_league uuid := '9838a0a1-97c6-4cab-bb88-af177317abfe';
  v_bad public.contracts%rowtype;
  v_good public.contracts%rowtype;
begin
  -- Luke Musgrave: identical material terms; preserve correctly spelled row.
  select * into v_good from public.contracts where id='e4f94d09-e310-4fbf-9552-d581ffe5ea8f' for update;
  select * into v_bad from public.contracts where id='9e173761-1589-4c63-9321-013ab9ad2b6c' for update;
  if v_good.id is not null and v_bad.id is not null then
    if v_good.league_id<>v_league or v_bad.league_id<>v_league or v_good.sleeper_player_id<>'9481' or v_bad.sleeper_player_id<>'9481'
       or v_good.owner_id is distinct from v_bad.owner_id or v_good.salary is distinct from v_bad.salary
       or v_good.contract_years_left is distinct from v_bad.contract_years_left
       or v_good.contract_total_years is distinct from v_bad.contract_total_years then
      raise exception 'Luke Musgrave correction preconditions no longer match reviewed evidence.';
    end if;
    insert into public.contract_source_corrections
      (league_id,sleeper_player_id,affected_row_id,canonical_surviving_row_id,reason,evidence,actor,previous_values,resulting_values,idempotency_key)
    values (v_league,'9481',v_bad.id,v_good.id,'Typo-only duplicate of the correctly spelled Luke Musgrave source row.',
      jsonb_build_object('historical_roster_team_id','02b004e4-8053-428c-bb9c-a65165259d13','sleeper_roster_id',4,'material_terms_identical',true),
      'phase3a_contract_reconciliation',to_jsonb(v_bad),to_jsonb(v_good),'contract-source-correction:luke-musgrave-9481:v1')
    on conflict (idempotency_key) do nothing;
    delete from public.contracts where id=v_bad.id;
  end if;

  -- Tyler Allgeier: every authoritative roster source establishes Nando Munoz / roster 8.
  select * into v_good from public.contracts where id='b8ace9db-caee-43a7-97b5-574aa04fa41d' for update;
  select * into v_bad from public.contracts where id='1ce6b9e1-26bf-4cb8-9929-ad822918d752' for update;
  if v_good.id is not null and v_bad.id is not null then
    if v_good.league_id<>v_league or v_bad.league_id<>v_league or v_good.sleeper_player_id<>'8132' or v_bad.sleeper_player_id<>'8132'
       or v_good.owner_name<>'Nando Munoz' or v_good.salary is distinct from v_bad.salary
       or v_good.contract_years_left is distinct from v_bad.contract_years_left then
      raise exception 'Tyler Allgeier correction preconditions no longer match reviewed evidence.';
    end if;
    insert into public.contract_source_corrections
      (league_id,sleeper_player_id,affected_row_id,canonical_surviving_row_id,reason,evidence,actor,previous_values,resulting_values,idempotency_key)
    values (v_league,'8132',v_bad.id,v_good.id,'Stale conflicting owner; immutable 2025 snapshot, Sleeper roster, latest add, and canonical ownership establish Nando Munoz.',
      jsonb_build_object('historical_roster_team_id','b4c8e502-6fa7-4915-8eba-187a61f45bda','sleeper_roster_id',8,
        'latest_transaction_id','1266811864516407296-add-8132','canonical_current_owner','Nando Munoz','dead_cap_found',false,'cap_adjustment_found',false),
      'phase3a_contract_reconciliation',to_jsonb(v_bad),to_jsonb(v_good),'contract-source-correction:tyler-allgeier-8132:v1')
    on conflict (idempotency_key) do nothing;
    delete from public.contracts where id=v_bad.id;
  end if;
end $$;
