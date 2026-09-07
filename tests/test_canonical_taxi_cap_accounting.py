from decimal import Decimal
from pathlib import Path
import unittest

from services.team_roster_state import calculate_team_financials


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "supabase/migrations/20261107_canonical_taxi_cap_accounting.sql").read_text().lower()
AUTH_SQL = (ROOT / "supabase/migrations/20261106_owner_self_service_taxi_authority.sql").read_text().lower()


def roster(cap_hit, *, designation=None, contract_cap_hit=None, team="team-1"):
    row = {"league_team_id": team, "owner_name": "Owner", "cap_hit": cap_hit}
    if designation:
        row["roster_designation"] = designation
    if contract_cap_hit is not None:
        row["contract_cap_hit"] = contract_cap_hit
    return row


class CanonicalTaxiCapAccountingTests(unittest.TestCase):
    def financials(self, rows, adjustments=()):
        return calculate_team_financials(
            rows, adjustments, salary_cap=225, league_team_id="team-1"
        )

    def test_active_twelve_counts_twelve(self):
        self.assertEqual(self.financials([roster("12")])["cap_used"], Decimal("12"))

    def test_taxi_twelve_counts_six_exactly_once(self):
        result = self.financials([
            roster("6", designation="taxi", contract_cap_hit="12")
        ])
        self.assertEqual(result["active_salary"], Decimal("6"))
        self.assertEqual(result["cap_used"], Decimal("6"))
        self.assertNotEqual(result["cap_used"], Decimal("18"))
        self.assertNotEqual(result["cap_used"], Decimal("12"))
        self.assertNotEqual(result["cap_used"], Decimal("0"))

    def test_common_taxi_values(self):
        self.assertEqual(self.financials([roster("0.50", designation="taxi")])["cap_used"], Decimal("0.50"))
        self.assertEqual(self.financials([roster("1.50", designation="taxi")])["cap_used"], Decimal("1.50"))

    def test_ir_adjustment_is_unchanged(self):
        adjustments = [{"league_team_id": "team-1", "adjustment_type": "ir_adjustment", "amount": "-6"}]
        self.assertEqual(self.financials([roster("12", designation="ir")], adjustments)["cap_used"], Decimal("6"))

    def test_dead_cap_is_unchanged(self):
        adjustments = [{"league_team_id": "team-1", "adjustment_type": "dropped_player_charge", "amount": "4"}]
        result = self.financials([roster("12")], adjustments)
        self.assertEqual(result["dead_cap"], Decimal("4"))
        self.assertEqual(result["cap_used"], Decimal("16"))

    def test_retained_salary_postprocessing_contract_is_preserved(self):
        # The established read service subtracts retained salary from the RPC's
        # effective cap_hit; contract_cap_hit remains informational authority.
        self.assertEqual(self.financials([roster("4", designation="taxi", contract_cap_hit="12")])["cap_used"], Decimal("4"))

    def test_sql_preserves_contract_and_exposes_effective_taxi_fields(self):
        for fragment in (
            "'contract_cap_hit'",
            "'normal_annual_charge'",
            "'taxi_charge'",
            "'cap_hit'",
            "taxi.taxi_charge",
            "taxi.contract_year_consumed",
            "ls.season = yr",
            "ta.locked",
            "ta.unlocked_at is null",
        ):
            self.assertIn(fragment, SQL)
        self.assertNotIn("update public.contract_seasons", SQL)
        self.assertNotIn("insert into public.cap_adjustments", SQL)

    def test_rollover_discount_is_season_scoped(self):
        self.assertIn("ls.season = yr", SQL)
        self.assertIn("ta.league_season_id", SQL)

    def test_authorization_slot_and_contract_pause_are_unchanged(self):
        self.assertIn("actor_team_id = tid", AUTH_SQL)
        self.assertIn("taxi team authority required", AUTH_SQL)
        self.assertIn("commissioner', 'admin', 'host", AUTH_SQL)
        self.assertIn("taxi slot is locked for this team and season", AUTH_SQL)
        self.assertIn("contract_year_consumed, locked", AUTH_SQL)
        self.assertIn("false, true", AUTH_SQL)


if __name__ == "__main__":
    unittest.main()
