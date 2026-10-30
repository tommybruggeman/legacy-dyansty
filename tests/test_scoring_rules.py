import unittest

from scoring.rules import KNOWN, rows_from_rules, rules_from_rows


class ScoringRulesTests(unittest.TestCase):
    def test_round_trip_keeps_only_rules_worth_points(self):
        saved = {"rec": 1, "pass_td": 4, "pass_yd": 0.04, "sack": 0, "idp_tkl": 0.5}
        self.assertEqual(rules_from_rows(rows_from_rules(saved)), {"rec": 1, "pass_td": 4, "pass_yd": 0.04, "idp_tkl": 0.5})

    def test_unknown_saved_key_shows_under_other(self):
        rows = rows_from_rules({"idp_tkl": 0.5})
        self.assertEqual([r for r in rows if r["group"] == "Other"], [{"group": "Other", "rule": "idp tkl", "key": "idp_tkl", "points": 0.5}])

    def test_every_catalog_rule_shows_even_when_unused(self):
        self.assertEqual(len(rows_from_rules({})), len(KNOWN))

    def test_bad_points_ignored(self):
        self.assertEqual(rules_from_rows([{"key": "rec", "points": "x"}, {"key": "", "points": 3}, {"key": "rec_td", "points": 6.0}]), {"rec_td": 6})


if __name__ == "__main__":
    unittest.main()
