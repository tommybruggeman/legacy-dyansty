-- The immutable-history verifier intentionally takes transaction-scoped SHARE
-- locks. PostgreSQL requires any function issuing locking reads to be VOLATILE.
begin;
set local search_path=pg_catalog,public;

alter function public.phase3b6c_verify_history_snapshot_compatible_private(
  jsonb,uuid,uuid,uuid
) volatile;

commit;
