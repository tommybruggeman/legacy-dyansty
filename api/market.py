"""Market tab: free agents to browse, every player in the league, and contracts about to expire.

Bidding and the rookie draft stay on the website, so this is read only.
Free agents are ranked by season points, with points per game from the
website's Free Agent page (services.free_agents.load_ranking_ppg) shown too.
Owners can save players as transfer targets (table market_targets).
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Callable

from services.free_agents import load_ranking_ppg

POSITIONS = ("QB", "RB", "WR", "TE", "K")
LIST_SIZE = 150
CACHE_SECONDS = 3600

# Player pool and PPG are the same for every owner and change at most
# nightly, so one copy per server instance is plenty.
_cache: dict[Any, tuple[float, Any]] = {}


def _cached(key: Any, load: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = load()
    _cache[key] = (now + CACHE_SECONDS, value)
    return value


def clear() -> None:
    _cache.clear()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_pool(client: Any, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        batch = (
            client.table("nfl_players")
            .select("sleeper_id,full_name,position,team,age,injury_status,years_exp")
            .in_("position", list(POSITIONS))
            .eq("active", True)
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def player_pool(client: Any) -> list[dict[str, Any]]:
    return _cached("pool", lambda: _load_pool(client))


def ranking(client: Any, season: int) -> Any:
    return _cached(("ppg", season), lambda: load_ranking_ppg(client, active_season=season))


def season_points(ppg: Any, pid: str) -> float | None:
    """Points in the ranking season: PPG times games played."""
    rate, games = ppg.ranking_ppg.get(pid), getattr(ppg, "ranking_games", {}).get(pid)
    return round(rate * games, 1) if rate is not None and games else None


def _rank_key(r: dict[str, Any]) -> tuple:
    return (r["points"] is None, -(r["points"] or 0), r["rank_ppg"] is None, -(r["rank_ppg"] or 0), r["name"])


def free_agents(
    pool: list[dict[str, Any]],
    state: dict[str, Any],
    ppg: Any,
    limit: int = LIST_SIZE,
    rostered: Iterable[str] = (),
) -> dict[str, Any]:
    """Everyone in the player pool who isn't under contract or on a Sleeper roster in this league."""
    signed = {_text(r.get("sleeper_player_id")) for r in state.get("roster", [])} | {_text(p) for p in rostered}
    rows = []
    for player in pool:
        pid = _text(player.get("sleeper_id"))
        if not pid or pid in signed:
            continue
        rows.append({
            "player_id": pid,
            "name": _text(player.get("full_name")) or pid,
            "pos": _text(player.get("position")),
            "nfl_team": _text(player.get("team")) or "FA",
            "injury": _text(player.get("injury_status")) or None,
            "rookie": player.get("years_exp") == 0,
            "ppg": ppg.current_ppg.get(pid),
            "last_ppg": ppg.last_ppg.get(pid),
            "rank_ppg": ppg.ranking_ppg.get(pid),
            "points": season_points(ppg, pid),
        })
    rows.sort(key=_rank_key)
    return {
        "season": ppg.active_season,
        "last_season": ppg.last_season,
        "season_started": ppg.active_season_started,
        "players": rows[:limit],
    }


FA_IN_SEARCH = 500


def _contract(row: dict[str, Any]) -> tuple[float, int]:
    return round(float(row.get("contract_cap_hit") or row.get("cap_hit") or 0), 2), int(row.get("contract_years_left") or 0)


