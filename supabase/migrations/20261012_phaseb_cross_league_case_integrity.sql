-- Phase B forward correction: bind every supplied case to its execution league.
BEGIN;

CREATE OR REPLACE FUNCTION public.phaseb_commissioner_expected_cases_private(p_execution_id uuid)
RETURNS TABLE(case_key text,case_fingerprint text,case_payload jsonb)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public STABLE AS $$
 WITH boundary AS(SELECT * FROM public.rollover_executions WHERE id=p_execution_id),base AS(
  SELECT x.league_id,x.source_season,x.target_season,a.id agreement_id,a.player_id,a.league_team_id,
   CASE WHEN a.status='active' THEN 'active_off_roster_liability' ELSE 'expired_unrostered_publication_candidate' END review_type,
   NULL::uuid source_identity,a.status agreement_status,'unrostered'::text roster_status,
   CASE WHEN cs.salary IS NULL THEN NULL ELSE to_char(cs.salary,'FM9999999999999999999999999990.00') END source_salary,
   greatest(a.end_season-x.source_season,0) source_contract_years
  FROM boundary x JOIN public.contract_agreements a ON a.league_id=x.league_id AND a.status IN('active','expired')
  JOIN public.league_teams t ON t.id=a.league_team_id AND t.league_id=x.league_id
  JOIN public.league_seasons s ON s.league_id=x.league_id AND s.season=x.source_season
  LEFT JOIN public.contract_seasons cs ON cs.contract_id=a.id AND cs.season=x.source_season
  WHERE NOT EXISTS(SELECT 1 FROM public.season_roster_assignments r WHERE r.league_season_id=s.id AND r.sleeper_player_id=a.player_id)
 ),escalations AS(
  SELECT x.league_id,x.source_season,x.target_season,d.agreement_id,d.player_id,d.league_team_id,
   'owner_escalation'::text,d.id,a.status,d.initial_roster_status,NULL::text,0
  FROM boundary x JOIN public.rollover_owner_decisions d ON d.rollover_execution_id=x.id
  JOIN public.contract_agreements a ON a.id=d.agreement_id AND a.league_id=x.league_id
  WHERE d.decision_status='commissioner_review_requested'
 ),conflicts AS(
  SELECT x.league_id,x.source_season,x.target_season,r.agreement_id,r.player_id,r.league_team_id,r.review_type,r.id,
   a.status,coalesce(r.evidence->>'roster_status','unknown'),r.evidence->>'source_salary',
   coalesce((r.evidence->>'source_contract_years')::int,0)
  FROM boundary x JOIN public.rollover_commissioner_reviews r ON r.rollover_execution_id=x.id
  LEFT JOIN public.contract_agreements a ON a.id=r.agreement_id AND a.league_id=x.league_id
  WHERE r.review_type IN('identity_conflict','contract_conflict','waiver_conflict','rookie_draft_conflict')
 ),cases AS(SELECT * FROM base UNION ALL SELECT * FROM escalations UNION ALL SELECT * FROM conflicts),payloads AS(
  SELECT *,jsonb_build_object('league_id',league_id,'source_season',source_season,'target_season',target_season,
   'review_type',review_type,'agreement_id',agreement_id,'player_id',player_id,'league_team_id',league_team_id,
   'source_identity',source_identity,'agreement_status',agreement_status,'roster_status',roster_status,
   'source_salary',source_salary,'source_contract_years',source_contract_years) payload FROM cases)
 SELECT format('%s:%s:%s:%s:%s',review_type,coalesce(agreement_id::text,'-'),player_id,
  coalesce(league_team_id::text,'-'),coalesce(source_identity::text,'base')),
  public.phaseb_commissioner_case_fingerprint_v3_private(payload),payload FROM payloads
 ORDER BY review_type,agreement_id,player_id,league_team_id,source_identity
$$;

