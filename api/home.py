"""Home tab: one page that changes with the calendar.

In season it leads with your game (game day, Thu-Mon) or last week's recap
(Tue-Wed); in the fantasy playoffs it shows the bracket. In the offseason
it is your rookie-deal decisions until the rollover (June 30), then draft
prep. Everything here comes from data the app already loads, so Home never
calls the AI; its Front Office card shows the week's saved brief line.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from services.standings_rules import TOP_FIVE_POINTS, WIN_POINTS

ROLLOVER_MONTH, ROLLOVER_DAY = 6, 30
ROSTER_MAX = 22
GAME_DAYS = {3, 4, 5, 6, 0}  # Thu-Mon (Monday=0)
ROUND_NAMES = {1: "Semifinal", 2: "Final"}
EASTERN = ZoneInfo("America/New_York")
# nflverse team codes that differ from Sleeper's.
NFLVERSE_TEAMS = {"LA": "LAR"}


def _f(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def phase(today: date, league_status: str | None, week: int, playoff_start: int | None) -> str:
    """game_day, recap, playoffs, decisions (offseason until the rollover) or draft_prep."""
    if (league_status or "") == "in_season":
        if playoff_start and week >= playoff_start:
            return "playoffs"
        return "game_day" if today.weekday() in GAME_DAYS else "recap"
    if (today.month, today.day) <= (ROLLOVER_MONTH, ROLLOVER_DAY):
        return "decisions"
    return "draft_prep"


def rollover_deadline(today: date) -> date:
    return date(today.year, ROLLOVER_MONTH, ROLLOVER_DAY)


def my_game(games: list[dict[str, Any]], my_roster_id: int | None) -> dict[str, Any] | None:
    """My game as me vs them, or None on a bye."""
    for game in games:
        sides = game.get("teams") or []
        mine = [t for t in sides if t.get("roster_id") == my_roster_id]
        if mine:
            them = next((t for t in sides if t is not mine[0]), None)
            return {"matchup_id": game.get("matchup_id"), "me": mine[0], "them": them}
    return None


def standing_points_earned(game: dict[str, Any] | None) -> int:
    """Standing points from one finished game: a win plus a Top 5 score."""
    if not game or not game.get("me"):
        return 0
    me, them = game["me"], game.get("them") or {}
    return WIN_POINTS * (me["score"] > them.get("score", 0)) + TOP_FIVE_POINTS * bool(me.get("top5"))


def bracket(matches: list[dict[str, Any]], names: dict[int, str], points: dict[tuple[int, int], float], playoff_start: int) -> list[dict[str, Any]]:
    """Sleeper's winners bracket as rounds of games with names and scores.

    `points` maps (round, roster_id) to that roster's score in the round's week.
    """
    rounds: dict[int, list[dict[str, Any]]] = {}
    for m in matches:
        rnd = int(m.get("r") or 0)
        if not rnd or m.get("p") not in (None, 1):  # keep the title path, not placement games
            continue

        def side(roster_id: Any) -> dict[str, Any] | None:
            if not isinstance(roster_id, int):
                return None
            return {"roster_id": roster_id, "name": names.get(roster_id) or f"Roster {roster_id}", "score": points.get((rnd, roster_id))}

        rounds.setdefault(rnd, []).append({"a": side(m.get("t1")), "b": side(m.get("t2")), "winner": m.get("w")})
    last = max(rounds) if rounds else 0
    return [{"round": r, "week": playoff_start + r - 1, "label": "Final" if r == last else ROUND_NAMES.get(r, f"Round {r}"), "games": games}
            for r, games in sorted(rounds.items())]


def rookie_decisions(decisions: list[dict[str, Any]], roster: Iterable[dict[str, Any]], ppg: dict[str, float]) -> list[dict[str, Any]]:
    """My rollover decisions with the player's name, position, salary and PPG."""
    by_id = {_text(r.get("sleeper_player_id")): r for r in roster}
    out = []
    for d in decisions:
        pid = _text(d.get("player_id"))
        row = by_id.get(pid, {})
        status = _text(d.get("decision_status"))
        choice = {"recontract": "keep", "decline": "walk", "commissioner_review": "review"}.get(_text(d.get("owner_choice")))
        out.append({
            "decision_id": _text(d.get("id")),
            "player_id": pid,
            "name": _text(row.get("player_name")) or pid,
            "pos": _text(row.get("pos")),
            "salary": _f(row.get("contract_cap_hit") or row.get("cap_hit")),
            "ppg": ppg.get(pid),
            "choice": choice,
            "open": status in {"waiting_for_owner", "recontract_invalid"} and not d.get("locked_at"),
            "status": status,
        })
    return sorted(out, key=lambda r: (-r["salary"], r["name"]))


