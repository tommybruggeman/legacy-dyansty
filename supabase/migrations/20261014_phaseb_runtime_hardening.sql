-- Phase B runtime hardening: deterministic, ambiguity-free population assertion.
BEGIN;

CREATE OR REPLACE FUNCTION public.phaseb_population_fingerprint_private(p_kind text,p_rows jsonb)
RETURNS text LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.phaseb_sha256_private('['||public.phaseb_json_string_private('phaseb-'||p_kind||'-population-v3')||',['||
  coalesce(string_agg('['||public.phaseb_json_string_private(population_case->>'key')||','||
   public.phaseb_json_string_private(population_case->>'fingerprint')||']',',' ORDER BY population_case->>'key'),'')||']]')
 FROM jsonb_array_elements(p_rows) AS population_rows(population_case)
$$;

CREATE OR REPLACE FUNCTION public.phaseb_assert_population_private(p_execution_id uuid,p_kind text,p_supplied jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
#variable_conflict error
DECLARE
 v_execution public.rollover_executions%ROWTYPE;
 v_supplied_case jsonb;
 v_expected_set jsonb;
 v_actual_set jsonb;
 v_actual_count bigint;
 v_expected_count bigint;
 v_distinct_expected_count bigint;
 v_calculated_case_key text;
 v_authoritative_fingerprint text;
BEGIN
 IF p_kind IS NULL OR p_kind NOT IN('owner','commissioner') THEN
  RAISE EXCEPTION 'phaseb_population_kind_invalid';
 END IF;
 IF p_supplied IS NULL OR jsonb_typeof(p_supplied) IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'phaseb_population_array_required:%',p_kind;
 END IF;

 SELECT execution_table.* INTO STRICT v_execution
 FROM public.rollover_executions AS execution_table
 WHERE execution_table.id=p_execution_id;

 IF p_kind='owner' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',owner_expected.case_key,'fingerprint',owner_expected.case_fingerprint)
   ORDER BY owner_expected.case_key),'[]'::jsonb),count(*)
  INTO v_expected_set,v_expected_count
  FROM public.phaseb_owner_expected_cases_private(v_execution.id) AS owner_expected;
 ELSE
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',review_expected.case_key,'fingerprint',review_expected.case_fingerprint)
   ORDER BY review_expected.case_key),'[]'::jsonb),count(*)
  INTO v_expected_set,v_expected_count
  FROM public.phaseb_commissioner_expected_cases_private(v_execution.id) AS review_expected;
 END IF;
 SELECT count(DISTINCT expected_case->>'key') INTO v_distinct_expected_count
 FROM jsonb_array_elements(v_expected_set) AS expected_rows(expected_case);
 IF v_expected_count<>v_distinct_expected_count THEN
  RAISE EXCEPTION 'phaseb_duplicate_expected_%_case',p_kind;
 END IF;

 FOR v_supplied_case IN
  SELECT supplied_rows.supplied_case
  FROM jsonb_array_elements(p_supplied) AS supplied_rows(supplied_case)
 LOOP
  IF v_supplied_case->>'league_id' IS DISTINCT FROM v_execution.league_id::text
   OR (v_supplied_case->>'source_season')::integer IS DISTINCT FROM v_execution.source_season
   OR (v_supplied_case->>'target_season')::integer IS DISTINCT FROM v_execution.target_season
  THEN RAISE EXCEPTION 'phaseb_%_execution_boundary_mismatch',p_kind; END IF;

  IF p_kind='owner' THEN
   v_calculated_case_key:=format('%s:%s:%s:%s:%s',v_supplied_case->>'source_season',
    v_supplied_case->>'target_season',v_supplied_case->>'agreement_id',
    v_supplied_case->>'player_id',v_supplied_case->>'league_team_id');
   IF NOT EXISTS(
    SELECT 1 FROM public.contract_agreements AS agreement_row
    JOIN public.league_teams AS team_row ON team_row.id=agreement_row.league_team_id
     AND team_row.league_id=v_execution.league_id
    JOIN public.league_seasons AS source_season_row ON source_season_row.league_id=v_execution.league_id
     AND source_season_row.season=v_execution.source_season
    JOIN public.season_roster_assignments AS roster_row ON roster_row.league_season_id=source_season_row.id
     AND roster_row.league_team_id=team_row.id AND roster_row.sleeper_player_id=agreement_row.player_id
    WHERE agreement_row.id=(v_supplied_case->>'agreement_id')::uuid
     AND agreement_row.league_id=v_execution.league_id
     AND agreement_row.league_team_id=(v_supplied_case->>'league_team_id')::uuid
     AND agreement_row.player_id=v_supplied_case->>'player_id'
   ) THEN RAISE EXCEPTION 'phaseb_owner_cross_league_or_identity_mismatch'; END IF;
  ELSE
   v_calculated_case_key:=v_supplied_case->>'case_key';
   IF v_calculated_case_key IS NULL THEN RAISE EXCEPTION 'phaseb_commissioner_case_key_required'; END IF;
   IF v_supplied_case->>'agreement_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.contract_agreements AS agreement_row
    JOIN public.league_teams AS team_row ON team_row.id=agreement_row.league_team_id
     AND team_row.league_id=v_execution.league_id
    WHERE agreement_row.id=(v_supplied_case->>'agreement_id')::uuid
     AND agreement_row.league_id=v_execution.league_id
     AND agreement_row.player_id=v_supplied_case->>'player_id'
     AND (v_supplied_case->>'league_team_id' IS NULL
      OR agreement_row.league_team_id=(v_supplied_case->>'league_team_id')::uuid)
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_cross_league_or_identity_mismatch'; END IF;
   IF v_supplied_case->>'league_team_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.league_teams AS team_row
    WHERE team_row.id=(v_supplied_case->>'league_team_id')::uuid AND team_row.league_id=v_execution.league_id
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_team_cross_league'; END IF;
   IF v_supplied_case->>'source_identity' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.rollover_owner_decisions AS owner_source
    WHERE owner_source.id=(v_supplied_case->>'source_identity')::uuid
     AND owner_source.rollover_execution_id=v_execution.id AND owner_source.league_id=v_execution.league_id
    UNION ALL
    SELECT 1 FROM public.rollover_commissioner_reviews AS review_source
    WHERE review_source.id=(v_supplied_case->>'source_identity')::uuid
     AND review_source.rollover_execution_id=v_execution.id AND review_source.league_id=v_execution.league_id
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_source_identity_cross_league'; END IF;
  END IF;

  SELECT expected_case->>'fingerprint' INTO STRICT v_authoritative_fingerprint
  FROM jsonb_array_elements(v_expected_set) AS expected_rows(expected_case)
  WHERE expected_case->>'key'=v_calculated_case_key;
  IF v_supplied_case->>'evidence_fingerprint' IS DISTINCT FROM v_authoritative_fingerprint THEN
   RAISE EXCEPTION 'phaseb_%_case_fingerprint_mismatch',p_kind;
  END IF;
 END LOOP;

 IF p_kind='owner' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',
   owner_case->>'source_season',owner_case->>'target_season',owner_case->>'agreement_id',
   owner_case->>'player_id',owner_case->>'league_team_id'),'fingerprint',owner_case->>'evidence_fingerprint')
   ORDER BY format('%s:%s:%s:%s:%s',owner_case->>'source_season',owner_case->>'target_season',
   owner_case->>'agreement_id',owner_case->>'player_id',owner_case->>'league_team_id')),'[]'::jsonb),count(*)
  INTO v_actual_set,v_actual_count
  FROM jsonb_array_elements(p_supplied) AS supplied_owner_rows(owner_case);
 ELSE
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',review_case->>'case_key','fingerprint',review_case->>'evidence_fingerprint')
   ORDER BY review_case->>'case_key'),'[]'::jsonb),count(*)
  INTO v_actual_set,v_actual_count
  FROM jsonb_array_elements(p_supplied) AS supplied_review_rows(review_case);
 END IF;
 IF v_actual_count<>(SELECT count(DISTINCT actual_case->>'key')
  FROM jsonb_array_elements(v_actual_set) AS actual_rows(actual_case)) THEN
  RAISE EXCEPTION 'phaseb_duplicate_%_case',p_kind;
 END IF;
 IF v_actual_set<>v_expected_set THEN RAISE EXCEPTION 'phaseb_%_population_set_mismatch',p_kind; END IF;
 RETURN public.phaseb_population_fingerprint_private(p_kind,v_expected_set);
