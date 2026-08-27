-- Authenticated league members may read Rookie Draft Board provenance.
-- Existing RLS remains the row-level authority.

grant select
on table public.rookie_draft_board_assignments
to authenticated;
