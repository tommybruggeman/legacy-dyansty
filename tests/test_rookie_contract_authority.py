from decimal import Decimal
import unittest

from season_engine.rookie_contract_authority import (
    ContractRolloverClass,
    classify_contract,
    remaining_initial_years,
    rookie_terms,
    taxi_charge,
)


class RookieContractAuthorityTests(unittest.TestCase):
    def test_ordinary_expiration_has_no_rookie_option(self):
        self.assertEqual(classify_contract(has_board_provenance=False, agreement_expired=True,
            taxi_in_source_season=False, initial_term_exhausted=True, option_consumed=False),
            ContractRolloverClass.ORDINARY_EXPIRATION)

    def test_nfl_rookie_metadata_is_not_board_provenance(self):
        self.assertNotEqual(classify_contract(has_board_provenance=False, agreement_expired=True,
            taxi_in_source_season=False, initial_term_exhausted=True, option_consumed=False),
            ContractRolloverClass.ROOKIE_OPTION_ELIGIBLE)

    def test_authoritative_option_matrix(self):
        self.assertEqual((rookie_terms(1, 1).option_salary, rookie_terms(1, 1).option_years), (Decimal("25"), 1))
        self.assertEqual((rookie_terms(2, 5).option_salary, rookie_terms(2, 5).option_years), (Decimal("15"), 1))
        self.assertEqual((rookie_terms(3, 9).option_salary, rookie_terms(3, 9).option_years), (Decimal("7"), 1))

    def test_round_one_initial_matrix(self):
        self.assertEqual(rookie_terms(1, 5).initial_salary, Decimal("6"))
        self.assertEqual(rookie_terms(1, 7).initial_salary, Decimal("4"))

    def test_taxi_requires_board_provenance_semantically(self):
        self.assertEqual(classify_contract(has_board_provenance=False, agreement_expired=False,
            taxi_in_source_season=True, initial_term_exhausted=False, option_consumed=False),
            ContractRolloverClass.ORDINARY_CONTINUING)

    def test_taxi_pauses_contract_year_and_option(self):
        self.assertEqual(classify_contract(has_board_provenance=True, agreement_expired=True,
            taxi_in_source_season=True, initial_term_exhausted=True, option_consumed=False),
            ContractRolloverClass.ROOKIE_TAXI_PAUSED)
        self.assertEqual(remaining_initial_years(initial_years=1, consumed_years=1,
            taxi_in_source_season=True), 1)

    def test_taxi_charge_is_half_with_currency_rounding(self):
        self.assertEqual(taxi_charge("3"), Decimal("1.50"))
        self.assertEqual(taxi_charge("1"), Decimal("0.50"))

    def test_non_taxi_third_round_exhaustion_gets_option(self):
        self.assertEqual(classify_contract(has_board_provenance=True, agreement_expired=True,
            taxi_in_source_season=False, initial_term_exhausted=True, option_consumed=False),
            ContractRolloverClass.ROOKIE_OPTION_ELIGIBLE)

    def test_taxi_third_round_carries_initial_contract(self):
        self.assertEqual(classify_contract(has_board_provenance=True, agreement_expired=True,
            taxi_in_source_season=True, initial_term_exhausted=True, option_consumed=False),
            ContractRolloverClass.ROOKIE_TAXI_PAUSED)

    def test_first_and_second_round_taxi_retain_two_years(self):
        self.assertEqual(remaining_initial_years(initial_years=2, consumed_years=1,
            taxi_in_source_season=True), 2)

    def test_option_consumed_cannot_create_second_option(self):
        self.assertEqual(classify_contract(has_board_provenance=True, agreement_expired=True,
            taxi_in_source_season=False, initial_term_exhausted=True, option_consumed=True),
            ContractRolloverClass.ORDINARY_EXPIRATION)


if __name__ == "__main__":
    unittest.main()
