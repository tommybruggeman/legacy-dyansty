"""Live NFL game state (score, clock, who has the ball) and each player's position stat line.

The game state comes from ESPN's free public scoreboard, read at most once every
LIVE_TTL seconds per server and shared by every owner. The stat line is built from
the Sleeper weekly stats the box score already reads, so it costs no extra calls.
Shared by the app's game screen and the website's box score.
"""
from __future__ import annotations

import math
from typing import Any

from api import sleeper

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
LIVE_TTL = 15
DONE_TTL = 3600
ESPN_TEAMS = {"WSH": "WAS", "LA": "LAR", "JAC": "JAX"}  # ESPN code -> Sleeper's
REGULAR_WEEKS = 18


def _code(team: dict[str, Any]) -> str:
    code = str(team.get("abbreviation") or "")
    return ESPN_TEAMS.get(code, code)


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_scoreboard(board: Any) -> dict[str, dict[str, Any]]:
    """NFL team -> {state (pre/in/post), quarter, clock, team, opp, home, score, opp_score, possession, red_zone, down}."""
    out: dict[str, dict[str, Any]] = {}
    for event in (board or {}).get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        status = event.get("status") or comp.get("status") or {}
        state = str((status.get("type") or {}).get("state") or "pre")
        sides = comp.get("competitors") or []
        if len(sides) != 2:
            continue
        situation = comp.get("situation") or {}
        ball_id = str(situation.get("possession") or "")
        for me, them in ((sides[0], sides[1]), (sides[1], sides[0])):
            team, opp = _code(me.get("team") or {}), _code(them.get("team") or {})
            if not team:
                continue
            has_ball = state == "in" and bool(ball_id) and ball_id == str((me.get("team") or {}).get("id") or me.get("id") or "")
            out[team] = {
                "state": state,
                "quarter": _int(status.get("period")),
                "clock": str(status.get("displayClock") or ""),
                "detail": str((status.get("type") or {}).get("shortDetail") or ""),
                "team": team,
                "opp": opp,
                "home": me.get("homeAway") == "home",
                "score": _int(me.get("score")),
                "opp_score": _int(them.get("score")),
                "possession": has_ball,
                "red_zone": has_ball and bool(situation.get("isRedZone")),
                "down": str(situation.get("shortDownDistanceText") or situation.get("downDistanceText") or "") if has_ball else "",
            }
    return out


def week_games(season: int, week: int, final: bool) -> dict[str, dict[str, Any]]:
    """NFL team -> game state for one regular-season week. Empty when ESPN can't be read."""
    if not 1 <= week <= REGULAR_WEEKS:
        return {}
    url = f"{ESPN_SCOREBOARD}?seasontype=2&week={week}&dates={season}"
    try:
        return parse_scoreboard(sleeper.get_url(url, ttl=DONE_TTL if final else LIVE_TTL))
    except Exception:
        return {}


