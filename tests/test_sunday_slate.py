import unittest
from datetime import datetime, timezone

from api.home import before_sunday_slate


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


class SundaySlateTest(unittest.TestCase):
    def test_week_days(self):
        # 2026-11-03 is a Tuesday; Pacific is UTC-8 in November.
        self.assertTrue(before_sunday_slate(utc(2026, 11, 3, 20)))   # Tue
        self.assertTrue(before_sunday_slate(utc(2026, 11, 6, 3)))    # Thu night (TNF) still counts as before
        self.assertTrue(before_sunday_slate(utc(2026, 11, 8, 17, 59)))  # Sun 9:59 AM PT
        self.assertFalse(before_sunday_slate(utc(2026, 11, 8, 18)))  # Sun 10:00 AM PT
        self.assertFalse(before_sunday_slate(utc(2026, 11, 10, 7)))  # Mon 11 PM PT
        self.assertTrue(before_sunday_slate(utc(2026, 11, 10, 8)))   # Tue 12 AM PT


if __name__ == "__main__":
    unittest.main()
