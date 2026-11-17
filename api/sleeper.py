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
    parts = path.strip("/").split("/")
    data = _mirrored(path) if parts[0] == "league" and len(parts) > 1 and is_mirror(parts[1]) else fetch(url)
    _cache[url] = (now + ttl, data)
    return data


# ---- Test leagues ----------------------------------------------------------------------------------------------------
# A test copy of a league (public.create_test_league) has no Sleeper league of its own. Its seasons carry a mirror id:
# "mirror-" plus the real Sleeper id with each digit written as a letter (0 -> a ... 9 -> j). Reads under it come from
# the real league, read only, with owner names hidden and no transactions, and everything the app writes for it
# (api/own_league.py overrides) lives under the mirror id, so it can never touch the real league. Scripts that keep
# only the digits of a Sleeper id see an empty id and skip it.
MIRROR = "mirror-"
_TO_LETTERS = str.maketrans("0123456789", "abcdefghij")
_TO_DIGITS = str.maketrans("abcdefghij", "0123456789")


def is_mirror(sleeper_id: Any) -> bool:
    return str(sleeper_id or "").startswith(MIRROR)


def mirror_id(real_id: str) -> str:
    return MIRROR + str(real_id).translate(_TO_LETTERS)


def real_id(sleeper_id: str) -> str:
    return str(sleeper_id)[len(MIRROR):].translate(_TO_DIGITS) if is_mirror(sleeper_id) else str(sleeper_id)


def _mirrored(path: str) -> Any:
    parts = path.strip("/").split("/")
    rest = parts[2:]
    if rest[:1] == ["transactions"] or rest[:1] == ["traded_picks"]:
        return []  # the test league's moves are its own (the app's tables), not the real league's
    data = fetch(f"{BASE}/league/{real_id(parts[1])}" + ("/" + "/".join(rest) if rest else ""))
    if not rest and isinstance(data, dict):
        return {**data, "league_id": parts[1], "name": "Legacy Test", "avatar": None, "previous_league_id": None}
    if rest == ["users"] and isinstance(data, list):
        return [{**u, "display_name": f"Owner {i}", "username": f"owner{i}", "avatar": None, "metadata": {}}
                for i, u in enumerate(data, start=1) if isinstance(u, dict)]
    return data


def clear() -> None:
    _cache.clear()


def put(path: str, data: Any) -> None:
    _overrides[f"{BASE}/{path.lstrip('/')}"] = data


def put_many(prefix: str, data: dict[str, Any]) -> None:
    """Swap every override under `prefix` for `data` ({path under prefix: rows}) in one step, so readers never see a
    half-built set."""
    start = f"{BASE}/{prefix.lstrip('/')}"
    fresh = {f"{start}{path}": rows for path, rows in data.items()}
    for url in [u for u in _overrides if u.startswith(start) and u not in fresh]:
        _overrides.pop(url, None)
    _overrides.update(fresh)


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
