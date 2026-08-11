begin;

-- Forward-only commissioner authority history. No existing membership is
-- backfilled: absence of durable history must fail reviewed-case freezing.
create table public.league_membership_authority_events (
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 membership_id uuid not null,
 user_id uuid not null references auth.users(id),
 authority_role text not null check(authority_role='commissioner'),
 event_type text not null check(event_type in('authority_granted','authority_revoked')),
 effective_at timestamptz not null,
 source text not null check(source in('membership_trigger','synthetic_disposable_fixture','reviewed_backfill')),
 source_fingerprint text not null check(source_fingerprint~'^[0-9a-f]{64}$'),
 recorded_at timestamptz not null default clock_timestamp(),
 metadata jsonb not null default '{}'::jsonb check(jsonb_typeof(metadata)='object'),
 unique(membership_id,event_type,effective_at)
);
create index league_membership_authority_events_lookup_idx
 on public.league_membership_authority_events(league_id,user_id,effective_at,id);

create or replace function public.capture_commissioner_authority_event_phase3b6c1()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare event_name text;event_league uuid;event_membership uuid;event_user uuid;event_time timestamptz:=clock_timestamp();
begin
 if tg_op='INSERT' and new.role='commissioner' then
  event_name:='authority_granted';event_league:=new.league_id;event_membership:=new.id;event_user:=new.user_id;
 elsif tg_op='DELETE' and old.role='commissioner' then
  event_name:='authority_revoked';event_league:=old.league_id;event_membership:=old.id;event_user:=old.user_id;
 elsif tg_op='UPDATE' and old.role='commissioner' and
       (new.role<>'commissioner' or (new.league_id,new.user_id) is distinct from(old.league_id,old.user_id)) then
  insert into public.league_membership_authority_events(
   league_id,membership_id,user_id,authority_role,event_type,effective_at,source,source_fingerprint)
  values(old.league_id,old.id,old.user_id,'commissioner','authority_revoked',event_time,
   'membership_trigger',public.rollover_material_fingerprint(jsonb_build_object(
    'membership_id',old.id,'league_id',old.league_id,'user_id',old.user_id,'event','authority_revoked','effective_at',event_time)));
  if new.role='commissioner' then
   event_name:='authority_granted';event_league:=new.league_id;event_membership:=new.id;event_user:=new.user_id;
  else return new;end if;
 elsif tg_op='UPDATE' and new.role='commissioner' and old.role<>'commissioner' then
  event_name:='authority_granted';event_league:=new.league_id;event_membership:=new.id;event_user:=new.user_id;
 else return case when tg_op='DELETE' then old else new end;end if;
 insert into public.league_membership_authority_events(
  league_id,membership_id,user_id,authority_role,event_type,effective_at,source,source_fingerprint)
 values(event_league,event_membership,event_user,'commissioner',event_name,event_time,
  'membership_trigger',public.rollover_material_fingerprint(jsonb_build_object(
   'membership_id',event_membership,'league_id',event_league,'user_id',event_user,
   'event',event_name,'effective_at',event_time)));
 return case when tg_op='DELETE' then old else new end;
end $$;
create trigger league_memberships_commissioner_authority_history
after insert or update or delete on public.league_memberships
for each row execute function public.capture_commissioner_authority_event_phase3b6c1();

create table public.rollover_owner_option_snapshot_v2 (
 id uuid primary key default gen_random_uuid(),
 snapshot_id uuid not null unique references public.rollover_execution_input_snapshots(id),
 rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),
 schema_version text not null check(schema_version='phase3b6c1-owner-options-v2'),
 source_v1_component_fingerprint text not null check(source_v1_component_fingerprint~'^[0-9a-f]{64}$'),
 case_count integer not null check(case_count>0),
 review_count integer not null check(review_count>=0),
 case_set_fingerprint text not null check(case_set_fingerprint~'^[0-9a-f]{64}$'),
 review_set_fingerprint text not null check(review_set_fingerprint~'^[0-9a-f]{64}$'),
 aggregate_fingerprint text not null check(aggregate_fingerprint~'^[0-9a-f]{64}$'),
 payload_bytes integer not null check(payload_bytes between 1 and 524288),
 created_by uuid not null,
 created_at timestamptz not null default clock_timestamp()
);

