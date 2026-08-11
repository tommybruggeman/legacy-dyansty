begin;

insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,result_schema_version,execution_owner,mutation_class,metadata) values
('RELEASE_EXPIRED_CONTRACTS',13,1,'phase3b7c-release-input-v1','phase3b7c-release-result-v1','execution','contract_domain',jsonb_build_object('phase','3B.7C')),
('APPLY_COMMISSIONER_HOLDS',14,1,'phase3b7c-hold-input-v1','phase3b7c-hold-result-v1','execution','contract_domain',jsonb_build_object('phase','3B.7C'));

create table public.rollover_contract_releases(
 id uuid primary key default gen_random_uuid(),rollover_execution_id uuid not null references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),closing_season_id uuid not null references public.league_seasons(id),
 target_season_id uuid not null references public.league_seasons(id),contract_agreement_id uuid not null references public.contract_agreements(id),
 player_id text not null references public.player_universe(sleeper_id),source_league_team_id uuid not null references public.league_teams(id),
 source_roster_assignment_id uuid not null references public.season_roster_assignments(id),final_outcome_id uuid not null references public.rollover_owner_option_final_outcomes(id),
 final_outcome_hash text not null check(final_outcome_hash~'^[0-9a-f]{64}$'),release_disposition text not null check(release_disposition in('ordinary_release','release_to_commissioner_hold')),
 effective_season integer not null,previous_agreement_status text not null,resulting_agreement_status text not null check(resulting_agreement_status='released'),
 release_fingerprint text not null check(release_fingerprint~'^[0-9a-f]{64}$'),created_at timestamptz not null default clock_timestamp(),
 unique(rollover_execution_id,contract_agreement_id),unique(rollover_execution_id,player_id));

