begin;

-- Earlier environment-wide grants gave client roles write privileges despite
-- roster RLS. Phase 3B.8A requires the private security-definer writer to be
-- the sole target-assignment mutation authority.
revoke insert,update,delete,truncate,references,trigger
 on public.season_roster_assignments from public,anon,authenticated;

commit;
