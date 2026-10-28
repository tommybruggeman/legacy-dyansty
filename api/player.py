"""The full player page: NFL stats next to how the player has done in this league.

NFL numbers come from the nflverse tables (nfl_player_stats, nfl_games,
nfl_injuries, nfl_practice_reports, nfl_injury_episodes). League numbers
come from Sleeper matchups (points, starts) and the contract history read.
Every piece is optional: a missing table or a failed read leaves that card
out instead of breaking the page.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from concurrent.futures import ThreadPoolExecutor

from api import cache, projections, sleeper
from api.home import EASTERN
from api.teams import _f, _text
from league_ai.nfl_data import (
    add_weather,
    bye_weeks_from_games,
    current_week_from_games,
    defense_vs_position,
    matchup_label,
    season_games,
    team_code,
    week_matchups,
)

DVP_POSITIONS = {"QB", "RB", "WR", "TE"}
# Sep 1 through Feb 14 counts as the season for "mid-season pickup".
SEASON_MONTHS = {9, 10, 11, 12, 1}

ACQUIRED = {
    "rookie_draft": "Rookie draft",
    "fa_auction": "FA auction",
    "sleeper_waiver": "Waiver pickup",
    "sleeper_free_agent": "Free agent pickup",
    "commissioner_manual_add": "Added by commissioner",
}


def _n(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    return int(round(_n(value)))


def snap_pct(row: dict[str, Any]) -> int | None:
    pct = row.get("offense_pct")
    if pct is None:
        return None
    pct = _n(pct)
    return int(round(pct * 100 if pct <= 1 else pct))


def nfl_line(row: dict[str, Any] | None, pos: str) -> dict[str, Any] | None:
    """One game's box score, in the few numbers that matter for the position."""
    if not row:
        return None
    line = {
        "carries": _int(row.get("carries")), "rush_yds": _int(row.get("rushing_yards")), "rush_td": _int(row.get("rushing_tds")),
        "targets": _int(row.get("targets")), "rec": _int(row.get("receptions")), "rec_yds": _int(row.get("receiving_yards")),
        "rec_td": _int(row.get("receiving_tds")), "snap_pct": snap_pct(row),
    }
    if pos == "QB":
        line.update({
            "comp": _int(row.get("completions")), "att": _int(row.get("attempts")),
            "pass_yds": _int(row.get("passing_yards")), "pass_td": _int(row.get("passing_tds")), "ints": _int(row.get("interceptions")),
        })
    return line


def _opp_label(home: bool | None, opp: str) -> str:
    return f"{'vs' if home else 'at'} {opp}" if opp else ""


def schedule_by_week(games: Iterable[dict[str, Any]], team: str) -> dict[int, dict[str, Any]]:
    """Week -> {opp, home, final score} for one NFL team."""
    team = team_code(team)
    out: dict[int, dict[str, Any]] = {}
    for g in games:
        home, away = team_code(g.get("home_team")), team_code(g.get("away_team"))
        if team not in (home, away) or g.get("week") is None:
            continue
        is_home = team == home
        out[int(g["week"])] = {"opp": away if is_home else home, "home": is_home}
    return out


def game_log(
    league_weeks: list[dict[str, Any]],
    nfl_rows: list[dict[str, Any]],
    schedule: dict[int, dict[str, Any]],
    bye_week: int | None,
    pos: str,
) -> list[dict[str, Any]]:
    """Every scored week, newest first: opponent, NFL line, league points and lineup spot.

    status is started, bench, out (his team played, he didn't), bye, or free_agent
    (he played but wasn't on a league roster that week).
    """
    by_week = {int(r["week"]): r for r in nfl_rows if r.get("week") is not None}
    out = []
    for w in league_weeks:
        week = int(w["week"])
        row = by_week.get(week)
        game = schedule.get(week) or {}
        opp = team_code(row.get("opponent")) if row and row.get("opponent") else game.get("opp", "")
        home = row.get("is_home") if row and row.get("is_home") is not None else game.get("home")
        if week == bye_week and not row:
            status = "bye"
        elif row is None and w.get("points") in (None, 0):
            status = "out" if game else "bye"
        elif w.get("started"):
            status = "started"
        elif w.get("points") is None:
            status = "free_agent"
        else:
            status = "bench"
        out.append({
            "week": week,
            "opp": "" if status == "bye" else _opp_label(home, opp),
            "status": status,
            "points": None if status in ("bye", "out") else w.get("points"),
            "line": nfl_line(row, pos),
        })
    return list(reversed(out))


