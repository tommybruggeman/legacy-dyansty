-- Phase B forward correction: explicit compact positional UTF-8 fingerprint v3.
begin;

create or replace function public.phaseb_sha256_private(p_material text)
returns text language sql immutable strict security definer set search_path=pg_catalog,public as $$
 select encode(extensions.digest(convert_to(p_material,'UTF8'),'sha256'),'hex')
$$;
create or replace function public.phaseb_json_string_private(p_value text)
returns text language sql immutable security definer set search_path=pg_catalog,public as $$
 select coalesce(to_jsonb(p_value)::text,'null')
$$;

create or replace function public.phaseb_owner_expected_cases_private(p_execution_id uuid)
returns table(case_key text,case_fingerprint text,case_payload jsonb)
language sql security definer set search_path=pg_catalog,public stable as $$
 with cases as(
  select x.source_season,x.target_season,x.league_id,a.id agreement_id,a.player_id,a.league_team_id,
   a.status agreement_status,case when r.roster_designation in('taxi','ir') then r.roster_designation else 'rostered' end roster_designation,
   r.sleeper_player_id,case when cs.salary is null then null else to_char(cs.salary,'FM9999999990.00') end source_salary,
   greatest(a.end_season-x.source_season,0) source_contract_years
  from public.rollover_executions x join public.league_seasons s on s.league_id=x.league_id and s.season=x.source_season
  join public.contract_agreements a on a.league_id=x.league_id and a.status='expired'
  join public.league_teams t on t.id=a.league_team_id and t.league_id=x.league_id
  join public.season_roster_assignments r on r.league_season_id=s.id and r.league_team_id=a.league_team_id and r.sleeper_player_id=a.player_id
  left join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.source_season where x.id=p_execution_id)
 select format('%s:%s:%s:%s:%s',source_season,target_season,agreement_id,player_id,league_team_id),
  public.phaseb_sha256_private('["phaseb-owner-case-v3",'||
   public.phaseb_json_string_private('ROSTERED_EXPIRED_POLICY_UNDEFINED')||','||public.phaseb_json_string_private(league_id::text)||','||
   source_season||','||target_season||','||public.phaseb_json_string_private(agreement_id::text)||','||
   public.phaseb_json_string_private(player_id)||','||public.phaseb_json_string_private(league_team_id::text)||','||
   public.phaseb_json_string_private(agreement_status)||','||public.phaseb_json_string_private(roster_designation)||','||
   public.phaseb_json_string_private(sleeper_player_id)||','||public.phaseb_json_string_private(source_salary)||','||source_contract_years||']'),
  jsonb_build_object('classification','ROSTERED_EXPIRED_POLICY_UNDEFINED','league_id',league_id,'source_season',source_season,
   'target_season',target_season,'agreement_id',agreement_id,'player_id',player_id,'league_team_id',league_team_id,
   'agreement_status',agreement_status,'roster_designation',roster_designation,'sleeper_player_id',sleeper_player_id,
   'source_salary',source_salary,'source_contract_years',source_contract_years,'rostered_status','rostered','roster_slot',roster_designation)
 from cases order by agreement_id,player_id,league_team_id
$$;

