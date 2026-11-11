"""Every team's lineup as it is SET this week, for the Assistant GM (get_lineups tool).

The rest of the GM's lineup info is the model's best lineup; this is what each owner actually started. Rosters, taxi
and IR come from our own roster state. Starters come from the app's saved lineups (league_lineups) once the league
runs its own weeks, and from Sleeper's matchup rows before that (the only place lineups are set until then).

Pure functions here; api/front_office.py loads the data and caches it.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from api.lineups import FITS, label

BAD_STATUS = {"out", "ir", "injured reserve", "pup", "suspended", "doubtful"}
SWAP_EDGE = 1.5  # projected points a bench player must beat a starter by to be called out as a better option


def _status(info: Mapping[str, Any]) -> str:
    return str(info.get("injury_status") or "").strip()


def player_line(pid: str, *, names: Mapping[str, str], info: Mapping[str, Mapping[str, Any]], proj: Mapping[str, float],
                bye_teams: set[str], locked: set[str], designation: Mapping[str, str]) -> dict[str, Any]:
    row = info.get(pid) or {}
    team = str(row.get("team") or "")
    flags = []
    if designation.get(pid):
        flags.append("taxi, can't play" if designation[pid] == "taxi" else "IR, can't play")
    if team and team in bye_teams:
        flags.append("bye")
    if _status(row):
        flags.append(_status(row))
    if pid in locked:
        flags.append("game started")
    return {"player": names.get(pid) or str(row.get("full_name") or pid), "pos": str(row.get("position") or ""), "nfl": team or None,
            "proj": round(float(proj[pid]), 1) if pid in proj else None, "flags": ", ".join(flags) or None}


def can_score(pid: str, info: Mapping[str, Mapping[str, Any]], bye_teams: set[str], designation: Mapping[str, str]) -> bool:
    row = info.get(pid) or {}
    return not designation.get(pid) and str(row.get("team") or "") not in bye_teams and _status(row).lower() not in BAD_STATUS


def team_lineup(*, slots: list[str], starters: list[str | None], roster: Iterable[str], names: Mapping[str, str],
                info: Mapping[str, Mapping[str, Any]], proj: Mapping[str, float], bye_teams: set[str], locked: set[str],
                designation: Mapping[str, str], bench_size: int) -> dict[str, Any]:
    """One team's set lineup: starters by slot, top bench options, who can't play, and problems worth fixing."""
    roster = [str(p) for p in roster]
    line = lambda pid: player_line(pid, names=names, info=info, proj=proj, bye_teams=bye_teams, locked=locked, designation=designation)  # noqa: E731
    started = {p for p in starters if p}
    rows, problems = [], []
    for slot, pid in zip(slots, starters):
        if not pid:
            rows.append({"slot": label(slot), "player": None, "flags": "EMPTY slot, scores 0"})
            problems.append(f"{label(slot)} slot is empty")
            continue
        rows.append({"slot": label(slot), **line(pid)})
        if not can_score(pid, info, bye_teams, designation):
            problems.append(f"{rows[-1]['player']} ({label(slot)}) is {rows[-1]['flags']}")
    bench_ids = [p for p in roster if p not in started and not designation.get(p)]
    bench_ids.sort(key=lambda p: proj.get(p, -1.0), reverse=True)
    # Better options on the bench: an unlocked starter whose slot an unlocked, playable bench player fits and out-projects.
    used: set[str] = set()
    swaps = []
    for slot, pid in zip(slots, starters):
        if pid and pid in locked:
            continue
        base = proj.get(pid, 0.0) if pid and can_score(pid, info, bye_teams, designation) else 0.0
        fits = FITS.get(slot, {slot})
        pick = next((b for b in bench_ids if b not in used and b not in locked and str((info.get(b) or {}).get("position") or "") in fits
                     and can_score(b, info, bye_teams, designation) and proj.get(b, 0.0) - base >= SWAP_EDGE), None)
        if pick:
            used.add(pick)
            swaps.append({"slot": label(slot), "start": line(pick)["player"], "over": line(pid)["player"] if pid else "empty slot",
                          "gain": round(proj.get(pick, 0.0) - base, 1)})
    total = sum(proj.get(p, 0.0) for p in started if can_score(p, info, bye_teams, designation))
    return {
        "starters": rows,
        "projected_total": round(total, 1) if proj else None,
        "bench": [line(p) for p in bench_ids[:bench_size]],
        "cant_play_taxi_ir": [f"{names.get(p) or p} ({'taxi' if designation[p] == 'taxi' else 'IR'})" for p in roster if designation.get(p)],
        "problems": problems,
        "better_bench_options": swaps,
    }
