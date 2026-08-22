from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from services.application_request_context import (
    ApplicationContextResolver,
    ApplicationRequestContext,
    ContextFailureCode,
    ContextRequest,
)
from services.trade_contract_models import TradeCalculationContext, TradeContractEvidence


COMMAND_VERSION = "trade_command.v1"
VALIDATION_VERSION = "trade_validation.v2"


class TradeResultCode(str, Enum):
    VALID_DRY_RUN = "valid_dry_run"
    INVALID_DRY_RUN = "invalid_dry_run"
    EMPTY_TRADE = "empty_trade"
    ALREADY_PROCESSED = "already_processed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNAUTHORIZED = "unauthorized"
    UNAUTHENTICATED = "unauthenticated"
    INVALID_CONTEXT = "invalid_context"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    MEMBERSHIP_NOT_FOUND = "membership_not_found"
    LEAGUE_TEAM_NOT_FOUND = "league_team_not_found"
    LEAGUE_TEAM_MISMATCH = "league_team_mismatch"
    STALE_OWNERSHIP = "stale_ownership"
    RULE_VIOLATION = "rule_violation"
    REQUIRES_RULE_DECISION = "requires_rule_decision"
    MISSING_RULE_EVIDENCE = "missing_rule_evidence"
    UNKNOWN_RULE_RESULT = "unknown_rule_result"
    MISSING_EVIDENCE = "missing_evidence"
    DATABASE_TRANSACTION_REQUIRED = "database_transaction_required"
    MIXED_SEASON_LEGALITY_DEFERRED = "mixed_season_legality_deferred"


class RuleCheckStatus(str, Enum):
    PASS = "pass"
    VIOLATION = "violation"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class OperationType(str, Enum):
    UPDATE_PLAYER_OWNERSHIP = "update_player_ownership"
    UPDATE_DRAFT_PICK_OWNERSHIP = "update_draft_pick_ownership"
    CREATE_CAP_ADJUSTMENT = "create_cap_adjustment"
    UPDATE_TRADE_STATUS = "update_trade_status"
    INSERT_TRANSACTION_LEDGER = "insert_transaction_ledger"
    INSERT_AUDIT_RECORD = "insert_audit_record"


@dataclass(frozen=True)
class PlayerTransfer:
    player_id: str
    from_league_team_id: str
    to_league_team_id: str


@dataclass(frozen=True)
class DraftPickTransfer:
    draft_pick_id: str
    from_league_team_id: str
    to_league_team_id: str


@dataclass(frozen=True)
class CapAdjustment:
    from_league_team_id: str
    to_league_team_id: str
    amount: float
    seasons: tuple[int, ...]


@dataclass(frozen=True)
class TradeCommand:
    identity: ContextRequest
    trade_id: str
    initiating_league_team_id: str
    counterparty_league_team_id: str
    idempotency_key: str
    dry_run: bool = True
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
    player_transfers: tuple[PlayerTransfer, ...] = ()
    draft_pick_transfers: tuple[DraftPickTransfer, ...] = ()
    cap_adjustments: tuple[CapAdjustment, ...] = ()


@dataclass(frozen=True)
class TeamEvidence:
    league_team_id: str
    league_id: str


@dataclass(frozen=True)
class AssetOwnership:
    asset_id: str
    league_id: str
    league_team_id: str


@dataclass(frozen=True)
class DeterministicRuleCheck:
    rule_id: str
    status: RuleCheckStatus
    explanation: str
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradeEvidence:
    initiating_team: TeamEvidence | None
    counterparty_team: TeamEvidence | None
    player_ownership: Mapping[str, AssetOwnership]
    draft_pick_ownership: Mapping[str, AssetOwnership]
    rule_checks: tuple[DeterministicRuleCheck, ...] = ()
    rule_provenance: tuple[str, ...] = ()
    player_contracts: Mapping[str, TradeContractEvidence] = field(default_factory=dict)
    calculation_context: TradeCalculationContext | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "player_ownership", _immutable_mapping(self.player_ownership))
        object.__setattr__(self, "draft_pick_ownership", _immutable_mapping(self.draft_pick_ownership))
        object.__setattr__(self, "player_contracts", _immutable_mapping(self.player_contracts))


