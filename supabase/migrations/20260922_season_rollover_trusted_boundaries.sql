begin;

-- Additive authenticated boundary for the one certified rollover policy.  The
-- caller supplies only the season identity and must repeat the two fixed policy
-- constants; all authoritative material, actor identity, timestamps and hashes
-- are derived inside this boundary.
create or replace function public.approve_canonical_rollover_policy_private(
 p_request jsonb,
 p_actor uuid
) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public,extensions as $$
declare
 league uuid := nullif(p_request->>'league_id','')::uuid;
 source_year integer := nullif(p_request->>'source_season','')::integer;
 target_year integer := nullif(p_request->>'target_season','')::integer;
 member_count integer;
 payload jsonb;
 fingerprint text;
 existing public.league_rollover_policies%rowtype;
 saved public.league_rollover_policies%rowtype;
begin
 if p_actor is null then raise exception 'authenticated actor required'; end if;
 if p_request is null or jsonb_typeof(p_request)<>'object'
    or p_request - array['league_id','source_season','target_season','deadline_rule','failure_to_act_outcome'] <> '{}'::jsonb then
  raise exception 'canonical policy request fields invalid';
 end if;
 if source_year<>2025 or target_year<>2026 or target_year<>source_year+1 then
  raise exception 'certified rollover policy boundary required';
 end if;
 if p_request->>'deadline_rule'<>'SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE'
    or p_request->>'failure_to_act_outcome'<>'RELEASE_AT_ROLLOVER_TO_COMMISSIONER_HOLD' then
  raise exception 'certified seven-calendar-day policy required';
 end if;
 select count(*) into member_count from public.league_memberships m
 where m.league_id=league and m.user_id=p_actor and lower(m.role) in('commissioner','host','admin');
 if member_count<>1 then raise exception 'exactly one canonical commissioner membership required'; end if;
 perform 1 from public.leagues l where l.id=league for share;
 if not found then raise exception 'canonical league missing'; end if;

 payload:=jsonb_build_object(
  'league_id',league,'source_season',source_year,'target_season',target_year,'version',1,'status','draft',
  'rostered_expired_policy','owner_option_window',
  'off_roster_active_policy','retain_liability_block_second_agreement',
  'free_agent_publication_policy','commissioner_hold_until_rollover_resolution',
  'waiver_policy','waiver_status_must_resolve_before_acquisition',
  'extension_deadline','SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE',
  'taxi_policy','automatic_commissioner_review; taxi unlock does not alter contract; no automatic taxi return',
  'ir_policy','general owner option window plus roster eligibility reconciliation; IR never alters contract lifecycle',
  'dead_cap_policy','zero_percent_only_after_qualifying_early_termination_and_initialized_authority',
  'early_termination_policy','explicit_approved_audited_event_required',
  'cap_adjustment_policy','season_scoped_positive_consumes_negative_credits',
  'draft_rookie_policy','draft_and_rookie_locks_resolve_before_publication','effective_at',null,
  'approved_by',null,'approved_at',null,
  'metadata',jsonb_build_object(
   'active_roster_treatment','owner_option_window',
   'failure_to_act_outcome','RELEASE_AT_ROLLOVER_TO_COMMISSIONER_HOLD',
   'deadline_resolution','each rollover notice timestamp plus seven calendar days produces the exact deadline timestamp',
   'publication_requirements',jsonb_build_array('publication authority initialized','contract-conflict validation','waiver validation','rookie/draft validation','commissioner authorization'))
 );
 fingerprint:=public.rollover_material_fingerprint(payload);
 select * into existing from public.league_rollover_policies p
 where p.league_id=league and p.source_season=source_year and p.target_season=target_year and p.version=1;
 if existing.id is not null then
  if existing.status<>'approved' or existing.fingerprint<>fingerprint
     or existing.metadata->'policy_payload'<>payload then
   raise exception 'changed-material policy replay rejected';
  end if;
  return jsonb_build_object('policy',to_jsonb(existing),'idempotent',true);
 end if;
 if exists(select 1 from public.league_rollover_policies p where p.league_id=league and p.target_season=target_year and p.status='active') then
  raise exception 'conflicting active rollover policy';
 end if;
 insert into public.league_rollover_policies(
  league_id,source_season,target_season,version,status,rostered_expired_policy,
  off_roster_active_policy,free_agent_publication_policy,waiver_policy,extension_deadline,
  taxi_policy,ir_policy,dead_cap_policy,early_termination_policy,cap_adjustment_policy,
  draft_rookie_policy,effective_at,created_by,approved_by,approved_at,metadata,fingerprint)
 values(league,source_year,target_year,1,'approved',payload->>'rostered_expired_policy',
  payload->>'off_roster_active_policy',payload->>'free_agent_publication_policy',payload->>'waiver_policy',null,
  payload->>'taxi_policy',payload->>'ir_policy',payload->>'dead_cap_policy',payload->>'early_termination_policy',
  payload->>'cap_adjustment_policy',payload->>'draft_rookie_policy',null,p_actor,p_actor,clock_timestamp(),
  jsonb_build_object('policy_payload',payload,'deadline_rule',payload->>'extension_deadline',
                     'fingerprint_algorithm','postgres-jsonb-v1'),fingerprint)
 returning * into saved;
 return jsonb_build_object('policy',to_jsonb(saved),'idempotent',false);
end $$;

create or replace function public.approve_canonical_rollover_policy_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=auth.uid();
begin
 if actor is null then raise exception 'authentication required'; end if;
 return public.approve_canonical_rollover_policy_private(p_request,actor);
end $$;

revoke all on function public.approve_canonical_rollover_policy_private(jsonb,uuid) from public,anon,authenticated,service_role;
revoke all on function public.approve_canonical_rollover_policy_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.approve_canonical_rollover_policy_authenticated(jsonb) to authenticated;

commit;
