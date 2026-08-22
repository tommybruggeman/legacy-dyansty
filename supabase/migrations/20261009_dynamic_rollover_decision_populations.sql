-- Phase B: dynamic, independently derived rollover decision populations.
-- Forward-only function/metadata behavior; performs no migration-time fantasy-state DML.

begin;

alter table public.rollover_commissioner_reviews add column if not exists phaseb_case_key text;
do $$declare constraint_name text;begin
 select c.conname into constraint_name from pg_constraint c
 where c.conrelid='public.rollover_commissioner_reviews'::regclass and c.contype='u'::"char"
 and (select array_agg(a.attname::text order by u.ordinality) from unnest(c.conkey) with ordinality u(attnum,ordinality)
      join pg_attribute a on a.attrelid=c.conrelid and a.attnum=u.attnum)
     =array['rollover_execution_id','player_id','review_type']::text[];
 if constraint_name is not null then execute format('alter table public.rollover_commissioner_reviews drop constraint %I',constraint_name);end if;
end$$;
create unique index if not exists rollover_reviews_execution_phaseb_case_uidx
on public.rollover_commissioner_reviews(rollover_execution_id,phaseb_case_key) where phaseb_case_key is not null;

create or replace function public.phaseb_owner_expected_cases_private(p_execution_id uuid)
returns table(case_key text,case_fingerprint text,case_payload jsonb)
language sql security definer set search_path=pg_catalog,public stable as $$
  select format('%s:%s:%s:%s:%s',x.source_season,x.target_season,a.id,a.player_id,a.league_team_id),
         public.rollover_material_fingerprint(jsonb_build_object(
           'schema','rollover-owner-case-v2','classification','ROSTERED_EXPIRED_POLICY_UNDEFINED',
           'league_id',x.league_id,'source_season',x.source_season,'target_season',x.target_season,
           'agreement_id',a.id,'player_id',a.player_id,'league_team_id',a.league_team_id,
           'agreement_status',a.status,'roster_designation',case when r.roster_designation in('taxi','ir') then r.roster_designation else 'rostered' end,
           'sleeper_player_id',r.sleeper_player_id,'source_salary',case when cs.salary is null then null else to_char(cs.salary,'FM9999999990.00') end,
           'source_contract_years',greatest(a.end_season-x.source_season,0))),
         jsonb_build_object('league_id',x.league_id,'source_season',x.source_season,
           'target_season',x.target_season,'agreement_id',a.id,'player_id',a.player_id,
           'league_team_id',a.league_team_id,'rostered_status','rostered',
           'roster_slot',r.roster_designation,'classification','ROSTERED_EXPIRED_POLICY_UNDEFINED')
  from public.rollover_executions x
  join public.league_seasons s on s.league_id=x.league_id and s.season=x.source_season
  join public.contract_agreements a on a.league_id=x.league_id and a.status='expired'
  join public.league_teams t on t.id=a.league_team_id and t.league_id=x.league_id
  join public.season_roster_assignments r on r.league_season_id=s.id
    and r.league_team_id=a.league_team_id and r.sleeper_player_id=a.player_id
  left join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.source_season
  where x.id=p_execution_id
  order by a.id,a.player_id,a.league_team_id
$$;

create or replace function public.phaseb_commissioner_expected_cases_private(p_execution_id uuid)
returns table(case_key text,case_fingerprint text,case_payload jsonb)
language sql security definer set search_path=pg_catalog,public stable as $$
 with boundary as(select * from public.rollover_executions where id=p_execution_id), base as(
  select a.id agreement_id,a.player_id,a.league_team_id,
   case when a.status='active' then 'active_off_roster_liability' else 'expired_unrostered_publication_candidate' end review_type,
   null::uuid source_identity,a.status agreement_status,'unrostered'::text roster_status,
   case when cs.salary is null then null else to_char(cs.salary,'FM9999999990.00') end source_salary,greatest(a.end_season-x.source_season,0) source_contract_years
  from boundary x join public.contract_agreements a on a.league_id=x.league_id and a.status in('active','expired')
  join public.league_teams t on t.id=a.league_team_id and t.league_id=x.league_id
  join public.league_seasons s on s.league_id=x.league_id and s.season=x.source_season
  left join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.source_season
  where not exists(select 1 from public.season_roster_assignments r where r.league_season_id=s.id and r.sleeper_player_id=a.player_id)
 ), escalations as(
  select d.agreement_id,d.player_id,d.league_team_id,'owner_escalation'::text,d.id,
   a.status,d.initial_roster_status,null::text,0
  from boundary x join public.rollover_owner_decisions d on d.rollover_execution_id=x.id
  join public.contract_agreements a on a.id=d.agreement_id and a.league_id=x.league_id
  where d.decision_status='commissioner_review_requested'
 ), conflicts as(
  select r.agreement_id,r.player_id,r.league_team_id,r.review_type,r.id,
   a.status,coalesce(r.evidence->>'roster_status','unknown'),r.evidence->>'source_salary',coalesce((r.evidence->>'source_contract_years')::int,0)
  from boundary x join public.rollover_commissioner_reviews r on r.rollover_execution_id=x.id
  left join public.contract_agreements a on a.id=r.agreement_id and a.league_id=x.league_id
  where r.review_type in('identity_conflict','contract_conflict','waiver_conflict','rookie_draft_conflict')
 ), cases as(select * from base union all select * from escalations union all select * from conflicts)
 select format('%s:%s:%s:%s:%s',review_type,coalesce(agreement_id::text,'-'),player_id,
          coalesce(league_team_id::text,'-'),coalesce(source_identity::text,'base')),
        public.rollover_material_fingerprint(jsonb_build_object('schema','rollover-commissioner-case-v2',
          'review_type',review_type,'agreement_id',agreement_id,
          'player_id',player_id,'league_team_id',league_team_id,'source_identity',source_identity,
          'agreement_status',agreement_status,'roster_status',roster_status,'source_salary',source_salary,
          'source_contract_years',source_contract_years)),
        jsonb_build_object('review_type',review_type,'agreement_id',agreement_id,'player_id',player_id,
          'league_team_id',league_team_id,'source_identity',source_identity)
 from cases order by review_type,agreement_id,player_id,league_team_id,source_identity
