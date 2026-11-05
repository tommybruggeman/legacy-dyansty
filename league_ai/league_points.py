"""Every player's weekly points in THIS league's scoring, from our own official stat lines. No model call.

The Front Office used to value players on nflverse's generic full-PPR points. Our league scores its own way (the
rules saved in Settings > League AI > Scoring), and the daily final-scores job keeps our own copy of every finished
week's stat lines (final_stat_lines). Scoring those lines with our engine gives the points owners actually got, so
power ranks, trade values and the stats tools rank players on the league's own scale and don't lean on an outside
source's fantasy points.

Weeks our copy doesn't have yet (the current week before the job saves it) are left out here; callers fall back to
generic PPR only for a player with no league weeks at all.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from scoring.engine import player_points

TABLE = "final_stat_lines"
TTL = 1800  # finished weeks change only with stat corrections, re-read twice an hour at most


def league_scoring(client: Any, league_id: str | None) -> dict[str, Any]:
    """The scoring rules saved for this league in the app ({} when none are saved). Never reads Sleeper."""
    if not league_id:
        return {}
    try:
        from league_ai.settings_store import load_settings

        return dict(load_settings(client, str(league_id)).scoring_settings or {})
    except Exception:
        return {}


def _played(stats: Mapping[str, Any]) -> bool:
    return bool(stats.get("gp") or stats.get("off_snp") or stats.get("def_snp") or stats.get("st_snp"))


def _season_lines(client: Any, season: int) -> dict[int, dict[str, Any]]:
    """week -> {player id -> {stats, pos}} for every week of the season we have saved."""
    def load() -> dict[int, dict[str, Any]]:
        rows = client.table(TABLE).select("week, lines").eq("season", int(season)).execute().data or []
        return {int(r["week"]): dict(r.get("lines") or {}) for r in rows if r.get("week") is not None}
    try:
        from api import cache

        return cache.cached((TABLE + "_season", int(season)), TTL, load)
    except Exception:
        try:
            return load()
        except Exception:
            return {}


def weekly_points(client: Any, league_id: str | None, season: int, ids: Iterable[str],
                  scoring: Mapping[str, Any] | None = None, lines: Mapping[int, Mapping[str, Any]] | None = None) -> dict[str, list[tuple[int, float]]]:
    """player id -> [(week, league points)] for the weeks he played, oldest first. {} when the league has no saved
    scoring rules or we have no saved weeks (the caller then keeps its PPR fallback)."""
    rules = dict(scoring) if scoring is not None else league_scoring(client, league_id)
    if not rules:
        return {}
    weeks = lines if lines is not None else _season_lines(client, season)
    if not weeks:
        return {}
    wanted = {str(i) for i in ids if i}
    out: dict[str, list[tuple[int, float]]] = {}
    for week in sorted(weeks):
        for pid, line in (weeks[week] or {}).items():
            if pid not in wanted or not isinstance(line, Mapping):
                continue
            stats = line.get("stats") or {}
            if not _played(stats):
                continue
            out.setdefault(pid, []).append((week, round(player_points(stats, rules, line.get("pos")), 2)))
    return out


def weeks_saved(client: Any, season: int) -> list[int]:
    return sorted(_season_lines(client, season))
