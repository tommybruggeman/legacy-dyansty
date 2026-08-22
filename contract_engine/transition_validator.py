from __future__ import annotations


def validate_contract_transition(plan): return {"safe_to_transition":plan.safe_to_transition,"warnings":plan.warnings,"blocking_errors":plan.blocking_errors,"counts":plan.counts}