$$;

create or replace function public.phaseb_assert_population_private(p_execution_id uuid,p_kind text,p_supplied jsonb)
returns text language plpgsql security definer set search_path=pg_catalog,public as $$
declare expected jsonb;actual jsonb;expected_count int;actual_count int;population_fingerprint text;
begin
 if jsonb_typeof(p_supplied)<>'array' then raise exception 'phaseb_population_array_required:%',p_kind;end if;
 if p_kind='owner' then
  select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]'),count(*)
   into expected,expected_count from public.phaseb_owner_expected_cases_private(p_execution_id);
  select coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id'),'fingerprint',c->>'evidence_fingerprint') order by format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id')),'[]'),count(*)
   into actual,actual_count from jsonb_array_elements(p_supplied)c;
 else
  select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]'),count(*)
   into expected,expected_count from public.phaseb_commissioner_expected_cases_private(p_execution_id);
  select coalesce(jsonb_agg(jsonb_build_object('key',c->>'case_key','fingerprint',c->>'evidence_fingerprint') order by c->>'case_key'),'[]'),count(*)
   into actual,actual_count from jsonb_array_elements(p_supplied)c;
 end if;
 if actual_count<>(select count(distinct e->>'key') from jsonb_array_elements(actual)e) then raise exception 'phaseb_duplicate_%_case',p_kind;end if;
 if actual<>expected then raise exception 'phaseb_%_population_set_mismatch',p_kind;end if;
 population_fingerprint:=public.rollover_material_fingerprint(jsonb_build_object('schema','rollover-'||p_kind||'-population-v2','cases',expected));
 return population_fingerprint;
end$$;

create or replace function public.phaseb_assert_frozen_populations_private(p_execution_id uuid)
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare expected jsonb;actual jsonb;
begin
 select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]') into expected
 from public.phaseb_owner_expected_cases_private(p_execution_id);
 select coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',d.source_season,d.target_season,d.agreement_id,d.player_id,d.league_team_id),'fingerprint',d.metadata->>'evidence_fingerprint') order by format('%s:%s:%s:%s:%s',d.source_season,d.target_season,d.agreement_id,d.player_id,d.league_team_id)),'[]') into actual
 from public.rollover_owner_decisions d where d.rollover_execution_id=p_execution_id;
 if actual<>expected then raise exception 'phaseb_frozen_owner_population_mismatch';end if;
 select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]') into expected
 from public.phaseb_commissioner_expected_cases_private(p_execution_id);
 select coalesce(jsonb_agg(jsonb_build_object('key',r.metadata->>'phaseb_case_key','fingerprint',r.metadata->>'phaseb_case_fingerprint') order by r.metadata->>'phaseb_case_key'),'[]') into actual
 from public.rollover_commissioner_reviews r where r.rollover_execution_id=p_execution_id;
 if actual<>expected then raise exception 'phaseb_frozen_commissioner_population_mismatch';end if;
end$$;

create or replace function public.phaseb_guard_authority_population_transition_private()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if old.status='decision_window_closed' and new.status='authority_initializing' then
  perform public.phaseb_assert_frozen_populations_private(old.id);
 end if;
 return new;
end$$;
drop trigger if exists phaseb_guard_authority_population_transition on public.rollover_executions;
create trigger phaseb_guard_authority_population_transition before update of status on public.rollover_executions
for each row execute function public.phaseb_guard_authority_population_transition_private();

