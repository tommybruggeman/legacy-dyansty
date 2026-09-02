-- Canonical trade contract guard compatibility.
--
-- Canonical trades are audited contract ownership transitions. The existing
-- contract mutation guard requires legitimate contract writers to establish
-- the transaction-local contract-transition execution context.
--
-- Do not weaken or bypass the guard. Patch only the canonical trade executor.

do $$
declare
  fn text;
  old_fragment text;
  new_fragment text;
begin
  select pg_get_functiondef(
    'public.execute_canonical_trade_authenticated(jsonb)'::regprocedure
  )
  into fn;

  if fn is null then
    raise exception 'canonical_trade_executor_missing';
  end if;

  old_fragment := $patch$
  update public.contract_agreements set league_team_id=to_id,updated_at=clock_timestamp() where id=agreement.id;
  update public.contract_seasons set league_team_id=to_id,updated_at=clock_timestamp() where contract_id=agreement.id and obligation_status in('active','scheduled');$patch$;

  new_fragment := $patch$
  perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
  update public.contract_agreements set league_team_id=to_id,updated_at=clock_timestamp() where id=agreement.id;
  update public.contract_seasons set league_team_id=to_id,updated_at=clock_timestamp() where contract_id=agreement.id and obligation_status in('active','scheduled');$patch$;

  if length(fn) - length(replace(fn, old_fragment, '')) <> length(old_fragment) then
    raise exception 'canonical_trade_contract_guard_patch_shape_mismatch';
  end if;

  fn := replace(fn, old_fragment, new_fragment);
  execute fn;
end
$$;
