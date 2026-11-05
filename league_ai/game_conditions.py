"""Game conditions that move fantasy scoring away from the projection: weather and game situations.

One place that turns a game (nflverse schedule row or week_matchups entry) into numbers, used three ways:
  * scoring/weather_study.py fits how much each condition moves scoring, by position, on 2021-2025 and checks
    the fit on 2026 (out of sample);
  * league_ai.projection_calibration replays every finished week with these multipliers applied;
  * api.projections.week_projections applies them to this week's projections (app, website and Front Office).

Weather: kickoff to kickoff+3h. Past games use Open-Meteo's free historical archive (wind, gusts, rain, snow,
temperature); this week's games use the Open-Meteo forecast (league_ai.nfl_data.add_weather). No keys, free.
Prints nothing; holds no league data.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# older nflverse stadium names -> the names OPEN_AIR_VENUES uses
ALIASES = {"Heinz Field": "Acrisure Stadium", "FedExField": "Northwest Stadium", "FedEx Field": "Northwest Stadium",
           "Paul Brown Stadium": "Paycor Stadium", "TIAA Bank Stadium": "EverBank Stadium", "TIAA Bank Field": "EverBank Stadium",
           "FirstEnergy Stadium": "Huntington Bank Field", "Cleveland Browns Stadium": "Huntington Bank Field",
           "Arrowhead Stadium": "GEHA Field at Arrowhead Stadium", "GEHA Field at Arrowhead": "GEHA Field at Arrowhead Stadium",
           "Bills Stadium": "Highmark Stadium", "New Era Field": "Highmark Stadium", "Empower Field": "Empower Field at Mile High",
           "Broncos Stadium at Mile High": "Empower Field at Mile High", "Lambeau field": "Lambeau Field",
           "Commanders Field": "Northwest Stadium", "Cleveland Stadium": "Huntington Bank Field"}
INDOOR = ("dome", "closed")
POSITIONS = ("QB", "RB", "WR", "TE", "K")

_ARCHIVE_CACHE: dict[tuple[str, str, str], dict[str, list[Any]]] = {}


def venue(name: Any) -> str:
    n = str(name or "")
    return ALIASES.get(n, n)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _window(hourly: Mapping[str, list[Any]], gameday: str, gametime: str) -> dict[str, float] | None:
    times = hourly.get("time") or []
    start = f"{gameday}T{str(gametime)[:2]}:00"
    if start not in times:
        return None
    i = times.index(start)
    w = slice(i, i + 3)
    vals = {k: [x for x in (hourly.get(k) or [])[w] if x is not None]
            for k in ("temperature_2m", "rain", "snowfall", "wind_speed_10m", "wind_gusts_10m")}
    if not vals["temperature_2m"] or not vals["wind_speed_10m"]:
        return None
    return {"temp": sum(vals["temperature_2m"]) / len(vals["temperature_2m"]), "rain": sum(vals["rain"]),
            "snow": sum(vals["snowfall"]), "wind": sum(vals["wind_speed_10m"]) / len(vals["wind_speed_10m"]),
            "gust": max(vals["wind_gusts_10m"] or [0.0])}


def archive_weather(games: Iterable[Mapping[str, Any]], get_json: Any = None) -> dict[tuple[int, str], dict[str, float]]:
    """(week, home team) -> kickoff weather for every played open-air game at a known venue (one archive call per
    venue per season, cached in the process). Missing games are simply left out."""
    from league_ai.nfl_data import OPEN_AIR_VENUES

    by_venue: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for g in games:
        name = venue(g.get("stadium"))
        if (name in OPEN_AIR_VENUES and str(g.get("roof") or "") in ("outdoors", "open") and g.get("gameday")
                and g.get("gametime") and g.get("home_score") is not None):
            by_venue[name].append(g)
    out: dict[tuple[int, str], dict[str, float]] = {}
    for name, gs in by_venue.items():
        lat, lon = OPEN_AIR_VENUES[name]
        days = sorted(str(g["gameday"]) for g in gs)
        key = (name, days[0], days[-1])
        if key not in _ARCHIVE_CACHE:
            params = {"latitude": lat, "longitude": lon, "start_date": days[0], "end_date": days[-1],
                      "hourly": "temperature_2m,rain,snowfall,wind_speed_10m,wind_gusts_10m", "temperature_unit": "fahrenheit",
                      "wind_speed_unit": "mph", "precipitation_unit": "inch", "timezone": "America/New_York"}
            try:
                if get_json is None:
                    import requests

                    data = requests.get(ARCHIVE_URL, params=params, timeout=60).json()
                else:
                    data = get_json(ARCHIVE_URL, params)
                _ARCHIVE_CACHE[key] = (data or {}).get("hourly") or {}
            except Exception:
                _ARCHIVE_CACHE[key] = {}
        for g in gs:
            w = _window(_ARCHIVE_CACHE[key], str(g["gameday"]), str(g["gametime"]))
            if w:
                out[(int(g["week"]), str(g["home_team"]))] = w
    return out


def team_climates(games: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """team -> 'dome', 'cold' or 'warm', from its usual home stadium: indoors -> dome; an open-air venue north of
    about 38.5N and east of the Rockies' west side -> cold; any other open-air venue -> warm."""
    from league_ai.nfl_data import OPEN_AIR_VENUES

    homes: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        homes[str(g.get("home_team") or "")][(venue(g.get("stadium")), str(g.get("roof") or ""))] += 1
    out = {}
    for team, c in homes.items():
        if not team:
            continue
        (name, roof), _ = c.most_common(1)[0]
        if roof in INDOOR or name not in OPEN_AIR_VENUES:
            out[team] = "dome"
        else:
            lat, lon = OPEN_AIR_VENUES[name]
            out[team] = "cold" if lat >= 38.5 and lon > -110 else "warm"
    return out


