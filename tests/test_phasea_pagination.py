from __future__ import annotations

import unittest
from types import SimpleNamespace

from season_engine.history.repositories import paginated_rows


class Query:
    def __init__(self, client):
        self.client = client
        self.bounds = (0, client.page_size - 1)

    def select(self, *args, **kwargs): return self
    def eq(self, *args): return self
    def order(self, *args): return self

    def range(self, start, end):
        self.bounds = (start, end)
        return self

    def execute(self):
        start, end = self.bounds
        count = None if self.client.missing_count else self.client.exact_count
        if self.client.early_at is not None and start >= self.client.early_at:
            data = []
        elif self.client.repeat_first and start:
            data = self.client.rows[: end - start + 1]
        else:
            data = self.client.rows[start : end + 1]
        return SimpleNamespace(count=count, data=data)


class Client:
    def __init__(self, size, page_size=500, *, exact_count=None, early_at=None,
                 missing_count=False, repeat_first=False):
        self.rows = [{"id": f"{index:08d}"} for index in range(size)]
        self.page_size = page_size
        self.exact_count = size if exact_count is None else exact_count
        self.early_at = early_at
        self.missing_count = missing_count
        self.repeat_first = repeat_first

    def table(self, name): return Query(self)


class PhaseAPaginationTests(unittest.TestCase):
    def test_normal_one_page_and_exact_count_boundary(self):
        self.assertEqual(len(paginated_rows(Client(10), "league_teams")), 10)
        self.assertEqual(len(paginated_rows(Client(500), "league_teams")), 500)

    def test_normal_multi_page_and_four_page_2000(self):
        self.assertEqual(len(paginated_rows(Client(1201), "league_teams")), 1201)
        self.assertEqual(len(paginated_rows(Client(2000), "league_teams")), 2000)

    def test_early_page_and_missing_exact_count_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "ended before"):
            paginated_rows(Client(2000, early_at=500), "league_teams")
        with self.assertRaisesRegex(RuntimeError, "Exact count unavailable"):
            paginated_rows(Client(10, missing_count=True), "league_teams")

    def test_duplicate_page_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate stable order key"):
            paginated_rows(Client(2000, repeat_first=True), "league_teams")

    def test_more_rows_than_exact_count_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "more rows"):
            paginated_rows(Client(500, exact_count=499), "league_teams")


if __name__ == "__main__":
    unittest.main()