def rookie_deals_ending(roster: Iterable[dict[str, Any]], team_id: str, ppg: dict[str, float]) -> list[dict[str, Any]]:
    """Before the commissioner opens decisions: my rookie deals in their final year."""
    out = []
    for r in roster:
        if _text(r.get("league_team_id")) != team_id or not r.get("is_rookie") or int(r.get("contract_years_left") or 0) > 1:
            continue
        pid = _text(r.get("sleeper_player_id"))
        out.append({"decision_id": None, "player_id": pid, "name": _text(r.get("player_name")) or pid, "pos": _text(r.get("pos")),
                    "salary": _f(r.get("contract_cap_hit") or r.get("cap_hit")), "ppg": ppg.get(pid), "choice": None, "open": False, "status": "not_open"})
    return sorted(out, key=lambda r: (-r["salary"], r["name"]))


def coming_off_books(roster: Iterable[dict[str, Any]], team_id: str) -> dict[str, Any]:
    rows = sorted(
        ({"name": _text(r.get("player_name")), "player_id": _text(r.get("sleeper_player_id")), "pos": _text(r.get("pos")),
          "salary": _f(r.get("contract_cap_hit") or r.get("cap_hit"))}
         for r in roster if _text(r.get("league_team_id")) == team_id and int(r.get("contract_years_left") or 0) <= 1),
        key=lambda r: -r["salary"],
    )
    return {"total": round(sum(r["salary"] for r in rows), 2), "players": rows}


def who_has_money(summaries: list[dict[str, Any]], season_index: int = 1, limit: int = 4,
                  roster_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Owners with the most cap space in a season (next season by default)."""
    def space(s: dict[str, Any]) -> float:
        years = s.get("cap_by_year") or []
        return years[season_index]["space"] if len(years) > season_index else s.get("cap_space", 0)

    ranked = sorted(summaries, key=lambda s: -space(s))
    counts = roster_counts or {}
    return [{"name": s["owner_name"], "space": space(s), "is_me": bool(s.get("is_me")), "team_id": s.get("team_id"),
             "open_spots": max(0, ROSTER_MAX - counts[s["team_id"]]) if s.get("team_id") in counts else None}
            for s in ranked[:limit]]


def upcoming_picks(picks: list[dict[str, Any]]) -> dict[str, Any]:
    if not picks:
        return {"year": None, "picks": []}
    year = min(p["year"] for p in picks)
    return {"year": year, "picks": [p for p in picks if p["year"] == year]}


# ---- pictures and live player status ------------------------------------------

AVATAR_URL = "https://sleepercdn.com/avatars/thumbs/{}"
GAME_LENGTH_HOURS = 3.5


def avatars(users: list[dict[str, Any]], rosters: list[dict[str, Any]]) -> dict[int, str | None]:
    """roster_id -> the owner's Sleeper picture (their team picture first)."""
    by_user = {}
    for u in users:
        team_pic = _text((u.get("metadata") or {}).get("avatar"))
        by_user[u.get("user_id")] = team_pic or (AVATAR_URL.format(u["avatar"]) if u.get("avatar") else None)
    return {r["roster_id"]: by_user.get(r.get("owner_id")) for r in rosters if r.get("roster_id") is not None}


def kickoffs(games: Iterable[dict[str, Any]]) -> dict[str, datetime]:
    """NFL team -> kickoff (UTC) from nfl_games rows (gameday + gametime are US Eastern)."""
    out: dict[str, datetime] = {}
    for g in games:
        day, clock = _text(g.get("gameday")), _text(g.get("gametime")) or "13:00"
        try:
            start = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=EASTERN).astimezone(timezone.utc)
        except ValueError:
            continue
        for team in (_text(g.get("home_team")), _text(g.get("away_team"))):
            if team:
                out[NFLVERSE_TEAMS.get(team, team)] = start
    return out


