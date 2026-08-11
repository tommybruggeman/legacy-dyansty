-- Phase 3A audit-only repair. Restores two missing correction records from exact
-- pre-deletion source snapshots captured during production reconciliation.
-- This migration never inserts, updates, or deletes public.contracts rows.

begin;
set local search_path = pg_catalog, public;

do $$
declare
  v_league constant uuid := '9838a0a1-97c6-4cab-bb88-af177317abfe';
  v_luke_key constant text := 'contract-source-correction:luke-musgrave-9481:v1';
  v_tyler_key constant text := 'contract-source-correction:tyler-allgeier-8132:v1';
  v_luke_affected constant uuid := '9e173761-1589-4c63-9321-013ab9ad2b6c';
  v_luke_survivor constant uuid := 'e4f94d09-e310-4fbf-9552-d581ffe5ea8f';
  v_tyler_affected constant uuid := '1ce6b9e1-26bf-4cb8-9929-ad822918d752';
  v_tyler_survivor constant uuid := 'b8ace9db-caee-43a7-97b5-574aa04fa41d';
  v_luke_previous constant jsonb := '{"id":"9e173761-1589-4c63-9321-013ab9ad2b6c","league_id":"9838a0a1-97c6-4cab-bb88-af177317abfe","owner_id":"066234b4-f092-4e2a-95f1-a9afb4d9cffe","sleeper_player_id":"9481","player_name":"Like Musgrave","contract_total_years":1,"contract_years_left":1,"salary":2.0,"created_at":"2026-06-02T04:38:05.696448+00:00","owner_name":"Chasen Hardy","player_position":"TE","is_rookie":false}'::jsonb;
  v_luke_result constant jsonb := '{"id":"e4f94d09-e310-4fbf-9552-d581ffe5ea8f","league_id":"9838a0a1-97c6-4cab-bb88-af177317abfe","owner_id":"066234b4-f092-4e2a-95f1-a9afb4d9cffe","sleeper_player_id":"9481","player_name":"Luke Musgrave","contract_total_years":1,"contract_years_left":1,"salary":2.0,"created_at":"2026-06-02T04:38:05.696448+00:00","owner_name":"Chasen Hardy","player_position":"TE","is_rookie":false}'::jsonb;
  v_tyler_previous constant jsonb := '{"id":"1ce6b9e1-26bf-4cb8-9929-ad822918d752","league_id":"9838a0a1-97c6-4cab-bb88-af177317abfe","owner_id":"cfe0420d-787f-440b-bd94-209622227680","sleeper_player_id":"8132","player_name":"Tyler Allgeier","contract_total_years":1,"contract_years_left":1,"salary":1.0,"created_at":"2026-06-02T04:38:05.696448+00:00","owner_name":"Connor Cassidy","player_position":"WR","is_rookie":false}'::jsonb;
  v_tyler_result constant jsonb := '{"id":"b8ace9db-caee-43a7-97b5-574aa04fa41d","league_id":"9838a0a1-97c6-4cab-bb88-af177317abfe","owner_id":"79608ec9-d3bb-4b9b-bdc2-a3ad98c06ce3","sleeper_player_id":"8132","player_name":"Tyler Allgeier","contract_total_years":1,"contract_years_left":1,"salary":1.0,"created_at":"2026-06-02T04:38:05.696448+00:00","owner_name":"Nando Munoz","player_position":"RB","is_rookie":false}'::jsonb;
  v_luke_evidence constant jsonb := '{"historical_roster_team_id":"02b004e4-8053-428c-bb9c-a65165259d13","sleeper_roster_id":4,"material_terms_identical":true}'::jsonb;
  v_tyler_evidence constant jsonb := '{"historical_roster_team_id":"b4c8e502-6fa7-4915-8eba-187a61f45bda","sleeper_roster_id":8,"latest_transaction_id":"1266811864516407296-add-8132","canonical_current_owner":"Nando Munoz","dead_cap_found":false,"cap_adjustment_found":false}'::jsonb;
  v_current jsonb;
begin
  if (select count(*) from public.contracts) <> 211 then
    raise exception 'Audit repair requires exactly 211 contracts.';
  end if;
  if exists (select 1 from public.contracts where id in (v_luke_affected, v_tyler_affected)) then
    raise exception 'Audit repair requires both erroneous contract rows to remain absent.';
  end if;
  select to_jsonb(c) into v_current from public.contracts c where c.id=v_luke_survivor;
  if v_current is distinct from v_luke_result then
    raise exception 'Canonical Luke Musgrave row is absent or differs from reviewed evidence.';
  end if;
  select to_jsonb(c) into v_current from public.contracts c where c.id=v_tyler_survivor;
  if v_current is distinct from v_tyler_result then
    raise exception 'Canonical Tyler Allgeier row is absent or differs from reviewed evidence.';
  end if;

  if exists (
    select 1 from public.contract_source_corrections
    where idempotency_key=v_luke_key and (
      affected_row_id<>v_luke_affected or canonical_surviving_row_id<>v_luke_survivor
      or sleeper_player_id<>'9481' or previous_values<>v_luke_previous
      or resulting_values<>v_luke_result or evidence<>v_luke_evidence
      or actor<>'phase3a_contract_reconciliation'
    )
  ) then raise exception 'Conflicting preexisting Luke Musgrave audit record.'; end if;
  if exists (
    select 1 from public.contract_source_corrections
    where idempotency_key=v_tyler_key and (
      affected_row_id<>v_tyler_affected or canonical_surviving_row_id<>v_tyler_survivor
      or sleeper_player_id<>'8132' or previous_values<>v_tyler_previous
      or resulting_values<>v_tyler_result or evidence<>v_tyler_evidence
      or actor<>'phase3a_contract_reconciliation'
    )
  ) then raise exception 'Conflicting preexisting Tyler Allgeier audit record.'; end if;

  insert into public.contract_source_corrections
    (league_id,sleeper_player_id,affected_row_id,canonical_surviving_row_id,reason,evidence,actor,previous_values,resulting_values,idempotency_key)
  values
    (v_league,'9481',v_luke_affected,v_luke_survivor,'Typo-only duplicate of the correctly spelled Luke Musgrave source row.',v_luke_evidence,'phase3a_contract_reconciliation',v_luke_previous,v_luke_result,v_luke_key),
    (v_league,'8132',v_tyler_affected,v_tyler_survivor,'Stale conflicting owner row. Immutable 2025 snapshot, Sleeper roster, transaction history, and canonical ownership establish Nando Munoz.',v_tyler_evidence,'phase3a_contract_reconciliation',v_tyler_previous,v_tyler_result,v_tyler_key)
  on conflict (idempotency_key) do nothing;

  if (select count(*) from public.contract_source_corrections where idempotency_key in (v_luke_key,v_tyler_key)) <> 2 then
    raise exception 'Audit repair did not produce exactly two required records.';
  end if;
end $$;

commit;
