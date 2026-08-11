-- Phase 3B.5C.1: rollover operation trust-boundary and idempotency hardening.
-- Schema/functions only. This migration invokes no rollover operation and inserts no rows.

create table if not exists public.rollover_operation_requests (
  id uuid primary key default gen_random_uuid(),
  rollover_execution_id uuid references public.rollover_executions(id),
  league_id uuid not null references public.leagues(id),
  operation_type text not null check (length(btrim(operation_type)) > 0),
  idempotency_key text not null check (length(btrim(idempotency_key)) > 0),
  request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
  actor_user_id uuid references auth.users(id),
  caller_type text not null check (caller_type in (
    'authenticated_owner','authenticated_co_owner','authenticated_commissioner','internal_service'
  )),
  target_record_id uuid,
  status text not null check (status in ('completed','failed')),
  result_payload jsonb,
  error_code text,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  unique (league_id, operation_type, idempotency_key),
  check ((status = 'completed' and result_payload is not null and completed_at is not null and error_code is null)
      or (status = 'failed' and error_code is not null))
);

create index if not exists rollover_operation_requests_execution_idx
  on public.rollover_operation_requests(rollover_execution_id, operation_type, created_at);
create index if not exists rollover_operation_requests_actor_idx
  on public.rollover_operation_requests(actor_user_id, created_at) where actor_user_id is not null;

alter table public.rollover_operation_requests enable row level security;
revoke all on table public.rollover_operation_requests from public, anon, authenticated;
grant select, insert on table public.rollover_operation_requests to service_role;

create or replace function public.reject_rollover_operation_request_mutation()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
  raise exception 'rollover_operation_requests is append-only';
end $$;

drop trigger if exists rollover_operation_requests_append_only on public.rollover_operation_requests;
create trigger rollover_operation_requests_append_only
before update or delete on public.rollover_operation_requests
for each row execute function public.reject_rollover_operation_request_mutation();

create or replace function public.rollover_material_fingerprint(p_material jsonb)
returns text language sql immutable strict set search_path=pg_catalog,public,extensions as $$
  select encode(extensions.digest(convert_to(p_material::text, 'UTF8'), 'sha256'), 'hex')
$$;

create or replace function public.parse_required_rfc3339_instant(p_value text, p_field_name text)
returns timestamptz language plpgsql immutable set search_path=pg_catalog,public as $$
declare v timestamptz;
begin
  if p_value is null or btrim(p_value) = '' then raise exception '% is required', p_field_name; end if;
  if p_value !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$' then
    raise exception '% must be an explicit RFC 3339 instant with Z or a numeric offset', p_field_name;
  end if;
  begin v := p_value::timestamptz;
  exception when others then raise exception '% is not a valid RFC 3339 instant', p_field_name;
  end;
  return v;
end $$;

create or replace function public.canonical_optional_uuid(p_value text)
returns text language plpgsql immutable set search_path=pg_catalog,public as $$
begin
  if nullif(btrim(p_value),'') is null then return null; end if;
  return (p_value::uuid)::text;
end $$;

create or replace function public.require_authenticated_user()
returns uuid language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare u uuid := auth.uid();
begin
  if u is null then raise exception 'Authenticated user required'; end if;
  return u;
end $$;

create or replace function public.require_commissioner_authority(p_league_id uuid)
returns text language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare u uuid := public.require_authenticated_user(); r text;
begin
  select lower(m.role) into r from public.league_memberships m
   where m.league_id=p_league_id and m.user_id=u;
  if r is null or r not in ('commissioner','admin','host') then
    raise exception 'Commissioner authority required for league';
  end if;
  return r;
end $$;

create or replace function public.require_team_decision_authority(p_decision_id uuid)
returns text language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare u uuid := public.require_authenticated_user(); r text;
begin
  select lower(m.role) into r
    from public.rollover_owner_decisions d
    join public.league_memberships m on m.league_id=d.league_id and m.user_id=u
   where d.id=p_decision_id
     and lower(m.role) in ('owner','co_owner','co-owner')
     and m.league_team_id=d.league_team_id;
  if r is null then raise exception 'Owner decision authority required for linked team'; end if;
  return r;
end $$;

