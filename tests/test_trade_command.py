from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

from services.application_request_context import ApplicationContextResolver, ContextRequest
from services.trade_command import (
    COMMAND_VERSION,
    VALIDATION_VERSION,
    AssetOwnership,
    DeterministicRuleCheck,
    DraftPickTransfer,
    IdempotencyEvidence,
    OperationType,
    PlayerTransfer,
    RuleCheckStatus,
    TeamEvidence,
    TradeCommand,
    TradeCommandService,
    TradeEvidence,
    TradeResultCode,
    command_fingerprint,
)
from tests.test_application_request_context import FakeIdentityRepository, canonical_membership
from services.trade_contract_models import TradeCalculationContext, TradeContractEvidence


def contract_evidence(player_id="player-1"):
    return TradeContractEvidence("agreement-1","league-1",player_id,player_id,"Player One","team-1","Team One","active",2026,Decimal("10"),1,2026,(),Decimal("0"),"legacy-1","veteran","team-1","rostered_active_contract",{"authority":"normalized_contract_model"})


class FakeTradeRepository:
    def __init__(self, evidence, existing=None, fail_idempotency=False, fail_load=False):
        self.evidence = evidence
        self.existing = existing
        self.fail_idempotency = fail_idempotency
        self.fail_load = fail_load
        self.load_count = 0
        self.write_count = 0

    def get_idempotency_evidence(self, league_id, idempotency_key):
        if self.fail_idempotency:
            raise RuntimeError("backend secret detail")
        return self.existing

    def load_trade_evidence(self, context, command):
        self.load_count += 1
        if self.fail_load:
            raise RuntimeError("backend secret detail")
        return self.evidence

    def write(self, *_args, **_kwargs):
        self.write_count += 1
        raise AssertionError("dry run must never write")


def identity_repository(role="owner", memberships=None, teams=None, fail=False):
    return FakeIdentityRepository(
        memberships if memberships is not None else [canonical_membership(role=role)],
        teams if teams is not None else [{"id": "team-1", "league_id": "league-1"}],
        fail=fail,
    )


def passing_check():
    return DeterministicRuleCheck(
        "existing_roster_and_cap_checks",
        RuleCheckStatus.PASS,
        "Verified current rules pass.",
        ("services.transaction_engine",),
    )


def evidence(**updates):
    values = {
        "initiating_team": TeamEvidence("team-1", "league-1"),
        "counterparty_team": TeamEvidence("team-2", "league-1"),
        "player_ownership": {"player-1": AssetOwnership("player-1", "league-1", "team-1")},
        "draft_pick_ownership": {"pick-1": AssetOwnership("pick-1", "league-1", "team-2")},
        "rule_checks": (passing_check(),),
        "rule_provenance": ("services.transaction_engine",),
        "player_contracts": {"player-1":contract_evidence()},
        "calculation_context": TradeCalculationContext(2026,2026,2026,2026),
    }
    values.update(updates)
    return TradeEvidence(**values)


def command(**updates):
    values = {
        "identity": ContextRequest("user-1", "league-1", season=2026),
        "trade_id": "trade-1",
        "initiating_league_team_id": "team-1",
        "counterparty_league_team_id": "team-2",
        "idempotency_key": "idem-1",
        "requested_at": datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
        "player_transfers": (PlayerTransfer("player-1", "team-1", "team-2"),),
    }
    values.update(updates)
    return TradeCommand(**values)


