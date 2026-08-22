"""Canonical league-season authority for Legacy."""

from season_engine.models import LeagueSeason, SeasonStatus
from season_engine.resolver import (
    DuplicateActiveSeasonError,
    DuplicateLeagueSeasonError,
    SeasonAuthorityError,
    SeasonNotFoundError,
    SeasonResolver,
)
from season_engine.service import get_active_season, get_completed_season, get_next_season

__all__ = [
    "DuplicateActiveSeasonError",
    "DuplicateLeagueSeasonError",
    "LeagueSeason",
    "SeasonAuthorityError",
    "SeasonNotFoundError",
    "SeasonResolver",
    "SeasonStatus",
    "get_active_season",
    "get_completed_season",
    "get_next_season",
]
from season_engine.rollover_models import (
    CapSeasonPolicy, CommissionerRolloverDecision, FreeAgentPublicationState,
    RolloverReadinessReport, SeasonRolloverContext,
)
from season_engine.rollover_service import RolloverAuthorityService
from season_engine.target_authority import (
    CommissionerPolicyService, FreeAgentPublicationService, LeagueRolloverPolicy,
    TargetAuthorityRepository, TargetAuthorityService,
)
from season_engine.commissioner_policy_draft import CommissionerPolicyDraftService
from season_engine.rollover_control_repository import RolloverControlRepository,RolloverControlState
from season_engine.rollover_control_models import (
    RolloverExecution,RolloverExecutionStatus,RolloverApprovalStatus,
    RolloverOwnerDecision,RolloverOwnerDecisionStatus,RolloverOwnerChoice,RolloverDecisionExecutionStatus,
    RolloverOwnerDecisionRevision,RolloverCommissionerReview,RolloverCommissionerReviewType,
    RolloverCommissionerReviewStatus,RolloverCommissionerReviewEvent,RolloverExecutionPlan,
    RolloverExecutionPlanStatus,RolloverExecutionLock,RolloverExecutionLockScope,
    RolloverExecutionLockStatus,RolloverValidationResult,RolloverValidationRunType,RolloverValidationStatus,
)
from season_engine.rollover_window import (
    CommissionerPopulationBuilder,
    OwnerAuthorizationService,
    OwnerDecisionValidator,
    OwnerPopulationBuilder,
    RolloverPreflightRequest,
    RolloverPreflightService,
    RolloverWindowService,
    resolve_owner_deadline,
    rollover_window_readiness,
)
from season_engine.commissioner_review import (
    CommissionerPopulationBuilder, CommissionerReviewCandidate,
    CommissionerReviewDecision, CommissionerReviewPlanInstruction,
    CommissionerReviewService, CommissionerReviewValidationResult,
    CommissionerReviewValidator, OUTCOME_MATRIX, LEGAL_TRANSITIONS,
    commissioner_review_readiness, to_plan_instruction,
)
from season_engine.authority_preparation import (
    AuthorityDomainPlan, AuthorityPreparationService, AuthorityPreparationValidator,
    AuthoritySimulationInput, AuthorityStatus, AuthorityType, AuthorityValidationResult,
    CapAuthorityPlan, CapAuthorityPlanner, DeadCapAuthorityInstruction,
    DeadCapAuthorityPlanner, PublicationAuthorityInstruction, PublicationAuthorityPlanner,
    RolloverAuthorityPreparationPackage, TeamCapProjection,
    PersistedAuthorityPreparation,
    authority_preparation_readiness, build_preparation_package, material_fingerprint,
    to_simulation_input,
)
from season_engine.dry_run_simulator import (
    DryRunExecutionPlanInput, RolloverDryRunResult, RolloverDryRunSimulator,
    RolloverDryRunValidationResult, RolloverDryRunValidator, SimulationChange,
    TableMutationSummary, TeamDryRunResult, dry_run_readiness, to_execution_plan_input,
    PersistedDryRunSimulation, TrustedDryRunCancellationService, TrustedDryRunGenerationService,
)
from season_engine.execution_plan import (
    ExecutionPlanOperation, ExecutionPlanValidation, PLANNER_VERSION,
    PLAN_VALIDATOR_VERSION, RolloverExecutionPlan as DeterministicRolloverExecutionPlan,
    RolloverExecutionPlanner, RolloverExecutionPlanValidator,
    TrustedExecutionPlanService, execution_plan_readiness,
)
from season_engine.execution_approval import (
    APPROVAL_SCHEMA_VERSION, APPROVAL_STATEMENT_CODE, APPROVAL_STATEMENT_VERSION,
    DurableCutoverLock, RolloverExecutionApproval, RolloverExecutionApprovalInput,
    RolloverExecutionApprovalService, build_approval_input, execution_approval_readiness,
)

