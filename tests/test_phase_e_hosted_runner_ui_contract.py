import importlib.util
from pathlib import Path
import unittest

from services.season_rollover_ui import bounded_review_page


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/phase_e_pagination_integration/run_phase_e_hosted_certification.py"


class HostedRunnerUIContractTests(unittest.TestCase):
    def test_runner_uses_strict_paginator_for_ui_population(self):
        source = RUNNER.read_text()
        self.assertIn('reviews, review_metrics = complete_rows(service, TABLE', source)
        self.assertIn('review_metrics.backend_requests!=4', source)
        self.assertNotIn('.eq("league_id",home_league).order("id").range(0,1999)', source)

    def test_runner_fixture_matches_application_review_fields(self):
        source = RUNNER.read_text()
        self.assertIn("player_id text not null", source)
        self.assertIn("review_state text not null", source)
        self.assertNotIn("review_status text not null", source)

    def test_hosted_shape_has_expected_deterministic_slice(self):
        reviews = [{"id":f"r-{n:04d}","player_id":f"player-{n}","review_type":"option" if n%2 else "release",
                    "review_state":"blocked" if n%7==0 else "approved"} for n in range(1,2001)]
        ui = bounded_review_page(reviews, status="exceptions", page=1, page_size=25)
        ui2 = bounded_review_page(list(reversed(reviews)), status="exceptions", page=1, page_size=25)
        self.assertEqual(ui, ui2)
        self.assertEqual(ui["total"], 2000)
        self.assertEqual(ui["filtered"], 285)
        self.assertEqual(ui["displayed"], 25)


if __name__ == "__main__":
    unittest.main()