create or replace function public.rollover_operation_retry(
  p_league_id uuid, p_operation_type text, p_idempotency_key text, p_request_fingerprint text
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare prior public.rollover_operation_requests%rowtype;
begin
  if nullif(btrim(p_idempotency_key),'') is null then raise exception 'idempotency_key required'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_league_id::text||':'||p_operation_type||':'||p_idempotency_key,0));
  select * into prior from public.rollover_operation_requests
   where league_id=p_league_id and operation_type=p_operation_type and idempotency_key=p_idempotency_key;
  if found then
    if prior.request_fingerprint <> p_request_fingerprint then raise exception 'Idempotency key material request conflict'; end if;
    if prior.status <> 'completed' then raise exception 'Prior operation request did not complete'; end if;
    return prior.result_payload || jsonb_build_object('idempotent',true,'operation_request_id',prior.id);
  end if;
  return null;
end $$;

create or replace function public.record_rollover_operation(
 p_league_id uuid,p_execution_id uuid,p_operation_type text,p_idempotency_key text,
 p_request_fingerprint text,p_actor uuid,p_caller_type text,p_target uuid,p_result jsonb,p_metadata jsonb
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare request_id uuid;
begin
  insert into public.rollover_operation_requests(
    rollover_execution_id,league_id,operation_type,idempotency_key,request_fingerprint,
    actor_user_id,caller_type,target_record_id,status,result_payload,completed_at,metadata
  ) values (
    p_execution_id,p_league_id,p_operation_type,p_idempotency_key,p_request_fingerprint,
    p_actor,p_caller_type,p_target,'completed',p_result,now(),coalesce(p_metadata,'{}')
  ) returning id into request_id;
  return p_result || jsonb_build_object('idempotent',false,'operation_request_id',request_id);
end $$;

-- Authenticated owner/co-owner operation. Actor identity is exclusively auth.uid().
create or replace function public.submit_rollover_owner_decision_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; d public.rollover_owner_decisions%rowtype; role_name text; material jsonb; fp text; retry jsonb; result jsonb; k text;
begin
  actor:=public.require_authenticated_user();
  if p_request ? 'submitted_by' and nullif(p_request->>'submitted_by','')::uuid is distinct from actor then raise exception 'submitted_by does not match authenticated user'; end if;
  select * into d from public.rollover_owner_decisions where id=(p_request->>'owner_decision_id')::uuid;
  if d.id is null then raise exception 'Owner decision not found'; end if;
  role_name:=public.require_team_decision_authority(d.id); k:=nullif(btrim(p_request->>'idempotency_key'),'');
  material:=jsonb_build_object('operation','submit_owner_decision','execution_id',d.rollover_execution_id::text,'decision_id',d.id::text,
    'actor',actor::text,'choice',p_request->>'choice','recontract_agreement_id',public.canonical_optional_uuid(p_request->>'recontract_agreement_id'),
    'recontract_event_id',public.canonical_optional_uuid(p_request->>'recontract_event_id'),'expected_revision_number',(p_request->>'expected_revision_number')::integer,
    'expected_decision_fingerprint',p_request->>'expected_decision_fingerprint','new_decision_fingerprint',p_request->>'decision_fingerprint',
    'reason',p_request->>'reason','evidence',coalesce(p_request->'evidence','null'::jsonb));
  fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(d.league_id,'submit_owner_decision',k,fp); if retry is not null then return retry; end if;
  result:=public.submit_rollover_owner_decision(p_request||jsonb_build_object('submitted_by',actor::text));
  return public.record_rollover_operation(d.league_id,d.rollover_execution_id,'submit_owner_decision',k,fp,actor,
    case when role_name in ('co_owner','co-owner') then 'authenticated_co_owner' else 'authenticated_owner' end,d.id,result,'{}');
end $$;

-- Separate, audited commissioner override. Never reachable through the owner RPC.
create or replace function public.override_rollover_owner_decision_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; d public.rollover_owner_decisions%rowtype; material jsonb; fp text; retry jsonb; result jsonb; k text;
begin
  actor:=public.require_authenticated_user();
  select * into d from public.rollover_owner_decisions where id=(p_request->>'owner_decision_id')::uuid;
  if d.id is null then raise exception 'Owner decision not found'; end if;
  perform public.require_commissioner_authority(d.league_id);
  if nullif(btrim(p_request->>'override_reason'),'') is null then raise exception 'override_reason required'; end if;
  k:=nullif(btrim(p_request->>'idempotency_key'),'');
  material:=jsonb_build_object('operation','commissioner_owner_override','execution_id',d.rollover_execution_id::text,'decision_id',d.id::text,
    'actor',actor::text,'affected_team',d.league_team_id::text,'choice',p_request->>'choice','recontract_agreement_id',public.canonical_optional_uuid(p_request->>'recontract_agreement_id'),
    'recontract_event_id',public.canonical_optional_uuid(p_request->>'recontract_event_id'),'expected_revision_number',(p_request->>'expected_revision_number')::integer,
    'expected_decision_fingerprint',p_request->>'expected_decision_fingerprint','new_decision_fingerprint',p_request->>'decision_fingerprint',
    'reason',p_request->>'reason','override_reason',p_request->>'override_reason',
    'evidence',coalesce(p_request->'evidence','null'::jsonb));
  fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(d.league_id,'commissioner_owner_override',k,fp); if retry is not null then return retry; end if;
  result:=public.submit_rollover_owner_decision(p_request||jsonb_build_object('submitted_by',actor::text,'reason',p_request->>'override_reason'));
  return public.record_rollover_operation(d.league_id,d.rollover_execution_id,'commissioner_owner_override',k,fp,actor,'authenticated_commissioner',d.id,result,
    jsonb_build_object('affected_team_id',d.league_team_id,'override_reason',p_request->>'override_reason'));
end $$;

create or replace function public.create_rollover_execution_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; league uuid; material jsonb; fp text; retry jsonb; result jsonb; k text; execution_id uuid;
begin
 actor:=public.require_authenticated_user(); league:=(p_request->>'league_id')::uuid; perform public.require_commissioner_authority(league); k:=nullif(btrim(p_request->>'idempotency_key'),'');
 material:=jsonb_build_object('operation','create_execution','league_id',league::text,'source_season',(p_request->>'source_season')::integer,'target_season',(p_request->>'target_season')::integer,'policy_id',public.canonical_optional_uuid(p_request->>'policy_id'),'expected_policy_fingerprint',p_request->>'expected_policy_fingerprint','expected_preflight_fingerprint',p_request->>'expected_preflight_fingerprint','before_state_fingerprint',p_request->>'before_state_fingerprint','actor',actor::text,'material_metadata',coalesce(p_request->'material_metadata','null'::jsonb));
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(league,'create_execution',k,fp); if retry is not null then return retry; end if;
 result:=public.create_rollover_execution(p_request||jsonb_build_object('requested_by',actor::text)); execution_id:=(result->'execution'->>'id')::uuid;
 return public.record_rollover_operation(league,execution_id,'create_execution',k,fp,actor,'authenticated_commissioner',execution_id,result,'{}');
end $$;

create or replace function public.open_rollover_notice_window_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; x public.rollover_executions%rowtype; notice timestamptz; material jsonb; fp text; retry jsonb; result jsonb; k text;
begin
 actor:=public.require_authenticated_user(); select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid; if x.id is null then raise exception 'Execution not found'; end if; perform public.require_commissioner_authority(x.league_id);
 notice:=public.parse_required_rfc3339_instant(p_request->>'official_notice_timestamp','official_notice_timestamp'); k:=nullif(btrim(p_request->>'idempotency_key'),'');
 material:=jsonb_build_object('operation','open_notice_window','execution_id',x.id::text,'official_notice_timestamp',to_char(notice at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'expected_preflight_fingerprint',p_request->>'expected_preflight_fingerprint','expected_owner_population_fingerprint',p_request->>'expected_owner_population_fingerprint','calculated_owner_population_fingerprint',p_request->>'calculated_owner_population_fingerprint','expected_owner_count',(p_request->>'expected_owner_count')::integer,'owner_population',p_request->'owner_population','actor',actor::text,'material_metadata',coalesce(p_request->'material_metadata','null'::jsonb));
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'open_notice_window',k,fp); if retry is not null then return retry; end if;
 perform set_config('TimeZone','UTC',true);
 result:=public.open_rollover_notice_window(p_request||jsonb_build_object('requested_by',actor::text,'official_notice_timestamp',notice));
 return public.record_rollover_operation(x.league_id,x.id,'open_notice_window',k,fp,actor,'authenticated_commissioner',x.id,result,'{}');
end $$;

create or replace function public.close_rollover_decision_window_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; x public.rollover_executions%rowtype; close_at timestamptz; material jsonb; fp text; retry jsonb; result jsonb; k text;
begin
 actor:=public.require_authenticated_user(); select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid; if x.id is null then raise exception 'Execution not found'; end if; perform public.require_commissioner_authority(x.league_id);
 k:=nullif(btrim(p_request->>'idempotency_key'),''); if k is null then raise exception 'idempotency_key required'; end if;
 close_at:=public.parse_required_rfc3339_instant(p_request->>'effective_close_timestamp','effective_close_timestamp');
 material:=jsonb_build_object('operation','close_decision_window','execution_id',x.id::text,'effective_close_timestamp',to_char(close_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'expected_population_fingerprint',p_request->>'expected_population_fingerprint','actor',actor::text,'material_metadata',coalesce(p_request->'material_metadata','null'::jsonb));
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'close_decision_window',k,fp); if retry is not null then return retry; end if;
 result:=public.close_rollover_decision_window(p_request||jsonb_build_object('requested_by',actor::text,'effective_close_timestamp',close_at));
 return public.record_rollover_operation(x.league_id,x.id,'close_decision_window',k,fp,actor,'authenticated_commissioner',x.id,result,'{}');
end $$;

create or replace function public.cancel_rollover_execution_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid; x public.rollover_executions%rowtype; material jsonb; fp text; retry jsonb; result jsonb; k text;
begin
 actor:=public.require_authenticated_user(); select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid; if x.id is null then raise exception 'Execution not found'; end if; perform public.require_commissioner_authority(x.league_id);
 k:=nullif(btrim(p_request->>'idempotency_key'),''); if k is null or nullif(btrim(p_request->>'reason'),'') is null then raise exception 'Cancellation reason and idempotency key required'; end if;
 material:=jsonb_build_object('operation','cancel_execution','execution_id',x.id::text,'reason',p_request->>'reason','actor',actor::text,'material_metadata',coalesce(p_request->'material_metadata','null'::jsonb));
 fp:=public.rollover_material_fingerprint(material); retry:=public.rollover_operation_retry(x.league_id,'cancel_execution',k,fp); if retry is not null then return retry; end if;
 result:=public.cancel_rollover_execution(p_request||jsonb_build_object('requested_by',actor::text));
 return public.record_rollover_operation(x.league_id,x.id,'cancel_execution',k,fp,actor,'authenticated_commissioner',x.id,result,'{}');
end $$;

-- Unsafe legacy entry points become implementation primitives with no external EXECUTE.
revoke all on function public.create_rollover_execution(jsonb),public.open_rollover_notice_window(jsonb),public.submit_rollover_owner_decision(jsonb),public.close_rollover_decision_window(jsonb),public.cancel_rollover_execution(jsonb) from public,anon,authenticated,service_role;

revoke all on function public.submit_rollover_owner_decision_authenticated(jsonb),public.override_rollover_owner_decision_authenticated(jsonb),public.create_rollover_execution_authenticated(jsonb),public.open_rollover_notice_window_authenticated(jsonb),public.close_rollover_decision_window_authenticated(jsonb),public.cancel_rollover_execution_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.submit_rollover_owner_decision_authenticated(jsonb),public.override_rollover_owner_decision_authenticated(jsonb),public.create_rollover_execution_authenticated(jsonb),public.open_rollover_notice_window_authenticated(jsonb),public.close_rollover_decision_window_authenticated(jsonb),public.cancel_rollover_execution_authenticated(jsonb) to authenticated;

revoke all on function public.require_authenticated_user(),public.require_commissioner_authority(uuid),public.require_team_decision_authority(uuid),public.rollover_operation_retry(uuid,text,text,text),public.record_rollover_operation(uuid,uuid,text,text,text,uuid,text,uuid,jsonb,jsonb) from public,anon,authenticated,service_role;
revoke all on function public.parse_required_rfc3339_instant(text,text),public.canonical_optional_uuid(text),public.rollover_material_fingerprint(jsonb) from public,anon,authenticated;
grant execute on function public.parse_required_rfc3339_instant(text,text),public.canonical_optional_uuid(text),public.rollover_material_fingerprint(jsonb) to service_role;

comment on table public.rollover_operation_requests is 'Append-only durable material request/result ledger for rollover-window mutating operations.';
comment on function public.submit_rollover_owner_decision_authenticated(jsonb) is 'Authenticated owner/co-owner operation; actor is auth.uid(), never payload submitted_by.';
comment on function public.override_rollover_owner_decision_authenticated(jsonb) is 'Separate audited commissioner override; actual actor is auth.uid().';
