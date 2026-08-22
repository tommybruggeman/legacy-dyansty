BEGIN;

ALTER TABLE public.rollover_execution_input_snapshots
 DROP CONSTRAINT IF EXISTS rollover_execution_input_snapshot_snapshot_schema_version_check;
ALTER TABLE public.rollover_execution_input_snapshots
 ADD CONSTRAINT rollover_execution_input_snapshot_snapshot_schema_version_check
 CHECK(snapshot_schema_version IN('phase3b6c-snapshot-v1','phase3b6c-snapshot-v2','phase3b6c-snapshot-v3'));
ALTER TABLE public.rollover_execution_input_snapshots
 DROP CONSTRAINT IF EXISTS rollover_execution_input_snapshots_payload_bytes_check;
ALTER TABLE public.rollover_execution_input_snapshots
 ADD CONSTRAINT rollover_execution_input_snapshots_payload_bytes_check CHECK(
  (snapshot_schema_version IN('phase3b6c-snapshot-v1','phase3b6c-snapshot-v2') AND payload_bytes BETWEEN 1 AND 524288)
  OR (snapshot_schema_version='phase3b6c-snapshot-v3' AND payload_bytes BETWEEN 1 AND 67108864)
 );

ALTER TABLE public.rollover_execution_input_snapshot_components
 DROP CONSTRAINT IF EXISTS rollover_execution_input_snapsho_component_schema_version_check;
ALTER TABLE public.rollover_execution_input_snapshot_components
 ADD CONSTRAINT rollover_execution_input_snapsho_component_schema_version_check
 CHECK(component_schema_version~'^phase3b6c-[a-z_-]+-v(1|2|3)$');