def all_players(
    pool: list[dict[str, Any]],
    state: dict[str, Any],
    ppg: Any,
    league_teams: list[dict[str, Any]],
    sleeper_rosters: list[dict[str, Any]],
    my_team_id: str | None,
    fa_limit: int = FA_IN_SEARCH,
) -> dict[str, Any]:
    """Every rostered player plus the best free agents, for league-wide search and filters.

    Sleeper rosters say who is rostered (taxi and IR included) and on which team;
    contract rows add salary, years left and rookie deals. Free agents are the
    same pool as free_agents(), cut to the top `fa_limit` by points.
    """
    by_roster = {t["roster_id"]: t for t in league_teams if t.get("roster_id") is not None}
    by_team = {t["team_id"]: t for t in league_teams}
    sleeper_team: dict[str, dict[str, Any]] = {}
    designation: dict[str, str] = {}
    for roster in sleeper_rosters:
        team = by_roster.get(roster.get("roster_id"))
        for key in ("players", "taxi", "reserve"):
            for pid in roster.get(key) or []:
                pid = _text(pid)
                if not pid:
                    continue
                if team:
                    sleeper_team[pid] = team
                if key != "players":
                    designation[pid] = "taxi" if key == "taxi" else "ir"
    contracts = {_text(r.get("sleeper_player_id")): r for r in state.get("roster", []) if _text(r.get("sleeper_player_id"))}
    players = {_text(p.get("sleeper_id")): p for p in pool if _text(p.get("sleeper_id"))}
    mine = _text(my_team_id)

    def row(pid: str) -> dict[str, Any]:
        player, contract = players.get(pid, {}), contracts.get(pid)
        team = (by_team.get(_text((contract or {}).get("league_team_id"))) if contract else None) or sleeper_team.get(pid)
        salary, years = _contract(contract) if contract else (None, None)
        return {
            "player_id": pid,
            "name": _text(player.get("full_name")) or _text((contract or {}).get("player_name")) or pid,
            "pos": _text(player.get("position")) or _text((contract or {}).get("pos")),
            "nfl_team": _text(player.get("team")) or "FA",
            "age": player.get("age"),
            "injury": _text(player.get("injury_status")) or None,
            "rookie": player.get("years_exp") == 0,
            "rank_ppg": ppg.ranking_ppg.get(pid),
            "last_ppg": ppg.last_ppg.get(pid),
            "points": season_points(ppg, pid),
            "team_id": team["team_id"] if team else None,
            "owner_name": team["owner_name"] if team else None,
            "is_mine": bool(team) and team["team_id"] == mine,
            "salary": salary,
            "years_left": years,
            "rookie_deal": bool((contract or {}).get("is_rookie")),
            "designation": designation.get(pid),
        }

    rostered_ids = set(contracts) | set(sleeper_team)
    rostered = [row(pid) for pid in rostered_ids]
    free = [row(pid) for pid in players if pid not in rostered_ids and _text(players[pid].get("position")) in POSITIONS]
    free.sort(key=_rank_key)
    rows = rostered + free[:fa_limit]
    rows.sort(key=_rank_key)
    owners = sorted(league_teams, key=lambda t: (t["team_id"] != mine, t["owner_name"].lower()))
    return {
        "season": ppg.active_season,
        "last_season": ppg.last_season,
        "season_started": ppg.active_season_started,
        "players": rows,
        "owners": [{"team_id": t["team_id"], "owner_name": t["owner_name"], "team_name": t["team_name"], "is_mine": t["team_id"] == mine}
                   for t in owners],
    }


