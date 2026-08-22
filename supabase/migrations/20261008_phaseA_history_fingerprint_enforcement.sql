begin;

-- Phase A cross-language encoding, version phaseA-history-identity-json-v1:
-- * compact JSON arrays of positional arrays (never JSON objects)
-- * UTF-8 bytes, no insignificant whitespace
-- * deterministic identity ordering specified per set below
-- * SHA-256, lowercase 64-character hexadecimal
-- PostgreSQL constructs the compact JSON text explicitly, avoiding jsonb object-key ordering.

alter function public.capture_pre_rollover_history(jsonb)
 rename to capture_pre_rollover_history_phasea_set_validated_private;
revoke all on function public.capture_pre_rollover_history_phasea_set_validated_private(jsonb)
 from public,anon,authenticated,service_role;

create or replace function public.phasea_history_sha256_private(p_material text)
returns text language sql immutable strict set search_path=pg_catalog,public as $$
 select encode(extensions.digest(pg_catalog.convert_to(p_material,'UTF8'),'sha256'),'hex')
$$;

create or replace function public.phasea_history_canonical_team_fingerprint_private(p_league_id uuid)
returns text language sql stable set search_path=pg_catalog,public as $$
 select public.phasea_history_sha256_private('['||coalesce(string_agg(
  '['||to_jsonb(t.id::text)::text||','||t.sleeper_roster_id::text||','||coalesce(to_jsonb(t.sleeper_user_id)::text,'null')||']',
  ',' order by t.id::text,t.sleeper_roster_id,coalesce(t.sleeper_user_id,'')),'')||']')
 from public.league_teams t where t.league_id=p_league_id
$$;

create or replace function public.phasea_history_source_roster_fingerprint_private(p_rows jsonb)
returns text language sql immutable set search_path=pg_catalog,public as $$
 select public.phasea_history_sha256_private('['||coalesce(string_agg(
  '['||(x->>'sleeper_roster_id')::integer::text||','||coalesce(to_jsonb(x->>'sleeper_owner_id')::text,'null')||']',
  ',' order by (x->>'sleeper_roster_id')::integer,coalesce(x->>'sleeper_owner_id','')),'')||']')
 from jsonb_array_elements(p_rows) x
$$;

create or replace function public.phasea_history_mapping_fingerprint_private(p_rows jsonb)
returns text language sql immutable set search_path=pg_catalog,public as $$
 select public.phasea_history_sha256_private('['||coalesce(string_agg(
  '['||to_jsonb((x->>'league_team_id')::uuid::text)::text||','||(x->>'sleeper_roster_id')::integer::text||','||
  coalesce(to_jsonb(x->>'sleeper_user_id')::text,'null')||']',',' order by (x->>'league_team_id')::uuid::text,
  (x->>'sleeper_roster_id')::integer,coalesce(x->>'sleeper_user_id','')),'')||']')
 from jsonb_array_elements(p_rows) x
$$;

create or replace function public.phasea_history_standings_fingerprint_private(p_rows jsonb)
returns text language sql immutable set search_path=pg_catalog,public as $$
 select public.phasea_history_sha256_private('['||coalesce(string_agg(
  '['||to_jsonb((x->>'league_team_id')::uuid::text)::text||']',',' order by (x->>'league_team_id')::uuid::text),'')||']')
 from jsonb_array_elements(p_rows) x
$$;

revoke all on function public.phasea_history_sha256_private(text) from public,anon,authenticated,service_role;
revoke all on function public.phasea_history_canonical_team_fingerprint_private(uuid) from public,anon,authenticated,service_role;
revoke all on function public.phasea_history_source_roster_fingerprint_private(jsonb) from public,anon,authenticated,service_role;
revoke all on function public.phasea_history_mapping_fingerprint_private(jsonb) from public,anon,authenticated,service_role;
revoke all on function public.phasea_history_standings_fingerprint_private(jsonb) from public,anon,authenticated,service_role;

create function public.capture_pre_rollover_history(p_plan jsonb)
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
'Service-role-only Phase A history capture; validates four compact positional-array UTF-8 SHA-256 fingerprints before atomic set validation and insertion.';

commit;