CREATE TABLE public.rollover_execution_input_snapshot_component_chunks(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 snapshot_id uuid NOT NULL REFERENCES public.rollover_execution_input_snapshots(id) ON DELETE RESTRICT,
 component_name text NOT NULL,
 chunk_index integer NOT NULL CHECK(chunk_index>=0),
 first_canonical_key text NOT NULL CHECK(length(first_canonical_key)>0),
 last_canonical_key text NOT NULL CHECK(length(last_canonical_key)>0),
 record_count integer NOT NULL CHECK(record_count>0),
 canonical_payload jsonb NOT NULL CHECK(jsonb_typeof(canonical_payload)='array'),
 payload_bytes integer NOT NULL CHECK(payload_bytes BETWEEN 2 AND 73728),
 chunk_fingerprint text NOT NULL CHECK(chunk_fingerprint~'^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(snapshot_id,component_name,chunk_index),
 FOREIGN KEY(snapshot_id,component_name)
  REFERENCES public.rollover_execution_input_snapshot_components(snapshot_id,component_name)
  DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX rollover_snapshot_component_chunks_order_idx
 ON public.rollover_execution_input_snapshot_component_chunks(snapshot_id,component_name,chunk_index);

CREATE OR REPLACE FUNCTION public.reject_rollover_input_snapshot_chunk_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'execution input snapshot chunk evidence is immutable';
END;
$$;
CREATE TRIGGER rollover_execution_input_snapshot_component_chunks_immutable
 BEFORE UPDATE OR DELETE ON public.rollover_execution_input_snapshot_component_chunks
 FOR EACH ROW EXECUTE FUNCTION public.reject_rollover_input_snapshot_chunk_mutation();

ALTER TABLE public.rollover_execution_input_snapshot_component_chunks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.rollover_execution_input_snapshot_component_chunks FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.rollover_execution_input_snapshot_component_chunks TO authenticated;
GRANT SELECT,INSERT ON public.rollover_execution_input_snapshot_component_chunks TO service_role;
CREATE POLICY rollover_input_snapshot_component_chunks_commissioner_read
 ON public.rollover_execution_input_snapshot_component_chunks FOR SELECT TO authenticated
 USING(EXISTS(
  SELECT 1 FROM public.rollover_execution_input_snapshots AS input_snapshot
  JOIN public.league_memberships AS membership ON membership.league_id=input_snapshot.league_id
  WHERE input_snapshot.id=snapshot_id AND membership.user_id=auth.uid()
   AND membership.role='commissioner'
 ));

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_compact_json_private(p_value jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE result text;
BEGIN
 CASE jsonb_typeof(p_value)
  WHEN 'array' THEN
   SELECT '['||coalesce(string_agg(public.phase3b6c_snapshot_v3_compact_json_private(item.value),',' ORDER BY item.ordinality),'')||']'
   INTO result FROM jsonb_array_elements(p_value) WITH ORDINALITY AS item(value,ordinality);
  WHEN 'object' THEN
   SELECT '{'||coalesce(string_agg(to_jsonb(field.key)::text||':'||public.phase3b6c_snapshot_v3_compact_json_private(field.value),',' ORDER BY field.key COLLATE "C"),'')||'}'
   INTO result FROM jsonb_each(p_value) AS field(key,value);
  ELSE result:=p_value::text;
 END CASE;
 RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_sha256_private(p_value jsonb)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT encode(extensions.digest(convert_to(public.phase3b6c_snapshot_v3_compact_json_private(p_value),'UTF8'),'sha256'),'hex')
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_record_fingerprint_private(p_record jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF jsonb_typeof(p_record)<>'array' OR jsonb_array_length(p_record)<>2
  OR nullif(p_record->>0,'') IS NULL OR jsonb_typeof(p_record->1)<>'array'
 THEN RAISE EXCEPTION 'snapshot_v3_record_invalid'; END IF;
 RETURN public.phase3b6c_snapshot_v3_sha256_private(
  jsonb_build_array('phase3b6c-record-v3',p_record->>0,p_record->1)
 );
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_build_component_private(
 p_component_name text,p_records jsonb,p_inline_metadata jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE
 ordered_records jsonb;record_doc jsonb;candidate jsonb;current_chunk jsonb:='[]'::jsonb;
 chunks jsonb:='[]'::jsonb;chunk_doc jsonb;record_fingerprints jsonb;
 chunk_index integer:=0;payload_bytes integer;total_bytes integer:=0;
 record_count integer;first_key text;last_key text;record_set_fingerprint text;
 metadata_fingerprint text;component_fingerprint text;
BEGIN
 IF nullif(p_component_name,'') IS NULL OR jsonb_typeof(p_records)<>'array'
  OR jsonb_typeof(p_inline_metadata)<>'object'
 THEN RAISE EXCEPTION 'snapshot_v3_component_input_invalid'; END IF;
 SELECT coalesce(jsonb_agg(source_record.record_doc ORDER BY convert_to(source_record.record_doc->>0,'UTF8')),'[]'::jsonb)
 INTO ordered_records FROM jsonb_array_elements(p_records) AS source_record(record_doc);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(ordered_records) AS duplicate_record(record_doc)
  GROUP BY duplicate_record.record_doc->>0 HAVING count(*)>1)
 THEN RAISE EXCEPTION 'snapshot_v3_duplicate_canonical_key'; END IF;

 FOR record_doc IN SELECT ordered_record.record_doc FROM jsonb_array_elements(ordered_records) AS ordered_record(record_doc)
 LOOP
  PERFORM public.phase3b6c_snapshot_v3_record_fingerprint_private(record_doc);
  IF octet_length(public.phase3b6c_snapshot_v3_compact_json_private(jsonb_build_array(record_doc)))>73728
  THEN RAISE EXCEPTION 'snapshot_v3_single_record_oversize'; END IF;
  candidate:=current_chunk||jsonb_build_array(record_doc);
  IF jsonb_array_length(current_chunk)>0
   AND octet_length(public.phase3b6c_snapshot_v3_compact_json_private(candidate))>65536
  THEN
   SELECT jsonb_agg(jsonb_build_array(chunk_record.record_doc->>0,
    public.phase3b6c_snapshot_v3_record_fingerprint_private(chunk_record.record_doc))
    ORDER BY convert_to(chunk_record.record_doc->>0,'UTF8'))
   INTO record_fingerprints FROM jsonb_array_elements(current_chunk) AS chunk_record(record_doc);
   payload_bytes:=octet_length(public.phase3b6c_snapshot_v3_compact_json_private(current_chunk));
   first_key:=current_chunk->0->>0;last_key:=current_chunk->(jsonb_array_length(current_chunk)-1)->>0;
   chunk_doc:=jsonb_build_object('chunk_index',chunk_index,'first_canonical_key',first_key,
    'last_canonical_key',last_key,'record_count',jsonb_array_length(current_chunk),
    'canonical_payload',current_chunk,'payload_bytes',payload_bytes,
    'chunk_fingerprint',public.phase3b6c_snapshot_v3_sha256_private(jsonb_build_array(
     'phase3b6c-chunk-v3',p_component_name,chunk_index,first_key,last_key,
     jsonb_array_length(current_chunk),record_fingerprints)));
   chunks:=chunks||jsonb_build_array(chunk_doc);total_bytes:=total_bytes+payload_bytes;
   chunk_index:=chunk_index+1;current_chunk:=jsonb_build_array(record_doc);
  ELSE current_chunk:=candidate; END IF;
 END LOOP;
 IF jsonb_array_length(current_chunk)>0 THEN
  SELECT jsonb_agg(jsonb_build_array(chunk_record.record_doc->>0,
   public.phase3b6c_snapshot_v3_record_fingerprint_private(chunk_record.record_doc))
   ORDER BY convert_to(chunk_record.record_doc->>0,'UTF8'))
  INTO record_fingerprints FROM jsonb_array_elements(current_chunk) AS chunk_record(record_doc);
  payload_bytes:=octet_length(public.phase3b6c_snapshot_v3_compact_json_private(current_chunk));
  first_key:=current_chunk->0->>0;last_key:=current_chunk->(jsonb_array_length(current_chunk)-1)->>0;
  chunk_doc:=jsonb_build_object('chunk_index',chunk_index,'first_canonical_key',first_key,
   'last_canonical_key',last_key,'record_count',jsonb_array_length(current_chunk),
   'canonical_payload',current_chunk,'payload_bytes',payload_bytes,
   'chunk_fingerprint',public.phase3b6c_snapshot_v3_sha256_private(jsonb_build_array(
    'phase3b6c-chunk-v3',p_component_name,chunk_index,first_key,last_key,
    jsonb_array_length(current_chunk),record_fingerprints)));
  chunks:=chunks||jsonb_build_array(chunk_doc);total_bytes:=total_bytes+payload_bytes;
 END IF;
 IF jsonb_array_length(chunks)>1024 THEN RAISE EXCEPTION 'snapshot_v3_component_chunk_count_exceeded'; END IF;
 IF total_bytes>67108864 THEN RAISE EXCEPTION 'snapshot_v3_total_evidence_size_exceeded'; END IF;
 record_count:=jsonb_array_length(ordered_records);
 SELECT public.phase3b6c_snapshot_v3_sha256_private(jsonb_build_array(
  'phase3b6c-record-set-v3',coalesce(jsonb_agg(jsonb_build_array(set_record.record_doc->>0,
   public.phase3b6c_snapshot_v3_record_fingerprint_private(set_record.record_doc))
   ORDER BY convert_to(set_record.record_doc->>0,'UTF8')),'[]'::jsonb)))
 INTO record_set_fingerprint FROM jsonb_array_elements(ordered_records) AS set_record(record_doc);
 metadata_fingerprint:=public.phase3b6c_snapshot_v3_sha256_private(
  jsonb_build_array('phase3b6c-component-metadata-v3',p_inline_metadata));
 first_key:=CASE WHEN record_count=0 THEN NULL ELSE ordered_records->0->>0 END;
 last_key:=CASE WHEN record_count=0 THEN NULL ELSE ordered_records->(record_count-1)->>0 END;
 component_fingerprint:=public.phase3b6c_snapshot_v3_sha256_private(jsonb_build_array(
  'phase3b6c-component-v3',p_component_name,record_count,first_key,last_key,
  (SELECT coalesce(jsonb_agg(chunk_value->>'chunk_fingerprint' ORDER BY (chunk_value->>'chunk_index')::integer),'[]'::jsonb)
   FROM jsonb_array_elements(chunks) AS chunk_rows(chunk_value)),record_set_fingerprint,metadata_fingerprint));
 RETURN jsonb_build_object('manifest',jsonb_build_object(
  'storage','ordered_chunks','schema_version','phase3b6c-component-v3','component_name',p_component_name,
  'record_count',record_count,'chunk_count',jsonb_array_length(chunks),'first_canonical_key',first_key,
  'last_canonical_key',last_key,'ordered_chunk_fingerprints',(SELECT coalesce(jsonb_agg(chunk_value->>'chunk_fingerprint' ORDER BY (chunk_value->>'chunk_index')::integer),'[]'::jsonb) FROM jsonb_array_elements(chunks) AS chunk_rows(chunk_value)),
  'aggregate_record_set_fingerprint',record_set_fingerprint,'component_fingerprint',component_fingerprint,
  'total_payload_bytes',total_bytes,'inline_metadata',p_inline_metadata,'metadata_fingerprint',metadata_fingerprint),
  'chunks',chunks);
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_store_component_private(
 p_snapshot_id uuid,p_component_name text,p_records jsonb,p_inline_metadata jsonb,
 p_source_fingerprint text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE built jsonb;manifest jsonb;chunk_doc jsonb;component_fingerprint text;
BEGIN
 built:=public.phase3b6c_snapshot_v3_build_component_private(p_component_name,p_records,p_inline_metadata);
 manifest:=built->'manifest';component_fingerprint:=manifest->>'component_fingerprint';
 INSERT INTO public.rollover_execution_input_snapshot_components(
  snapshot_id,component_name,component_schema_version,canonical_payload,component_fingerprint,
  source_fingerprint,record_count,payload_bytes
 ) VALUES(p_snapshot_id,p_component_name,'phase3b6c-'||replace(p_component_name,'-','_')||'-v3',manifest,
  component_fingerprint,p_source_fingerprint,(manifest->>'record_count')::integer,
  octet_length(public.phase3b6c_snapshot_v3_compact_json_private(manifest)));
 FOR chunk_doc IN SELECT chunk_row.value FROM jsonb_array_elements(built->'chunks') AS chunk_row(value)
 LOOP
  INSERT INTO public.rollover_execution_input_snapshot_component_chunks(
   snapshot_id,component_name,chunk_index,first_canonical_key,last_canonical_key,
   record_count,canonical_payload,payload_bytes,chunk_fingerprint
  ) VALUES(p_snapshot_id,p_component_name,(chunk_doc->>'chunk_index')::integer,
   chunk_doc->>'first_canonical_key',chunk_doc->>'last_canonical_key',(chunk_doc->>'record_count')::integer,
   chunk_doc->'canonical_payload',(chunk_doc->>'payload_bytes')::integer,chunk_doc->>'chunk_fingerprint');
 END LOOP;
 RETURN component_fingerprint;
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_component_records_private(
 p_snapshot_id uuid,p_component_name text
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE component_row public.rollover_execution_input_snapshot_components%rowtype;
 records jsonb;rebuilt jsonb;manifest jsonb;
BEGIN
 SELECT * INTO component_row FROM public.rollover_execution_input_snapshot_components AS component
 WHERE component.snapshot_id=p_snapshot_id AND component.component_name=p_component_name;
 IF component_row.id IS NULL THEN RAISE EXCEPTION 'snapshot_v3_component_missing'; END IF;
 manifest:=component_row.canonical_payload;
 IF manifest->>'storage'<>'ordered_chunks' OR manifest->>'schema_version'<>'phase3b6c-component-v3'
 THEN RAISE EXCEPTION 'snapshot_v3_component_not_chunked'; END IF;
 SELECT coalesce(jsonb_agg(chunk_record.record_doc ORDER BY stored_chunk.chunk_index,chunk_record.ordinality),'[]'::jsonb)
 INTO records FROM public.rollover_execution_input_snapshot_component_chunks AS stored_chunk
 CROSS JOIN LATERAL jsonb_array_elements(stored_chunk.canonical_payload) WITH ORDINALITY AS chunk_record(record_doc,ordinality)
 WHERE stored_chunk.snapshot_id=p_snapshot_id AND stored_chunk.component_name=p_component_name;
 rebuilt:=public.phase3b6c_snapshot_v3_build_component_private(p_component_name,records,manifest->'inline_metadata');
 IF rebuilt->'manifest'<>manifest OR component_row.component_fingerprint<>manifest->>'component_fingerprint'
  OR EXISTS(
   (SELECT (stored_chunk.chunk_index,stored_chunk.first_canonical_key,stored_chunk.last_canonical_key,
      stored_chunk.record_count,stored_chunk.payload_bytes,stored_chunk.chunk_fingerprint,stored_chunk.canonical_payload)
    FROM public.rollover_execution_input_snapshot_component_chunks AS stored_chunk
    WHERE stored_chunk.snapshot_id=p_snapshot_id AND stored_chunk.component_name=p_component_name)
   EXCEPT
   (SELECT ((expected_chunk.value->>'chunk_index')::integer,expected_chunk.value->>'first_canonical_key',
      expected_chunk.value->>'last_canonical_key',(expected_chunk.value->>'record_count')::integer,
      (expected_chunk.value->>'payload_bytes')::integer,expected_chunk.value->>'chunk_fingerprint',expected_chunk.value->'canonical_payload')
    FROM jsonb_array_elements(rebuilt->'chunks') AS expected_chunk(value))
  ) OR EXISTS(
   (SELECT ((expected_chunk.value->>'chunk_index')::integer,expected_chunk.value->>'first_canonical_key',
      expected_chunk.value->>'last_canonical_key',(expected_chunk.value->>'record_count')::integer,
      (expected_chunk.value->>'payload_bytes')::integer,expected_chunk.value->>'chunk_fingerprint',expected_chunk.value->'canonical_payload')
    FROM jsonb_array_elements(rebuilt->'chunks') AS expected_chunk(value))
   EXCEPT
   (SELECT (stored_chunk.chunk_index,stored_chunk.first_canonical_key,stored_chunk.last_canonical_key,
      stored_chunk.record_count,stored_chunk.payload_bytes,stored_chunk.chunk_fingerprint,stored_chunk.canonical_payload)
    FROM public.rollover_execution_input_snapshot_component_chunks AS stored_chunk
    WHERE stored_chunk.snapshot_id=p_snapshot_id AND stored_chunk.component_name=p_component_name)
  )
 THEN RAISE EXCEPTION 'snapshot_v3_component_replay_mismatch'; END IF;
 RETURN records;
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_component_payload_private(
 p_snapshot_id uuid,p_component_name text
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE component_row public.rollover_execution_input_snapshot_components%rowtype;records jsonb;field_name text;
BEGIN
 SELECT * INTO component_row FROM public.rollover_execution_input_snapshot_components AS component
 WHERE component.snapshot_id=p_snapshot_id AND component.component_name=p_component_name;
 IF component_row.id IS NULL THEN RETURN NULL; END IF;
 IF component_row.canonical_payload->>'storage' IS DISTINCT FROM 'ordered_chunks' THEN
  RETURN component_row.canonical_payload;
 END IF;
 records:=public.phase3b6c_snapshot_v3_component_records_private(p_snapshot_id,p_component_name);
 IF p_component_name='team_mapping' THEN
  RETURN (component_row.canonical_payload->'inline_metadata')||jsonb_build_object(
   'teams',(SELECT coalesce(jsonb_agg(record_doc->1 ORDER BY record_doc->>0),'[]'::jsonb) FROM jsonb_array_elements(records) AS row_value(record_doc) WHERE record_doc->>0 LIKE 'team:%'),
   'memberships',(SELECT coalesce(jsonb_agg(record_doc->1 ORDER BY record_doc->>0),'[]'::jsonb) FROM jsonb_array_elements(records) AS row_value(record_doc) WHERE record_doc->>0 LIKE 'membership:%'),
   'target_mappings',(SELECT coalesce(jsonb_agg(record_doc->1 ORDER BY record_doc->>0),'[]'::jsonb) FROM jsonb_array_elements(records) AS row_value(record_doc) WHERE record_doc->>0 LIKE 'target_mapping:%'));
 END IF;
 field_name:=CASE p_component_name WHEN 'owner_option_decisions' THEN 'decisions'
  WHEN 'owner_option_revisions' THEN 'revisions' WHEN 'owner_option_reviews' THEN 'commissioner_reviews' END;
 IF field_name IS NOT NULL THEN
  RETURN (component_row.canonical_payload->'inline_metadata')||jsonb_build_object(field_name,
   (SELECT coalesce(jsonb_agg(record_doc->1 ORDER BY record_doc->>0),'[]'::jsonb) FROM jsonb_array_elements(records) AS row_value(record_doc)));
 END IF;
 RETURN jsonb_build_object('records',records)||(component_row.canonical_payload->'inline_metadata');
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_fingerprint_private(p_components jsonb)
RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.phase3b6c_snapshot_v3_sha256_private(jsonb_build_array(
  'phase3b6c-snapshot-fingerprint-v3',coalesce(jsonb_agg(jsonb_build_array(
   component.value->>'name',component.value->>'schema_version',component.value->>'component_fingerprint')
   ORDER BY convert_to(component.value->>'name','UTF8')),'[]'::jsonb)))
 FROM jsonb_array_elements(p_components) AS component(value)
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_inline_fingerprint_private(
 p_component_name text,p_schema_version text,p_payload jsonb
) RETURNS text LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.phase3b6c_snapshot_v3_sha256_private(jsonb_build_array(
  'phase3b6c-inline-component-v3',p_component_name,p_schema_version,p_payload))
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_freeze_snapshot_v3_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE
 execution_row public.rollover_executions%rowtype;plan_row public.rollover_execution_plans%rowtype;
 approval_row public.rollover_execution_plan_approvals%rowtype;source_season public.league_seasons%rowtype;
 target_season public.league_seasons%rowtype;capture_row public.historical_capture_executions%rowtype;
 existing_snapshot public.rollover_execution_input_snapshots%rowtype;snapshot_id uuid:=gen_random_uuid();
 mapping_fingerprint text;decision_fingerprint text;history_manifest jsonb;team_count integer;
 decision_count integer;capture_count integer;component_entries jsonb:='[]'::jsonb;
 inline_components jsonb:='[]'::jsonb;inline_component jsonb;component_payload jsonb;
 team_records jsonb;decision_records jsonb;revision_records jsonb;review_records jsonb;
 team_built jsonb;decision_built jsonb;revision_built jsonb;review_built jsonb;
 aggregate_fingerprint text;payload_bytes integer;stored_fingerprint text;
BEGIN
 IF p_actor IS NULL OR p_operation->>'operation_type'<>'FREEZE_FINAL_EXECUTION_INPUTS'
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('typed_handler_operation_invalid','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 SELECT * INTO execution_row FROM public.rollover_executions WHERE id=p_rollover_execution_id;
 SELECT * INTO plan_row FROM public.rollover_execution_plans WHERE id=p_execution_plan_id AND rollover_execution_id=p_rollover_execution_id;
 SELECT * INTO approval_row FROM public.rollover_execution_plan_approvals WHERE id=p_approval_id
  AND rollover_execution_id=p_rollover_execution_id AND execution_plan_id=p_execution_plan_id;
 IF execution_row.id IS NULL OR plan_row.id IS NULL OR approval_row.id IS NULL
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('approved_plan_material_missing','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 SELECT * INTO existing_snapshot FROM public.rollover_execution_input_snapshots WHERE rollover_execution_id=p_rollover_execution_id;
 IF existing_snapshot.id IS NOT NULL THEN
  IF existing_snapshot.execution_plan_id<>plan_row.id OR existing_snapshot.approval_id<>approval_row.id
   OR existing_snapshot.source_plan_fingerprint<>plan_row.plan_fingerprint
  THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('execution_input_snapshot_already_conflicts','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
  RETURN jsonb_build_object('operation_code','FREEZE_FINAL_EXECUTION_INPUTS','handler_version',1,
   'result_schema_version','phase3b6c-freeze-result-v1','read_only',true,'domain_mutations',0,
   'result',jsonb_build_object('snapshot_id',existing_snapshot.id,'snapshot_schema_version',existing_snapshot.snapshot_schema_version,
    'component_count',existing_snapshot.component_count,'aggregate_snapshot_hash',existing_snapshot.aggregate_snapshot_fingerprint,
    'source_plan_hash',existing_snapshot.source_plan_fingerprint,'mapping_fingerprint',existing_snapshot.mapping_fingerprint,
    'option_decision_fingerprint',existing_snapshot.option_decision_fingerprint,
    'historical_capture_identifier',existing_snapshot.historical_capture_execution_id,'frozen_team_count',existing_snapshot.frozen_team_count,
    'frozen_option_decision_count',existing_snapshot.frozen_option_decision_count,'durable_snapshot_rows_written',0,
    'validation_outcome','passed','validation_codes','[]'::jsonb,'football_domain_mutation_count',0));
 END IF;
 SELECT * INTO source_season FROM public.league_seasons WHERE league_id=execution_row.league_id AND season=execution_row.source_season FOR SHARE;
 SELECT * INTO target_season FROM public.league_seasons WHERE league_id=execution_row.league_id AND season=execution_row.target_season FOR SHARE;
 IF source_season.id IS NULL OR target_season.id IS NULL
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('approved_plan_material_missing','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 SELECT operation_doc.value->>'evidence_fingerprint' INTO mapping_fingerprint
 FROM jsonb_array_elements(plan_row.ordered_operations) AS operation_doc(value)
 WHERE operation_doc.value->>'operation_type'='VERIFY_TEAM_ROSTER_MAPPINGS';
 SELECT operation_doc.value->>'evidence_fingerprint' INTO decision_fingerprint
 FROM jsonb_array_elements(plan_row.ordered_operations) AS operation_doc(value)
 WHERE operation_doc.value->>'operation_type'='VERIFY_OPTION_WINDOW_CLOSED';
 IF mapping_fingerprint IS NULL OR decision_fingerprint IS NULL
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('snapshot_source_evidence_missing','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 SELECT count(*) INTO capture_count FROM public.historical_capture_executions AS capture
 WHERE capture.league_season_id=source_season.id AND capture.status IN('validated','finalized');
 IF capture_count<>1 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c(
  CASE WHEN capture_count=0 THEN 'historical_capture_reference_missing' ELSE 'duplicate_historical_capture' END,
  'FREEZE_FINAL_EXECUTION_INPUTS',jsonb_build_object('count',capture_count)); END IF;
 SELECT * INTO capture_row FROM public.historical_capture_executions AS capture
 WHERE capture.league_season_id=source_season.id AND capture.status IN('validated','finalized') FOR SHARE;
 PERFORM 1 FROM public.league_teams WHERE league_id=execution_row.league_id ORDER BY id FOR SHARE;
 PERFORM 1 FROM public.league_memberships WHERE league_id=execution_row.league_id ORDER BY id FOR SHARE;
 PERFORM 1 FROM public.season_team_mappings WHERE league_season_id IN(source_season.id,target_season.id) ORDER BY id FOR SHARE;
 PERFORM 1 FROM public.rollover_owner_decisions WHERE rollover_execution_id=execution_row.id ORDER BY id FOR SHARE;
 PERFORM 1 FROM public.rollover_owner_decision_revisions WHERE rollover_execution_id=execution_row.id ORDER BY id FOR SHARE;
 PERFORM 1 FROM public.rollover_commissioner_reviews WHERE rollover_execution_id=execution_row.id ORDER BY id FOR SHARE;
 PERFORM 1 FROM public.league_rules WHERE league_id=execution_row.league_id ORDER BY id FOR SHARE;
 IF (SELECT count(*) FROM public.league_rules WHERE league_id=execution_row.league_id)<>1
  OR (SELECT salary_cap FROM public.league_rules WHERE league_id=execution_row.league_id) IS NULL
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('league_rules_missing','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 SELECT count(*) INTO team_count FROM public.league_teams WHERE league_id=execution_row.league_id;
 SELECT count(*) INTO decision_count FROM public.rollover_owner_decisions WHERE rollover_execution_id=execution_row.id;
 IF team_count=0 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('snapshot_team_population_empty','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 history_manifest:=public.phase3b6c_history_manifest(source_season.id);

 SELECT coalesce(jsonb_agg(record_doc ORDER BY convert_to(record_doc->>0,'UTF8')),'[]'::jsonb) INTO team_records FROM(
  SELECT jsonb_build_array('team:'||team.id,to_jsonb(team)-'created_at'-'updated_at') record_doc FROM public.league_teams AS team WHERE team.league_id=execution_row.league_id
  UNION ALL SELECT jsonb_build_array('membership:'||membership.id,to_jsonb(membership)-'created_at'-'updated_at') FROM public.league_memberships AS membership WHERE membership.league_id=execution_row.league_id AND membership.league_team_id IS NOT NULL
  UNION ALL SELECT jsonb_build_array('target_mapping:'||mapping.id,to_jsonb(mapping)-'created_at'-'updated_at') FROM public.season_team_mappings AS mapping WHERE mapping.league_season_id=target_season.id
 ) AS team_material;
 SELECT coalesce(jsonb_agg(jsonb_build_array('decision:'||decision.id,to_jsonb(decision)) ORDER BY decision.id),'[]'::jsonb)
 INTO decision_records FROM public.rollover_owner_decisions AS decision WHERE decision.rollover_execution_id=execution_row.id;
 SELECT coalesce(jsonb_agg(jsonb_build_array('revision:'||revision.id,to_jsonb(revision)) ORDER BY revision.id),'[]'::jsonb)
 INTO revision_records FROM public.rollover_owner_decision_revisions AS revision WHERE revision.rollover_execution_id=execution_row.id;
 SELECT coalesce(jsonb_agg(jsonb_build_array('review:'||review.id,to_jsonb(review)) ORDER BY review.id),'[]'::jsonb)
 INTO review_records FROM public.rollover_commissioner_reviews AS review WHERE review.rollover_execution_id=execution_row.id;
 IF lower(public.phase3b6c_snapshot_v3_compact_json_private(team_records||decision_records||revision_records||review_records))
  ~'"(password|secret|token|credential)[^"]*"[[:space:]]*:'
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('snapshot_serialization_failed','FREEZE_FINAL_EXECUTION_INPUTS',jsonb_build_object('reason','credential_like_field')); END IF;

 team_built:=public.phase3b6c_snapshot_v3_build_component_private('team_mapping',team_records,
  jsonb_build_object('team_count',team_count,'mapping_fingerprint',mapping_fingerprint));
 decision_built:=public.phase3b6c_snapshot_v3_build_component_private('owner_option_decisions',decision_records,
  jsonb_build_object('notice_identifier',execution_row.id,'notice_timestamp',to_char(execution_row.notice_timestamp AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'owner_deadline',to_char(execution_row.owner_deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'decision_fingerprint',decision_fingerprint));
 revision_built:=public.phase3b6c_snapshot_v3_build_component_private('owner_option_revisions',revision_records,'{}');
 review_built:=public.phase3b6c_snapshot_v3_build_component_private('owner_option_reviews',review_records,'{}');

 inline_components:=jsonb_build_array(
  jsonb_build_object('name','execution_identity','version','phase3b6c-execution_identity-v3','count',1,'source',plan_row.plan_fingerprint,'payload',jsonb_build_object('execution_id',execution_row.id,'approval_id',approval_row.id,'plan_id',plan_row.id,'plan_version',plan_row.plan_version,'plan_hash',plan_row.plan_fingerprint,'league_id',execution_row.league_id)),
  jsonb_build_object('name','season_authority','version','phase3b6c-season_authority-v3','count',2,'payload',jsonb_build_object('closing',jsonb_build_object('id',source_season.id,'season',source_season.season,'sleeper_league_id',source_season.sleeper_league_id,'is_active',source_season.is_active),'target',jsonb_build_object('id',target_season.id,'season',target_season.season,'sleeper_league_id',target_season.sleeper_league_id,'is_active',target_season.is_active))),
  jsonb_build_object('name','league_rules','version','phase3b6c-league_rules-v3','count',1,'payload',(SELECT to_jsonb(rule)-'created_at'-'updated_at'-'transaction_go_live_at' FROM public.league_rules AS rule WHERE rule.league_id=execution_row.league_id)),
  jsonb_build_object('name','history_reference','version','phase3b6c-history_reference-v3','count',5,'source',capture_row.source_fingerprint,'payload',jsonb_build_object('capture_id',capture_row.id,'league_season_id',capture_row.league_season_id,'capture_type',capture_row.capture_type,'source_fingerprint',capture_row.source_fingerprint,'status',capture_row.status,'completed_at',to_char(capture_row.completed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'row_counts',capture_row.row_counts,'history_manifest',history_manifest)),
  jsonb_build_object('name','rollover_policy','version','phase3b6c-rollover_policy-v3','count',8,'payload',jsonb_build_object('third_round_option_denominator',225,'third_round_option_base',7,'third_round_guaranteed_salary',1,'rounding_policy','round_half_up','draft_horizon',4,'draft_rounds',3,'operation_catalog_version','phase3b5j1-v2','current_salary_cap',(SELECT salary_cap FROM public.league_rules WHERE league_id=execution_row.league_id))),
  jsonb_build_object('name','handler_catalog','version','phase3b6c-handler_catalog-v3','count',7,'payload',jsonb_build_object('handlers',(SELECT jsonb_agg(jsonb_build_object('operation_code',operation_code,'operation_order',operation_order,'handler_version',handler_version,'input_schema_version',input_schema_version,'result_schema_version',result_schema_version) ORDER BY operation_order) FROM public.rollover_execution_handler_registry WHERE operation_order<=7))),
  jsonb_build_object('name','execution_boundary','version','phase3b6c-execution_boundary-v3','count',1,'payload',jsonb_build_object('version','phase3b6c-executed-unpublished-v1','publication_permitted',false,'domain_mutation_phase_started',false)));
 FOR inline_component IN SELECT component_doc.value FROM jsonb_array_elements(inline_components) AS component_doc(value) ORDER BY component_doc.value->>'name'
 LOOP
  component_entries:=component_entries||jsonb_build_array(jsonb_build_object('name',inline_component->>'name','schema_version',inline_component->>'version',
   'component_fingerprint',public.phase3b6c_snapshot_v3_inline_fingerprint_private(inline_component->>'name',inline_component->>'version',inline_component->'payload')));
 END LOOP;
 component_entries:=component_entries||jsonb_build_array(
  jsonb_build_object('name','team_mapping','schema_version','phase3b6c-team_mapping-v3','component_fingerprint',team_built#>>'{manifest,component_fingerprint}'),
  jsonb_build_object('name','owner_option_decisions','schema_version','phase3b6c-owner_option_decisions-v3','component_fingerprint',decision_built#>>'{manifest,component_fingerprint}'),
  jsonb_build_object('name','owner_option_revisions','schema_version','phase3b6c-owner_option_revisions-v3','component_fingerprint',revision_built#>>'{manifest,component_fingerprint}'),
  jsonb_build_object('name','owner_option_reviews','schema_version','phase3b6c-owner_option_reviews-v3','component_fingerprint',review_built#>>'{manifest,component_fingerprint}'));
 aggregate_fingerprint:=public.phase3b6c_snapshot_v3_fingerprint_private(component_entries);
 payload_bytes:=coalesce((SELECT sum(octet_length(public.phase3b6c_snapshot_v3_compact_json_private(component_doc.value->'payload'))) FROM jsonb_array_elements(inline_components) AS component_doc(value)),0)
 +(team_built#>>'{manifest,total_payload_bytes}')::integer+(decision_built#>>'{manifest,total_payload_bytes}')::integer
  +(revision_built#>>'{manifest,total_payload_bytes}')::integer+(review_built#>>'{manifest,total_payload_bytes}')::integer
  +octet_length(public.phase3b6c_snapshot_v3_compact_json_private(team_built->'manifest'))
  +octet_length(public.phase3b6c_snapshot_v3_compact_json_private(decision_built->'manifest'))
  +octet_length(public.phase3b6c_snapshot_v3_compact_json_private(revision_built->'manifest'))
  +octet_length(public.phase3b6c_snapshot_v3_compact_json_private(review_built->'manifest'));
 IF payload_bytes>67108864 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('snapshot_size_exceeded','FREEZE_FINAL_EXECUTION_INPUTS',jsonb_build_object('payload_bytes',payload_bytes,'total_limit_bytes',67108864)); END IF;
 INSERT INTO public.rollover_execution_input_snapshots(id,rollover_execution_id,league_id,approval_id,execution_plan_id,
  execution_plan_version,source_plan_fingerprint,snapshot_schema_version,component_count,aggregate_snapshot_fingerprint,
  mapping_fingerprint,option_decision_fingerprint,historical_capture_execution_id,frozen_team_count,
  frozen_option_decision_count,payload_bytes,created_by,metadata)
 VALUES(snapshot_id,execution_row.id,execution_row.league_id,approval_row.id,plan_row.id,plan_row.plan_version,
  plan_row.plan_fingerprint,'phase3b6c-snapshot-v3',11,aggregate_fingerprint,mapping_fingerprint,decision_fingerprint,
  capture_row.id,team_count,decision_count,payload_bytes,p_actor,jsonb_build_object('phase','3B.6C','storage','hybrid_ordered_chunks'));
 FOR inline_component IN SELECT component_doc.value FROM jsonb_array_elements(inline_components) AS component_doc(value) ORDER BY component_doc.value->>'name'
 LOOP
  component_payload:=inline_component->'payload';
  INSERT INTO public.rollover_execution_input_snapshot_components(snapshot_id,component_name,component_schema_version,
   canonical_payload,component_fingerprint,source_fingerprint,record_count,payload_bytes)
  VALUES(snapshot_id,inline_component->>'name',inline_component->>'version',component_payload,
   public.phase3b6c_snapshot_v3_inline_fingerprint_private(inline_component->>'name',inline_component->>'version',component_payload),
   nullif(inline_component->>'source',''),(inline_component->>'count')::integer,
   octet_length(public.phase3b6c_snapshot_v3_compact_json_private(component_payload)));
 END LOOP;
 stored_fingerprint:=public.phase3b6c_snapshot_v3_store_component_private(snapshot_id,'team_mapping',team_records,team_built#>'{manifest,inline_metadata}',mapping_fingerprint);
 stored_fingerprint:=public.phase3b6c_snapshot_v3_store_component_private(snapshot_id,'owner_option_decisions',decision_records,decision_built#>'{manifest,inline_metadata}',decision_fingerprint);
 stored_fingerprint:=public.phase3b6c_snapshot_v3_store_component_private(snapshot_id,'owner_option_revisions',revision_records,'{}',decision_fingerprint);
 stored_fingerprint:=public.phase3b6c_snapshot_v3_store_component_private(snapshot_id,'owner_option_reviews',review_records,'{}',decision_fingerprint);
 IF history_manifest<>public.phase3b6c_history_manifest(source_season.id)
  OR team_count<>(SELECT count(*) FROM public.league_teams WHERE league_id=execution_row.league_id)
  OR decision_count<>(SELECT count(*) FROM public.rollover_owner_decisions WHERE rollover_execution_id=execution_row.id)
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('source_changed_during_freeze','FREEZE_FINAL_EXECUTION_INPUTS','{}'); END IF;
 RETURN jsonb_build_object('operation_code','FREEZE_FINAL_EXECUTION_INPUTS','handler_version',1,
  'input_schema_version','phase3b6c-freeze-input-v1','result_schema_version','phase3b6c-freeze-result-v1',
  'read_only',true,'domain_mutations',0,'authority_fingerprint',aggregate_fingerprint,'result',jsonb_build_object(
   'snapshot_id',snapshot_id,'snapshot_schema_version','phase3b6c-snapshot-v3','component_count',11,
   'aggregate_snapshot_hash',aggregate_fingerprint,'source_plan_hash',plan_row.plan_fingerprint,
   'mapping_fingerprint',mapping_fingerprint,'option_decision_fingerprint',decision_fingerprint,
   'historical_capture_identifier',capture_row.id,'frozen_team_count',team_count,
   'frozen_option_decision_count',decision_count,'validation_outcome','passed','validation_codes','[]'::jsonb,
   'evidence_count',team_count+decision_count+11,'durable_snapshot_rows_written',12+
    jsonb_array_length(team_built->'chunks')+jsonb_array_length(decision_built->'chunks')+
    jsonb_array_length(revision_built->'chunks')+jsonb_array_length(review_built->'chunks'),
   'football_domain_mutation_count',0));
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_snapshot_v3_assert_snapshot_private(p_snapshot_id uuid)
RETURNS void LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE snapshot_row public.rollover_execution_input_snapshots%rowtype;component_row record;
 component_entries jsonb:='[]'::jsonb;recomputed_fingerprint text;component_count integer:=0;
BEGIN
 SELECT * INTO snapshot_row FROM public.rollover_execution_input_snapshots WHERE id=p_snapshot_id;
 IF snapshot_row.id IS NULL THEN RAISE EXCEPTION 'snapshot_v3_snapshot_missing'; END IF;
 IF snapshot_row.snapshot_schema_version IN('phase3b6c-snapshot-v1','phase3b6c-snapshot-v2') THEN RETURN; END IF;
 IF snapshot_row.snapshot_schema_version<>'phase3b6c-snapshot-v3' THEN RAISE EXCEPTION 'snapshot_v3_schema_unsupported'; END IF;
 FOR component_row IN SELECT component.component_name,component.component_schema_version,
  component.canonical_payload,component.component_fingerprint
  FROM public.rollover_execution_input_snapshot_components AS component
  WHERE component.snapshot_id=p_snapshot_id ORDER BY component.component_name
 LOOP
  component_count:=component_count+1;
  IF component_row.canonical_payload->>'storage'='ordered_chunks' THEN
   PERFORM public.phase3b6c_snapshot_v3_component_records_private(p_snapshot_id,component_row.component_name);
   recomputed_fingerprint:=component_row.canonical_payload->>'component_fingerprint';
  ELSE
   recomputed_fingerprint:=public.phase3b6c_snapshot_v3_inline_fingerprint_private(
    component_row.component_name,component_row.component_schema_version,component_row.canonical_payload);
  END IF;
  IF recomputed_fingerprint IS DISTINCT FROM component_row.component_fingerprint
  THEN RAISE EXCEPTION 'snapshot_v3_component_fingerprint_mismatch:%',component_row.component_name; END IF;
  component_entries:=component_entries||jsonb_build_array(jsonb_build_object('name',component_row.component_name,
   'schema_version',component_row.component_schema_version,'component_fingerprint',recomputed_fingerprint));
 END LOOP;
 IF component_count<>snapshot_row.component_count OR component_count<>11
 THEN RAISE EXCEPTION 'snapshot_v3_component_count_mismatch'; END IF;
 recomputed_fingerprint:=public.phase3b6c_snapshot_v3_fingerprint_private(component_entries);
 IF recomputed_fingerprint IS DISTINCT FROM snapshot_row.aggregate_snapshot_fingerprint
 THEN RAISE EXCEPTION 'snapshot_v3_snapshot_fingerprint_mismatch'; END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b6c_verify_history_snapshot_compatible_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE execution_row public.rollover_executions%rowtype;plan_row public.rollover_execution_plans%rowtype;
 snapshot_row public.rollover_execution_input_snapshots%rowtype;source_season public.league_seasons%rowtype;
 capture_row public.historical_capture_executions%rowtype;history_payload jsonb;history_manifest jsonb;
 expected_counts jsonb;capture_count integer;component_name text;immutability_ok boolean;
BEGIN
 SELECT * INTO execution_row FROM public.rollover_executions WHERE id=p_rollover_execution_id;
 SELECT * INTO plan_row FROM public.rollover_execution_plans WHERE id=p_execution_plan_id AND rollover_execution_id=p_rollover_execution_id;
 SELECT * INTO snapshot_row FROM public.rollover_execution_input_snapshots WHERE rollover_execution_id=p_rollover_execution_id FOR SHARE;
 SELECT * INTO source_season FROM public.league_seasons WHERE league_id=execution_row.league_id AND season=plan_row.source_season FOR SHARE;
 IF snapshot_row.id IS NULL THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('historical_capture_missing','VERIFY_IMMUTABLE_HISTORY_CAPTURE',jsonb_build_object('reason','snapshot_missing')); END IF;
 PERFORM public.phase3b6c_snapshot_v3_assert_snapshot_private(snapshot_row.id);
 history_payload:=public.phase3b6c_snapshot_component_payload_private(snapshot_row.id,'history_reference');
 history_manifest:=history_payload->'history_manifest';
 SELECT count(*) INTO capture_count FROM public.historical_capture_executions AS capture
  JOIN public.league_seasons AS season_row ON season_row.id=capture.league_season_id
  WHERE season_row.league_id=execution_row.league_id AND season_row.season=plan_row.source_season
   AND capture.status IN('validated','finalized');
 IF capture_count<>1 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c(CASE WHEN capture_count=0 THEN 'historical_capture_missing' ELSE 'duplicate_historical_capture' END,'VERIFY_IMMUTABLE_HISTORY_CAPTURE',jsonb_build_object('count',capture_count)); END IF;
 SELECT * INTO capture_row FROM public.historical_capture_executions WHERE id=snapshot_row.historical_capture_execution_id FOR SHARE;
 IF capture_row.id IS NULL OR capture_row.league_season_id<>source_season.id OR capture_row.status NOT IN('validated','finalized')
  OR capture_row.completed_at IS NULL OR jsonb_array_length(capture_row.blocking_errors)>0
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('history_reference_mismatch','VERIFY_IMMUTABLE_HISTORY_CAPTURE','{}'); END IF;
 IF history_manifest IS NULL OR history_manifest<>public.phase3b6c_history_manifest(source_season.id)
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('historical_hash_mismatch','VERIFY_IMMUTABLE_HISTORY_CAPTURE','{}'); END IF;
 expected_counts:=capture_row.row_counts;
 FOREACH component_name IN ARRAY ARRAY['team_mappings','matchups','standings','brackets','roster_assignments']
 LOOP IF NOT expected_counts?component_name THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('historical_component_missing','VERIFY_IMMUTABLE_HISTORY_CAPTURE',jsonb_build_object('component',component_name)); END IF; END LOOP;
 IF (expected_counts->>'team_mappings')::integer<>(history_manifest#>>'{team_mappings,row_count}')::integer
  OR (expected_counts->>'matchups')::integer<>(history_manifest#>>'{matchups,row_count}')::integer
  OR (expected_counts->>'standings')::integer<>(history_manifest#>>'{standings,row_count}')::integer
  OR (expected_counts->>'brackets')::integer<>(history_manifest#>>'{playoff_brackets,row_count}')::integer
  OR (expected_counts->>'roster_assignments')::integer<>(history_manifest#>>'{roster_assignments,row_count}')::integer
 THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('historical_component_count_mismatch','VERIFY_IMMUTABLE_HISTORY_CAPTURE','{}'); END IF;
 SELECT bool_and(catalog.relrowsecurity) INTO immutability_ok FROM pg_class AS catalog WHERE catalog.oid IN(
  'public.season_team_mappings'::regclass,'public.season_matchups'::regclass,'public.season_standings'::regclass,
  'public.season_playoff_brackets'::regclass,'public.season_roster_assignments'::regclass,'public.historical_capture_executions'::regclass);
 IF NOT immutability_ok THEN PERFORM public.raise_rollover_preflight_failure_phase3b6c('historical_immutability_not_enforced','VERIFY_IMMUTABLE_HISTORY_CAPTURE','{}'); END IF;
 RETURN jsonb_build_object('operation_code','VERIFY_IMMUTABLE_HISTORY_CAPTURE','handler_version',1,
  'input_schema_version','phase3b6c-history-input-v1','result_schema_version','phase3b6c-history-result-v1',
  'read_only',true,'domain_mutations',0,'authority_fingerprint',public.rollover_material_fingerprint(history_manifest),
  'result',jsonb_build_object('historical_capture_execution_identifier',capture_row.id,'league_id',execution_row.league_id,
   'closing_season_identifier',source_season.id,'closing_season_year',source_season.season,
   'capture_completion_status',capture_row.status,'capture_completed_timestamp',capture_row.completed_at,
   'required_component_count',5,'present_component_count',5,'per_component_row_counts',capture_row.row_counts,
   'per_component_hashes',history_manifest,'aggregate_history_hash',public.rollover_material_fingerprint(history_manifest),
   'duplicate_capture_count',0,'immutable_protection_outcome','passed','snapshot_capture_reference_match',true,
   'validation_outcome','passed','validation_codes','[]'::jsonb,'football_domain_mutation_count',0));
END;
$$;

CREATE OR REPLACE FUNCTION public.execute_rollover_typed_handler_phase3b6c_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF p_operation->>'operation_type' IN('VERIFY_CLOSING_SEASON_AUTHORITY','VERIFY_TARGET_SEASON_AUTHORITY',
  'VERIFY_TARGET_SLEEPER_LINKAGE','VERIFY_TEAM_ROSTER_MAPPINGS','VERIFY_OPTION_WINDOW_CLOSED')
 THEN RETURN public.execute_rollover_typed_handler_phase3b6b_private(p_operation,p_rollover_execution_id,p_execution_plan_id); END IF;
 IF p_operation->>'operation_type'='FREEZE_FINAL_EXECUTION_INPUTS'
 THEN RETURN public.phase3b6c_freeze_snapshot_v3_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor); END IF;
 IF p_operation->>'operation_type'='VERIFY_IMMUTABLE_HISTORY_CAPTURE'
 THEN RETURN public.phase3b6c_verify_history_snapshot_compatible_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id); END IF;
 PERFORM public.raise_rollover_preflight_failure_phase3b6c('unsupported_operation',coalesce(p_operation->>'operation_type',''),'{}');
 RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.phase3b8a_is_preserved_off_roster_liability(
 p_snapshot_id uuid,p_agreement_id uuid,p_player_id text,p_league_team_id uuid
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH review_payload AS(
  SELECT coalesce(
   public.phase3b6c_snapshot_component_payload_private(p_snapshot_id,'owner_option_reviews'),
   public.phase3b6c_snapshot_component_payload_private(p_snapshot_id,'owner_options')
  ) AS payload
 )
 SELECT count(*)=1 FROM review_payload
 CROSS JOIN LATERAL jsonb_array_elements(review_payload.payload->'commissioner_reviews') AS review_row(review)
 WHERE review_row.review->>'agreement_id'=p_agreement_id::text
  AND review_row.review->>'player_id'=p_player_id
  AND review_row.review->>'league_team_id'=p_league_team_id::text
  AND review_row.review->>'review_type'='active_off_roster_liability'
  AND review_row.review->>'review_state'='approved'
  AND review_row.review->>'outcome'='preserve_active_liability'
  AND coalesce((review_row.review->>'evidence_complete')::boolean,false)
  AND coalesce((review_row.review->>'action_validated')::boolean,false)
  AND review_row.review->>'evidence_fingerprint'~'^[0-9a-f]{64}$'
  AND review_row.review->>'review_fingerprint'~'^[0-9a-f]{64}$'
$$;

create or replace function public.execute_rollover_typed_handler_phase3b6c1_private(
 p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 base_result jsonb;s public.rollover_execution_input_snapshots%rowtype;
 v2 public.rollover_owner_option_snapshot_v2%rowtype;c public.rollover_execution_input_snapshot_components%rowtype;c_rev public.rollover_execution_input_snapshot_components%rowtype;c_review public.rollover_execution_input_snapshot_components%rowtype;
 x public.rollover_executions%rowtype;src public.league_seasons%rowtype;tgt public.league_seasons%rowtype;
 d jsonb;live_d jsonb;r jsonb;rv jsonb;cases jsonb:='[]';reviews jsonb:='[]';case_payload jsonb;review_payload jsonb;
 agreement public.contract_agreements%rowtype;source_obligation public.contract_seasons%rowtype;
 option_obligation public.contract_seasons%rowtype;player public.player_universe%rowtype;
 latest_revision jsonb;review_row jsonb;authority public.league_membership_authority_events%rowtype;
 draft_round integer;is_third boolean;option_term integer;submitted_choice text;submitted_at timestamptz;
 submitted_by uuid;defaulted boolean;before_deadline boolean;taxi_status text;eligible boolean;
 case_fp text;review_fp text;case_set_fp text;review_set_fp text;aggregate_fp text;v2_id uuid:=gen_random_uuid();
 payload_size integer;review_count integer:=0;component_fp text;rules jsonb;owner_payload jsonb;
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
 -- Backwards-compatible reader:
 -- old snapshots use one owner_options component;
 -- snapshot-v2 uses three bounded immutable components.
 select * into c
 from public.rollover_execution_input_snapshot_components
 where snapshot_id=s.id and component_name='owner_options';

 if c.id is not null then
  if c.component_schema_version<>'phase3b6c-owner_options-v1' then
   perform public.raise_phase3b6c1_failure(
    'option_snapshot_schema_unsupported',
    jsonb_build_object(
     'component','owner_options',
     'schema',c.component_schema_version
    )
   );
  end if;

  owner_payload:=c.canonical_payload;
  component_fp:=c.component_fingerprint;

 else
  select * into c
  from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id
    and component_name='owner_option_decisions';

  select * into c_rev
  from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id
    and component_name='owner_option_revisions';

  select * into c_review
  from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id
    and component_name='owner_option_reviews';

  if c.id is null
     or c_rev.id is null
     or c_review.id is null
     or c.component_schema_version not in
        ('phase3b6c-owner_option_decisions-v2','phase3b6c-owner_option_decisions-v3')
     or c_rev.component_schema_version not in
        ('phase3b6c-owner_option_revisions-v2','phase3b6c-owner_option_revisions-v3')
     or c_review.component_schema_version not in
        ('phase3b6c-owner_option_reviews-v2','phase3b6c-owner_option_reviews-v3')
  then
   perform public.raise_phase3b6c1_failure(
    'option_snapshot_schema_unsupported',
    jsonb_build_object(
     'reason','split_owner_option_component_missing_or_invalid'
    )
   );
  end if;

  owner_payload :=
      public.phase3b6c_snapshot_component_payload_private(s.id,'owner_option_decisions')
      || public.phase3b6c_snapshot_component_payload_private(s.id,'owner_option_revisions')
      || public.phase3b6c_snapshot_component_payload_private(s.id,'owner_option_reviews');

  component_fp :=
   public.rollover_material_fingerprint(
    jsonb_build_object(
     'schema_version',
       'phase3b6c-owner-options-split-v2',
     'decisions_component_fingerprint',
       c.component_fingerprint,
     'revisions_component_fingerprint',
       c_rev.component_fingerprint,
     'reviews_component_fingerprint',
       c_review.component_fingerprint
    )
   );
 end if;
 select canonical_payload into rules from public.rollover_execution_input_snapshot_components
  where snapshot_id=s.id and component_name='rollover_policy';

 perform 1 from public.contract_agreements a join jsonb_array_elements(owner_payload->'decisions') q(value)
  on a.id=(q.value->>'agreement_id')::uuid order by a.id for share of a;
 perform 1 from public.contract_seasons cs join jsonb_array_elements(owner_payload->'decisions') q(value)
  on cs.contract_id=(q.value->>'agreement_id')::uuid order by cs.id for share of cs;
 perform 1 from public.player_universe u join jsonb_array_elements(owner_payload->'decisions') q(value)
  on u.sleeper_id=q.value->>'player_id' order by u.sleeper_id for share of u;
 perform 1 from public.league_membership_authority_events e where e.league_id=x.league_id order by e.effective_at,e.id for share;

 for d in select value from jsonb_array_elements(owner_payload->'decisions') q(value) order by (value->>'id')::uuid loop
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
  latest_revision:=(select value from jsonb_array_elements(owner_payload->'revisions') q(value)
   where value->>'owner_decision_id'=d->>'id' order by (value->>'revision_number')::integer desc,(value->>'id')::uuid desc limit 1);
  if exists(select 1 from jsonb_array_elements(owner_payload->'revisions') q(value)
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
   (select public.phase3b6c_snapshot_component_payload_private(s.id,'team_mapping')->'memberships')) q(value)) z
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
  review_row:=(select value from jsonb_array_elements(owner_payload->'commissioner_reviews') q(value)
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
   'is_default_nonresponse',defaulted,'notice_timestamp',owner_payload->>'notice_timestamp',
   'deadline_timestamp',owner_payload->>'owner_deadline',
   'response_reason_code',case when defaulted then 'no_response_default' else 'frozen_owner_response' end,
   'response_evidence',jsonb_build_object('decision_evidence',coalesce(live_d->'evidence','{}'::jsonb),'latest_revision',latest_revision),
   'revision_history',coalesce((select jsonb_agg(value order by (value->>'revision_number')::integer,(value->>'id')::uuid) from jsonb_array_elements(owner_payload->'revisions') q(value) where value->>'owner_decision_id'=d->>'id'),'[]'::jsonb),
   'duplicate_conflict_evidence',jsonb_build_object('duplicate_decision_count',1,'revision_conflict',false),
   'taxi_status',taxi_status,'taxi_source','frozen_initial_roster_slot','taxi_cutoff_timestamp',owner_payload->>'owner_deadline',
   'taxi_evidence_fingerprint',public.rollover_material_fingerprint(jsonb_build_object('decision_id',d->>'id','slot',taxi_status,'cutoff',owner_payload->>'owner_deadline')),
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


REVOKE ALL ON FUNCTION public.reject_rollover_input_snapshot_chunk_mutation(),
 public.phase3b6c_snapshot_v3_compact_json_private(jsonb),
 public.phase3b6c_snapshot_v3_sha256_private(jsonb),
 public.phase3b6c_snapshot_v3_record_fingerprint_private(jsonb),
 public.phase3b6c_snapshot_v3_build_component_private(text,jsonb,jsonb),
 public.phase3b6c_snapshot_v3_store_component_private(uuid,text,jsonb,jsonb,text),
 public.phase3b6c_snapshot_v3_component_records_private(uuid,text),
 public.phase3b6c_snapshot_component_payload_private(uuid,text),
 public.phase3b6c_snapshot_v3_fingerprint_private(jsonb),
 public.phase3b6c_snapshot_v3_inline_fingerprint_private(text,text,jsonb),
 public.phase3b6c_freeze_snapshot_v3_private(jsonb,uuid,uuid,uuid,uuid),
 public.phase3b6c_snapshot_v3_assert_snapshot_private(uuid),
 public.phase3b6c_verify_history_snapshot_compatible_private(jsonb,uuid,uuid,uuid),
 public.execute_rollover_typed_handler_phase3b6c_private(jsonb,uuid,uuid,uuid,uuid),
 public.phase3b8a_is_preserved_off_roster_liability(uuid,uuid,text,uuid)
 FROM PUBLIC,anon,authenticated,service_role;

COMMIT;
