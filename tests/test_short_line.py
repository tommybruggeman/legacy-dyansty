import unittest

from api.nfl_live import short_line


class ShortLineTest(unittest.TestCase):
    def text(self, stats, pos):
        return ", ".join(x["text"] for x in short_line(stats, pos))

    def test_wr_catches_and_yards(self):
        self.assertEqual(self.text({"rec_tgt": 7, "rec": 5, "rec_yd": 49, "rec_td": 1}, "WR"), "5 REC, 49 YDS, TD")
        self.assertEqual(self.text({"rec_tgt": 10, "rec": 8, "rec_yd": 86}, "TE"), "8 REC, 86 YDS")

    def test_rb_total_yards(self):
        self.assertEqual(self.text({"rush_att": 20, "rush_yd": 88, "rush_td": 1, "rec_tgt": 4, "rec": 3, "rec_yd": 22, "rec_td": 1}, "RB"),
                         "110 YDS, 2 TD")

    def test_qb_yards_scores_and_mistakes(self):
        line = short_line({"pass_cmp": 22, "pass_att": 37, "pass_yd": 279, "pass_td": 2, "pass_int": 1, "rush_att": 5,
                           "rush_yd": 31, "fum_lost": 1}, "QB")
        self.assertEqual([x["text"] for x in line], ["279 YDS", "2 TD", "INT", "FUM"])
        self.assertEqual([x["tone"] for x in line], ["", "good", "bad", "bad"])

    def test_qb_rushing_td_is_total(self):
        self.assertEqual(self.text({"pass_att": 30, "pass_yd": 232, "pass_td": 2, "rush_att": 4, "rush_td": 1, "pass_int": 1}, "QB"),
                         "232 YDS, 3 TOT TD, INT")
        self.assertEqual(self.text({"pass_att": 30, "pass_yd": 242, "rush_att": 2, "rush_td": 1}, "QB"), "242 YDS, TD")

    def test_no_stats(self):
        self.assertEqual(short_line({}, "TE"), [])


if __name__ == "__main__":
    unittest.main()