def game_weather(game: Mapping[str, Any], archived: Mapping[str, float] | None = None) -> dict[str, float] | None:
    """Kickoff weather for one game: the archive when we have it, else the forecast or game record on the game
    (week_matchups 'weather': temp_f, wind_mph, rain_in / snow_in / gust_mph when the forecast has them)."""
    if archived:
        return dict(archived)
    wx = game.get("weather") or {}
    temp, wind = _f(wx.get("temp_f")), _f(wx.get("wind_mph"))
    if temp is None and wind is None:
        return None
    precip = _f(wx.get("precip_in")) or 0.0
    snow = _f(wx.get("snow_in"))
    rain = _f(wx.get("rain_in"))
    if rain is None:   # forecast without the split: precipitation at or below freezing counts as snow
        cold = temp is not None and temp <= 32
        rain, snow = (0.0, snow if snow is not None else precip * 10) if cold else (precip, snow or 0.0)
    return {"temp": temp if temp is not None else 60.0, "wind": wind or 0.0, "gust": _f(wx.get("gust_mph")) or 0.0,
            "rain": rain or 0.0, "snow": snow or 0.0}


WEST = {"SEA", "SF", "LA", "LAR", "LAC", "LV", "ARI"}
EAST = {"NE", "NYJ", "NYG", "BUF", "MIA", "PHI", "PIT", "BAL", "WAS", "CAR", "ATL", "TB", "JAX", "CLE", "CIN", "DET", "IND"}


def situations(games: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str], dict[str, float]]:
    """(week, team) -> game situations known before kickoff, for one season's schedule rows: rest (short week,
    off a bye, 3+ fewer days of rest than the opponent), neutral or international site, a west coast team at an
    early east kickoff, visiting Denver, playing at home, a division rematch, weeks 17-18, a head coach change
    during the season."""
    from datetime import date

    rows = sorted((g for g in games if g.get("week") is not None and g.get("gameday")), key=lambda g: (int(g["week"]), str(g["gameday"])))
    homes: dict[str, Counter] = defaultdict(Counter)
    for g in rows:
        homes[str(g["home_team"])][venue(g.get("stadium"))] += 1
    usual = {t: c.most_common(1)[0][0] for t, c in homes.items()}
    last_day: dict[str, date] = {}
    first_coach: dict[str, str] = {}
    met: set[tuple[str, str]] = set()
    out: dict[tuple[int, str], dict[str, float]] = {}
    for g in rows:
        week, home, away = int(g["week"]), str(g["home_team"]), str(g["away_team"])
        try:
            day = date.fromisoformat(str(g["gameday"])[:10])
        except ValueError:
            continue
        rest = {t: (day - last_day[t]).days if t in last_day else None for t in (home, away)}
        pair = tuple(sorted((home, away)))
        neutral = venue(g.get("stadium")) != usual.get(home)
        for team, opp, is_home in ((home, away, True), (away, home, False)):
            f: dict[str, float] = {}
            r, ro = rest[team], rest[opp]
            if r is not None and r <= 5:
                f["short_rest"] = 1.0
            elif r is not None and r >= 13:
                f["off_bye"] = 1.0
            if r is not None and ro is not None and ro - r >= 3:
                f["rest_disadvantage"] = 1.0
            if neutral:
                f["neutral_site"] = 1.0
            elif is_home:
                f["home"] = 1.0
            if not is_home and team in WEST and home in EAST and str(g.get("gametime") or "")[:2] in ("09", "13"):
                f["west_early"] = 1.0
            if not is_home and home == "DEN" and not neutral:
                f["altitude"] = 1.0
            if g.get("div_game") and pair in met:
                f["div_rematch"] = 1.0
            if week >= 17:
                f["late_season"] = 1.0
            coach = str(g.get("home_coach" if is_home else "away_coach") or "")
            if coach and first_coach.setdefault(team, coach) != coach:
                f["new_coach"] = 1.0
            out[(week, team)] = f
        if g.get("div_game"):
            met.add(pair)
        last_day[home] = last_day[away] = day
    return out


# ---- the features (same numbers in the study, the replay and live) ---------------------------------------------

