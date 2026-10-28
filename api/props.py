"""Vegas player yard lines from ESPN's free odds feed (DraftKings numbers).

Read on demand and cached for an hour per game, so a player page costs at most
two ESPN calls the first time anyone opens a player from that game.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Callable

import requests

from api import cache

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
PROP_BETS = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{event}/competitions/{event}/odds/{provider}/propBets"
CROSSWALK = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
DRAFTKINGS = "100"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ESPN prop type ids for the over/under on total yards, best first per position.
PASS, RUSH, REC, RUSH_REC = "8", "12", "13", "20"
BY_POSITION = {
    "QB": ((PASS, "PASS YDS"),),
    "RB": ((RUSH_REC, "RUSH+REC YDS"), (RUSH, "RUSH YDS")),
    "WR": ((REC, "REC YDS"),),
    "TE": ((REC, "REC YDS"),),
}
# ESPN team codes that differ from Sleeper's.
TEAM_ALIASES = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR"}


def _get(url: str, params: dict[str, Any] | None = None) -> Any:
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


fetch: Callable[..., Any] = _get


def _team(code: Any) -> str:
    code = str(code or "").upper()
    return TEAM_ALIASES.get(code, code)


def event_for_team(scoreboard: dict[str, Any], team: str) -> str | None:
    team = _team(team)
    for event in scoreboard.get("events") or []:
        for comp in event.get("competitions") or []:
            if any(_team((c.get("team") or {}).get("abbreviation")) == team for c in comp.get("competitors") or []):
                return str(event["id"])
    return None


def lines_by_athlete(items: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """ESPN athlete id -> {prop type id: current line}. Each line is listed once per side, so keep one."""
    out: dict[str, dict[str, float]] = {}
    for item in items:
        ref = str((item.get("athlete") or {}).get("$ref") or "")
        type_id = str((item.get("type") or {}).get("id") or "")
        value = ((item.get("current") or {}).get("target") or {}).get("value")
        if "/athletes/" not in ref or value is None:
            continue
        athlete = ref.split("/athletes/")[1].split("?")[0]
        out.setdefault(athlete, {})[type_id] = float(value)
    return out


def game_lines(event: str) -> dict[str, dict[str, float]]:
    def load() -> dict[str, dict[str, float]]:
        items: list[dict[str, Any]] = []
        for page in range(1, 6):
            try:
                data = fetch(PROP_BETS.format(event=event, provider=DRAFTKINGS), {"limit": 1000, "page": page})
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    break  # lines for this game aren't posted yet
                raise
            items.extend(data.get("items") or [])
            if page >= int(data.get("pageCount") or 1):
                break
        return lines_by_athlete(items)
    return cache.cached(("espn_props", event), 3600, load)


def espn_ids() -> dict[str, str]:
    """Sleeper id -> ESPN id, from the DynastyProcess crosswalk the nightly sync also uses."""
    def load() -> dict[str, str]:
        resp = requests.get(CROSSWALK, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        out = {}
        for row in csv.DictReader(io.StringIO(resp.text)):
            sleeper, espn = (row.get("sleeper_id") or "").split(".")[0], (row.get("espn_id") or "").split(".")[0]
            if sleeper and espn and sleeper != "NA" and espn != "NA":
                out[sleeper] = espn
        return out
    return cache.cached(("espn_ids",), 86400, load)


def pick_line(lines: dict[str, float], pos: str) -> dict[str, Any] | None:
    for type_id, label in BY_POSITION.get(pos, ()):
        if type_id in lines:
            return {"label": f"VEGAS {label}", "line": lines[type_id]}
    return None


def yards_line(player_id: str, pos: str, team: str, season: int, week: int) -> dict[str, Any] | None:
    """His over/under for the game, e.g. {"label": "VEGAS REC YDS", "line": 38.5}."""
    if pos not in BY_POSITION or not team:
        return None
    espn = espn_ids().get(str(player_id))
    if not espn:
        return None
    board = cache.cached(("espn_board", season, week), 3600,
                         lambda: fetch(SCOREBOARD, {"dates": season, "seasontype": 2, "week": week}))
    event = event_for_team(board, team)
    return pick_line(game_lines(event).get(espn, {}), pos) if event else None