def game_line(game: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the app shows for a player's game: the state plus score from his team's side."""
    if not game:
        return None
    keys = ("state", "quarter", "clock", "detail", "opp", "home", "score", "opp_score", "possession", "red_zone", "down")
    return {k: game[k] for k in keys}


def stat_line(stats: dict[str, Any] | None, pos: str) -> list[dict[str, str]]:
    """The position's box score line as [{text, tone}], tone '', 'good' (TDs) or 'bad' (INTs, fumbles).

    QB: completions, passing yards, TD, INT (plus rushing at 10+ yards).
    RB: carries, rushing yards, TD, catches. WR/TE: catches/targets, yards, TD.
    Everyone: fumbles lost when there are any.
    """
    s = stats or {}
    n = lambda key: _int(s.get(key))
    out: list[dict[str, str]] = []
    add = lambda text, tone="": out.append({"text": text, "tone": tone})
    if pos == "QB":
        if not (n("pass_att") or n("rush_att")):
            return []
        add(f"{n('pass_cmp')}/{n('pass_att')}")
        add(f"{n('pass_yd')} YD")
        add(f"{n('pass_td')} TD", "good" if n("pass_td") else "")
        add(f"{n('pass_int')} INT", "bad" if n("pass_int") else "")
        if n("rush_yd") >= 10:
            add(f"{n('rush_yd')} RUSH")
        if n("rush_td"):
            add(f"{n('rush_td')} RUSH TD", "good")
    elif pos == "RB":
        if not (n("rush_att") or n("rec_tgt") or n("rec")):
            return []
        add(f"{n('rush_att')} CAR")
        add(f"{n('rush_yd')} YD")
        if n("rush_td"):
            add(f"{n('rush_td')} TD", "good")
        if n("rec"):
            add(f"{n('rec')} REC {n('rec_yd')} YD")
        if n("rec_td"):
            add(f"{n('rec_td')} REC TD", "good")
    elif pos in ("WR", "TE"):
        if not (n("rec_tgt") or n("rec")):
            return []
        add(f"{n('rec')}/{max(n('rec_tgt'), n('rec'))} REC")
        add(f"{n('rec_yd')} YD")
        if n("rec_td"):
            add(f"{n('rec_td')} TD", "good")
        if n("rush_td"):
            add(f"{n('rush_td')} RUSH TD", "good")
    else:
        return []
    if n("pass_int") and pos != "QB":
        add(f"{n('pass_int')} INT", "bad")
    fumbles = n("fum_lost")
    if fumbles:
        add(f"{fumbles} FUM", "bad")
    return out


QUARTER_MINUTES = 15


def time_left(game: dict[str, Any] | None) -> float | None:
    """Share of regulation still to play (1.0 before kickoff, 0.0 at the end); None without a live clock."""
    if not game or game.get("state") != "in":
        return None
    quarter = int(game.get("quarter") or 0)
    if quarter > 4:
        return 0.0
    try:
        mins, secs = str(game.get("clock") or "0:00").split(":")
        clock = int(mins) + int(secs) / 60
    except ValueError:
        return None
    left = (4 - max(1, quarter)) * QUARTER_MINUTES + min(clock, QUARTER_MINUTES)
    return max(0.0, min(1.0, left / (4 * QUARTER_MINUTES)))


def live_projection(points: float, proj: float | None, status: str, game: dict[str, Any] | None) -> float | None:
    """A starter's projected final: real points once done, points so far plus his projection for the
    game time left while playing (so a hot start raises it and a quiet game lowers it), else the projection."""
    if status == "final" or proj is None:
        return round(points, 2)
    if status != "playing":
        return round(proj, 2)
    left = time_left(game)
    if left is None:  # no clock from the scoreboard: don't let a live total sink below the projection
        return round(max(points, proj), 2)
    return round(points + proj * left, 2)


def win_chance(sides: list[dict[str, Any]], final: bool) -> list[int] | None:
    """Each side's chance to win, from the current score plus what's still projected to come.

    Each unfinished starter's remaining points get a spread of about half his
    remaining projection (plus a little), a free normal approximation with no model run.
    """
    if len(sides) != 2:
        return None
    if final:
        a, b = sides[0].get("score") or 0, sides[1].get("score") or 0
        return [100, 0] if a > b else [0, 100] if b > a else [50, 50]
    means, var = [], 0.0
    for side in sides:
        means.append(float(side.get("projected") or side.get("score") or 0))
        for p in side.get("players") or []:
            if p.get("status") in ("waiting", "playing") and p.get("proj") is not None:
                left = max(0.0, float(p.get("live_proj") if p.get("live_proj") is not None else p["proj"]) - (float(p.get("points") or 0) if p.get("status") == "playing" else 0.0))
                var += (0.5 * left + 1.0) ** 2
    if var <= 0:
        return None
    z = (means[0] - means[1]) / math.sqrt(var)
    a = round(100 * 0.5 * (1 + math.erf(z / math.sqrt(2))))
    a = min(99, max(1, a))
    return [a, 100 - a]
