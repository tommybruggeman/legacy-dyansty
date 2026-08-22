from __future__ import annotations

from copy import deepcopy


class ContractTransitionExecutionError(RuntimeError): pass


def simulate_atomic_contract_transition(state, plan, request, *, fail_at=None):
    """Pure test oracle for RPC semantics; mutates a copy and commits only on success."""
    existing=(state.get("executions") or {}).get(request.transition_key)
    signature=(request.expected_source_fingerprint,request.expected_plan_fingerprint,request.request_version,request.planner_version,request.executor_version)
    if existing:
        if existing["signature"]!=signature or existing["status"]!="validated": raise ContractTransitionExecutionError("conflicting execution")
        return state, {**existing["result"],"idempotent":True}
    working=deepcopy(state)
    if request.dry_run:return state,{"status":"dry_run_validated","safe_to_apply":True,"idempotent":False}
    if fail_at=="validation":raise ContractTransitionExecutionError("injected validation failure")
    source_ids={x["agreement_id"] for x in plan.classifications}
    for row in working["seasons"]:
        if row["contract_id"] in source_ids and row["season"]==request.source_season:row["obligation_status"]="satisfied"
    if fail_at=="source_update":raise ContractTransitionExecutionError("injected source update failure")
    continuing={x["agreement_id"] for x in plan.classifications if x["outcome"]=="CONTINUES"}
    expiring={x["agreement_id"] for x in plan.classifications if x["outcome"]=="EXPIRES_AFTER_2025"}
    for row in working["seasons"]:
        if row["contract_id"] in continuing and row["season"]==request.target_season:row["obligation_status"]="active"
    for row in working["agreements"]:
        if row["id"] in expiring:row["status"]="expired"
    if fail_at=="agreement_expiration":raise ContractTransitionExecutionError("injected agreement failure")
    for aid in sorted(expiring):
        key=f"contract-expired:{aid}:{request.source_season}:{request.target_season}:v1"
        if not any(x["idempotency_key"]==key for x in working["events"]):
            working["events"].append({"contract_id":aid,"event_type":"expired","effective_season":request.source_season,
                "idempotency_key":key,"metadata":{"reason":"natural_expiration","dead_cap_consequence":False,
                "roster_consequence":False,"free_agent_publication_consequence":False}})
    if fail_at=="event_creation":raise ContractTransitionExecutionError("injected event failure")
    result={"status":"validated","idempotent":False,"satisfied":len(source_ids),"activated":len(continuing),"expired":len(expiring),"events":len(expiring)}
    working.setdefault("executions",{})[request.transition_key]={"status":"validated","signature":signature,"result":result,"id":"execution-1"}
    return working,result
