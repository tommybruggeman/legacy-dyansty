-- Restore the contract-guard context in the trade engine.
--
-- 20261104 taught the trade engine to open the transaction-local
-- contract-transition context before it moves a contract's seasons. 20261205
-- rewrote the engine as execute_canonical_trade_private and dropped that line,
-- so every trade since (Sleeper sync and owner offers alike) fails on
-- reject_historical_contract_season_mutation with "Contract obligations
-- require an audited contract-edit RPC." Same surgical patch as 20261104:
-- add the line back, change nothing else, and never weaken the guard itself.

do $$
declare
  fn text;
  old_fragment text;
  new_fragment text;
begin
  select pg_get_functiondef(
    'public.execute_canonical_trade_private(jsonb,uuid)'::regprocedure
  )
  into fn;

  if fn is null then
    raise exception 'canonical_trade_private_missing';
  end if;

  -- Already patched: nothing to do.
  if position('contract-transition-executor-v1' in fn) > 0 then
    return;
  end if;

  old_fragment := $patch$
  update public.contract_agreements set league_team_id=to_id,updated_at=clock_timestamp() where id=agreement.id;$patch$;

  new_fragment := $patch$
  perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
  update public.contract_agreements set league_team_id=to_id,updated_at=clock_timestamp() where id=agreement.id;$patch$;

  if length(fn) - length(replace(fn, old_fragment, '')) <> length(old_fragment) then
    raise exception 'canonical_trade_private_guard_patch_shape_mismatch';
  end if;

  fn := replace(fn, old_fragment, new_fragment);
  execute fn;
end
$$;