class TradeCommandServiceTest(unittest.TestCase):
    def service(self, trade_repo, identity_repo=None):
        return TradeCommandService(ApplicationContextResolver(identity_repo or identity_repository()), trade_repo)

    def test_valid_complete_rule_evidence_and_full_audit(self):
        repo = FakeTradeRepository(evidence())
        result = self.service(repo).execute(command(correlation_id="caller-correlation"))
        self.assertEqual(result.code, TradeResultCode.VALID_DRY_RUN)
        audit = result.audit_record
        self.assertEqual(audit.correlation_id, "caller-correlation")
        self.assertEqual(audit.idempotency_key, "idem-1")
        self.assertEqual(audit.authenticated_user_id, "user-1")
        self.assertEqual(audit.league_id, "league-1")
        self.assertEqual(audit.initiating_league_team_id, "team-1")
        self.assertEqual(audit.affected_league_team_ids, ("team-1", "team-2"))
        self.assertEqual(audit.trade_id, "trade-1")
        self.assertEqual(audit.command_version, COMMAND_VERSION)
        self.assertEqual(audit.validation_version, VALIDATION_VERSION)
        self.assertEqual(audit.rule_provenance, ("services.transaction_engine",))
        self.assertIn(OperationType.UPDATE_PLAYER_OWNERSHIP.value, audit.requested_operations)
        self.assertEqual(audit.result_status, TradeResultCode.VALID_DRY_RUN.value)
        self.assertIsNone(audit.safe_failure_code)
        self.assertIsNotNone(audit.timestamp.utcoffset())
        self.assertEqual(repo.write_count, 0)

    def test_generated_correlation_id(self):
        result = self.service(FakeTradeRepository(evidence())).execute(command(correlation_id=None))
        self.assertTrue(result.audit_record.correlation_id.startswith("trade-"))

    def test_naive_timestamp_is_rejected(self):
        result = self.service(FakeTradeRepository(evidence())).execute(command(requested_at=datetime(2026, 1, 1)))
        self.assertEqual(result.code, TradeResultCode.INVALID_DRY_RUN)

    def test_empty_trade_is_rejected(self):
        result = self.service(FakeTradeRepository(evidence())).execute(command(player_transfers=()))
        self.assertEqual(result.code, TradeResultCode.EMPTY_TRADE)

    def test_empty_rule_checks_are_rejected(self):
        result = self.service(FakeTradeRepository(evidence(rule_checks=()))).execute(command())
        self.assertEqual(result.code, TradeResultCode.MISSING_RULE_EVIDENCE)

    def test_mixed_season_legality_is_deferred_not_declared_valid(self):
        mixed=TradeCalculationContext(2025,2026,2025,2025)
        result=self.service(FakeTradeRepository(evidence(calculation_context=mixed))).execute(command())
        self.assertEqual(result.code,TradeResultCode.MIXED_SEASON_LEGALITY_DEFERRED)
        self.assertFalse(result.ok)

    def test_missing_normalized_contract_evidence_blocks_player_trade(self):
        result=self.service(FakeTradeRepository(evidence(player_contracts={}))).execute(command())
        self.assertEqual(result.code,TradeResultCode.MISSING_EVIDENCE)

    def test_empty_rule_provenance_is_rejected(self):
        result = self.service(FakeTradeRepository(evidence(rule_provenance=()))).execute(command())
        self.assertEqual(result.code, TradeResultCode.MISSING_RULE_EVIDENCE)

    def test_rule_without_own_provenance_is_rejected(self):
        check = DeterministicRuleCheck("roster", RuleCheckStatus.PASS, "pass", ())
        result = self.service(FakeTradeRepository(evidence(rule_checks=(check,)))).execute(command())
        self.assertEqual(result.code, TradeResultCode.MISSING_RULE_EVIDENCE)

    def test_unknown_rule_result_is_rejected(self):
        check = DeterministicRuleCheck("roster", RuleCheckStatus.UNKNOWN, "unknown", ("source",))
        result = self.service(FakeTradeRepository(evidence(rule_checks=(check,)))).execute(command())
        self.assertEqual(result.code, TradeResultCode.UNKNOWN_RULE_RESULT)

    def test_conflicting_rule_source_requires_decision(self):
        check = DeterministicRuleCheck("dead_cap_formula", RuleCheckStatus.CONFLICT, "Sources disagree.", ("a", "b"))
        result = self.service(FakeTradeRepository(evidence(rule_checks=(check,)))).execute(command())
        self.assertEqual(result.code, TradeResultCode.REQUIRES_RULE_DECISION)

    def test_rule_violation(self):
        check = DeterministicRuleCheck("roster_limit", RuleCheckStatus.VIOLATION, "fails", ("source",))
        result = self.service(FakeTradeRepository(evidence(rule_checks=(check,)))).execute(command())
        self.assertEqual(result.code, TradeResultCode.RULE_VIOLATION)

    def test_context_failure_types_are_preserved(self):
        cases = [
            (identity_repository(memberships=[]), TradeResultCode.MEMBERSHIP_NOT_FOUND),
            (identity_repository(teams=[]), TradeResultCode.LEAGUE_TEAM_NOT_FOUND),
            (identity_repository(teams=[{"id": "team-1", "league_id": "league-2"}]), TradeResultCode.LEAGUE_TEAM_MISMATCH),
            (identity_repository(memberships=[canonical_membership(), canonical_membership(id="membership-2")]), TradeResultCode.INVALID_CONTEXT),
            (identity_repository(fail=True), TradeResultCode.BACKEND_UNAVAILABLE),
        ]
        for repository, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.service(FakeTradeRepository(evidence()), repository).execute(command()).code, expected)

    def test_unauthenticated_context_is_distinct(self):
        result = self.service(FakeTradeRepository(evidence())).execute(command(identity=ContextRequest(None, "league-1")))
        self.assertEqual(result.code, TradeResultCode.UNAUTHENTICATED)

    def test_true_authorization_denial_is_unauthorized_and_audited(self):
        result = self.service(FakeTradeRepository(evidence()), identity_repository(role="member")).execute(command())
        self.assertEqual(result.code, TradeResultCode.UNAUTHORIZED)
        self.assertEqual(result.audit_record.safe_failure_code, "unauthorized")

    def test_failure_details_exclude_secrets(self):
        result = self.service(FakeTradeRepository(evidence(), fail_load=True)).execute(command())
        self.assertEqual(result.code, TradeResultCode.BACKEND_UNAVAILABLE)
        rendered = repr(result).lower()
        for forbidden in ("secret detail", "access_token", "refresh_token", "authorization", "api_key"):
            self.assertNotIn(forbidden, rendered)

    def test_same_key_same_command_is_already_processed(self):
        cmd = command()
        repo = FakeTradeRepository(evidence(), IdempotencyEvidence(command_fingerprint(cmd)))
        self.assertEqual(self.service(repo).execute(cmd).code, TradeResultCode.ALREADY_PROCESSED)

    def test_same_key_different_command_is_conflict(self):
        existing = IdempotencyEvidence(command_fingerprint(command(trade_id="other-trade")))
        result = self.service(FakeTradeRepository(evidence(), existing)).execute(command())
        self.assertEqual(result.code, TradeResultCode.IDEMPOTENCY_CONFLICT)

    def test_fingerprint_is_deterministic_and_excludes_transport_metadata(self):
        first = command(correlation_id="one", requested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        second = command(correlation_id="two", requested_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(command_fingerprint(first), command_fingerprint(second))

    def test_repository_evidence_load_failure_is_typed_and_audited(self):
        result = self.service(FakeTradeRepository(evidence(), fail_load=True)).execute(command())
        self.assertEqual(result.code, TradeResultCode.BACKEND_UNAVAILABLE)
        self.assertEqual(result.audit_record.result_status, "backend_unavailable")

    def test_nested_plan_payload_is_immutable(self):
        result = self.service(FakeTradeRepository(evidence())).execute(command())
        operation = result.plan.operations[0]
        with self.assertRaises(TypeError):
            operation.values["player_id"] = "changed"
        audit_operation = result.plan.operations[-1]
        with self.assertRaises(TypeError):
            audit_operation.values["nested"] = {"secret": "value"}
        with self.assertRaises(FrozenInstanceError):
            result.plan.audit_record.trade_id = "changed"

    def test_pick_transfer_and_expected_owner_compare_and_set(self):
        cmd = command(player_transfers=(), draft_pick_transfers=(DraftPickTransfer("pick-1", "team-2", "team-1"),))
        result = self.service(FakeTradeRepository(evidence())).execute(cmd)
        operation = next(item for item in result.plan.operations if item.operation is OperationType.UPDATE_DRAFT_PICK_OWNERSHIP)
        self.assertEqual(operation.values["expected_owner_league_team_id"], "team-2")

    def test_missing_ownership_evidence(self):
        result = self.service(FakeTradeRepository(evidence(player_ownership={}))).execute(command())
        self.assertEqual(result.code, TradeResultCode.MISSING_EVIDENCE)

    def test_stale_ownership(self):
        changed = {"player-1": AssetOwnership("player-1", "league-1", "team-2")}
        result = self.service(FakeTradeRepository(evidence(player_ownership=changed))).execute(command())
        self.assertEqual(result.code, TradeResultCode.STALE_OWNERSHIP)

    def test_dry_run_performs_no_writes(self):
        repo = FakeTradeRepository(evidence())
        self.service(repo).execute(command())
        self.assertEqual(repo.write_count, 0)

    def test_live_execution_requires_database_transaction_and_never_loads(self):
        repo = FakeTradeRepository(evidence())
        result = self.service(repo).execute(command(dry_run=False))
        self.assertEqual(result.code, TradeResultCode.DATABASE_TRANSACTION_REQUIRED)
        self.assertEqual(repo.load_count, 0)
        self.assertEqual(repo.write_count, 0)


if __name__ == "__main__":
    unittest.main()
