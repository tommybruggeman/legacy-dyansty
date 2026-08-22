-- The canonical execution is one atomic transaction across operations 1-31.
-- Scope a longer timeout to this commissioner-only RPC instead of widening the
-- authenticated role's global eight-second budget.
begin;
set local search_path=pg_catalog,public;

alter function public.execute_rollover_plan_authenticated(jsonb)
  set statement_timeout = '120s';

commit;
