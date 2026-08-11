#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from season_engine.history.sleeper_source import DeterministicHistorySource
from services.season_rollover_control import SeasonRolloverControlService
from tests.fixtures.season_rollover_domain_factory import (
    LIFECYCLE_TABLE_DENYLIST, SeasonRolloverDomainFactory,
)
from tests.season_rollover_hosted_integration.psql_client import PsqlSession


def require(condition: bool, message: str):
    if not condition: raise AssertionError(message)


def sentinel(session: PsqlSession) -> str:
    rows = session.command("select count(*)::text||':'||count(*) filter(where singleton and environment_name='phase3b5h-testing' and environment_type='disposable_test' and parent_project='Legacy-Dynasty')::text from public.environment_identity")
    return rows[-1]


def scoped_count(session: PsqlSession, table: str, league_id: str) -> int:
    name = table.split(".", 1)[1]
    league = "'" + league_id.replace("'", "''") + "'"
    value = session.json_query(
        f"select to_jsonb(count(*)) from public.{name} t where "
        f"(to_jsonb(t)->>'league_id'={league}) or "
        f"(to_jsonb(t)->>'rollover_execution_id' in (select id::text from public.rollover_executions where league_id={league}))"
    )
    return int(value)


def assert_lifecycle_empty(session: PsqlSession, league_id: str):
    league = "'" + league_id.replace("'", "''") + "'"
    parts = []
    for table in sorted(LIFECYCLE_TABLE_DENYLIST):
        name = table.split(".", 1)[1]
        parts.append(f"select '{table}' table_name,count(*) n from public.{name} t where "
                     f"to_jsonb(t)->>'league_id'={league} or to_jsonb(t)->>'rollover_execution_id' in "
                     f"(select id::text from public.rollover_executions where league_id={league})")
    rows = session.json_query("select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) from (" + " union all ".join(parts) + ") q")
    nonzero = {row["table_name"]: int(row["n"]) for row in rows if int(row["n"])}
    history = int(session.json_query(
        "select to_jsonb(count(*)) from public.historical_capture_executions h join public.league_seasons s on s.id=h.league_season_id "
        f"where s.league_id='{league_id}'"))
    if history: nonzero["public.historical_capture_executions"] = history
    require(not nonzero, "bootstrap seeded lifecycle rows: " + json.dumps(nonzero, sort_keys=True))


