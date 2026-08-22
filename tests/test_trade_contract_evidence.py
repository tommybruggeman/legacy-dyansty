from copy import deepcopy
from decimal import Decimal
import unittest
import pandas as pd

from services.trade_contract_evidence import TradeContractEvidenceError,TradeContractEvidenceService
from tests.test_contract_reads import Client
from engine.scoring_engine import roster_with_scores


class TradeContractEvidenceTests(unittest.TestCase):
    def test_active_and_expired_normalized_evidence(self):
        rows=TradeContractEvidenceService(Client()).load_trade_contract_evidence("l1",{"p1":"t1","p3":"t1"});by={x.player_id:x for x in rows}
        self.assertEqual(by["p1"].operational_salary,Decimal("11.00"));self.assertEqual(by["p1"].years_remaining,1)
        self.assertEqual(by["p2"].years_remaining,2);self.assertEqual(by["p2"].future_2027_salary,Decimal("22.00"))
        self.assertEqual(by["p3"].agreement_status,"expired");self.assertIsNone(by["p3"].operational_salary);self.assertEqual(by["p3"].years_remaining,0)
        self.assertEqual(by["p3"].roster_classification,"rostered_contract_expired");self.assertEqual(by["p2"].roster_classification,"active_off_roster_liability")
        self.assertEqual(by["p1"].provenance["authority"],"normalized_contract_model")

    def test_package_salary_years_future_and_decimal(self):
        service=TradeContractEvidenceService(Client());rows=service.load_trade_contract_evidence("l1");by={x.player_id:x for x in rows}
        impact=service.calculate_trade_contract_impact((by["p1"],by["p3"]),(by["p2"],))
        self.assertEqual(impact.outgoing_salary,Decimal("11.00"));self.assertEqual(impact.incoming_salary,Decimal("21.00"))
        self.assertEqual(impact.outgoing_years_profile,(1,0));self.assertEqual(impact.incoming_years_profile,(2,));self.assertEqual(impact.incoming_future_commitments[2027],Decimal("22.00"))

    def test_duplicates_and_missing_assets_block(self):
        service=TradeContractEvidenceService(Client())
        with self.assertRaises(TradeContractEvidenceError):service.get_trade_package_contracts("l1",["p1","p1"])
        with self.assertRaises(TradeContractEvidenceError):service.get_trade_package_contracts("l1",["missing"])

    def test_context_exposes_mixed_authorities(self):
        context=TradeContractEvidenceService(Client()).calculation_context("l1",cap_calculation_season=2025)
        self.assertEqual((context.league_season,context.contract_operational_season,context.cap_calculation_season),(2025,2026,2025));self.assertFalse(context.supports_definitive_cap_legality)

    def test_compare_and_legacy_modes_are_explicit_and_read_only(self):
        c=Client();before=deepcopy(c.rows);diagnostics=[]
        normalized=TradeContractEvidenceService(c,mode="compare",diagnostic_sink=diagnostics.append).load_trade_contract_evidence("l1")
        self.assertEqual(len(normalized),3);self.assertTrue(diagnostics);self.assertEqual(c.rows,before)
        legacy=TradeContractEvidenceService(c,mode="legacy",diagnostic_sink=lambda _x:None).load_trade_contract_evidence("l1")
        self.assertEqual(legacy[0].agreement_status,"legacy_unresolved");self.assertEqual(c.rows,before)

    def test_no_database_writes(self):
        c=Client();before=deepcopy(c.rows);service=TradeContractEvidenceService(c)
        rows=service.load_trade_contract_evidence("l1");service.calculate_trade_contract_impact((rows[0],),(rows[1],));self.assertEqual(c.rows,before)

    def test_value_model_keeps_base_value_and_changes_only_contract_inputs(self):
        common={"player":"One","pos":"QB","engine_score":80,"recent_production_score":70,"roster_fit_score":60,"is_rookie":False}
        legacy=roster_with_scores({"rosters":pd.DataFrame([{**common,"salary":10,"years":2}])}).iloc[0]
        normalized=roster_with_scores({"rosters":pd.DataFrame([{**common,"salary":11,"years":1}])}).iloc[0]
        self.assertEqual(legacy["base_value_score"],normalized["base_value_score"]);self.assertEqual(legacy["production_score"],normalized["production_score"])
        self.assertNotEqual(legacy["contract_risk_score"],normalized["contract_risk_score"]);self.assertNotEqual(legacy["trade_value_score"],normalized["trade_value_score"])


if __name__=="__main__":unittest.main()
