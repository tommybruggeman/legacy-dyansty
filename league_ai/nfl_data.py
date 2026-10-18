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


def _cached(key: tuple[Any, ...], loader: Any, ttl: int = CACHE_SECONDS) -> Any:
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
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
            return client.table("nfl_games").select("week, gameday, gametime, home_team, away_team, home_score, away_score, spread_line, total_line, roof, stadium, temp, wind, home_coach, away_coach").eq("season", season).eq("game_type", "REG").limit(400).execute().data or []
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
        shared = {"gameday": r.get("gameday"), "gametime": r.get("gametime"), "roof": r.get("roof") or None, "stadium": r.get("stadium")}
        if r.get("wind") is not None or r.get("temp") is not None:  # nflverse fills these once the game is played
            shared["weather"] = {"temp_f": r.get("temp"), "wind_mph": r.get("wind"), "precip_in": None, "source": "game record"}
        out[home] = {"opponent": away, "home": True, "implied_points": implied_home, **shared}
        out[away] = {"opponent": home, "home": False, "implied_points": implied_away, **shared}
    return out


# Open-air venues (nflverse stadium name -> lat, lon). Domes need no forecast; a blank roof is a
# retractable roof whose state isn't announced yet, so it gets no weather adjustment either.
OPEN_AIR_VENUES = {
    "Acrisure Stadium": (40.4468, -80.0158), "Bank of America Stadium": (35.2258, -80.8528), "Empower Field at Mile High": (39.7439, -105.0201),
    "Estadio Banorte": (19.3029, -99.1505), "EverBank Stadium": (30.3239, -81.6373), "GEHA Field at Arrowhead Stadium": (39.0489, -94.4839),
    "Gillette Stadium": (42.0909, -71.2643), "Hard Rock Stadium": (25.9580, -80.2389), "Highmark Stadium": (42.7738, -78.7870),
    "Huntington Bank Field": (41.5061, -81.6995), "Lambeau Field": (44.5013, -88.0622), "Levi's Stadium": (37.4030, -121.9700),
    "Lincoln Financial Field": (39.9008, -75.1675), "Lumen Field": (47.5952, -122.3316), "M&T Bank Stadium": (39.2780, -76.6227),
    "MetLife Stadium": (40.8135, -74.0745), "Nissan Stadium": (36.1665, -86.7713), "Northwest Stadium": (38.9077, -76.8645),
    "Paycor Stadium": (39.0954, -84.5160), "Raymond James Stadium": (27.9759, -82.5033), "Soldier Field": (41.8623, -87.6167),
    "Tottenham Hotspur Stadium": (51.6043, -0.0664), "Wembley Stadium": (51.5560, -0.2795), "Maracana Stadium": (-22.9121, -43.2302),
}
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def kickoff_forecasts(games: list[Mapping[str, Any]], get_json: Any = None) -> dict[str, dict[str, Any]]:
    """Stadium -> forecast over the first three hours from kickoff, one Open-Meteo call for every open-air game.

    Free, keyless API; kickoff times in nflverse are US Eastern, so the forecast is asked for in that zone.
    Returns {} on any failure: weather is a refinement, never a reason for the page to break.
    """
    venues = []
    for g in games:
        name = g.get("stadium")
        if name in OPEN_AIR_VENUES and str(g.get("roof") or "") in ("outdoors", "open") and g.get("gameday") and g.get("gametime"):
            venues.append(g)
    if not venues:
        return {}
    days = sorted({str(g["gameday"]) for g in venues})
    params = {
        "latitude": ",".join(str(OPEN_AIR_VENUES[g["stadium"]][0]) for g in venues), "longitude": ",".join(str(OPEN_AIR_VENUES[g["stadium"]][1]) for g in venues),
        "hourly": "temperature_2m,precipitation,wind_speed_10m", "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": "America/New_York", "start_date": days[0], "end_date": days[-1],
    }
    try:
        if get_json is None:
            import requests

            data = requests.get(FORECAST_URL, params=params, timeout=10).json()
        else:
            data = get_json(FORECAST_URL, params)
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    out: dict[str, dict[str, Any]] = {}
    for g, loc in zip(venues, data or []):
        hourly = (loc or {}).get("hourly") or {}
        times = hourly.get("time") or []
        start = f"{g['gameday']}T{str(g['gametime'])[:2]}:00"
        if start not in times:
            continue
        i = times.index(start)
        window = slice(i, i + 3)
        try:
            temps = [t for t in hourly["temperature_2m"][window] if t is not None]
            winds = [w for w in hourly["wind_speed_10m"][window] if w is not None]
            rain = [x for x in hourly["precipitation"][window] if x is not None]
        except Exception:
            continue
        if not temps or not winds:
            continue
        out[g["stadium"]] = {"temp_f": round(sum(temps) / len(temps)), "wind_mph": round(sum(winds) / len(winds)), "precip_in": round(sum(rain), 2) if rain else 0.0, "source": "forecast"}
    return out