def player_status(team: str, starts: dict[str, datetime], now: datetime) -> str:
    """bye, waiting, playing or final, from the kickoff time."""
    start = starts.get(team)
    if start is None:
        return "bye"
    if now < start:
        return "waiting"
    return "playing" if now < start + timedelta(hours=GAME_LENGTH_HOURS) else "final"


def lineup(row: dict[str, Any] | None, info: dict[str, dict[str, Any]], starts: dict[str, datetime], now: datetime) -> list[dict[str, Any]]:
    """One side's starters with live points, status and kickoff, in lineup order."""
    if not row:
        return []
    points = row.get("starters_points") or []
    out = []
    for index, pid in enumerate(row.get("starters") or []):
        pid = _text(pid)
        if not pid or pid == "0":
            out.append({"player_id": None, "name": "Empty slot", "pos": "", "team": "", "points": 0.0,
                        "status": "empty", "kickoff": None, "injury": None})
            continue
        meta = info.get(pid, {})
        team = _text(meta.get("team")) or (pid if not pid.isdigit() else "")
        start = starts.get(team)
        out.append({
            "player_id": pid,
            "name": _text(meta.get("full_name")) or pid,
            "pos": _text(meta.get("position")) or ("DEF" if not pid.isdigit() else ""),
            "team": team,
            "points": _f(points[index] if index < len(points) else 0),
            "status": player_status(team, starts, now),
            "kickoff": start.isoformat() if start else None,
            "injury": _text(meta.get("injury_status")) or None,
        })
    return out


def lineup_issues(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Starters who are out, doubtful, questionable, on bye, or empty slots, before they play."""
    flagged = []
    for p in players:
        if p["status"] == "empty":
            flagged.append({**p, "issue": "Empty slot"})
        elif p["status"] == "bye":
            flagged.append({**p, "issue": "On bye"})
        elif p["status"] == "waiting" and (p["injury"] or "").lower() in {"out", "ir", "doubtful", "questionable", "pup", "sus"}:
            flagged.append({**p, "issue": p["injury"].capitalize()})
    return flagged


def race(games: list[dict[str, Any]], my_roster_id: int | None, pics: dict[int, str | None]) -> list[dict[str, Any]]:
    """Every team's score this week, best first (the Top 5 race)."""
    everyone = sorted((t for g in games for t in g["teams"]), key=lambda t: t.get("rank") or 99)
    return [{"rank": t.get("rank"), "roster_id": t["roster_id"], "name": t["name"], "score": t["score"],
             "top5": bool(t.get("top5")), "is_me": t["roster_id"] == my_roster_id, "avatar": pics.get(t["roster_id"])}
            for t in everyone]


def standings_table(rows: list[dict[str, Any]], team_ids: dict[int, str], pics: dict[int, str | None], playoff_teams: int, show: int = 6) -> list[dict[str, Any]]:
    """Top of the standings for Home's table; I'm always included."""
    picked = rows[:show]
    me = next((r for r in rows if r.get("is_me")), None)
    if me and me not in picked:
        picked = picked[: show - 1] + [me]
    return [{"rank": r["rank"], "roster_id": r["roster_id"], "name": r["name"], "wins": r.get("wins", 0), "losses": r.get("losses", 0),
             "ties": r.get("ties", 0), "standing_points": r.get("standing_points", 0), "is_me": bool(r.get("is_me")),
             "in": r["rank"] <= playoff_teams, "team_id": team_ids.get(r["roster_id"]), "avatar": pics.get(r["roster_id"])}
            for r in picked]
