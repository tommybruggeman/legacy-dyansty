"""Short-lived in-memory caches so repeat requests skip database round trips.

Everyone in a league reads the same league-wide rows (row-level security
lets every member read them), so those are keyed by league; anything about
one owner is keyed by their user id. Writes call forget() so the next read
is fresh.
"""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_cache: dict[Any, tuple[float, Any]] = {}
pool = ThreadPoolExecutor(max_workers=8)


def cached(key: Any, seconds: float, load: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = load()
    _cache[key] = (now + seconds, value)
    return value


def forget(kind: str, league_id: Any = None) -> None:
    """Drop every entry of one kind (optionally for one league)."""
    for key in [k for k in _cache if isinstance(k, tuple) and k[0] == kind and (league_id is None or league_id in k)]:
        _cache.pop(key, None)


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clear() -> None:
    _cache.clear()