@dataclass(frozen=True)
class IdempotencyEvidence:
    command_fingerprint: str


@dataclass(frozen=True)
class ProposedOperation:
    operation: OperationType
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _immutable_mapping(self.values))


@dataclass(frozen=True)
class TradeAuditRecord:
    correlation_id: str
    idempotency_key: str
    authenticated_user_id: str
    league_id: str
    initiating_league_team_id: str
    affected_league_team_ids: tuple[str, ...]
    trade_id: str
    command_version: str
    validation_version: str
    command_fingerprint: str
    rule_provenance: tuple[str, ...]
    requested_operations: tuple[str, ...]
    result_status: str
    timestamp: datetime
    safe_failure_code: str | None = None


@dataclass(frozen=True)
class ValidatedTradePlan:
    context: ApplicationRequestContext
    operations: tuple[ProposedOperation, ...]
    audit_record: TradeAuditRecord


@dataclass(frozen=True)
class TradeCommandResult:
    code: TradeResultCode
    message: str
    plan: ValidatedTradePlan | None = None
    audit_record: TradeAuditRecord | None = None
    failures: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", _immutable_mapping(self.diagnostics))

    @property
    def ok(self) -> bool:
        return self.code is TradeResultCode.VALID_DRY_RUN


class TradeEvidenceRepository(Protocol):
    def get_idempotency_evidence(self, league_id: str, idempotency_key: str) -> IdempotencyEvidence | None: ...

    def load_trade_evidence(self, context: ApplicationRequestContext, command: TradeCommand) -> TradeEvidence: ...


class AtomicTradePersistence(Protocol):
    def execute_trade_atomically(self, command: TradeCommand, validated_plan: ValidatedTradePlan) -> TradeCommandResult: ...


