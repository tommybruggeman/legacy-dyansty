import unittest

from api.nfl_live import short_line


class ShortLineTest(unittest.TestCase):
    def text(self, stats, pos):
        return ", ".join(x["text"] for x in short_line(stats, pos))

    def test_wr_keeps_targets(self):
        self.assertEqual(self.text({"rec_tgt": 7, "rec": 5, "rec_yd": 49, "rec_td": 1}, "WR"), "5/7 REC, 49 YDS, TD")
        self.assertEqual(self.text({"rec_tgt": 10, "rec": 8, "rec_yd": 86}, "TE"), "8/10 REC, 86 YDS")

    def test_rb_carries_and_catches(self):
        self.assertEqual(self.text({"rush_att": 20, "rush_yd": 88, "rush_td": 1, "rec_tgt": 4, "rec": 3, "rec_yd": 22, "rec_td": 1}, "RB"),
                         "20 CAR, 88 YDS, 3/4 REC, 2 TD")

    def test_qb_and_fumbles_last(self):
        line = short_line({"pass_cmp": 22, "pass_att": 37, "pass_yd": 279, "pass_td": 2, "pass_int": 1, "rush_att": 5,
                           "rush_yd": 31, "fum_lost": 1}, "QB")
        self.assertEqual([x["text"] for x in line], ["22/37", "279 YDS", "31 RUSH", "2 TD", "INT", "FUM"])
        self.assertEqual([x["tone"] for x in line], ["", "", "", "good", "bad", "bad"])

    def test_no_stats(self):
        self.assertEqual(short_line({}, "TE"), [])


if __name__ == "__main__":
    unittest.main()
