begin;

-- One commissioner-directed exception for one league and one boundary.  The
-- ordinary seven-day close implementation is intentionally not replaced.
create table public.abs_immediate_rollover_authorities (
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 source_season integer not null,
 target_season integer not null,
 authority_key text not null unique,
 reason text not null,
 prior_rule text not null,
 required_confirmation text not null,
 owner_case_count integer not null check(owner_case_count=0),
 classification_population_fingerprint text not null check(classification_population_fingerprint~'^[0-9a-f]{64}$'),
 evidence_fingerprint text not null check(evidence_fingerprint~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default clock_timestamp(),
 authorized_by uuid,
 authorized_at timestamptz,
 rollover_execution_id uuid references public.rollover_executions(id),
 consumed_at timestamptz,
 check(target_season=source_season+1),
 check((consumed_at is null and authorized_by is null and authorized_at is null and rollover_execution_id is null)
    or (consumed_at is not null and authorized_by is not null and authorized_at is not null and rollover_execution_id is not null))
);
alter table public.abs_immediate_rollover_authorities enable row level security;
revoke all on public.abs_immediate_rollover_authorities from public,anon,authenticated;
grant select,insert,update on public.abs_immediate_rollover_authorities to service_role;
create policy abs_immediate_authority_commissioner_read on public.abs_immediate_rollover_authorities
 for select to authenticated using(exists(select 1 from public.league_memberships m
  where m.league_id=abs_immediate_rollover_authorities.league_id and m.user_id=auth.uid()
   and lower(m.role) in('commissioner','host','admin')));

create or replace function public.guard_abs_immediate_rollover_authority_private()
returns trigger
language plpgsql
set search_path=pg_catalog,public
as $function$
begin
 if old.consumed_at is not null then
  raise exception 'ABS immediate rollover authority is immutable after consumption';
 end if;

 if new.id<>old.id
  or new.league_id<>old.league_id
  or new.source_season<>old.source_season
  or new.target_season<>old.target_season
  or new.authority_key<>old.authority_key
  or new.reason<>old.reason
  or new.prior_rule<>old.prior_rule
  or new.required_confirmation<>old.required_confirmation
  or new.owner_case_count<>old.owner_case_count
  or new.classification_population_fingerprint<>old.classification_population_fingerprint
  or new.evidence_fingerprint<>old.evidence_fingerprint
  or new.created_at<>old.created_at
  or new.consumed_at is null
  or new.authorized_by is null
  or new.authorized_at is null
  or new.rollover_execution_id is null
 then
  raise exception 'Only complete one-time authority consumption is permitted';
 end if;

 return new;
end;
$function$;
create trigger guard_abs_immediate_rollover_authority before update on public.abs_immediate_rollover_authorities
 for each row execute function public.guard_abs_immediate_rollover_authority_private();

-- Fail closed against the exact certified pre-correction population.
do $$declare lid constant uuid:='9838a0a1-97c6-4cab-bb88-af177317abfe';changed_players text[];begin
 perform pg_advisory_xact_lock(hashtextextended('abs-immediate-rollover:'||lid||':2025:2026',0));
 if (select count(*) from public.contract_rollover_classifications
      where league_id=lid and source_season=2025 and target_season=2026)<>211 then
  raise exception 'ABS immediate rollover expected exactly 211 classifications';end if;
 if (select count(*) from public.contract_rollover_classifications
      where league_id=lid and source_season=2025 and target_season=2026
       and classification='rookie_option_eligible')<>3 then
  raise exception 'ABS immediate rollover expected exactly three option cases';end if;
 select array_agg(player_id order by player_id) into changed_players
 from public.contract_rollover_classifications where league_id=lid and source_season=2025
  and target_season=2026 and classification='rookie_option_eligible';
 if changed_players<>array['12483','12512','12547']::text[] then
  raise exception 'ABS immediate rollover option player set mismatch:%',changed_players;end if;
 if exists(select 1 from public.contract_rollover_classifications where league_id=lid
  and source_season=2025 and target_season=2026 and player_id=any(changed_players)
  and (classification='rookie_initial_taxi_paused' or taxi_assignment_id is not null)) then
  raise exception 'ABS immediate rollover option case overlaps taxi authority';end if;
 if (select count(*) from public.contract_rollover_classifications where league_id=lid
  and source_season=2025 and target_season=2026 and classification='rookie_initial_taxi_paused')<>9 then
  raise exception 'ABS immediate rollover taxi population mismatch';end if;
end$$;

update public.contract_rollover_classifications c set
 classification='ordinary_expiration',
 rookie_draft_assignment_id=null,
 classification_evidence=c.classification_evidence||jsonb_build_object(
  'prior_classification','rookie_option_eligible',
  'preserved_rookie_draft_assignment_id',c.rookie_draft_assignment_id,
  'commissioner_directed_one_time_immediate_rollover',true,
  'authority_key','abs:2025:2026:immediate-rollover-authority:v1',
  'disposition','expire_without_owner_option_decision'),
 deterministic_fingerprint=public.rollover_material_fingerprint(jsonb_build_object(
  'agreement',c.contract_agreement_id,'player',c.player_id,'classification','ordinary_expiration',
  'source',2025,'target',2026,'authority','abs:2025:2026:immediate-rollover-authority:v1',
  'prior_classification','rookie_option_eligible','rookie_draft_assignment',c.rookie_draft_assignment_id))
where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and c.source_season=2025
 and c.target_season=2026 and c.classification='rookie_option_eligible'
 and c.player_id in('12512','12483','12547');

do $$declare lid constant uuid:='9838a0a1-97c6-4cab-bb88-af177317abfe';begin
 if (select count(*) from public.contract_rollover_classifications where league_id=lid and source_season=2025 and target_season=2026)<>211
  or (select count(*) from public.contract_rollover_classifications where league_id=lid and source_season=2025 and target_season=2026 and classification='ordinary_continuing')<>74
  or (select count(*) from public.contract_rollover_classifications where league_id=lid and source_season=2025 and target_season=2026 and classification='ordinary_expiration')<>116
  or (select count(*) from public.contract_rollover_classifications where league_id=lid and source_season=2025 and target_season=2026 and classification='rookie_initial_continuing')<>12
  or (select count(*) from public.contract_rollover_classifications where league_id=lid and source_season=2025 and target_season=2026 and classification='rookie_initial_taxi_paused')<>9
  or (select count(*) from public.contract_rollover_classifications where league_id=lid and source_season=2025 and target_season=2026 and classification='rookie_option_eligible')<>0
 then raise exception 'ABS immediate rollover corrected classification counts mismatch';end if;
end$$;

-- Re-certify only the existing ABs reconciliation row. Historical transition
-- facts and rookie board rows remain intact; the new directive is additive.
with material as(select jsonb_build_object(
 'agreements',(select jsonb_agg(to_jsonb(a)-'updated_at' order by a.id) from public.contract_agreements a where a.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe'),
 'seasons',(select jsonb_agg(to_jsonb(s)-'updated_at' order by s.id) from public.contract_seasons s where s.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe'),
 'classifications',(select jsonb_agg(to_jsonb(c)-'created_at' order by c.contract_agreement_id) from public.contract_rollover_classifications c where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe')) value)
update public.contract_transition_reconciliations r set
 after_fingerprint=public.rollover_material_fingerprint(material.value),
 actual_counts=r.actual_counts||jsonb_build_object('ordinary_expirations',116,'prepared_options',0,'taxi_paused',9,'classifications',211),
 evidence=r.evidence||jsonb_build_object('legacy_transition_preserved',true,
  'one_time_immediate_rollover_authority',jsonb_build_object('authority_key','abs:2025:2026:immediate-rollover-authority:v1',
   'converted_player_ids',jsonb_build_array('12512','12483','12547'),'from','rookie_option_eligible','to','ordinary_expiration',
   'owner_decisions_required',0,'commissioner_directed',true)),
 certified_at=clock_timestamp()
from material where r.reconciliation_key='abs:2025:2026:legacy-transition-reconciliation:v1'
 and r.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and r.source_season=2025 and r.target_season=2026;

do $$begin
 if not exists(select 1 from public.contract_transition_reconciliations
  where reconciliation_key='abs:2025:2026:legacy-transition-reconciliation:v1'
   and (actual_counts->>'ordinary_expirations')::integer=116
   and (actual_counts->>'prepared_options')::integer=0
   and evidence->'one_time_immediate_rollover_authority'->>'commissioner_directed'='true') then
  raise exception 'ABS immediate rollover reconciliation recertification failed';end if;
end$$;

with population as(select public.rollover_material_fingerprint(jsonb_build_object(
 'league_id','9838a0a1-97c6-4cab-bb88-af177317abfe','source_season',2025,'target_season',2026,
 'classifications',(select jsonb_agg(jsonb_build_array(c.contract_agreement_id,c.player_id,c.classification,c.deterministic_fingerprint)
  order by c.contract_agreement_id) from public.contract_rollover_classifications c
  where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and c.source_season=2025 and c.target_season=2026))) fp),
evidence as(select fp,public.rollover_material_fingerprint(jsonb_build_object(
 'authority_key','abs:2025:2026:immediate-rollover-authority:v1','league_id','9838a0a1-97c6-4cab-bb88-af177317abfe',
 'source_season',2025,'target_season',2026,'reason','commissioner-directed one-time immediate rollover; three rookie options expire',
 'prior_rule','SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE','owner_case_count',0,
 'classification_population_fingerprint',fp)) evidence_fp from population)
insert into public.abs_immediate_rollover_authorities(league_id,source_season,target_season,authority_key,reason,prior_rule,
 required_confirmation,owner_case_count,classification_population_fingerprint,evidence_fingerprint)
select '9838a0a1-97c6-4cab-bb88-af177317abfe',2025,2026,'abs:2025:2026:immediate-rollover-authority:v1',
 'commissioner-directed one-time immediate rollover; three rookie options expire',
 'SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE',
 'AUTHORIZE ABS 2025->2026 IMMEDIATE ROLLOVER',0,fp,evidence_fp from evidence;

-- Preserve the dynamic Phase-B owner set while making its already-supported
-- zero-owner branch obey the same sequential lifecycle transitions as every
-- non-empty population. The canonical seven-day deadline is still recorded.
create or replace function public.open_rollover_notice_window(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare fp text;expected_count int;x public.rollover_executions%rowtype;n timestamptz;d timestamptz;
begin
 fp:=public.phaseb_assert_population_private((p_request->>'rollover_execution_id')::uuid,'owner',p_request->'owner_population');
 if p_request->>'expected_owner_population_fingerprint' is distinct from fp then raise exception 'phaseb_owner_population_fingerprint_mismatch';end if;
 select count(*) into expected_count from public.phaseb_owner_expected_cases_private((p_request->>'rollover_execution_id')::uuid);
 if expected_count>0 then return public.phaseb_open_rollover_notice_window_v1_private(p_request||jsonb_build_object('expected_owner_count',expected_count,'calculated_owner_population_fingerprint',fp));end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 n:=(p_request->>'official_notice_timestamp')::timestamptz;d:=n+interval '7 days';
 if x.id is null or x.status<>'preflight_ready' or x.preflight_fingerprint<>p_request->>'expected_preflight_fingerprint' then raise exception 'Execution state or preflight fingerprint mismatch';end if;
 update public.rollover_executions set status='notice_open',notice_timestamp=n,owner_deadline=d,
  decision_population_fingerprint=fp,metadata=metadata||jsonb_build_object('notice_idempotency_key',p_request->>'idempotency_key',
  'owner_expected_set_fingerprint',fp,'owner_expected_count',0) where id=x.id;
 update public.rollover_executions set status='decision_window_open' where id=x.id and status='notice_open' returning * into x;
 if x.id is null then raise exception 'Zero-owner notice-window state transition failed';end if;
 return jsonb_build_object('idempotent',false,'execution',to_jsonb(x),'owner_count',0,'deadline',d);
end$$;
revoke all on function public.open_rollover_notice_window(jsonb) from public,anon,authenticated;
grant execute on function public.open_rollover_notice_window(jsonb) to service_role;

-- Authority preparation previously treated an empty owner table as inherently
-- unresolved. Replace that obsolete fixed-cardinality assumption with exact
-- parity against the independently derived canonical owner set.
do $patch$
declare
 definition text;
 signature regprocedure :=
  'public.prepare_rollover_authorities_authenticated(jsonb)'::regprocedure;

 old_fragment text :=
  'if owner_count=0 or exists(select 1 from public.rollover_owner_decisions d where d.rollover_execution_id=x.id and d.decision_status not in (''planned_retention'',''planned_release'',''commissioner_review_requested'',''no_response'',''execution_ready'')) then raise exception ''unresolved owner decisions''; end if;';

 new_fragment text :=
  'if (
      owner_count=0
      and not (
       x.league_id=''9838a0a1-97c6-4cab-bb88-af177317abfe''::uuid
       and x.source_season=2025
       and x.target_season=2026
       and (select count(*) from public.phaseb_owner_expected_cases_private(x.id))=0
       and exists(
        select 1
        from public.abs_immediate_rollover_authorities a
        where a.league_id=x.league_id
         and a.source_season=x.source_season
         and a.target_season=x.target_season
         and a.rollover_execution_id=x.id
         and a.consumed_at is not null
       )
      )
     )
     or exists(
      select 1
      from public.rollover_owner_decisions d
      where d.rollover_execution_id=x.id
       and d.decision_status not in (
        ''planned_retention'',
        ''planned_release'',
        ''commissioner_review_requested'',
        ''no_response'',
        ''execution_ready''
       )
     )
    then
     raise exception ''unresolved owner decisions'';
    end if;';

begin
 select pg_get_functiondef(signature)
 into definition;

 if definition not like '%'||old_fragment||'%' then
  raise exception
   'Expected production authority-preparation owner guard not found';
 end if;

 definition := replace(
  definition,
  old_fragment,
  new_fragment
 );

 execute definition;
end
$patch$;

-- An empty canonical owner snapshot is evidence, not an absent snapshot.
alter table public.rollover_owner_option_snapshot_v2
 drop constraint if exists rollover_owner_option_snapshot_v2_case_count_check;
alter table public.rollover_owner_option_snapshot_v2
 add constraint rollover_owner_option_snapshot_v2_case_count_check check(case_count>=0);

-- The trusted dry-run persistence boundary must enforce the same dynamic
-- owner cardinality contract as preparation.
do $$declare definition text;signature regprocedure :=
 'public.persist_rollover_dry_run_service(jsonb)'::regprocedure;
 old_fragment text:='if owner_count=0 or exists(select 1 from public.rollover_owner_decisions d where d.rollover_execution_id=x.id and d.decision_status not in (''planned_retention'',''planned_release'',''commissioner_review_requested'',''no_response'',''execution_ready'')) then raise exception ''missing or unresolved owner outcome'';end if;';
 new_fragment text:='if owner_count<>(select count(*) from public.phaseb_owner_expected_cases_private(x.id)) or exists(select 1 from public.rollover_owner_decisions d where d.rollover_execution_id=x.id and d.decision_status not in (''planned_retention'',''planned_release'',''commissioner_review_requested'',''no_response'',''execution_ready'')) then raise exception ''missing or unresolved owner outcome'';end if;';
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%'||old_fragment||'%' then raise exception 'Expected dry-run owner cardinality guard not found';end if;
 execute replace(definition,old_fragment,new_fragment);
end$$;

create or replace function public.close_abs_2025_2026_immediate_rollover_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;
 authority public.abs_immediate_rollover_authorities%rowtype;canonical_rows jsonb;canonical_fp text;
 canonical_count integer;actual_count integer;current_classification_fp text;consumed_time timestamptz:=clock_timestamp();
begin
 if p_request ? 'requested_by' or p_request ? 'actor_user_id' then raise exception 'actor spoofing forbidden';end if;
 select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null or x.league_id<>'9838a0a1-97c6-4cab-bb88-af177317abfe'::uuid
  or x.source_season<>2025 or x.target_season<>2026 or x.status<>'decision_window_open' then
  raise exception 'ABs immediate rollover boundary or state is not eligible';end if;
 perform public.require_commissioner_authority(x.league_id);
 select * into authority from public.abs_immediate_rollover_authorities
  where authority_key='abs:2025:2026:immediate-rollover-authority:v1' for update;
 if authority.id is null or authority.consumed_at is not null then raise exception 'ABs immediate rollover authority absent or already consumed';end if;
 if p_request->>'confirmation' is distinct from authority.required_confirmation then raise exception 'Exact ABs immediate rollover confirmation required';end if;
 select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]'::jsonb),count(*)
  into canonical_rows,canonical_count from public.phaseb_owner_expected_cases_private(x.id);
 canonical_fp:=public.phaseb_population_fingerprint_private('owner',canonical_rows);
 select count(*) into actual_count from public.rollover_owner_decisions where rollover_execution_id=x.id;
 if canonical_count<>0 or actual_count<>0 then raise exception 'ABs immediate rollover requires zero canonical and actual owner cases';end if;
 if x.decision_population_fingerprint is distinct from canonical_fp
  or p_request->>'expected_population_fingerprint' is distinct from canonical_fp then raise exception 'ABs zero-owner population fingerprint mismatch';end if;
 select public.rollover_material_fingerprint(jsonb_build_object('league_id',x.league_id,'source_season',x.source_season,
  'target_season',x.target_season,'classifications',jsonb_agg(jsonb_build_array(c.contract_agreement_id,c.player_id,
  c.classification,c.deterministic_fingerprint) order by c.contract_agreement_id))) into current_classification_fp
 from public.contract_rollover_classifications c where c.league_id=x.league_id and c.source_season=x.source_season
  and c.target_season=x.target_season;
 if current_classification_fp is distinct from authority.classification_population_fingerprint then
  raise exception 'ABs classification population drift detected';end if;
 update public.abs_immediate_rollover_authorities set authorized_by=actor,authorized_at=consumed_time,
  rollover_execution_id=x.id,consumed_at=consumed_time where id=authority.id;
 update public.rollover_executions set status='decision_window_closed',metadata=metadata||jsonb_build_object(
  'abs_immediate_authority_id',authority.id,'abs_immediate_authority_fingerprint',authority.evidence_fingerprint,
  'abs_immediate_authorized_by',actor,'abs_immediate_consumed_at',consumed_time,
  'close_idempotency_key',p_request->>'idempotency_key','owner_expected_count',0)
  where id=x.id and status='decision_window_open' returning * into x;
 if x.id is null then raise exception 'ABs immediate rollover legal state transition failed';end if;
 return jsonb_build_object('idempotent',false,'execution',to_jsonb(x),'authority_id',authority.id,
  'evidence_fingerprint',authority.evidence_fingerprint,'owner_count',0);
end$$;

revoke all on function public.guard_abs_immediate_rollover_authority_private(),
 public.close_abs_2025_2026_immediate_rollover_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.close_abs_2025_2026_immediate_rollover_authenticated(jsonb) to authenticated;

commit;
