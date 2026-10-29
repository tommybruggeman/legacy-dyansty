"""The signals the AI didn't have yet, built to be graded before they go live. No model call.

- red zone role: carries inside the 10 and targets inside the 20 per game (nflverse play-by-play), turned into
  expected touchdowns with the league's own TD rate on each, pulled toward the position average until a few games in
- offensive line: how often the team's QBs get sacked per dropback, and how many offensive linemen are listed out or
  doubtful on that week's final injury report (nflverse injuries, published before kickoff)
- backup QB: the team's main passer so far is listed out or doubtful this week, so its receivers catch passes from
  the backup

Everything uses only games before the week, plus that week's pre-game injury report.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

RZ_RUSH_YARDLINE = 10
RZ_TARGET_YARDLINE = 20
PRIOR_GAMES = 3
OL_POSITIONS = {"T", "G", "C", "OT", "OG", "OL", "LT", "RT", "LG", "RG"}
OUT_STATUSES = {"out", "doubtful"}


def _f(v: Any) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


class RedZone:
    """Built from play-by-play rows (dicts) of weeks before this one."""

    def __init__(self, plays: Iterable[Mapping[str, Any]]):
        carries: dict[str, float] = defaultdict(float)
        targets: dict[str, float] = defaultdict(float)
        self.games: dict[str, set[int]] = defaultdict(set)
        td = {"c": [0.0, 0.0], "t": [0.0, 0.0]}
        for p in plays:
            yl = _f(p.get("yardline_100")) or 99.0
            wk = int(_f(p.get("week")))
            for key in ("rusher_player_id", "receiver_player_id"):
                if p.get(key):
                    self.games[str(p[key])].add(wk)
            if _f(p.get("rush")) and p.get("rusher_player_id") and yl <= RZ_RUSH_YARDLINE:
                carries[str(p["rusher_player_id"])] += 1
                td["c"][0] += 1
                td["c"][1] += _f(p.get("rush_touchdown"))
            if _f(p.get("pass_attempt")) and p.get("receiver_player_id") and yl <= RZ_TARGET_YARDLINE:
                targets[str(p["receiver_player_id"])] += 1
                td["t"][0] += 1
                td["t"][1] += _f(p.get("pass_touchdown"))
        self.td_carry = td["c"][1] / td["c"][0] if td["c"][0] else 0.35
        self.td_target = td["t"][1] / td["t"][0] if td["t"][0] else 0.2
        self.xtd = {g: (carries[g] * self.td_carry + targets[g] * self.td_target) / len(self.games[g]) for g in self.games if self.games[g]}

    def expected_tds(self, gsis: str | None, pos_avg: float) -> float | None:
        """Red-zone touchdowns per game from his role, pulled toward pos_avg with PRIOR_GAMES games of weight."""
        if not gsis or gsis not in self.xtd:
            return None
        n = len(self.games[gsis])
        return (self.xtd[gsis] * n + pos_avg * PRIOR_GAMES) / (n + PRIOR_GAMES)


def ol_out(injury_rows: Iterable[Mapping[str, Any]], season: int, week: int) -> dict[str, int]:
    """team -> offensive linemen listed out or doubtful on that week's report."""
    out: dict[str, int] = defaultdict(int)
    for r in injury_rows:
        if int(_f(r.get("season"))) != season or int(_f(r.get("week"))) != week:
            continue
        if str(r.get("position") or "").upper() in OL_POSITIONS and str(r.get("report_status") or "").lower() in OUT_STATUSES:
            out[str(r.get("team") or "")] += 1
    return dict(out)


def qb_out(injury_rows: Iterable[Mapping[str, Any]], season: int, week: int, main_qb: Mapping[str, str]) -> set[str]:
    """Teams whose main passer so far (team -> gsis id) is listed out or doubtful this week."""
    listed = {str(r.get("gsis_id")) for r in injury_rows if int(_f(r.get("season"))) == season and int(_f(r.get("week"))) == week
              and str(r.get("report_status") or "").lower() in OUT_STATUSES}
    return {team for team, g in main_qb.items() if g in listed}


def main_passers(stat_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """team -> the QB with the most pass attempts in these rows."""
    att: dict[tuple[str, str], float] = defaultdict(float)
    for r in stat_rows:
        if str(r.get("position") or "").upper() == "QB" and r.get("team"):
            att[(str(r["team"]), str(r.get("gsis_id")))] += _f(r.get("attempts"))
    best: dict[str, tuple[float, str]] = {}
    for (team, g), a in att.items():
        if a > best.get(team, (-1.0, ""))[0]:
            best[team] = (a, g)
    return {t: g for t, (_, g) in best.items()}


def sack_rate(stat_rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, float], float]:
    """team -> sacks per dropback its QBs took (pulled toward the league), and the league rate."""
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in stat_rows:
        if str(r.get("position") or "").upper() == "QB" and r.get("team"):
            a = acc[str(r["team"])]
            a[0] += _f(r.get("attempts")) + _f(r.get("sacks"))
            a[1] += _f(r.get("sacks"))
    tot = [sum(v[0] for v in acc.values()), sum(v[1] for v in acc.values())]
    lg = tot[1] / tot[0] if tot[0] else 0.065
    return {t: (v[1] + lg * 120) / (v[0] + 120) for t, v in acc.items()}, lg
