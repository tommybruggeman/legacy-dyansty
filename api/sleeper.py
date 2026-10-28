"""Cached reads from Sleeper's public API.

Everyone in the league sees the same Sleeper data, so one in-memory cache
per server instance serves all owners and keeps calls to Sleeper low.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests

BASE = "https://api.sleeper.app/v1"
DEFAULT_TTL = 300

_cache: dict[str, tuple[float, Any]] = {}


def _fetch(url: str) -> Any:
    response = requests.get(url, timeout=25)
    response.raise_for_status()
    return response.json()


fetch: Callable[[str], Any] = _fetch


def get(path: str, ttl: int = DEFAULT_TTL) -> Any:
    url = f"{BASE}/{path.lstrip('/')}"
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    data = fetch(url)
    _cache[url] = (now + ttl, data)
    return data


def clear() -> None:
    _cache.clear()


def get_url(url: str, ttl: int = DEFAULT_TTL) -> Any:
    """Like get(), for Sleeper endpoints outside /v1 (weekly stats and projections)."""
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    data = fetch(url)
    _cache[url] = (now + ttl, data)
    return data
