-- Phase 3A: normalized contract identity, seasonal obligations, and append-only events.
-- public.contracts remains the unchanged legacy source during parallel validation.

create table if not exists public.contract_agreements (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id),
  league_team_id uuid not null references public.league_teams(id),
  player_id text not null references public.player_universe(sleeper_id),
  sleeper_player_id text not null,
  contract_type text not null check (contract_type in ('rookie','veteran','franchise_tag','extension','unknown')),
  origin text not null check (origin in ('imported_initial_contract','signed','extended','commissioner_adjustment','unknown')),
  signed_season integer, start_season integer not null, end_season integer not null,
  status text not null check (status in ('active','scheduled','expired','released','voided','superseded')),
  parent_contract_id uuid references public.contract_agreements(id),
  superseded_by_contract_id uuid references public.contract_agreements(id),
  source_legacy_contract_id uuid unique references public.contracts(id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  check (start_season <= end_season)
);

create unique index if not exists contract_agreements_one_live_player_uidx
  on public.contract_agreements (league_id, player_id)
  where status in ('active','scheduled') and superseded_by_contract_id is null;

create table if not exists public.contract_seasons (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null references public.contract_agreements(id),
  league_season_id uuid not null references public.league_seasons(id),
  league_id uuid not null references public.leagues(id),
  league_team_id uuid not null references public.league_teams(id),
  player_id text not null references public.player_universe(sleeper_id),
  season integer not null, salary numeric(12,2) not null, guaranteed_salary numeric(12,2),
  cap_hit numeric(12,2) not null, roster_bonus numeric(12,2), dead_cap_if_released numeric(12,2),
  obligation_status text not null check (obligation_status in ('active','scheduled','satisfied','expired','voided','released','converted_to_dead_cap')),
  is_option_year boolean not null default false, option_type text,
  source text not null, source_legacy_contract_id uuid references public.contracts(id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique (contract_id, season), unique (contract_id, league_season_id),
  check (salary >= 0 and cap_hit >= 0 and coalesce(guaranteed_salary,0) >= 0 and coalesce(roster_bonus,0) >= 0 and coalesce(dead_cap_if_released,0) >= 0)
);

create table if not exists public.contract_events (
  id uuid primary key default gen_random_uuid(), contract_id uuid not null references public.contract_agreements(id),
  league_id uuid not null references public.leagues(id), league_team_id uuid not null references public.league_teams(id),
  player_id text not null references public.player_universe(sleeper_id),
  event_type text not null check (event_type in ('imported','signed','extended','restructured','salary_changed','option_exercised','option_declined','traded','released','expired','voided','superseded','dead_cap_created')),
  effective_season integer not null, effective_at timestamptz not null default now(), source text not null,
  actor_user_id uuid, related_transaction_id text, previous_values jsonb, new_values jsonb,
  metadata jsonb not null default '{}'::jsonb, idempotency_key text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.contract_backfill_executions (
  id uuid primary key default gen_random_uuid(), league_id uuid not null references public.leagues(id),
  source_season integer not null, idempotency_key text not null unique, source_fingerprint text not null,
  status text not null check (status in ('validating','validated','failed')),
  row_counts jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), completed_at timestamptz
);

create or replace function public.reject_contract_event_mutation() returns trigger language plpgsql
set search_path=pg_catalog,public as $$ begin raise exception 'Contract events are append-only.'; end $$;
drop trigger if exists contract_events_append_only on public.contract_events;
create trigger contract_events_append_only before update or delete on public.contract_events
for each row execute function public.reject_contract_event_mutation();

create or replace function public.reject_historical_contract_season_mutation() returns trigger language plpgsql
set search_path=pg_catalog,public as $$
declare v_status text;
begin
  select status into v_status from public.league_seasons where id=old.league_season_id;
  if v_status='completed' then raise exception 'Completed contract seasons are immutable.'; end if;
  raise exception 'Contract obligations require an audited contract-edit RPC.';
end $$;
drop trigger if exists contract_seasons_controlled_update on public.contract_seasons;
create trigger contract_seasons_controlled_update before update or delete on public.contract_seasons
for each row execute function public.reject_historical_contract_season_mutation();

create or replace function public.backfill_contract_model(p_plan jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare v_active public.league_seasons%rowtype; v_existing public.contract_backfill_executions%rowtype; v_counts jsonb;
begin
  select * into v_active from public.league_seasons where id=(p_plan->>'league_season_id')::uuid for share;
  if not found or v_active.league_id<>(p_plan->>'league_id')::uuid or not v_active.is_active
     or v_active.season<>(p_plan->>'source_season')::integer or v_active.season<>2025 then
    raise exception 'Contract backfill requires authoritative active 2025.';
  end if;
  select * into v_existing from public.contract_backfill_executions where idempotency_key=p_plan->>'idempotency_key';
  if found then
    if v_existing.source_fingerprint<>p_plan->>'source_fingerprint' then raise exception 'Contract source fingerprint conflict.'; end if;
    return jsonb_build_object('idempotent',true,'execution_id',v_existing.id,'row_counts',v_existing.row_counts);
  end if;
  if jsonb_array_length(p_plan->'agreements')<>211 or jsonb_array_length(p_plan->'events')<>211
     or jsonb_array_length(p_plan->'contract_seasons')<>335 then
    raise exception 'Reconciled initial contract backfill requires 211 agreements, 335 contract seasons, and 211 import events.';
  end if;
  if exists (select 1 from jsonb_array_elements(p_plan->'agreements') x
    left join public.league_teams t on t.id=(x->>'league_team_id')::uuid
    left join public.player_universe p on p.sleeper_id=x->>'player_id'
    left join public.contracts c on c.id=(x->>'source_legacy_contract_id')::uuid
    where t.id is null or t.league_id<>v_active.league_id or p.sleeper_id is null
       or c.id is null or c.league_id<>v_active.league_id) then
    raise exception 'Contract plan contains invalid league/team/player/source identity.';
  end if;
  if exists (select 1 from jsonb_array_elements(p_plan->'agreements') x group by x->>'player_id' having count(*)>1) then
    raise exception 'Contract plan contains overlapping current player obligations.';
  end if;
  insert into public.contract_backfill_executions(league_id,source_season,idempotency_key,source_fingerprint,status)
    values(v_active.league_id,2025,p_plan->>'idempotency_key',p_plan->>'source_fingerprint','validating');
  insert into public.league_seasons(league_id,season,sleeper_league_id,is_active,status)
    select v_active.league_id,(y.value)::integer,null,false,'scheduled'
    from jsonb_array_elements_text(coalesce(p_plan->'future_league_seasons','[]'::jsonb)) y
    on conflict (league_id,season) do nothing;
  update public.league_seasons future
    set previous_league_season_id=prior.id
    from public.league_seasons prior
    where future.league_id=v_active.league_id and future.season=2027
      and prior.league_id=future.league_id and prior.season=2026
      and future.previous_league_season_id is null;
  insert into public.contract_agreements
    (league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,signed_season,start_season,end_season,status,source_legacy_contract_id)
    select (x->>'league_id')::uuid,(x->>'league_team_id')::uuid,x->>'player_id',x->>'sleeper_player_id',x->>'contract_type',x->>'origin',nullif(x->>'signed_season','')::integer,(x->>'start_season')::integer,(x->>'end_season')::integer,x->>'status',(x->>'source_legacy_contract_id')::uuid
    from jsonb_array_elements(p_plan->'agreements') x;
  insert into public.contract_seasons
    (contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,cap_hit,obligation_status,is_option_year,source,source_legacy_contract_id)
    select a.id,ls.id,(x->>'league_id')::uuid,(x->>'league_team_id')::uuid,x->>'player_id',(x->>'season')::integer,(x->>'salary')::numeric,(x->>'cap_hit')::numeric,x->>'obligation_status',(x->>'is_option_year')::boolean,x->>'source',(x->>'source_legacy_contract_id')::uuid
    from jsonb_array_elements(p_plan->'contract_seasons') x
    join public.contract_agreements a on a.source_legacy_contract_id=(x->>'source_legacy_contract_id')::uuid
    join public.league_seasons ls on ls.league_id=a.league_id and ls.season=(x->>'season')::integer;
  insert into public.contract_events
    (contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,new_values,metadata,idempotency_key)
    select a.id,(x->>'league_id')::uuid,(x->>'league_team_id')::uuid,x->>'player_id',x->>'event_type',(x->>'effective_season')::integer,x->>'source',x->'new_values',x->'metadata',x->>'idempotency_key'
    from jsonb_array_elements(p_plan->'events') x join public.contract_agreements a on a.source_legacy_contract_id=replace(x->>'contract_key','legacy:','')::uuid;
  v_counts=jsonb_build_object('agreements',jsonb_array_length(p_plan->'agreements'),'contract_seasons',jsonb_array_length(p_plan->'contract_seasons'),'events',jsonb_array_length(p_plan->'events'));
  update public.contract_backfill_executions set status='validated',row_counts=v_counts,completed_at=now() where idempotency_key=p_plan->>'idempotency_key';
  return jsonb_build_object('idempotent',false,'status','validated','row_counts',v_counts);
end $$;

revoke all on function public.backfill_contract_model(jsonb) from public;
grant execute on function public.backfill_contract_model(jsonb) to service_role;
do $$ declare t text; begin
  foreach t in array array['contract_agreements','contract_seasons','contract_events','contract_backfill_executions'] loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on table public.%I from anon,authenticated',t);
    execute format('grant all on table public.%I to service_role',t);
  end loop;
end $$;
