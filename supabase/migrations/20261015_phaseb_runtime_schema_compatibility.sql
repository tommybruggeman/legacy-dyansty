BEGIN;

-- A review is frozen if any canonical approval surface says its execution plan
-- is approved.  Treating inconsistent approval evidence as approved is
-- deliberate and fail-closed: review evidence must not become mutable merely
-- because one approval surface is stale.
CREATE OR REPLACE FUNCTION public.phaseb_commissioner_review_plan_approved_private(
  p_rollover_execution_id uuid
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
 SELECT
  EXISTS(
   SELECT 1
   FROM public.rollover_execution_plans AS execution_plan
   WHERE execution_plan.rollover_execution_id=p_rollover_execution_id
    AND (
     execution_plan.plan_status='approved_for_execution'
     OR execution_plan.approved_for_execution=true
    )
  )
  OR EXISTS(
   SELECT 1
   FROM public.rollover_execution_plan_approvals AS plan_approval
   WHERE plan_approval.rollover_execution_id=p_rollover_execution_id
    AND plan_approval.approval_status='approved'
  );
$$;

CREATE OR REPLACE FUNCTION public.enforce_commissioner_review_state()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog,public
AS $$
DECLARE
 legal boolean:=false;
BEGIN
 IF tg_op='UPDATE' THEN
  IF old.review_state='executed' THEN
   RAISE EXCEPTION 'Executed commissioner review is immutable';
  END IF;
  IF public.phaseb_commissioner_review_plan_approved_private(old.rollover_execution_id)
   AND (new.review_state,new.outcome,new.evidence)
    IS DISTINCT FROM (old.review_state,old.outcome,old.evidence)
  THEN
   RAISE EXCEPTION 'Commissioner review cannot change after final plan approval';
  END IF;
  legal:=new.review_state=old.review_state
   OR (old.review_state='pending' AND new.review_state IN ('under_review','cancelled'))
   OR (old.review_state='under_review' AND new.review_state IN ('evidence_required','decision_ready','blocked','cancelled'))
   OR (old.review_state='evidence_required' AND new.review_state IN ('under_review','blocked','cancelled'))
   OR (old.review_state='decision_ready' AND new.review_state IN ('approved','rejected','blocked','cancelled'))
   OR (old.review_state IN ('approved','rejected') AND new.review_state IN ('superseded','executed'))
   OR (old.review_state='superseded' AND new.review_state IN ('under_review','cancelled'))
   OR (old.review_state='blocked' AND new.review_state IN ('under_review','cancelled'));
  IF NOT legal THEN
   RAISE EXCEPTION 'Illegal commissioner review transition: % -> %',old.review_state,new.review_state;
  END IF;
  IF new.revision_number<old.revision_number THEN
   RAISE EXCEPTION 'Commissioner review revision cannot decrease';
  END IF;
 END IF;
 IF new.review_state IN ('approved','rejected') AND new.outcome IS NULL THEN
  RAISE EXCEPTION 'Final review state requires an outcome';
 END IF;
 IF new.outcome IS NOT NULL
  AND NOT public.commissioner_review_outcome_allowed(new.review_type,new.outcome)
 THEN
  RAISE EXCEPTION 'Outcome is not allowed for review type';
 END IF;
 RETURN new;
END;
$$;

CREATE OR REPLACE FUNCTION public.supersede_rollover_commissioner_review_authenticated(p_request jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
 actor uuid;
 review_row public.rollover_commissioner_reviews%rowtype;
 idempotency_key_value text;
 material jsonb;
 request_fingerprint_value text;
 retry_result jsonb;
 result jsonb;
 next_revision integer;
BEGIN
 actor:=public.require_authenticated_user();
 idempotency_key_value:=nullif(btrim(p_request->>'idempotency_key'),'');
 IF idempotency_key_value IS NULL OR nullif(btrim(p_request->>'reason'),'') IS NULL THEN
  RAISE EXCEPTION 'Supersession reason and idempotency key required';
 END IF;
 SELECT * INTO review_row
 FROM public.rollover_commissioner_reviews AS current_review
 WHERE current_review.id=(p_request->>'review_id')::uuid
 FOR UPDATE;
 IF review_row.id IS NULL THEN RAISE EXCEPTION 'Review not found'; END IF;
 PERFORM public.require_commissioner_authority(review_row.league_id);
 material:=jsonb_build_object(
  'operation','supersede_commissioner_review',
  'execution_id',review_row.rollover_execution_id::text,
  'review_id',review_row.id::text,
  'actor',actor::text,
  'reason',p_request->>'reason',
  'expected_revision_number',(p_request->>'expected_revision_number')::integer,
  'expected_review_fingerprint',p_request->>'expected_review_fingerprint'
 );
 request_fingerprint_value:=public.rollover_material_fingerprint(material);
 retry_result:=public.rollover_operation_retry(
  review_row.league_id,'supersede_commissioner_review',idempotency_key_value,request_fingerprint_value
 );
 IF retry_result IS NOT NULL THEN RETURN retry_result; END IF;
 IF review_row.review_state NOT IN ('approved','rejected')
  OR review_row.revision_number<>(p_request->>'expected_revision_number')::integer
  OR review_row.review_fingerprint IS DISTINCT FROM p_request->>'expected_review_fingerprint'
 THEN
  RAISE EXCEPTION 'Only the current final review may be superseded';
 END IF;
 IF public.phaseb_commissioner_review_plan_approved_private(review_row.rollover_execution_id) THEN
  RAISE EXCEPTION 'Review cannot be superseded after final plan approval';
 END IF;
 next_revision:=review_row.revision_number+1;
 UPDATE public.rollover_commissioner_reviews
 SET review_state='superseded',review_status='decision_pending',outcome=NULL,approved_action=NULL,
  superseded_at=now(),superseded_by=actor,revision_number=next_revision,
  request_fingerprint=request_fingerprint_value,
  review_fingerprint=public.rollover_material_fingerprint(jsonb_build_object(
   'review',review_row.id,'state','superseded','revision',next_revision,
   'actor',actor,'reason',p_request->>'reason'
  )),updated_at=now()
 WHERE id=review_row.id
 RETURNING * INTO review_row;
 INSERT INTO public.rollover_commissioner_review_events(
  commissioner_review_id,rollover_execution_id,event_type,prior_status,new_status,
  performed_by,reason,evidence,idempotency_key,metadata
 ) VALUES(
  review_row.id,review_row.rollover_execution_id,'review_superseded','final','superseded',
  actor,p_request->>'reason',review_row.evidence,idempotency_key_value,
  jsonb_build_object('revision_number',next_revision,'request_fingerprint',request_fingerprint_value)
 );
 result:=jsonb_build_object('review',to_jsonb(review_row));
 RETURN public.record_rollover_operation(
  review_row.league_id,review_row.rollover_execution_id,'supersede_commissioner_review',
  idempotency_key_value,request_fingerprint_value,actor,'authenticated_commissioner',
  review_row.id,result,'{}'
 );
END;
$$;

REVOKE ALL ON FUNCTION public.phaseb_commissioner_review_plan_approved_private(uuid)
 FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.enforce_commissioner_review_state()
 FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.supersede_rollover_commissioner_review_authenticated(jsonb)
 FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.supersede_rollover_commissioner_review_authenticated(jsonb)
 TO authenticated;

COMMIT;
