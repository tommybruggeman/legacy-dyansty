"""Team pages for the app: every team's roster, contracts, picks and activity.

Contracts, cap and picks come from the same authenticated read the website's
Teams page uses (read_canonical_team_state_authenticated), so the numbers match
the website. Lineups and points come from Sleeper.
"""
from __future__ import annotations

from typing import Any, Iterable

from api import sleeper
from services.team_roster_state import (
    calculate_team_financials,
    load_team_state,
    roster_designation,
    state_cap_adjustments,
)

DEFAULT_CAP = 225.0
CAP_YEARS = 3
LINEUP_SLOTS = {"QB", "RB", "WR", "TE", "K", "DEF", "FLEX", "SUPER_FLEX", "REC_FLEX", "WRRB_FLEX"}


def _f(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def salary_cap(client: Any, league_id: str) -> float:
    try:
        rows = client.table("league_rules").select("salary_cap").eq("league_id", league_id).limit(1).execute().data or []
    except Exception:
        rows = []
    return _f((rows[0] if rows else {}).get("salary_cap")) or DEFAULT_CAP


def league_teams(client: Any, league_id: str) -> list[dict[str, Any]]:
    rows = (
        client.table("league_teams")
        .select("id,owner_name,team_name,sleeper_roster_id")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )
    teams = []
    for row in rows:
        try:
            roster_id = int(row.get("sleeper_roster_id"))
        except (TypeError, ValueError):
            roster_id = None
        owner = _text(row.get("owner_name"))
        if not owner or owner.lower() == "none":
            continue
        teams.append({
            "team_id": str(row["id"]),
            "owner_name": owner,
            "team_name": _text(row.get("team_name")) or owner,
            "roster_id": roster_id,
        })
    return teams


def team_leans(client: Any, league_id: str) -> dict[str, str]:
    """Contending / middle / rebuilding, from the trade engine's owner profiles."""
    try:
        rows = client.table("league_ai_owner_trade_styles").select("owner_name,lean").eq("league_id", league_id).execute().data or []
    except Exception:
        rows = []
    return {_text(r.get("owner_name")).casefold(): r.get("lean") for r in rows if r.get("lean")}


def seasons_state(client: Any, league_id: str, season: int) -> list[dict[str, Any]]:
    """League-wide team state for this season and the next two (for cap by year)."""
    states = [load_team_state(client, league_id, season)]
    for year in range(season + 1, season + CAP_YEARS):
        try:
            states.append(load_team_state(client, league_id, year))
        except Exception:
            states.append({"season": year, "roster": [], "dead_cap": [], "cap_adjustments": []})
    return states


def cap_by_year(states: list[dict[str, Any]], cap: float, team_id: str) -> list[dict[str, Any]]:
    out = []
    for state in states:
        money = calculate_team_financials(
            state.get("roster", []),
            state_cap_adjustments({"dead_cap": state.get("dead_cap", []), "cap_adjustments": state.get("cap_adjustments", [])}),
            salary_cap=cap,
            league_team_id=team_id,
        )
        out.append({
            "season": int(state["season"]),
            "used": _f(money["cap_used"]),
            "space": _f(money["cap_space"]),
            "dead_cap": _f(money["dead_cap"]),
        })
    return out


def player_info(client: Any, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = sorted({_text(i) for i in ids if _text(i)})
    info: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 200):
        try:
            rows = (
                client.table("nfl_players")
                .select("sleeper_id,full_name,position,team,age,injury_status,injury_body_part")
                .in_("sleeper_id", ids[start:start + 200])
                .execute()
                .data
                or []
            )
        except Exception:
            rows = []
        for row in rows:
            info[str(row["sleeper_id"])] = row
    return info


def player_ppg(sleeper_id: str, weeks: int) -> dict[str, float]:
    """Average fantasy points per game in this league's scoring, from Sleeper.

    Weeks a player scored exactly zero (bye, inactive) don't count as games.
    """
    totals: dict[str, list[float]] = {}
    for week in range(1, weeks + 1):
        rows = sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=86400) or []
        for row in rows:
            for pid, pts in (row.get("players_points") or {}).items():
                if pts:
                    totals.setdefault(str(pid), []).append(float(pts))
    return {pid: round(sum(v) / len(v), 1) for pid, v in totals.items()}