create table public.rollover_owner_option_snapshot_v2_cases (
 id uuid primary key default gen_random_uuid(),
 owner_option_snapshot_v2_id uuid not null references public.rollover_owner_option_snapshot_v2(id),
 eligible_option_case_id uuid not null,
 league_id uuid not null references public.leagues(id),
 closing_season_id uuid not null references public.league_seasons(id),
 closing_season integer not null,target_season_id uuid not null references public.league_seasons(id),
 target_season integer not null,contract_agreement_id uuid not null references public.contract_agreements(id),
 player_id text not null,league_team_id uuid not null references public.league_teams(id),
 decision_id uuid not null,latest_revision_id uuid,commissioner_review_id uuid,
 contract_type text not null,option_type text not null,option_eligibility_type text not null,
 rookie_class_year integer,rookie_draft_year integer,rookie_draft_round integer,
 is_third_round boolean not null,option_term integer not null check(option_term>0),
 option_exercise_season integer not null,guaranteed_salary numeric not null check(guaranteed_salary>=0),
 current_contract_salary numeric not null check(current_contract_salary>=0),
 source_agreement_fingerprint text not null check(source_agreement_fingerprint~'^[0-9a-f]{64}$'),
 submitted_choice text,submitted_at timestamptz,submitted_by uuid,
 submitting_league_team_id uuid,response_source text not null,response_status text not null,
 response_before_deadline boolean not null,is_default_nonresponse boolean not null,
 notice_timestamp timestamptz not null,deadline_timestamp timestamptz not null,
 response_reason_code text not null,response_evidence jsonb not null,
 revision_history jsonb not null,duplicate_conflict_evidence jsonb not null,
 taxi_status text,taxi_source text not null,taxi_cutoff_timestamp timestamptz not null,
 taxi_evidence_fingerprint text not null check(taxi_evidence_fingerprint~'^[0-9a-f]{64}$'),
 option_exercise_eligible boolean not null,exercise_eligibility_reason_code text not null,
 salary_rule_linkage jsonb not null,case_fingerprint text not null check(case_fingerprint~'^[0-9a-f]{64}$'),
 payload_bytes integer not null check(payload_bytes between 1 and 131072),
 created_at timestamptz not null default clock_timestamp(),
 unique(owner_option_snapshot_v2_id,eligible_option_case_id),
 unique(owner_option_snapshot_v2_id,contract_agreement_id),
 check((contract_type='rookie' and rookie_draft_round is not null) or contract_type<>'rookie'),
 check(is_third_round=(contract_type='rookie' and rookie_draft_round=3)),
 check(not is_third_round or guaranteed_salary=1),
 check((submitted_choice is null and submitted_at is null and submitted_by is null and is_default_nonresponse)
    or (submitted_choice is not null and submitted_at is not null and submitted_by is not null and not is_default_nonresponse)),
 check(jsonb_typeof(response_evidence)='object' and jsonb_typeof(revision_history)='array'
  and jsonb_typeof(duplicate_conflict_evidence)='object' and jsonb_typeof(salary_rule_linkage)='object')
);

create table public.rollover_owner_option_snapshot_v2_reviews (
 id uuid primary key default gen_random_uuid(),
 owner_option_snapshot_v2_id uuid not null references public.rollover_owner_option_snapshot_v2(id),
 review_id uuid not null,eligible_option_case_id uuid not null,reviewer_user_id uuid not null,
 reviewer_membership_id uuid not null,reviewer_league_team_id uuid,
 review_timestamp timestamptz not null,disposition text not null,review_state text not null,
 superseded boolean not null,reason_code text not null,reason_explanation text not null,
 decision_id uuid not null,contract_agreement_id uuid not null,player_id text not null,
 authority_event_id uuid not null references public.league_membership_authority_events(id),
 authority_source_version text not null,authorized_at_review_time boolean not null check(authorized_at_review_time),
 review_payload jsonb not null check(jsonb_typeof(review_payload)='object'),
 review_fingerprint text not null check(review_fingerprint~'^[0-9a-f]{64}$'),
 payload_bytes integer not null check(payload_bytes between 1 and 131072),
 created_at timestamptz not null default clock_timestamp(),
 unique(owner_option_snapshot_v2_id,review_id)
);

create index rollover_owner_option_snapshot_v2_cases_order_idx
 on public.rollover_owner_option_snapshot_v2_cases(owner_option_snapshot_v2_id,eligible_option_case_id);
create index rollover_owner_option_snapshot_v2_reviews_order_idx
 on public.rollover_owner_option_snapshot_v2_reviews(owner_option_snapshot_v2_id,eligible_option_case_id,review_id);

create or replace function public.reject_phase3b6c1_immutable_mutation()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin raise exception 'Phase 3B.6C.1 authority evidence is immutable';end $$;
create trigger league_membership_authority_events_immutable before update or delete
 on public.league_membership_authority_events for each row execute function public.reject_phase3b6c1_immutable_mutation();
create trigger rollover_owner_option_snapshot_v2_immutable before update or delete
 on public.rollover_owner_option_snapshot_v2 for each row execute function public.reject_phase3b6c1_immutable_mutation();