__all__ += [
    "CapSeasonPolicy", "CommissionerRolloverDecision", "FreeAgentPublicationState",
    "RolloverAuthorityService", "RolloverReadinessReport", "SeasonRolloverContext",
    "CommissionerPolicyService", "FreeAgentPublicationService", "LeagueRolloverPolicy",
    "TargetAuthorityService",
    "TargetAuthorityRepository",
    "CommissionerPolicyDraftService",
    "RolloverControlRepository", "RolloverControlState", "RolloverExecution", "RolloverExecutionStatus",
    "RolloverApprovalStatus", "RolloverOwnerDecision", "RolloverOwnerDecisionStatus", "RolloverOwnerChoice",
    "RolloverDecisionExecutionStatus", "RolloverOwnerDecisionRevision", "RolloverCommissionerReview",
    "RolloverCommissionerReviewType", "RolloverCommissionerReviewStatus", "RolloverCommissionerReviewEvent",
    "RolloverExecutionPlan", "RolloverExecutionPlanStatus", "RolloverExecutionLock",
    "RolloverExecutionLockScope", "RolloverExecutionLockStatus", "RolloverValidationResult",
    "RolloverValidationRunType", "RolloverValidationStatus",
    "CommissionerPopulationBuilder", "OwnerAuthorizationService", "OwnerDecisionValidator",
    "OwnerPopulationBuilder", "RolloverPreflightRequest", "RolloverPreflightService",
    "RolloverWindowService", "resolve_owner_deadline", "rollover_window_readiness",
    "CommissionerPopulationBuilder", "CommissionerReviewCandidate", "CommissionerReviewDecision",
    "CommissionerReviewPlanInstruction", "CommissionerReviewService",
    "CommissionerReviewValidationResult", "CommissionerReviewValidator", "OUTCOME_MATRIX",
    "LEGAL_TRANSITIONS", "commissioner_review_readiness", "to_plan_instruction",
    "AuthorityDomainPlan", "AuthorityPreparationService", "AuthorityPreparationValidator",
    "AuthoritySimulationInput", "AuthorityStatus", "AuthorityType", "AuthorityValidationResult",
    "CapAuthorityPlan", "CapAuthorityPlanner", "DeadCapAuthorityInstruction", "DeadCapAuthorityPlanner",
    "PublicationAuthorityInstruction", "PublicationAuthorityPlanner",
    "RolloverAuthorityPreparationPackage", "TeamCapProjection", "authority_preparation_readiness",
    "PersistedAuthorityPreparation",
    "build_preparation_package", "material_fingerprint", "to_simulation_input",
    "DryRunExecutionPlanInput", "RolloverDryRunResult", "RolloverDryRunSimulator",
    "RolloverDryRunValidationResult", "RolloverDryRunValidator", "SimulationChange",
    "TableMutationSummary", "TeamDryRunResult", "dry_run_readiness", "to_execution_plan_input",
    "PersistedDryRunSimulation", "TrustedDryRunCancellationService", "TrustedDryRunGenerationService",
    "ExecutionPlanOperation", "ExecutionPlanValidation", "PLANNER_VERSION", "PLAN_VALIDATOR_VERSION",
    "DeterministicRolloverExecutionPlan", "RolloverExecutionPlanner", "RolloverExecutionPlanValidator",
    "TrustedExecutionPlanService", "execution_plan_readiness",
    "APPROVAL_SCHEMA_VERSION", "APPROVAL_STATEMENT_CODE", "APPROVAL_STATEMENT_VERSION",
    "DurableCutoverLock", "RolloverExecutionApproval", "RolloverExecutionApprovalInput",
    "RolloverExecutionApprovalService", "build_approval_input", "execution_approval_readiness",
]