def season_tiles(nfl_rows: list[dict[str, Any]], pos: str) -> dict[str, Any] | None:
    """Four season numbers for the position, plus average snap share."""
    games = [r for r in nfl_rows if r.get("week") is not None]
    if not games:
        return None
    total = lambda f: sum(_n(r.get(f)) for r in games)  # noqa: E731
    carries, rush, rec, rec_yds = total("carries"), total("rushing_yards"), total("receptions"), total("receiving_yards")
    tds = total("rushing_tds") + total("receiving_tds")
    if pos == "QB":
        att = total("attempts")
        tiles = [
            {"value": f"{total('passing_yards'):,.0f}", "label": "pass yds"},
            {"value": f"{total('passing_tds'):.0f}", "label": "pass TDs"},
            {"value": f"{total('interceptions'):.0f}", "label": "INTs"},
            {"value": f"{100 * total('completions') / att:.0f}%" if att else "—", "label": "completions"},
        ]
    elif pos == "RB":
        tiles = [
            {"value": f"{rush:,.0f}", "label": "rush yds"},
            {"value": f"{rush / carries:.1f}" if carries else "—", "label": "per carry"},
            {"value": f"{rec:.0f}", "label": "catches"},
            {"value": f"{tds:.0f}", "label": "total TDs"},
        ]
    else:
        tiles = [
            {"value": f"{rec:.0f}", "label": "catches"},
            {"value": f"{rec_yds:,.0f}", "label": "rec yds"},
            {"value": f"{total('targets'):.0f}", "label": "targets"},
            {"value": f"{tds:.0f}", "label": "total TDs"},
        ]
    snaps = [s for s in (snap_pct(r) for r in games) if s is not None]
    recent = [s for s in (snap_pct(r) for r in sorted(games, key=lambda r: int(r["week"]))[-3:]) if s is not None]
    avg = round(sum(snaps) / len(snaps)) if snaps else None
    trend = None
    if avg is not None and recent:
        last = sum(recent) / len(recent)
        trend = "up" if last >= avg + 5 else "down" if last <= avg - 5 else None
    return {"games": len(games), "tiles": tiles, "snap_pct": avg, "snap_trend": trend}


