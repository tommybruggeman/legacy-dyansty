from copy import deepcopy
from decimal import Decimal
import unittest

from gm_assistant.contract_evidence import GMContractEvidenceService
from gm_assistant.interpretation import interpret_question
from gm_assistant.openai_reasoning.models import ReasoningRequest,ReasoningResponse
from gm_assistant.openai_reasoning.validation import validate_reasoning_response
from tests.test_assistant_repositories import make_context
from tests.test_contract_reads import Client


def client():
    c=Client();c.rows["season_roster_assignments"]=[{"league_season_id":"ls25","league_team_id":"t1","sleeper_player_id":"p1"},{"league_season_id":"ls25","league_team_id":"t1","sleeper_player_id":"p3"}];c.rows["free_agents"]=[];return c


class GMContractEvidenceTests(unittest.TestCase):
    def test_active_expired_historical_future_and_roster_states(self):
        rows=GMContractEvidenceService(client()).load("l1");by={x.player_id:x for x in rows}
        self.assertEqual(by["p1"].lifecycle_classification,"ACTIVE_ROSTERED_CONTRACT");self.assertEqual(by["p1"].operational_salary,Decimal("11.00"))
        self.assertEqual(by["p2"].lifecycle_classification,"ACTIVE_OFF_ROSTER_LIABILITY");self.assertEqual(by["p2"].salary_2027,Decimal("22.00"));self.assertEqual(by["p2"].years_remaining,2)
        self.assertEqual(by["p3"].lifecycle_classification,"ROSTERED_CONTRACT_EXPIRED");self.assertIsNone(by["p3"].operational_salary);self.assertEqual(by["p3"].years_remaining,0)
        self.assertEqual(by["p3"].historical_obligations[0]["season"],2025);self.assertEqual(by["p3"].free_agent_publication_status,"not_published")
        self.assertEqual(by["p1"].trade_legality_status,"mixed_season_legality_deferred");self.assertEqual(by["p1"].contract_operational_season,2026)

    def test_missing_agreement_classification_is_safe(self):
        service=GMContractEvidenceService(client());self.assertEqual(service.classify_missing_player(canonical_identity_resolved=True),"NO_NORMALIZED_AGREEMENT");self.assertEqual(service.classify_missing_player(canonical_identity_resolved=False),"IDENTITY_OR_DATA_FAILURE")

    def test_load_is_read_only(self):
        c=client();before=deepcopy(c.rows);GMContractEvidenceService(c).load("l1");self.assertEqual(c.rows,before)

    def test_query_subtypes_are_deterministic(self):
        ctx=make_context(current_season=2025,requested_season=2025)
        cases={"What was his salary in 2025?":"contract_history","What will his contract cost in 2027?":"contract_future","Is he a free agent?":"contract_free_agent_status","Why am I paying him off my roster?":"contract_roster_mismatch"}
        for question,expected in cases.items():self.assertEqual(interpret_question(question,ctx).constraints.get("contract_query_type"),expected)

    def test_validator_blocks_mixed_legality_and_publication_claims(self):
        verified={"contracts":[{"trade_legality_status":"mixed_season_legality_deferred","free_agent_publication_status":"not_published"}]}
        request=ReasoningRequest("r","l1","t1","trade_evaluation","","Can I afford it?",verified_facts=verified,allowed_fact_refs=["answer.direct_answer"],validation_constraints={"authoritative_numbers":["2025","2026"]})
        bad=ReasoningResponse("factual_explanation","This is cap legal and he is a free agent.",facts_used=["answer.direct_answer"])
        validation=validate_reasoning_response(request,bad);self.assertFalse(validation.ok);self.assertIn("mixed_season_legality_claim",validation.errors);self.assertIn("unsupported_free_agent_publication",validation.errors)
        good=ReasoningResponse("factual_explanation","The 2026 contract terms can be analyzed, but cap legality is deferred.",facts_used=["answer.direct_answer"],limitations=["League and cap authority remain 2025."])
        self.assertTrue(validate_reasoning_response(request,good).ok)


if __name__=="__main__":unittest.main()
