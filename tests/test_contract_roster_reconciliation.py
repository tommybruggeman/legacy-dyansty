import unittest

from contract_engine.roster_reconciliation import *


def evidence(**changes):
    base = dict(
        agreement={"id":"a1","league_team_id":"t1","player_id":"p1","sleeper_player_id":"p1","contract_type":"standard"},
        legacy_contract={"id":"l1","player_name":"Player","contract_years_left":1},
        source_obligation={"salary":"1.00"}, contract_owner={"owner_name":"Owner"}, captured_assignment=None,
        sleeper_roster_team=None, canonical_roster_team=None, canonical_player={"current_owner":"Owner"},
        latest_add=None, latest_drop=None, latest_trade=None)
    base.update(changes); return MissingRosterEvidence(**base)


class MissingRosterReconciliationTests(unittest.TestCase):
    def test_legitimate_off_roster_natural_expiration(self):
        row=evidence(canonical_player={"current_owner":None},latest_drop={"status":"complete","league_team_id":"t1"})
        result=classify_missing_roster_contract(row)
        self.assertEqual(result["classification"],NATURAL_EXPIRATION_PENDING)
        self.assertEqual(result["rollover_treatment"],"natural expiration; no dead cap")

    def test_final_year_drop_preserves_liability_until_natural_expiration(self):
        row=evidence(latest_drop={"status":"complete","league_team_id":"t1"})
        result=classify_missing_roster_contract(row)
        self.assertEqual(result["classification"],VALID_FINAL_SEASON_OFF_ROSTER_LIABILITY)
        self.assertEqual(result["free_agent_readiness"],READY_FOR_FREE_AGENT_STAGING)
        self.assertFalse(result["dead_cap_required"])
        self.assertFalse(result["source_correction_required"])

    def test_valid_dead_cap_liability(self):
        result=classify_missing_roster_contract(evidence(dead_cap=({"amount":"1.00"},)))
        self.assertEqual(result["classification"],DROPPED_WITH_VALID_CONTRACT_LIABILITY)
        self.assertEqual(result["free_agent_readiness"],REQUIRES_DEAD_CAP_REVIEW)

    def test_trade_ownership_mismatch(self):
        row=evidence(sleeper_roster_team={"id":"t2"},latest_trade={"id":"trade"})
        self.assertEqual(classify_missing_roster_contract(row)["classification"],TRADED_CONTRACT_MISMATCH)

    def test_historical_capture_gap(self):
        row=evidence(sleeper_roster_team={"id":"t1"})
        self.assertEqual(classify_missing_roster_contract(row)["classification"],CURRENT_ROSTER_CAPTURE_GAP)

    def test_non_roster_contract_right(self):
        row=evidence(non_roster_right_supported=True)
        self.assertEqual(classify_missing_roster_contract(row)["classification"],NON_ROSTER_CONTRACT_RIGHT)

    def test_ambiguous_evidence_requires_decision(self):
        result=classify_missing_roster_contract(evidence(legacy_contract={"id":"l1","player_name":"Player","contract_years_left":2}))
        self.assertEqual(result["classification"],AMBIGUOUS)
        self.assertEqual(result["free_agent_readiness"],REQUIRES_COMMISSIONER_DECISION)

    def test_salary_above_minimum_does_not_imply_dead_cap(self):
        row=evidence(source_obligation={"salary":"2.00"},latest_drop={"status":"complete","league_team_id":"t1"})
        result=classify_missing_roster_contract(row)
        self.assertEqual(result["classification"],VALID_FINAL_SEASON_OFF_ROSTER_LIABILITY)
        self.assertFalse(result["dead_cap_required"])

    def test_early_termination_with_future_obligation_requires_dead_cap_review(self):
        row=evidence(processed_drop={"processed":True},future_obligations=({"season":2026},))
        result=classify_missing_roster_contract(row)
        self.assertEqual(result["classification"],EARLY_TERMINATION_MISSING_DEAD_CAP)
        self.assertTrue(result["dead_cap_required"])

    def test_report_is_read_only_and_fingerprint_stable(self):
        rows=[evidence()]; payload={"contracts":[{"id":"x"}]}
        first=build_missing_roster_reconciliation(rows,payload); second=build_missing_roster_reconciliation(rows,payload)
        self.assertEqual(first["writes_performed"],0)
        self.assertEqual(first["source_fingerprint"],second["source_fingerprint"])
        self.assertEqual(rows[0].agreement["id"],"a1")

    def test_source_change_changes_fingerprint(self):
        first=build_missing_roster_reconciliation([evidence()],{"x":1})
        second=build_missing_roster_reconciliation([evidence()],{"x":2})
        self.assertNotEqual(first["source_fingerprint"],second["source_fingerprint"])


if __name__ == "__main__": unittest.main()