class TradeCommandService:
    def __init__(self, context_resolver: ApplicationContextResolver, evidence_repository: TradeEvidenceRepository):
        self.context_resolver = context_resolver
        self.evidence_repository = evidence_repository

    def execute(self, command: TradeCommand) -> TradeCommandResult:
        if not command.dry_run:
            return TradeCommandResult(
                TradeResultCode.DATABASE_TRANSACTION_REQUIRED,
                "Live trade execution requires a proven atomic database transaction or RPC.",
            )
        try:
            return self._dry_run(command)
        except Exception:
            return TradeCommandResult(
                TradeResultCode.BACKEND_UNAVAILABLE,
                "Trade validation is temporarily unavailable.",
                failures=("service_boundary_failure",),
            )

    def dry_run(self, command: TradeCommand) -> TradeCommandResult:
        return self.execute(command)

    def _dry_run(self, command: TradeCommand) -> TradeCommandResult:
        required = {
            "trade_id": command.trade_id,
            "initiating_league_team_id": command.initiating_league_team_id,
            "counterparty_league_team_id": command.counterparty_league_team_id,
            "idempotency_key": command.idempotency_key,
        }
        missing = tuple(name for name, value in required.items() if not _clean(value))
        if missing:
            return TradeCommandResult(TradeResultCode.INVALID_DRY_RUN, "Required trade identifiers are missing.", failures=missing)
        if command.requested_at.tzinfo is None or command.requested_at.utcoffset() is None:
            return TradeCommandResult(
                TradeResultCode.INVALID_DRY_RUN,
                "The requested timestamp must be timezone-aware.",
                failures=("timezone_aware_timestamp_required",),
            )

        resolution = self.context_resolver.resolve(command.identity)
        if not resolution.ok:
            failure = resolution.failure
            code = _context_result_code(failure.code if failure else ContextFailureCode.INVALID_CONTEXT)
            return TradeCommandResult(
                code,
                "Canonical request context could not be resolved.",
                failures=(failure.code.value,) if failure else ("invalid_context",),
                diagnostics=failure.diagnostics if failure else {},
            )
        context = resolution.context
        assert context is not None
        correlation_id = _clean(command.correlation_id) or f"trade-{uuid4()}"
        fingerprint = command_fingerprint(command)

        if context.league_team_id != command.initiating_league_team_id or not context.has_scope("team_control"):
            return _audited_failure(
                TradeResultCode.UNAUTHORIZED,
                "The authenticated user does not control the initiating league team.",
                context, command, correlation_id, fingerprint,
                failures=("team_control_denied",),
            )
        if not (command.player_transfers or command.draft_pick_transfers or command.cap_adjustments):
            return _audited_failure(
                TradeResultCode.EMPTY_TRADE,
                "A trade must contain at least one transferable asset or cap adjustment.",
                context, command, correlation_id, fingerprint,
                failures=("no_transferable_operations",),
            )

        participant_failure = _validate_transfer_participants(command)
        if participant_failure:
            return _with_audit(participant_failure, context, command, correlation_id, fingerprint)
        duplicate_failure = _duplicate_assets(command)
        if duplicate_failure:
            return _with_audit(duplicate_failure, context, command, correlation_id, fingerprint)

        try:
            existing = self.evidence_repository.get_idempotency_evidence(context.league_id, command.idempotency_key)
            if existing:
                code = TradeResultCode.ALREADY_PROCESSED if existing.command_fingerprint == fingerprint else TradeResultCode.IDEMPOTENCY_CONFLICT
                message = "The same normalized command was already processed." if code is TradeResultCode.ALREADY_PROCESSED else "The idempotency key belongs to a different command."
                return _audited_failure(code, message, context, command, correlation_id, fingerprint, failures=(code.value,))
            evidence = self.evidence_repository.load_trade_evidence(context, command)
        except Exception:
            return _audited_failure(
                TradeResultCode.BACKEND_UNAVAILABLE,
                "Trade evidence is unavailable.",
                context, command, correlation_id, fingerprint,
                failures=("evidence_backend_unavailable",),
            )

        evidence_failure = _validate_evidence(context, command, evidence)
        if evidence_failure:
            return _with_audit(evidence_failure, context, command, correlation_id, fingerprint, evidence.rule_provenance)
        season_failure = _validate_trade_season_context(command, evidence)
        if season_failure:
            return _with_audit(season_failure, context, command, correlation_id, fingerprint, evidence.rule_provenance)
        rule_failure = _validate_rules(evidence.rule_checks, evidence.rule_provenance)
        if rule_failure:
            return _with_audit(rule_failure, context, command, correlation_id, fingerprint, evidence.rule_provenance)

        operations = _build_operations(context, command)
        audit = _build_audit(
            context, command, correlation_id, fingerprint, TradeResultCode.VALID_DRY_RUN,
            evidence.rule_provenance, tuple(operation.operation.value for operation in operations),
        )
        operations = operations + (ProposedOperation(OperationType.INSERT_AUDIT_RECORD, {"audit": audit}),)
        plan = ValidatedTradePlan(context, operations, audit)
        return TradeCommandResult(
            TradeResultCode.VALID_DRY_RUN,
            "Trade is valid for dry run; no persistence was attempted.",
            plan=plan,
            audit_record=audit,
            diagnostics={"legacy_identity_used": context.provenance.legacy_fallback_used,
                "league_season":evidence.calculation_context.league_season if evidence.calculation_context else None,
                "contract_operational_season":evidence.calculation_context.contract_operational_season if evidence.calculation_context else None,
                "cap_calculation_season":evidence.calculation_context.cap_calculation_season if evidence.calculation_context else None},
        )


