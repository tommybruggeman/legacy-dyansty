"""Read helpers over the nfl_* tables (freshness, lookups). Read-only."""
from __future__ import annotations

import time
from typing import Any, Mapping


def data_freshness(client: Any) -> dict[str, str]:
    """Latest successful run per loader, for the pack's freshness section."""
    out: dict[str, str] = {}
    if client is None:
        return out
    try:
        rows = (
            client.table("nfl_data_sync_log")
            .select("loader, finished_at, rows_written, ok")
            .eq("ok", True)
            .order("finished_at", desc=True)
            .limit(30)
            .execute()
            .data
            or []
        )
    except Exception:
        return {"NFL data": "not loaded yet (nightly sync has not run)"}
    labels = {"sleeper_players": "NFL players (Sleeper)", "nflverse_stats": "NFL stats (nflverse)", "dynastyprocess_values": "market values (DynastyProcess)", "injuries": "injuries (ESPN report + nflverse practice reports)", "cfbd_prospects": "rookie prospects (CFBD)", "league_history": "league history (Sleeper transactions, drafts, matchups)"}
    for row in rows:
        label = labels.get(row.get("loader"), row.get("loader"))
        if label not in out and row.get("finished_at"):
            out[label] = str(row["finished_at"])[:16].replace("T", " ") + " UTC"
    if not out:
        out["NFL data"] = "not loaded yet (nightly sync has not run)"
    return out


# nflverse schedule codes -> Sleeper team codes (nfl_players.team uses Sleeper's)
TEAM_ALIASES = {"LA": "LAR", "WSH": "WAS", "JAC": "JAX"}


def team_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    return TEAM_ALIASES.get(code, code)


def bye_weeks_from_games(rows: list[dict[str, Any]]) -> dict[str, int]:
    """NFL team -> bye week, from one season's regular-season nfl_games rows.

    A team's bye is the one week in the season's range it has no game. Teams with
    zero or several missing weeks (schedule not fully loaded) are left out rather than guessed.
    """
    played: dict[str, set[int]] = {}
    weeks: set[int] = set()
    for r in rows:
        try:
            week = int(r.get("week"))
        except Exception:
            continue
        weeks.add(week)
        for side in ("home_team", "away_team"):
            code = team_code(r.get(side))
            if code:
                played.setdefault(code, set()).add(week)
    if not weeks:
        return {}
    season_weeks = set(range(min(weeks), max(weeks) + 1))
    out: dict[str, int] = {}
    for code, seen in played.items():
        missing = season_weeks - seen
        if len(missing) == 1:
            out[code] = missing.pop()
    return out


# Small process-wide cache: the schedule and defense ratings are the same for every user and
# change at most once a day, so the chat, the Front Office page and the tools share one read.
_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
CACHE_SECONDS = 6 * 3600


def _cached(key: tuple[Any, ...], loader: Any) -> Any:
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    if value:  # don't pin an empty result (table not loaded yet, transient error)
        _CACHE[key] = (now, value)
    return value


def season_games(client: Any, season: int) -> list[dict[str, Any]]:
    """The season's regular-season schedule from nfl_games (one query, cached)."""
    if client is None:
        return []

    def load() -> list[dict[str, Any]]:
        try:
            return client.table("nfl_games").select("week, gameday, home_team, away_team, home_score, away_score, spread_line, total_line").eq("season", season).eq("game_type", "REG").limit(400).execute().data or []
        except Exception:
            return []

    return _cached(("games", int(season)), load)


def bye_weeks(client: Any, season: int) -> dict[str, int]:
    """Empty when the schedule isn't loaded."""
    return bye_weeks_from_games(season_games(client, season))


def current_week_from_games(rows: list[dict[str, Any]]) -> int | None:
    """Earliest week that still has an unplayed game (None once the regular season is over)."""
    open_weeks = [int(r["week"]) for r in rows if r.get("week") is not None and r.get("home_score") is None]
    return min(open_weeks) if open_weeks else None


