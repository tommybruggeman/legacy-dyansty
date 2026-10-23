"""Past injury absences and how players looked when they came back, plus how often a listed player plays.

Built nightly from the same nflverse files the stats and injury loaders read:
- nfl_injury_episodes: one row per absence (games the player's team played without him, tied to the
  injury on the reports around it), with his role before it and his first games back.
- nfl_injury_play_rates: how often players with a given game status and final practice status
  actually played that week (recorded a stat), by position and by injury.

The chat compares a returning player with past players at his position who missed a similar stretch
with the same injury, and turns a status into a chance to play from the league's real history.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Iterable, Mapping

from pipeline.nfl_data.common import now_iso, to_float, to_int, to_text

POSITIONS = {"QB", "RB", "WR", "TE"}
RELEVANT_SNAP_PCT, RELEVANT_OPPS = 0.35, 6
NOT_INJURY = re.compile(r"not injury|rest|personal|illness|coach", re.I)
INJURY_ALIASES = {"quadricep": "quad", "quadriceps": "quad", "thigh": "quad", "ribs": "rib", "hip flexor": "hip", "achilles tendon": "achilles",
                  "foot/ankle": "ankle", "toes": "toe", "hamstrings": "hamstring", "abdomen": "abdomen", "oblique": "abdomen", "pectoral": "chest"}
PRACTICE_CODES = {"did not participate in practice": "DNP", "limited participation in practice": "Limited", "full participation in practice": "Full"}
GAME_STATUSES = {"out": "Out", "doubtful": "Doubtful", "questionable": "Questionable"}


def injury_key(text: Any) -> str | None:
    """'Right Knee, Ankle' -> 'knee'. None for blanks and non-injury entries (rest, personal, illness)."""
    raw = str(text or "").strip()
    if not raw or NOT_INJURY.search(raw):
        return None
    if "concussion" in raw.lower():
        return "concussion"
    first = re.split(r"[,;/]| and ", raw.lower())[0]
    first = re.sub(r"\b(left|right|r|l)\b", "", first).strip(" .-")
    return INJURY_ALIASES.get(first, first) or None


def practice_code(text: Any) -> str:
    return PRACTICE_CODES.get(str(text or "").strip().lower(), "")


def game_status(text: Any) -> str:
    return GAME_STATUSES.get(str(text or "").strip().lower(), "")


def _opps(r: Mapping[str, Any]) -> float:
    """Opportunities: carries + targets (QBs: dropbacks + carries)."""
    if str(r.get("position") or "").upper() == "QB":
        return (to_float(r.get("attempts")) or 0) + (to_float(r.get("sacks")) or 0) + (to_float(r.get("carries")) or 0)
    return (to_float(r.get("carries")) or 0) + (to_float(r.get("targets")) or 0)


def _avg(rows: list[Mapping[str, Any]], fn) -> float | None:
    vals = [v for v in (fn(r) for r in rows) if v is not None]
    return round(mean(vals), 3) if vals else None


def _line(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {"games": len(rows), "snap_pct": _avg(rows, lambda r: to_float(r.get("offense_pct"))), "opps": _avg(rows, _opps),
            "ppr": _avg(rows, lambda r: to_float(r.get("fantasy_points_ppr")) or 0.0)}


def _baseline(before: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Up to four games before the absence. The last one is dropped when he clearly left it early (snap share well under the others)."""
    window = list(before[-4:])
    if len(window) >= 3:
        last = to_float(window[-1].get("offense_pct"))
        others = [to_float(r.get("offense_pct")) for r in window[:-1]]
        others = [o for o in others if o is not None]
        if last is not None and others and last < 0.7 * mean(others):
            window = window[:-1]
    return window


