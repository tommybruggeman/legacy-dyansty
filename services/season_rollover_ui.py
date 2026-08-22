from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping
from uuid import uuid4

import streamlit as st

from services.season_rollover_control import (
    RolloverControlError,
    SeasonRolloverControlService,
    build_commissioner_rollover_report,
    derive_lifecycle_timeline,
    rollover_admin_password_configured,
    verify_rollover_admin_password,
)

REVIEW_PAGE_SIZES = (25, 50)


def bounded_stable_page(rows, *, page=1, page_size=25, stable_key="id"):
    if page_size not in REVIEW_PAGE_SIZES or page < 1:
        raise ValueError("invalid bounded page request")
    material = list(rows)
    stable_ids = [str(row.get(stable_key) or "") for row in material]
    if any(not value for value in stable_ids) or len(set(stable_ids)) != len(stable_ids):
        raise ValueError("bounded page requires unique non-empty stable IDs")
    material.sort(key=lambda row: str(row[stable_key]))
    pages = max(1, (len(material) + page_size - 1) // page_size)
    selected = min(page, pages)
    start = (selected - 1) * page_size
    displayed_rows = material[start:start + page_size]
    return {"rows": displayed_rows, "filtered": len(material), "displayed": len(displayed_rows),
            "page": selected, "pages": pages, "page_size": page_size}


def bounded_review_page(reviews, *, search="", review_type="all", status="exceptions", page=1, page_size=25,
                        exception_only=None):
    if exception_only is not None:
        status = "exceptions" if exception_only else "all"
    needle = str(search or "").strip().lower()
    exception_states = {"pending", "blocked", "evidence_required", "superseded", "under_review"}
    filtered = []
    for row in reviews:
        row_type = str(row.get("review_type") or "")
        row_status = str(row.get("review_state") or "")
        haystack = " ".join(str(row.get(key) or "") for key in
                            ("player_id", "player_name", "league_team_id", "review_type", "review_state")).lower()
        if needle and needle not in haystack: continue
        if review_type != "all" and row_type != review_type: continue
        if status == "exceptions" and row_status not in exception_states: continue
        if status not in {"all", "exceptions"} and row_status != status: continue
        filtered.append(row)
    result = bounded_stable_page(filtered, page=page, page_size=page_size)
    return {**result, "total": len(reviews)}


def _key(label: str, execution_id: str) -> str:
    return f"rollover-ui:{label}:{execution_id}:{uuid4()}"


def _current(rows: list[Mapping[str, Any]], status_key: str, allowed: set[str]) -> Mapping[str, Any] | None:
    matches = [row for row in rows if str(row.get(status_key)) in allowed]
    matches.sort(key=lambda row: int(row.get("version") or row.get("plan_version") or row.get("simulation_version") or 0), reverse=True)
    return matches[0] if matches else None


def _protected_form(form_key: str, target: int, phrase: str | None = None,
                    expected_execution_id: str | None = None) -> tuple[bool, str]:
    password = st.text_input("Rollover administrator password", type="password", key=f"{form_key}-password")
    confirmation = ""
    if phrase:
        confirmation = st.text_input(f"Type {phrase}", key=f"{form_key}-confirmation")
    target_confirmation = st.text_input("Exact target season", key=f"{form_key}-target-season")
    execution_confirmation = ""
    if expected_execution_id:
        execution_confirmation = st.text_input("Exact execution ID", key=f"{form_key}-execution-id")
    submitted = st.form_submit_button("Confirm protected action", use_container_width=True)
    if not submitted:
        return False, ""
    if not rollover_admin_password_configured():
        return False, "Rollover administrator confirmation is not configured."
    if not verify_rollover_admin_password(password):
        return False, "Rollover administrator confirmation failed."
    if phrase and confirmation.strip() != phrase:
        return False, "The confirmation phrase does not match."
    if target_confirmation.strip() != str(target):
        return False, "The target season does not match canonical state."
    if expected_execution_id and execution_confirmation.strip() != expected_execution_id:
        return False, "The execution ID does not match canonical state."
    return True, ""


def _rpc(service: SeasonRolloverControlService, name: str, request: Mapping[str, Any]) -> None:
    before = service.load_state()
    before_marker = _state_marker(before)
    try:
        service.authenticated_rpc(name, request)
    except RolloverControlError as exc:
        st.error(str(exc))
    else:
        after = service.load_state()
        if _state_marker(after) == before_marker:
            st.info("The command was a compatible replay; canonical state was already current.")
            return
        st.session_state["rollover_verified_notice"] = "Canonical persisted state confirms the transition."
        st.rerun()


def _state_marker(state: Mapping[str, Any]) -> tuple[Any, ...]:
    execution = state.get("execution") or {}
    return (execution.get("status"), execution.get("version"),
            *(len(state.get(key) or ()) for key in (
                "owner_decisions", "preparations", "simulations", "plans", "approvals", "operation_results",
                "finalizations", "season_publications", "cap_publications", "market_publications",
                "cutover_releases", "context_generations")))


def execution_ui_enabled() -> bool:
    """Fail closed unless a disposable/local operator explicitly enables execution UI."""
    return (
        os.getenv("LEGACY_DISPOSABLE_ROLLOVER_EXECUTION_UI", "").strip() == "1"
        and os.getenv("LEGACY_ENVIRONMENT_TYPE", "").strip() == "disposable_test"
    )


def production_operator_ui_enabled(league_id: str) -> bool:
    """Independent production allowlist; never reuses the disposable feature contract."""
    allowlist = {value.strip() for value in os.getenv(
        "LEGACY_PRODUCTION_ROLLOVER_LEAGUE_IDS", "").split(",") if value.strip()}
    return (
        os.getenv("LEGACY_PRODUCTION_ROLLOVER_OPERATOR_UI", "").strip() == "1"
        and os.getenv("LEGACY_ENVIRONMENT_TYPE", "").strip() == "production"
        and str(league_id) in allowlist
    )


def rollover_operator_ui_enabled(league_id: str) -> bool:
    return execution_ui_enabled() or production_operator_ui_enabled(league_id)


def matching_active_cutover_lock(
    locks: list[Mapping[str, Any]], approval: Mapping[str, Any] | None, plan: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not approval or not plan:
        return None
    return next((row for row in locks
        if row.get("status") == "active" and row.get("lock_type") == "cutover"
        and str(row.get("approval_id")) == str(approval.get("id"))
        and str(row.get("execution_plan_id")) == str(plan.get("id"))
        and str(row.get("plan_fingerprint")) == str(plan.get("plan_fingerprint"))), None)


def execution_idempotency_key(execution: Mapping[str, Any], approval: Mapping[str, Any], plan: Mapping[str, Any]) -> str:
    return f"rollover-ui-execute:{execution['id']}:{approval['id']}:{plan['plan_fingerprint']}"


def _verified_success(message: str) -> None:
    st.session_state["rollover_verified_notice"] = message
    st.rerun()


def render_season_rollover_control(authenticated_client: Any, league_id: str, league_name: str = "Current League") -> None:
    st.markdown("### Season Rollover")
    st.caption("Commissioner-only, state-gated control of the certified rollover lifecycle.")
    service = SeasonRolloverControlService(authenticated_client, league_id)
    try:
        service.authorize()
        state = service.load_state()
    except RolloverControlError as exc:
        st.error(str(exc))
        return
    notice = st.session_state.pop("rollover_verified_notice", None)
    if notice: st.success(notice)

    execution = state.get("execution")
    if not execution:
        try:
            readiness = service.load_initiation_readiness()
        except RolloverControlError as exc:
            st.error(str(exc)); return
        st.info("Not started")
        a, b, c, d = st.columns(4)
        a.metric("Season boundary", f"{readiness.source_season or '—'} → {readiness.target_season or '—'}")
        b.metric("Team mappings", f"{readiness.mapped_team_count}/{readiness.canonical_team_count}")
        c.metric("History", readiness.history_status)
        d.metric("Policy", readiness.policy_status)
        st.json({"sleeper_league_linked": bool(readiness.sleeper_league_id),
                 "execution_status": readiness.execution_status,
                 "contract_authority_status": readiness.contract_authority_status,
                 "contract_agreement_count": readiness.contract_agreement_count,
                 "contract_source_season_count": readiness.contract_source_season_count,
                 "contract_authority_blockers": list(readiness.contract_authority_blockers),
                 "blockers": list(readiness.blockers)})
        st.markdown("#### Lifecycle")
        icons = {"complete": "✅", "current": "▶️", "blocked": "⛔", "pending": "○", "warning": "⚠️"}
        empty_state = {"execution": None}
        for offset in range(0, 12, 4):
            cols = st.columns(4)
            for stage, col in zip(derive_lifecycle_timeline(empty_state, readiness)[offset:offset + 4], cols):
                with col:
                    st.markdown(f"**{icons[stage.status]} {stage.number}. {stage.name}**")
                    st.caption(stage.summary)
                    if stage.blockers: st.caption("Blocked: " + ", ".join(stage.blockers))
        target = readiness.target_season or 0
        if readiness.policy_status != "approved" and not readiness.blockers:
            with st.form("rollover-approve-policy", clear_on_submit=True):
                st.write("Approve the certified seven-calendar-day owner-option policy. Missed responses remain planned for release to commissioner hold only during later certified execution.")
                ok, error = _protected_form("rollover-approve-policy", target)
            if error: st.error(error)
            elif ok:
                try: service.approve_canonical_seven_day_policy()
                except RolloverControlError as exc: st.error(str(exc))
                else:
                    confirmed = service.load_initiation_readiness()
                    if confirmed.policy_status != "approved": st.error("Policy approval was not confirmed by canonical state.")
                    else: _verified_success("Canonical state confirms policy approval.")
        elif readiness.policy_status == "approved" and readiness.history_status != "validated":
            with st.form("rollover-capture-history", clear_on_submit=True):
                st.write("Capture immutable closing-season history using the certified replay-safe history service.")
                ok, error = _protected_form("rollover-capture-history", target)
            if error: st.error(error)
            elif ok:
                try: service.capture_immutable_history()
                except RolloverControlError as exc: st.error(str(exc))
                else:
                    confirmed = service.load_initiation_readiness()
                    if confirmed.history_status != "validated": st.error("Historical capture was not confirmed by canonical state.")
                    else: _verified_success("Canonical state confirms immutable history validation.")
        elif readiness.policy_status == "approved" and readiness.history_status == "validated" and not readiness.blockers:
            with st.form("rollover-start", clear_on_submit=True):
                st.write("Run canonical preflight and create the initial rollover execution only if every check passes.")
                ok, error = _protected_form("rollover-start", target, f"START {target}")
            if error: st.error(error)
            elif ok:
                try: service.run_preflight_and_create_execution()
                except RolloverControlError as exc: st.error(str(exc))
                else:
                    confirmed = service.load_state()
                    if not confirmed.get("execution"): st.error("Execution creation was not confirmed by canonical state.")
                    else: _verified_success("Canonical state confirms preflight eligibility and execution creation.")
        else:
            st.warning("Resolve the listed readiness blockers before initiation.")
        return

    execution_id = str(execution["id"])
    target = int(execution["target_season"])
    status = str(execution.get("status") or "unknown")
    preparations = list(state.get("preparations") or [])
    simulation = _current(list(state.get("simulations") or []), "simulation_status", {"valid", "blocked"})
    plan = _current(list(state.get("plans") or []), "plan_status", {"valid", "approved_for_execution"})
    approval = _current(list(state.get("approvals") or []), "approval_status", {"approved"})
    operation_results = list(state.get("operation_results") or [])
    completed = {int(row.get("operation_index") or 0) for row in operation_results}
    owner_decisions = list(state.get("owner_decisions") or [])
    commissioner_reviews = list(state.get("commissioner_reviews") or [])

    locks = list(state.get("locks") or [])
    matching_lock = matching_active_cutover_lock(locks, approval, plan)
    active_lock = matching_lock is not None
    publication_steps = sum(bool(state.get(key)) for key in
        ("season_publications", "cap_publications", "market_publications", "cutover_releases", "context_generations"))
    st.markdown(f"#### {league_name}")
    a, b, c, d = st.columns(4)
    a.metric("Commissioner", "Authenticated")
    b.metric("Season", f"{execution.get('source_season')} → {target}")
    c.metric("Rollover state", status.replace("_", " "))
    d.metric("Publication", f"{publication_steps}/5")
    e, f, g, h = st.columns(4)
    e.metric("Owner deadline", str(execution.get("owner_option_deadline") or "Not set"))
    f.metric("Cutover lock", "Active" if active_lock else "Inactive")
    g.metric("Execution", f"{len(completed.intersection(range(1, 32)))}/31")
    h.metric("Target season", target)

    timeline = derive_lifecycle_timeline(state)
    st.markdown("#### Lifecycle")
    icons = {"complete": "✅", "current": "▶️", "blocked": "⛔", "pending": "○", "warning": "⚠️"}
    for offset in range(0, 12, 4):
        cols = st.columns(4)
        for stage, col in zip(timeline[offset:offset + 4], cols):
            with col:
                st.markdown(f"**{icons[stage.status]} {stage.number}. {stage.name}**")
                st.caption(stage.summary)
                if stage.blockers: st.caption("Blocked: " + ", ".join(stage.blockers))

    with st.expander("Canonical evidence", expanded=True):
        st.json({
            "execution_id": execution_id,
            "policy_evidence": "verified" if execution.get("policy_fingerprint") else "missing",
            "preflight_evidence": "verified" if execution.get("preflight_fingerprint") else "missing",
            "simulation": None if not simulation else {k: simulation.get(k) for k in (
                "id", "simulation_status", "input_fingerprint", "result_fingerprint", "blockers", "warnings",
                "expected_operation_effects", "result_payload")},
            "plan": None if not plan else {k: plan.get(k) for k in (
                "id", "plan_status", "plan_version", "plan_input_fingerprint", "plan_fingerprint",
                "operation_count", "operations", "operation_catalog", "blockers", "warnings")},
            "approval": None if not approval else {k: approval.get(k) for k in (
                "id", "approval_status", "approval_version", "approval_fingerprint", "plan_fingerprint")},
            "matching_cutover_lock_id": None if not matching_lock else matching_lock.get("id"),
        })

    evidence_counts = {
        "owner_outcomes": len(owner_decisions),
        "commissioner_outcomes": len(commissioner_reviews),
        "authority_preparations": len(preparations),
        "target_roster_assignment_sets": len(state.get("target_roster_sets") or ()),
        "taxi_unlock_sets": len(state.get("taxi_unlock_sets") or ()),
        "draft_inventory_generations": len(state.get("draft_generations") or ()),
        "rookie_eligibility_sets": len(state.get("rookie_eligibility_sets") or ()),
        "prepared_team_cap_sets": len(state.get("prepared_cap_sets") or ()),
        "prepared_free_agent_sets": len(state.get("prepared_free_agent_sets") or ()),
        "prepared_expiring_contract_sets": len(state.get("prepared_expiring_sets") or ()),
        "prepared_standings_sets": len(state.get("prepared_standings_sets") or ()),
        "prepared_matchup_sets": len(state.get("prepared_matchup_sets") or ()),
        "prepared_playoff_structures": len(state.get("prepared_playoff_structures") or ()),
        "cache_context_manifests": len(state.get("cache_manifests") or ()),
    }
    with st.expander("Canonical artifact counts", expanded=bool(simulation or plan)):
        st.json(evidence_counts)

    terminal_execution_states = {"executing", "executed_unpublished", "completed", "cancelled", "failed_postcommit_validation"}
    if status not in terminal_execution_states and not active_lock and not execution.get("committed_at"):
        with st.expander("Recovery · Cancel rollover execution", expanded=False):
            with st.form("rollover-cancel-execution", clear_on_submit=True):
                reason = st.text_area("Cancellation reason", key="rollover-cancel-reason")
                ok, error = _protected_form("rollover-cancel-execution", target, f"CANCEL {target}", execution_id)
            if error: st.error(error)
            elif ok:
                _rpc(service, "cancel_rollover_execution_authenticated", {
                    "rollover_execution_id": execution_id, "reason": reason.strip(),
                    "idempotency_key": f"execution-cancel:{execution_id}:{execution.get('version', 1)}",
                    "material_metadata": {"source": "commissioner_rollover_control"},
                })

    if status == "execution_ready" and approval and plan and matching_lock:
        with st.expander("Recovery · Revoke plan approval", expanded=False):
            with st.form("rollover-revoke-approval", clear_on_submit=True):
                reason = st.text_area("Revocation reason", key="rollover-revoke-reason")
                ok, error = _protected_form("rollover-revoke-approval", target, f"REVOKE {target}", execution_id)
            if error: st.error(error)
            elif ok:
                _rpc(service, "revoke_rollover_execution_plan_approval_authenticated", {
                    "rollover_execution_id": execution_id, "approval_id": approval["id"],
                    "expected_approval_fingerprint": approval["approval_fingerprint"],
                    "expected_plan_fingerprint": approval["plan_fingerprint"],
                    "expected_simulation_result_fingerprint": approval["simulation_result_fingerprint"],
                    "reason": reason.strip(),
                    "idempotency_key": f"approval-revoke:{approval['id']}:{approval['approval_version']}",
                    "material_metadata": {"source": "commissioner_rollover_control"},
                })

    mutable_preparations = [row for row in preparations
                            if row.get("authority_status") in {"prepared", "blocked", "approved_for_execution"}]
    if mutable_preparations and not active_lock and status not in terminal_execution_states:
        with st.expander("Recovery · Authority preparation", expanded=False):
            st.caption("Cancellation is available before locking. Supersession requires canonical replacement generation and is not exposed by this UI.")
            for preparation in mutable_preparations:
                label = f"{preparation.get('authority_type')} v{preparation.get('version')}"
                st.write(label)
                with st.form(f"authority-cancel-{preparation['id']}", clear_on_submit=True):
                    reason = st.text_input("Cancellation reason", key=f"authority-cancel-reason-{preparation['id']}")
                    ok, error = _protected_form(f"authority-cancel-{preparation['id']}", target,
                                                expected_execution_id=execution_id)
                if error: st.error(error)
                elif ok:
                    _rpc(service, "cancel_rollover_authority_preparation_authenticated", {
                        "preparation_id": preparation["id"], "expected_current_version": preparation["version"],
                        "expected_current_authority_status": preparation["authority_status"],
                        "expected_current_authority_fingerprint": preparation["authority_fingerprint"],
                        "expected_current_preparation_fingerprint": preparation["preparation_fingerprint"],
                        "reason": reason.strip(),
                        "idempotency_key": f"authority-cancel:{preparation['id']}:{preparation['version']}",
                        "material_metadata": {"source": "commissioner_rollover_control"},
                    })

    report = build_commissioner_rollover_report(state)
    with st.expander("Commissioner Rollover Report", expanded=False):
        display = {key.replace("_", " ").title(): ("Not available" if value is None else value)
                   for key, value in report.items()}
        st.json(display)
        st.download_button("Download sanitized JSON report", data=json.dumps(display, sort_keys=True, indent=2, default=str),
                           file_name=f"season-rollover-{target}-report.json", mime="application/json")

    st.markdown("#### 1–4 · Preparation and canonical planning")
    if status == "preflight_ready":
        preflight = dict((execution.get("metadata") or {}).get("canonical_preflight") or {})
        owner_population = list(preflight.get("owner_population") or [])
        with st.form("rollover-open-window", clear_on_submit=True):
            notice = st.text_input("Official notice timestamp (UTC)", value=datetime.now(timezone.utc).isoformat())
            st.caption("The database calculates the close timestamp as exactly seven calendar days after this notice.")
            ok, error = _protected_form("rollover-open-window", target, expected_execution_id=execution_id)
        if error: st.error(error)
        elif ok:
            _rpc(service, "open_rollover_notice_window_authenticated", {
                "rollover_execution_id": execution_id, "official_notice_timestamp": notice,
                "expected_preflight_fingerprint": execution["preflight_fingerprint"],
                "expected_owner_population_fingerprint": preflight["owner_population_fingerprint"],
                "calculated_owner_population_fingerprint": preflight["owner_population_fingerprint"],
                "expected_owner_count": len(owner_population), "owner_population": owner_population,
                "idempotency_key": _key("open-window", execution_id),
            })
    elif status in {"notice_open", "decision_window_open"}:
        deadline_text = execution.get("owner_option_deadline")
        deadline = datetime.fromisoformat(str(deadline_text).replace("Z", "+00:00")) if deadline_text else None
        unresolved = [row for row in owner_decisions if row.get("decision_status") not in {
            "planned_retention", "planned_release", "commissioner_review_requested", "no_response", "execution_ready"}]
        st.write(f"Owner responses: {len(owner_decisions) - len(unresolved)}/{len(owner_decisions)}")
        st.write(f"Canonical deadline: {deadline_text or 'Unavailable'}")
        if deadline and datetime.now(timezone.utc) >= deadline:
            with st.form("rollover-close-window", clear_on_submit=True):
                ok, error = _protected_form("rollover-close-window", target, expected_execution_id=execution_id)
            if error: st.error(error)
            elif ok:
                _rpc(service, "close_rollover_decision_window_authenticated", {
                    "rollover_execution_id": execution_id,
                    "effective_close_timestamp": datetime.now(timezone.utc).isoformat(),
                    "expected_population_fingerprint": execution.get("decision_population_fingerprint") or
                        (execution.get("metadata") or {}).get("canonical_preflight", {}).get("owner_population_fingerprint"),
                    "idempotency_key": _key("close-window", execution_id),
                })
        else:
            st.info("The owner-option window remains open. Closure is unavailable until the canonical deadline.")
            if (str(service.league_id) == "9838a0a1-97c6-4cab-bb88-af177317abfe"
                    and int(execution.get("source_season") or 0) == 2025
                    and int(execution.get("target_season") or 0) == 2026
                    and not owner_decisions):
                with st.form("abs-2025-2026-immediate-rollover", clear_on_submit=True):
                    st.markdown("**ABs 2025→2026 Immediate Rollover**")
                    confirmation = st.text_input("Type the exact commissioner confirmation")
                    submitted = st.form_submit_button("Consume one-time authority and close")
                if submitted:
                    try: service.close_abs_2025_2026_immediate_rollover(execution_id, confirmation)
                    except RolloverControlError as exc: st.error(str(exc))
                    else: _verified_success("Canonical state confirms the one-time ABs window closure.")
    elif status == "decision_window_closed":
        review_state = service.review_readiness(execution_id)
        if not commissioner_reviews:
            with st.form("rollover-initialize-reviews", clear_on_submit=True):
                st.write("Freeze the canonical commissioner-review population.")
                ok, error = _protected_form("rollover-initialize-reviews", target,
                                            expected_execution_id=execution_id)
            if error: st.error(error)
            elif ok:
                try: service.initialize_canonical_reviews(execution_id)
                except RolloverControlError as exc: st.error(str(exc))
                else:
                    confirmed = service.load_state()
                    if not confirmed.get("commissioner_reviews"): st.error("Review initialization was not confirmed by canonical state.")
                    else: _verified_success("Canonical state confirms commissioner-review initialization.")
        else:
            completed_reviews = sum(x.get('review_state') in {'approved','rejected'} for x in commissioner_reviews)
            st.write(f"Commissioner reviews: {completed_reviews}/{len(commissioner_reviews)} complete")
            filter_columns = st.columns(4)
            search = filter_columns[0].text_input("Search reviews", key="rollover-review-search")
            review_types = ["all"] + sorted({str(x.get("review_type")) for x in commissioner_reviews if x.get("review_type")})
            review_type = filter_columns[1].selectbox("Review type", review_types, key="rollover-review-type")
            review_states = ["exceptions", "all"] + sorted({str(x.get("review_state")) for x in commissioner_reviews if x.get("review_state")})
            review_status = filter_columns[2].selectbox("Status", review_states, key="rollover-review-status")
            review_page_size = filter_columns[3].selectbox("Rows per page", REVIEW_PAGE_SIZES, key="rollover-review-size")
            preview = bounded_review_page(commissioner_reviews, search=search, review_type=review_type,
                                          status=review_status, page=1, page_size=review_page_size)
            review_page = st.number_input("Review page", min_value=1, max_value=preview["pages"], value=1,
                                          step=1, key="rollover-review-page")
            review_view = bounded_review_page(commissioner_reviews, search=search, review_type=review_type,
                                              status=review_status, page=int(review_page), page_size=review_page_size)
            st.caption(f"Total {review_view['total']} · Filtered {review_view['filtered']} · "
                       f"Displayed {review_view['displayed']} · Page {review_view['page']}/{review_view['pages']}")
            for review in review_view["rows"]:
                safe_id = str(review["id"])[:8]
                with st.expander(f"{review.get('player_id')} · {review.get('review_type')} · {review.get('review_state')}"):
                    st.json({"player": review.get("player_id"), "team": review.get("league_team_id"),
                        "review_identifier": safe_id, "review_type": review.get("review_type"),
                        "state": review.get("review_state"), "allowed_outcomes": list(service.allowed_review_outcomes(review)),
                        "blockers": list(review.get("blockers") or ()), "warnings": list(review.get("warnings") or ()),
                        "evidence_complete": bool(review.get("evidence_complete"))})
                    if review.get("review_state") in {"pending", "blocked", "evidence_required", "superseded"}:
                        with st.form(f"review-begin-{review['id']}", clear_on_submit=True):
                            ok, error = _protected_form(f"review-begin-{review['id']}", target,
                                                        expected_execution_id=execution_id)
                        if error: st.error(error)
                        elif ok:
                            try: service.begin_canonical_review(review)
                            except RolloverControlError as exc: st.error(str(exc))
                            else: _verified_success(f"Canonical state confirms review {safe_id} is in progress.")
                    elif review.get("review_state") == "under_review":
                        outcomes = [x for x in service.allowed_review_outcomes(review) if x != "cancelled"]
                        with st.form(f"review-submit-{review['id']}", clear_on_submit=True):
                            outcome = st.selectbox("Outcome", outcomes, key=f"review-outcome-{review['id']}")
                            reason = st.text_area("Decision reason", key=f"review-reason-{review['id']}")
                            ok, error = _protected_form(f"review-submit-{review['id']}", target,
                                                        f"REVIEW {safe_id}", execution_id)
                        if error: st.error(error)
                        elif ok:
                            try: service.submit_canonical_review(review, outcome, reason, dict(review.get("evidence") or {}))
                            except RolloverControlError as exc: st.error(str(exc))
                            else:
                                confirmed = service.load_state(); current = next((x for x in confirmed.get("commissioner_reviews", ()) if x["id"] == review["id"]), {})
                                if current.get("review_state") not in {"approved", "rejected", "blocked"}: st.error("Final review transition was not confirmed.")
                                else: _verified_success(f"Canonical state confirms review {safe_id} was finalized.")
                    if review.get("review_state") in {"approved", "rejected"} and not active_lock:
                        with st.form(f"review-supersede-{review['id']}", clear_on_submit=True):
                            reason = st.text_input("Supersession reason", key=f"review-supersede-reason-{review['id']}")
                            ok, error = _protected_form(f"review-supersede-{review['id']}", target,
                                                        expected_execution_id=execution_id)
                        if error: st.error(error)
                        elif ok:
                            _rpc(service, "supersede_rollover_commissioner_review_authenticated", {
                                "review_id": review["id"], "reason": reason.strip(),
                                "expected_revision_number": review["revision_number"],
                                "expected_review_fingerprint": review["review_fingerprint"],
                                "idempotency_key": f"review-supersede:{review['id']}:{review['revision_number']}",
                            })
                    elif review.get("review_state") not in {"approved", "rejected", "executed", "cancelled"} and not active_lock:
                        with st.form(f"review-cancel-{review['id']}", clear_on_submit=True):
                            reason = st.text_input("Cancellation reason", key=f"review-cancel-reason-{review['id']}")
                            ok, error = _protected_form(f"review-cancel-{review['id']}", target,
                                                        expected_execution_id=execution_id)
                        if error: st.error(error)
                        elif ok:
                            _rpc(service, "cancel_rollover_commissioner_review_authenticated", {
                                "review_id": review["id"], "reason": reason.strip(),
                                "expected_revision_number": review["revision_number"],
                                "expected_review_fingerprint": review["review_fingerprint"],
                                "idempotency_key": f"review-cancel:{review['id']}:{review['revision_number']}",
                            })
            if review_state.get("status") == "authority_preparation_required":
                with st.form("rollover-prepare-authorities", clear_on_submit=True):
                    st.write("Prepare publication, dead-cap, and salary-cap authority from final canonical outcomes.")
                    ok, error = _protected_form("rollover-prepare-authorities", target,
                                                f"PREPARE {target}", execution_id)
                if error: st.error(error)
                elif ok:
                    try: service.prepare_canonical_authorities(execution_id)
                    except RolloverControlError as exc: st.error(str(exc))
                    else:
                        confirmed = service.load_state()
                        if str((confirmed.get("execution") or {}).get("status")) != "authority_ready": st.error("Authority readiness was not confirmed.")
                        else: _verified_success("Canonical state confirms all three authorities are prepared.")
            elif review_state.get("blockers"):
                st.warning("Review progression is blocked: " + ", ".join(map(str, review_state["blockers"])))
    elif status == "authority_ready" and len([x for x in preparations if x.get("authority_status") == "prepared"]) == 3 and simulation is None:
        with st.form("rollover-generate-dry-run", clear_on_submit=True):
            ok, error = _protected_form("rollover-generate-dry-run", target,
                                        expected_execution_id=execution_id)
        if error:
            st.error(error)
        elif ok:
            try:
                service.generate_canonical_dry_run(execution_id, {
                    "expected_execution_status": "authority_ready",
                    "expected_policy_id": execution["policy_id"],
                    "expected_policy_fingerprint": execution["policy_fingerprint"],
                    "expected_preflight_fingerprint": execution["preflight_fingerprint"],
                    "expected_owner_population_fingerprint": execution["decision_population_fingerprint"],
                    "simulator_version": "rollover-dry-run-v1",
                    "idempotency_key": _key("dry-run", execution_id),
                    "material_metadata": {"source": "commissioner_rollover_control"},
                })
            except RolloverControlError as exc:
                st.error(str(exc))
            else:
                confirmed = service.load_state()
                if not confirmed.get("simulations"): st.error("Dry-run generation was not confirmed by canonical state.")
                else: _verified_success("Canonical state confirms dry-run generation.")
    elif simulation and simulation.get("blockers"):
        st.error("Canonical dry run is blocked: " + ", ".join(map(str, simulation.get("blockers") or ())))
    elif simulation and not plan:
        with st.form("rollover-generate-plan", clear_on_submit=True):
            ok, error = _protected_form("rollover-generate-plan", target,
                                        expected_execution_id=execution_id)
        if error:
            st.error(error)
        elif ok:
            try:
                service.generate_canonical_execution_plan(execution_id, str(simulation["id"]), {
                    "idempotency_key": _key("plan", execution_id),
                    "material_metadata": {"source": "commissioner_rollover_control"},
                })
            except RolloverControlError as exc:
                st.error(str(exc))
            else:
                confirmed = service.load_state()
                if not confirmed.get("plans"): st.error("Execution-plan generation was not confirmed by canonical state.")
                else: _verified_success("Canonical state confirms execution-plan generation.")
    else:
        st.info("Preparation and planning actions are hidden because they are not valid for the current canonical state.")

    st.markdown("#### 5 · Plan approval")
    if plan and plan.get("plan_status") == "valid" and not approval and simulation:
        with st.form("rollover-approve-plan", clear_on_submit=True):
            statement = st.text_area("Approval statement", value=f"Approve the canonical {target} rollover execution plan.")
            ok, error = _protected_form("rollover-approve-plan", target,
                                        expected_execution_id=execution_id)
        if error:
            st.error(error)
        elif ok:
            request = {
                "rollover_execution_id": execution_id, "execution_plan_id": plan["id"],
                "execution_plan_version": plan["plan_version"], "simulation_id": simulation["id"],
                "expected_execution_status": status, "expected_plan_status": "valid",
                "expected_plan_input_fingerprint": plan["plan_input_fingerprint"],
                "expected_plan_fingerprint": plan["plan_fingerprint"], "expected_operation_count": plan["operation_count"],
                "expected_simulation_result_fingerprint": simulation["result_fingerprint"],
                "expected_simulation_input_fingerprint": simulation["input_fingerprint"],
                "expected_preflight_fingerprint": simulation["preflight_fingerprint"],
                "expected_policy_fingerprint": simulation["policy_fingerprint"],
                "expected_owner_population_fingerprint": simulation["owner_population_fingerprint"],
                "expected_commissioner_population_fingerprint": simulation["commissioner_population_fingerprint"],
                "expected_authority_preparation_fingerprint": simulation["authority_preparation_fingerprint"],
                "approval_statement_code": "ROLLOVER_EXECUTION_PLAN_APPROVED", "approval_statement_version": 1,
                "approval_statement": statement.strip(), "idempotency_key": _key("approve", execution_id),
            }
            _rpc(service, "approve_rollover_execution_plan_authenticated", request)
    else:
        st.info("Plan approval is not currently available.")

    st.markdown("#### 6 · Execute operations 1–31")
    operator_enabled = rollover_operator_ui_enabled(league_id)
    if status == "execution_ready" and approval and plan and matching_lock and operator_enabled:
        with st.form("rollover-execute", clear_on_submit=True):
            st.warning("Operations 1–31 mutate target-season football-domain state. They do not publish the target season.")
            st.json({"execution_id": execution_id, "source_season": execution.get("source_season"),
                     "target_season": target, "approval_fingerprint": approval.get("approval_fingerprint"),
                     "plan_fingerprint": plan.get("plan_fingerprint"), "operation_count": plan.get("operation_count"),
                     "cutover_lock_id": matching_lock.get("id")})
            ok, error = _protected_form("rollover-execute", target,
                f"EXECUTE {execution.get('source_season')} TO {target}", execution_id)
        if error:
            st.error(error)
        elif ok:
            try: service.execute_current_plan(execution_id, approval, plan)
            except RolloverControlError as exc: st.error(str(exc))
            else:
                after = service.load_state(); completed_after = {int(x.get("operation_index") or 0) for x in after.get("operation_results", ())}
                current = after.get("execution") or {}
                after_seasons = list(after.get("seasons") or ())
                source_authority = next((x for x in after_seasons if int(x.get("season") or 0) == int(execution["source_season"])), {})
                target_authority = next((x for x in after_seasons if int(x.get("season") or 0) == target), {})
                lock_after = matching_active_cutover_lock(list(after.get("locks") or ()), approval, plan)
                postconditions_met = (
                    current.get("status") == "executed_unpublished"
                    and len(completed_after.intersection(range(1, 32))) == 31
                    and source_authority.get("status") == "active" and bool(source_authority.get("is_active"))
                    and target_authority.get("status") == "scheduled" and not bool(target_authority.get("is_active"))
                    and lock_after is not None
                )
                if not postconditions_met:
                    st.error("Execution returned without the certified 31-operation postcondition. Publication remains unavailable.")
                else: _verified_success("Canonical state confirms operations 1–31 completed in executed-unpublished state.")
    else:
        prerequisites = {
            "operator_ui_enabled": operator_enabled,
            "execution_status_ready": status == "execution_ready",
            "current_plan": bool(plan), "current_approval": bool(approval),
            "matching_active_cutover_lock": bool(matching_lock),
        }
        st.info("Execution is disabled until every certified prerequisite and the explicit disposable/test feature gate are active.")
        st.json(prerequisites)

    st.markdown("#### 7–8 · Validation and publication")
    st.json({
        "execution_state": status,
        "execution_complete_unpublished": status == "executed_unpublished",
        "publication_ui_authorized": production_operator_ui_enabled(league_id) or execution_ui_enabled(),
        "season_authority_published": bool(state.get("season_publications")),
        "cap_authority_published": bool(state.get("cap_publications")),
        "market_visibility_published": bool(state.get("market_publications")),
        "cutover_released": bool(state.get("cutover_releases")),
        "published_context_refreshed": bool(state.get("context_generations")),
    })
    checks = list(state.get("validation_checks") or ())
    if checks:
        st.markdown("##### Canonical post-execution validation")
        st.dataframe([{k: row.get(k) for k in ("validation_code", "domain", "status", "severity", "blockers", "warnings")}
                      for row in checks], use_container_width=True)
    reports = list(state.get("validation_reports") or ())
    publication_eligible = bool(reports and reports[-1].get("publication_eligible")
                                and int(reports[-1].get("publication_blocker_count") or 0) == 0
                                and int(reports[-1].get("execution_failure_count") or 0) == 0)
    if operator_enabled and status in {"executed_unpublished", "completed"}:
        publication_counts = [len(state.get(key) or ()) for key in
                              ("season_publications", "cap_publications", "market_publications", "cutover_releases", "context_generations")]
        next_operation = next((32 + i for i, count in enumerate(publication_counts) if count == 0), None)
        if next_operation and (publication_eligible or next_operation > 32):
            code = {32:"PUBLISH_TARGET_SEASON_AUTHORITY",33:"ACTIVATE_TARGET_CAP_AUTHORITY",
                    34:"ENABLE_TARGET_FREE_AGENT_VISIBILITY",35:"RELEASE_CUTOVER_RESTRICTIONS",
                    36:"REFRESH_PUBLISHED_UI_AND_AI_CONTEXT"}[next_operation]
            phrase = f"{code} {target}"
            with st.form(f"rollover-publish-{next_operation}", clear_on_submit=True):
                st.warning(f"Operation {next_operation} is a committed publication boundary. Only this next legal stage will run.")
                ok, error = _protected_form(f"rollover-publish-{next_operation}", target, phrase, execution_id)
            if error: st.error(error)
            elif ok:
                try: service.publish_next_operation(execution_id, next_operation, state)
                except RolloverControlError as exc: st.error(str(exc))
                else: _verified_success(f"Canonical state confirms publication operation {next_operation}.")
        elif not publication_eligible and not state.get("season_publications"):
            st.error("Publication is blocked by canonical validation evidence.")

    st.caption(f"State loaded at {datetime.now(timezone.utc).isoformat()}. No external synchronization is performed by this page.")