create or replace function public.phaseb_commissioner_expected_cases_private(p_execution_id uuid)
returns table(case_key text,case_fingerprint text,case_payload jsonb)
language sql security definer set search_path=pg_catalog,public stable as $$
 with boundary as(select * from public.rollover_executions where id=p_execution_id),base as(
  select a.id agreement_id,a.player_id,a.league_team_id,case when a.status='active' then 'active_off_roster_liability' else 'expired_unrostered_publication_candidate' end review_type,
   null::uuid source_identity,a.status agreement_status,'unrostered'::text roster_status,case when cs.salary is null then null else to_char(cs.salary,'FM9999999990.00') end source_salary,greatest(a.end_season-x.source_season,0) source_contract_years
  from boundary x join public.contract_agreements a on a.league_id=x.league_id and a.status in('active','expired') join public.league_teams t on t.id=a.league_team_id and t.league_id=x.league_id
  join public.league_seasons s on s.league_id=x.league_id and s.season=x.source_season left join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.source_season
  where not exists(select 1 from public.season_roster_assignments r where r.league_season_id=s.id and r.sleeper_player_id=a.player_id)),escalations as(
  select d.agreement_id,d.player_id,d.league_team_id,'owner_escalation'::text,d.id,a.status,d.initial_roster_status,null::text,0 from boundary x
  join public.rollover_owner_decisions d on d.rollover_execution_id=x.id join public.contract_agreements a on a.id=d.agreement_id and a.league_id=x.league_id where d.decision_status='commissioner_review_requested'),conflicts as(
  select r.agreement_id,r.player_id,r.league_team_id,r.review_type,r.id,a.status,coalesce(r.evidence->>'roster_status','unknown'),r.evidence->>'source_salary',coalesce((r.evidence->>'source_contract_years')::int,0)
  from boundary x join public.rollover_commissioner_reviews r on r.rollover_execution_id=x.id left join public.contract_agreements a on a.id=r.agreement_id and a.league_id=x.league_id
  where r.review_type in('identity_conflict','contract_conflict','waiver_conflict','rookie_draft_conflict')),cases as(select * from base union all select * from escalations union all select * from conflicts)
 select format('%s:%s:%s:%s:%s',review_type,coalesce(agreement_id::text,'-'),player_id,coalesce(league_team_id::text,'-'),coalesce(source_identity::text,'base')),
  public.phaseb_sha256_private('["phaseb-commissioner-case-v3",'||public.phaseb_json_string_private(review_type)||','||public.phaseb_json_string_private(agreement_id::text)||','||
   public.phaseb_json_string_private(player_id)||','||public.phaseb_json_string_private(league_team_id::text)||','||public.phaseb_json_string_private(source_identity::text)||','||
   public.phaseb_json_string_private(agreement_status)||','||public.phaseb_json_string_private(roster_status)||','||public.phaseb_json_string_private(source_salary)||','||source_contract_years||']'),
  jsonb_build_object('review_type',review_type,'agreement_id',agreement_id,'player_id',player_id,'league_team_id',league_team_id,'source_identity',source_identity,
   'agreement_status',agreement_status,'roster_status',roster_status,'source_salary',source_salary,'source_contract_years',source_contract_years)
 from cases order by review_type,agreement_id,player_id,league_team_id,source_identity
$$;

create or replace function public.phaseb_population_fingerprint_private(p_kind text,p_rows jsonb)
returns text language sql immutable security definer set search_path=pg_catalog,public as $$
 select public.phaseb_sha256_private('["phaseb-'||p_kind||'-population-v3",['||coalesce(string_agg(
  '['||public.phaseb_json_string_private(x->>'key')||','||public.phaseb_json_string_private(x->>'fingerprint')||']',',' order by x->>'key'),'')||']]')
 from jsonb_array_elements(p_rows)x
$$;

create or replace function public.phaseb_assert_population_private(p_execution_id uuid,p_kind text,p_supplied jsonb)
returns text language plpgsql security definer set search_path=pg_catalog,public as $$
declare expected jsonb;actual jsonb;actual_count int;
begin
 if jsonb_typeof(p_supplied)<>'array' then raise exception 'phaseb_population_array_required:%',p_kind;end if;
 if p_kind='owner' then
  select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]') into expected from public.phaseb_owner_expected_cases_private(p_execution_id);
  select coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id'),'fingerprint',c->>'evidence_fingerprint') order by format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id')),'[]'),count(*) into actual,actual_count from jsonb_array_elements(p_supplied)c;
 else
  select coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) order by case_key),'[]') into expected from public.phaseb_commissioner_expected_cases_private(p_execution_id);
  select coalesce(jsonb_agg(jsonb_build_object('key',c->>'case_key','fingerprint',c->>'evidence_fingerprint') order by c->>'case_key'),'[]'),count(*) into actual,actual_count from jsonb_array_elements(p_supplied)c;
 end if;
 if actual_count<>(select count(distinct e->>'key') from jsonb_array_elements(actual)e) then raise exception 'phaseb_duplicate_%_case',p_kind;end if;
 if actual<>expected then raise exception 'phaseb_%_population_set_mismatch',p_kind;end if;
 return public.phaseb_population_fingerprint_private(p_kind,expected);
end$$;

revoke all on function public.phaseb_sha256_private(text),public.phaseb_json_string_private(text),public.phaseb_population_fingerprint_private(text,jsonb),
 public.phaseb_owner_expected_cases_private(uuid),public.phaseb_commissioner_expected_cases_private(uuid),public.phaseb_assert_population_private(uuid,text,jsonb)
 from public,anon,authenticated,service_role;
commit;