create table public.rollover_commissioner_holds(
 id uuid primary key default gen_random_uuid(),league_id uuid not null references public.leagues(id),player_id text not null references public.player_universe(sleeper_id),
 closing_season_id uuid not null references public.league_seasons(id),target_season_id uuid not null references public.league_seasons(id),
 source_contract_agreement_id uuid not null references public.contract_agreements(id),source_league_team_id uuid not null references public.league_teams(id),
 rollover_execution_id uuid not null references public.rollover_executions(id),release_id uuid not null references public.rollover_contract_releases(id),
 final_outcome_id uuid not null references public.rollover_owner_option_final_outcomes(id),final_outcome_hash text not null check(final_outcome_hash~'^[0-9a-f]{64}$'),
 hold_reason_code text not null check(hold_reason_code in('owner_nonresponse_release_to_hold','approved_release_to_hold')),
 hold_status text not null check(hold_status in('active','released','voided')),effective_at timestamptz not null default clock_timestamp(),
 released_from_hold_at timestamptz,release_authority uuid,creation_fingerprint text not null check(creation_fingerprint~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default clock_timestamp(),unique(rollover_execution_id,player_id),
 check((hold_status='active' and released_from_hold_at is null and release_authority is null) or hold_status<>'active'));
create unique index rollover_commissioner_holds_one_active_uidx on public.rollover_commissioner_holds(league_id,player_id,target_season_id) where hold_status='active';

create table public.rollover_commissioner_hold_events(
 id uuid primary key default gen_random_uuid(),hold_id uuid not null references public.rollover_commissioner_holds(id),
 rollover_execution_id uuid not null references public.rollover_executions(id),operation_code text not null check(operation_code='APPLY_COMMISSIONER_HOLDS'),
 league_id uuid not null,player_id text not null,source_contract_agreement_id uuid not null,source_league_team_id uuid not null,
 release_id uuid not null,final_outcome_id uuid not null,final_outcome_hash text not null,previous_state jsonb not null,resulting_state jsonb not null,
 event_fingerprint text not null check(event_fingerprint~'^[0-9a-f]{64}$'),effective_at timestamptz not null default clock_timestamp(),
 unique(rollover_execution_id,operation_code,hold_id));

create or replace function public.reject_phase3b7c_immutable_mutation() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$begin raise exception 'Phase 3B.7C release and hold evidence is immutable';end$$;
create trigger rollover_contract_releases_immutable before update or delete on public.rollover_contract_releases for each row execute function public.reject_phase3b7c_immutable_mutation();
create trigger rollover_commissioner_hold_events_immutable before update or delete on public.rollover_commissioner_hold_events for each row execute function public.reject_phase3b7c_immutable_mutation();
alter table public.rollover_contract_releases enable row level security;alter table public.rollover_commissioner_holds enable row level security;alter table public.rollover_commissioner_hold_events enable row level security;
revoke all on public.rollover_contract_releases,public.rollover_commissioner_holds,public.rollover_commissioner_hold_events from public,anon,authenticated;
grant select,insert,update on public.rollover_contract_releases,public.rollover_commissioner_holds,public.rollover_commissioner_hold_events to service_role;
create policy phase3b7c_release_read on public.rollover_contract_releases for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_contract_releases.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy phase3b7c_hold_read on public.rollover_commissioner_holds for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_commissioner_holds.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy phase3b7c_hold_event_read on public.rollover_commissioner_hold_events for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rollover_commissioner_hold_events.league_id and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.has_active_rollover_cutover_lock(p_league_id uuid) returns boolean language sql stable security definer set search_path=pg_catalog,public as $$select exists(select 1 from public.rollover_execution_locks where league_id=p_league_id and lock_type='cutover' and lock_scope='rollover_global' and status='active')$$;
create or replace function public.assert_no_active_rollover_cutover_lock(p_league_id uuid) returns void language plpgsql stable security definer set search_path=pg_catalog,public as $$begin if public.has_active_rollover_cutover_lock(p_league_id) then raise exception using errcode='P0001',message='rollover_cutover_contract_writes_blocked';end if;end$$;
create or replace function public.guard_contract_write_during_rollover() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$declare lid uuid:=coalesce(new.league_id,old.league_id);begin if current_setting('app.rollover_typed_execution',true)<>'phase3b7c-v1' then perform public.assert_no_active_rollover_cutover_lock(lid);end if;return case when tg_op='DELETE' then old else new end;end$$;
-- Preserve the legacy RPC API outside cutover, but fail before validation or writes
-- whenever the canonical league has an active rollover cutover lock.
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
  v_league_id=(p_request->>'league_id')::uuid; perform public.assert_no_active_rollover_cutover_lock(v_league_id); v_source=(p_request->>'source_season')::integer;
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



create trigger contracts_rollover_cutover_guard before insert or update or delete on public.contracts for each row execute function public.guard_contract_write_during_rollover();
create trigger contract_agreements_rollover_cutover_guard before insert or update or delete on public.contract_agreements for each row execute function public.guard_contract_write_during_rollover();
create trigger contract_seasons_rollover_cutover_guard before insert or update or delete on public.contract_seasons for each row execute function public.guard_contract_write_during_rollover();
create trigger contract_events_rollover_cutover_guard before insert or update or delete on public.contract_events for each row execute function public.guard_contract_write_during_rollover();
revoke all on function public.assert_no_active_rollover_cutover_lock(uuid),public.guard_contract_write_during_rollover() from public,anon,authenticated,service_role;
revoke all on function public.has_active_rollover_cutover_lock(uuid) from public,anon;
grant execute on function public.has_active_rollover_cutover_lock(uuid) to authenticated,service_role;

create or replace function public.execute_rollover_typed_handler_phase3b7c_private(p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare code text:=p_operation->>'operation_type';x public.rollover_executions%rowtype;a public.contract_agreements%rowtype;f public.rollover_owner_option_final_outcomes%rowtype;
 r public.rollover_contract_releases%rowtype;assignment public.season_roster_assignments%rowtype;hold_id uuid;fp text;result_hash text;
 candidates int:=0;ordinary int:=0;to_hold int:=0;agreements int:=0;ownership_closed int:=0;evidence int:=0;holds int:=0;hold_events int:=0;
begin
 if code not in('RELEASE_EXPIRED_CONTRACTS','APPLY_COMMISSIONER_HOLDS') then return public.execute_rollover_typed_handler_phase3b7b_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);end if;
 if not exists(select 1 from public.rollover_execution_handler_registry where operation_code=code and operation_order=(p_operation->>'operation_index')::integer and handler_version=1) then perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}');end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 if not exists(select 1 from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=p_approval_id and execution_plan_id=p_execution_plan_id and status='active' and lock_type='cutover' for update) then perform public.raise_phase3b6c1_failure(case when code='RELEASE_EXPIRED_CONTRACTS' then 'release_cutover_lock_missing' else 'hold_cutover_lock_missing' end,'{}');end if;
 perform set_config('app.rollover_typed_execution','phase3b7c-v1',true);perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
 if code='RELEASE_EXPIRED_CONTRACTS' then
  for a in select * from public.contract_agreements where rollover_execution_id=p_rollover_execution_id and rollover_pending_disposition in('pending_release','pending_release_to_commissioner_hold') order by player_id,id for update loop
   candidates:=candidates+1;select * into f from public.rollover_owner_option_final_outcomes where id=a.rollover_final_outcome_id and rollover_execution_id=p_rollover_execution_id;
   if f.id is null then perform public.raise_phase3b6c1_failure('release_outcome_missing','{}');end if;if f.policy_resolution_code='exercise' or f.final_disposition_code='approve_policy_supported_exercise' then perform public.raise_phase3b6c1_failure('release_exercise_conflict','{}');end if;
   select * into assignment from public.season_roster_assignments where league_season_id=f.closing_season_id and sleeper_player_id=f.player_id;
   if assignment.id is null then perform public.raise_phase3b6c1_failure('release_assignment_missing','{}');end if;if assignment.league_team_id<>f.league_team_id then perform public.raise_phase3b6c1_failure('release_owner_mismatch','{}');end if;
   fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7c-release-v1','execution',p_rollover_execution_id,'agreement',a.id,'assignment',assignment.id,'outcome',f.id,'outcome_hash',f.final_outcome_hash,'disposition',a.rollover_pending_disposition));
   insert into public.rollover_contract_releases(rollover_execution_id,league_id,closing_season_id,target_season_id,contract_agreement_id,player_id,source_league_team_id,source_roster_assignment_id,final_outcome_id,final_outcome_hash,release_disposition,effective_season,previous_agreement_status,resulting_agreement_status,release_fingerprint)
   values(p_rollover_execution_id,f.league_id,f.closing_season_id,f.target_season_id,a.id,f.player_id,f.league_team_id,assignment.id,f.id,f.final_outcome_hash,case when a.rollover_pending_disposition='pending_release_to_commissioner_hold' then 'release_to_commissioner_hold' else 'ordinary_release' end,x.target_season,a.status,'released',fp) returning * into r;
   update public.contract_agreements set status='released',updated_at=clock_timestamp() where id=a.id;
   insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
   values(a.id,a.league_id,a.league_team_id,a.player_id,'released',x.target_season,'phase3b7c',p_actor,jsonb_build_object('status',a.status,'pending_disposition',a.rollover_pending_disposition),jsonb_build_object('status','released','ownership','closed','public_visibility',false),jsonb_build_object('rollover_execution_id',p_rollover_execution_id,'operation_code',code,'release_id',r.id,'final_outcome_id',f.id,'final_outcome_hash',f.final_outcome_hash,'event_fingerprint',fp),format('phase3b7c:%s:%s:%s',p_rollover_execution_id,code,a.id));
   agreements:=agreements+1;ownership_closed:=ownership_closed+1;evidence:=evidence+1;if r.release_disposition='release_to_commissioner_hold' then to_hold:=to_hold+1;else ordinary:=ordinary+1;end if;
  end loop;
  if candidates<>(select count(*) from public.contract_agreements where rollover_execution_id=p_rollover_execution_id and rollover_pending_disposition is not null) then perform public.raise_phase3b6c1_failure('release_population_incomplete','{}');end if;
  result_hash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7c-release-result-v1','execution',p_rollover_execution_id,'candidates',candidates,'ordinary',ordinary,'hold',to_hold));
  return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('release_candidate_count',candidates,'ordinary_release_count',ordinary,'release_to_hold_count',to_hold,'agreements_transitioned',agreements,'ownership_relationships_closed',ownership_closed,'roster_references_closed_or_superseded',0,'release_evidence_rows_written',evidence,'compatible_replay_count',0,'conflict_count',0,'operation_mutation_count',agreements+evidence+candidates,'validation_codes',jsonb_build_array('final_outcome','pending_disposition','source_assignment','cutover_lock'),'deterministic_result_hash',result_hash));
 end if;
 for r in select * from public.rollover_contract_releases where rollover_execution_id=p_rollover_execution_id and release_disposition='release_to_commissioner_hold' order by player_id,id for share loop
  candidates:=candidates+1;select * into f from public.rollover_owner_option_final_outcomes where id=r.final_outcome_id;
  select * into a from public.contract_agreements where id=r.contract_agreement_id for update;
  if a.status<>'released' then perform public.raise_phase3b6c1_failure('hold_player_still_owned','{}');end if;
  if exists(select 1 from public.free_agent_publications p where p.league_id=r.league_id and p.player_id=r.player_id and p.season=x.target_season and (p.publication_status='published' or p.acquisition_status='eligible') for update) then perform public.raise_phase3b6c1_failure('hold_free_agent_visibility_conflict','{}');end if;
  fp:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7c-hold-v1','execution',p_rollover_execution_id,'release',r.id,'outcome_hash',r.final_outcome_hash));
  insert into public.rollover_commissioner_holds(league_id,player_id,closing_season_id,target_season_id,source_contract_agreement_id,source_league_team_id,rollover_execution_id,release_id,final_outcome_id,final_outcome_hash,hold_reason_code,hold_status,creation_fingerprint)
  values(r.league_id,r.player_id,r.closing_season_id,r.target_season_id,r.contract_agreement_id,r.source_league_team_id,p_rollover_execution_id,r.id,r.final_outcome_id,r.final_outcome_hash,case when f.policy_resolution_code='default_release_to_commissioner_hold' then 'owner_nonresponse_release_to_hold' else 'approved_release_to_hold' end,'active',fp) returning id into hold_id;
  insert into public.rollover_commissioner_hold_events(hold_id,rollover_execution_id,operation_code,league_id,player_id,source_contract_agreement_id,source_league_team_id,release_id,final_outcome_id,final_outcome_hash,previous_state,resulting_state,event_fingerprint)
  values(hold_id,p_rollover_execution_id,code,r.league_id,r.player_id,r.contract_agreement_id,r.source_league_team_id,r.id,r.final_outcome_id,r.final_outcome_hash,jsonb_build_object('custody','none','team_owned',false,'free_agent_visible',false),jsonb_build_object('custody','commissioner_administrative_hold','team_owned',false,'free_agent_visible',false),fp);
  holds:=holds+1;hold_events:=hold_events+1;
 end loop;
 if candidates<>(select count(*) from public.rollover_contract_releases where rollover_execution_id=p_rollover_execution_id and release_disposition='release_to_commissioner_hold') then perform public.raise_phase3b6c1_failure('hold_population_incomplete','{}');end if;
 result_hash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b7c-hold-result-v1','execution',p_rollover_execution_id,'candidates',candidates,'holds',holds));
 return jsonb_build_object('operation_code',code,'handler_version',1,'result',jsonb_build_object('hold_candidate_count',candidates,'active_holds_created',holds,'compatible_active_holds_reused',0,'conflicting_holds',0,'free_agent_visibility_conflicts',0,'hold_events_written',hold_events,'operation_mutation_count',holds+hold_events,'validation_codes',jsonb_build_array('release_evidence','released_ownership','nonvisibility','cutover_lock'),'deterministic_result_hash',result_hash));
