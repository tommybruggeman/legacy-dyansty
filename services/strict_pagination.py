from __future__ import annotations

"""One fail-closed PostgREST pagination contract for authoritative collections."""

from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 500


class PaginationIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class PageMetrics:
    expected_rows: int
    fetched_rows: int
    backend_requests: int
    page_size: int


def _scoped_query(client: Any, table: str, columns: str, filters: Mapping[str, Any], *, head: bool = False):
    query = client.table(table).select(columns, count="exact", head=head)
    for key, value in filters.items():
        query = query.eq(key, value)
    return query


def exact_count(client: Any, table: str, *, filters: Mapping[str, Any] | None = None) -> int:
    response = _scoped_query(client, table, "id", filters or {}, head=True).execute()
    count = getattr(response, "count", None)
    if count is None:
        raise PaginationIntegrityError(f"Exact count unavailable for {table}.")
    try:
        value = int(count)
    except (TypeError, ValueError):
        raise PaginationIntegrityError(f"Invalid exact count for {table}.") from None
    if value < 0:
        raise PaginationIntegrityError(f"Invalid exact count for {table}.")
    return value


def complete_rows(client: Any, table: str, columns: str = "*", *,
                  filters: Mapping[str, Any] | None = None, order_key: str = "id",
                  page_size: int = DEFAULT_PAGE_SIZE, with_metrics: bool = False):
    if not 1 <= int(page_size) <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    scoped = dict(filters or {})
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    expected: int | None = None
    requests = 0
    start = 0
    while True:
        response = (_scoped_query(client, table, columns, scoped)
                    .order(order_key).range(start, start + page_size - 1).execute())
        requests += 1
        raw_count = getattr(response, "count", None)
        if raw_count is None:
            raise PaginationIntegrityError(f"Exact count unavailable for {table}.")
        try:
            current_count = int(raw_count)
        except (TypeError, ValueError):
            raise PaginationIntegrityError(f"Invalid exact count for {table}.") from None
        if current_count < 0:
            raise PaginationIntegrityError(f"Invalid exact count for {table}.")
        if expected is None:
            expected = current_count
        elif current_count != expected:
            raise PaginationIntegrityError(f"Exact count changed during paginated read of {table}.")
        page = list(getattr(response, "data", None) or [])
        for row in page:
            if not isinstance(row, Mapping) or order_key not in row or row[order_key] is None:
                raise PaginationIntegrityError(f"Stable order key {order_key!r} missing from {table}.")
            value = row[order_key]
            if value in seen:
                raise PaginationIntegrityError(
                    f"Duplicate stable order key; Duplicate/replayed page in {table}."
                )
            seen.add(value)
            rows.append(dict(row))
        if len(rows) > expected:
            raise PaginationIntegrityError(f"Paginated read of {table} returned more rows than its exact count.")
        if len(rows) == expected:
            break
        if not page:
            raise PaginationIntegrityError(
                f"Paginated read of {table} terminated early; ended before its exact count."
            )
        start += len(page)
    metrics = PageMetrics(expected or 0, len(rows), requests, int(page_size))
    return (rows, metrics) if with_metrics else rows
