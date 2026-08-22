import unittest

from services.transaction_engine import Contract, TransactionEngine, TransactionEngineError


class TransactionEngineTaxiAuthorityTests(unittest.TestCase):
    def test_taxi_is_half_salary(self):
        contract = Contract("p", "Player", "t", 3, 2, status="Taxi",
                            rookie_draft_assignment_id="board-row")
        self.assertEqual(TransactionEngine().cap_hit_for_contract(contract), 1.5)

    def test_taxi_requires_board_assignment_not_generic_rookie_flag(self):
        contract = Contract("p", "Player", "t", 3, 2, status="Taxi", rookie=True)
        with self.assertRaises(TransactionEngineError):
            TransactionEngine().validate_taxi_eligibility(contract)

    def test_board_provenance_allows_taxi(self):
        contract = Contract("p", "Player", "t", 3, 2, status="Taxi",
                            rookie_draft_assignment_id="board-row")
        TransactionEngine().validate_taxi_eligibility(contract)


if __name__ == "__main__":
    unittest.main()
