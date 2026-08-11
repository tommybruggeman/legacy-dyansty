begin;

revoke all on function public.guard_phase3b10b_salary_cap_setting()
from public, anon, authenticated, service_role;

alter table public.prepared_team_cap_sets
  add constraint prepared_team_cap_sets_hashes_check check (
    frozen_snapshot_hash ~ '^[0-9a-f]{64}$'
    and target_assignment_set_hash ~ '^[0-9a-f]{64}$'
    and contract_evidence_hash ~ '^[0-9a-f]{64}$'
    and dead_cap_evidence_hash ~ '^[0-9a-f]{64}$'
    and cap_limit_fingerprint ~ '^[0-9a-f]{64}$'
    and aggregate_cap_set_hash ~ '^[0-9a-f]{64}$'
  );

alter table public.prepared_team_caps
  add constraint prepared_team_caps_hashes_check check (
    contract_evidence_hash ~ '^[0-9a-f]{64}$'
    and dead_cap_evidence_hash ~ '^[0-9a-f]{64}$'
    and deterministic_row_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  add constraint prepared_team_caps_nonnegative_check check (
    salary_cap_limit > 0
    and active_target_salary >= 0
    and prepared_dead_cap >= 0
    and over_cap_amount >= 0
    and active_contract_count >= 0
    and dead_cap_obligation_count >= 0
  ),
  add constraint prepared_team_caps_block_reason_check check (
    (publication_blocked and blocking_reason_codes = array['hard_cap_exceeded']::text[])
    or (not publication_blocked and blocking_reason_codes = '{}'::text[])
  );

commit;