def league_ranks(weeks: Iterable[list[dict[str, Any]]], positions: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Player -> rank at his position by total league points this season (rostered weeks only)."""
    totals: dict[str, float] = {}
    for rows in weeks:
        for row in rows or []:
            for pid, pts in (row.get("players_points") or {}).items():
                totals[pid] = totals.get(pid, 0.0) + _n(pts)
    out: dict[str, dict[str, Any]] = {}
    by_pos: dict[str, list[str]] = {}
    for pid in totals:
        pos = positions.get(pid)
        if pos:
            by_pos.setdefault(pos, []).append(pid)
    for pos, ids in by_pos.items():
        ids.sort(key=lambda p: totals[p], reverse=True)
        for rank, pid in enumerate(ids, start=1):
            out[pid] = {"rank": rank, "of": len(ids), "pos": pos, "total": round(totals[pid], 1)}
    return out


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def contract_history(rows: list[dict[str, Any]], years_left: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Contract events, newest first, as titled rows: how he was acquired, by whom, for how much, and what happened."""
    out = []
    for r in rows:
        kind = _text(r.get("event_type"))
        when = _when(r.get("effective_at") or r.get("created_at"))
        new = r.get("new_values") or {}
        prev = r.get("previous_values") or {}
        salary = new.get("salary") if new.get("salary") is not None else prev.get("salary")
        years = new.get("years")
        if years is None and r.get("start_season") and r.get("end_season"):
            years = int(r["end_season"]) - int(r["start_season"]) + 1
        source = _text(r.get("acquisition_type")) or _text(r.get("source"))
        if kind == "signed":
            title = ACQUIRED.get(source, "Signed")
            if source in ("sleeper_waiver", "sleeper_free_agent") and when and when.month in SEASON_MONTHS:
                title = "Mid-season pickup"
        elif kind == "imported":
            title = "Rookie deal" if r.get("contract_type") == "rookie" else "Signed"
        else:
            title = {
                "traded": "Traded for", "released": "Released", "expired": "Contract ended",
                "extended": "Extended", "option_exercised": "Rookie option kept", "option_declined": "Rookie option declined",
            }.get(kind)
        if not title:
            continue
        status = _text(r.get("contract_status"))
        live = status in ("active", "scheduled") and kind not in ("released", "expired")
        if kind == "released":
            tag = "ENDED"
        elif kind == "traded":
            tag = "TRADED"
        elif live:
            left = (years_left or {}).get(_text(r.get("league_team_id")))
            tag = f"{left} YR{'S' if left != 1 else ''} LEFT" if left else "ACTIVE"
        elif r.get("contract_type") == "rookie":
            tag = "ROOKIE DEAL"
        else:
            tag = status.upper() or None
        out.append({
            "kind": kind,
            "title": title,
            "owner": _text(r.get("owner_name")),
            "team_id": _text(r.get("league_team_id")),
            "date": when.strftime("%b %Y") if when else str(r.get("effective_season") or ""),
            "salary": _f(salary) if salary is not None else None,
            "years": int(years) if years else None,
            "tag": tag,
            "live": live,
        })
    # The live contract is the newest signing/trade still active; older rows for it are history.
    seen_live = False
    for row in out:
        if row["live"] and not seen_live:
            seen_live = True
        elif row["live"]:
            row["live"] = False
            if row["tag"] and "LEFT" in row["tag"]:
                row["tag"] = "ACTIVE" if row["kind"] != "traded" else "TRADED"
    return out


def next_game(games: list[dict[str, Any]], season: int, team: str, pos: str, dvp: dict | None, bye_week: int | None) -> dict[str, Any] | None:
    week = current_week_from_games(games)
    if not week or not team:
        return None
    team = team_code(team)
    if bye_week == week:
        return {"week": week, "bye": True}
    matchups = week_matchups(games, week)
    try:
        matchups = add_weather(matchups, season, week)
    except Exception:
        pass
    game = matchups.get(team)
    if not game:
        return None
    kickoff = None
    try:
        clock = _text(game.get("gametime")) or "13:00"
        kickoff = datetime.fromisoformat(f"{game['gameday']}T{clock}").replace(tzinfo=EASTERN).astimezone(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    rating = (dvp or {}).get((game["opponent"], pos)) if pos in DVP_POSITIONS else None
    weather = None
    roof = _text(game.get("roof"))
    wx = game.get("weather") or {}
    if roof in ("dome", "closed"):
        weather = "Indoors"
    elif wx.get("temp_f") is not None:
        weather = f"{wx['temp_f']:.0f}°" + (f", wind {wx['wind_mph']:.0f}" if wx.get("wind_mph") and wx["wind_mph"] >= 12 else "")
    vs = None
    if rating:
        rank, teams = int(rating["rank"]), int(rating.get("teams") or 32)
        easy = teams - rank + 1
        vs = {
            "label": matchup_label(rating),
            "text": f"{_ordinal(easy)} easiest" if easy <= teams / 2 else f"{_ordinal(rank)} toughest",
            "allowed": rating.get("ppg_allowed"),
        }
    return {
        "week": week,
        "bye": False,
        "opp": game["opponent"],
        "home": bool(game.get("home")),
        "kickoff": kickoff,
        "team_total": game.get("implied_points"),
        "weather": weather,
        "vs_pos": vs,
    }


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


PRACTICE = {
    "Did Not Participate In Practice": "Did not practice",
    "Limited Participation in Practice": "Limited practice",
    "Full Participation in Practice": "Full practice",
}


def health(meta: dict[str, Any], espn: dict[str, Any] | None, practice: list[dict[str, Any]], episodes: list[dict[str, Any]], season: int) -> dict[str, Any]:
    status = _text(meta.get("injury_status")) or _text((espn or {}).get("status"))
    latest = practice[0] if practice else None
    this_week = None
    if latest and int(latest.get("season") or 0) == season:
        this_week = {
            "week": int(latest["week"]),
            "practice": PRACTICE.get(_text(latest.get("practice_status")), _text(latest.get("practice_status")) or None),
            "game_status": _text(latest.get("report_status")) or None,
            "injury": _text(latest.get("report_injury") or latest.get("practice_injury")) or None,
        }
    missed = []
    for e in episodes[:4]:
        games = int(e.get("games_missed") or 0)
        if games <= 0:
            continue
        missed.append({
            "injury": (_text(e.get("injury")) or "injury").lower(),
            "season": int(e.get("last_game_season") or 0),
            "games": games,
            "back": bool(e.get("returned")),
        })
    return {
        "status": status or "Healthy",
        "healthy": not status,
        "detail": _text(meta.get("injury_body_part")) or _text((espn or {}).get("injury_type")) or None,
        "update": _text((espn or {}).get("short_comment")) or None,
        "return": _text((espn or {}).get("return_date")) or None,
        "this_week": this_week,
        "missed": missed,
    }


# ---- loaders: one read each, every one allowed to fail -----------------------

def _safe(load: Any, default: Any) -> Any:
    try:
        return load()
    except Exception:
        return default


def load_nfl_rows(client: Any, player_id: str, season: int) -> list[dict[str, Any]]:
    return _safe(lambda: client.table("nfl_player_stats").select("*").eq("sleeper_id", player_id).eq("season", season)
                 .eq("season_type", "REG").order("week").limit(25).execute().data or [], [])


def load_espn(client: Any, player_id: str) -> dict[str, Any] | None:
    rows = _safe(lambda: client.table("nfl_injuries").select("status,injury_type,short_comment,return_date").eq("sleeper_id", player_id).limit(1).execute().data or [], [])
    return rows[0] if rows else None


def load_practice(client: Any, player_id: str) -> list[dict[str, Any]]:
    return _safe(lambda: client.table("nfl_practice_reports").select("season,week,practice_status,report_status,report_injury,practice_injury")
                 .eq("sleeper_id", player_id).order("season", desc=True).order("week", desc=True).limit(1).execute().data or [], [])


def load_episodes(client: Any, player_id: str) -> list[dict[str, Any]]:
    rows = _safe(lambda: client.table("nfl_injury_episodes").select("injury,last_game_season,last_game_week,games_missed,returned")
                 .eq("sleeper_id", player_id).limit(30).execute().data or [], [])
    return sorted(rows, key=lambda r: (int(r.get("last_game_season") or 0), int(r.get("last_game_week") or 0)), reverse=True)


def load_history(client: Any, league_id: str, player_id: str) -> list[dict[str, Any]]:
    return _safe(lambda: client.rpc("read_player_contract_history_authenticated", {"p_league_id": league_id, "p_player_id": player_id}).execute().data or [], [])


def load_ranks(client: Any, league_id: str, sleeper_id: str, weeks: int, player_info: Any) -> dict[str, dict[str, Any]]:
    """Position ranks for every rostered player, shared by the whole league for 10 minutes."""
    def load() -> dict[str, dict[str, Any]]:
        matchups = [sleeper.get(f"league/{sleeper_id}/matchups/{w}", ttl=86400) or [] for w in range(1, weeks + 1)]
        ids = {pid for rows in matchups for row in rows for pid in (row.get("players_points") or {})}
        info = player_info(client, ids)
        return league_ranks(matchups, {pid: _text(i.get("position")) for pid, i in info.items()})
    return cache.cached(("ranks", league_id, weeks), 600, load)


def page_extras(
    client: Any,
    *,
    league_id: str,
    sleeper_id: str,
    season: int,
    player_id: str,
    meta: dict[str, Any],
    pos: str,
    league_weeks: list[dict[str, Any]],
    last_week: int,
    years_left: dict[str, int],
    salary: float | None,
    player_info: Any,
) -> dict[str, Any]:
    """Everything beyond the basic contract card, read side by side."""
    team = _text(meta.get("team"))
    jobs = {
        "nfl": lambda: load_nfl_rows(client, player_id, season),
        "games": lambda: _safe(lambda: season_games(client, season), []),
        "espn": lambda: load_espn(client, player_id),
        "practice": lambda: load_practice(client, player_id),
        "episodes": lambda: load_episodes(client, player_id),
        "history": lambda: load_history(client, league_id, player_id),
        "ranks": lambda: _safe(lambda: load_ranks(client, league_id, sleeper_id, last_week, player_info), {}),
        "dvp": lambda: _safe(lambda: defense_vs_position(client, season), {}) if pos in DVP_POSITIONS else {},
        "week_ranks": lambda: weekly_ranks(sleeper_id, season, last_week, player_id, pos),
    }
    futures = {k: cache.pool.submit(f) for k, f in jobs.items()}
    got = {k: _safe(f.result, None) for k, f in futures.items()}

    games = got["games"] or []
    bye = bye_weeks_from_games(games).get(team_code(team)) if team else None
    nfl_rows = got["nfl"] or []
    log = game_log(league_weeks, nfl_rows, schedule_by_week(games, team), bye, pos)
    week_ranks = got["week_ranks"] or {}
    for g in log:
        g["rank"] = week_ranks.get(g["week"]) if g["status"] in ("started", "bench", "free_agent") else None
    upcoming = _safe(lambda: next_game(games, season, team, pos, got["dvp"], bye), None)
    if upcoming and not upcoming.get("bye"):
        upcoming["projected"] = _safe(lambda: projections.week_projections(client, sleeper_id, season, upcoming["week"], [player_id], {player_id: pos}, {player_id: team}).get(player_id), None)
    scored = [w["points"] for w in league_weeks if w.get("points")]
    ppg = sum(scored) / len(scored) if scored else None
    rank = (got["ranks"] or {}).get(player_id)
    started = sum(1 for g in log if g["status"] == "started")
    played = sum(1 for g in log if g["status"] in ("started", "bench"))
    return {
        "bye_week": bye,
        "nfl_season": season_tiles(nfl_rows, pos),
        "league_season": {
            "ppg": round(ppg, 1) if ppg is not None else None,
            "ppg_started": _avg([g["points"] for g in log if g["status"] == "started" and g["points"] is not None]),
            "rank": rank,
            "started": started,
            "played": played,
            "per_dollar": round(ppg / salary, 1) if ppg and salary else None,
        },
        "game_log": log,
        "next_game": upcoming,
        "health": health(meta, got["espn"], got["practice"] or [], got["episodes"] or [], season),
        "history": contract_history(got["history"] or [], years_left),
    }


def weekly_ranks(sleeper_id: str, season: int, last_week: int, player_id: str, pos: str) -> dict[int, str]:
    """Week -> his position rank that week ("RB7"), scored with league rules across every NFL player."""
    def one(week: int) -> str | None:
        return (projections.week_ranks(sleeper_id, season, week, True, {player_id: pos}).get(player_id) or {}).get("label")
    with ThreadPoolExecutor(max_workers=6) as pool:
        labels = list(pool.map(lambda w: _safe(lambda: one(w), None), range(1, last_week + 1)))
    return {w: label for w, label in zip(range(1, last_week + 1), labels) if label}


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None
