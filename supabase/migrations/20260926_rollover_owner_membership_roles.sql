begin;

alter table public.league_memberships
  drop constraint if exists league_memberships_role_check;
alter table public.league_memberships
  add constraint league_memberships_role_check
  check (lower(role) in ('commissioner', 'member', 'owner', 'co_owner', 'co-owner', 'admin', 'host'));

commit;