def summaries(
    teams: list[dict[str, Any]],
    states: list[dict[str, Any]],
    cap: float,
    standings_rows: list[dict[str, Any]],
    leans: dict[str, str],
) -> list[dict[str, Any]]:
    """One line per team for the Teams list and the league cap page, in standings order."""
    by_roster = {row["roster_id"]: row for row in standings_rows}
    out = []
    for team in teams:
        stand = by_roster.get(team["roster_id"], {})
        years = cap_by_year(states, cap, team["team_id"])
        out.append({
            **team,
            "rank": stand.get("rank"),
            "wins": stand.get("wins", 0),
            "losses": stand.get("losses", 0),
            "ties": stand.get("ties", 0),
            "standing_points": stand.get("standing_points", 0),
            "is_me": bool(stand.get("is_me")),
            "lean": leans.get(team["owner_name"].casefold()),
            "cap_space": years[0]["space"],
            "cap_by_year": years,
        })
    out.sort(key=lambda t: (t["rank"] is None, t["rank"] or 0, t["owner_name"]))
    return out


def _sleeper_roster(sleeper_id: str, roster_id: int | None) -> dict[str, Any]:
    if roster_id is None:
        return {}
    rosters = sleeper.get(f"league/{sleeper_id}/rosters") or []
    return next((r for r in rosters if r.get("roster_id") == roster_id), {})


def roster_rows(
    state: dict[str, Any],
    team_id: str,
    lineup: dict[str, Any],
    slots: list[str],
    info: dict[str, dict[str, Any]],
    ppg: dict[str, float],
) -> dict[str, Any]:
    """Starters in lineup order, then bench, plus the taxi and IR players."""
    contracts = {}
    for row in state.get("roster", []):
        if _text(row.get("league_team_id")) != team_id:
            continue
        pid = _text(row.get("sleeper_player_id"))
        meta = info.get(pid, {})
        contracts[pid] = {
            "player_id": pid,
            "name": _text(row.get("player_name")) or _text(meta.get("full_name")) or pid,
            "pos": _text(row.get("pos")) or _text(meta.get("position")),
            "nfl_team": _text(meta.get("team")) or "FA",
            "injury": _text(meta.get("injury_status")) or None,
            "salary": _f(row.get("cap_hit")),
            "contract_salary": _f(row.get("contract_cap_hit") or row.get("cap_hit")),
            "years_left": int(row.get("contract_years_left") or 0),
            "designation": roster_designation(row),
            "is_rookie": bool(row.get("is_rookie")),
            "ppg": ppg.get(pid),
        }

    starter_ids = [_text(p) for p in lineup.get("starters") or []]
    lineup_slots = [s for s in slots if s in LINEUP_SLOTS]
    starters = []
    for index, pid in enumerate(starter_ids):
        player = contracts.get(pid)
        # The app's Taxi and IR designations win over a stale Sleeper lineup.
        if player and not player["designation"]:
            slot = lineup_slots[index] if index < len(lineup_slots) else player["pos"]
            starters.append({**player, "slot": "FLX" if "FLEX" in slot else slot})

    taken = {p["player_id"] for p in starters}
    others = [p for p in contracts.values() if p["player_id"] not in taken]
    taxi = [p for p in others if p["designation"] == "taxi"]
    ir = [p for p in others if p["designation"] == "ir"]
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4}
    bench = sorted(
        (p for p in others if not p["designation"]),
        key=lambda p: (order.get(p["pos"], 9), -(p["ppg"] or 0), p["name"]),
    )
    return {"starters": starters, "bench": bench, "taxi": taxi, "ir": ir, "count": len(contracts)}


