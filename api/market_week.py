"""This week's numbers on Market rows, for the app's Market list and the website's League Players tab.

Each row gets this week's number (projection before kickoff, live points during, final points after), the
season average with games played and position rank, the last-3 average, and the next game with its live
state and the defense's rank vs the position, all in league scoring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api import cache, home, league, live_stats, market, nfl_live, projections
from league_ai import nfl_data


def kickoff_utc(game: dict[str, Any]) -> str | None:
    try:
        clock = str(game.get("gametime") or "") or "13:00"
        return datetime.fromisoformat(f"{game['gameday']}T{clock}").replace(tzinfo=home.EASTERN).astimezone(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def add_this_week(client: Any, sid: str, season: int, rows: list[dict[str, Any]]) -> None:
    """Market rows get this week's number (projection before kickoff, live points during, final points after),
    the season average with games played and position rank, the last-3 average, and the next game with its
    live state and the defense's rank vs the position, all in league scoring. The slow part is one pass for the
    whole league cached half an hour; the game states and this week's points are refreshed every minute."""
    week = league.current_week()

    def load() -> dict[str, Any]:
        ids = [r["player_id"] for r in rows if r.get("pos") in ("QB", "RB", "WR", "TE", "K")]
        pos = {r["player_id"]: r["pos"] for r in rows}
        team = {r["player_id"]: r["nfl_team"] for r in rows}
        out: dict[str, Any] = {"proj": {}, "avg": {}, "gp": {}, "rank": {}, "form": {}, "games": {}, "dvp": {}}
        try:
            out["proj"] = projections.week_projections(client, sid, season, week, ids, pos, team)
        except Exception as exc:
            print(f"MARKET_PROJ_ERROR {type(exc).__name__}", flush=True)
        played: list[tuple[int, str, str, float]] = []   # (week, pos, nfl team, league points) for every game played
        try:
            avg, gp = projections.season_averages(sid, season, week - 1) if week > 1 else ({}, {})
            scoring = projections.league_scoring(sid)
            positions: dict[str, str] = {}
            by_week: dict[int, dict[str, float]] = {}
            for w in range(1, week):
                pts: dict[str, float] = {}
                for p, l in projections.week_lines("stats", season, w, 86400).items():
                    if not projections.played(l.get("stats")):
                        continue
                    pts[p] = projections.score(l["stats"], scoring, l.get("pos"))
                    positions[p] = positions.get(p) or l.get("pos") or ""
                    played.append((w, l.get("pos") or "", nfl_data.team_code(l.get("team")) or "", pts[p]))
                by_week[w] = pts
            weekly = [by_week[w] for w in range(week - 1, max(0, week - 1 - market.TREND_WEEKS), -1)]
            out["avg"], out["gp"], out["form"] = avg, gp, market.recent_form(weekly, avg)
            out["rank"] = market.position_ranks({p: a * gp.get(p, 0) for p, a in avg.items()}, {**positions, **pos})
        except Exception as exc:
            print(f"MARKET_FORM_ERROR {type(exc).__name__}", flush=True)
        try:
            schedule = nfl_data.season_games(client, season)
            out["games"] = nfl_data.week_matchups(schedule, week)
            opp = {w: nfl_data.week_matchups(schedule, w) for w in range(1, week)}
            # the defense rank in our own scoring, from the same weekly lines as the averages
            out["dvp"] = market.defense_ranks(played, lambda t, w: (opp.get(w, {}).get(t) or {}).get("opponent"))
        except Exception as exc:
            print(f"MARKET_GAMES_ERROR {type(exc).__name__}", flush=True)
        return out

    def load_live() -> dict[str, Any]:
        """This week's game states (ESPN scoreboard) and points so far (our watcher's live lines over the stat feed)."""
        games = nfl_live.week_games(season, week, False)
        points: dict[str, float] = {}
        if any(g.get("state") in ("in", "post") for g in games.values()) or not games:
            try:
                scoring = projections.league_scoring(sid)
                lines = {**projections.week_lines("stats", season, week, 120), **live_stats.fresh_lines(client, season, week)}
                points = {p: round(projections.score(l.get("stats") or {}, scoring, l.get("pos")), 1) for p, l in lines.items() if l.get("stats")}
            except Exception as exc:
                print(f"MARKET_LIVE_ERROR {type(exc).__name__}", flush=True)
        return {"games": games, "points": points}

    data = cache.cached(("market_week", sid, season, week), 1800, load)
    live = cache.cached(("market_live", sid, season, week), 60, load_live)
    find = market.next_games(data["games"], data["dvp"], week, kickoff_utc)
    now = datetime.now(timezone.utc)
    for r in rows:
        pid = r["player_id"]
        r["proj"] = data["proj"].get(pid)
        a = data["avg"].get(pid)
        r["avg"] = round(a, 1) if a is not None else None
        r["games"] = data["gp"].get(pid)
        r["pos_rank"] = data["rank"].get(pid)
        form = data["form"].get(pid) or {}
        r["last3"], r["trend"] = form.get("last3"), form.get("trend")
        nxt = find(nfl_data.team_code(r.get("nfl_team")) or "", r.get("pos") or "")
        game = live["games"].get(r.get("nfl_team") or "")
        state = market.game_state(nxt, game, now, home.GAME_LENGTH_HOURS)
        if nxt and state:
            nxt["state"] = state
            nxt["final"] = state == "post"
            if game and state != "pre":
                nxt.update(score=game.get("score"), opp_score=game.get("opp_score"), detail=game.get("detail"))
        r["next"] = nxt
        # this week's points once his game has started (0 if he hasn't recorded a stat yet)
        r["week_pts"] = live["points"].get(pid, 0.0) if state in ("in", "post") else None


def week_text(row: dict[str, Any]) -> str:
    """The Week column as text (the website table): projection, live points with a dot, final points, BYE."""
    nxt = row.get("next") or {}
    if nxt.get("bye"):
        return "BYE"
    if nxt.get("state") in ("in", "post") and row.get("week_pts") is not None:
        return f"{'● ' if nxt['state'] == 'in' else ''}{row['week_pts']:.1f}"
    return f"{row['proj']:.1f}" if row.get("proj") is not None else "—"


def game_text(row: dict[str, Any]) -> str:
    """His game in a few words: kickoff with a tough/soft defense note, the live clock and score, or the final."""
    nxt = row.get("next") or {}
    if not nxt:
        return ""
    if nxt.get("bye"):
        return "Bye week"
    where = f"{'vs' if nxt.get('home') else '@'} {nxt.get('opp')}"
    score = f"{nxt['score']}-{nxt['opp_score']}" if nxt.get("score") is not None and nxt.get("opp_score") is not None else ""
    if nxt.get("state") == "post":
        result = ("W" if nxt["score"] > nxt["opp_score"] else "L" if nxt["score"] < nxt["opp_score"] else "T") if score else ""
        return f"Final · {result} {score} {where}".replace("  ", " ") if score else f"Final {where}"
    if nxt.get("state") == "in":
        return f"{nxt.get('detail') or 'Live'} · {where}{f' · {score}' if score else ''}"
    when = ""
    if nxt.get("kickoff"):
        try:
            when = datetime.fromisoformat(nxt["kickoff"]).astimezone(home.EASTERN).strftime("%a %-I:%M %p ET · ")
        except ValueError:
            when = ""
    rank, teams = nxt.get("opp_rank"), nxt.get("teams") or 32
    tone = "" if rank is None else " · tough D" if rank <= teams / 3 else " · soft D" if rank > teams * 2 / 3 else ""
    return f"{when}{where}{tone}"