def add_weather(games: dict[str, dict[str, Any]], season: int, week: int, get_json: Any = None) -> dict[str, dict[str, Any]]:
    """Attach kickoff forecasts to week_matchups output (cached per week for 3 hours)."""
    pending = {}
    for g in games.values():
        if "weather" not in g and g.get("stadium"):
            pending[(g.get("stadium"), g.get("gameday"))] = g
    if pending:
        # the "_checked" marker caches a miss too, so a failed or empty forecast isn't re-fetched on every chat turn
        forecasts = _cached(("wx", int(season), int(week)), lambda: {"_checked": True, **kickoff_forecasts(list(pending.values()), get_json)}, ttl=3 * 3600) or {}
        for g in games.values():
            if "weather" not in g and g.get("stadium") in forecasts:
                g["weather"] = forecasts[g["stadium"]]
    return games


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


def season_stat_rows(client: Any, season: int) -> list[dict[str, Any]]:
    """One season's REG weekly player rows, only the columns matchup and tendency math needs (paged read, cached)."""
    if client is None:
        return []

    def load() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1000
        try:
            for start in range(0, 20000, page):
                chunk = (client.table("nfl_player_stats").select("week, position, team, opponent, attempts, carries, sacks, targets, passing_yards, passing_epa, rushing_yards, fantasy_points_ppr").eq("season", season).eq("season_type", "REG")
                         .order("gsis_id").order("week").range(start, start + page - 1).execute().data or [])
                rows.extend(chunk)
                if len(chunk) < page:
                    break
        except Exception:
            return []
        return rows

    return _cached(("stats", int(season)), load)


def defense_vs_position(client: Any, season: int) -> dict[tuple[str, str], dict[str, Any]]:
    """Defense-vs-position ratings from this season's nfl_player_stats."""
    return defense_vs_position_from_stats(season_stat_rows(client, season))


LEAGUE_AVG_IMPLIED = 22.5


def game_multiplier(position: str, game: Mapping[str, Any] | None) -> float:
    """Game environment: Vegas implied team points (a high-scoring script lifts everyone) and open-air weather.

    Wind is the weather that moves fantasy output most: it hurts passing and kicking and pushes teams to run.
    Heavy rain or snow and deep cold trim passing a little more.
    """
    if not game:
        return 1.0
    pos = str(position or "").upper()
    mult = 1.0
    implied = game.get("implied_points")
    if implied is not None:
        mult *= max(0.9, min(1.1, 1 + 0.5 * (float(implied) / LEAGUE_AVG_IMPLIED - 1)))
    wx = game.get("weather") or {}
    if str(game.get("roof") or "") in ("outdoors", "open") and wx:
        wind = float(wx.get("wind_mph") or 0)
        precip = float(wx.get("precip_in") or 0)
        temp = wx.get("temp_f")
        passing = pos in ("QB", "WR", "TE")
        if wind >= 20:
            mult *= {"QB": 0.88, "WR": 0.92, "TE": 0.94, "K": 0.85, "RB": 1.03}.get(pos, 1.0)
        elif wind >= 15:
            mult *= {"QB": 0.94, "WR": 0.96, "TE": 0.97, "K": 0.92, "RB": 1.01}.get(pos, 1.0)
        if precip >= 0.3:  # steady rain or snow across the first three hours
            mult *= 0.96 if passing else (1.02 if pos == "RB" else 0.95 if pos == "K" else 1.0)
        if temp is not None and float(temp) <= 20 and passing:
            mult *= 0.97
    # rest: teams off a bye score a bit more on average; a short week against a rested opponent costs a little
    if game.get("off_bye"):
        mult *= 1.02
    elif game.get("short_week") and not game.get("opp_short_week"):
        mult *= 0.98
    return mult


def weather_text(game: Mapping[str, Any] | None) -> str | None:
    if not game:
        return None
    roof = str(game.get("roof") or "")
    if roof in ("dome", "closed"):
        return "indoors"
    if not roof:
        return "retractable roof, open or closed not announced"
    wx = game.get("weather")
    if not wx:
        return "outdoors, no forecast yet"
    bits = [f"{wx['temp_f']:.0f}F" if wx.get("temp_f") is not None else "", f"wind {wx['wind_mph']:.0f} mph" if wx.get("wind_mph") is not None else ""]
    if wx.get("precip_in"):
        bits.append(f"{wx['precip_in']:.2f} in precip")
    return "outdoors, " + ", ".join(b for b in bits if b) + (" (forecast)" if wx.get("source") == "forecast" else "")


def total_multiplier(position: str, game: Mapping[str, Any] | None, rating: Mapping[str, Any] | None) -> float:
    """Defense matchup x game environment, capped at +/-20% so this week's context tilts calls without overriding talent."""
    return max(0.8, min(1.2, matchup_multiplier(rating.get("factor") if rating else None) * game_multiplier(position, game)))


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
    wx = weather_text(game)
    if wx:
        parts.append(wx)
    if game.get("off_bye"):
        parts.append("off a bye")
    elif game.get("short_week"):
        parts.append(f"short week ({game.get('rest_days')} days rest)")
    if game.get("opp_off_bye"):
        parts.append(f"{game['opponent']} off a bye")
    return ", ".join(parts)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
