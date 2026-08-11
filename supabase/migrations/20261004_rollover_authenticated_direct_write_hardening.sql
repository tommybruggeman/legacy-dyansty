begin;

-- Gate 2B production parity remediation:
-- authenticated callers must use certified authenticated function boundaries.
-- Direct mutation of rollover/prepared lifecycle tables is prohibited.

do $$
declare
  r record;
begin
  for r in
    select c.relname as table_name
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p')
      and (
        c.relname like 'rollover_%'
        or c.relname like 'prepared_%'
      )
  loop
    execute format(
      'revoke insert, update, delete, truncate on table public.%I from authenticated',
      r.table_name
    );
  end loop;
end
$$;

commit;
