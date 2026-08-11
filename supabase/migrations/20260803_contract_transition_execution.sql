-- Phase 3B.2: atomic normalized-contract transition only. This function never changes
-- season authority, legacy contracts, rosters, free agents, dead cap, cap adjustments,
-- draft picks, or immutable history.

create table if not exists public.contract_transition_executions (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id),
  source_league_season_id uuid not null references public.league_seasons(id),
  target_league_season_id uuid not null references public.league_seasons(id),
  source_season integer not null,
  target_season integer not null,
  transition_key text not null unique,
  request_version text not null,
  planner_version text not null,
  executor_version text not null,
  expected_source_fingerprint text not null,
  actual_source_fingerprint text not null,
  plan_fingerprint text not null,
  status text not null check (status in ('applying','validated','failed')),
  dry_run boolean not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  requested_by text,
  agreement_count integer not null,
  continuing_count integer not null,
  expiring_count integer not null,
  satisfied_season_count integer not null default 0,
  activated_season_count integer not null default 0,
  expired_agreement_count integer not null default 0,
  expiration_event_count integer not null default 0,
  result jsonb not null default '{}'::jsonb,
  error jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (target_season=source_season+1),
  check (dry_run=false)
);

-- The pre-existing trigger remains closed to every caller except this security-definer
-- RPC while its transaction-local guard is present.
create or replace function public.reject_historical_contract_season_mutation() returns trigger
language plpgsql set search_path=pg_catalog,public as $$
declare v_status text;
begin
  if current_setting('app.contract_transition_execution',true)='contract-transition-executor-v1' then
    return new;
  end if;
  select status into v_status from public.league_seasons where id=old.league_season_id;
  if v_status='completed' then raise exception 'Completed contract seasons are immutable.'; end if;
  raise exception 'Contract obligations require an audited contract-edit RPC.';
end $$;

