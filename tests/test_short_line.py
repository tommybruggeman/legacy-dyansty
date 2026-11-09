import unittest

from api.nfl_live import short_line


class ShortLineTest(unittest.TestCase):
    def text(self, stats, pos):
        return " · ".join(x["text"] for x in short_line(stats, pos))

    def test_rb(self):
        self.assertEqual(self.text({"rush_att": 17, "rush_yd": 57, "rush_td": 1, "rec": 4, "rec_yd": 31}, "RB"), "57 rush · 31 rec · 1 TD")

    def test_qb_adds_all_tds_and_ints(self):
        line = short_line({"pass_att": 37, "pass_yd": 279, "pass_td": 2, "pass_int": 4, "rush_att": 3, "rush_yd": 16}, "QB")
        self.assertEqual([x["text"] for x in line], ["279 pass", "16 rush", "2 TD", "4 INT"])
        self.assertEqual([x["tone"] for x in line], ["", "", "good", "bad"])

    def test_wr_no_zero_td(self):
        self.assertEqual(self.text({"rec_tgt": 10, "rec": 8, "rec_yd": 86}, "WR"), "8 rec · 86 yds")

    def test_no_stats(self):
        self.assertEqual(short_line({}, "TE"), [])


if __name__ == "__main__":
    unittest.main()
