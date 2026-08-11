begin;

-- Phase 3B.6C snapshot storage now supports both the certified
-- legacy nine-component v1 format and the split eleven-component v2 format.

alter table public.rollover_execution_input_snapshots
  drop constraint if exists
  rollover_execution_input_snapshot_snapshot_schema_version_check;

alter table public.rollover_execution_input_snapshots
  add constraint
  rollover_execution_input_snapshot_snapshot_schema_version_check
  check (
    snapshot_schema_version in (
      'phase3b6c-snapshot-v1',
      'phase3b6c-snapshot-v2'
    )
  );

commit;
