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

from services.matchups_view import _team_points as team_points
from services.standings_rules import TOP_FIVE_POINTS, WIN_POINTS

ROLLOVER_MONTH, ROLLOVER_DAY = 6, 30
ROSTER_MAX = 22
GAME_DAYS = {2, 3, 4, 5, 6, 0}  # Wed-Mon (Monday=0); only Tuesday is last week's recap (Tommy, 2026-11-04)
ROUND_NAMES = {1: "Semifinal", 2: "Final"}
EASTERN = ZoneInfo("America/New_York")
# Home's day flips at midnight Pacific, so Monday night's final screen stays up
# until the West Coast's Monday is over (Tommy, 2026-11-02).
HOME_TZ = ZoneInfo("America/Los_Angeles")
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


def schedule_week(gamedays: dict[int, str], today: date) -> int | None:
    """The NFL week Home shows, from our own schedule: the first week whose last game
    day is today or later. Monday night's week holds until that day is over, then
    the next week starts. None when the schedule is missing or the season is over."""
    for week in sorted(gamedays):
        try:
            last = date.fromisoformat(str(gamedays[week])[:10])
        except ValueError:
            continue
        if last >= today:
            return week
    return None


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


# ---- the bottom of game-day Home -------------------------------------------------
#
# Wed-Sat: GM's Risers (who our projection says beats his normal the most this week).
# Sunday: the week's scoreboard (every Legacy game) until Shotgun watch takes over
# midway through the late games. Shotgun watch follows the lowest projected final
# score (last place shotguns a beer) and everyone close to it: within 10 projected
# points until the late games end, 7 on Sunday night, 5 on Monday. Once last place
# can't change it shows the locked result.

LATE_SLATE_HOUR = 16          # Sunday kickoffs from 4 PM Eastern on are the late games
LATE_SLATE_END_HOUR = 19      # ...and before 7 PM (later is the night game)
SHOTGUN_AFTER = timedelta(hours=1, minutes=45)  # about halftime of the late games
SHOTGUN_WINDOWS = {"late": 10.0, "night": 7.0, "monday": 5.0}
CEILING = 2.5                 # a big game: 2.5x what's left of a projection
RISER_MIN_PROJ, RISER_MIN_GAMES, RISERS_SHOWN = 8.0, 2, 20
OUT_TAGS = {"out", "ir", "doubtful", "pup", "sus", "na"}


def _late_slate(starts: Iterable[datetime], sunday: date) -> list[datetime]:
    out = []
    for s in starts:
        local = s.astimezone(EASTERN)
        if local.date() == sunday and LATE_SLATE_HOUR <= local.hour < LATE_SLATE_END_HOUR:
            out.append(s)
    return sorted(set(out))


def bottom_mode(now: datetime, starts: dict[str, datetime]) -> dict[str, Any]:
    """Which card sits at the bottom of game-day Home: risers, scoreboard or shotgun (with its window).

    Days follow the league's own clock (US Eastern kickoffs), so Sunday is the NFL's Sunday.
    """
    local = now.astimezone(EASTERN)
    day = local.weekday()
    if day in (2, 3, 4, 5):
        return {"mode": "risers"}
    if day == 0 or (day == 1 and now.astimezone(HOME_TZ).weekday() == 0):  # Monday holds until midnight Pacific
        return {"mode": "shotgun", "window": SHOTGUN_WINDOWS["monday"]}
    if day != 6:
        return {"mode": "scoreboard"}
    late = _late_slate(starts.values(), local.date())
    if not late:  # no late games found: fall back to a 6:05 PM Eastern switch, night window after 7:30
        switch = local.replace(hour=18, minute=5, second=0, microsecond=0)
        night = local.replace(hour=19, minute=30, second=0, microsecond=0)
    else:
        switch = late[0].astimezone(EASTERN) + SHOTGUN_AFTER
        night = late[-1].astimezone(EASTERN) + timedelta(hours=GAME_LENGTH_HOURS)
    if local < switch:
        return {"mode": "scoreboard"}
    return {"mode": "shotgun", "window": SHOTGUN_WINDOWS["night" if local >= night else "late"]}


