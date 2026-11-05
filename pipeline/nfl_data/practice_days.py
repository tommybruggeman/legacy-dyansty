"""Day-by-day practice participation (DNP / Limited / Full) -> nfl_practice_days.

The weekly practice table (nfl_practice_reports) keeps one row per player per week: the last
practice of the week. That is enough for history but not for "did he practice Wednesday?", and the
current week is missing until a team's first report lands. This loader keeps every report day.

Two independent, free sources, so neither one owns the data:

* ``nflverse``: the NFL's official injury report as nflverse republishes it. Each refresh of the
  season file holds every player's latest practice line for the week. Runs several times a day, so
  each refresh becomes one dated row (the report day is worked out from when the file was rebuilt and
  the team's game day). Official, but it trails the real report by up to a day.
* ``espn_news``: ESPN's injury notes ("was limited at Wednesday's practice"). Usually up the same
  evening, so it fills today's line until the official one arrives.

Readers (league_ai.injury_outlook.practice_week) prefer the official line on any day both have. Nothing here comes from Sleeper.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from league_ai.nfl_data import team_code
from pipeline.nfl_data.common import now_iso, to_int, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

EASTERN = ZoneInfo("America/New_York")
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K"}
# Teams post the day's report after practice, usually 4-6pm Eastern. A file rebuilt before this
# hour still holds the previous day's report.
REPORT_POSTED_HOUR_ET = 17
# Report days before a game: Wed-Fri for Sunday (and Monday/Saturday: Thu-Sat, Tue-Thu) games,
# Mon-Wed for a Thursday game, Tue-Thu for a Friday game.
def report_window(gameday: date) -> tuple[int, int]:
    return (1, 3) if gameday.weekday() in (3, 4) else (2, 4)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

OFFICIAL = {
    "did not participate in practice": "DNP",
    "limited participation in practice": "Limited",
    "full participation in practice": "Full",
}


def practice_code(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return OFFICIAL.get(text) or {"dnp": "DNP", "limited": "Limited", "lp": "Limited", "full": "Full", "fp": "Full"}.get(text)


def parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime

        stamp = parsedate_to_datetime(value)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def report_day(built_at: datetime) -> date:
    """The practice day a file rebuilt at ``built_at`` reflects (Eastern calendar)."""
    local = built_at.astimezone(EASTERN)
    return local.date() if local.hour >= REPORT_POSTED_HOUR_ET else local.date() - timedelta(days=1)


def upcoming_games(games: Iterable[Mapping[str, Any]], season: int) -> dict[str, list[tuple[date, int]]]:
    """Team -> [(gameday, week)] for the season's regular and post season games."""
    out: dict[str, list[tuple[date, int]]] = {}
    for g in games:
        if to_int(g.get("season")) != season or not g.get("gameday"):
            continue
        try:
            day = date.fromisoformat(str(g["gameday"])[:10])
        except ValueError:
            continue
        week = to_int(g.get("week"))
        if week is None:
            continue
        for side in ("home_team", "away_team"):
            if g.get(side):
                out.setdefault(team_code(g[side]), []).append((day, week))
    for team in out:
        out[team].sort()
    return out


def game_for_report(schedule: Mapping[str, list[tuple[date, int]]], team: Any, day: date) -> tuple[date, int] | None:
    """The game a report on ``day`` is for, when ``day`` is one of that game's report days."""
    for gameday, week in schedule.get(team_code(team), []):
        lo, hi = report_window(gameday)
        if lo <= (gameday - day).days <= hi:
            return gameday, week
    return None


def _row(*, gsis: str, season: int, week: int, day: date, gameday: date, status: str, source: str, name: Any, pos: Any, team: Any,
         sleeper_id: Any, injury: Any = None, game_status: Any = None, note: Any = None, stamp: str) -> dict[str, Any]:
    return {
        "gsis_id": gsis,
        "season": season,
        "week": week,
        "practice_date": day.isoformat(),
        "day": DAY_ABBR[day.weekday()],
        "days_to_game": (gameday - day).days,
        "source": source,
        "practice_status": status,
        "game_status": to_text(game_status),
        "injury": to_text(injury),
        "note": (to_text(note) or "")[:400] or None,
        "player_name": to_text(name),
        "position": to_text(pos),
        "team": team_code(team) if team else None,
        "sleeper_id": to_text(sleeper_id),
        "refreshed_at": stamp,
    }


