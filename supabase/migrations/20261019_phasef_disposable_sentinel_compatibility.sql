begin;

create or replace function public.rollover_is_approved_disposable()
returns boolean
language plpgsql
stable
security definer
set search_path=pg_catalog,public
as $$
declare
  matched integer := 0;
  total integer := 0;
begin
  if to_regclass('public.environment_identity') is null then
    return false;
  end if;

  execute '
    select
      count(*),
      count(*) filter (
        where singleton
          and environment_type = $1
          and parent_project = $2
          and environment_name in ($3, $4)
      )
    from public.environment_identity
  '
  into total, matched
  using
    'disposable_test',
    'Legacy-Dynasty',
    'phase3b5h-testing',
    'rollover-phase-f-final-certification';

  return total = 1 and matched = 1;
end
$$;

comment on function public.rollover_is_approved_disposable()
is 'Fail-closed disposable-environment guard for historical phase3b5h and final Phase F certification sentinels.';

commit;
