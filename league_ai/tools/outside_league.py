"""Leagues outside Legacy: an owner pastes another league's Sleeper ID (or link) and the GM reads that whole league.

Read-only, on demand, cached for an hour per league: four small public Sleeper calls (league, rosters, users,
traded picks), no sign-in. Player names come from our own sleeper_players table, never Sleeper's 5 MB player dump.
This is the only place the GM reads Sleeper for a league other than ours; Legacy itself doesn't depend on it.
ESPN and Yahoo leagues come in as screenshots instead (Yahoo needs each user's sign-in, private ESPN leagues need
browser cookies).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Mapping

from league_ai.tools import ToolSpec

SLEEPER = "https://api.sleeper.app/v1"
CACHE_SECONDS = 60 * 60
CACHE_MAX = 30
_ID = re.compile(r"(\d{12,20})")
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()

LEAGUE_TYPES = {0: "redraft", 1: "keeper", 2: "dynasty"}
# The scoring settings that change player values most; the rest is listed only when it isn't the usual default.
KEY_SCORING = {
    "rec": "points per catch", "bonus_rec_te": "TE premium per catch", "bonus_rec_rb": "RB bonus per catch",
    "bonus_rec_wr": "WR bonus per catch", "pass_td": "pass TD", "pass_yd": "per passing yard", "pass_int": "interception",
    "rush_yd": "per rushing yard", "rec_yd": "per receiving yard", "rush_td": "rush TD", "rec_td": "rec TD",
    "rush_att": "per carry", "pass_cmp": "per completion", "rec_fd": "per receiving first down", "rush_fd": "per rushing first down",
    "fum_lost": "fumble lost",
}


def league_id_from(text: Any) -> str | None:
    """A Sleeper league ID from an ID or a sleeper.com link."""
    m = _ID.search(str(text or ""))
    return m.group(1) if m else None


def _get(url: str) -> Any:
    import requests

    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    return resp.json()


def _player_names(client: Any, ids: list[str]) -> dict[str, str]:
    """Sleeper id -> 'Name POS TEAM', from our own player table."""
    out: dict[str, str] = {}
    ids = [i for i in dict.fromkeys(ids) if i and i.isdigit()]
    for start in range(0, len(ids), 150):
        chunk = ids[start:start + 150]
        try:
            rows = (client.table("sleeper_players").select("sleeper_player_id, full_name, position, team")
                    .in_("sleeper_player_id", chunk).execute().data or [])
        except Exception:
            rows = []
        for r in rows:
            out[str(r["sleeper_player_id"])] = " ".join(str(x) for x in (r.get("full_name"), r.get("position"), r.get("team") or "FA") if x)
    return out


def _scoring(settings: Mapping[str, Any], positions: list[str]) -> dict[str, Any]:
    s = {k: float(v) for k, v in (settings or {}).items() if isinstance(v, (int, float))}
    rec = s.get("rec", 0.0)
    fmt = ["PPR" if rec >= 1 else "half PPR" if rec >= 0.5 else "standard" if rec == 0 else f"{rec} PPR"]
    if any(p in ("SUPER_FLEX", "QB_FLEX") for p in positions) or positions.count("QB") >= 2:
        fmt.append("superflex")
    if s.get("bonus_rec_te"):
        fmt.append(f"TE premium +{s['bonus_rec_te']:g}")
    if s.get("pass_td", 4) >= 6:
        fmt.append("6-pt pass TD")
    if s.get("rec_fd") or s.get("rush_fd"):
        fmt.append("first-down points")
    return {"format": ", ".join(fmt), "key": {label: s[k] for k, label in KEY_SCORING.items() if s.get(k)}}


def _load(league_id: str) -> dict[str, Any]:
    now = time.time()
    with _lock:
        hit = _cache.get(league_id)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    league = _get(f"{SLEEPER}/league/{league_id}")
    if not league:
        raise LookupError("not_found")
    raw = {"league": league, "rosters": _get(f"{SLEEPER}/league/{league_id}/rosters") or [],
           "users": _get(f"{SLEEPER}/league/{league_id}/users") or []}
    try:
        raw["traded_picks"] = _get(f"{SLEEPER}/league/{league_id}/traded_picks") or []
    except Exception:
        raw["traded_picks"] = []
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
        _cache[league_id] = (now, raw)
    return raw


def summarize(raw: Mapping[str, Any], names: Callable[[list[str]], Mapping[str, str]], team: str | None = None) -> dict[str, Any]:
    """The league as the GM needs it: format, lineup, every team's record and roster (starters, bench, taxi, IR), and
    who holds traded picks. team narrows rosters to the teams whose owner or team name contains it."""
    league = raw.get("league") or {}
    settings = league.get("settings") or {}
    positions = [p for p in (league.get("roster_positions") or []) if p != "BN"]
    users = {str(u.get("user_id")): u for u in raw.get("users") or []}
    rosters = list(raw.get("rosters") or [])
    every = [str(p) for r in rosters for p in (r.get("players") or [])]
    label = dict(names(every))

    def name(pid: Any) -> str:
        pid = str(pid)
        return label.get(pid) or (f"{pid} D/ST" if not pid.isdigit() else f"player {pid}")

    def owner_of(r: Mapping[str, Any]) -> tuple[str, str]:
        u = users.get(str(r.get("owner_id"))) or {}
        return str(u.get("display_name") or f"Team {r.get('roster_id')}"), str((u.get("metadata") or {}).get("team_name") or "")

    roster_owner = {r.get("roster_id"): owner_of(r)[0] for r in rosters}
    if team and not any(team.lower() in " ".join(owner_of(r)).lower() for r in rosters):
        team = None  # no team by that name: show every roster
    teams = []
    for r in sorted(rosters, key=lambda r: (-(r.get("settings") or {}).get("wins", 0),
                                            -((r.get("settings") or {}).get("fpts", 0) + (r.get("settings") or {}).get("fpts_decimal", 0) / 100))):
        st = r.get("settings") or {}
        owner, team_name = owner_of(r)
        row: dict[str, Any] = {"owner": owner, "team": team_name,
                               "record": f"{st.get('wins', 0)}-{st.get('losses', 0)}" + (f"-{st['ties']}" if st.get("ties") else ""),
                               "points_for": round(st.get("fpts", 0) + st.get("fpts_decimal", 0) / 100, 1)}
        if team and team.lower() not in f"{owner} {team_name}".lower():
            teams.append(row)
            continue
        starters = [str(p) for p in (r.get("starters") or []) if str(p) != "0"]
        taxi = [str(p) for p in (r.get("taxi") or [])]
        ir = [str(p) for p in (r.get("reserve") or [])]
        bench = [str(p) for p in (r.get("players") or []) if str(p) not in set(starters) | set(taxi) | set(ir)]
        row.update(starters=[name(p) for p in starters], bench=[name(p) for p in bench], taxi=[name(p) for p in taxi], ir=[name(p) for p in ir])
        teams.append(row)
    picks = [f"{p.get('season')} round {p.get('round')}: {roster_owner.get(p.get('roster_id'), p.get('roster_id'))}'s pick, owned by "
             f"{roster_owner.get(p.get('owner_id'), p.get('owner_id'))}"
             for p in raw.get("traded_picks") or [] if p.get("owner_id") != p.get("roster_id")]
    return {
        "league": league.get("name"), "season": league.get("season"), "status": league.get("status"),
        "type": LEAGUE_TYPES.get(settings.get("type"), "unknown"), "teams_count": len(rosters),
        "scoring": _scoring(league.get("scoring_settings") or {}, positions), "starting_lineup": positions,
        "taxi_slots": settings.get("taxi_slots"), "ir_slots": settings.get("reserve_slots"),
        "playoff_teams": settings.get("playoff_teams"), "playoff_week_start": settings.get("playoff_week_start"),
        "teams": teams, "traded_picks": picks,
        "note": "Another league, not Legacy: none of Legacy's contracts, cap, rules or owners apply here.",
    }


def outside_league_tools(client: Any, own_sleeper_id: str | None = None) -> list[ToolSpec]:
    def get_outside_league(league: str, team: str | None = None) -> dict[str, Any]:
        league_id = league_id_from(league)
        if not league_id:
            return {"error": "That isn't a Sleeper league ID. It's the long number in the league's sleeper.com link "
                             "(Sleeper app: league settings, then share/copy the league ID)."}
        if own_sleeper_id and league_id == str(own_sleeper_id):
            return {"error": "That's this league (Legacy). Use the league state and the normal tools."}
        try:
            raw = _load(league_id)
        except LookupError:
            return {"error": "Sleeper has no league with that ID. Ask the owner to double-check it."}
        except Exception as exc:
            return {"error": f"Couldn't reach Sleeper right now ({type(exc).__name__}). Ask for screenshots instead."}
        return summarize(raw, lambda ids: _player_names(client, ids), team)

    return [ToolSpec(
        name="get_outside_league",
        description=("Read a fantasy league OUTSIDE Legacy that the owner also plays in, from Sleeper: its format and scoring "
                     "(PPR, superflex, TE premium), starting lineup, every team's record, points and roster (starters, bench, "
                     "taxi, IR) and traded picks. Use it when the owner gives a Sleeper league ID or link, or asks about "
                     "'my other league' and your notes have its ID. Pass team (an owner or team name) to list only that "
                     "team's full roster. Only Sleeper leagues; ESPN, Yahoo and others come as screenshots."),
        input_schema={"type": "object", "properties": {
            "league": {"type": "string", "description": "The Sleeper league ID (a long number) or a sleeper.com league link"},
            "team": {"type": "string", "description": "Optional owner or team name, to list just that team's roster"},
        }, "required": ["league"]},
        fn=get_outside_league,
    )]
