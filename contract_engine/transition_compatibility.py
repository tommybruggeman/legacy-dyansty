from __future__ import annotations


def compare_transition_to_legacy(plan):
    mismatches=[x for x in plan.blocking_errors if x.get("code")=="contract_validation" and "legacy parity mismatch" in x.get("message","")]
    return {"exact":not mismatches,"differences":mismatches,"agreement_count":plan.counts["agreements"],"legacy_count":plan.counts["legacy_contracts"]}