def build_episodes(stat_rows: Iterable[Mapping[str, Any]], injury_rows: Iterable[Mapping[str, Any]], *, current_season: int | None = None) -> list[dict[str, Any]]:
    """Absences from games played: a stretch of team games without the player, labelled by the injury on the reports in between.

    stat_rows: nflverse weekly player rows (REG and POST; the transformed nfl_player_stats shape).
    injury_rows: nflverse weekly injury report rows (raw columns, REG and POST).
    An unlabelled gap (benched, healthy scratch, suspension, signed late) is skipped. A gap that is still open
    (no game since) is kept with returned False when it is in the latest season, so the chat can read it.
    """
    stamp = now_iso()
    team_games: dict[str, set[tuple[int, int]]] = defaultdict(set)
    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in stat_rows:
        season, week, team = to_int(r.get("season")), to_int(r.get("week")), to_text(r.get("team"))
        gsis = to_text(r.get("gsis_id"))
        if season is None or week is None or not team or not gsis:
            continue
        team_games[team].add((season, week))
        if str(r.get("position") or "").upper() in POSITIONS:
            games[gsis].append(dict(r, season=season, week=week))
    reports: dict[str, list[tuple[tuple[int, int], str | None, str]]] = defaultdict(list)
    for r in injury_rows:
        gsis, season, week = to_text(r.get("gsis_id")), to_int(r.get("season")), to_int(r.get("week"))
        if not gsis or season is None or week is None:
            continue
        label = injury_key(r.get("report_primary_injury")) or injury_key(r.get("practice_primary_injury"))
        reports[gsis].append(((season, week), label, game_status(r.get("report_status"))))
    latest = current_season or max((s for t in team_games.values() for s, _ in t), default=None)
    ordered = {t: sorted(v) for t, v in team_games.items()}

    def missed_between(a: dict[str, Any], b: dict[str, Any] | None) -> list[tuple[int, int]]:
        start = (a["season"], a["week"])
        team_a = to_text(a.get("team"))
        if b is None:
            return [g for g in ordered.get(team_a, []) if g > start and g[0] <= start[0] + 1]
        end = (b["season"], b["week"])
        if end[0] - start[0] > 1:
            return []  # missed a whole season or more: too different to compare
        team_b = to_text(b.get("team"))
        out = [g for g in ordered.get(team_a, []) if start < g < end and (g[0] == start[0] or team_a == team_b)]
        if team_b != team_a:
            out += [g for g in ordered.get(team_b, []) if start < g < end and g[0] == end[0]]
        return sorted(set(out))

    rows: list[dict[str, Any]] = []
    for gsis, played in games.items():
        played.sort(key=lambda r: (r["season"], r["week"]))
        reps = reports.get(gsis, [])
        for i, a in enumerate(played):
            b = played[i + 1] if i + 1 < len(played) else None
            missed = missed_between(a, b)
            if not missed:
                continue
            start, end = (a["season"], a["week"]), (b["season"], b["week"]) if b else (9999, 0)
            window = [rep for rep in reps if start < rep[0] <= end]
            labels = Counter(rep[1] for rep in window if rep[1])
            if not labels:
                continue
            if b is None and (latest is None or missed[-1][0] < latest):
                continue  # an old open gap: retired, released or out of the league, not an injury still running
            injury = labels.most_common(1)[0][0]
            before = _baseline([g for g in played[: i + 1]])
            after = played[i + 1: i + 4]
            pre, post1, post3 = _line(before), _line(after[:1]), _line(after)
            return_status = next((rep[2] for rep in reversed(window) if b and rep[0] == end), "")
            rows.append({
                "gsis_id": gsis, "sleeper_id": to_text(a.get("sleeper_id")), "player_name": to_text(a.get("player_name")),
                "position": str(a.get("position") or "").upper(), "team": to_text(a.get("team")), "injury": injury,
                "last_game_season": a["season"], "last_game_week": a["week"],
                "return_season": b["season"] if b else None, "return_week": b["week"] if b else None,
                "games_missed": len(missed), "returned": b is not None, "return_game_status": return_status or None,
                "pre_games": pre["games"], "pre_snap_pct": pre["snap_pct"], "pre_opps": pre["opps"], "pre_ppr": pre["ppr"],
                "post1_snap_pct": post1["snap_pct"], "post1_opps": post1["opps"], "post1_ppr": post1["ppr"],
                "post3_games": post3["games"], "post3_snap_pct": post3["snap_pct"], "post3_opps": post3["opps"], "post3_ppr": post3["ppr"],
                "refreshed_at": stamp,
            })
    return rows


def build_play_rates(stat_rows: Iterable[Mapping[str, Any]], injury_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """How often a player listed on the report played that week, by game status and final practice status.

    Scopes: 'all', 'pos:RB' and so on, and 'injury:hamstring' and so on (injuries with at least 30 listings).
    'next:<scope>' rows give how often that tag played the team's following game.
    Played means he recorded a stat that week, which is what a fantasy lineup cares about. Only players with a real
    offensive role count.
    """
    stamp = now_iso()
    played: set[tuple[str, int, int]] = set()
    team_weeks: set[tuple[str, int, int]] = set()
    usage: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in stat_rows:
        season, week, team, gsis = to_int(r.get("season")), to_int(r.get("week")), to_text(r.get("team")), to_text(r.get("gsis_id"))
        if season is None or week is None:
            continue
        if team:
            team_weeks.add((team, season, week))
        if gsis:
            played.add((gsis, season, week))
            usage[(gsis, season)].append(1.0 if (to_float(r.get("offense_pct")) or 0) >= RELEVANT_SNAP_PCT or _opps(r) >= RELEVANT_OPPS else 0.0)

    team_schedule: dict[tuple[str, int], list[int]] = defaultdict(list)
    for team, season, week in sorted(team_weeks):
        team_schedule[(team, season)].append(week)

    def relevant(gsis: str, season: int) -> bool:
        """A real part of his offense this season or last (half his games at 35%+ of snaps or 6+ opportunities), so depth
        players who sit with a minor knock don't drag the rates down."""
        return any(usage.get((gsis, s)) and mean(usage[(gsis, s)]) >= 0.5 for s in (season, season - 1))
    tally: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in injury_rows:
        gsis, season, week, team = to_text(r.get("gsis_id")), to_int(r.get("season")), to_int(r.get("week")), to_text(r.get("team"))
        pos = str(r.get("position") or "").upper()
        if not gsis or season is None or week is None or pos not in POSITIONS or (team, season, week) not in team_weeks:
            continue  # bye weeks and unplayed games say nothing
        if not relevant(gsis, season):
            continue
        label = injury_key(r.get("report_primary_injury")) or injury_key(r.get("practice_primary_injury"))
        if not label:
            continue
        status, practice = game_status(r.get("report_status")), practice_code(r.get("practice_status"))
        hit = 1 if (gsis, season, week) in played else 0
        nxt = next((w for w in team_schedule.get((team, season), []) if w > week), None)
        for scope in ("all", f"pos:{pos}", f"injury:{label}"):
            for key in {(scope, status, practice), (scope, status, "")}:  # practice "" = any practice status
                tally[key][0] += 1
                tally[key][1] += hit
            if nxt is not None:  # early in a week only last week's tag is known: how often did that tag play the team's next game?
                tally[("next:" + scope, status, "")][0] += 1
                tally[("next:" + scope, status, "")][1] += 1 if (gsis, season, nxt) in played else 0
    rows = []
    for (scope, status, practice), (n, hit) in tally.items():
        if "injury:" in scope and n < 30:
            continue
        rows.append({"scope": scope, "game_status": status, "practice": practice, "listings": n, "played": hit, "rate": round(hit / n, 3), "refreshed_at": stamp})
    return rows
