-- Real snapshot freeze rows are JSON objects. Normalize them to ordered
-- positional field vectors before hashing while keeping array vectors unchanged.
begin;
set local search_path=pg_catalog,public;

create or replace function public.phase3b6c_snapshot_v3_record_fingerprint_private(p_record jsonb)
returns text language plpgsql immutable strict security definer set search_path=pg_catalog,public as $$
declare positional_value jsonb;
begin
 if jsonb_typeof(p_record)<>'array' or jsonb_array_length(p_record)<>2
  or nullif(p_record->>0,'') is null or jsonb_typeof(p_record->1) not in('array','object')
 then raise exception 'snapshot_v3_record_invalid'; end if;
 if jsonb_typeof(p_record->1)='object' then
  select coalesce(jsonb_agg(jsonb_build_array(field.key,field.value)
   order by field.key collate "C"),'[]'::jsonb)
  into positional_value from jsonb_each(p_record->1) as field(key,value);
 else positional_value:=p_record->1; end if;
 return public.phase3b6c_snapshot_v3_sha256_private(
  jsonb_build_array('phase3b6c-record-v3',p_record->>0,positional_value));
end;
$$;

revoke all on function public.phase3b6c_snapshot_v3_record_fingerprint_private(jsonb)
 from public,anon,authenticated,service_role;

commit;
