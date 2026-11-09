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


def short_line(stats: dict[str, Any] | None, pos: str) -> list[dict[str, str]]:
    """The app's compact matchup stat line, ESPN style, with attempts kept in:
    WR/TE '5/7 REC, 49 YDS, TD'; RB '20 CAR, 88 YDS, 3/4 REC, 2 TD'; QB '22/37, 279 YDS, 2 TD, INT'.

    Catches and completions show over targets and attempts. TDs (all kinds) are added up, then INTs, then
    fumbles lost at the end. A count of 1 is just the word ('TD'); zero counts are left out.
    """
    s = stats or {}
    n = lambda key: _int(s.get(key))
    out: list[dict[str, str]] = []
    add = lambda text, tone="": out.append({"text": text, "tone": tone})
    count = lambda k, word: word if k == 1 else f"{k} {word}"
    tds = n("pass_td") + n("rush_td") + n("rec_td")
    catches = lambda: add(f"{n('rec')}/{max(n('rec_tgt'), n('rec'))} REC")
    if pos == "QB":
        if not (n("pass_att") or n("rush_att")):
            return []
        add(f"{n('pass_cmp')}/{n('pass_att')}")
        add(f"{n('pass_yd')} YDS")
        if n("rush_yd") >= 20:
            add(f"{n('rush_yd')} RUSH")
    elif pos == "RB":
        if not (n("rush_att") or n("rec_tgt") or n("rec")):
            return []
        add(f"{n('rush_att')} CAR")
        add(f"{n('rush_yd')} YDS")
        if n("rec_tgt") or n("rec"):
            catches()
    elif pos in ("WR", "TE"):
        if not (n("rec_tgt") or n("rec")):
            return []
        catches()
        add(f"{n('rec_yd')} YDS")
        if n("rush_yd") >= 10:
            add(f"{n('rush_yd')} RUSH")
    else:
        return []
    if tds:
        add(count(tds, "TD"), "good")
    if n("pass_int"):
        add(count(n("pass_int"), "INT"), "bad")
    if n("fum_lost"):
        add(count(n("fum_lost"), "FUM"), "bad")
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


# The live update, fitted on every QB/RB/WR/TE game of 2023-2026 replayed play by play (scoring/live_projection_study.py):
# points so far + time left x (a x scoring pace + b x usage pace + c x score margin + d). Paces lean on the projection
# early and on what has happened as the game goes on. Against "points + projection x time left" on 2026 (fit on
# 2023-25): QB miss 5.56 -> 5.36, WR 3.82 -> 3.76, TE 4.39 -> 4.32, RB about even.
LIVE_FIT = {"QB": (1.1657, 0.0194, -0.2926, -2.118), "RB": (0.7299, 0.1606, -0.015, 0.9468),
            "WR": (0.906, 0.0068, -0.118, 1.0548), "TE": (1.1952, -0.1973, -0.1, 0.4126)}


def opportunities(stats: dict[str, Any] | None, pos: str) -> float:
    """Pass attempts + carries for a QB, targets + carries for everyone else."""
    s = stats or {}
    f = lambda k: float(s.get(k) or 0)  # noqa: E731
    return f("pass_att") + f("rush_att") if pos == "QB" else f("rec_tgt") + f("rush_att")


def live_projection(points: float, proj: float | None, status: str, game: dict[str, Any] | None, pos: str | None = None,
                    stats: dict[str, Any] | None = None, proj_opps: float | None = None) -> float | None:
    """A starter's projected final: real points once done, else the projection before kickoff. While playing:
    points so far plus what's left, from his scoring pace, his usage (pass attempts, carries, targets against his
    projected share) and the score (LIVE_FIT), each leaning on the projection early in the game. proj should be the
    in-game number (week_projections(active=True)). Without the stat line, usage or a clock it falls back to
    points + projection x time left."""
    if status == "final" or proj is None:
        return round(points, 2)
    if status != "playing":
        return round(proj, 2)
    left = time_left(game)
    if left is None:  # no clock from the scoreboard: don't let a live total sink below the projection
        return round(max(points, proj), 2)
    fit = LIVE_FIT.get(str(pos or "").upper())
    if not fit or stats is None or not proj_opps:
        return round(points + proj * left, 2)
    el = 1.0 - left
    pts_rate = (3 * proj + points) / (3 + el)
    opp_rate = (0.5 * proj_opps + opportunities(stats, str(pos).upper())) / (0.5 + el) * proj / max(proj_opps, 1.0)
    margin = max(-21.0, min(21.0, float((game or {}).get("score") or 0) - float((game or {}).get("opp_score") or 0)))
    a, b, c, d = fit
    rest = left * (a * pts_rate + b * opp_rate + c * margin + d)
    return round(points + max(0.0, rest), 2)


def win_chance(sides: list[dict[str, Any]], final: bool) -> list[int] | None:
    """Each side's chance to win, from the current score plus what's still projected to come.

    Each unfinished starter's remaining points get a spread of 2 x sqrt(points still projected) + 0.4,
    fitted on every QB/RB/WR/TE game of 2023-2025 replayed play by play against Sleeper's projections
    (scoring/live_projection_study.py). A free normal approximation, no model run.
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
                var += (2.0 * math.sqrt(left) + 0.4) ** 2
    if var <= 0:
        return None
    z = (means[0] - means[1]) / math.sqrt(var)
    a = round(100 * 0.5 * (1 + math.erf(z / math.sqrt(2))))
    a = min(99, max(1, a))
    return [a, 100 - a]