def contracts_view(players: list[dict[str, Any]], state: dict[str, Any], team_id: str) -> dict[str, Any]:
    biggest = sorted(players, key=lambda p: -p["contract_salary"])[:8]
    season = int(state["season"])
    dead = [
        {"player": _text(r.get("player_name")) or "Player", "amount": _f(r.get("amount"))}
        for r in state_cap_adjustments(state)
        if _text(r.get("league_team_id")) == team_id
        and r.get("adjustment_type") in {"dropped_player_charge", "trade_retained_salary"}
    ]
    return {
        "biggest": [{**p, "through": season + max(p["years_left"], 1) - 1} for p in biggest],
        "expiring": [p for p in players if p["years_left"] <= 1],
        "dead_cap": dead,
    }


def picks_view(state: dict[str, Any], team_id: str) -> dict[str, Any]:
    owned, traded_away = [], []
    for row in state.get("draft_picks", []):
        if _text(row.get("asset_status")) not in {"", "active", "available", "owned"}:
            continue
        current = _text(row.get("current_owner_league_team_id"))
        original = _text(row.get("original_league_team_id"))
        pick = {
            "year": int(row.get("draft_year") or 0),
            "round": int(row.get("round_number") or 0),
            "original_owner": _text(row.get("original_owner_name")),
            "current_owner": _text(row.get("current_owner_name")),
            "own": current == original,
        }
        if current == team_id:
            owned.append(pick)
        elif original == team_id:
            traded_away.append(pick)
    key = lambda p: (p["year"], p["round"], p["original_owner"])  # noqa: E731
    return {"owned": sorted(owned, key=key), "traded_away": sorted(traded_away, key=key)}


def activity_view(state: dict[str, Any], team_id: str, limit: int = 25) -> list[dict[str, Any]]:
    rows = [r for r in state.get("activity", []) if _text(r.get("league_team_id")) == team_id]
    return [
        {
            "kind": "SIGN" if r.get("event_type") == "signed" else "CUT",
            "player": _text(r.get("player_name")) or "Player",
            "at": r.get("effective_at") or r.get("created_at"),
        }
        for r in rows[:limit]
    ]


def head_to_head(sleeper_id: str, weeks: int, me: int | None, them: int | None) -> dict[str, Any] | None:
    if me is None or them is None or me == them:
        return None
    wins = losses = ties = 0
    last = None
    for week in range(1, weeks + 1):
        rows = sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=86400) or []
        mine = next((r for r in rows if r.get("roster_id") == me), None)
        if not mine or mine.get("matchup_id") is None:
            continue
        theirs = next((r for r in rows if r.get("roster_id") == them and r.get("matchup_id") == mine["matchup_id"]), None)
        if not theirs:
            continue
        a, b = _f(mine.get("points")), _f(theirs.get("points"))
        wins, losses, ties = wins + (a > b), losses + (a < b), ties + (a == b)
        last = {"week": week, "me": a, "them": b}
    if last is None:
        return None
    return {"wins": wins, "losses": losses, "ties": ties, "last": last}


def team_detail(
    *,
    team: dict[str, Any],
    summary: dict[str, Any],
    standing: dict[str, Any] | None,
    state: dict[str, Any],
    sleeper_id: str,
    slots: list[str],
    info: dict[str, dict[str, Any]],
    ppg: dict[str, float],
    h2h: dict[str, Any] | None,
) -> dict[str, Any]:
    roster = roster_rows(state, team["team_id"], _sleeper_roster(sleeper_id, team["roster_id"]), slots, info, ppg)
    everyone = roster["starters"] + roster["bench"] + roster["taxi"] + roster["ir"]
    return {
        **team,
        "is_me": summary["is_me"],
        "lean": summary["lean"],
        "record": {
            "wins": summary["wins"], "losses": summary["losses"], "ties": summary["ties"],
            "rank": summary["rank"], "standing_points": summary["standing_points"],
            "pf_per_game": (standing or {}).get("pf_per_game"),
        },
        "cap": {"space": summary["cap_space"], "by_year": summary["cap_by_year"]},
        "roster": roster,
        "contracts": contracts_view(everyone, state, team["team_id"]),
        "picks": picks_view(state, team["team_id"]),
        "activity": activity_view(state, team["team_id"]),
        "head_to_head": h2h,
    }


