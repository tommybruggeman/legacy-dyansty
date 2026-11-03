"""Stat corrections: what changed between two saves of a week's official stat lines, and whose score it moved.

The daily final-scores job (scripts/final_scores.py) re-saves each finished week until it locks, so NFL stat
corrections land on their own. When a re-save changes a starter's points in a league, it records one correction
for that league (stat_corrections) and pushes it to everyone in the league. Nobody edits scores by hand.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from scoring.engine import player_points

EPS = 0.005


def changed_players(old: Mapping[str, Mapping[str, Any]], new: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Player ids whose stat line differs between the two saves. A line only in the new save isn't a correction (it's
    a line we didn't keep before, like team defenses before they were added); a line that disappeared is."""
    out = set()
    for pid in old:
        a, b = (old.get(pid) or {}).get("stats") or {}, (new.get(pid) or {}).get("stats") or {}
        keys = set(a) | set(b)
        if any(abs(float(a.get(k) or 0) - float(b.get(k) or 0)) > 1e-9 for k in keys):
            out.add(pid)
    return out


def _pts(lines: Mapping[str, Mapping[str, Any]], pid: str, scoring: Mapping[str, Any]) -> float:
    line = lines.get(pid)
    return player_points(line.get("stats"), scoring, line.get("pos")) if line else 0.0


def team_changes(starters: Mapping[Any, Iterable[str]], old: Mapping[str, Mapping[str, Any]], new: Mapping[str, Mapping[str, Any]],
                 scoring: Mapping[str, Any]) -> list[dict[str, Any]]:
    """For each team (starters: team key -> starter ids) whose score moved: its old and new totals and every starter
    whose points changed, with the stats that changed ({key: [old, new]})."""
    moved = changed_players(old, new)
    out = []
    for team, ids in starters.items():
        ids = [str(p) for p in ids if p and str(p) != "0"]
        players = []
        for pid in ids:
            if pid not in moved:
                continue
            a, b = _pts(old, pid, scoring), _pts(new, pid, scoring)
            if abs(a - b) <= EPS:
                continue
            sa, sb = (old.get(pid) or {}).get("stats") or {}, (new.get(pid) or {}).get("stats") or {}
            stats = {k: [float(sa.get(k) or 0), float(sb.get(k) or 0)] for k in sorted(set(sa) | set(sb))
                     if abs(float(sa.get(k) or 0) - float(sb.get(k) or 0)) > 1e-9 and scoring.get(k)}
            players.append({"player_id": pid, "old": round(a, 2), "new": round(b, 2), "stats": stats})
        if players:
            before = round(sum(_pts(old, p, scoring) for p in ids), 2)
            after = round(sum(_pts(new, p, scoring) for p in ids), 2)
            out.append({"team": team, "old": before, "new": after, "players": players})
    return out


def push_body(week: int, teams: int) -> str:
    return f"Stat correction: Week {week} scores changed for {teams} team{'s' if teams != 1 else ''}. Tap to see what changed."


def totals(starters: Mapping[Any, Iterable[str]], lines: Mapping[str, Mapping[str, Any]], scoring: Mapping[str, Any]) -> dict[Any, float]:
    return {team: round(sum(_pts(lines, str(p), scoring) for p in ids if p and str(p) != "0"), 2) for team, ids in starters.items()}


def flips(before: Mapping[Any, float], after: Mapping[Any, float], matchup_of: Mapping[Any, Any], top: int = 5) -> dict[Any, list[str]]:
    """team -> results the correction changed for it: 'win'/'loss' (its head-to-head flipped) and 'top5'/'not_top5'
    (it moved into or out of the week's top scores, worth a standings point)."""
    out: dict[Any, list[str]] = {}
    by_matchup: dict[Any, list[Any]] = {}
    for team, m in matchup_of.items():
        if m is not None:
            by_matchup.setdefault(m, []).append(team)
    for teams in by_matchup.values():
        if len(teams) != 2:
            continue
        a, b = teams
        was = (before.get(a, 0) > before.get(b, 0)) - (before.get(a, 0) < before.get(b, 0))
        now = (after.get(a, 0) > after.get(b, 0)) - (after.get(a, 0) < after.get(b, 0))
        if was != now:
            for team, sign in ((a, now), (b, -now)):
                out.setdefault(team, []).append("win" if sign > 0 else "loss" if sign < 0 else "tie")

    def best(scores: Mapping[Any, float]) -> set[Any]:
        return set(sorted(scores, key=lambda t: -scores[t])[:top])
    was_top, now_top = best(before), best(after)
    for team in now_top - was_top:
        out.setdefault(team, []).append("top5")
    for team in was_top - now_top:
        out.setdefault(team, []).append("not_top5")
    return out