def command_fingerprint(command: TradeCommand) -> str:
    normalized = {
        "command_version": COMMAND_VERSION,
        "trade_id": _clean(command.trade_id),
        "league_id": _clean(command.identity.active_league_id),
        "initiating_league_team_id": _clean(command.initiating_league_team_id),
        "counterparty_league_team_id": _clean(command.counterparty_league_team_id),
        "player_transfers": sorted((item.player_id, item.from_league_team_id, item.to_league_team_id) for item in command.player_transfers),
        "draft_pick_transfers": sorted((item.draft_pick_id, item.from_league_team_id, item.to_league_team_id) for item in command.draft_pick_transfers),
        "cap_adjustments": sorted((item.from_league_team_id, item.to_league_team_id, str(item.amount), tuple(sorted(item.seasons))) for item in command.cap_adjustments),
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _context_result_code(code: ContextFailureCode) -> TradeResultCode:
    return {
        ContextFailureCode.UNAUTHENTICATED: TradeResultCode.UNAUTHENTICATED,
        ContextFailureCode.NO_ACTIVE_LEAGUE_SELECTED: TradeResultCode.INVALID_CONTEXT,
        ContextFailureCode.MEMBERSHIP_NOT_FOUND: TradeResultCode.MEMBERSHIP_NOT_FOUND,
        ContextFailureCode.DUPLICATE_MEMBERSHIP: TradeResultCode.INVALID_CONTEXT,
        ContextFailureCode.LEAGUE_TEAM_NOT_FOUND: TradeResultCode.LEAGUE_TEAM_NOT_FOUND,
        ContextFailureCode.LEAGUE_TEAM_MISMATCH: TradeResultCode.LEAGUE_TEAM_MISMATCH,
        ContextFailureCode.LEGACY_IDENTITY_REQUIRED: TradeResultCode.INVALID_CONTEXT,
        ContextFailureCode.BACKEND_UNAVAILABLE: TradeResultCode.BACKEND_UNAVAILABLE,
        ContextFailureCode.INVALID_CONTEXT: TradeResultCode.INVALID_CONTEXT,
    }[code]


def _validate_rules(checks: Sequence[DeterministicRuleCheck], provenance: Sequence[str]) -> TradeCommandResult | None:
    if not checks or not provenance:
        return TradeCommandResult(TradeResultCode.MISSING_RULE_EVIDENCE, "Complete deterministic rule evidence is required.", failures=("missing_rule_evidence",))
    incomplete = tuple(check.rule_id or "unnamed_rule" for check in checks if not _clean(check.rule_id) or not check.provenance)
    if incomplete:
        return TradeCommandResult(TradeResultCode.MISSING_RULE_EVIDENCE, "Every rule result requires identity and provenance.", failures=incomplete)
    conflicts = tuple(check.rule_id for check in checks if check.status is RuleCheckStatus.CONFLICT)
    if conflicts:
        return TradeCommandResult(TradeResultCode.REQUIRES_RULE_DECISION, "Rule evidence conflicts and requires an explicit decision.", failures=conflicts)
    unknown = tuple(check.rule_id for check in checks if check.status is RuleCheckStatus.UNKNOWN)
    if unknown:
        return TradeCommandResult(TradeResultCode.UNKNOWN_RULE_RESULT, "A deterministic rule result is unknown.", failures=unknown)
    violations = tuple(check.rule_id for check in checks if check.status is RuleCheckStatus.VIOLATION)
    if violations:
        return TradeCommandResult(TradeResultCode.RULE_VIOLATION, "A verified deterministic trade rule failed.", failures=violations)
    return None


def _duplicate_assets(command: TradeCommand) -> TradeCommandResult | None:
    player_ids = [item.player_id for item in command.player_transfers]
    pick_ids = [item.draft_pick_id for item in command.draft_pick_transfers]
    if len(player_ids) != len(set(player_ids)):
        return TradeCommandResult(TradeResultCode.INVALID_DRY_RUN, "A player appears more than once in the trade.", failures=("duplicate_player",))
    if len(pick_ids) != len(set(pick_ids)):
        return TradeCommandResult(TradeResultCode.INVALID_DRY_RUN, "A draft pick appears more than once in the trade.", failures=("duplicate_draft_pick",))
    return None


def _validate_transfer_participants(command: TradeCommand) -> TradeCommandResult | None:
    participants = {command.initiating_league_team_id, command.counterparty_league_team_id}
    transfers = [(item.player_id, item.from_league_team_id, item.to_league_team_id, "player") for item in command.player_transfers]
    transfers += [(item.draft_pick_id, item.from_league_team_id, item.to_league_team_id, "draft_pick") for item in command.draft_pick_transfers]
    for asset_id, from_team, to_team, asset_type in transfers:
        if not all(_clean(value) for value in (asset_id, from_team, to_team)):
            return TradeCommandResult(TradeResultCode.INVALID_DRY_RUN, f"{asset_type} transfer is missing required identity.")
        if from_team == to_team or from_team not in participants or to_team not in participants:
            return TradeCommandResult(TradeResultCode.INVALID_DRY_RUN, f"{asset_type} transfer does not match the trade participants.")
    for adjustment in command.cap_adjustments:
        if (adjustment.from_league_team_id == adjustment.to_league_team_id or adjustment.from_league_team_id not in participants or adjustment.to_league_team_id not in participants or adjustment.amount <= 0 or not adjustment.seasons):
            return TradeCommandResult(TradeResultCode.INVALID_DRY_RUN, "Cap adjustment has invalid participants, amount, or seasons.")
    return None


def _validate_evidence(context: ApplicationRequestContext, command: TradeCommand, evidence: TradeEvidence) -> TradeCommandResult | None:
    if not evidence.initiating_team or not evidence.counterparty_team:
        return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE, "Team evidence is incomplete.")
    teams = (evidence.initiating_team, evidence.counterparty_team)
    if any(team.league_id != context.league_id for team in teams):
        return TradeCommandResult(TradeResultCode.UNAUTHORIZED, "Both trade teams must belong to the authenticated league.", failures=("team_league_mismatch",))
    if evidence.initiating_team.league_team_id != command.initiating_league_team_id or evidence.counterparty_team.league_team_id != command.counterparty_league_team_id:
        return TradeCommandResult(TradeResultCode.STALE_OWNERSHIP, "Trade team evidence does not match the command.")
    for transfer in command.player_transfers:
        failure = _asset_failure(transfer.player_id, transfer.from_league_team_id, context.league_id, evidence.player_ownership, "player")
        if failure:
            return failure
        contract=evidence.player_contracts.get(transfer.player_id)
        if not contract:
            return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE,"Normalized contract evidence is missing for a transferred player.",failures=(transfer.player_id,))
        if contract.league_id!=context.league_id or transfer.player_id not in {contract.player_id,contract.sleeper_player_id}:
            return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE,"Transferred player contract identity is inconsistent.",failures=(transfer.player_id,))
        if contract.agreement_status=="active" and (contract.operational_salary is None or contract.years_remaining<1):
            return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE,"Active contract evidence lacks operational salary or term.",failures=(transfer.player_id,))
        if contract.agreement_status=="expired" and (contract.operational_salary is not None or contract.years_remaining!=0):
            return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE,"Expired contract evidence carries active salary or term.",failures=(transfer.player_id,))
    for transfer in command.draft_pick_transfers:
        failure = _asset_failure(transfer.draft_pick_id, transfer.from_league_team_id, context.league_id, evidence.draft_pick_ownership, "draft_pick")
        if failure:
            return failure
    return None


