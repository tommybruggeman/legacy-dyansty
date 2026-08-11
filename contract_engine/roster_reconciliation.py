from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .transition_planner import stable_fingerprint


DROPPED_BUT_STALE_CONTRACT = "DROPPED_BUT_STALE_CONTRACT"
DROPPED_WITH_VALID_CONTRACT_LIABILITY = "DROPPED_WITH_VALID_CONTRACT_LIABILITY"
TRADED_CONTRACT_MISMATCH = "TRADED_CONTRACT_MISMATCH"
CURRENT_ROSTER_CAPTURE_GAP = "CURRENT_ROSTER_CAPTURE_GAP"
NON_ROSTER_CONTRACT_RIGHT = "NON_ROSTER_CONTRACT_RIGHT"
NATURALLY_EXPIRED_OFF_ROSTER = "NATURALLY_EXPIRED_OFF_ROSTER"
AMBIGUOUS = "AMBIGUOUS"
EARLY_TERMINATION_MISSING_DEAD_CAP = "EARLY_TERMINATION_MISSING_DEAD_CAP"
EARLY_TERMINATION_NO_DEAD_CAP = "EARLY_TERMINATION_NO_DEAD_CAP"
VALID_FINAL_SEASON_OFF_ROSTER_LIABILITY = "VALID_FINAL_SEASON_OFF_ROSTER_LIABILITY"
NATURAL_EXPIRATION_PENDING = "NATURAL_EXPIRATION_PENDING"
STALE_CONTRACT_AFTER_COMPLETED_TERMINATION = "STALE_CONTRACT_AFTER_COMPLETED_TERMINATION"

READY_FOR_FREE_AGENT_STAGING = "READY_FOR_FREE_AGENT_STAGING"
READY_FOR_EXPIRATION_ONLY = "READY_FOR_EXPIRATION_ONLY"
REQUIRES_SOURCE_CORRECTION = "REQUIRES_SOURCE_CORRECTION"
REQUIRES_DEAD_CAP_REVIEW = "REQUIRES_DEAD_CAP_REVIEW"
REQUIRES_COMMISSIONER_DECISION = "REQUIRES_COMMISSIONER_DECISION"


@dataclass(frozen=True)
class MissingRosterEvidence:
    agreement: dict[str, Any]
    legacy_contract: dict[str, Any]
    source_obligation: dict[str, Any]
    contract_owner: dict[str, Any]
    captured_assignment: dict[str, Any] | None
    sleeper_roster_team: dict[str, Any] | None
    canonical_roster_team: dict[str, Any] | None
    canonical_player: dict[str, Any] | None
    latest_add: dict[str, Any] | None
    latest_drop: dict[str, Any] | None
    latest_trade: dict[str, Any] | None
    dead_cap: tuple[dict[str, Any], ...] = ()
    cap_adjustments: tuple[dict[str, Any], ...] = ()
    processed_drop: dict[str, Any] | None = None
    termination_event: dict[str, Any] | None = None
    future_obligations: tuple[dict[str, Any], ...] = ()
    non_roster_right_supported: bool = False