def _words(text: str) -> str:
    return " ".join("".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").split())


def name_matches(name: str, needle: str) -> bool:
    """'aj brown' finds A.J. Brown, 'mc' finds McCaffrey (same rule as the app's search)."""
    hay = _words(name)
    return all(word in hay for word in _words(needle).split())


SALARY_CAPS = {"Under $5": 5, "Under $10": 10, "Under $20": 20}


def filter_players(rows: list[dict[str, Any]], *, search: str = "", status: str = "All", pos: str = "All",
                   owner: str | None = None, salary: str = "Any", contract: str = "Any", sort: str = "Points") -> list[dict[str, Any]]:
    """The Players tab's filters, for the website. A name search always looks through the whole league."""
    def keep(p: dict[str, Any]) -> bool:
        if search.strip():
            if not name_matches(p["name"], search):
                return False
        elif status != "All" and (status == "Rostered") != bool(p["team_id"]):
            return False
        if pos != "All" and p["pos"] != pos:
            return False
        if owner and p["team_id"] != owner:
            return False
        pay = p["salary"]
        if salary != "Any" and (pay is None or (pay < 20 if salary == "$20+" else pay >= SALARY_CAPS[salary])):
            return False
        years = p["years_left"]
        return {
            "Any": True,
            "Expiring": years is not None and years <= 1,
            "2+ years": (years or 0) >= 2,
            "Rookie deal": p["rookie_deal"],
            "Taxi": p["designation"] == "taxi",
            "IR": p["designation"] == "ir",
        }[contract]

    out = [p for p in rows if keep(p)]
    per_dollar = lambda p: p["rank_ppg"] / p["salary"] if p["rank_ppg"] is not None and p["salary"] else -1  # noqa: E731
    keys = {
        "PPG": lambda p: -(p["rank_ppg"] if p["rank_ppg"] is not None else -1),
        "Value per $": lambda p: -per_dollar(p),
        "Youngest": lambda p: p["age"] if p["age"] is not None else 99,
    }
    return sorted(out, key=keys[sort]) if sort in keys else out


def nfl_teams(pool: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {_text(p.get("sleeper_id")): _text(p.get("team")) or "FA" for p in pool}


def expiring(state: dict[str, Any], my_team_id: str | None, years_out: int = 0,
             pool: Iterable[dict[str, Any]] = (), ppg: Any = None) -> list[dict[str, Any]]:
    """Contracts ending after this season (years_out=0) or a later one, most points first.

    These players hit free agency at that season's rollover unless extended.
    """
    nfl = nfl_teams(pool)
    rows = []
    for r in state.get("roster", []):
        left = int(r.get("contract_years_left") or 0)
        if left != years_out + 1 and not (years_out == 0 and left < 1):
            continue
        team_id = _text(r.get("league_team_id"))
        rows.append({
            "player_id": _text(r.get("sleeper_player_id")),
            "name": _text(r.get("player_name")) or _text(r.get("sleeper_player_id")),
            "pos": _text(r.get("pos")),
            "owner_name": _text(r.get("owner_name")),
            "team_id": team_id,
            "salary": round(float(r.get("contract_cap_hit") or r.get("cap_hit") or 0), 2),
            "is_mine": bool(my_team_id) and team_id == _text(my_team_id),
        })
    for row in rows:
        pid = row["player_id"]
        row["nfl_team"] = nfl.get(pid, "FA")
        row["rank_ppg"] = ppg.ranking_ppg.get(pid) if ppg else None
        row["points"] = season_points(ppg, pid) if ppg else None
    rows.sort(key=lambda r: (r["points"] is None, -(r["points"] or 0), -r["salary"], r["name"]))
    return rows


# ---- Transfer targets ---------------------------------------------------------------

def target_ids(client: Any, league_id: str) -> list[str]:
    """Players this owner saved, newest first. Row security limits it to their own list."""
    rows = (
        client.table("market_targets").select("player_id,created_at")
        .eq("league_id", league_id).order("created_at", desc=True).execute().data
        or []
    )
    return [_text(r.get("player_id")) for r in rows if r.get("player_id")]


def save_target(client: Any, league_id: str, player_id: str) -> None:
    client.table("market_targets").upsert({"league_id": league_id, "player_id": player_id},
                                          on_conflict="league_id,user_id,player_id", ignore_duplicates=True).execute()


def drop_target(client: Any, league_id: str, player_id: str) -> None:
    client.table("market_targets").delete().eq("league_id", league_id).eq("player_id", player_id).execute()


def targets(ids: Iterable[str], state: dict[str, Any], my_team_id: str | None,
            pool: Iterable[dict[str, Any]], ppg: Any) -> list[dict[str, Any]]:
    """Saved targets with who has them now (or free agent), salary and points, in saved order."""
    signed = {_text(r.get("sleeper_player_id")): r for r in state.get("roster", [])}
    players = {_text(p.get("sleeper_id")): p for p in pool}
    out = []
    for pid in ids:
        row, player = signed.get(pid), players.get(pid, {})
        name = _text((row or {}).get("player_name")) or _text(player.get("full_name"))
        if not name:
            continue
        team_id = _text((row or {}).get("league_team_id"))
        out.append({
            "player_id": pid,
            "name": name,
            "pos": _text((row or {}).get("pos")) or _text(player.get("position")),
            "nfl_team": _text(player.get("team")) or "FA",
            "owner_name": _text((row or {}).get("owner_name")) or None,
            "team_id": team_id or None,
            "salary": round(float((row or {}).get("contract_cap_hit") or (row or {}).get("cap_hit") or 0), 2) if row else None,
            "years_left": int((row or {}).get("contract_years_left") or 0) if row else None,
            "is_mine": bool(my_team_id) and team_id == _text(my_team_id),
            "rank_ppg": ppg.ranking_ppg.get(pid),
            "points": season_points(ppg, pid),
        })
    return out


def strategy_question(saved: list[dict[str, Any]], season: int) -> str:
    """The question the Plan button sends to the Front Office."""
    if not saved:
        return f"Plan my offseason after the {season} season."
    names = [f"{t['name']} ({t['owner_name']})" if t.get("owner_name") else f"{t['name']} (free agent)" for t in saved]
    listed = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
    return (f"Plan my offseason. My transfer targets are {listed}. "
            "Which ones should I go after, in what order, and should I trade for each now or wait for the auction?")
