from league_ai import game_conditions as gc


def _g(week, day, home, away, stadium="Highmark Stadium", roof="outdoors", **kw):
    return {"week": week, "gameday": day, "gametime": "13:00", "home_team": home, "away_team": away, "stadium": stadium, "roof": roof, **kw}


def test_climates_from_home_stadium():
    games = [_g(1, "2026-09-13", "BUF", "MIA"), _g(2, "2026-09-20", "MIA", "BUF", stadium="Hard Rock Stadium"),
             _g(3, "2026-09-27", "DET", "BUF", stadium="Ford Field", roof="dome")]
    assert gc.team_climates(games) == {"BUF": "cold", "MIA": "warm", "DET": "dome"}


def test_features_weather_and_misfits():
    g = {"roof": "outdoors"}
    f = gc.features(g, "MIA", {"MIA": "warm"}, {"wind": 18, "gust": 40, "rain": 0.25, "snow": 0.0, "temp": 20})
    assert f["wind"] == 1.0 and f["gusts"] == 1.0 and f["rain"] == 1.0 and f["cold"] == 1.0 and f["cold_unused"] == 1.0
    assert "snow" not in f and "heat_unused" not in f
    assert gc.features({"roof": "dome"}, "DET", {}, None) == {"indoors": 1.0}
    assert gc.features({"roof": "outdoors"}, "BUF", {"BUF": "cold"}, {"wind": 3, "gust": 10, "rain": 0, "snow": 0, "temp": 90}) == {"heat_unused": 1.0}


def test_situations_rest_rematch_and_neutral():
    games = [_g(1, "2026-09-13", "BUF", "MIA", div_game=True), _g(2, "2026-09-17", "NE", "BUF", stadium="Gillette Stadium"),
             _g(2, "2026-09-20", "MIA", "NYJ", stadium="Hard Rock Stadium"), _g(4, "2026-10-04", "MIA", "BUF", stadium="Hard Rock Stadium", div_game=True),
             _g(5, "2026-10-11", "BUF", "NE", stadium="Tottenham Hotspur Stadium"), _g(6, "2026-10-18", "BUF", "NYJ")]
    s = gc.situations(games)
    assert s[(2, "BUF")].get("short_rest") == 1.0           # 4 days after week 1
    assert s[(4, "MIA")].get("off_bye") == 1.0 and s[(4, "MIA")].get("div_rematch") == 1.0 and s[(4, "MIA")].get("home") == 1.0
    assert s[(5, "BUF")].get("neutral_site") == 1.0 and "home" not in s[(5, "BUF")]


def test_multiplier_uses_table_and_clamps():
    table = {"wind": {"QB": -0.1}, "indoors": {"WR": 0.05}}
    assert gc.multiplier("QB", {"wind": 1.5}, table) == 0.85
    assert gc.multiplier("WR", {"indoors": 1.0}, table) == 1.05
    assert gc.multiplier("QB", {"wind": 5.0}, table) == 0.75
    assert gc.multiplier("RB", {"wind": 1.0}, table) == 1.0


def test_game_weather_prefers_archive_then_forecast_split():
    assert gc.game_weather({}, {"temp": 40, "wind": 5, "gust": 9, "rain": 0, "snow": 0})["temp"] == 40
    wx = gc.game_weather({"weather": {"temp_f": 30, "wind_mph": 10, "precip_in": 0.1}})
    assert wx["rain"] == 0.0 and wx["snow"] == 1.0
    wx = gc.game_weather({"weather": {"temp_f": 50, "wind_mph": 10, "precip_in": 0.2, "rain_in": 0.2, "snow_in": 0.0, "gust_mph": 31}})
    assert wx["rain"] == 0.2 and wx["gust"] == 31
    assert gc.game_weather({"weather": {}}) is None


def test_archive_weather_window():
    hourly = {"time": ["2026-12-06T12:00", "2026-12-06T13:00", "2026-12-06T14:00", "2026-12-06T15:00"],
              "temperature_2m": [30, 31, 32, 33], "rain": [0, 0.1, 0.1, 0.1], "snowfall": [0, 0, 0, 0],
              "wind_speed_10m": [10, 20, 20, 20], "wind_gusts_10m": [20, 30, 35, 40]}
    gc._ARCHIVE_CACHE.clear()
    out = gc.archive_weather([_g(14, "2026-12-06", "BUF", "MIA", home_score=20)], get_json=lambda url, params: {"hourly": hourly})
    w = out[(14, "BUF")]
    assert round(w["rain"], 2) == 0.3 and w["wind"] == 20 and w["gust"] == 40 and w["temp"] == 32
