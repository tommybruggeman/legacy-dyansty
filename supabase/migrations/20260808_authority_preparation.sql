begin;

create table if not exists public.rollover_authority_preparations (
  id uuid primary key default gen_random_uuid(),
  rollover_execution_id uuid not null references public.rollover_executions(id) on delete restrict,
  league_id uuid not null references public.leagues(id) on delete restrict,
  source_season integer not null,
  target_season integer not null,
  authority_type text not null check (authority_type in ('publication','dead_cap','salary_cap')),
  status text not null default 'prepared' check (status in ('prepared','blocked','superseded','cancelled')),
  version integer not null default 1 check (version > 0),
  policy_id uuid not null references public.league_rollover_policies(id) on delete restrict,
  policy_fingerprint text not null check (length(policy_fingerprint)=64),
  owner_population_fingerprint text not null check (length(owner_population_fingerprint)=64),
  commissioner_population_fingerprint text not null check (length(commissioner_population_fingerprint)=64),
  evidence_fingerprint text not null check (length(evidence_fingerprint)=64),
  authority_fingerprint text not null check (length(authority_fingerprint)=64),
  preparation_fingerprint text not null check (length(preparation_fingerprint)=64),
  preparation_payload jsonb not null,
  blockers jsonb not null default '[]'::jsonb check (jsonb_typeof(blockers)='array'),
  warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(warnings)='array'),
  prepared_by uuid not null references auth.users(id) on delete restrict,
  prepared_at timestamptz not null default clock_timestamp(),
  superseded_at timestamptz,
  superseded_by uuid references public.rollover_authority_preparations(id) on delete restrict,
  cancelled_at timestamptz,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata)='object'),
  created_at timestamptz not null default clock_timestamp(),
  constraint authority_preparation_season_boundary check (target_season=source_season+1),
  constraint authority_preparation_supersession_shape check (
    (status='superseded')=(superseded_at is not null and superseded_by is not null)),
  constraint authority_preparation_cancel_shape check ((status='cancelled')=(cancelled_at is not null)),
  unique (rollover_execution_id, authority_type, version),
  unique (rollover_execution_id, authority_type, authority_fingerprint)
);

create unique index if not exists rollover_authority_preparations_one_current
  on public.rollover_authority_preparations(rollover_execution_id,authority_type)
  where status in ('prepared','blocked');
create index if not exists rollover_authority_preparations_league_boundary
  on public.rollover_authority_preparations(league_id,source_season,target_season);

create or replace function public.enforce_rollover_authority_preparation_immutability()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
  if old.status in ('superseded','cancelled') then
    raise exception 'terminal authority preparation is immutable';
  end if;
  if new.id<>old.id or new.rollover_execution_id<>old.rollover_execution_id or new.league_id<>old.league_id
     or new.source_season<>old.source_season or new.target_season<>old.target_season
     or new.authority_type<>old.authority_type or new.version<>old.version or new.policy_id<>old.policy_id
     or new.policy_fingerprint<>old.policy_fingerprint
     or new.owner_population_fingerprint<>old.owner_population_fingerprint
     or new.commissioner_population_fingerprint<>old.commissioner_population_fingerprint
     or new.evidence_fingerprint<>old.evidence_fingerprint or new.authority_fingerprint<>old.authority_fingerprint
     or new.preparation_fingerprint<>old.preparation_fingerprint or new.preparation_payload<>old.preparation_payload
     or new.blockers<>old.blockers or new.warnings<>old.warnings or new.prepared_by<>old.prepared_by
     or new.prepared_at<>old.prepared_at or new.metadata<>old.metadata or new.created_at<>old.created_at then
    raise exception 'authority preparation material is immutable';
  end if;
  if new.status not in ('superseded','cancelled') then raise exception 'only supersede or cancel is permitted'; end if;
  return new;
end $$;

drop trigger if exists enforce_rollover_authority_preparation_immutability on public.rollover_authority_preparations;
create trigger enforce_rollover_authority_preparation_immutability before update on public.rollover_authority_preparations
for each row execute function public.enforce_rollover_authority_preparation_immutability();

alter table public.rollover_authority_preparations enable row level security;
drop policy if exists rollover_authority_preparations_league_read on public.rollover_authority_preparations;
create policy rollover_authority_preparations_league_read on public.rollover_authority_preparations for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id=rollover_authority_preparations.league_id and lm.user_id=auth.uid()));
revoke all on public.rollover_authority_preparations from anon,authenticated;
grant select on public.rollover_authority_preparations to authenticated;
grant select,insert,update on public.rollover_authority_preparations to service_role;

create or replace function public.prepare_rollover_authorities_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare v_actor uuid:=auth.uid(); v_key text:=btrim(coalesce(p_request->>'idempotency_key',''));
begin
  if v_actor is null then raise exception 'authentication required'; end if;
  if v_key='' then raise exception 'idempotency key required'; end if;
  if p_request ? 'actor_user_id' then raise exception 'actor spoofing forbidden'; end if;
  raise exception 'authority preparation persistence is unavailable until a rollover execution and finalized outcomes exist';
end $$;

create or replace function public.supersede_rollover_authority_preparation_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
begin
  if auth.uid() is null then raise exception 'authentication required'; end if;
  if btrim(coalesce(p_request->>'idempotency_key',''))='' then raise exception 'idempotency key required'; end if;
  if p_request ? 'actor_user_id' then raise exception 'actor spoofing forbidden'; end if;
  raise exception 'authority supersession is unavailable before persisted preparation';
end $$;

create or replace function public.cancel_rollover_authority_preparation_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
begin
  if auth.uid() is null then raise exception 'authentication required'; end if;
  if btrim(coalesce(p_request->>'idempotency_key',''))='' then raise exception 'idempotency key required'; end if;
  if p_request ? 'actor_user_id' then raise exception 'actor spoofing forbidden'; end if;
  raise exception 'authority cancellation is unavailable before persisted preparation';
end $$;

revoke all on function public.prepare_rollover_authorities_authenticated(jsonb) from public,anon;
revoke all on function public.supersede_rollover_authority_preparation_authenticated(jsonb) from public,anon;
revoke all on function public.cancel_rollover_authority_preparation_authenticated(jsonb) from public,anon;
grant execute on function public.prepare_rollover_authorities_authenticated(jsonb) to authenticated;
grant execute on function public.supersede_rollover_authority_preparation_authenticated(jsonb) to authenticated;
grant execute on function public.cancel_rollover_authority_preparation_authenticated(jsonb) to authenticated;
grant execute on function public.prepare_rollover_authorities_authenticated(jsonb) to service_role;
grant execute on function public.supersede_rollover_authority_preparation_authenticated(jsonb) to service_role;
grant execute on function public.cancel_rollover_authority_preparation_authenticated(jsonb) to service_role;

commit;