def remaining(status: str, projected: float, kickoff: datetime | None, now: datetime) -> float:
    """What's left of one starter's projection: all of it before kickoff, the unplayed share while playing."""
    if status == "waiting":
        return projected
    if status != "playing" or kickoff is None:
        return 0.0
    played = (now - kickoff).total_seconds() / (GAME_LENGTH_HOURS * 3600)
    return round(max(0.0, projected * (1 - played)), 2)


def team_live(rows: list[dict[str, Any]], info: dict[str, dict[str, Any]], starts: dict[str, datetime], now: datetime,
              projections: dict[str, float]) -> dict[int, dict[str, Any]]:
    """roster_id -> score now, starters left to play, and the projected rest of the week."""
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("roster_id")
        if rid is None:
            continue
        players = lineup(row, info, starts, now)
        left = [p for p in players if p["status"] in ("waiting", "playing")]
        rest = sum(remaining(p["status"], projections.get(p["player_id"] or "", 0.0),
                             starts.get(p["team"]), now) for p in left)
        score = _f(team_points(row))
        out[int(rid)] = {"score": score, "left": len(left), "playing": sum(p["status"] == "playing" for p in left),
                         "rest": round(rest, 2), "projected": round(score + rest, 1)}
    return out


TOP_FIVE = 5


def top5_line(live: dict[int, dict[str, Any]], final: bool = False, places: int = TOP_FIVE) -> dict[str, Any] | None:
    """The score it takes to finish top 5 this week: the 5th-best projected final (the 5th-best score once final)."""
    if len(live) < places:
        return None
    key = "score" if final else "projected"
    finals = sorted((t[key] for t in live.values()), reverse=True)
    return {"line": round(finals[places - 1], 1), "final": final}


def scoreboard(games: list[dict[str, Any]], live: dict[int, dict[str, Any]], my_roster_id: int | None,
               pics: dict[int, str | None]) -> list[dict[str, Any]]:
    """Every Legacy game this week, mine first, each side with its score, starters left and projected final."""
    out = []
    for g in games:
        sides = [{"roster_id": t["roster_id"], "name": t["name"], "score": t["score"], "top5": bool(t.get("top5")),
                  "left": live.get(t["roster_id"], {}).get("left", 0),
                  "projected": live.get(t["roster_id"], {}).get("projected"), "is_me": t["roster_id"] == my_roster_id,
                  "avatar": pics.get(t["roster_id"])} for t in g.get("teams") or []]
        sides.sort(key=lambda s: (not s["is_me"], -s["score"]))
        out.append({"matchup_id": g.get("matchup_id"), "teams": sides, "mine": any(s["is_me"] for s in sides)})
    out.sort(key=lambda g: (not g["mine"], -max((s["score"] for s in g["teams"]), default=0)))
    return out


def _reach(t: dict[str, Any]) -> float:
    """The most a team can still finish with: its score plus CEILING times what's left (its score once frozen)."""
    return t["score"] + CEILING * t["rest"]


def still_alive(live: dict[int, dict[str, Any]]) -> set[int]:
    """Teams that can still finish last.

    A team is safe once some other team is sure to finish below it: that team's best possible
    final (_reach) is under this team's score now. So a frozen owner sitting above someone who is
    also done (or can't climb past them) drops off the watch.
    """
    return {rid for rid, t in live.items()
            if not any(_reach(o) < t["score"] for oid, o in live.items() if oid != rid)}