def box_score(
    sleeper_id: str,
    week: int,
    matchup_id: int,
    names: dict[int, str],
    slots: list[str],
    info: dict[str, dict[str, Any]] | None = None,
    ttl: int = 60,
) -> dict[str, Any] | None:
    """Both lineups of one game, slot by slot."""
    rows = [r for r in (sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=ttl) or []) if r.get("matchup_id") == matchup_id]
    if len(rows) < 2:
        return None
    lineup_slots = [s for s in slots if s in LINEUP_SLOTS]
    sides = []
    for row in rows[:2]:
        starters = [_text(p) for p in row.get("starters") or []]
        points = row.get("starters_points") or []
        players = []
        for index, pid in enumerate(starters):
            meta = (info or {}).get(pid, {})
            slot = lineup_slots[index] if index < len(lineup_slots) else ""
            players.append({
                "player_id": pid,
                "name": _text(meta.get("full_name")) or (pid if pid and pid != "0" else "Empty"),
                "slot": "FLX" if "FLEX" in slot else slot,
                "points": _f(points[index]) if index < len(points) else 0.0,
            })
        sides.append({
            "roster_id": row.get("roster_id"),
            "name": names.get(row.get("roster_id"), f"Roster {row.get('roster_id')}"),
            "score": _f(row.get("points")) or round(sum(p["points"] for p in players), 2),
            "players": players,
        })
    return {"week": week, "matchup_id": matchup_id, "teams": sides}


def starter_ids(sleeper_id: str, week: int, matchup_id: int, ttl: int = 60) -> list[str]:
    rows = sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=ttl) or []
    return [_text(p) for r in rows if r.get("matchup_id") == matchup_id for p in (r.get("starters") or [])]


def ir_rows(state: dict[str, Any], team_id: str) -> list[dict[str, Any]]:
    """The team's IR designation rows (one at most, by league rule)."""
    return [
        r for r in state.get("cap_adjustments", [])
        if r.get("adjustment_type") == "ir_adjustment" and _text(r.get("league_team_id")) == team_id and r.get("id")
    ]


def weekly_points(sleeper_id: str, weeks: int, player_id: str) -> list[dict[str, Any]]:
    out = []
    for week in range(1, weeks + 1):
        rows = sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=86400) or []
        row = next((r for r in rows if player_id in (r.get("players") or []) or player_id in (r.get("players_points") or {})), None)
        points = (row or {}).get("players_points", {}).get(player_id)
        started = bool(row) and player_id in (row.get("starters") or [])
        out.append({"week": week, "points": _f(points) if points is not None else None, "started": started})
    return out


def player_detail(
    player_id: str,
    state: dict[str, Any],
    meta: dict[str, Any],
    weeks: list[dict[str, Any]],
    my_team_id: str | None,
) -> dict[str, Any] | None:
    contract = next((r for r in state.get("roster", []) if _text(r.get("sleeper_player_id")) == player_id), None)
    if contract is None and not meta:
        return None
    scored = [w["points"] for w in weeks if w["points"]]
    years = int((contract or {}).get("contract_years_left") or 0)
    season = int(state.get("season") or 0)
    return {
        "player_id": player_id,
        "name": _text((contract or {}).get("player_name")) or _text(meta.get("full_name")) or player_id,
        "pos": _text((contract or {}).get("pos")) or _text(meta.get("position")),
        "nfl_team": _text(meta.get("team")) or "FA",
        "age": meta.get("age"),
        "injury": _text(meta.get("injury_status")) or None,
        "injury_detail": _text(meta.get("injury_body_part")) or None,
        "ppg": round(sum(scored) / len(scored), 1) if scored else None,
        "weeks": weeks[-8:],
        "contract": None if contract is None else {
            "team_id": _text(contract.get("league_team_id")),
            "owner_name": _text(contract.get("owner_name")),
            "salary": _f(contract.get("contract_cap_hit") or contract.get("cap_hit")),
            "cap_hit": _f(contract.get("cap_hit")),
            "years_left": years,
            "through": season + max(years, 1) - 1,
            "designation": roster_designation(contract),
            "is_rookie": bool(contract.get("is_rookie")),
        },
        "is_mine": bool(contract) and _text(contract.get("league_team_id")) == _text(my_team_id),
    }
