\set ON_ERROR_STOP on
begin;

do $$
declare
 lid constant uuid:='b03edc51-bec1-4064-9201-72e48ba413f9';
 replay jsonb;
 before_agreements integer;
begin
 if (select count(*) from public.contracts where league_id=lid)<>92 then raise exception 'legacy source count mismatch';end if;
 if (select count(*) from public.contract_agreements where league_id=lid and status in('active','scheduled'))<>92 then raise exception 'canonical live agreement count mismatch';end if;
 if (select count(distinct player_id) from public.contract_agreements where league_id=lid and status in('active','scheduled'))<>92 then raise exception 'canonical owned-player count mismatch';end if;
 if (select count(*) from public.contract_seasons where league_id=lid)<>185 then raise exception 'contract season count mismatch';end if;
 if (select count(*) from public.contract_seasons where league_id=lid and season=2026 and obligation_status='active')<>92 then raise exception 'active 2026 obligation count mismatch';end if;
 if (select count(*) from public.contract_seasons where league_id=lid and season>2026 and obligation_status='scheduled')<>93 then raise exception 'future scheduled obligation count mismatch';end if;
 if (select count(*) from public.contract_events where league_id=lid and event_type='imported' and source='legacy_2026_canonical_bootstrap')<>92 then raise exception 'import-event count mismatch';end if;
 if exists(select 1 from public.contract_agreements where league_id=lid and status in('active','scheduled') and superseded_by_contract_id is null group by player_id having count(*)>1) then raise exception 'duplicate live ownership exists';end if;
 if exists(select 1 from public.contract_seasons s left join public.contract_agreements a on a.id=s.contract_id where s.league_id=lid and a.id is null) then raise exception 'orphan contract obligation exists';end if;
 if exists(select 1 from public.contract_seasons where league_id=lid and dead_cap_if_released is not null) then raise exception 'bootstrap invented dead-cap authority';end if;
 if not exists(select 1 from public.league_seasons where league_id=lid and season=2026 and is_active and status='active') then raise exception 'active-season lifecycle normalization missing';end if;

 replay:=public.bootstrap_legacy_contracts_private(lid,92);
 if coalesce((replay->>'idempotent')::boolean,false) is not true then raise exception 'bootstrap replay was not idempotent';end if;
 select count(*) into before_agreements from public.contract_agreements where league_id=lid;
 update public.contracts set salary=salary+1 where league_id=lid and sleeper_player_id='player-2';
 begin
  perform public.bootstrap_legacy_contracts_private(lid,92);
  raise exception 'stale source fingerprint accepted';
 exception when others then
  if sqlerrm='stale source fingerprint accepted' then raise;end if;
 end;
 if (select count(*) from public.contract_agreements where league_id=lid)<>before_agreements then raise exception 'failed replay mutated canonical agreements';end if;

 if has_function_privilege('anon','public.bootstrap_legacy_contracts_private(uuid,integer)','execute')
  or has_function_privilege('authenticated','public.bootstrap_legacy_contracts_private(uuid,integer)','execute')
  or not has_function_privilege('service_role','public.bootstrap_legacy_contracts_private(uuid,integer)','execute')
 then raise exception 'bootstrap privilege boundary invalid';end if;
 if not (select prosecdef and proconfig@>array['search_path=pg_catalog, public'] from pg_proc where oid='public.bootstrap_legacy_contracts_private(uuid,integer)'::regprocedure)
 then raise exception 'bootstrap security definer/search_path invalid';end if;
 if position('is_active and status=''active''' in pg_get_functiondef('public.acquire_offseason_player_private(jsonb,uuid)'::regprocedure))>0
  or position('is_active and status=''active''' in pg_get_functiondef('public.release_offseason_player_authenticated(jsonb)'::regprocedure))>0
 then raise exception 'duplicate active-season semantics remain';end if;
end$$;

rollback;