def from_nflverse(records: Iterable[Mapping[str, Any]], crosswalk: Crosswalk, schedule: Mapping[str, list[tuple[date, int]]],
                  season: int, built_at: datetime) -> list[dict[str, Any]]:
    """One dated row per player for the report this nflverse file holds (only players in this week's report)."""
    day = report_day(built_at)
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    for r in records:
        if to_int(r.get("season")) != season or to_text(r.get("position")) not in KEEP_POSITIONS:
            continue
        gsis, team, week = to_text(r.get("gsis_id")), to_text(r.get("team")), to_int(r.get("week"))
        status = practice_code(r.get("practice_status"))
        if not gsis or not team or not status:
            continue
        game = game_for_report(schedule, team, day)
        if not game or game[1] != week:  # no practice that day for this team, or the file still holds last week
            continue
        rows.append(_row(gsis=gsis, season=season, week=week, day=day, gameday=game[0], status=status, source="nflverse",
                         name=r.get("full_name"), pos=r.get("position"), team=team, sleeper_id=crosswalk.sleeper_for_gsis(gsis),
                         injury=r.get("practice_primary_injury") or r.get("report_primary_injury"), game_status=r.get("report_status"), stamp=stamp))
    return rows


# ESPN notes: "did not practice Wednesday", "was limited at Thursday's practice", "practiced fully Friday".
_STATUS_PATTERNS = [
    ("DNP", re.compile(r"\b(did not practice|didn't practice|did not participate|didn't participate|sat out (of )?practice|missed practice|"
                       r"not practicing|absent from practice|held out of practice|was a non-participant|non-participant|\bDNP\b)", re.I)),
    ("Full", re.compile(r"\b(full participant|practiced fully|fully practiced|full practice|full participation|fully participated|practiced in full|"
                        r"full-go|without limitations|no limitations)", re.I)),
    ("Limited", re.compile(r"\b(limited participant|limited practice|limited participation|practiced on a limited basis|limited basis|"
                           r"limited (in|at|during) (practice|\w+'s practice|\w+ practice|\w+'s session)|was limited|remained limited|"
                           r"logged a limited|upgraded to limited|downgraded to limited)", re.I)),
]
_WEEKDAY = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_CLAUSE_SPLIT = re.compile(r"(?<=[.;])\s+|,\s*(?:but|and|before|after|then)\s+|\s+(?:but|before|after|then|while)\s+", re.I)


def practice_mentions(text: str | None, written: date) -> list[tuple[date, str]]:
    """(practice day, status) for each practice line in an ESPN note written on ``written``.

    A weekday in the same clause names the day (the latest such day on or before the note); a clause
    with no weekday counts for the day the note was written.
    """
    out: dict[date, str] = {}
    for clause in _CLAUSE_SPLIT.split(text or ""):
        status = next((code for code, pat in _STATUS_PATTERNS if pat.search(clause)), None)
        if not status:
            continue
        if status == "Limited" and not re.search(r"practic|session|participa|workout", clause, re.I):
            continue  # "limited snaps", "limited role"
        weekday = _WEEKDAY.search(clause)
        if weekday:
            target = WEEKDAYS.index(weekday.group(1).lower())
            day = written - timedelta(days=(written.weekday() - target) % 7)
        else:
            day = written
        out.setdefault(day, status)
    return sorted(out.items())


def from_espn(payload: Mapping[str, Any], crosswalk: Crosswalk, schedule: Mapping[str, list[tuple[date, int]]], season: int,
              sleeper_by_espn: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Dated practice lines read from ESPN's injury notes for players on this week's report."""
    stamp = now_iso()
    sleeper_by_espn = sleeper_by_espn or {}
    rows: list[dict[str, Any]] = []
    for team in payload.get("injuries") or []:
        abbr = to_text(team.get("abbreviation")) or to_text(team.get("displayName"))
        for e in team.get("injuries") or []:
            athlete = e.get("athlete") or {}
            pos = to_text((athlete.get("position") or {}).get("abbreviation"))
            name = to_text(athlete.get("displayName"))
            espn_id = to_text(athlete.get("id")) or to_text(e.get("id"))
            if pos not in KEEP_POSITIONS or not name:
                continue
            nfl_team = to_text((athlete.get("team") or {}).get("abbreviation")) or abbr
            try:
                written = datetime.fromisoformat(str(e.get("date") or "").replace("Z", "+00:00")).astimezone(EASTERN).date()
            except ValueError:
                continue
            mentions = practice_mentions(" ".join(x for x in (to_text(e.get("shortComment")), to_text(e.get("longComment"))) if x), written)
            if not mentions:
                continue
            sleeper_id = sleeper_by_espn.get(espn_id or "") or crosswalk.sleeper_for_espn(espn_id) or crosswalk.sleeper_for_name(name, pos, nfl_team)
            gsis = to_text((crosswalk.record_for_sleeper(sleeper_id) or {}).get("gsis_id")) if sleeper_id else None
            if not gsis:
                continue
            details = e.get("details") or {}
            for day, status in mentions:
                game = game_for_report(schedule, nfl_team, day)
                if not game:
                    continue
                rows.append(_row(gsis=gsis, season=season, week=game[1], day=day, gameday=game[0], status=status, source="espn_news",
                                 name=name, pos=pos, team=nfl_team, sleeper_id=sleeper_id, injury=details.get("type"),
                                 note=e.get("shortComment"), stamp=stamp))
    return rows
