begin;

-- Forward-only repair for parent clones where the Phase A exact-set private
-- implementation exists but the public fingerprint-enforcing wrapper is stale.
do $$
begin
 if to_regprocedure('public.capture_pre_rollover_history_phasea_set_validated_private(jsonb)') is null
 then raise exception 'phasea_final_private_capture_missing'; end if;
 if to_regprocedure('public.phasea_history_sha256_private(text)') is null
  or to_regprocedure('public.phasea_history_canonical_team_fingerprint_private(uuid)') is null
  or to_regprocedure('public.phasea_history_source_roster_fingerprint_private(jsonb)') is null
  or to_regprocedure('public.phasea_history_mapping_fingerprint_private(jsonb)') is null
  or to_regprocedure('public.phasea_history_standings_fingerprint_private(jsonb)') is null
 then raise exception 'phasea_final_fingerprint_helpers_missing'; end if;
end $$;

create or replace function public.capture_pre_rollover_history(p_plan jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 v_season public.league_seasons%rowtype;
 v_name text;
 v_supplied text;
 v_expected text;
begin
 if jsonb_typeof(p_plan) is distinct from 'object' then raise exception 'phasea_history_payload_malformed';end if;
 select * into v_season from public.league_seasons where id=(p_plan->>'league_season_id')::uuid for share;
 if v_season.id is null then raise exception 'phasea_history_season_missing';end if;
 foreach v_name in array array['canonical_team_set_fingerprint','source_roster_set_fingerprint','mapping_set_fingerprint','standings_set_fingerprint'] loop
  v_supplied:=p_plan->>v_name;
  if v_supplied is null or v_supplied='' then raise exception 'phasea_history_fingerprint_missing:%',v_name;end if;
  if v_supplied!~'^[0-9a-f]{64}$' then raise exception 'phasea_history_fingerprint_malformed:%',v_name;end if;
  v_expected:=case v_name
   when 'canonical_team_set_fingerprint' then public.phasea_history_canonical_team_fingerprint_private(v_season.league_id)
   when 'source_roster_set_fingerprint' then public.phasea_history_source_roster_fingerprint_private(p_plan->'source_roster_identifiers')
   when 'mapping_set_fingerprint' then public.phasea_history_mapping_fingerprint_private(p_plan->'team_mappings')
   when 'standings_set_fingerprint' then public.phasea_history_standings_fingerprint_private(p_plan->'standings') end;
  if v_expected is null or v_supplied<>v_expected then raise exception 'phasea_history_fingerprint_mismatch:%',v_name;end if;
 end loop;
 return public.capture_pre_rollover_history_phasea_set_validated_private(p_plan);
end $$;

revoke all on function public.capture_pre_rollover_history(jsonb) from public,anon,authenticated;
grant execute on function public.capture_pre_rollover_history(jsonb) to service_role;
comment on function public.capture_pre_rollover_history(jsonb) is
'Phase A final fingerprint contract reassertion v1; service-role-only wrapper validates four compact positional-array UTF-8 SHA-256 fingerprints before exact-set capture.';

commit;
