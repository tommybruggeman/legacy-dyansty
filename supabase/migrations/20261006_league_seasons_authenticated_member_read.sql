begin;

-- league_memberships.memberships_select_self is rooted only in auth.uid(), so
-- this cross-table policy has no league_seasons -> league_memberships ->
-- league_seasons recursion. Count every visible membership for the actor and
-- require the sole row to use the canonical commissioner/member role model.
alter table public.league_seasons enable row level security;

revoke select on table public.league_seasons from public,anon;
grant select on table public.league_seasons to authenticated;

drop policy if exists league_seasons_canonical_membership_select
  on public.league_seasons;
create policy league_seasons_canonical_membership_select
  on public.league_seasons
  for select
  to authenticated
  using (
    auth.uid() is not null
    and (
      select count(*)
      from public.league_memberships membership
      where membership.league_id=league_seasons.league_id
        and membership.user_id=auth.uid()
    )=1
    and exists (
      select 1
      from public.league_memberships membership
      where membership.league_id=league_seasons.league_id
        and membership.user_id=auth.uid()
        and lower(membership.role) in ('commissioner','member')
    )
  );

comment on policy league_seasons_canonical_membership_select
  on public.league_seasons is
  'Authenticated season reads require exactly one canonical commissioner/member membership in the row league.';

commit;