EXCEPTION
 WHEN no_data_found THEN
  RAISE EXCEPTION 'phaseb_%_case_not_expected',p_kind;
 WHEN too_many_rows THEN
  RAISE EXCEPTION 'phaseb_duplicate_expected_%_case',p_kind;
END;
$$;

CREATE OR REPLACE FUNCTION public.phaseb_assert_frozen_populations_private(p_execution_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
#variable_conflict error
DECLARE v_expected_set jsonb;v_actual_set jsonb;
BEGIN
 SELECT coalesce(jsonb_agg(jsonb_build_object('key',owner_expected.case_key,'fingerprint',owner_expected.case_fingerprint)
  ORDER BY owner_expected.case_key),'[]'::jsonb) INTO v_expected_set
 FROM public.phaseb_owner_expected_cases_private(p_execution_id) AS owner_expected;
 SELECT coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',owner_row.source_season,
  owner_row.target_season,owner_row.agreement_id,owner_row.player_id,owner_row.league_team_id),
  'fingerprint',owner_row.metadata->>'evidence_fingerprint') ORDER BY format('%s:%s:%s:%s:%s',owner_row.source_season,
  owner_row.target_season,owner_row.agreement_id,owner_row.player_id,owner_row.league_team_id)),'[]'::jsonb) INTO v_actual_set
 FROM public.rollover_owner_decisions AS owner_row WHERE owner_row.rollover_execution_id=p_execution_id;
 IF v_actual_set<>v_expected_set THEN RAISE EXCEPTION 'phaseb_frozen_owner_population_mismatch'; END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('key',review_expected.case_key,'fingerprint',review_expected.case_fingerprint)
  ORDER BY review_expected.case_key),'[]'::jsonb) INTO v_expected_set
 FROM public.phaseb_commissioner_expected_cases_private(p_execution_id) AS review_expected;
 SELECT coalesce(jsonb_agg(jsonb_build_object('key',review_row.metadata->>'phaseb_case_key',
  'fingerprint',review_row.metadata->>'phaseb_case_fingerprint') ORDER BY review_row.metadata->>'phaseb_case_key'),'[]'::jsonb)
 INTO v_actual_set FROM public.rollover_commissioner_reviews AS review_row
 WHERE review_row.rollover_execution_id=p_execution_id;
 IF v_actual_set<>v_expected_set THEN RAISE EXCEPTION 'phaseb_frozen_commissioner_population_mismatch'; END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.initialize_rollover_commissioner_reviews_authenticated(p_request jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
#variable_conflict error
DECLARE
 v_actor uuid;
 v_execution public.rollover_executions%ROWTYPE;
 v_population_fingerprint text;
 v_expected_count bigint;
 v_idempotency_key text;
 v_request_material jsonb;
 v_request_fingerprint text;
 v_retry jsonb;
 v_result jsonb;
 v_supplied_case jsonb;
 v_review public.rollover_commissioner_reviews%ROWTYPE;
 v_actual_count bigint;
BEGIN
 v_actor:=public.require_authenticated_user();
 SELECT execution_table.* INTO v_execution FROM public.rollover_executions AS execution_table
 WHERE execution_table.id=(p_request->>'rollover_execution_id')::uuid FOR UPDATE;
 IF v_execution.id IS NULL THEN RAISE EXCEPTION 'Execution not found'; END IF;
 PERFORM public.require_commissioner_authority(v_execution.league_id);
 IF v_execution.status<>'decision_window_closed' THEN
  RAISE EXCEPTION 'Commissioner reviews require a closed owner decision window';
 END IF;
 v_idempotency_key:=nullif(btrim(p_request->>'idempotency_key'),'');
 IF v_idempotency_key IS NULL THEN RAISE EXCEPTION 'idempotency_key required'; END IF;
 v_population_fingerprint:=public.phaseb_assert_population_private(v_execution.id,'commissioner',p_request->'commissioner_population');
 IF p_request->>'expected_commissioner_population_fingerprint' IS DISTINCT FROM v_population_fingerprint THEN
  RAISE EXCEPTION 'phaseb_commissioner_population_fingerprint_mismatch';
 END IF;
 SELECT count(*) INTO v_expected_count
 FROM public.phaseb_commissioner_expected_cases_private(v_execution.id) AS expected_review;
 v_request_material:=jsonb_build_object('operation','initialize_commissioner_reviews','execution_id',v_execution.id,
  'population',p_request->'commissioner_population','expected_population_fingerprint',v_population_fingerprint,'actor',v_actor);
 v_request_fingerprint:=public.rollover_material_fingerprint(v_request_material);
 v_retry:=public.rollover_operation_retry(v_execution.league_id,'initialize_commissioner_reviews',v_idempotency_key,v_request_fingerprint);
 IF v_retry IS NOT NULL THEN RETURN v_retry; END IF;

 FOR v_supplied_case IN SELECT supplied_rows.supplied_case
  FROM jsonb_array_elements(p_request->'commissioner_population') AS supplied_rows(supplied_case)
 LOOP
 IF v_supplied_case->>'review_type' IN('identity_conflict','contract_conflict','waiver_conflict','rookie_draft_conflict')
   AND v_supplied_case->>'source_identity' IS NOT NULL THEN
   v_review.id:=NULL;
   UPDATE public.rollover_commissioner_reviews AS existing_conflict SET
    phaseb_case_key=v_supplied_case->>'case_key',evidence_fingerprint=v_supplied_case->>'evidence_fingerprint',
    metadata=existing_conflict.metadata||jsonb_build_object('population_fingerprint',v_population_fingerprint,
     'phaseb_case_key',v_supplied_case->>'case_key','phaseb_case_fingerprint',v_supplied_case->>'evidence_fingerprint')
   WHERE existing_conflict.id=(v_supplied_case->>'source_identity')::uuid
    AND existing_conflict.rollover_execution_id=v_execution.id AND existing_conflict.league_id=v_execution.league_id
   RETURNING existing_conflict.* INTO v_review;
   IF v_review.id IS NULL THEN RAISE EXCEPTION 'phaseb_commissioner_conflict_source_missing'; END IF;
  ELSE
   INSERT INTO public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,target_season,player_id,
    agreement_id,league_team_id,review_type,review_status,review_state,execution_status,evidence,evidence_fingerprint,
    review_fingerprint,revision_number,phaseb_case_key,metadata)
   VALUES(v_execution.id,v_execution.league_id,v_execution.source_season,v_execution.target_season,
    v_supplied_case->>'player_id',nullif(v_supplied_case->>'agreement_id','')::uuid,
    nullif(v_supplied_case->>'league_team_id','')::uuid,v_supplied_case->>'review_type','review_required','pending','pending',
    coalesce(v_supplied_case->'evidence','{}'::jsonb),v_supplied_case->>'evidence_fingerprint',
    public.rollover_material_fingerprint(jsonb_build_object('execution',v_execution.id,'case_key',v_supplied_case->>'case_key',
     'state','pending','evidence_fingerprint',v_supplied_case->>'evidence_fingerprint')),0,v_supplied_case->>'case_key',
    jsonb_build_object('population_fingerprint',v_population_fingerprint,'phaseb_case_key',v_supplied_case->>'case_key',
     'phaseb_case_fingerprint',v_supplied_case->>'evidence_fingerprint')) RETURNING * INTO v_review;
  END IF;
  INSERT INTO public.rollover_commissioner_review_events(commissioner_review_id,rollover_execution_id,event_type,new_status,
   performed_by,reason,evidence,idempotency_key,metadata)
  VALUES(v_review.id,v_execution.id,'review_initialized','pending',v_actor,'commissioner population frozen',v_review.evidence,
   format('commissioner-initial:%s:%s',v_execution.id,v_review.id),jsonb_build_object('review_fingerprint',v_review.review_fingerprint));
 END LOOP;
 SELECT count(*) INTO v_actual_count FROM public.rollover_commissioner_reviews AS review_row
 WHERE review_row.rollover_execution_id=v_execution.id;
 IF v_actual_count<>v_expected_count THEN RAISE EXCEPTION 'phaseb_frozen_commissioner_population_mismatch'; END IF;
 UPDATE public.rollover_executions AS execution_update SET metadata=execution_update.metadata||jsonb_build_object(
  'commissioner_expected_set_fingerprint',v_population_fingerprint,'commissioner_expected_count',v_expected_count)
 WHERE execution_update.id=v_execution.id;
 PERFORM public.phaseb_assert_frozen_populations_private(v_execution.id);
 v_result:=jsonb_build_object('execution_id',v_execution.id,'review_count',v_actual_count,
  'population_fingerprint',v_population_fingerprint);
 RETURN public.record_rollover_operation(v_execution.league_id,v_execution.id,'initialize_commissioner_reviews',
  v_idempotency_key,v_request_fingerprint,v_actor,'authenticated_commissioner',v_execution.id,v_result,'{}'::jsonb);
END;
$$;

REVOKE ALL ON FUNCTION public.phaseb_population_fingerprint_private(text,jsonb),
 public.phaseb_assert_population_private(uuid,text,jsonb),public.phaseb_assert_frozen_populations_private(uuid),
 public.initialize_rollover_commissioner_reviews_authenticated(jsonb)
 FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.initialize_rollover_commissioner_reviews_authenticated(jsonb) TO authenticated;

COMMIT;
