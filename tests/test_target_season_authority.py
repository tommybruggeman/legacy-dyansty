from copy import deepcopy
from decimal import Decimal
import unittest

from season_engine.rollover_service import RolloverAuthorityService
from season_engine.target_authority import (
    CommissionerPolicyService,FreeAgentPublicationService,LeagueRolloverPolicy,
    OffRosterActivePolicy,PolicyValidationError,RosteredExpiredPolicy,
    TargetAuthorityService,AuthorityValidationError,write_path_authority_plan,
)
from tests.test_rollover_authority import rollover_client


def policy(**changes):
    values=dict(league_id="l1",source_season=2025,target_season=2026,version=1,status="draft",
        rostered_expired_policy=RosteredExpiredPolicy.COMMISSIONER_REVIEW_PER_PLAYER.value,
        off_roster_active_policy=OffRosterActivePolicy.RETAIN_LIABILITY_BLOCK_SECOND_AGREEMENT.value,
        free_agent_publication_policy="commissioner_approval",waiver_policy="waiver_first",
        taxi_policy="review",ir_policy="review",dead_cap_policy="early_termination_only",
        early_termination_policy="explicit_event_required",cap_adjustment_policy="season_scoped",
        draft_rookie_policy="locked_until_commissioner_release")
    values.update(changes);return LeagueRolloverPolicy(**values).validated()


class TargetSeasonAuthorityTests(unittest.TestCase):
    def test_policy_is_league_season_scoped_and_fingerprint_deterministic(self):
        a=policy();b=policy();self.assertEqual(a.fingerprint,b.fingerprint);self.assertEqual((a.league_id,a.source_season,a.target_season),("l1",2025,2026))
    def test_activated_policy_is_changed_only_by_superseding_version(self):
        active=policy(status="active",approved_by="commissioner",approved_at="2026-07-29")
        newer=active.supersede(rostered_expired_policy=RosteredExpiredPolicy.OWNER_OPTION_WINDOW.value,extension_deadline="2026-08-15")
        self.assertEqual((active.version,newer.version,newer.status),(1,2,"draft"))
    def test_invalid_combinations_block(self):
        invalid=(
            dict(rostered_expired_policy=RosteredExpiredPolicy.OWNER_OPTION_WINDOW.value,extension_deadline=None),
            dict(dead_cap_policy="natural_expiration"),dict(off_roster_active_policy="cancel_on_roster_absence"),
            dict(rostered_expired_policy=RosteredExpiredPolicy.AUTOMATIC_RELEASE_AT_ROLLOVER.value,taxi_policy=None),
        )
        for change in invalid:
            with self.assertRaises(PolicyValidationError):policy(**change)
    def test_policy_options_and_decision_reduction_do_not_mutate(self):
        client=rollover_client();report=RolloverAuthorityService(client).build_rollover_readiness_report("l1");before=deepcopy(client.rows)
        effects=CommissionerPolicyService().options(108);self.assertEqual(effects[0].automatically_resolved,108)
        result=CommissionerPolicyService().reduce(policy(rostered_expired_policy=RosteredExpiredPolicy.RETAIN_UNCONTRACTED_UNTIL_DEADLINE.value,extension_deadline="2026-08-15"),report.roster_exceptions)
        self.assertEqual(result["resolved_count"],1);self.assertEqual(result["remaining_count"],1);self.assertEqual(client.rows,before)
    def test_publication_is_independent_idempotent_planning_and_writes_disabled(self):
        service=FreeAgentPublicationService(mode="shadow")
        a=service.plan_publication(league_id="l",player_id="p",team_id="t",agreement_id="a",agreement_state="expired",roster_state="unrostered",season=2026,reason="natural_expiration",policy_approved=False)
        b=service.plan_publication(league_id="l",player_id="p",team_id="t",agreement_id="a",agreement_state="expired",roster_state="unrostered",season=2026,reason="natural_expiration",policy_approved=False)
        self.assertEqual(a.idempotency_key,b.idempotency_key);self.assertEqual(a.publication_status,"pending");self.assertIn("commissioner_policy_not_approved",a.blockers)
        self.assertEqual(service.determine_acquisition_eligibility({"publication_status":"published","acquisition_status":"eligible","waiver_status":"locked"}),"waiver_required")
        with self.assertRaises(PermissionError):service.publish()
    def test_active_contract_blocks_publication_and_signing(self):
        item=FreeAgentPublicationService().plan_publication(league_id="l",player_id="p",team_id="t",agreement_id="a",agreement_state="active",roster_state="unrostered",season=2026,reason="off_roster",policy_approved=True)
        self.assertIn("active_contract_conflict",item.blockers);self.assertEqual(item.acquisition_status,"ineligible")
    def test_dead_cap_trusted_zero_and_missing_or_invalid_evidence_are_distinct(self):
        service=TargetAuthorityService();zero=service.plan_dead_cap_initialization("l",2026,["t1","t2"],[],set())
        self.assertTrue(zero.trusted_zero);self.assertEqual(zero.total,Decimal("0"))
        invalid=service.plan_dead_cap_initialization("l",2026,["t1"],[{"team_id":"t1","player_id":"p","contract_agreement_id":"a","season":2026,"amount":"5","source_event_id":"natural","termination_type":"natural_expiration"}],set())
        self.assertFalse(invalid.trusted_zero);self.assertIn("natural_expiration_dead_cap_forbidden",invalid.blockers)
    def test_duplicate_dead_cap_liability_blocks(self):
        row={"team_id":"t1","player_id":"p","contract_agreement_id":"a","season":2026,"amount":"5","source_event_id":"e","termination_type":"early_termination"}
        result=TargetAuthorityService().plan_dead_cap_initialization("l",2026,["t1"],[row,row],{"e"})
        self.assertIn("duplicate_dead_cap_liability",result.blockers)
    def test_cap_validation_requires_all_teams_and_initialized_dead_cap(self):
        service=TargetAuthorityService();dead=service.plan_dead_cap_initialization("l",2026,["t1","t2"],[],set())
        blocked=service.validate_cap("l",2026,["t1","t2"],{"t1":Decimal("10")},{"t1":Decimal("0"),"t2":Decimal("0")},dead,Decimal("227"))
        self.assertEqual(blocked.status,"blocked");self.assertIn("contracts_team_completeness_required",blocked.blockers);self.assertIn("dead_cap_authority_initialization_required",blocked.blockers)
    def test_readiness_stages_never_activate_or_execute(self):
        service=RolloverAuthorityService(rollover_client());report=service.build_rollover_readiness_report("l1")
        self.assertEqual(service.integrate_target_authorities(report,schema_available=False)["status"],"schema_required")
        self.assertEqual(service.integrate_target_authorities(report,schema_available=True,policy=policy(),publication_authority_status="validated",dead_cap_authority_status="initialized",cap_authority_status="validated")["status"],"commissioner_approval_required")
    def test_write_authority_plan_has_no_dual_write(self):
        rows=write_path_authority_plan();self.assertTrue(rows);self.assertTrue(all(not x["dual_write_required"] for x in rows))


if __name__=="__main__":unittest.main()