def main() -> int:
    factory = SeasonRolloverDomainFactory(os.getenv("ROLLOVER_FIXTURE_LABEL", "unified-hosted-v1"))
    ids = factory.identity
    commissioner_db = PsqlSession(
        ids.commissioner_id,
        "authenticated",
    )
    # Trusted dry-run and planner persistence require a real service-role
    # database context. User authorization remains on commissioner_db.
    service_db = PsqlSession(
        None,
        "service_role",
    )
    try:
        print(json.dumps({"stage": "connected"}), flush=True)
        require(sentinel(service_db) == "1:1", "initial sentinel mismatch")
        print(json.dumps({"stage": "sentinel_verified"}), flush=True)
        service_db.execute_script(factory.bootstrap_sql())
        print(json.dumps({"stage": "domain_bootstrapped"}), flush=True)
        assert_lifecycle_empty(service_db, ids.league_id)
        print(json.dumps({"stage": "denylist_empty"}), flush=True)

        source = DeterministicHistorySource(factory.history_source(), disposable=True)
        control = SeasonRolloverControlService(
            commissioner_db, ids.league_id,
            service_client_factory=lambda: service_db,
            history_source_factory=lambda: source,
        )
        require(control.load_state().get("execution") is None, "fresh lifecycle is not Not started")
        require(control.authorize() == ids.commissioner_id, "canonical commissioner not authorized")
        readiness = control.load_initiation_readiness()
        require(readiness.policy_status == "required" and readiness.history_status == "required", "unexpected initial readiness")
        policy = control.approve_canonical_seven_day_policy()
        print(json.dumps({"stage": "policy_approved"}), flush=True)
        require(policy.status == "approved", "policy approval failed")
        history = control.capture_immutable_history()
        print(json.dumps({"stage": "history_captured"}), flush=True)
        require(history.status == "validated", "history capture failed")
        created = control.run_preflight_and_create_execution()
        print(json.dumps({"stage": "execution_created"}), flush=True)
        execution = dict(created["execution"])
        require(execution.get("status") == "preflight_ready", "execution was not created preflight-ready")
        preflight = dict((execution.get("metadata") or {}).get("canonical_preflight") or {})
        require(len(preflight.get("owner_population") or ()) == 108, "owner population is not canonical 108")
        require(len(preflight.get("commissioner_population") or ()) == 13, "commissioner population is not canonical 13")
        require(not service_db.table("contract_transition_executions").select("id").eq("league_id", ids.league_id).execute().data,
                "legacy contract transition execution was created before operations 1-31")
        require(not service_db.table("rollover_execution_operation_results").select("id").eq("rollover_execution_id", execution["id"]).execute().data,
                "operation evidence exists before dispatcher execution")
        require(not service_db.table("rollover_target_season_authority_publications").select("id").eq("rollover_execution_id", execution["id"]).execute().data,
                "publication evidence exists before execution")
        target_rows = service_db.table("contract_seasons").select("obligation_status").eq("league_id", ids.league_id).eq("season", 2026).execute().data
        require(not any(row.get("obligation_status") == "active" for row in target_rows), "target contracts activated during preflight")

        # ------------------------------------------------------------
        # Owner-option window: open, reject early close, advance the
        # disposable-only clock exactly seven days, then close.
        # ------------------------------------------------------------
        notice_at = datetime.now(timezone.utc)
        owner_population = list(preflight.get("owner_population") or ())
        owner_population_fingerprint = str(preflight["owner_population_fingerprint"])

        control.authenticated_rpc(
            "open_rollover_notice_window_authenticated",
            {
                "rollover_execution_id": execution["id"],
                "official_notice_timestamp": notice_at.isoformat(),
                "expected_preflight_fingerprint": execution["preflight_fingerprint"],
                "expected_owner_population_fingerprint": owner_population_fingerprint,
                "calculated_owner_population_fingerprint": owner_population_fingerprint,
                "expected_owner_count": len(owner_population),
                "owner_population": owner_population,
                "idempotency_key": f"open-window:{execution['id']}",
            },
        )
        print(json.dumps({"stage": "owner_window_opened"}), flush=True)

        state = control.load_state()
        execution = dict(state["execution"])
        require(
            execution.get("status") in {"notice_open", "decision_window_open"},
            "owner-option window did not enter an open state",
        )

        deadline_text = (
            execution.get("owner_option_deadline")
            or execution.get("owner_deadline")
        )
        require(bool(deadline_text), "owner-option deadline was not persisted")
        deadline = datetime.fromisoformat(str(deadline_text).replace("Z", "+00:00"))

        expected_deadline = notice_at + timedelta(days=7)
        require(
            abs((deadline - expected_deadline).total_seconds()) < 2,
            "owner-option deadline is not exactly seven days after notice",
        )

        early_close_rejected = False
        try:
            control.authenticated_rpc(
                "close_rollover_decision_window_authenticated",
                {
                    "rollover_execution_id": execution["id"],
                    "effective_close_timestamp": datetime.now(timezone.utc).isoformat(),
                    "expected_population_fingerprint": execution["decision_population_fingerprint"],
                    "idempotency_key": f"close-window-early:{execution['id']}",
                },
            )
        except Exception:
            early_close_rejected = True

        require(early_close_rejected, "owner-option window closed before its deadline")
        print(json.dumps({"stage": "early_close_rejected"}), flush=True)

        deadline_sql = deadline.isoformat().replace("'", "''")
        service_db.command(
            f"select public.set_rollover_disposable_clock_private('{deadline_sql}'::timestamptz)"
        )
        print(json.dumps({"stage": "disposable_clock_advanced"}), flush=True)

        control.authenticated_rpc(
            "close_rollover_decision_window_authenticated",
            {
                "rollover_execution_id": execution["id"],
                "effective_close_timestamp": deadline.isoformat(),
                "expected_population_fingerprint": execution["decision_population_fingerprint"],
                "idempotency_key": f"close-window:{execution['id']}",
            },
        )

        # Keep the disposable clock pinned at the certified deadline
        # throughout planning, approval, execution, and validation.
        # The outer cleanup resets it after the complete hosted run.
        state = control.load_state()
        execution = dict(state["execution"])
        require(
            execution.get("status") == "decision_window_closed",
            "owner-option window closure was not confirmed",
        )
        clock_rows = (
            service_db.table("rollover_disposable_clock_override")
            .select("effective_now")
            .execute()
            .data
        )
        require(
            len(clock_rows) == 1,
            "disposable clock override was lost before execution",
        )
        print(json.dumps({"stage": "owner_window_closed"}), flush=True)

        # ------------------------------------------------------------
        # Commissioner reviews: initialize the exact canonical
        # population created during preflight.
        # ------------------------------------------------------------
        control.initialize_canonical_reviews(execution["id"])

        state = control.load_state()
        execution = dict(state["execution"])
        commissioner_reviews = list(
            state.get("commissioner_reviews") or ()
        )

        require(
            execution.get("status") == "decision_window_closed",
            "review initialization changed the execution incorrectly",
        )
        require(
            len(commissioner_reviews) == 13,
            "canonical commissioner-review population is not 13",
        )
        require(
            all(
                str(row.get("rollover_execution_id")) == execution["id"]
                for row in commissioner_reviews
            ),
            "commissioner review escaped the active execution boundary",
        )

        review_summary = {}
        for row in commissioner_reviews:
            key = (
                str(row.get("review_type")),
                str(row.get("review_state")),
            )
            review_summary[key] = review_summary.get(key, 0) + 1

        print(
            json.dumps(
                {
                    "stage": "commissioner_reviews_initialized",
                    "review_count": len(commissioner_reviews),
                    "review_summary": {
                        f"{kind}:{status}": count
                        for (kind, status), count
                        in sorted(review_summary.items())
                    },
                },
                sort_keys=True,
            ),
            flush=True,
        )

        # ------------------------------------------------------------
        # Complete each commissioner review through the same hosted
        # begin/submit boundaries used by the Control Center.
        # ------------------------------------------------------------
        deterministic_outcomes = {
            "active_off_roster_liability": "preserve_active_liability",
            "expired_unrostered_publication_candidate": "reject_publication",
        }

        for original_review in commissioner_reviews:
            review_type = str(original_review.get("review_type") or "")
            outcome = deterministic_outcomes.get(review_type)

            require(
                outcome is not None,
                f"no deterministic certification outcome for {review_type}",
            )
            require(
                outcome in control.allowed_review_outcomes(original_review),
                f"outcome {outcome} is not allowed for {review_type}",
            )

            control.begin_canonical_review(original_review)

            refreshed = control.load_state()
            current_review = next(
                (
                    row
                    for row in refreshed.get("commissioner_reviews") or ()
                    if str(row.get("id")) == str(original_review["id"])
                ),
                None,
            )

            require(
                current_review is not None,
                "review disappeared after begin transition",
            )
            require(
                current_review.get("review_state") == "under_review",
                "review did not enter under_review",
            )

            control.submit_canonical_review(
                current_review,
                outcome,
                f"Deterministic disposable certification outcome: {outcome}",
                dict(current_review.get("evidence") or {}),
            )

            confirmed = control.load_state()
            finalized = next(
                (
                    row
                    for row in confirmed.get("commissioner_reviews") or ()
                    if str(row.get("id")) == str(original_review["id"])
                ),
                None,
            )

            require(
                finalized is not None,
                "review disappeared after submit transition",
            )
            require(
                finalized.get("review_state") in {"approved", "rejected"},
                (
                    "review did not reach a non-blocking terminal state: "
                    f"{finalized.get('review_state')}"
                ),
            )
            require(
                finalized.get("outcome") == outcome,
                "persisted review outcome differs from submitted outcome",
            )

        state = control.load_state()
        commissioner_reviews = list(
            state.get("commissioner_reviews") or ()
        )

        require(
            len(commissioner_reviews) == 13,
            "commissioner-review count changed during finalization",
        )
        require(
            all(
                row.get("review_state") in {"approved", "rejected"}
                for row in commissioner_reviews
            ),
            "one or more commissioner reviews remain incomplete",
        )

        review_readiness = control.review_readiness(execution["id"])
        require(
            review_readiness.get("status")
            == "authority_preparation_required",
            (
                "authority preparation did not become available: "
                f"{dict(review_readiness)}"
            ),
        )

        terminal_summary = {}
        for row in commissioner_reviews:
            key = (
                str(row.get("review_type")),
                str(row.get("outcome")),
                str(row.get("review_state")),
            )
            terminal_summary[key] = terminal_summary.get(key, 0) + 1

        print(
            json.dumps(
                {
                    "stage": "commissioner_reviews_completed",
                    "review_count": len(commissioner_reviews),
                    "readiness": review_readiness.get("status"),
                    "terminal_summary": {
                        f"{kind}:{outcome}:{status}": count
                        for (kind, outcome, status), count
                        in sorted(terminal_summary.items())
                    },
                },
                sort_keys=True,
            ),
            flush=True,
        )

        # ------------------------------------------------------------
        # Prepare all three canonical authority domains through the
        # hosted Control Center boundary.
        # ------------------------------------------------------------
        authority_result = control.prepare_canonical_authorities(
            execution["id"]
        )

        require(
            authority_result.status == "prepared",
            (
                "canonical authority preparation did not complete: "
                f"{authority_result.status}; "
                f"blockers={authority_result.blockers}"
            ),
        )
        require(
            not authority_result.blockers,
            (
                "canonical authority preparation returned blockers: "
                f"{authority_result.blockers}"
            ),
        )

        state = control.load_state()
        execution = dict(state["execution"])
        preparations = list(state.get("preparations") or ())

        require(
            execution.get("status") == "authority_ready",
            (
                "execution did not advance to authority_ready: "
                f"{execution.get('status')}"
            ),
        )
        require(
            len(preparations) == 3,
            (
                "expected exactly three canonical authority preparations, "
                f"found {len(preparations)}"
            ),
        )

        preparation_by_type = {
            str(row.get("authority_type")): row
            for row in preparations
        }

        require(
            set(preparation_by_type)
            == {"publication", "dead_cap", "salary_cap"},
            (
                "authority preparation types are incomplete: "
                f"{sorted(preparation_by_type)}"
            ),
        )
        require(
            all(
                row.get("authority_status") == "prepared"
                for row in preparations
            ),
            "one or more canonical authority domains are not prepared",
        )
        require(
            all(not (row.get("blockers") or ()) for row in preparations),
            "one or more canonical authority domains contain blockers",
        )

        print(
            json.dumps(
                {
                    "stage": "authorities_prepared",
                    "execution_status": execution.get("status"),
                    "authority_count": len(preparations),
                    "authority_statuses": {
                        authority_type: row.get("authority_status")
                        for authority_type, row
                        in sorted(preparation_by_type.items())
                    },
                },
                sort_keys=True,
            ),
            flush=True,
        )

        # ------------------------------------------------------------
        # Generate the canonical dry run through the trusted simulator
        # boundary. This must not execute or publish any operation.
        # ------------------------------------------------------------
        control.generate_canonical_dry_run(
            execution["id"],
            {
                "expected_execution_status": "authority_ready",
                "expected_policy_id": execution["policy_id"],
                "expected_policy_fingerprint":
                    execution["policy_fingerprint"],
                "expected_preflight_fingerprint":
                    execution["preflight_fingerprint"],
                "expected_owner_population_fingerprint":
                    execution["decision_population_fingerprint"],
                "simulator_version": "rollover-dry-run-v1",
                "idempotency_key":
                    f"dry-run:{execution['id']}",
                "material_metadata": {
                    "source": "unified_hosted_certification",
                },
            },
        )

        state = control.load_state()
        execution = dict(state["execution"])
        simulations = list(state.get("simulations") or ())

        require(
            len(simulations) == 1,
            (
                "expected exactly one canonical simulation, "
                f"found {len(simulations)}"
            ),
        )

        simulation = dict(simulations[0])

        require(
            simulation.get("simulation_status")
            in {"valid", "completed", "ready"},
            (
                "canonical dry run is not valid: "
                f"{simulation.get('simulation_status')}"
            ),
        )
        require(
            not (simulation.get("blockers") or ()),
            (
                "canonical dry run contains blockers: "
                f"{simulation.get('blockers')}"
            ),
        )
        require(
            str(simulation.get("rollover_execution_id"))
            == execution["id"],
            "simulation escaped the active execution boundary",
        )

        require(
            not service_db.table(
                "rollover_execution_operation_results"
            )
            .select("id")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data,
            "operation evidence exists after dry run",
        )

        print(
            json.dumps(
                {
                    "stage": "canonical_dry_run_generated",
                    "simulation_id": simulation.get("id"),
                    "simulation_status":
                        simulation.get("simulation_status"),
                    "blocker_count":
                        len(simulation.get("blockers") or ()),
                    "warning_count":
                        len(simulation.get("warnings") or ()),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        # ------------------------------------------------------------
        # Generate the canonical execution plan. The planner must emit
        # exactly the 31 execution-owned registry operations.
        # ------------------------------------------------------------
        control.generate_canonical_execution_plan(
            execution["id"],
            str(simulation["id"]),
            {
                "idempotency_key":
                    f"plan:{execution['id']}",
                "material_metadata": {
                    "source": "unified_hosted_certification",
                },
            },
        )

        state = control.load_state()
        execution = dict(state["execution"])
        plans = list(state.get("plans") or ())

        require(
            len(plans) == 1,
            (
                "expected exactly one canonical execution plan, "
                f"found {len(plans)}"
            ),
        )

        plan = dict(plans[0])

        require(
            plan.get("plan_status") == "valid",
            (
                "canonical execution plan is not valid: "
                f"{plan.get('plan_status')}"
            ),
        )
        require(
            int(plan.get("operation_count") or 0) == 31,
            (
                "canonical execution plan does not contain exactly "
                f"31 operations: {plan.get('operation_count')}"
            ),
        )
        require(
            bool(plan.get("executable")),
            "canonical execution plan is not executable",
        )
        require(
            not bool(plan.get("approved_for_execution")),
            "plan was approved during generation",
        )
        require(
            not (plan.get("blockers") or ()),
            (
                "canonical execution plan contains blockers: "
                f"{plan.get('blockers')}"
            ),
        )

        ordered_operations = list(
            plan.get("ordered_operations") or ()
        )

        require(
            len(ordered_operations) == 31,
            (
                "ordered-operation material does not contain "
                f"31 entries: {len(ordered_operations)}"
            ),
        )

        require(
            not service_db.table(
                "rollover_execution_operation_results"
            )
            .select("id")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data,
            "operation evidence exists after plan generation",
        )
        require(
            not service_db.table(
                "rollover_execution_plan_approvals"
            )
            .select("id")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data,
            "approval evidence exists before plan approval",
        )

        print(
            json.dumps(
                {
                    "stage": "canonical_execution_plan_generated",
                    "execution_status": execution.get("status"),
                    "plan_id": plan.get("id"),
                    "plan_status": plan.get("plan_status"),
                    "operation_count": plan.get("operation_count"),
                    "executable": bool(plan.get("executable")),
                    "approved_for_execution":
                        bool(plan.get("approved_for_execution")),
                    "blocker_count":
                        len(plan.get("blockers") or ()),
                    "warning_count":
                        len(plan.get("warnings") or ()),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        approval_request = {
            "rollover_execution_id": execution["id"],
            "league_id": ids.league_id,
            "source_season": int(plan["source_season"]),
            "target_season": int(plan["target_season"]),
            "execution_plan_id": plan["id"],
            "execution_plan_version": int(plan["plan_version"]),
            "simulation_id": plan["simulation_id"],
            "simulation_version": int(plan["simulation_version"]),
            "expected_execution_status": "authority_ready",
            "expected_plan_status": "valid",
            "expected_plan_input_fingerprint":
                plan["plan_input_fingerprint"],
            "expected_plan_fingerprint":
                plan["plan_fingerprint"],
            "expected_simulation_input_fingerprint":
                plan["simulation_input_fingerprint"],
            "expected_simulation_result_fingerprint":
                plan["simulation_result_fingerprint"],
            "expected_preflight_fingerprint":
                plan["preflight_fingerprint"],
            "expected_policy_fingerprint":
                plan["policy_fingerprint"],
            "expected_owner_population_fingerprint":
                plan["owner_population_fingerprint"],
            "expected_commissioner_population_fingerprint":
                plan["commissioner_population_fingerprint"],
            "expected_authority_preparation_fingerprint":
                plan["authority_preparation_fingerprint"],
            "expected_operation_count":
                int(plan["operation_count"]),
            "operation_fingerprints": [
                str(item["operation_fingerprint"])
                for item in ordered_operations
            ],
            "approval_statement_code":
                "ROLLOVER_EXECUTION_PLAN_APPROVED",
            "approval_statement_version": 1,
            "approval_statement":
                (
                    "Commissioner approves the immutable canonical "
                    "2025-to-2026 rollover execution plan for "
                    "disposable hosted certification."
                ),
            "approval_version": 1,
            "idempotency_key":
                f"hosted-plan-approval:{plan['id']}:v1",
            "material_metadata": {
                "source": "unified_hosted_rollover_certification",
                "fixture_version": ids.version,
            },
        }

        approval_response = (
            commissioner_db.rpc(
                "approve_rollover_execution_plan_authenticated",
                {"p_request": approval_request},
            )
            .execute()
            .data
        )

        print("\nAPPROVAL RESPONSE")
        print(json.dumps(approval_response, indent=2, sort_keys=True))
        print()

        require(
            isinstance(approval_response, dict),
            "approval RPC returned malformed material",
        )
        require(
            approval_response.get("operations_executed") == 0,
            "plan approval executed rollover operations",
        )
        require(
            approval_response.get("plan_status")
                == "approved_for_execution",
            "approval response did not approve the plan",
        )
        require(
            approval_response.get("execution_status")
                == "execution_ready",
            "approval response did not make execution ready",
        )

        approval_rows = (
            commissioner_db.table(
                "rollover_execution_plan_approvals"
            )
            .select("*")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data
        )
        lock_rows = (
            commissioner_db.table("rollover_execution_locks")
            .select("*")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data
        )
        approved_plan_rows = (
            commissioner_db.table("rollover_execution_plans")
            .select("*")
            .eq("id", plan["id"])
            .execute()
            .data
        )
        approved_execution_rows = (
            commissioner_db.table("rollover_executions")
            .select("*")
            .eq("id", execution["id"])
            .execute()
            .data
        )

        require(
            len(approval_rows) == 1,
            f"expected one approval row, found {len(approval_rows)}",
        )
        require(
            len(lock_rows) == 1,
            f"expected one cutover lock, found {len(lock_rows)}",
        )
        require(
            len(approved_plan_rows) == 1,
            "approved execution plan row is missing",
        )
        require(
            len(approved_execution_rows) == 1,
            "approved rollover execution row is missing",
        )

        approval = dict(approval_rows[0])
        lock = dict(lock_rows[0])
        approved_plan = dict(approved_plan_rows[0])
        approved_execution = dict(approved_execution_rows[0])

        require(
            approval.get("approval_status") == "approved",
            "approval row is not approved",
        )
        require(
            int(approval.get("operation_count") or 0) == 31,
            "approval did not bind exactly 31 operations",
        )
        require(
            approval.get("approval_statement_code")
                == "ROLLOVER_EXECUTION_PLAN_APPROVED",
            "approval statement code is incorrect",
        )
        require(
            lock.get("lock_type") == "cutover",
            "approval did not create a cutover lock",
        )
        require(
            lock.get("status") == "active",
            "cutover lock is not active",
        )
        require(
            lock.get("approval_id") == approval.get("id"),
            "cutover lock is not bound to the approval",
        )
        require(
            approved_plan.get("plan_status")
                == "approved_for_execution",
            "persisted plan status was not approved",
        )
        require(
            bool(approved_plan.get("approved_for_execution")),
            "persisted plan approval flag is false",
        )
        require(
            approved_execution.get("status") == "execution_ready",
            "persisted execution status is not execution_ready",
        )

        operation_rows = (
            commissioner_db.table(
                "rollover_execution_operation_results"
            )
            .select("id")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data
        )
        publication_count = sum(
            len(
                commissioner_db.table(table)
                .select("id")
                .eq("rollover_execution_id", execution["id"])
                .execute()
                .data
            )
            for table in (
                "rollover_target_season_authority_publications",
                "rollover_target_cap_authority_publications",
                "rollover_target_market_visibility_publications",
                "rollover_cutover_release_publications",
            )
        )

        require(
            not operation_rows,
            "operation evidence exists immediately after approval",
        )
        require(
            publication_count == 0,
            "publication evidence exists immediately after approval",
        )

        print(
            json.dumps(
                {
                    "stage": "canonical_execution_plan_approved",
                    "execution_status":
                        approved_execution.get("status"),
                    "plan_status":
                        approved_plan.get("plan_status"),
                    "approval_count": len(approval_rows),
                    "approval_status":
                        approval.get("approval_status"),
                    "active_lock_count": sum(
                        1 for row in lock_rows
                        if row.get("status") == "active"
                    ),
                    "operations_executed":
                        approval_response.get("operations_executed"),
                    "operation_result_count":
                        len(operation_rows),
                    "publication_operation_count":
                        publication_count,
                },
                sort_keys=True,
            ),
            flush=True,
        )

        # ------------------------------------------------------------
        # Execute the approved immutable operations 1–31. This must
        # finish as executed_unpublished and must not run publication
        # operations 32–36 or release the active cutover lock.
        # ------------------------------------------------------------
        execution_response = (
            commissioner_db.rpc(
                "execute_rollover_plan_authenticated",
                {
                    "p_request": {
                        "rollover_execution_id": execution["id"],
                        "approval_id": approval["id"],
                        "execution_plan_id": plan["id"],
                        "expected_plan_fingerprint":
                            plan["plan_fingerprint"],
                        "expected_plan_version":
                            int(plan["plan_version"]),
                        "expected_execution_status":
                            "execution_ready",
                        "expected_approval_status":
                            "approved",
                        "idempotency_key":
                            f"hosted-execute:{execution['id']}:v1",
                        "material_metadata": {
                            "source":
                                "unified_hosted_rollover_certification",
                            "fixture_version": ids.version,
                        },
                    }
                },
            )
            .execute()
            .data
        )

        print("\nEXECUTION RESPONSE")
        print(json.dumps(execution_response, indent=2, sort_keys=True))
        print()

        require(
            isinstance(execution_response, dict),
            "execution RPC returned malformed material",
        )

        executed_rows = (
            commissioner_db.table("rollover_executions")
            .select("*")
            .eq("id", execution["id"])
            .execute()
            .data
        )
        require(
            len(executed_rows) == 1,
            "executed rollover row is missing",
        )
        executed = dict(executed_rows[0])

        require(
            executed.get("status") == "executed_unpublished",
            (
                "execution did not finish executed_unpublished: "
                f"{executed.get('status')}"
            ),
        )
        require(
            executed.get("executed_unpublished_at") is not None,
            "executed-unpublished timestamp is missing",
        )
        require(
            executed.get("post_validation_report_id") is not None,
            "post-execution validation report reference is missing",
        )
        require(
            executed.get("prepared_artifact_aggregate_hash"),
            "prepared artifact aggregate hash is missing",
        )

        operation_results = (
            commissioner_db.table(
                "rollover_execution_operation_results"
            )
            .select("*")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data
        )

        require(
            len(operation_results) == 31,
            (
                "expected exactly 31 execution-operation results, "
                f"found {len(operation_results)}"
            ),
        )

        operation_indexes = sorted(
            int(row["operation_index"])
            for row in operation_results
        )
        require(
            operation_indexes == list(range(1, 32)),
            (
                "operation indexes are not exactly 1–31: "
                f"{operation_indexes}"
            ),
        )

        incomplete_operations = [
            {
                "index": row.get("operation_index"),
                "code":
                    row.get("operation_code")
                    or row.get("operation_type"),
                "status": row.get("operation_status"),
            }
            for row in operation_results
            if row.get("operation_status") != "completed"
        ]
        require(
            not incomplete_operations,
            (
                "one or more execution operations did not complete: "
                f"{incomplete_operations}"
            ),
        )

        operation_15 = next(
            row for row in operation_results
            if int(row["operation_index"]) == 15
        )
        operation_15_result = dict(
            (operation_15.get("result_payload") or {}).get("result") or {}
        )
        require(
            int(operation_15_result.get(
                "preserved_off_roster_liability_count", -1
            )) == 2,
            "Operation 15 did not certify two intentional off-roster liabilities",
        )
        preserved_players = list(ids.commissioner_player_ids[-2:])
        preserved_target_assignments = (
            service_db.table("season_roster_assignments")
            .select("sleeper_player_id")
            .eq("league_season_id", ids.target_season_id)
            .in_("sleeper_player_id", preserved_players)
            .execute()
            .data
        )
        require(
            not preserved_target_assignments,
            "preserved off-roster liability was fabricated onto target roster",
        )
        preserved_agreements = (
            service_db.table("contract_agreements")
            .select("player_id,status")
            .eq("league_id", ids.league_id)
            .in_("player_id", preserved_players)
            .execute()
            .data
        )
        require(
            len(preserved_agreements) == 2
            and all(row.get("status") == "active" for row in preserved_agreements),
            "preserved off-roster contract liability was not retained",
        )
        preserved_obligations = (
            service_db.table("contract_seasons")
            .select("player_id,obligation_status,cap_hit")
            .eq("league_season_id", ids.target_season_id)
            .in_("player_id", preserved_players)
            .execute()
            .data
        )
        require(
            len(preserved_obligations) == 2
            and all(
                row.get("obligation_status") in {"active", "scheduled"}
                and float(row.get("cap_hit") or 0) > 0
                for row in preserved_obligations
            ),
            "preserved off-roster target contract/cap liability is incomplete",
        )
        preserved_team_ids = [ids.team_ids[9], ids.team_ids[0]]
        preserved_cap_rows = (
            service_db.table("prepared_team_caps")
            .select("league_team_id,active_target_salary,active_contract_count")
            .eq("league_id", ids.league_id)
            .in_("league_team_id", preserved_team_ids)
            .execute()
            .data
        )
        require(
            len(preserved_cap_rows) == 2
            and all(
                float(row.get("active_target_salary") or 0) >= 10
                and int(row.get("active_contract_count") or 0) >= 1
                for row in preserved_cap_rows
            ),
            "preserved off-roster liability was omitted from prepared team caps",
        )

        validation_reports = (
            commissioner_db.table(
                "rollover_post_execution_validation_reports"
            )
            .select("*")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data
        )
        require(
            len(validation_reports) == 1,
            (
                "expected one post-execution validation report, "
                f"found {len(validation_reports)}"
            ),
        )
        validation_report = dict(validation_reports[0])

        require(
            validation_report.get("execution_failure_count") == 0,
            "post-execution validation recorded execution failures",
        )
        require(
            validation_report.get("expected_check_count")
                == validation_report.get("actual_check_count"),
            "post-execution validation check count is incomplete",
        )

        validation_checks = (
            commissioner_db.table(
                "rollover_post_execution_validation_checks"
            )
            .select("*")
            .eq("report_id", validation_report["id"])
            .execute()
            .data
        )
        require(
            len(validation_checks)
                == int(validation_report["expected_check_count"]),
            "post-execution validation check rows are incomplete",
        )

        finalizations = (
            commissioner_db.table(
                "rollover_executed_unpublished_finalizations"
            )
            .select("*")
            .eq("rollover_execution_id", execution["id"])
            .execute()
            .data
        )
        require(
            len(finalizations) == 1,
            (
                "expected one executed-unpublished finalization, "
                f"found {len(finalizations)}"
            ),
        )
        finalization = dict(finalizations[0])

        require(
            finalization.get("finalization_status")
                == "executed_unpublished",
            "finalization status is incorrect",
        )
        require(
            int(finalization.get("completed_operation_count") or 0)
                == 30,
            (
                "finalization did not bind operations 1–30 before "
                "operation 31"
            ),
        )
        require(
            finalization.get("validation_report_id")
                == validation_report.get("id"),
            "finalization is not bound to the validation report",
        )
        require(
            finalization.get("prepared_artifact_aggregate_hash")
                == executed.get("prepared_artifact_aggregate_hash"),
            "execution and finalization artifact hashes differ",
        )

        retained_approval_rows = (
            commissioner_db.table(
                "rollover_execution_plan_approvals"
            )
            .select("*")
            .eq("id", approval["id"])
            .execute()
            .data
        )
        retained_lock_rows = (
            commissioner_db.table("rollover_execution_locks")
            .select("*")
            .eq("id", lock["id"])
            .execute()
            .data
        )
        retained_plan_rows = (
            commissioner_db.table("rollover_execution_plans")
            .select("*")
            .eq("id", plan["id"])
            .execute()
            .data
        )

        require(
            len(retained_approval_rows) == 1
            and retained_approval_rows[0].get("approval_status")
                == "approved",
            "execution did not retain the approved plan approval",
        )
        require(
            len(retained_lock_rows) == 1
            and retained_lock_rows[0].get("status") == "active",
            "execution did not retain the active cutover lock",
        )
        require(
            len(retained_plan_rows) == 1
            and retained_plan_rows[0].get("plan_status")
                == "approved_for_execution"
            and bool(
                retained_plan_rows[0].get("approved_for_execution")
            ),
            "execution changed the approved execution plan",
        )

        publication_tables = (
            "rollover_target_season_authority_publications",
            "rollover_target_cap_authority_publications",
            "rollover_target_market_visibility_publications",
            "rollover_cutover_release_publications",
            "publication_context_generations",
        )
        publication_counts = {}

        for table in publication_tables:
            rows = (
                commissioner_db.table(table)
                .select("id")
                .eq("rollover_execution_id", execution["id"])
                .execute()
                .data
            )
            publication_counts[table] = len(rows)

        require(
            sum(publication_counts.values()) == 0,
            (
                "publication evidence exists after operations 1–31: "
                f"{publication_counts}"
            ),
        )

        target_seasons = (
            commissioner_db.table("league_seasons")
            .select("*")
            .eq("league_id", ids.league_id)
            .eq("season", int(plan["target_season"]))
            .execute()
            .data
        )
        require(
            len(target_seasons) == 1,
            "target season authority row is missing",
        )
        require(
            target_seasons[0].get("status") == "scheduled"
            and not bool(target_seasons[0].get("is_active")),
            "target season was published during execution",
        )

        print(
            json.dumps(
                {
                    "stage":
                        "canonical_operations_1_31_executed",
                    "execution_status": executed.get("status"),
                    "operation_result_count":
                        len(operation_results),
                    "operation_indexes":
                        operation_indexes,
                    "validation_report_count":
                        len(validation_reports),
                    "validation_check_count":
                        len(validation_checks),
                    "execution_failure_count":
                        validation_report.get(
                            "execution_failure_count"
                        ),
                    "publication_eligible":
                        validation_report.get(
                            "publication_eligible"
                        ),
                    "publication_blocker_count":
                        validation_report.get(
                            "publication_blocker_count"
                        ),
                    "finalization_count":
                        len(finalizations),
                    "finalization_status":
                        finalization.get("finalization_status"),
                    "completed_operation_count":
                        finalization.get(
                            "completed_operation_count"
                        ),
                    "approval_status":
                        retained_approval_rows[0].get(
                            "approval_status"
                        ),
                    "lock_status":
                        retained_lock_rows[0].get("status"),
                    "plan_status":
                        retained_plan_rows[0].get("plan_status"),
                    "publication_counts":
                        publication_counts,
                    "target_season_status":
                        target_seasons[0].get("status"),
                    "target_season_active":
                        bool(target_seasons[0].get("is_active")),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        result = {
            "success": True, "stage": "executed_unpublished", "fixture_version": ids.version,
            "league_id": ids.league_id, "execution_id": execution["id"],
            "owner_population": 108, "commissioner_population": 13,
            "bootstrap_writes": sorted(factory.audit_bootstrap_sql(factory.bootstrap_sql())),
            "authenticated_rpcs": commissioner_db.invocations,
            "trusted_rpcs": service_db.invocations,
            "initial_sentinel": "1:1",
        }
        print(json.dumps(result, sort_keys=True))
        service_db.execute_script(factory.cleanup_sql())
        require(sentinel(service_db) == "1:1", "final sentinel mismatch")
        require(not service_db.table("leagues").select("id").eq("id", ids.league_id).execute().data, "synthetic cleanup failed")
        print(json.dumps({"stage": "cleanup_complete", "final_sentinel": "1:1"}), flush=True)
        return 0
    finally:
        try:
            service_db.close()
        finally:
            commissioner_db.close()


if __name__ == "__main__":
    raise SystemExit(main())
