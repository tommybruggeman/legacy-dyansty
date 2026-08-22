from __future__ import annotations

from .transition_execution_models import EXECUTOR_VERSION, REQUEST_VERSION


APPROVED_2025_2026_COUNTS = {
    "agreements": 211, "continues": 92, "expires": 119, "source_obligations": 211,
    "target_obligations": 92, "season_2027_obligations": 32, "invalid": 0, "already_transitioned": 0,
}


def validate_contract_transition_execution(request, plan):
    errors=[]
    expected_key=f"contract-transition:{request.league_id}:{request.source_season}:{request.target_season}:v1"
    checks = [
        (request.dry_run in (True,False) and isinstance(request.dry_run,bool),"explicit_dry_run","dry_run must be an explicit boolean."),
        (request.transition_key==expected_key,"transition_key","Transition key is not canonical."),
        (request.request_version==REQUEST_VERSION,"request_version","Unsupported request version."),
        (request.planner_version==plan.request.planner_version,"planner_version","Planner version mismatch."),
        (request.executor_version==EXECUTOR_VERSION,"executor_version","Executor version mismatch."),
        (request.source_season==2025 and request.target_season==2026 and request.target_season==request.source_season+1,"season_pair","This approved execution is 2025 -> 2026 only."),
        (request.source_league_season_id==plan.request.source_league_season_id and request.target_league_season_id==plan.request.target_league_season_id,"season_identity","Season authority IDs differ from the plan."),
        (request.actual_source_fingerprint==request.expected_source_fingerprint==plan.source_fingerprint,"source_fingerprint","Source fingerprint mismatch."),
        (request.actual_plan_fingerprint==request.expected_plan_fingerprint==plan.plan_fingerprint,"plan_fingerprint","Plan fingerprint mismatch."),
        (plan.safe_to_transition,"planner_blocked","Transition planner is not safe."),
    ]
    for ok,code,message in checks:
        if not ok: errors.append({"code":code,"message":message})
    for key,value in APPROVED_2025_2026_COUNTS.items():
        if plan.counts.get(key)!=value or request.expected_counts.get(key)!=value:
            errors.append({"code":"approved_count_mismatch","message":f"Approved {key}={value}; actual/request differs."})
    return {"safe_to_apply":not errors,"blocking_errors":errors,"warnings":list(plan.warnings)}

