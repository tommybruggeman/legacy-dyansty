"""NFL stat lines from ESPN's free public game feed, in the stat keys our scoring engine reads.

The live watcher (scripts/live_watcher.py) reads ESPN's scoreboard every few seconds and only downloads a
game's full summary when something happened in it (a new last play, a score or state change). Each summary's
box score becomes per-player stat lines keyed by our player ids (Sleeper ids for now), so scoring/engine.py can
score them with the league's own rules. Nothing here is tied to a fantasy platform: ESPN supplies raw NFL stats,
and the id map comes from public crosswalks.

Kept behind this one module so the raw-stats source can be swapped (or backed up by nflverse) without
touching scoring, lineups or the app.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ESPN_TEAMS = {"WSH": "WAS", "LA": "LAR", "JAC": "JAX"}  # ESPN code -> ours

# ESPN box score key -> our stat key. "a/b" and "a-b" keys are split into two values first.
KEYS = {
    "passing": {"completions": "pass_cmp", "passingAttempts": "pass_att", "passingYards": "pass_yd", "passingTouchdowns": "pass_td",
                "interceptions": "pass_int", "sacks": "pass_sack", "sackYardsLost": "pass_sack_yds"},
    "rushing": {"rushingAttempts": "rush_att", "rushingYards": "rush_yd", "rushingTouchdowns": "rush_td", "longRushing": "rush_lng"},
    "receiving": {"receptions": "rec", "receivingYards": "rec_yd", "receivingTouchdowns": "rec_td", "receivingTargets": "rec_tgt",
                  "longReception": "rec_lng"},
    "fumbles": {"fumbles": "fum", "fumblesLost": "fum_lost", "fumblesRecovered": "fum_rec"},
    "kicking": {"fieldGoalsMade": "fgm", "fieldGoalAttempts": "fga", "extraPointsMade": "xpm", "extraPointAttempts": "xpa", "longFieldGoalMade": "fgm_lng"},
    "kickReturns": {"kickReturns": "kr", "kickReturnYards": "kr_yd", "kickReturnTouchdowns": "kr_td"},
    "puntReturns": {"puntReturns": "pr", "puntReturnYards": "pr_yd", "puntReturnTouchdowns": "pr_td"},
    "interceptions": {"interceptions": "idp_int", "interceptionYards": "idp_int_ret_yd", "interceptionTouchdowns": "idp_int_td"},
    "defensive": {"totalTackles": "idp_tkl", "soloTackles": "idp_tkl_solo", "sacks": "idp_sack", "tacklesForLoss": "idp_tkl_loss",
                  "passesDefended": "idp_pass_def", "QBHits": "idp_qb_hit", "defensiveTouchdowns": "idp_def_td"},
}
TWO_POINT = re.compile(r"TWO-POINT CONVERSION ATTEMPT\.\s*(.+?)\s+ATTEMPT SUCCEEDS", re.IGNORECASE)
PASS_2PT = re.compile(r"([A-Z][\w'.-]*\.\s?[\w'.-]+)\s+pass to\s+([A-Z][\w'.-]*\.\s?[\w'.-]+)", re.IGNORECASE)
RUSH_2PT = re.compile(r"([A-Z][\w'.-]*\.\s?[\w'.-]+)\s+(?:rushes|runs|scrambles|left|right|up)", re.IGNORECASE)


def team_code(team: Mapping[str, Any] | None) -> str:
    code = str((team or {}).get("abbreviation") or "")
    return ESPN_TEAMS.get(code, code)


def _num(text: Any) -> float | None:
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _split(key: str, value: Any) -> list[tuple[str, Any]]:
    """'completions/passingAttempts' + '21/30' -> both pairs; 'sacks-sackYardsLost' + '2-14' likewise."""
    for sep in ("/", "-"):
        if sep in key:
            keys, values = key.split(sep), str(value).split(sep)
            if len(keys) == len(values):
                return list(zip(keys, values))
            return []
    return [(key, value)]


def games(board: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Each game on a scoreboard: {id, state (pre/in/post), teams, version}. version changes whenever anything a
    fan would notice changes (last play, score, clock, state), so the watcher downloads a summary only then."""
    out = []
    for event in (board or {}).get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        status = event.get("status") or comp.get("status") or {}
        sides = comp.get("competitors") or []
        situation = comp.get("situation") or {}
        last = (situation.get("lastPlay") or {}).get("id")
        scores = "-".join(str(s.get("score") or 0) for s in sides)
        state = str((status.get("type") or {}).get("state") or "pre")
        out.append({
            "id": str(event.get("id") or ""),
            "state": state,
            "teams": [team_code(s.get("team")) for s in sides],
            "version": f"{state}|{last}|{scores}|{status.get('period')}|{status.get('displayClock')}",
        })
    return [g for g in out if g["id"]]


def _short(name: str) -> str:
    """'J. Allen' / 'J.Allen' / 'Josh Allen' -> 'jallen' (first initial + last name), for matching play text."""
    name = re.sub(r"\s+(jr|sr|ii|iii|iv)\.?$", "", name.strip(), flags=re.IGNORECASE)
    parts = [p for p in re.split(r"[\s.]+", name) if p]
    if len(parts) < 2:
        return name.lower()
    return (parts[0][0] + "".join(parts[1:])).lower().replace("'", "").replace("-", "")