end$$;

create or replace function public.execute_rollover_plan_phase3b7c_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;domain_mutations integer:=0;failed_op jsonb;failure_sqlstate text;failure_message text;
 failure_detail text;failure_hint text;failure_context text;result jsonb;
begin
 if p_actor is null then raise exception 'authenticated actor required';end if;if k is null then raise exception 'idempotency key required';end if;
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 if nullif(p_request->>'rollover_execution_id','') is null or nullif(p_request->>'approval_id','') is null
  or nullif(p_request->>'execution_plan_id','') is null or nullif(p_request->>'expected_plan_fingerprint','') is null
  or nullif(p_request->>'expected_execution_status','') is null or nullif(p_request->>'expected_approval_status','') is null then raise exception 'complete execution assertions required';end if;
 perform pg_advisory_xact_lock(hashtextextended('phase3b5i:'||(p_request->>'rollover_execution_id'),0));
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 -- Retain the certified request-material operation label so a same-key replay
 -- of a completed v1 execution has the identical fingerprint and returns it.
 material:=jsonb_build_object('operation','rollover_plan_execute_phase3b6c','execution_id',x.id,'league_id',x.league_id,'request',p_request-'idempotency_key','actor',p_actor);
 request_fp:=public.rollover_material_fingerprint(material);
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and idempotency_key=k for update;
 if found then if prior.request_fingerprint<>request_fp then raise exception 'Idempotency key material request conflict';end if;return prior.result_payload||jsonb_build_object('idempotent',true,'execution_run_id',prior.id);end if;
 select * into prior from public.rollover_execution_runs where rollover_execution_id=x.id and run_status='executed_successfully' for update;
 if found then return prior.result_payload||jsonb_build_object('idempotent',true,'duplicate_execution',true,'execution_run_id',prior.id);end if;
 if x.status is distinct from p_request->>'expected_execution_status' or x.status<>'execution_ready' then raise exception 'stale or ineligible execution status';end if;
 select * into a from public.rollover_execution_plan_approvals where id=(p_request->>'approval_id')::uuid and rollover_execution_id=x.id for update;
 if a.id is null or a.approval_status is distinct from p_request->>'expected_approval_status' or a.approval_status<>'approved' then raise exception 'invalid or inactive approval';end if;
 select * into p from public.rollover_execution_plans where id=(p_request->>'execution_plan_id')::uuid and rollover_execution_id=x.id for update;
 if p.id is null or p.id<>a.execution_plan_id or p.plan_version<>(p_request->>'expected_plan_version')::integer or p.plan_version<>a.execution_plan_version
  or p.plan_status<>'approved_for_execution' or not p.approved_for_execution or p.plan_fingerprint is distinct from p_request->>'expected_plan_fingerprint'
  or p.plan_fingerprint<>a.plan_fingerprint or p.operation_count<>jsonb_array_length(p.ordered_operations) then raise exception 'stale or invalid approved execution plan';end if;
 select * into l from public.rollover_execution_locks where rollover_execution_id=x.id and approval_id=a.id and execution_plan_id=p.id
  and execution_plan_version=p.plan_version and plan_fingerprint=p.plan_fingerprint and lock_type='cutover' and status='active' for update;
 if l.id is null then raise exception 'active matching cutover lock required';end if;
 insert into public.rollover_execution_runs(rollover_execution_id,league_id,approval_id,execution_plan_id,execution_plan_version,
  plan_fingerprint,idempotency_key,request_fingerprint,run_status,operation_count,started_at,executed_by,diagnostics)
 values(x.id,x.league_id,a.id,p.id,p.plan_version,p.plan_fingerprint,k,request_fp,'execution_started',p.operation_count,run_started,p_actor,
  jsonb_build_object('engine_version','phase3b7c-v1','typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  perform set_config('app.rollover_typed_execution','phase3b7c-v1',true);
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then raise exception 'ordered operation sequence mismatch';end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE',
    'VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED','FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE','RESOLVE_OWNER_OPTION_OUTCOMES','VALIDATE_COMMISSIONER_REVIEW_OUTCOMES','ADVANCE_CONTRACT_SEASON_OBLIGATIONS','EXERCISE_VALID_OWNER_OPTIONS','DECLINE_OR_EXPIRE_OWNER_OPTIONS','RELEASE_EXPIRED_CONTRACTS','APPLY_COMMISSIONER_HOLDS') then
    handler_result:=public.execute_rollover_typed_handler_phase3b7c_private(op,x.id,p.id,a.id,p_actor);
    domain_mutations:=domain_mutations+coalesce((handler_result#>>'{result,operation_mutation_count}')::integer,0);
   else raise exception 'unsupported Phase 3B.7C operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,
    operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint','completed',op_started,
    clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),handler_result,
    jsonb_build_object('domain_mutations',coalesce((handler_result#>>'{result,operation_mutation_count}')::integer,0),'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
   completed:=completed+1;
  end loop;
 exception when others then get stacked diagnostics failure_sqlstate=returned_sqlstate,failure_message=message_text,
  failure_detail=pg_exception_detail,failure_hint=pg_exception_hint,failure_context=pg_exception_context;end;
 if failure_message is not null then
  insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,operation_type,
   operation_fingerprint,operation_status,started_at,finished_at,duration_ms,diagnostics,failure_reason)
  values(runrow.id,x.id,coalesce((failed_op->>'operation_id')::uuid,gen_random_uuid()),greatest(attempted,1),
   coalesce(failed_op->>'operation_type','dispatcher_validation'),coalesce(failed_op->>'operation_fingerprint',repeat('0',64)),'failed',
   coalesce(op_started,run_started),clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-coalesce(op_started,run_started))*1000)::bigint),
   jsonb_build_object('failure_code',failure_message,'sqlstate',failure_sqlstate,'detail',left(coalesce(failure_detail,''),4096),
    'hint',left(coalesce(failure_hint,''),1024),'context',left(coalesce(failure_context,''),4096),'rolled_back_operations',completed,
    'domain_mutations_committed',0,'live_external_call_performed',false),failure_message);
  result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
   'operations_attempted',attempted,'operations_completed',0,'success',false,'failure_code',failure_message,'failure_reason',failure_message,
   'diagnostics',jsonb_build_object('rolled_back_operations',completed,'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
  update public.rollover_execution_runs set run_status='execution_failed',operations_attempted=attempted,operations_completed=0,
   finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
   result_payload=result,diagnostics=diagnostics||result->'diagnostics',failure_reason=failure_message where id=runrow.id;
  return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
 end if;
 result:=jsonb_build_object('execution_id',x.id,'approval_id',a.id,'plan_id',p.id,'operation_count',p.operation_count,
  'operations_attempted',attempted,'operations_completed',completed,'success',true,
  'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',domain_mutations,'live_external_call_performed',false,'publication_performed',false));
 update public.rollover_execution_runs set run_status='executed_successfully',operations_attempted=attempted,operations_completed=completed,
  finished_at=clock_timestamp(),duration_ms=greatest(0,(extract(epoch from clock_timestamp()-run_started)*1000)::bigint),
  result_payload=result,diagnostics=diagnostics||result->'diagnostics' where id=runrow.id;
 return result||jsonb_build_object('idempotent',false,'execution_run_id',runrow.id);
end $$;

create or replace function public.execute_rollover_plan_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
begin
 if p_request?'actor_user_id' or p_request?'executed_by' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;
 if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 return public.execute_rollover_plan_phase3b7c_private(p_request,actor);
end $$;



revoke all on function public.execute_rollover_typed_handler_phase3b7c_private(jsonb,uuid,uuid,uuid,uuid) from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_phase3b7c_private(jsonb,uuid),public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;
commit;
