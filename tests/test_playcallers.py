import unittest

from league_ai.playcallers import PLAYCALLERS, note


class PlaycallersTest(unittest.TestCase):
    def test_all_teams_and_notes(self):
        self.assertEqual(len(PLAYCALLERS[2026]), 32)
        self.assertIn("MIA", note(2026, "LAC", {"MIA": {"pass_rate": 0.58}}))
        self.assertIn("58% pass rate", note(2026, "LAC", {"MIA": {"pass_rate": 0.58}}))
        self.assertIn("first time", note(2026, "SEA"))
        self.assertEqual(note(2026, "KC"), "plays called by Andy Reid (HC)")
        self.assertIsNone(note(2019, "KC"))


if __name__ == "__main__":
    unittest.main()
