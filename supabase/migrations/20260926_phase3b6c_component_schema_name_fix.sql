begin;

alter table public.rollover_execution_input_snapshot_components
    drop constraint if exists
        rollover_execution_input_snapsho_component_schema_version_check;

alter table public.rollover_execution_input_snapshot_components
    add constraint
        rollover_execution_input_snapsho_component_schema_version_check
check (
    component_schema_version ~
    '^phase3b6c-[a-z_-]+-v(1|2)$'
);

commit;
