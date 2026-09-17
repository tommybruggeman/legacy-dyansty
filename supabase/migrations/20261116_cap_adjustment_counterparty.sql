-- Name the other team on a traded-cash adjustment.
--
-- cap_adjustment_display_rows already formats these as "$4 to <owner>" and
-- "+$4 from <owner>", but nothing ever wrote counterparty_owner, so every
-- cash leg rendered against a missing value -- "+$12 from nan" on the Teams
-- page. The team-state reads select a.*, so adding the column is enough to
-- carry it through to the UI with no RPC change.

begin;
set local search_path = pg_catalog, public;

alter table public.cap_adjustments
    add column if not exists counterparty_owner text;

comment on column public.cap_adjustments.counterparty_owner is
    'For trade_carryover rows: the other team in the cash leg. Positive amount '
    'means this team sent cap space to counterparty_owner and is charged for '
    'it; negative means it received cap space and is credited.';

commit;