CREATE OR REPLACE FUNCTION public.phaseb_assert_population_private(p_execution_id uuid,p_kind text,p_supplied jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE
 x public.rollover_executions%ROWTYPE;
 c jsonb;
 expected jsonb;
 actual jsonb;
 actual_count integer;
 calculated_key text;
 calculated_fingerprint text;
BEGIN
 SELECT * INTO STRICT x FROM public.rollover_executions WHERE id=p_execution_id;
 IF p_kind NOT IN('owner','commissioner') THEN RAISE EXCEPTION 'phaseb_population_kind_invalid'; END IF;
 IF jsonb_typeof(p_supplied)<>'array' THEN RAISE EXCEPTION 'phaseb_population_array_required:%',p_kind; END IF;

 FOR c IN SELECT value FROM jsonb_array_elements(p_supplied) LOOP
  IF c->>'league_id' IS DISTINCT FROM x.league_id::text
   OR (c->>'source_season')::integer IS DISTINCT FROM x.source_season
   OR (c->>'target_season')::integer IS DISTINCT FROM x.target_season
  THEN RAISE EXCEPTION 'phaseb_%_execution_boundary_mismatch',p_kind; END IF;

  IF p_kind='owner' THEN
   calculated_key:=format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id');
   IF NOT EXISTS(
    SELECT 1 FROM public.contract_agreements a
    JOIN public.league_teams t ON t.id=a.league_team_id AND t.league_id=x.league_id
    JOIN public.league_seasons s ON s.league_id=x.league_id AND s.season=x.source_season
    JOIN public.season_roster_assignments r ON r.league_season_id=s.id AND r.league_team_id=t.id
     AND r.sleeper_player_id=a.player_id
    WHERE a.id=(c->>'agreement_id')::uuid AND a.league_id=x.league_id
     AND a.league_team_id=(c->>'league_team_id')::uuid AND a.player_id=c->>'player_id'
   ) THEN RAISE EXCEPTION 'phaseb_owner_cross_league_or_identity_mismatch'; END IF;
  ELSE
   calculated_key:=c->>'case_key';
   IF c->>'agreement_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.contract_agreements a
    JOIN public.league_teams t ON t.id=a.league_team_id AND t.league_id=x.league_id
    WHERE a.id=(c->>'agreement_id')::uuid AND a.league_id=x.league_id
     AND a.player_id=c->>'player_id'
     AND (c->>'league_team_id' IS NULL OR a.league_team_id=(c->>'league_team_id')::uuid)
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_cross_league_or_identity_mismatch'; END IF;
   IF c->>'league_team_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.league_teams t WHERE t.id=(c->>'league_team_id')::uuid AND t.league_id=x.league_id
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_team_cross_league'; END IF;
   IF c->>'source_identity' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.rollover_owner_decisions d WHERE d.id=(c->>'source_identity')::uuid
     AND d.rollover_execution_id=x.id AND d.league_id=x.league_id
    UNION ALL
    SELECT 1 FROM public.rollover_commissioner_reviews r WHERE r.id=(c->>'source_identity')::uuid
     AND r.rollover_execution_id=x.id AND r.league_id=x.league_id
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_source_identity_cross_league'; END IF;
  END IF;

  SELECT e->>'fingerprint' INTO calculated_fingerprint
  FROM jsonb_array_elements(CASE WHEN p_kind='owner' THEN
   (SELECT coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint)),'[]'::jsonb)
    FROM public.phaseb_owner_expected_cases_private(x.id)) ELSE
   (SELECT coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint)),'[]'::jsonb)
    FROM public.phaseb_commissioner_expected_cases_private(x.id)) END) e
  WHERE e->>'key'=calculated_key;
  IF calculated_fingerprint IS NULL OR c->>'evidence_fingerprint' IS DISTINCT FROM calculated_fingerprint
  THEN RAISE EXCEPTION 'phaseb_%_case_fingerprint_mismatch',p_kind; END IF;
 END LOOP;

 IF p_kind='owner' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) ORDER BY case_key),'[]'::jsonb)
   INTO expected FROM public.phaseb_owner_expected_cases_private(x.id);
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id'),'fingerprint',c->>'evidence_fingerprint')
   ORDER BY format('%s:%s:%s:%s:%s',c->>'source_season',c->>'target_season',c->>'agreement_id',c->>'player_id',c->>'league_team_id')),'[]'::jsonb),count(*)
   INTO actual,actual_count FROM jsonb_array_elements(p_supplied)c;
 ELSE
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',case_key,'fingerprint',case_fingerprint) ORDER BY case_key),'[]'::jsonb)
   INTO expected FROM public.phaseb_commissioner_expected_cases_private(x.id);
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',c->>'case_key','fingerprint',c->>'evidence_fingerprint') ORDER BY c->>'case_key'),'[]'::jsonb),count(*)
   INTO actual,actual_count FROM jsonb_array_elements(p_supplied)c;
 END IF;
 IF actual_count<>(SELECT count(DISTINCT e->>'key') FROM jsonb_array_elements(actual)e)
 THEN RAISE EXCEPTION 'phaseb_duplicate_%_case',p_kind; END IF;
 IF actual<>expected THEN RAISE EXCEPTION 'phaseb_%_population_set_mismatch',p_kind; END IF;
 RETURN public.phaseb_population_fingerprint_private(p_kind,expected);
END;
$$;

REVOKE ALL ON FUNCTION public.phaseb_assert_population_private(uuid,text,jsonb),
 public.phaseb_commissioner_expected_cases_private(uuid) FROM PUBLIC,anon,authenticated,service_role;

COMMIT;