def box_lines(summary: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """ESPN athlete id -> {stats, team, name}: every player's box score line from one game summary,
    plus successful two-point conversions read from the play text."""
    out: dict[str, dict[str, Any]] = {}
    by_short: dict[tuple[str, str], str] = {}
    for team_box in ((summary or {}).get("boxscore") or {}).get("players") or []:
        team = team_code(team_box.get("team"))
        for cat in team_box.get("statistics") or []:
            mapping = KEYS.get(str(cat.get("name") or ""))
            if not mapping:
                continue
            keys = cat.get("keys") or []
            for row in cat.get("athletes") or []:
                athlete = row.get("athlete") or {}
                aid = str(athlete.get("id") or "")
                if not aid:
                    continue
                line = out.setdefault(aid, {"stats": {}, "team": team, "name": str(athlete.get("displayName") or "")})
                for key, value in zip(keys, row.get("stats") or []):
                    for k, v in _split(str(key), value):
                        ours, n = mapping.get(k), _num(v)
                        if ours and n is not None:
                            line["stats"][ours] = line["stats"].get(ours, 0) + n if ours.startswith("idp_") else n
                for name in (athlete.get("shortName"), athlete.get("displayName")):
                    if name:
                        by_short[(team, _short(str(name)))] = aid
    for team, passer, catcher, rusher in two_points(summary):
        for name, key in ((passer, "pass_2pt"), (catcher, "rec_2pt"), (rusher, "rush_2pt")):
            aid = by_short.get((team, _short(name))) if name else None
            if aid:
                stats = out[aid]["stats"]
                stats[key] = stats.get(key, 0) + 1
    for line in out.values():
        s = line["stats"]
        if s.get("pass_att") or s.get("rush_att") or s.get("rec_tgt") or s.get("rec") or s.get("fga") or s.get("xpa"):
            s["gp"] = 1
        if "pass_cmp" in s and "pass_att" in s:
            s["pass_inc"] = max(0.0, s["pass_att"] - s["pass_cmp"])
        if "kr_td" in s or "pr_td" in s:
            s["st_td"] = s.get("kr_td", 0) + s.get("pr_td", 0)
    return out


def _plays(summary: Mapping[str, Any] | None) -> Iterable[tuple[str, Mapping[str, Any]]]:
    drives = (summary or {}).get("drives") or {}
    groups = list(drives.get("previous") or []) + ([drives["current"]] if drives.get("current") else [])
    for drive in groups:
        team = team_code(drive.get("team"))
        for play in drive.get("plays") or []:
            yield team, play


def two_points(summary: Mapping[str, Any] | None) -> list[tuple[str, str | None, str | None, str | None]]:
    """(team, passer, catcher, rusher) for each successful two-point try, from the scoring plays' text."""
    out, seen = [], set()
    for team, play in _plays(summary):
        text = str(play.get("text") or "")
        pid = str(play.get("id") or "")
        m = TWO_POINT.search(text)
        if not m or pid in seen:
            continue
        seen.add(pid)
        attempt = m.group(1)
        throw = PASS_2PT.search(attempt)
        if throw:
            out.append((team, throw.group(1), throw.group(2), None))
        else:
            run = RUSH_2PT.search(attempt)
            if run:
                out.append((team, None, None, run.group(1)))
    return out


def week_lines(summaries: Iterable[Mapping[str, Any]], to_ours: Callable[[str, Mapping[str, Any]], tuple[str, str] | None]) -> tuple[dict[str, dict[str, Any]], int]:
    """Our player id -> {stats, pos} across every game summary. to_ours(espn id, line) -> (our id, position)
    or None when the player can't be matched. Returns the lines and how many ESPN players went unmatched."""
    out: dict[str, dict[str, Any]] = {}
    unmatched = 0
    for summary in summaries:
        for aid, line in box_lines(summary).items():
            hit = to_ours(aid, line)
            if not hit:
                unmatched += 1
                continue
            pid, pos = hit
            out[pid] = {"stats": line["stats"], "pos": pos}
    return out, unmatched


class IdMap:
    """ESPN athlete id -> (our player id, position). Built from Sleeper's public player list (which carries ESPN ids)
    and DynastyProcess's open crosswalk, with a name + team + position match as the last resort."""

    def __init__(self, players: Mapping[str, Mapping[str, Any]], crosswalk: Any = None):
        self.by_espn: dict[str, tuple[str, str]] = {}
        self.by_name: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self.pos: dict[str, str] = {}
        for pid, p in (players or {}).items():
            pos = str(p.get("position") or "").upper()
            self.pos[str(pid)] = pos
            espn = str(p.get("espn_id") or "").split(".")[0]
            if espn and espn not in ("None", "0"):
                self.by_espn[espn] = (str(pid), pos)
            name = str(p.get("full_name") or f"{p.get('first_name') or ''} {p.get('last_name') or ''}").strip()
            if name and p.get("team"):
                self.by_name.setdefault((_norm(name), str(p.get("team"))), []).append((str(pid), pos))
        self.crosswalk = crosswalk

    def __call__(self, espn_id: str, line: Mapping[str, Any]) -> tuple[str, str] | None:
        hit = self.by_espn.get(espn_id)
        if hit:
            return hit
        if self.crosswalk is not None:
            sid = self.crosswalk.sleeper_for_espn(espn_id)
            if sid:
                hit = (str(sid), self.pos.get(str(sid), ""))
                self.by_espn[espn_id] = hit
                return hit
        found = self.by_name.get((_norm(str(line.get("name") or "")), str(line.get("team") or "")))
        if found and len(found) == 1:
            self.by_espn[espn_id] = found[0]
            return found[0]
        return None


def _norm(name: str) -> str:
    name = re.sub(r"\s+(jr|sr|ii|iii|iv|v)\.?$", "", name.strip().lower())
    return re.sub(r"[^a-z]", "", name)