def _validate_trade_season_context(command: TradeCommand,evidence: TradeEvidence)->TradeCommandResult|None:
    context=evidence.calculation_context
    if not context:
        return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE,"Explicit league, contract, cap, roster, and draft season context is required.",failures=("trade_calculation_context",))
    if any(contract.contract_operational_season!=context.contract_operational_season for contract in evidence.player_contracts.values()):
        return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE,"Contract evidence does not match the declared operational season.",failures=("contract_operational_season",))
    if not context.supports_definitive_cap_legality:
        return TradeCommandResult(TradeResultCode.MIXED_SEASON_LEGALITY_DEFERRED,
            "Contract value analysis is available, but definitive trade legality is deferred because league, contract, and cap season authority are not aligned.",
            failures=("mixed_season_legality_deferred",),diagnostics={"league_season":context.league_season,"contract_operational_season":context.contract_operational_season,"cap_calculation_season":context.cap_calculation_season,"roster_snapshot_season":context.roster_snapshot_season,"draft_pick_season_basis":context.draft_pick_season_basis})
    return None


def _asset_failure(asset_id: str, expected_owner: str, league_id: str, ownership: Mapping[str, AssetOwnership], asset_type: str) -> TradeCommandResult | None:
    current = ownership.get(asset_id)
    if not current:
        return TradeCommandResult(TradeResultCode.MISSING_EVIDENCE, f"Ownership evidence is missing for {asset_type}.", failures=(asset_type,))
    if current.league_id != league_id or current.league_team_id != expected_owner:
        return TradeCommandResult(TradeResultCode.STALE_OWNERSHIP, f"{asset_type} ownership changed or is outside the league.", failures=(asset_id,))
    return None


