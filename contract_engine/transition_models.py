from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CONTINUES="CONTINUES"
EXPIRES_AFTER_SOURCE="EXPIRES_AFTER_2025"
INVALID_MISSING_SOURCE="INVALID_MISSING_2025"
INVALID_GAP="INVALID_GAP"
INVALID_DUPLICATE="INVALID_DUPLICATE"
ALREADY_TRANSITIONED="ALREADY_TRANSITIONED"
NOT_APPLICABLE="NOT_APPLICABLE"


@dataclass(frozen=True)
class TransitionRequest:
    league_id: str
    source_season: int
    target_season: int
    source_league_season_id: str
    target_league_season_id: str
    source_status: str
    target_status: str
    requested_at: str
    request_version: str="v1"
    planner_version: str="contract-transition-v1"
    expected_source_fingerprint: str|None=None


@dataclass(frozen=True)
class TransitionPlan:
    request: TransitionRequest
    classifications: tuple[dict[str,Any],...]
    free_agent_candidates: tuple[dict[str,Any],...]
    team_projections: tuple[dict[str,Any],...]
    warnings: tuple[dict[str,Any],...]
    blocking_errors: tuple[dict[str,Any],...]
    source_fingerprints: dict[str,str]
    source_fingerprint: str
    plan_fingerprint: str
    idempotency_key: str
    counts: dict[str,int]

    @property
    def safe_to_transition(self)->bool:return not self.blocking_errors