def features(game: Mapping[str, Any] | None, team: str, climate: Mapping[str, str], wx: Mapping[str, float] | None,
             situation: Mapping[str, float] | None = None) -> dict[str, float]:
    """Condition -> size for one team's game (0 = not present). Weather only for open-air games."""
    if not game:
        return {}
    out: dict[str, float] = {}
    roof = str(game.get("roof") or "")
    if roof in INDOOR:
        out["indoors"] = 1.0
    elif roof in ("outdoors", "open") and wx:
        wind, gust, rain, snow, temp = wx["wind"], wx.get("gust", 0.0), wx["rain"], wx["snow"], wx["temp"]
        if wind > 8:
            out["wind"] = min(wind - 8, 17) / 10            # per 10 mph over 8, capped at 25 mph
        if gust > 30:
            out["gusts"] = min(gust - 30, 20) / 10
        if rain >= 0.02:
            out["rain"] = min(rain, 0.5) / 0.25              # per quarter inch over the first three hours
        if snow >= 0.1:
            out["snow"] = 1.0
        if temp < 35:
            out["cold"] = min(35 - temp, 40) / 15            # per 15F under 35F
        cl = climate.get(team)
        if temp >= 85 and cl in ("cold", "dome"):
            out["heat_unused"] = 1.0                         # a cold-climate or dome team in 85F+
        if temp <= 35 and cl in ("warm", "dome"):
            out["cold_unused"] = 1.0                         # a warm-climate or dome team in 35F or colder
    for k, v in (situation or {}).items():
        if v:
            out[k] = float(v)
    return out


def multiplier(position: str, feats: Mapping[str, float], table: Mapping[str, Mapping[str, float]] | None = None) -> float:
    """1 + sum of each condition's fitted effect at this position x its size, kept within 0.75-1.25."""
    table = COND_EFFECTS if table is None else table
    pos = str(position or "").upper()
    m = 1.0 + sum(float((table.get(k) or {}).get(pos, 0.0)) * v for k, v in feats.items())
    return max(0.75, min(1.25, m))


# Fitted by scoring/weather_study.py on 2021-2026 regular seasons (25,862 player-games, ridge-shrunk): share of the
# projection per unit of each feature. Kept only conditions that helped when each season was predicted from the other
# five (team-week miss better in all five seasons, 2022 to 2026). Fit one at a time they read: steady rain about -16%,
# wind 20+ mph about -24%, snow +10% (projections cut too much), domes +3%; jointly the numbers share the credit.
# Refit each offseason (python -m scoring.weather_study); the daily learning loop rescales them ('conditions' signal).
COND_EFFECTS: dict[str, dict[str, float]] = {
    "altitude": {"K": 0.054, "QB": -0.067, "RB": -0.061, "TE": -0.097, "WR": -0.069},
    "gusts": {"K": -0.06, "QB": 0.125, "RB": 0.025, "TE": -0.057, "WR": 0.014},
    "heat_unused": {"K": -0.028, "QB": -0.044, "RB": -0.123, "TE": -0.067, "WR": 0.018},
    "home": {"K": -0.003, "QB": 0.034, "RB": 0.012, "TE": -0.0, "WR": 0.007},
    "indoors": {"K": 0.049, "QB": 0.007, "RB": 0.003, "TE": 0.006, "WR": 0.041},
    "new_coach": {"K": 0.111, "QB": -0.061, "RB": 0.012, "TE": -0.088, "WR": 0.012},
    "rain": {"K": -0.049, "QB": -0.095, "RB": -0.07, "TE": -0.008, "WR": -0.076},
    "rest_disadvantage": {"K": -0.027, "QB": -0.064, "RB": -0.049, "TE": 0.001, "WR": -0.017},
    "short_rest": {"K": -0.053, "QB": 0.02, "RB": 0.011, "TE": 0.112, "WR": 0.028},
    "snow": {"K": -0.004, "QB": 0.131, "RB": 0.156, "TE": -0.003, "WR": 0.032},
    "west_early": {"K": 0.02, "QB": -0.013, "RB": -0.036, "TE": -0.023, "WR": 0.082},
    "wind": {"K": -0.126, "QB": -0.17, "RB": -0.017, "TE": -0.084, "WR": -0.137},
}


def week_conditions(games_all: list[Mapping[str, Any]], week: int, games: Mapping[str, Mapping[str, Any]],
                    archive: bool = False, get_json: Any = None) -> dict[str, dict[str, float]]:
    """team -> features for one week. games_all: the season's nfl_games rows; games: week_matchups for the week
    (with add_weather's forecast for games not played yet). archive=True looks up Open-Meteo's archive for played
    games (the daily learning job's replay); the app's server never does, it uses the forecast or game record."""
    from league_ai.nfl_data import team_code

    climate = {team_code(t): c for t, c in team_climates(games_all).items()}
    situ = {(w, team_code(t)): f for (w, t), f in situations(games_all).items()}
    arch = {}
    if archive:
        arch = {team_code(h): w for (wk, h), w in archive_weather([g for g in games_all if int(g.get("week") or 0) == int(week)], get_json).items()}
    out = {}
    for team, g in games.items():
        home = team if g.get("home") else team_code(g.get("opponent"))
        wx = game_weather(g, arch.get(home))
        out[team] = features(g, team, climate, wx, situ.get((int(week), team)))
    return out