def _build_operations(context: ApplicationRequestContext, command: TradeCommand) -> tuple[ProposedOperation, ...]:
    operations: list[ProposedOperation] = []
    for transfer in command.player_transfers:
        operations.append(ProposedOperation(OperationType.UPDATE_PLAYER_OWNERSHIP, {"league_id": context.league_id, "player_id": transfer.player_id, "expected_owner_league_team_id": transfer.from_league_team_id, "new_owner_league_team_id": transfer.to_league_team_id}))
    for transfer in command.draft_pick_transfers:
        operations.append(ProposedOperation(OperationType.UPDATE_DRAFT_PICK_OWNERSHIP, {"league_id": context.league_id, "draft_pick_id": transfer.draft_pick_id, "expected_owner_league_team_id": transfer.from_league_team_id, "new_owner_league_team_id": transfer.to_league_team_id}))
    for adjustment in command.cap_adjustments:
        operations.append(ProposedOperation(OperationType.CREATE_CAP_ADJUSTMENT, {"league_id": context.league_id, "from_league_team_id": adjustment.from_league_team_id, "to_league_team_id": adjustment.to_league_team_id, "amount": adjustment.amount, "seasons": adjustment.seasons}))
    operations.append(ProposedOperation(OperationType.UPDATE_TRADE_STATUS, {"trade_id": command.trade_id, "expected_status": "OPEN", "new_status": "COMPLETE"}))
    operations.append(ProposedOperation(OperationType.INSERT_TRANSACTION_LEDGER, {"league_id": context.league_id, "trade_id": command.trade_id, "status": "complete"}))
    return tuple(operations)


def _build_audit(context: ApplicationRequestContext, command: TradeCommand, correlation_id: str, fingerprint: str, code: TradeResultCode, rule_provenance: Sequence[str] = (), requested_operations: Sequence[str] = ()) -> TradeAuditRecord:
    return TradeAuditRecord(
        correlation_id=correlation_id,
        idempotency_key=command.idempotency_key,
        authenticated_user_id=context.user_id,
        league_id=context.league_id,
        initiating_league_team_id=context.league_team_id,
        affected_league_team_ids=tuple(sorted({command.initiating_league_team_id, command.counterparty_league_team_id})),
        trade_id=command.trade_id,
        command_version=COMMAND_VERSION,
        validation_version=VALIDATION_VERSION,
        command_fingerprint=fingerprint,
        rule_provenance=tuple(rule_provenance),
        requested_operations=tuple(requested_operations),
        result_status=code.value,
        timestamp=command.requested_at.astimezone(timezone.utc),
        safe_failure_code=None if code is TradeResultCode.VALID_DRY_RUN else code.value,
    )


def _audited_failure(code: TradeResultCode, message: str, context: ApplicationRequestContext, command: TradeCommand, correlation_id: str, fingerprint: str, failures: tuple[str, ...] = (), rule_provenance: Sequence[str] = ()) -> TradeCommandResult:
    result = TradeCommandResult(code, message, failures=failures)
    return _with_audit(result, context, command, correlation_id, fingerprint, rule_provenance)


def _with_audit(result: TradeCommandResult, context: ApplicationRequestContext, command: TradeCommand, correlation_id: str, fingerprint: str, rule_provenance: Sequence[str] = ()) -> TradeCommandResult:
    audit = _build_audit(context, command, correlation_id, fingerprint, result.code, rule_provenance)
    return TradeCommandResult(result.code, result.message, audit_record=audit, failures=result.failures, diagnostics=result.diagnostics)


def _immutable_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(value) for key, value in values.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
