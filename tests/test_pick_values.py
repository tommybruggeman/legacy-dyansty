import unittest

from league_ai.pick_values import PickBook, non_increasing, parse

ROWS = [{"pick_label": f"2026 Pick {r}.{s:02d}", "draft_year": 2026, "round": r, "slot": f"{r}.{s:02d}", "value_2qb": v}
        for (r, s), v in zip([(r, s) for r in (1, 2, 3) for s in range(1, 13)], [7977, 6453, 5239, 4268, 3489, 2862, 2356, 1946, 1613, 1341, 1119, 937,
                                                                                 787, 663, 560, 475, 405, 345, 296, 254, 219, 189, 164, 143,
                                                                                 124, 109, 95, 84, 74, 66, 58, 51, 45, 40, 35, 31])]
ROWS += [{"pick_label": "2027 Mid 1st", "draft_year": 2027, "round": 1, "slot": None, "value_2qb": 2216},
         {"pick_label": "2028 Mid 1st", "draft_year": 2028, "round": 1, "slot": None, "value_2qb": 1900}]


class ParseTests(unittest.TestCase):
    def test_every_way_of_writing_a_round(self):
        for label, rnd in [("2027 R1", 1), ("2027 R2", 2), ("2027 R3", 3), ("2027 Round 2", 2), ("2027 2nd", 2), ("2027 first", 1),
                           ("2027 Early 1st", 1), ("2027 1.03", 1), ("2027 Pick 2.07", 2)]:
            self.assertEqual(parse(label)["round"], rnd, label)
            self.assertEqual(parse(label)["year"], 2027, label)

    def test_slot_tier_and_via(self):
        p = parse("2027 R1 (via Hardy)")
        self.assertEqual((p["round"], p["via"]), (1, "Hardy"))
        self.assertEqual(parse("2027 Pick 1.03")["slot"], 3)
        self.assertEqual(parse("2027 Late 2nd")["tier"], "late")


class PickBookTests(unittest.TestCase):
    def setUp(self):
        self.book = PickBook(ROWS, teams=10, rounds=3, season=2026)

    def test_rounds_are_priced_apart(self):
        r1, r2, r3 = (self.book.value(f"2027 R{r}")[0] for r in (1, 2, 3))
        self.assertGreater(r1, r2)
        self.assertGreater(r2, r3)
        self.assertGreater(r3, 0)

    def test_101_beats_102_and_the_top_falls_off_fast(self):
        v = [self.book.slot_value(2027, 1, s) for s in range(1, 11)]
        self.assertEqual(v, sorted(v, reverse=True))
        self.assertGreater(v[0], v[1] * 1.08)
        self.assertLess(v[4], v[0] * 0.6)   # 1.05 is worth well under two thirds of 1.01

    def test_late_picks_keep_real_value(self):
        # the market prices a 2nd near nothing; what 2nds turned into says otherwise
        self.assertGreater(self.book.slot_value(2027, 2, 5), 0.15 * self.book.slot_value(2027, 1, 1))

    def test_later_drafts_are_discounted(self):
        self.assertGreater(self.book.round_value(2027, 1), self.book.round_value(2028, 1))
        self.assertGreater(self.book.round_value(2028, 1), self.book.round_value(2029, 1))

    def test_projected_slot_from_record(self):
        records = {f"t{i}": (i, 8 - i) for i in range(10)}   # t0 is 0-8: worst record picks first
        book = PickBook(ROWS, teams=10, rounds=3, season=2026, records=records)
        worst, _ = book.value("2027 R1", "t0")
        best, _ = book.value("2027 R1", "t9")
        self.assertGreater(worst, book.round_value(2027, 1))
        self.assertLess(best, book.round_value(2027, 1))
        self.assertEqual(book.projected_slot("t0")[0], 1)

    def test_no_round_no_value(self):
        self.assertEqual(self.book.value("a pick")[0], 0.0)

    def test_labels_for_nearest_pick(self):
        labels = dict(self.book.labels())
        self.assertIn("2027 1st", labels)
        self.assertGreater(labels["2027 Early 1st"], labels["2027 Late 1st"])

    def test_non_increasing(self):
        self.assertEqual(non_increasing([4, 5, 3]), [4.5, 4.5, 3])


if __name__ == "__main__":
    unittest.main()


def test_class_strength_moves_a_deep_class_half_way():
    from league_ai.pick_values import PickBook, class_strength

    strength = class_strength({2027: [100.0] * 30, 2028: [50.0] * 30})
    assert round(strength[2027], 3) == 1.333 and round(strength[2028], 3) == 0.667
    flat = PickBook(None, teams=10, rounds=3, season=2026)
    deep = PickBook(None, teams=10, rounds=3, season=2026, class_strength=strength)
    assert abs(deep.slot_value(2027, 1, 1) / flat.slot_value(2027, 1, 1) - (1 + 0.5 * (strength[2027] - 1))) < 1e-9
    assert deep.slot_value(2028, 1, 1) < flat.slot_value(2028, 1, 1)
    assert deep.slot_value(2029, 1, 1) == flat.slot_value(2029, 1, 1)
