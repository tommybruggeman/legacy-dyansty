-- Prevent authenticated callers from bypassing canonical team authority through
-- direct cap_adjustments writes. Service-role maintenance remains unaffected.
begin;
set local search_path = pg_catalog, public;

drop policy if exists cap_adjustments_insert on public.cap_adjustments;
drop policy if exists cap_adjustments_update on public.cap_adjustments;
drop policy if exists cap_adjustments_delete on public.cap_adjustments;

create policy cap_adjustments_insert on public.cap_adjustments
for insert to authenticated
with check (
    exists (
        select 1
          from public.league_memberships m
          join public.league_teams t
            on t.league_id = cap_adjustments.league_id
           and lower(btrim(t.owner_name)) = lower(btrim(cap_adjustments.owner_name))
         where m.league_id = cap_adjustments.league_id
           and m.user_id = auth.uid()
           and (
               lower(m.role) in ('commissioner', 'admin', 'host')
               or m.league_team_id = t.id
           )
    )
);

create policy cap_adjustments_update on public.cap_adjustments
for update to authenticated
using (
    exists (
        select 1
          from public.league_memberships m
          join public.league_teams t
            on t.league_id = cap_adjustments.league_id
           and lower(btrim(t.owner_name)) = lower(btrim(cap_adjustments.owner_name))
         where m.league_id = cap_adjustments.league_id
           and m.user_id = auth.uid()
           and (
               lower(m.role) in ('commissioner', 'admin', 'host')
               or m.league_team_id = t.id
           )
    )
)
with check (
    exists (
        select 1
          from public.league_memberships m
          join public.league_teams t
            on t.league_id = cap_adjustments.league_id
           and lower(btrim(t.owner_name)) = lower(btrim(cap_adjustments.owner_name))
         where m.league_id = cap_adjustments.league_id
           and m.user_id = auth.uid()
           and (
               lower(m.role) in ('commissioner', 'admin', 'host')
               or m.league_team_id = t.id
           )
    )
);

create policy cap_adjustments_delete on public.cap_adjustments
for delete to authenticated
using (
    exists (
        select 1
          from public.league_memberships m
          join public.league_teams t
            on t.league_id = cap_adjustments.league_id
           and lower(btrim(t.owner_name)) = lower(btrim(cap_adjustments.owner_name))
         where m.league_id = cap_adjustments.league_id
           and m.user_id = auth.uid()
           and (
               lower(m.role) in ('commissioner', 'admin', 'host')
               or m.league_team_id = t.id
           )
    )
);

commit;
