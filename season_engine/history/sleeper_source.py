from __future__ import annotations

from typing import Any, Mapping, Protocol
import requests

from season_engine.models import LeagueSeason
from .models import SourceBundle


class HistorySource(Protocol):
    """Acquisition-only boundary feeding the canonical history normalizer."""

    def fetch(self, season: LeagueSeason) -> SourceBundle: ...


class DeterministicHistorySource:
    """Explicitly test-only source; construction fails outside a disposable context."""

    def __init__(self, fixtures: Mapping[str, Any], *, disposable: bool = False):
        if not disposable:
            raise RuntimeError("deterministic history source is restricted to disposable tests")
        self._bundle = SourceBundle(
            league=dict(fixtures.get("league") or {}),
            users=tuple(dict(x) for x in fixtures.get("users") or ()),
            rosters=tuple(dict(x) for x in fixtures.get("rosters") or ()),
            matchups_by_week={int(k): tuple(dict(x) for x in v)
                              for k, v in (fixtures.get("matchups_by_week") or {}).items()},
            winners_bracket=tuple(dict(x) for x in fixtures.get("winners_bracket") or ()),
            losers_bracket=tuple(dict(x) for x in fixtures.get("losers_bracket") or ()),
        )

    def fetch(self, season: LeagueSeason) -> SourceBundle:
        if str(self._bundle.league.get("league_id") or "") != str(season.sleeper_league_id):
            raise ValueError("deterministic history fixture does not match season authority")
        return self._bundle


class SleeperHistorySource:
    """Read Sleeper history for one explicitly supplied season authority row."""

    def __init__(self, session: Any = requests, timeout: int = 25):
        self.session, self.timeout = session, timeout

    def fetch(self, season: LeagueSeason) -> SourceBundle:
        if not season.id or not season.sleeper_league_id:
            raise ValueError("Historical source requires an explicit persisted LeagueSeason with Sleeper ID.")
        base = f"https://api.sleeper.app/v1/league/{season.sleeper_league_id}"
        league = self._get(base)
        last_week = int((league.get("settings") or {}).get("last_scored_leg") or 0)
        return SourceBundle(
            league=league,
            users=tuple(self._get(f"{base}/users")),
            rosters=tuple(self._get(f"{base}/rosters")),
            matchups_by_week={week: tuple(self._get(f"{base}/matchups/{week}")) for week in range(1, last_week + 1)},
            winners_bracket=tuple(self._get(f"{base}/winners_bracket")),
            losers_bracket=tuple(self._get(f"{base}/losers_bracket")),
        )

    def _get(self, url: str):
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json() or []
