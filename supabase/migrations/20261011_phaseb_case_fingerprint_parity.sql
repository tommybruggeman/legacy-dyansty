-- Phase B forward correction: centralize byte-identical v3 case material.
BEGIN;

CREATE OR REPLACE FUNCTION public.phaseb_owner_case_material_v3_private(p_case jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE salary text;
BEGIN
 IF p_case->>'classification' IS NULL OR p_case->>'league_id' IS NULL
  OR p_case->>'source_season' IS NULL OR p_case->>'target_season' IS NULL
  OR p_case->>'agreement_id' IS NULL OR p_case->>'player_id' IS NULL
  OR p_case->>'league_team_id' IS NULL OR p_case->>'agreement_status' IS NULL
  OR p_case->>'roster_designation' IS NULL OR p_case->>'sleeper_player_id' IS NULL
 THEN RAISE EXCEPTION 'phaseb_owner_case_required_field_missing'; END IF;
 salary:=CASE WHEN p_case->'source_salary' IS NULL OR p_case->'source_salary'='null'::jsonb
  THEN NULL ELSE to_char((p_case->>'source_salary')::numeric,'FM9999999999999999999999999990.00') END;
 RETURN '["phaseb-owner-case-v3",'||public.phaseb_json_string_private(p_case->>'classification')||','||
  public.phaseb_json_string_private((p_case->>'league_id')::uuid::text)||','||(p_case->>'source_season')::integer||','||
  (p_case->>'target_season')::integer||','||public.phaseb_json_string_private((p_case->>'agreement_id')::uuid::text)||','||
  public.phaseb_json_string_private(p_case->>'player_id')||','||public.phaseb_json_string_private((p_case->>'league_team_id')::uuid::text)||','||
  public.phaseb_json_string_private(p_case->>'agreement_status')||','||public.phaseb_json_string_private(p_case->>'roster_designation')||','||
  public.phaseb_json_string_private(p_case->>'sleeper_player_id')||','||public.phaseb_json_string_private(salary)||','||
  coalesce((p_case->>'source_contract_years')::integer,0)||']';
END;
$$;

CREATE OR REPLACE FUNCTION public.phaseb_commissioner_case_material_v3_private(p_case jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE salary text;
BEGIN
 IF p_case->>'review_type' IS NULL OR p_case->>'player_id' IS NULL OR p_case->>'roster_status' IS NULL
 THEN RAISE EXCEPTION 'phaseb_commissioner_case_required_field_missing'; END IF;
 salary:=CASE WHEN p_case->'source_salary' IS NULL OR p_case->'source_salary'='null'::jsonb
  THEN NULL ELSE to_char((p_case->>'source_salary')::numeric,'FM9999999999999999999999999990.00') END;
 RETURN '["phaseb-commissioner-case-v3",'||public.phaseb_json_string_private(p_case->>'review_type')||','||
  public.phaseb_json_string_private(CASE WHEN p_case->>'agreement_id' IS NULL THEN NULL ELSE (p_case->>'agreement_id')::uuid::text END)||','||
  public.phaseb_json_string_private(p_case->>'player_id')||','||
  public.phaseb_json_string_private(CASE WHEN p_case->>'league_team_id' IS NULL THEN NULL ELSE (p_case->>'league_team_id')::uuid::text END)||','||
  public.phaseb_json_string_private(CASE WHEN p_case->>'source_identity' IS NULL THEN NULL ELSE (p_case->>'source_identity')::uuid::text END)||','||
  public.phaseb_json_string_private(p_case->>'agreement_status')||','||public.phaseb_json_string_private(p_case->>'roster_status')||','||
  public.phaseb_json_string_private(salary)||','||coalesce((p_case->>'source_contract_years')::integer,0)||']';
END;
$$;

CREATE OR REPLACE FUNCTION public.phaseb_owner_case_fingerprint_v3_private(p_case jsonb)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.phaseb_sha256_private(public.phaseb_owner_case_material_v3_private(p_case))
$$;

CREATE OR REPLACE FUNCTION public.phaseb_commissioner_case_fingerprint_v3_private(p_case jsonb)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.phaseb_sha256_private(public.phaseb_commissioner_case_material_v3_private(p_case))
$$;

CREATE OR REPLACE FUNCTION public.phaseb_owner_expected_cases_private(p_execution_id uuid)
RETURNS TABLE(case_key text,case_fingerprint text,case_payload jsonb)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public STABLE AS $$
 WITH cases AS(
  SELECT x.source_season,x.target_season,x.league_id,a.id agreement_id,a.player_id,a.league_team_id,
   a.status agreement_status,CASE WHEN r.roster_designation IN('taxi','ir') THEN r.roster_designation ELSE 'rostered' END roster_designation,
   r.sleeper_player_id,CASE WHEN cs.salary IS NULL THEN NULL ELSE to_char(cs.salary,'FM9999999999999999999999999990.00') END source_salary,
   greatest(a.end_season-x.source_season,0) source_contract_years
  FROM public.rollover_executions x JOIN public.league_seasons s ON s.league_id=x.league_id AND s.season=x.source_season
  JOIN public.contract_agreements a ON a.league_id=x.league_id AND a.status='expired'
  JOIN public.league_teams t ON t.id=a.league_team_id AND t.league_id=x.league_id
  JOIN public.season_roster_assignments r ON r.league_season_id=s.id AND r.league_team_id=a.league_team_id AND r.sleeper_player_id=a.player_id
  LEFT JOIN public.contract_seasons cs ON cs.contract_id=a.id AND cs.season=x.source_season WHERE x.id=p_execution_id
 ), payloads AS(
  SELECT *,jsonb_build_object('classification','ROSTERED_EXPIRED_POLICY_UNDEFINED','league_id',league_id,'source_season',source_season,
   'target_season',target_season,'agreement_id',agreement_id,'player_id',player_id,'league_team_id',league_team_id,
   'agreement_status',agreement_status,'roster_designation',roster_designation,'sleeper_player_id',sleeper_player_id,
   'source_salary',source_salary,'source_contract_years',source_contract_years,'rostered_status','rostered','roster_slot',roster_designation) payload
  FROM cases)
 SELECT format('%s:%s:%s:%s:%s',source_season,target_season,agreement_id,player_id,league_team_id),
  public.phaseb_owner_case_fingerprint_v3_private(payload),payload FROM payloads
 ORDER BY agreement_id,player_id,league_team_id
$$;

CREATE OR REPLACE FUNCTION public.phaseb_commissioner_expected_cases_private(p_execution_id uuid)
RETURNS TABLE(case_key text,case_fingerprint text,case_payload jsonb)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public STABLE AS $$
 WITH boundary AS(SELECT * FROM public.rollover_executions WHERE id=p_execution_id),base AS(
  SELECT a.id agreement_id,a.player_id,a.league_team_id,CASE WHEN a.status='active' THEN 'active_off_roster_liability' ELSE 'expired_unrostered_publication_candidate' END review_type,
   NULL::uuid source_identity,a.status agreement_status,'unrostered'::text roster_status,CASE WHEN cs.salary IS NULL THEN NULL ELSE to_char(cs.salary,'FM9999999999999999999999999990.00') END source_salary,greatest(a.end_season-x.source_season,0) source_contract_years
  FROM boundary x JOIN public.contract_agreements a ON a.league_id=x.league_id AND a.status IN('active','expired') JOIN public.league_teams t ON t.id=a.league_team_id AND t.league_id=x.league_id
  JOIN public.league_seasons s ON s.league_id=x.league_id AND s.season=x.source_season LEFT JOIN public.contract_seasons cs ON cs.contract_id=a.id AND cs.season=x.source_season
  WHERE NOT EXISTS(SELECT 1 FROM public.season_roster_assignments r WHERE r.league_season_id=s.id AND r.sleeper_player_id=a.player_id)),escalations AS(
  SELECT d.agreement_id,d.player_id,d.league_team_id,'owner_escalation'::text,d.id,a.status,d.initial_roster_status,NULL::text,0 FROM boundary x
  JOIN public.rollover_owner_decisions d ON d.rollover_execution_id=x.id JOIN public.contract_agreements a ON a.id=d.agreement_id AND a.league_id=x.league_id WHERE d.decision_status='commissioner_review_requested'),conflicts AS(
  SELECT r.agreement_id,r.player_id,r.league_team_id,r.review_type,r.id,a.status,coalesce(r.evidence->>'roster_status','unknown'),r.evidence->>'source_salary',coalesce((r.evidence->>'source_contract_years')::int,0)
  FROM boundary x JOIN public.rollover_commissioner_reviews r ON r.rollover_execution_id=x.id LEFT JOIN public.contract_agreements a ON a.id=r.agreement_id AND a.league_id=x.league_id
  WHERE r.review_type IN('identity_conflict','contract_conflict','waiver_conflict','rookie_draft_conflict')),cases AS(SELECT * FROM base UNION ALL SELECT * FROM escalations UNION ALL SELECT * FROM conflicts),payloads AS(
  SELECT *,jsonb_build_object('review_type',review_type,'agreement_id',agreement_id,'player_id',player_id,'league_team_id',league_team_id,'source_identity',source_identity,
   'agreement_status',agreement_status,'roster_status',roster_status,'source_salary',source_salary,'source_contract_years',source_contract_years) payload FROM cases)
 SELECT format('%s:%s:%s:%s:%s',review_type,coalesce(agreement_id::text,'-'),player_id,coalesce(league_team_id::text,'-'),coalesce(source_identity::text,'base')),
  public.phaseb_commissioner_case_fingerprint_v3_private(payload),payload FROM payloads
 ORDER BY review_type,agreement_id,player_id,league_team_id,source_identity
$$;

REVOKE ALL ON FUNCTION public.phaseb_owner_case_material_v3_private(jsonb),public.phaseb_commissioner_case_material_v3_private(jsonb),
 public.phaseb_owner_case_fingerprint_v3_private(jsonb),public.phaseb_commissioner_case_fingerprint_v3_private(jsonb),
 public.phaseb_owner_expected_cases_private(uuid),public.phaseb_commissioner_expected_cases_private(uuid)
 FROM PUBLIC,anon,authenticated,service_role;

COMMIT;
