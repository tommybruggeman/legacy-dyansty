"""Market tab: free agents to browse and contracts about to expire.

Bidding and the rookie draft stay on the website, so this is read only.
Free agents are ranked by points per game like the website's Free Agent
page (services.free_agents.load_ranking_ppg).
"""
from __future__ import annotations

import time
from typing import Any, Callable

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


def free_agents(
    pool: list[dict[str, Any]],
    state: dict[str, Any],
    ppg: Any,
    limit: int = LIST_SIZE,
) -> dict[str, Any]:
    """Everyone in the player pool who isn't under contract in this league."""
    signed = {_text(r.get("sleeper_player_id")) for r in state.get("roster", [])}
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
        })
    rows.sort(key=lambda r: (r["rank_ppg"] is None, -(r["rank_ppg"] or 0), r["name"]))
    return {
        "season": ppg.active_season,
        "last_season": ppg.last_season,
        "season_started": ppg.active_season_started,
        "players": rows[:limit],
    }


def expiring(state: dict[str, Any], my_team_id: str | None, years_out: int = 0) -> list[dict[str, Any]]:
    """Contracts ending after this season (years_out=0) or a later one, biggest first.

    These players hit free agency at that season's rollover unless extended.
    """
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
    rows.sort(key=lambda r: (-r["salary"], r["name"]))
    return rows
