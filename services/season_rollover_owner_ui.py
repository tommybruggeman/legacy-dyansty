from __future__ import annotations

"""Authenticated owner-only rollover decisions.

This boundary deliberately cannot dispatch commissioner RPCs and never accepts an
actor or team identifier from UI input. PostgreSQL re-resolves both from auth.uid().
"""

import hashlib
import json
from typing import Any, Mapping
from services.strict_pagination import PaginationIntegrityError, complete_rows


OWNER_ROLES = frozenset({"member"})
OWNER_DECISION_RPC = "submit_rollover_owner_decision_authenticated"
MUTABLE_DECISION_STATES = frozenset({"waiting_for_owner", "recontract_invalid"})


class OwnerRolloverDecisionError(RuntimeError):
    pass


def _rows(client: Any, table: str, **filters: Any) -> list[dict[str, Any]]:
    return complete_rows(client, table, filters=filters,
                         order_key="user_id" if table == "league_memberships" else "id")


def _actor(client: Any) -> str:
    try:
        user = getattr(client.auth.get_user(), "user", None)
        actor = str(getattr(user, "id", "") or "")
    except Exception:
        actor = ""
    if not actor:
        raise OwnerRolloverDecisionError("Authentication is required.")
    return actor


def _fingerprint(value: Mapping[str, Any]) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode()).hexdigest()


class OwnerRolloverDecisionService:
    def __init__(self, authenticated_client: Any, league_id: str):
        self.client = authenticated_client
        self.league_id = str(league_id or "").strip()

    def canonical_team_id(self) -> str:
        actor = _actor(self.client)
        try:
            memberships = _rows(self.client, "league_memberships", league_id=self.league_id, user_id=actor)
        except PaginationIntegrityError:
            raise OwnerRolloverDecisionError("Exactly one canonical member team membership is required.") from None
        eligible = [row for row in memberships if str(row.get("role") or "").lower() in OWNER_ROLES and row.get("league_team_id")]
        team_ids = {str(row["league_team_id"]) for row in eligible}
        if len(eligible) != 1 or len(team_ids) != 1:
            raise OwnerRolloverDecisionError("Exactly one canonical member team membership is required.")
        return next(iter(team_ids))

    def load(self) -> Mapping[str, Any]:
        team_id = self.canonical_team_id()
        executions = _rows(self.client, "rollover_executions", league_id=self.league_id)
        executions.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        execution = executions[0] if executions else None
        decisions = []
        if execution:
            decisions = _rows(
                self.client,
                "rollover_owner_decisions",
                rollover_execution_id=execution["id"],
                league_team_id=team_id,
            )
        return {"execution": execution, "team_id": team_id, "decisions": decisions}

    def submit(
        self,
        decision_id: str,
        choice: str,
        reason: str,
    ) -> Mapping[str, Any]:
        if choice not in {"recontract", "decline", "commissioner_review"}:
            raise OwnerRolloverDecisionError("Unsupported owner decision.")
        state = self.load()
        execution = state["execution"]
        if not execution or execution.get("status") != "decision_window_open":
            raise OwnerRolloverDecisionError("The owner decision window is not open.")
        matches = [row for row in state["decisions"] if str(row.get("id")) == str(decision_id)]
        if len(matches) != 1:
            raise OwnerRolloverDecisionError("The decision is not assigned to your canonical league team.")
        decision = matches[0]
        if decision.get("decision_status") not in MUTABLE_DECISION_STATES or decision.get("locked_at"):
            raise OwnerRolloverDecisionError("This decision is no longer mutable.")
        metadata = dict(decision.get("metadata") or {})
        revision = int(metadata.get("revision_number") or 1)
        current_fingerprint = str(metadata.get("decision_fingerprint") or "")
        if not current_fingerprint:
            raise OwnerRolloverDecisionError("Canonical decision evidence is incomplete.")
        new_fingerprint = _fingerprint({
            "decision_id": str(decision["id"]), "revision": revision + 1, "choice": choice,
            "reason": str(reason or "").strip(),
        })
        request = {
            "owner_decision_id": decision["id"],
            "choice": choice,
            "expected_revision_number": revision,
            "expected_decision_fingerprint": current_fingerprint,
            "decision_fingerprint": new_fingerprint,
            "reason": str(reason or "").strip(),
            "evidence": {"source": "owner_rollover_decision_ui"},
            "idempotency_key": f"owner-ui:{decision['id']}:{revision + 1}:{new_fingerprint}",
        }
        try:
            result = self.client.rpc(OWNER_DECISION_RPC, {"p_request": request}).execute().data
        except Exception:
            raise OwnerRolloverDecisionError("The owner decision was rejected. Reload and verify the current window state.") from None
        if not isinstance(result, Mapping):
            raise OwnerRolloverDecisionError("The owner decision returned an invalid response.")
        return dict(result)


def render_owner_rollover_decisions(authenticated_client: Any, league_id: str) -> None:
    import streamlit as st

    st.markdown("### Owner Rollover Decisions")
    st.caption("Decisions are scoped to your canonical league team and the seven-calendar-day owner window.")
    service = OwnerRolloverDecisionService(authenticated_client, league_id)
    try:
        state = service.load()
    except OwnerRolloverDecisionError as exc:
        st.info(str(exc))
        return
    execution = state["execution"]
    if not execution:
        st.info("No rollover decision window exists for this league.")
        return
    st.json({
        "source_season": execution.get("source_season"), "target_season": execution.get("target_season"),
        "window_status": execution.get("status"), "deadline": execution.get("owner_deadline"),
        "canonical_team_id": state["team_id"],
    })
    if execution.get("status") != "decision_window_open":
        st.info("The owner decision window is not currently open.")
    for decision in state["decisions"]:
        status = str(decision.get("decision_status") or "unknown")
        with st.expander(f"{decision.get('player_id')} · {status}", expanded=status in MUTABLE_DECISION_STATES):
            st.json({"player_id": decision.get("player_id"), "status": status,
                     "current_choice": decision.get("owner_choice"), "deadline": decision.get("deadline")})
            if execution.get("status") != "decision_window_open" or status not in MUTABLE_DECISION_STATES:
                continue
            with st.form(f"owner-rollover-decision-{decision['id']}", clear_on_submit=True):
                display_choice = st.selectbox("Decision", ["DROP / DECLINE", "EXTEND", "REQUEST COMMISSIONER REVIEW"])
                reason = st.text_area("Reason")
                submitted = st.form_submit_button("Submit owner decision", use_container_width=True)
            if submitted:
                choice = {"DROP / DECLINE": "decline", "EXTEND": "recontract",
                          "REQUEST COMMISSIONER REVIEW": "commissioner_review"}[display_choice]
                try:
                    service.submit(decision["id"], choice, reason)
                except OwnerRolloverDecisionError as exc:
                    st.error(str(exc))
                else:
                    st.success("Canonical state accepted the owner decision.")
                    st.rerun()
