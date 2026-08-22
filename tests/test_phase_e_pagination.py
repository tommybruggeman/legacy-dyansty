from types import SimpleNamespace
import unittest

from services.strict_pagination import PaginationIntegrityError, complete_rows, exact_count
import random

from services.season_rollover_ui import bounded_review_page, bounded_stable_page


class Query:
    def __init__(self, client):
        self.client, self.filters, self.start, self.end, self.head = client, {}, 0, 499, False
    def select(self, *_args, **kwargs): self.head = bool(kwargs.get("head")); return self
    def eq(self, key, value): self.filters[key] = value; return self
    def order(self, key): self.key = key; return self
    def range(self, start, end): self.start, self.end = start, end; return self
    def execute(self):
        self.client.requests += 1
        rows = [dict(row) for row in self.client.rows if all(row.get(k) == v for k, v in self.filters.items())]
        rows.sort(key=lambda row: row[getattr(self, "key", "id")])
        page_number = self.start // max(1, self.end - self.start + 1)
        if self.client.replay_page == page_number and self.start:
            page = rows[:self.end - self.start + 1]
        elif self.client.early_page == page_number:
            page = []
        else:
            page = rows[self.start:self.end + 1]
        count = None if self.client.missing_count else len(rows) + (1 if self.client.changed_count and self.start else 0)
        return SimpleNamespace(data=[] if self.head else page, count=count)


class Client:
    def __init__(self, rows, *, missing_count=False, changed_count=False, replay_page=None, early_page=None):
        self.rows, self.missing_count, self.changed_count = rows, missing_count, changed_count
        self.replay_page, self.early_page, self.requests = replay_page, early_page, 0
    def table(self, _name): return Query(self)


def rows(size): return [{"id": f"{n:05d}", "league_id": "league-1"} for n in range(size)]