create trigger rollover_owner_option_snapshot_v2_cases_immutable before update or delete
 on public.rollover_owner_option_snapshot_v2_cases for each row execute function public.reject_phase3b6c1_immutable_mutation();
create trigger rollover_owner_option_snapshot_v2_reviews_immutable before update or delete
 on public.rollover_owner_option_snapshot_v2_reviews for each row execute function public.reject_phase3b6c1_immutable_mutation();

alter table public.league_membership_authority_events enable row level security;
alter table public.rollover_owner_option_snapshot_v2 enable row level security;
alter table public.rollover_owner_option_snapshot_v2_cases enable row level security;
alter table public.rollover_owner_option_snapshot_v2_reviews enable row level security;
revoke all on public.league_membership_authority_events,public.rollover_owner_option_snapshot_v2,
 public.rollover_owner_option_snapshot_v2_cases,public.rollover_owner_option_snapshot_v2_reviews
 from public,anon,authenticated;
grant select,insert on public.league_membership_authority_events,public.rollover_owner_option_snapshot_v2,
 public.rollover_owner_option_snapshot_v2_cases,public.rollover_owner_option_snapshot_v2_reviews to service_role;

create policy membership_authority_events_commissioner_read on public.league_membership_authority_events
 for select to authenticated using(exists(select 1 from public.league_memberships m
  where m.league_id=league_membership_authority_events.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy owner_option_snapshot_v2_commissioner_read on public.rollover_owner_option_snapshot_v2
 for select to authenticated using(exists(select 1 from public.league_memberships m
  where m.league_id=rollover_owner_option_snapshot_v2.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy owner_option_snapshot_v2_cases_commissioner_read on public.rollover_owner_option_snapshot_v2_cases
 for select to authenticated using(exists(select 1 from public.league_memberships m
  where m.league_id=rollover_owner_option_snapshot_v2_cases.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy owner_option_snapshot_v2_reviews_commissioner_read on public.rollover_owner_option_snapshot_v2_reviews
 for select to authenticated using(exists(select 1 from public.rollover_owner_option_snapshot_v2 s
  join public.league_memberships m on m.league_id=s.league_id
  where s.id=owner_option_snapshot_v2_id and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.raise_phase3b6c1_failure(p_code text,p_details jsonb default '{}'::jsonb)
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
begin raise exception using errcode='P0001',message=p_code,
 detail=jsonb_build_object('failure_code',p_code,'details',coalesce(p_details,'{}'::jsonb))::text;end $$;

create or replace function public.execute_rollover_typed_handler_phase3b6c1_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 base_result jsonb;s public.rollover_execution_input_snapshots%rowtype;
 v2 public.rollover_owner_option_snapshot_v2%rowtype;c public.rollover_execution_input_snapshot_components%rowtype;
 x public.rollover_executions%rowtype;src public.league_seasons%rowtype;tgt public.league_seasons%rowtype;
 d jsonb;live_d jsonb;r jsonb;rv jsonb;cases jsonb:='[]';reviews jsonb:='[]';case_payload jsonb;review_payload jsonb;
 agreement public.contract_agreements%rowtype;source_obligation public.contract_seasons%rowtype;
 option_obligation public.contract_seasons%rowtype;player public.player_universe%rowtype;
 latest_revision jsonb;review_row jsonb;authority public.league_membership_authority_events%rowtype;
 draft_round integer;is_third boolean;option_term integer;submitted_choice text;submitted_at timestamptz;
 submitted_by uuid;defaulted boolean;before_deadline boolean;taxi_status text;eligible boolean;
 case_fp text;review_fp text;case_set_fp text;review_set_fp text;aggregate_fp text;v2_id uuid:=gen_random_uuid();
 payload_size integer;review_count integer:=0;component_fp text;rules jsonb;
begin
 if p_operation->>'operation_type'<>'FREEZE_FINAL_EXECUTION_INPUTS' then
  return public.execute_rollover_typed_handler_phase3b6c_private(
   p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 end if;
 base_result:=public.execute_rollover_typed_handler_phase3b6c_private(
  p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);
 select * into s from public.rollover_execution_input_snapshots where rollover_execution_id=p_rollover_execution_id;
 select * into v2 from public.rollover_owner_option_snapshot_v2 where snapshot_id=s.id;
 if v2.id is not null then
  return base_result||jsonb_build_object('owner_option_snapshot_v2',jsonb_build_object(
   'id',v2.id,'schema_version',v2.schema_version,'case_count',v2.case_count,
   'review_count',v2.review_count,'aggregate_fingerprint',v2.aggregate_fingerprint,'rows_written',0));
 end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into src from public.league_seasons where league_id=x.league_id and season=x.source_season;
 select * into tgt from public.league_seasons where league_id=x.league_id and season=x.target_season;
 select * into c from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id and component_name='owner_options';
 if c.id is null or c.component_schema_version<>'phase3b6c-owner_options-v1' then
  perform public.raise_phase3b6c1_failure('option_snapshot_schema_unsupported','{}');
 end if;
 component_fp:=c.component_fingerprint;
 select canonical_payload into rules from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id and component_name='rollover_policy';

 perform 1 from public.contract_agreements a join jsonb_array_elements(c.canonical_payload->'decisions') q(value)
  on a.id=(q.value->>'agreement_id')::uuid order by a.id for share of a;
 perform 1 from public.contract_seasons cs join jsonb_array_elements(c.canonical_payload->'decisions') q(value)
  on cs.contract_id=(q.value->>'agreement_id')::uuid order by cs.id for share of cs;
 perform 1 from public.player_universe u join jsonb_array_elements(c.canonical_payload->'decisions') q(value)
  on u.sleeper_id=q.value->>'player_id' order by u.sleeper_id for share of u;
 perform 1 from public.league_membership_authority_events e where e.league_id=x.league_id order by e.effective_at,e.id for share;

 for d in select value from jsonb_array_elements(c.canonical_payload->'decisions') q(value) order by (value->>'id')::uuid loop
  if nullif(d->>'id','') is null or nullif(d->>'agreement_id','') is null
   or nullif(d->>'player_id','') is null or nullif(d->>'league_team_id','') is null then
   perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('reason','option_case_identity_missing'));
  end if;
  select * into agreement from public.contract_agreements where id=(d->>'agreement_id')::uuid;
  select to_jsonb(od) into live_d from public.rollover_owner_decisions od
   where od.id=(d->>'id')::uuid and od.rollover_execution_id=x.id;
  select * into source_obligation from public.contract_seasons where contract_id=agreement.id and season=x.source_season;
  select * into option_obligation from public.contract_seasons where contract_id=agreement.id and season=x.target_season and is_option_year;
  select * into player from public.player_universe where sleeper_id=d->>'player_id';
  if agreement.id is null or agreement.league_id<>x.league_id or agreement.league_team_id<>(d->>'league_team_id')::uuid
    or agreement.player_id<>d->>'player_id' or agreement.contract_type='unknown' then
   perform public.raise_phase3b6c1_failure('option_contract_classification_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  if option_obligation.id is null or nullif(option_obligation.option_type,'') is null then
   perform public.raise_phase3b6c1_failure('option_type_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  if source_obligation.id is null then
   perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('reason','source_salary_missing'));
  end if;
  draft_round:=player.draft_round;
  if agreement.contract_type='rookie' and draft_round is null then
   perform public.raise_phase3b6c1_failure('rookie_draft_round_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  is_third:=agreement.contract_type='rookie' and draft_round=3;
  if is_third and (player.is_rookie_contract is false or player.is_rookie_contract is null) then
   perform public.raise_phase3b6c1_failure('third_round_classification_ambiguous',jsonb_build_object('decision_id',d->>'id'));
  end if;
  option_term:=(select count(*) from public.contract_seasons where contract_id=agreement.id and season>=x.target_season and is_option_year);
  if option_term=0 then perform public.raise_phase3b6c1_failure('option_term_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if option_obligation.guaranteed_salary is null or (is_third and option_obligation.guaranteed_salary<>1) then
   perform public.raise_phase3b6c1_failure('guaranteed_salary_evidence_missing',jsonb_build_object('decision_id',d->>'id'));
  end if;
  latest_revision:=(select value from jsonb_array_elements(c.canonical_payload->'revisions') q(value)
   where value->>'owner_decision_id'=d->>'id' order by (value->>'revision_number')::integer desc,(value->>'id')::uuid desc limit 1);
  if exists(select 1 from jsonb_array_elements(c.canonical_payload->'revisions') q(value)
   where value->>'owner_decision_id'=d->>'id' group by value->>'revision_number' having count(*)>1) then
   perform public.raise_phase3b6c1_failure('owner_response_evidence_incomplete',jsonb_build_object('reason','revision_conflict'));
  end if;
  if live_d is null then perform public.raise_phase3b6c1_failure(
   'option_snapshot_v2_incomplete',jsonb_build_object('reason','decision_identity_missing'));end if;
  submitted_choice:=live_d->>'owner_choice';submitted_at:=nullif(live_d->>'submitted_at','')::timestamptz;
  submitted_by:=nullif(live_d->>'submitted_by','')::uuid;
  defaulted:=submitted_choice is null and live_d->>'decision_status'='no_response';
  if not defaulted and submitted_choice is null then perform public.raise_phase3b6c1_failure('owner_response_identity_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if not defaulted and submitted_at is null then perform public.raise_phase3b6c1_failure('owner_response_timestamp_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if not defaulted and submitted_by is null then perform public.raise_phase3b6c1_failure('owner_response_identity_missing',jsonb_build_object('decision_id',d->>'id'));end if;
  if submitted_by is not null and not exists(select 1 from (select value from jsonb_array_elements(
   (select canonical_payload->'memberships' from public.rollover_execution_input_snapshot_components where snapshot_id=s.id and component_name='team_mapping')) q(value)) z
   where z.value->>'user_id'=submitted_by::text and z.value->>'league_team_id'=d->>'league_team_id')
   and not exists(select 1 from public.league_membership_authority_events e
    where e.league_id=x.league_id and e.user_id=submitted_by and e.event_type='authority_granted'
     and e.effective_at<=submitted_at and not exists(select 1 from public.league_membership_authority_events z
      where z.league_id=e.league_id and z.user_id=e.user_id and z.event_type='authority_revoked'
       and z.effective_at>e.effective_at and z.effective_at<=submitted_at)) then
   perform public.raise_phase3b6c1_failure('owner_response_actor_mismatch',jsonb_build_object('decision_id',d->>'id'));
  end if;
  before_deadline:=coalesce(submitted_at<=nullif(d->>'deadline','')::timestamptz,false);
  taxi_status:=lower(coalesce(d->>'initial_roster_slot',d->>'initial_roster_status','unknown'));
  eligible:=not(is_third and taxi_status='taxi');
  review_row:=(select value from jsonb_array_elements(c.canonical_payload->'commissioner_reviews') q(value)
   where value->>'player_id'=d->>'player_id' and value->>'agreement_id'=d->>'agreement_id'
   order by (value->>'revision_number')::integer desc,(value->>'id')::uuid desc limit 1);
  case_payload:=jsonb_build_object(
   'eligible_option_case_id',d->>'id','league_id',x.league_id,'closing_season_id',src.id,'closing_season',x.source_season,
   'target_season_id',tgt.id,'target_season',x.target_season,'contract_agreement_id',agreement.id,
   'player_id',agreement.player_id,'league_team_id',agreement.league_team_id,'decision_id',d->>'id',
   'latest_revision_id',latest_revision->>'id','commissioner_review_id',review_row->>'id',
   'contract_type',agreement.contract_type,'option_type',option_obligation.option_type,
   'option_eligibility_type',case when is_third then 'third_round_rookie_owner_option' else 'other_owner_option' end,
   'rookie_class_year',player.rookie_class_year,'rookie_draft_year',player.draft_year,
   'rookie_draft_round',draft_round,'is_third_round',is_third,'option_term',option_term,
   'option_exercise_season',x.target_season,'guaranteed_salary',option_obligation.guaranteed_salary,
   'current_contract_salary',source_obligation.salary,
   'source_agreement_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('agreement',to_jsonb(agreement)-'created_at'-'updated_at','source_obligation',to_jsonb(source_obligation)-'created_at'-'updated_at','option_obligation',to_jsonb(option_obligation)-'created_at'-'updated_at','player_classification',jsonb_build_object('rookie_class_year',player.rookie_class_year,'draft_year',player.draft_year,'draft_round',player.draft_round,'is_rookie_contract',player.is_rookie_contract))),
   'submitted_choice',submitted_choice,'submitted_at',submitted_at,'submitted_by',submitted_by,
   'submitting_league_team_id',case when submitted_by is null then null else d->>'league_team_id' end,
   'response_source',case when latest_revision is null then 'rollover_owner_decisions' else 'rollover_owner_decision_revisions' end,
   'response_status',live_d->>'decision_status','response_before_deadline',before_deadline,
   'is_default_nonresponse',defaulted,'notice_timestamp',c.canonical_payload->>'notice_timestamp',
   'deadline_timestamp',c.canonical_payload->>'owner_deadline',
   'response_reason_code',case when defaulted then 'no_response_default' else 'frozen_owner_response' end,
   'response_evidence',jsonb_build_object('decision_evidence',coalesce(live_d->'evidence','{}'::jsonb),'latest_revision',latest_revision),
   'revision_history',coalesce((select jsonb_agg(value order by (value->>'revision_number')::integer,(value->>'id')::uuid) from jsonb_array_elements(c.canonical_payload->'revisions') q(value) where value->>'owner_decision_id'=d->>'id'),'[]'::jsonb),
   'duplicate_conflict_evidence',jsonb_build_object('duplicate_decision_count',1,'revision_conflict',false),
   'taxi_status',taxi_status,'taxi_source','frozen_initial_roster_slot','taxi_cutoff_timestamp',c.canonical_payload->>'owner_deadline',
   'taxi_evidence_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('decision_id',d->>'id','slot',taxi_status,'cutoff',c.canonical_payload->>'owner_deadline')),
   'option_exercise_eligible',eligible,'exercise_eligibility_reason_code',case when eligible then 'eligible' else 'third_round_taxi_prohibited' end,
   'salary_rule_linkage',jsonb_build_object('applies',is_third,'salary_cap',rules->>'current_salary_cap','denominator',225,'base_option',7,'guarantee',1,'rounding','round_half_up','no_compounding',true));
  case_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-option-case-v2','payload',case_payload));
  cases:=cases||jsonb_build_array(case_payload||jsonb_build_object('case_fingerprint',case_fp));

  if review_row is not null then
   if nullif(review_row->>'decision_by','') is null or nullif(review_row->>'decision_at','') is null then
    perform public.raise_phase3b6c1_failure('review_authority_history_missing',jsonb_build_object('review_id',review_row->>'id'));
   end if;
   select * into authority from public.league_membership_authority_events e
    where e.league_id=x.league_id and e.user_id=(review_row->>'decision_by')::uuid
     and e.event_type='authority_granted' and e.effective_at<=(review_row->>'decision_at')::timestamptz
     and not exists(select 1 from public.league_membership_authority_events z
      where z.league_id=e.league_id and z.user_id=e.user_id and z.event_type='authority_revoked'
       and z.effective_at>e.effective_at and z.effective_at<=(review_row->>'decision_at')::timestamptz)
    order by e.effective_at desc,e.id desc limit 1;
   if authority.id is null then perform public.raise_phase3b6c1_failure('reviewer_not_authorized_at_review_time',jsonb_build_object('review_id',review_row->>'id'));end if;
   review_payload:=jsonb_build_object('review_id',review_row->>'id','eligible_option_case_id',d->>'id',
    'reviewer_user_id',review_row->>'decision_by','reviewer_membership_id',authority.membership_id,
    'reviewer_league_team_id',null,'review_timestamp',review_row->>'decision_at',
    'disposition',coalesce(review_row->>'outcome',review_row->>'approved_action',review_row->>'review_status'),
    'review_state',review_row->>'review_state','superseded',review_row->>'review_state'='superseded',
    'reason_code',coalesce(review_row#>>'{evidence,reason_code}',review_row#>>'{metadata,reason_code}','frozen_commissioner_review'),
    'reason_explanation',coalesce(review_row#>>'{evidence,reason}',review_row#>>'{metadata,reason}','frozen reviewed disposition'),
    'decision_id',d->>'id','contract_agreement_id',agreement.id,'player_id',agreement.player_id,
    'authority_event_id',authority.id,'authority_source_version','league-membership-authority-events-v1',
    'authorized_at_review_time',true,'review_payload',review_row);
   review_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-review-v2','payload',review_payload));
   reviews:=reviews||jsonb_build_array(review_payload||jsonb_build_object('review_fingerprint',review_fp));review_count:=review_count+1;
  end if;
 end loop;
 if jsonb_array_length(cases)<>c.record_count then perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('expected',c.record_count,'actual',jsonb_array_length(cases)));end if;
 payload_size:=octet_length(cases::text)+octet_length(reviews::text);
 if payload_size>524288 or lower((cases||reviews)::text)~'"(password|secret|token|credential)[^"]*"[[:space:]]*:' then
  perform public.raise_phase3b6c1_failure('option_snapshot_v2_incomplete',jsonb_build_object('reason','payload_safety'));
 end if;
 case_set_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-option-case-set-v2','cases',cases));
 review_set_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-review-set-v2','reviews',reviews));
 aggregate_fp:=public.rollover_material_fingerprint(jsonb_build_object('schema_version','phase3b6c1-owner-options-v2','source_v1_component_fingerprint',component_fp,'case_set_fingerprint',case_set_fp,'review_set_fingerprint',review_set_fp));
 insert into public.rollover_owner_option_snapshot_v2(id,snapshot_id,rollover_execution_id,league_id,schema_version,
  source_v1_component_fingerprint,case_count,review_count,case_set_fingerprint,review_set_fingerprint,
  aggregate_fingerprint,payload_bytes,created_by)
 values(v2_id,s.id,x.id,x.league_id,'phase3b6c1-owner-options-v2',component_fp,jsonb_array_length(cases),
  review_count,case_set_fp,review_set_fp,aggregate_fp,payload_size,p_actor);
 for case_payload in select value from jsonb_array_elements(cases) q(value) order by (value->>'eligible_option_case_id')::uuid loop
  insert into public.rollover_owner_option_snapshot_v2_cases(
   owner_option_snapshot_v2_id,eligible_option_case_id,league_id,closing_season_id,closing_season,target_season_id,target_season,
   contract_agreement_id,player_id,league_team_id,decision_id,latest_revision_id,commissioner_review_id,
   contract_type,option_type,option_eligibility_type,rookie_class_year,rookie_draft_year,rookie_draft_round,is_third_round,
   option_term,option_exercise_season,guaranteed_salary,current_contract_salary,source_agreement_fingerprint,
   submitted_choice,submitted_at,submitted_by,submitting_league_team_id,response_source,response_status,response_before_deadline,
   is_default_nonresponse,notice_timestamp,deadline_timestamp,response_reason_code,response_evidence,revision_history,
   duplicate_conflict_evidence,taxi_status,taxi_source,taxi_cutoff_timestamp,taxi_evidence_fingerprint,
   option_exercise_eligible,exercise_eligibility_reason_code,salary_rule_linkage,case_fingerprint,payload_bytes)
  values(v2_id,(case_payload->>'eligible_option_case_id')::uuid,x.league_id,(case_payload->>'closing_season_id')::uuid,
   (case_payload->>'closing_season')::integer,(case_payload->>'target_season_id')::uuid,(case_payload->>'target_season')::integer,
   (case_payload->>'contract_agreement_id')::uuid,case_payload->>'player_id',(case_payload->>'league_team_id')::uuid,
   (case_payload->>'decision_id')::uuid,nullif(case_payload->>'latest_revision_id','')::uuid,nullif(case_payload->>'commissioner_review_id','')::uuid,
   case_payload->>'contract_type',case_payload->>'option_type',case_payload->>'option_eligibility_type',
   nullif(case_payload->>'rookie_class_year','')::integer,nullif(case_payload->>'rookie_draft_year','')::integer,
   nullif(case_payload->>'rookie_draft_round','')::integer,(case_payload->>'is_third_round')::boolean,
   (case_payload->>'option_term')::integer,(case_payload->>'option_exercise_season')::integer,
   (case_payload->>'guaranteed_salary')::numeric,(case_payload->>'current_contract_salary')::numeric,
   case_payload->>'source_agreement_fingerprint',case_payload->>'submitted_choice',nullif(case_payload->>'submitted_at','')::timestamptz,
   nullif(case_payload->>'submitted_by','')::uuid,nullif(case_payload->>'submitting_league_team_id','')::uuid,
   case_payload->>'response_source',case_payload->>'response_status',(case_payload->>'response_before_deadline')::boolean,
   (case_payload->>'is_default_nonresponse')::boolean,(case_payload->>'notice_timestamp')::timestamptz,
   (case_payload->>'deadline_timestamp')::timestamptz,case_payload->>'response_reason_code',case_payload->'response_evidence',
   case_payload->'revision_history',case_payload->'duplicate_conflict_evidence',case_payload->>'taxi_status',case_payload->>'taxi_source',
   (case_payload->>'taxi_cutoff_timestamp')::timestamptz,case_payload->>'taxi_evidence_fingerprint',
   (case_payload->>'option_exercise_eligible')::boolean,case_payload->>'exercise_eligibility_reason_code',
   case_payload->'salary_rule_linkage',case_payload->>'case_fingerprint',octet_length(case_payload::text));
 end loop;
 for review_payload in select value from jsonb_array_elements(reviews) q(value) order by (value->>'review_id')::uuid loop
  insert into public.rollover_owner_option_snapshot_v2_reviews(
   owner_option_snapshot_v2_id,review_id,eligible_option_case_id,reviewer_user_id,reviewer_membership_id,
   reviewer_league_team_id,review_timestamp,disposition,review_state,superseded,reason_code,reason_explanation,
   decision_id,contract_agreement_id,player_id,authority_event_id,authority_source_version,
   authorized_at_review_time,review_payload,review_fingerprint,payload_bytes)
  values(v2_id,(review_payload->>'review_id')::uuid,(review_payload->>'eligible_option_case_id')::uuid,
   (review_payload->>'reviewer_user_id')::uuid,(review_payload->>'reviewer_membership_id')::uuid,
   nullif(review_payload->>'reviewer_league_team_id','')::uuid,(review_payload->>'review_timestamp')::timestamptz,
   review_payload->>'disposition',review_payload->>'review_state',(review_payload->>'superseded')::boolean,
   review_payload->>'reason_code',review_payload->>'reason_explanation',(review_payload->>'decision_id')::uuid,
   (review_payload->>'contract_agreement_id')::uuid,review_payload->>'player_id',(review_payload->>'authority_event_id')::uuid,
   review_payload->>'authority_source_version',(review_payload->>'authorized_at_review_time')::boolean,
   review_payload->'review_payload',review_payload->>'review_fingerprint',octet_length(review_payload::text));
 end loop;
 return base_result||jsonb_build_object('owner_option_snapshot_v2',jsonb_build_object(
  'id',v2_id,'schema_version','phase3b6c1-owner-options-v2','case_count',jsonb_array_length(cases),
  'review_count',review_count,'case_set_fingerprint',case_set_fp,'review_set_fingerprint',review_set_fp,
  'aggregate_fingerprint',aggregate_fp,'rows_written',1+jsonb_array_length(cases)+review_count));
end $$;

-- New engine version changes only operation-6 dispatch. Operations 1-5 and 7
-- delegate to their certified handlers; operations 8-36 remain fail-closed.
create or replace function public.execute_rollover_plan_phase3b6c1_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 x public.rollover_executions%rowtype;a public.rollover_execution_plan_approvals%rowtype;p public.rollover_execution_plans%rowtype;
 l public.rollover_execution_locks%rowtype;prior public.rollover_execution_runs%rowtype;runrow public.rollover_execution_runs%rowtype;
 op jsonb;handler_result jsonb;op_started timestamptz;run_started timestamptz:=clock_timestamp();
 k text:=nullif(btrim(p_request->>'idempotency_key'),'');material jsonb;request_fp text;
 attempted integer:=0;completed integer:=0;failed_op jsonb;failure_sqlstate text;failure_message text;
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
  jsonb_build_object('engine_version','phase3b6c1-v1','typed_handlers',true,'publication_performed',false)) returning * into runrow;
 update public.rollover_execution_runs set run_status='executing' where id=runrow.id;
 begin
  for op in select value from jsonb_array_elements(p.ordered_operations) with ordinality q(value,ord) order by ord loop
   attempted:=attempted+1;failed_op:=op;op_started:=clock_timestamp();
   if (op->>'operation_index')::integer is distinct from attempted or op->>'operation_fingerprint' is distinct from a.operation_fingerprints->>(attempted-1) then raise exception 'ordered operation sequence mismatch';end if;
   if op->>'operation_type' in('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY','VERIFY_TARGET_SLEEPER_LINKAGE',
    'VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED','FREEZE_FINAL_EXECUTION_INPUTS','VERIFY_IMMUTABLE_HISTORY_CAPTURE') then
    handler_result:=public.execute_rollover_typed_handler_phase3b6c1_private(op,x.id,p.id,a.id,p_actor);
   else raise exception 'unsupported Phase 3B.6C.1 operation type: %',op->>'operation_type';end if;
   insert into public.rollover_execution_operation_results(execution_run_id,rollover_execution_id,operation_id,operation_index,
    operation_type,operation_fingerprint,operation_status,started_at,finished_at,duration_ms,result_payload,diagnostics)
   values(runrow.id,x.id,(op->>'operation_id')::uuid,attempted,op->>'operation_type',op->>'operation_fingerprint','completed',op_started,
    clock_timestamp(),greatest(0,(extract(epoch from clock_timestamp()-op_started)*1000)::bigint),handler_result,
    jsonb_build_object('domain_mutations',0,'handler_version',coalesce(handler_result->'handler_version','null'::jsonb)));
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
  'diagnostics',jsonb_build_object('typed_handlers',true,'domain_mutations_committed',0,'live_external_call_performed',false,'publication_performed',false));
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
 return public.execute_rollover_plan_phase3b6c1_private(p_request,actor);
end $$;

revoke all on function public.capture_commissioner_authority_event_phase3b6c1(),public.raise_phase3b6c1_failure(text,jsonb),
 public.execute_rollover_typed_handler_phase3b6c1_private(jsonb,uuid,uuid,uuid,uuid),
 public.execute_rollover_plan_phase3b6c1_private(jsonb,uuid),public.execute_rollover_plan_authenticated(jsonb)
 from public,anon,authenticated,service_role;
grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;

comment on table public.league_membership_authority_events is
 'Forward-only commissioner authority events; intentionally contains no inferred pre-migration backfill.';
comment on table public.rollover_owner_option_snapshot_v2 is
 'Immutable Phase 3B.6C.1 extension linked to the unchanged Phase 3B.6C snapshot header.';
comment on function public.execute_rollover_plan_phase3b6c1_private(jsonb,uuid) is
 'Phase 3B.6C.1 operation-6 v2 authority extension; operations 8-36 remain unavailable.';

commit;
