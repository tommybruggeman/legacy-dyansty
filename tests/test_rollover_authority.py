from copy import deepcopy
from decimal import Decimal
import unittest

from season_engine.rollover_models import CapAuthorityKind
from season_engine.rollover_service import RolloverAuthorityService, requirement_graph, stable_fingerprint
from tests.test_contract_reads import Client


def rollover_client(*, publication=True, complete_cap=True):
    client=Client()
    client.rows["season_roster_assignments"]=[
        {"league_season_id":"ls25","league_team_id":"t1","sleeper_player_id":"p1","roster_status":"active"},
        {"league_season_id":"ls25","league_team_id":"t1","sleeper_player_id":"p3","roster_status":"ir"},
    ]
    client.rows["league_rules"]=[{"league_id":"l1","salary_cap":"225.00"}]
    client.rows["cap_adjustments"]=[{"league_id":"l1","league_team_id":"t1","season":2026,"amount":"2.25","adjustment_type":"manual"}]
    client.rows["dead_cap_ledger"]=[{"league_id":"l1","league_team_id":"t1","season":2026,"dead_cap_amount":"1.75"}]
    if publication:
        client.rows["free_agents"]=[]
    if not complete_cap:
        client.rows["cap_adjustments"]=[{"league_id":"l1","league_team_id":"t1","amount":"2.25"}]
    return client


class MissingPublicationClient(Client):
    def table(self,name):
        if name=="free_agents": raise RuntimeError("relation does not exist")
        return super().table(name)


class RolloverAuthorityTests(unittest.TestCase):
    def test_mixed_authority_is_explicit_and_no_authority_is_inferred(self):
        report=RolloverAuthorityService(rollover_client()).build_rollover_readiness_report("l1")
        self.assertEqual((report.context.source_league_season,report.context.contract_operational_season,report.context.cap_authority_season,report.context.target_league_season),(2025,2026,2025,2026))
        self.assertTrue(any("mixed_authority" in warning for warning in report.context.warnings))

    def test_projected_cap_uses_normalized_salary_and_decimal_precision(self):
        cap=RolloverAuthorityService(rollover_client()).build_rollover_readiness_report("l1").projected_caps[0]
        self.assertEqual(cap.active_contract_salary,Decimal("32.00"));self.assertEqual(cap.cap_adjustments,Decimal("2.25"));self.assertEqual(cap.dead_cap,Decimal("1.75"))
        self.assertEqual(cap.total_committed_salary,Decimal("36.00"));self.assertEqual(cap.available_cap,Decimal("189.00"));self.assertTrue(cap.complete)

    def test_incomplete_adjustment_scope_blocks_legality(self):
        report=RolloverAuthorityService(rollover_client(complete_cap=False)).build_rollover_readiness_report("l1")
        self.assertEqual(report.cap_policy.target_kind,CapAuthorityKind.BLOCKED);self.assertFalse(report.projected_caps[0].complete)
        self.assertIn("cap_adjustment_target_season_authority_ambiguous",report.blockers)

    def test_publication_dimensions_are_independent_and_missing_authority_blocks(self):
        service=RolloverAuthorityService(rollover_client())
        state=service.publication_state("p",contract_unbound=True,unrostered=True,row={"published":False,"acquisition_eligible":True,"waiver_locked":True},authority_available=True)
        self.assertFalse(state.published);self.assertTrue(state.acquisition_eligible);self.assertTrue(state.waiver_locked)
        client=MissingPublicationClient();client.rows.update(rollover_client().rows)
        report=RolloverAuthorityService(client).build_rollover_readiness_report("l1")
        self.assertIn("free_agent_publication_authority_absent",report.blockers)

    def test_expired_is_not_auto_published_or_released_and_taxi_ir_is_preserved(self):
        report=RolloverAuthorityService(rollover_client()).build_rollover_readiness_report("l1")
        expired=next(x for x in report.roster_exceptions if x.player_id=="p3")
        self.assertEqual(expired.classification,"ROSTERED_EXPIRED_POLICY_UNDEFINED");self.assertEqual(expired.proposed_action,"HOLD_FOR_COMMISSIONER_DECISION")
        self.assertEqual(expired.taxi_or_ir,"ir");self.assertEqual(expired.publication_status,"not_published")

    def test_active_off_roster_liability_is_retained_pending_review(self):
        report=RolloverAuthorityService(rollover_client()).build_rollover_readiness_report("l1")
        item=next(x for x in report.roster_exceptions if x.player_id=="p2")
        self.assertEqual(item.classification,"ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED")
        self.assertEqual(item.proposed_action,"RETAIN_ORIGINAL_TEAM_LIABILITY_PENDING_REVIEW")
        self.assertTrue(item.evidence["drop_does_not_terminate"]);self.assertFalse(item.evidence["natural_expiration_dead_cap"])

    def test_requirement_graph_is_ordered_acyclic_and_marks_irreversible_steps(self):
        graph=requirement_graph();seen=set()
        for node in graph:
            self.assertTrue(set(node.prerequisites)<=seen);seen.add(node.node_id)
        self.assertTrue(next(x for x in graph if x.node_id=="rollover_execution").irreversible)

    def test_fingerprints_are_deterministic_and_report_is_read_only(self):
        client=rollover_client();before=deepcopy(client.rows);service=RolloverAuthorityService(client)
        first=service.build_rollover_readiness_report("l1");second=service.build_rollover_readiness_report("l1")
        self.assertEqual((first.source_fingerprint,first.plan_fingerprint),(second.source_fingerprint,second.plan_fingerprint))
        self.assertEqual(first.writes_performed,0);self.assertEqual(client.rows,before);self.assertEqual(stable_fingerprint({"b":1,"a":2}),stable_fingerprint({"a":2,"b":1}))

    def test_page_and_write_paths_remain_deferred(self):
        report=RolloverAuthorityService(rollover_client()).build_rollover_readiness_report("l1")
        self.assertTrue(all(not row["safe_before_rollover"] for row in report.page_readiness))
        self.assertTrue(any(row["authority_conflict"] for row in report.write_path_readiness))
        self.assertEqual(report.context.readiness_status,"blocked")


if __name__=="__main__":unittest.main()