-- Preserve the certified lifecycle implementation behind a stricter independent assertion.
alter function public.open_rollover_notice_window(jsonb) rename to phaseb_open_rollover_notice_window_v1_private;
create function public.open_rollover_notice_window(p_request jsonb) returns jsonb
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
 update public.rollover_executions set status='decision_window_open',notice_timestamp=n,owner_deadline=d,
  decision_population_fingerprint=fp,metadata=metadata||jsonb_build_object('notice_idempotency_key',p_request->>'idempotency_key',
  'owner_expected_set_fingerprint',fp,'owner_expected_count',0) where id=x.id returning * into x;
 return jsonb_build_object('idempotent',false,'execution',to_jsonb(x),'owner_count',0,'deadline',d);
end$$;

alter function public.initialize_rollover_commissioner_reviews_authenticated(jsonb) rename to phaseb_initialize_commissioner_reviews_v1_private;
create function public.initialize_rollover_commissioner_reviews_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid;x public.rollover_executions%rowtype;fp text;expected_count int;k text;material jsonb;request_fp text;retry jsonb;result jsonb;c jsonb;r public.rollover_commissioner_reviews%rowtype;actual int;
begin
 actor:=public.require_authenticated_user();select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid for update;
 if x.id is null then raise exception 'Execution not found';end if;perform public.require_commissioner_authority(x.league_id);
 if x.status<>'decision_window_closed' then raise exception 'Commissioner reviews require a closed owner decision window';end if;
 k:=nullif(btrim(p_request->>'idempotency_key'),'');if k is null then raise exception 'idempotency_key required';end if;
 fp:=public.phaseb_assert_population_private(x.id,'commissioner',p_request->'commissioner_population');
 if p_request->>'expected_commissioner_population_fingerprint' is distinct from fp then raise exception 'phaseb_commissioner_population_fingerprint_mismatch';end if;
 select count(*) into expected_count from public.phaseb_commissioner_expected_cases_private(x.id);
 material:=jsonb_build_object('operation','initialize_commissioner_reviews','execution_id',x.id,'population',p_request->'commissioner_population','expected_population_fingerprint',fp,'actor',actor);
 request_fp:=public.rollover_material_fingerprint(material);retry:=public.rollover_operation_retry(x.league_id,'initialize_commissioner_reviews',k,request_fp);if retry is not null then return retry;end if;
 for c in select value from jsonb_array_elements(p_request->'commissioner_population') loop
  insert into public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,league_team_id,review_type,review_status,review_state,execution_status,evidence,evidence_fingerprint,review_fingerprint,revision_number,phaseb_case_key,metadata)
  values(x.id,x.league_id,x.source_season,x.target_season,c->>'player_id',nullif(c->>'agreement_id','')::uuid,nullif(c->>'league_team_id','')::uuid,c->>'review_type','review_required','pending','pending',coalesce(c->'evidence','{}'),c->>'evidence_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('execution',x.id,'case_key',c->>'case_key','state','pending','evidence_fingerprint',c->>'evidence_fingerprint')),0,c->>'case_key',jsonb_build_object('population_fingerprint',fp,'phaseb_case_key',c->>'case_key','phaseb_case_fingerprint',c->>'evidence_fingerprint')) returning * into r;
  insert into public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,new_status,performed_by,reason,evidence,idempotency_key,metadata)
  values(r.id,x.id,'review_initialized','pending',actor,'commissioner population frozen',r.evidence,format('commissioner-initial:%s:%s',x.id,r.id),jsonb_build_object('review_fingerprint',r.review_fingerprint));
 end loop;
 select count(*) into actual from public.rollover_commissioner_reviews where rollover_execution_id=x.id;
 if actual<>expected_count then raise exception 'phaseb_frozen_commissioner_population_mismatch';end if;
 update public.rollover_executions set metadata=metadata||jsonb_build_object('commissioner_expected_set_fingerprint',fp,'commissioner_expected_count',expected_count) where id=x.id;
 perform public.phaseb_assert_frozen_populations_private(x.id);
 result:=jsonb_build_object('execution_id',x.id,'review_count',actual,'population_fingerprint',fp);
 return public.record_rollover_operation(x.league_id,x.id,'initialize_commissioner_reviews',k,request_fp,actor,'authenticated_commissioner',x.id,result,'{}');
end$$;

revoke all on function public.phaseb_owner_expected_cases_private(uuid),public.phaseb_commissioner_expected_cases_private(uuid),public.phaseb_assert_population_private(uuid,text,jsonb),public.phaseb_assert_frozen_populations_private(uuid),public.phaseb_guard_authority_population_transition_private(),public.phaseb_open_rollover_notice_window_v1_private(jsonb),public.phaseb_initialize_commissioner_reviews_v1_private(jsonb),public.open_rollover_notice_window(jsonb),public.initialize_rollover_commissioner_reviews_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.initialize_rollover_commissioner_reviews_authenticated(jsonb) to authenticated;
grant execute on function public.open_rollover_notice_window(jsonb) to service_role;

commit;