def shotgun(live: dict[int, dict[str, Any]], names: dict[int, str], pics: dict[int, str | None], my_roster_id: int | None,
            window: float) -> dict[str, Any] | None:
    """Who is on the can (lowest projected final) and everyone who can still finish last within `window` of them.

    Owners who can no longer finish last (see still_alive) are left off. Locked once only one team can
    still finish last: nobody can catch them.
    """
    if len(live) < 2:
        return None
    alive = still_alive(live)
    ranked = sorted(live.items(), key=lambda kv: (kv[1]["projected"], kv[1]["score"]))
    # The lowest projected final can never be safe (anyone sure to finish under it projects lower).
    last_id, last = next((kv for kv in ranked if kv[0] in alive), ranked[0])
    locked = alive == {last_id}

    def row(rid: int, t: dict[str, Any]) -> dict[str, Any]:
        return {"roster_id": rid, "name": names.get(rid) or f"Roster {rid}", "avatar": pics.get(rid), "is_me": rid == my_roster_id,
                "score": t["score"], "projected": t["projected"], "left": t["left"], "playing": t["playing"],
                "frozen": t["left"] == 0, "gap": round(t["projected"] - last["projected"], 1)}

    if locked:  # the closest escapes, by final score
        escapes = sorted((row(rid, t) for rid, t in live.items() if rid != last_id), key=lambda o: o["score"])
        return {"window": window, "locked": True, "last": row(last_id, last), "danger": escapes[:2], "safe": [],
                "needs": None}

    others = [row(rid, t) for rid, t in ranked if rid != last_id and rid in alive]
    lr = row(last_id, last)
    needs = None
    if not lr["frozen"]:
        # how much last place needs to pass the lowest score still in the race right now
        chase = min(others, key=lambda o: o["score"], default=None)
        if chase and chase["score"] >= last["score"]:
            needs = {"points": round(chase["score"] - last["score"] + 0.01, 2), "to_pass": chase["name"],
                     "who": lr["name"], "who_is_me": lr["is_me"], "to_pass_is_me": chase["is_me"]}
    else:
        # last place is done: how much the lowest score below them needs to send them to the can
        below = [o for o in others if o["score"] < last["score"]]
        if below:
            chase = min(below, key=lambda o: o["score"])
            needs = {"points": round(last["score"] - chase["score"] + 0.01, 2), "to_pass": lr["name"],
                     "who": chase["name"], "who_is_me": chase["is_me"], "to_pass_is_me": lr["is_me"]}
    return {
        "window": window,
        "locked": False,
        "last": lr,
        "danger": [o for o in others if o["gap"] <= window][:5],
        "safe": [o for o in others if o["gap"] > window][:2],
        "needs": needs,
    }


def risers(proj: dict[str, float], avg: dict[str, float], games: dict[str, int], info: dict[str, dict[str, Any]],
           owners: dict[str, int], names: dict[int, str], my_roster_id: int | None, last_week: dict[str, float],
           limit: int = RISERS_SHOWN) -> list[dict[str, Any]]:
    """GM's Risers: players whose projection this week beats their season average by the most.

    Only skill players with a real projection (RISER_MIN_PROJ) and at least RISER_MIN_GAMES games this
    season, so one-game wonders and backups don't crowd the list.
    """
    rows = []
    for pid, p in proj.items():
        meta = info.get(pid, {})
        pos = _text(meta.get("position")).upper()
        if _text(meta.get("injury_status")).lower() in OUT_TAGS:
            continue
        if pos not in ("QB", "RB", "WR", "TE") or p < RISER_MIN_PROJ or (games.get(pid) or 0) < RISER_MIN_GAMES or avg.get(pid) is None:
            continue
        rise = round(p - avg[pid], 1)
        if rise <= 0:
            continue
        rid = owners.get(pid)
        rows.append({"player_id": pid, "name": _text(meta.get("full_name")) or pid, "pos": pos, "team": _text(meta.get("team")),
                     "avg": round(avg[pid], 1), "proj": round(p, 1), "rise": rise, "last": last_week.get(pid),
                     "roster_id": rid, "owner": names.get(rid) if rid is not None else None, "is_mine": rid is not None and rid == my_roster_id,
                     "injury": _text(meta.get("injury_status")) or None})
    rows.sort(key=lambda r: (-r["rise"], -r["proj"]))
    return rows[:limit]