create or replace function public.apply_contract_transition(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare
  v_league_id uuid; v_source_id uuid; v_target_id uuid; v_source integer; v_target integer;
  v_key text; v_dry boolean; v_source_row public.league_seasons%rowtype; v_target_row public.league_seasons%rowtype;
  v_existing public.contract_transition_executions%rowtype; v_execution_id uuid;
  v_agreements integer; v_continuing integer; v_expiring integer; v_source_active integer;
  v_target_scheduled integer; v_2027_scheduled integer; v_expired_events integer;
  v_imported_events integer; v_total_events integer;
  v_satisfied integer; v_activated integer; v_active_agreements integer; v_expired_agreements integer;
  v_result jsonb; v_result_fingerprint text;
begin
  if not (p_request ? 'dry_run') or jsonb_typeof(p_request->'dry_run')<>'boolean' then
    raise exception 'dry_run must be supplied explicitly as a JSON boolean.';
  end if;
  v_dry=(p_request->>'dry_run')::boolean;
  v_league_id=(p_request->>'league_id')::uuid; v_source=(p_request->>'source_season')::integer;
  v_target=(p_request->>'target_season')::integer; v_source_id=(p_request->>'source_league_season_id')::uuid;
  v_target_id=(p_request->>'target_league_season_id')::uuid; v_key=p_request->>'transition_key';
  if v_source<>2025 or v_target<>2026 or v_target<>v_source+1 then raise exception 'Approved execution requires 2025 -> 2026.'; end if;
  if v_key<>format('contract-transition:%s:%s:%s:v1',v_league_id,v_source,v_target) then raise exception 'Invalid transition key.'; end if;
  if p_request->>'request_version'<>'v1' or p_request->>'planner_version'<>'contract-transition-v1'
     or p_request->>'executor_version'<>'contract-transition-executor-v1' then raise exception 'Execution version mismatch.'; end if;
  if nullif(p_request->>'expected_source_fingerprint','') is null
     or p_request->>'expected_source_fingerprint'<>p_request->>'actual_source_fingerprint' then raise exception 'Contract transition source fingerprint mismatch.'; end if;
  if nullif(p_request->>'expected_plan_fingerprint','') is null
     or p_request->>'expected_plan_fingerprint'<>p_request->>'actual_plan_fingerprint' then raise exception 'Contract transition plan fingerprint mismatch.'; end if;
  if jsonb_typeof(p_request->'expected_counts')<>'object'
     or not ((p_request->'expected_counts') ?& array['agreements','continues','expires','source_obligations','target_obligations','season_2027_obligations','invalid','already_transitioned']) then
    raise exception 'Complete approved expected_counts are required.';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_key,0));
  select * into v_existing from public.contract_transition_executions where transition_key=v_key;
  if found then
    if v_existing.status<>'validated' or v_existing.league_id<>v_league_id or v_existing.source_season<>v_source
       or v_existing.target_season<>v_target or v_existing.expected_source_fingerprint<>p_request->>'expected_source_fingerprint'
       or v_existing.plan_fingerprint<>p_request->>'expected_plan_fingerprint' or v_existing.request_version<>p_request->>'request_version'
       or v_existing.planner_version<>p_request->>'planner_version' or v_existing.executor_version<>p_request->>'executor_version' then
      raise exception 'Conflicting request or non-validated execution exists for transition key %.',v_key;
    end if;
    return v_existing.result || jsonb_build_object('idempotent',true,'execution_id',v_existing.id);
  end if;

  select * into v_source_row from public.league_seasons where id=v_source_id for share;
  select * into v_target_row from public.league_seasons where id=v_target_id for share;
  if v_source_row.id is null or v_target_row.id is null or v_source_row.league_id<>v_league_id or v_target_row.league_id<>v_league_id
     or v_source_row.season<>v_source or v_target_row.season<>v_target then raise exception 'Cross-league or missing season authority.'; end if;
  if v_source_row.status<>'active' or not v_source_row.is_active then raise exception 'Source season must remain active.'; end if;
  if v_target_row.status<>'scheduled' or v_target_row.is_active then raise exception 'Target season must remain inactive and scheduled.'; end if;
  if v_target_row.previous_league_season_id<>v_source_row.id then raise exception 'Broken previous-season linkage.'; end if;

  -- Lock every normalized source row used by the approved plan so validation and
  -- mutation observe one stable database state.
  perform 1 from public.contract_agreements where league_id=v_league_id for share;
  perform 1 from public.contract_seasons where league_id=v_league_id for share;
  perform 1 from public.contract_events where league_id=v_league_id for share;

  select count(*) into v_agreements from public.contract_agreements where league_id=v_league_id;
  select count(*) into v_source_active from public.contract_seasons where league_id=v_league_id and season=v_source and obligation_status='active';
  select count(*) into v_target_scheduled from public.contract_seasons where league_id=v_league_id and season=v_target and obligation_status='scheduled';
  select count(*) into v_2027_scheduled from public.contract_seasons where league_id=v_league_id and season=2027 and obligation_status='scheduled';
  select count(*) into v_continuing from public.contract_agreements a where a.league_id=v_league_id and a.status='active'
    and exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season=v_source and s.obligation_status='active')
    and exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season=v_target and s.obligation_status='scheduled');
  select count(*) into v_expiring from public.contract_agreements a where a.league_id=v_league_id and a.status='active'
    and exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season=v_source and s.obligation_status='active')
    and not exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season>=v_target);
  select count(*) into v_expired_events from public.contract_events where league_id=v_league_id and event_type='expired'
    and metadata->>'transition_key'=v_key;
  select count(*) into v_imported_events from public.contract_events where league_id=v_league_id and event_type='imported';
  select count(*) into v_total_events from public.contract_events where league_id=v_league_id;

  if v_agreements<>(p_request->'expected_counts'->>'agreements')::integer
     or v_continuing<>(p_request->'expected_counts'->>'continues')::integer
     or v_expiring<>(p_request->'expected_counts'->>'expires')::integer
     or v_source_active<>(p_request->'expected_counts'->>'source_obligations')::integer
     or v_target_scheduled<>(p_request->'expected_counts'->>'target_obligations')::integer
     or v_2027_scheduled<>(p_request->'expected_counts'->>'season_2027_obligations')::integer
     or (p_request->'expected_counts'->>'invalid')::integer<>0
     or (p_request->'expected_counts'->>'already_transitioned')::integer<>0 then
    raise exception 'Approved transition counts differ from current pre-transition state.';
  end if;
  if jsonb_typeof(p_request->'agreement_plan')<>'array' or jsonb_array_length(p_request->'agreement_plan')<>v_agreements
     or exists(
       select 1 from jsonb_array_elements(p_request->'agreement_plan') x
       left join public.contract_agreements a on a.id=(x->>'agreement_id')::uuid and a.league_id=v_league_id
       left join public.contract_seasons s25 on s25.contract_id=a.id and s25.season=v_source
       left join public.contract_seasons s26 on s26.contract_id=a.id and s26.season=v_target
       where a.id is null or a.player_id<>x->>'player_id' or a.league_team_id<>(x->>'league_team_id')::uuid
          or s25.id is null or s25.player_id<>a.player_id or s25.league_team_id<>a.league_team_id
          or s25.salary<>(x->>'source_salary')::numeric
          or (x->>'outcome'='CONTINUES' and (s26.id is null or s26.salary<>(x->>'target_salary')::numeric
              or s26.player_id<>a.player_id or s26.league_team_id<>a.league_team_id))
          or (x->>'outcome'='EXPIRES_AFTER_2025' and s26.id is not null)
          or x->>'outcome' not in ('CONTINUES','EXPIRES_AFTER_2025')
     ) then raise exception 'Locked live contract rows differ from the approved fingerprint plan.';
  end if;
  if v_expired_events<>0 or v_imported_events<>v_agreements or v_total_events<>v_imported_events
     or exists(select 1 from public.contract_agreements where league_id=v_league_id and status<>'active')
     or exists(select 1 from public.contract_seasons where league_id=v_league_id and season=v_source and obligation_status<>'active')
     or exists(select 1 from public.contract_seasons where league_id=v_league_id and season=v_target and obligation_status<>'scheduled') then
    raise exception 'Partial or previously transitioned contract lifecycle state detected.';
  end if;
  if exists(select 1 from public.contract_seasons where league_id=v_league_id group by contract_id,season having count(*)>1)
     or exists(select 1 from public.contract_agreements where league_id=v_league_id and status in ('active','scheduled') group by player_id having count(*)>1) then
    raise exception 'Duplicate obligation or overlapping live agreement detected.';
  end if;

  v_result=jsonb_build_object('status','dry_run_validated','safe_to_apply',true,'idempotent',false,'dry_run',true,
    'transition_key',v_key,'source_fingerprint',p_request->>'actual_source_fingerprint','plan_fingerprint',p_request->>'actual_plan_fingerprint',
    'planned',jsonb_build_object('satisfied_2025',v_source_active,'activated_2026',v_target_scheduled,'expired_agreements',v_expiring,'expiration_events',v_expiring),
    'expected_persisted',jsonb_build_object('agreements',v_agreements,'active_agreements',v_continuing,'expired_agreements',v_expiring,
      'satisfied_2025',v_source_active,'active_2026',v_target_scheduled,'scheduled_2027',v_2027_scheduled,'contract_events',v_agreements+v_expiring));
  if v_dry then return v_result; end if;

  insert into public.contract_transition_executions
    (league_id,source_league_season_id,target_league_season_id,source_season,target_season,transition_key,request_version,planner_version,
     executor_version,expected_source_fingerprint,actual_source_fingerprint,plan_fingerprint,status,dry_run,requested_by,
     agreement_count,continuing_count,expiring_count)
  values(v_league_id,v_source_id,v_target_id,v_source,v_target,v_key,p_request->>'request_version',p_request->>'planner_version',
     p_request->>'executor_version',p_request->>'expected_source_fingerprint',p_request->>'actual_source_fingerprint',
     p_request->>'expected_plan_fingerprint','applying',false,p_request->>'requested_by',v_agreements,v_continuing,v_expiring)
  returning id into v_execution_id;

  perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
  update public.contract_seasons set obligation_status='satisfied',updated_at=now()
    where league_id=v_league_id and season=v_source and obligation_status='active';
  get diagnostics v_satisfied=row_count;
  update public.contract_seasons set obligation_status='active',updated_at=now()
    where league_id=v_league_id and season=v_target and obligation_status='scheduled';
  get diagnostics v_activated=row_count;
  update public.contract_agreements a set status='expired',updated_at=now()
    where a.league_id=v_league_id and a.status='active'
      and not exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season>=v_target);
  get diagnostics v_expired_agreements=row_count;
  insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,
    previous_values,new_values,metadata,idempotency_key)
  select a.id,a.league_id,a.league_team_id,a.player_id,'expired',v_source,'contract_transition_execution',
    jsonb_build_object('agreement_status','active','source_obligation_status','active'),
    jsonb_build_object('agreement_status','expired','source_obligation_status','satisfied'),
    jsonb_build_object('reason','natural_expiration','source_season',v_source,'target_season',v_target,'execution_id',v_execution_id,
      'transition_key',v_key,'dead_cap_consequence',false,'roster_consequence',false,'free_agent_publication_consequence',false),
    format('contract-expired:%s:%s:%s:v1',a.id,v_source,v_target)
  from public.contract_agreements a where a.league_id=v_league_id and a.status='expired'
    and not exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season>=v_target)
  on conflict(idempotency_key) do nothing;
  get diagnostics v_expired_events=row_count;

  select count(*) into v_satisfied from public.contract_seasons where league_id=v_league_id and season=v_source and obligation_status='satisfied';
  select count(*) into v_activated from public.contract_seasons where league_id=v_league_id and season=v_target and obligation_status='active';
  select count(*) into v_active_agreements from public.contract_agreements where league_id=v_league_id and status='active';
  select count(*) into v_expired_agreements from public.contract_agreements where league_id=v_league_id and status='expired';
  select count(*) into v_expired_events from public.contract_events where league_id=v_league_id and event_type='expired' and metadata->>'transition_key'=v_key;
  if v_satisfied<>v_agreements or v_activated<>v_continuing or v_active_agreements<>v_continuing
     or v_expired_agreements<>v_expiring or v_expired_events<>v_expiring
     or (select count(*) from public.contract_seasons where league_id=v_league_id and season=2027 and obligation_status='scheduled')<>v_2027_scheduled then
    raise exception 'Post-transition normalized state validation failed.';
  end if;
  v_result=jsonb_build_object('status','validated','safe_to_apply',true,'idempotent',false,'dry_run',false,'execution_id',v_execution_id,
    'transition_key',v_key,'persisted',jsonb_build_object('agreements',v_agreements,'active_agreements',v_active_agreements,
      'expired_agreements',v_expired_agreements,'satisfied_2025',v_satisfied,'active_2026',v_activated,
      'scheduled_2027',v_2027_scheduled,'expired_events',v_expired_events,'contract_events',v_agreements+v_expired_events));
  v_result_fingerprint=md5(v_result::text); v_result=v_result||jsonb_build_object('result_fingerprint',v_result_fingerprint);
  update public.contract_transition_executions set status='validated',completed_at=now(),updated_at=now(),
    satisfied_season_count=v_satisfied,activated_season_count=v_activated,expired_agreement_count=v_expired_agreements,
    expiration_event_count=v_expired_events,result=v_result where id=v_execution_id;
  return v_result;
end $$;

revoke all on function public.apply_contract_transition(jsonb) from public,anon,authenticated;
grant execute on function public.apply_contract_transition(jsonb) to service_role;
alter table public.contract_transition_executions enable row level security;
revoke all on table public.contract_transition_executions from anon,authenticated;
grant all on table public.contract_transition_executions to service_role;
