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
# Weeks the app runs itself (api/own_league.py) answer matchups/bracket reads from here instead of Sleeper.
_overrides: dict[str, Any] = {}


def _fetch(url: str) -> Any:
    response = requests.get(url, timeout=25)
    response.raise_for_status()
    return response.json()


fetch: Callable[[str], Any] = _fetch


def get(path: str, ttl: int = DEFAULT_TTL) -> Any:
    url = f"{BASE}/{path.lstrip('/')}"
    if url in _overrides:
        return _overrides[url]
    return raw(path, ttl)


def raw(path: str, ttl: int = DEFAULT_TTL) -> Any:
    """Sleeper's own answer, ignoring the app's overrides."""
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


def put(path: str, data: Any) -> None:
    _overrides[f"{BASE}/{path.lstrip('/')}"] = data


def drop(prefix: str) -> None:
    """Forget every override under `prefix` (e.g. "league/123/")."""
    start = f"{BASE}/{prefix.lstrip('/')}"
    for url in [u for u in _overrides if u.startswith(start)]:
        del _overrides[url]


def get_url(url: str, ttl: int = DEFAULT_TTL) -> Any:
    """Like get(), for Sleeper endpoints outside /v1 (weekly stats and projections)."""
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and hit[0] > now:
        return hit[1]
    data = fetch(url)
    _cache[url] = (now + ttl, data)
    return data
