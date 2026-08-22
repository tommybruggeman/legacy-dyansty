from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EXECUTOR_VERSION = "contract-transition-executor-v1"
REQUEST_VERSION = "v1"


@dataclass(frozen=True)
class ContractTransitionExecutionRequest:
    league_id: str
    source_season: int
    target_season: int
    source_league_season_id: str
    target_league_season_id: str
    expected_source_fingerprint: str
    actual_source_fingerprint: str
    expected_plan_fingerprint: str
    actual_plan_fingerprint: str
    transition_key: str
    request_version: str
    planner_version: str
    executor_version: str
    dry_run: bool
    requested_at: str
    expected_counts: dict[str, int]
    agreement_plan: tuple[dict[str, Any], ...]
    requested_by: str | None = None

    def payload(self) -> dict[str, Any]:
        value=dict(self.__dict__); value["agreement_plan"]=list(self.agreement_plan); return value


@dataclass(frozen=True)
class ContractTransitionExecutionPreview:
    safe_to_apply: bool
    request: ContractTransitionExecutionRequest
    mutation_preview: dict[str, int]
    expected_persisted_counts: dict[str, int]
    warnings: tuple[dict[str, Any], ...]
    blocking_errors: tuple[dict[str, Any], ...]