def classify_missing_roster_contract(evidence: MissingRosterEvidence) -> dict[str, Any]:
    agreement_team = str(evidence.agreement.get("league_team_id") or "")
    current_team = evidence.sleeper_roster_team or evidence.canonical_roster_team
    current_team_id = str((current_team or {}).get("id") or (current_team or {}).get("league_team_id") or "")
    dropped_team_id = str((evidence.latest_drop or {}).get("league_team_id") or "")
    final_year = int(evidence.legacy_contract.get("contract_years_left") or 0) == 1 and not evidence.future_obligations
    canonical_claims_owner = bool((evidence.canonical_player or {}).get("current_owner"))
    terminated = bool(evidence.processed_drop or evidence.termination_event or str(evidence.agreement.get("status") or "").lower() in {"terminated","released","voided"})

    if terminated and evidence.future_obligations and not evidence.dead_cap:
        classification, confidence = EARLY_TERMINATION_MISSING_DEAD_CAP, "HIGH"
        readiness = REQUIRES_DEAD_CAP_REVIEW
        reason = "Explicit termination occurred before scheduled future obligations ended, but no dead-cap evidence exists."
    elif terminated and not evidence.future_obligations and not evidence.dead_cap:
        classification, confidence = EARLY_TERMINATION_NO_DEAD_CAP, "HIGH"
        readiness = READY_FOR_FREE_AGENT_STAGING
        reason = "Explicit termination occurred with no contractual obligation remaining after termination."
    elif terminated and canonical_claims_owner:
        classification, confidence = STALE_CONTRACT_AFTER_COMPLETED_TERMINATION, "HIGH"
        readiness = REQUIRES_SOURCE_CORRECTION
        reason = "Explicit completed termination conflicts with continuing contract-derived ownership."
    elif current_team_id and current_team_id != agreement_team:
        classification, confidence = TRADED_CONTRACT_MISMATCH, "HIGH"
        readiness = REQUIRES_SOURCE_CORRECTION
        reason = "Current roster ownership conflicts with the agreement owner."
    elif current_team_id and not evidence.captured_assignment:
        classification, confidence = CURRENT_ROSTER_CAPTURE_GAP, "HIGH"
        readiness = READY_FOR_EXPIRATION_ONLY
        reason = "The player is currently on the agreement owner's roster but is absent from immutable history."
    elif evidence.dead_cap:
        classification, confidence = DROPPED_WITH_VALID_CONTRACT_LIABILITY, "HIGH"
        readiness = REQUIRES_DEAD_CAP_REVIEW
        reason = "A completed drop and explicit dead-cap evidence establish retained liability."
    elif evidence.latest_drop and str(evidence.latest_drop.get("status") or "").lower() == "complete" and dropped_team_id == agreement_team and canonical_claims_owner and final_year:
        classification, confidence = VALID_FINAL_SEASON_OFF_ROSTER_LIABILITY, "HIGH"
        readiness = READY_FOR_FREE_AGENT_STAGING
        reason = "The roster drop did not terminate the contract; the final 2025 obligation and ownership persisted with no future season obligation."
    elif evidence.non_roster_right_supported:
        classification, confidence = NON_ROSTER_CONTRACT_RIGHT, "HIGH"
        readiness = READY_FOR_EXPIRATION_ONLY
        reason = "An explicit league rule permits contract rights without a roster assignment."
    elif final_year and not terminated:
        classification, confidence = NATURAL_EXPIRATION_PENDING, "MEDIUM"
        readiness = READY_FOR_FREE_AGENT_STAGING
        reason = "The final 2025 obligation has no termination evidence and no future obligation; rollover should perform natural expiration."
    else:
        classification, confidence = AMBIGUOUS, "LOW"
        readiness = REQUIRES_COMMISSIONER_DECISION
        reason = "Available ID-based evidence does not establish one supported explanation."

    return {
        "agreement_id": str(evidence.agreement.get("id")),
        "legacy_contract_id": str(evidence.legacy_contract.get("id")),
        "player_id": str(evidence.agreement.get("player_id")),
        "sleeper_player_id": str(evidence.agreement.get("sleeper_player_id")),
        "player_name": evidence.legacy_contract.get("player_name"),
        "contract_owner": evidence.contract_owner.get("owner_name") or evidence.contract_owner.get("team_name"),
        "league_team_id": agreement_team,
        "salary_2025": str(evidence.source_obligation.get("salary")),
        "contract_type": evidence.agreement.get("contract_type"),
        "contract_years_remaining": evidence.legacy_contract.get("contract_years_left"),
        "last_known_roster_team": (evidence.latest_drop or evidence.latest_add or {}).get("owner_name"),
        "captured_historical_roster": evidence.captured_assignment,
        "active_sleeper_roster": evidence.sleeper_roster_team,
        "current_canonical_roster": evidence.canonical_roster_team,
        "latest_add": evidence.latest_add,
        "latest_drop": evidence.latest_drop,
        "latest_trade": evidence.latest_trade,
        "dead_cap_evidence": list(evidence.dead_cap),
        "cap_adjustment_evidence": list(evidence.cap_adjustments),
        "contract_expiration_season": evidence.agreement.get("end_season"),
        "future_obligations_after_drop": [dict(x) for x in evidence.future_obligations],
        "salary_liability_remaining_after_drop": str(evidence.source_obligation.get("salary")) if not terminated else "requires termination calculation",
        "salary_continued_to_count": not terminated,
        "contract_ownership_intended_to_persist": canonical_claims_owner and not terminated,
        "contract_terminated_at_sleeper_drop": terminated,
        "dead_cap_required": bool(terminated and evidence.future_obligations),
        "rollover_treatment": "natural expiration; no dead cap" if not terminated and final_year else "resolve termination evidence before rollover",
        "source_correction_required": readiness == REQUIRES_SOURCE_CORRECTION,
        "classification": classification,
        "confidence": confidence,
        "status": "BLOCKING_FOR_FREE_AGENT_STAGING" if readiness.startswith("REQUIRES_") else "NON_BLOCKING",
        "free_agent_readiness": readiness,
        "reason": reason,
        "recommended_phase_3b2_treatment": _treatment(readiness),
    }


def build_missing_roster_reconciliation(evidence_rows: list[MissingRosterEvidence], source_payloads: dict[str, Any]) -> dict[str, Any]:
    results = [classify_missing_roster_contract(row) for row in evidence_rows]
    return {
        "results": results,
        "counts": {name: sum(x["classification"] == name for x in results) for name in (
            DROPPED_BUT_STALE_CONTRACT, DROPPED_WITH_VALID_CONTRACT_LIABILITY, TRADED_CONTRACT_MISMATCH,
            CURRENT_ROSTER_CAPTURE_GAP, NON_ROSTER_CONTRACT_RIGHT, NATURALLY_EXPIRED_OFF_ROSTER, AMBIGUOUS,
            EARLY_TERMINATION_MISSING_DEAD_CAP, EARLY_TERMINATION_NO_DEAD_CAP, VALID_FINAL_SEASON_OFF_ROSTER_LIABILITY,
            NATURAL_EXPIRATION_PENDING, STALE_CONTRACT_AFTER_COMPLETED_TERMINATION)},
        "ready_for_phase_3b2": not any(x["status"].startswith("BLOCKING") for x in results),
        "source_fingerprint": stable_fingerprint(source_payloads),
        "writes_performed": 0,
    }


def _treatment(readiness: str) -> str:
    return {
        READY_FOR_FREE_AGENT_STAGING: "Stage only after the contract transition is atomically approved.",
        READY_FOR_EXPIRATION_ONLY: "Expire the agreement only; do not infer a roster release.",
        REQUIRES_SOURCE_CORRECTION: "Prepare an audited source correction before staging.",
        REQUIRES_DEAD_CAP_REVIEW: "Resolve the missing or retained liability before staging.",
        REQUIRES_COMMISSIONER_DECISION: "Hold from staging until the commissioner resolves the evidence gap.",
    }[readiness]