class StrictPaginationTests(unittest.TestCase):
    def test_certification_cardinalities_are_complete_and_bounded(self):
        for size in (1, 10, 32, 100, 2000):
            client = Client(rows(size))
            result, metrics = complete_rows(client, "league_teams", filters={"league_id": "league-1"},
                                            page_size=500, with_metrics=True)
            self.assertEqual(len(result), size)
            self.assertEqual(len({row["id"] for row in result}), size)
            self.assertEqual(metrics.backend_requests, max(1, (size + 499) // 500))
            self.assertLessEqual(metrics.page_size, 500)

    def test_missing_or_changed_count_fails(self):
        with self.assertRaisesRegex(PaginationIntegrityError, "count unavailable"):
            complete_rows(Client(rows(10), missing_count=True), "x")
        with self.assertRaisesRegex(PaginationIntegrityError, "count changed"):
            complete_rows(Client(rows(10), changed_count=True), "x", page_size=5)

    def test_duplicate_replayed_page_and_early_termination_fail(self):
        with self.assertRaisesRegex(PaginationIntegrityError, "Duplicate/replayed"):
            complete_rows(Client(rows(10), replay_page=1), "x", page_size=5)
        with self.assertRaisesRegex(PaginationIntegrityError, "terminated early"):
            complete_rows(Client(rows(10), early_page=1), "x", page_size=5)

    def test_count_only_does_not_download_rows(self):
        client = Client(rows(2000))
        self.assertEqual(exact_count(client, "league_teams", filters={"league_id": "league-1"}), 2000)
        self.assertEqual(client.requests, 1)


class BoundedReviewUITests(unittest.TestCase):
    def setUp(self):
        self.reviews = [{"id": f"r-{n:04d}", "player_id": f"p-{n}", "league_team_id": f"t-{n % 32}",
                         "review_type": "option" if n % 2 else "release",
                         "review_state": "blocked" if n % 7 == 0 else "approved"} for n in range(2000)]

    def test_default_is_exception_only_and_widget_rows_are_bounded(self):
        view = bounded_review_page(self.reviews)
        self.assertEqual(view["total"], 2000)
        self.assertEqual(view["filtered"], 286)
        self.assertEqual(view["displayed"], 25)
        self.assertLessEqual(len(view["rows"]), 25)

    def test_filters_totals_and_navigation_are_deterministic(self):
        first = bounded_review_page(self.reviews, review_type="option", status="all", page=2, page_size=50)
        second = bounded_review_page(list(reversed(self.reviews)), review_type="option", status="all", page=2, page_size=50)
        self.assertEqual(first, second)
        self.assertEqual(first["filtered"], 1000)
        self.assertEqual(first["displayed"], 50)
        searched = bounded_review_page(self.reviews, search="p-1999", status="all")
        self.assertEqual(searched["filtered"], 1)

    def test_repeated_reversed_and_shuffled_inputs_match(self):
        expected = bounded_review_page(self.reviews, status="all", page=17, page_size=50)
        shuffled = list(self.reviews); random.Random(42).shuffle(shuffled)
        self.assertEqual(expected, bounded_review_page(self.reviews, status="all", page=17, page_size=50))
        self.assertEqual(expected, bounded_review_page(list(reversed(self.reviews)), status="all", page=17, page_size=50))
        self.assertEqual(expected, bounded_review_page(shuffled, status="all", page=17, page_size=50))

    def test_first_middle_and_last_page_counts(self):
        first = bounded_review_page(self.reviews, status="all", page=1, page_size=25)
        middle = bounded_review_page(self.reviews, status="all", page=40, page_size=25)
        last = bounded_review_page(self.reviews, status="all", page=80, page_size=25)
        self.assertEqual((first["displayed"], middle["displayed"], last["displayed"]), (25, 25, 25))
        self.assertEqual((first["page"], middle["page"], last["page"]), (1, 40, 80))

    def test_exact_25_and_50_boundaries(self):
        rows25 = self.reviews[:25]; rows50 = self.reviews[:50]
        self.assertEqual(bounded_review_page(rows25, status="all", page_size=25)["pages"], 1)
        self.assertEqual(bounded_review_page(rows50, status="all", page_size=50)["displayed"], 50)
        self.assertEqual(bounded_review_page(rows50, status="all", page=2, page_size=25)["displayed"], 25)

    def test_filtered_below_page_size_and_no_results(self):
        one = bounded_review_page(self.reviews, search="p-1999", status="all", page_size=25)
        none = bounded_review_page(self.reviews, search="does-not-exist", status="all", page_size=25)
        self.assertEqual((one["filtered"], one["displayed"]), (1, 1))
        self.assertEqual((none["filtered"], none["displayed"], none["page"], none["pages"]), (0, 0, 1, 1))

    def test_combined_search_type_and_status(self):
        view = bounded_review_page(self.reviews, search="p-1999", review_type="option",
                                   status="approved", page_size=25)
        self.assertEqual((view["filtered"], view["displayed"]), (1, 1))
        blocked = bounded_review_page(self.reviews, search="p-1999", review_type="option",
                                      status="blocked", page_size=25)
        self.assertEqual(blocked["filtered"], 0)

    def test_exception_only_true_and_false(self):
        exceptions = bounded_review_page(self.reviews, exception_only=True)
        all_rows = bounded_review_page(self.reviews, exception_only=False)
        self.assertEqual(exceptions["filtered"], 286)
        self.assertEqual(all_rows["filtered"], 2000)

    def test_stable_ids_control_identical_record_order(self):
        rows = [{"id":"b","review_type":"same","review_state":"approved"},
                {"id":"a","review_type":"same","review_state":"approved"}]
        view = bounded_review_page(rows, status="all")
        self.assertEqual([row["id"] for row in view["rows"]], ["a", "b"])
        with self.assertRaisesRegex(ValueError, "unique non-empty stable IDs"):
            bounded_review_page([{**rows[0],"id":"a"}, rows[1]], status="all")

    def test_generic_audit_slice_is_stable_and_bounded(self):
        rows = [{"id":f"audit-{n:04d}","created_at":"same"} for n in range(2000)]
        first = bounded_stable_page(rows, page=1, page_size=50)
        reverse = bounded_stable_page(list(reversed(rows)), page=1, page_size=50)
        self.assertEqual(first, reverse)
        self.assertEqual((first["filtered"], first["displayed"]), (2000, 50))

    def test_page_request_validation(self):
        with self.assertRaises(ValueError): bounded_review_page(self.reviews, page=0)
        with self.assertRaises(ValueError): bounded_review_page(self.reviews, page_size=2000)
