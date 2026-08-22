-- Phase B forward correction: remove PL/pgSQL variable/SQL alias collisions.
BEGIN;

CREATE OR REPLACE FUNCTION public.phaseb_assert_population_private(p_execution_id uuid,p_kind text,p_supplied jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
<<assert_population>>
DECLARE
 execution_row public.rollover_executions%ROWTYPE;
 supplied_case_doc jsonb;
 expected_set jsonb;
 actual_set jsonb;
 actual_count integer;
 calculated_case_key text;
 authoritative_case_fingerprint text;
BEGIN
 SELECT execution_table.* INTO STRICT execution_row
 FROM public.rollover_executions AS execution_table
 WHERE execution_table.id=assert_population.p_execution_id;

 IF assert_population.p_kind NOT IN('owner','commissioner') THEN
  RAISE EXCEPTION 'phaseb_population_kind_invalid';
 END IF;
 IF jsonb_typeof(assert_population.p_supplied)<>'array' THEN
  RAISE EXCEPTION 'phaseb_population_array_required:%',assert_population.p_kind;
 END IF;

 IF assert_population.p_kind='owner' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',expected_owner.case_key,'fingerprint',expected_owner.case_fingerprint)
   ORDER BY expected_owner.case_key),'[]'::jsonb)
  INTO expected_set
  FROM public.phaseb_owner_expected_cases_private(execution_row.id) AS expected_owner;
 ELSE
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',expected_review.case_key,'fingerprint',expected_review.case_fingerprint)
   ORDER BY expected_review.case_key),'[]'::jsonb)
  INTO expected_set
  FROM public.phaseb_commissioner_expected_cases_private(execution_row.id) AS expected_review;
 END IF;

 FOR supplied_case_doc IN
  SELECT supplied_rows.supplied_case_value
  FROM jsonb_array_elements(assert_population.p_supplied) AS supplied_rows(supplied_case_value)
 LOOP
  IF supplied_case_doc->>'league_id' IS DISTINCT FROM execution_row.league_id::text
   OR (supplied_case_doc->>'source_season')::integer IS DISTINCT FROM execution_row.source_season
   OR (supplied_case_doc->>'target_season')::integer IS DISTINCT FROM execution_row.target_season
  THEN RAISE EXCEPTION 'phaseb_%_execution_boundary_mismatch',assert_population.p_kind; END IF;

  IF assert_population.p_kind='owner' THEN
   calculated_case_key:=format('%s:%s:%s:%s:%s',supplied_case_doc->>'source_season',
    supplied_case_doc->>'target_season',supplied_case_doc->>'agreement_id',
    supplied_case_doc->>'player_id',supplied_case_doc->>'league_team_id');
   IF NOT EXISTS(
    SELECT 1 FROM public.contract_agreements AS agreement_row
    JOIN public.league_teams AS team_row ON team_row.id=agreement_row.league_team_id
     AND team_row.league_id=execution_row.league_id
    JOIN public.league_seasons AS source_season_row ON source_season_row.league_id=execution_row.league_id
     AND source_season_row.season=execution_row.source_season
    JOIN public.season_roster_assignments AS roster_row ON roster_row.league_season_id=source_season_row.id
     AND roster_row.league_team_id=team_row.id AND roster_row.sleeper_player_id=agreement_row.player_id
    WHERE agreement_row.id=(supplied_case_doc->>'agreement_id')::uuid
     AND agreement_row.league_id=execution_row.league_id
     AND agreement_row.league_team_id=(supplied_case_doc->>'league_team_id')::uuid
     AND agreement_row.player_id=supplied_case_doc->>'player_id'
   ) THEN RAISE EXCEPTION 'phaseb_owner_cross_league_or_identity_mismatch'; END IF;
  ELSE
   calculated_case_key:=supplied_case_doc->>'case_key';
   IF supplied_case_doc->>'agreement_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.contract_agreements AS agreement_row
    JOIN public.league_teams AS team_row ON team_row.id=agreement_row.league_team_id
     AND team_row.league_id=execution_row.league_id
    WHERE agreement_row.id=(supplied_case_doc->>'agreement_id')::uuid
     AND agreement_row.league_id=execution_row.league_id
     AND agreement_row.player_id=supplied_case_doc->>'player_id'
     AND (supplied_case_doc->>'league_team_id' IS NULL
      OR agreement_row.league_team_id=(supplied_case_doc->>'league_team_id')::uuid)
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_cross_league_or_identity_mismatch'; END IF;
   IF supplied_case_doc->>'league_team_id' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.league_teams AS team_row
    WHERE team_row.id=(supplied_case_doc->>'league_team_id')::uuid
     AND team_row.league_id=execution_row.league_id
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_team_cross_league'; END IF;
   IF supplied_case_doc->>'source_identity' IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM public.rollover_owner_decisions AS owner_source
    WHERE owner_source.id=(supplied_case_doc->>'source_identity')::uuid
     AND owner_source.rollover_execution_id=execution_row.id
     AND owner_source.league_id=execution_row.league_id
    UNION ALL
    SELECT 1 FROM public.rollover_commissioner_reviews AS review_source
    WHERE review_source.id=(supplied_case_doc->>'source_identity')::uuid
     AND review_source.rollover_execution_id=execution_row.id
     AND review_source.league_id=execution_row.league_id
   ) THEN RAISE EXCEPTION 'phaseb_commissioner_source_identity_cross_league'; END IF;
  END IF;

  SELECT expected_case_doc->>'fingerprint' INTO authoritative_case_fingerprint
  FROM jsonb_array_elements(expected_set) AS expected_rows(expected_case_doc)
  WHERE expected_case_doc->>'key'=calculated_case_key;
  IF authoritative_case_fingerprint IS NULL
   OR supplied_case_doc->>'evidence_fingerprint' IS DISTINCT FROM authoritative_case_fingerprint
  THEN RAISE EXCEPTION 'phaseb_%_case_fingerprint_mismatch',assert_population.p_kind; END IF;
 END LOOP;

 IF assert_population.p_kind='owner' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',format('%s:%s:%s:%s:%s',
   supplied_case_value->>'source_season',supplied_case_value->>'target_season',
   supplied_case_value->>'agreement_id',supplied_case_value->>'player_id',supplied_case_value->>'league_team_id'),
   'fingerprint',supplied_case_value->>'evidence_fingerprint') ORDER BY format('%s:%s:%s:%s:%s',
   supplied_case_value->>'source_season',supplied_case_value->>'target_season',
   supplied_case_value->>'agreement_id',supplied_case_value->>'player_id',supplied_case_value->>'league_team_id')),'[]'::jsonb),count(*)
  INTO actual_set,actual_count
  FROM jsonb_array_elements(assert_population.p_supplied) AS supplied_owner_rows(supplied_case_value);
 ELSE
  SELECT coalesce(jsonb_agg(jsonb_build_object('key',supplied_case_value->>'case_key',
   'fingerprint',supplied_case_value->>'evidence_fingerprint') ORDER BY supplied_case_value->>'case_key'),'[]'::jsonb),count(*)
  INTO actual_set,actual_count
  FROM jsonb_array_elements(assert_population.p_supplied) AS supplied_review_rows(supplied_case_value);
 END IF;

 IF actual_count<>(SELECT count(DISTINCT actual_case_doc->>'key')
  FROM jsonb_array_elements(actual_set) AS actual_rows(actual_case_doc))
 THEN RAISE EXCEPTION 'phaseb_duplicate_%_case',assert_population.p_kind; END IF;
 IF actual_set<>expected_set THEN RAISE EXCEPTION 'phaseb_%_population_set_mismatch',assert_population.p_kind; END IF;
 RETURN public.phaseb_population_fingerprint_private(assert_population.p_kind,expected_set);
END assert_population;
$$;

REVOKE ALL ON FUNCTION public.phaseb_assert_population_private(uuid,text,jsonb)
 FROM PUBLIC,anon,authenticated,service_role;

COMMIT;