def week_matchups(rows: list[dict[str, Any]], week: int) -> dict[str, dict[str, Any]]:
    """NFL team -> this week's opponent, home/away and Vegas implied points (nflverse spread: positive = home favored)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            if int(r.get("week")) != int(week):
                continue
        except Exception:
            continue
        home, away = team_code(r.get("home_team")), team_code(r.get("away_team"))
        if not home or not away:
            continue
        total, spread = r.get("total_line"), r.get("spread_line")
        implied_home = implied_away = None
        if total is not None and spread is not None:
            implied_home = round(float(total) / 2 + float(spread) / 2, 1)
            implied_away = round(float(total) / 2 - float(spread) / 2, 1)
        out[home] = {"opponent": away, "home": True, "implied_points": implied_home, "gameday": r.get("gameday")}
        out[away] = {"opponent": home, "home": False, "implied_points": implied_away, "gameday": r.get("gameday")}
    return out


DVP_POSITIONS = ("QB", "RB", "WR", "TE")
DVP_PRIOR_GAMES = 3  # shrink early-season ratings toward average: a defense needs a few games before it means much


def defense_vs_position_from_stats(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(defense team, position) -> PPR points allowed per game, factor vs league average and rank.

    rank 1 = toughest (fewest points allowed). factor > 1 means the defense gives up more than
    average; it is shrunk toward 1 by DVP_PRIOR_GAMES so a two-game sample can't swing a call.
    """
    allowed: dict[tuple[str, str], float] = {}
    games: dict[str, set[int]] = {}
    for r in rows:
        pos = str(r.get("position") or "").upper()
        defense = team_code(r.get("opponent"))
        if pos not in DVP_POSITIONS or not defense or r.get("week") is None:
            continue
        try:
            pts = float(r.get("fantasy_points_ppr") or 0)
        except Exception:
            pts = 0.0
        allowed[(defense, pos)] = allowed.get((defense, pos), 0.0) + pts
        games.setdefault(defense, set()).add(int(r["week"]))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for pos in DVP_POSITIONS:
        ppg = {d: allowed.get((d, pos), 0.0) / len(w) for d, w in games.items() if w}
        if not ppg:
            continue
        avg = sum(ppg.values()) / len(ppg)
        if avg <= 0:
            continue
        for rank, d in enumerate(sorted(ppg, key=ppg.get), start=1):
            n = len(games[d])
            raw = ppg[d] / avg
            out[(d, pos)] = {"ppg_allowed": round(ppg[d], 1), "factor": round(1 + (raw - 1) * n / (n + DVP_PRIOR_GAMES), 3), "rank": rank, "teams": len(ppg), "games": n}
    return out


def defense_vs_position(client: Any, season: int) -> dict[tuple[str, str], dict[str, Any]]:
    """Defense-vs-position ratings from this season's nfl_player_stats (paged read, cached)."""
    if client is None:
        return {}

    def load() -> dict[tuple[str, str], dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1000
        try:
            for start in range(0, 20000, page):
                chunk = (client.table("nfl_player_stats").select("week, position, opponent, fantasy_points_ppr").eq("season", season).eq("season_type", "REG")
                         .order("gsis_id").order("week").range(start, start + page - 1).execute().data or [])
                rows.extend(chunk)
                if len(chunk) < page:
                    break
        except Exception:
            return {}
        return defense_vs_position_from_stats(rows)

    return _cached(("dvp", int(season)), load)


def matchup_multiplier(factor: float | None) -> float:
    """Half the defense's deviation from average, capped at +/-15%: matchups tilt close calls, they don't bench studs."""
    if factor is None:
        return 1.0
    return max(0.85, min(1.15, 1 + 0.5 * (float(factor) - 1)))


def matchup_label(rating: Mapping[str, Any] | None) -> str | None:
    if not rating:
        return None
    rank, teams = int(rating["rank"]), int(rating.get("teams") or 32)
    if rank <= max(1, round(teams * 0.25)):
        return "tough"
    if rank > teams - round(teams * 0.25):
        return "soft"
    return "neutral"


def matchup_note(position: str, game: Mapping[str, Any] | None, rating: Mapping[str, Any] | None) -> str | None:
    """'@ SF, tough: SF allows 3rd-fewest PPR to WR (11.2/g), team implied 20.5 pts' ('vs' at home, '@' away)."""
    if not game:
        return None
    parts = [f"{'vs' if game.get('home') else '@'} {game['opponent']}"]
    if rating:
        rank, teams = int(rating["rank"]), int(rating.get("teams") or 32)
        side = f"{_ordinal(rank)}-fewest" if rank <= teams / 2 else f"{_ordinal(teams - rank + 1)}-most"
        parts.append(f"{matchup_label(rating)}: {game['opponent']} allows {side} PPR to {position} ({rating['ppg_allowed']}/g)")
    if game.get("implied_points") is not None:
        parts.append(f"team implied {game['implied_points']} pts")
    return ", ".join(parts)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
