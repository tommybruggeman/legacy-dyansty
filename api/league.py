"""Standings and matchups for the app, using the website's own rules."""
from __future__ import annotations

from typing import Any

import pandas as pd

from api import sleeper
from season_engine import SeasonResolver
from services.app_context import _aggregate_standings, _standings_rank, as_number, STANDINGS_SORT
from services.matchups_view import build_games, week_summary
from services.standings_rules import last_completed_week, score_week

PLAYOFF_TEAMS = 4

# Live scores: Sleeper is asked at most this often, however many owners are
# watching, so the app can refresh every 30 seconds and stay close to Sleeper.
LIVE_TTL = 15


def sleeper_league_id(client: Any, league_id: str) -> str:
    season = SeasonResolver(client).get_active_season(league_id)
    if not season.sleeper_league_id:
        raise LookupError("The active season isn't linked to a Sleeper league yet.")
    return season.sleeper_league_id


def roster_names(client: Any, league_id: str, sleeper_id: str) -> dict[int, str]:
    """roster_id -> owner name, like the Standings and Matchups pages:
    league_teams owner name, then team name, then the Sleeper display name."""
    app_names: dict[int, str] = {}
    rows = (
        client.table("league_teams")
        .select("sleeper_roster_id,owner_name,team_name")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )
    for row in rows:
        name = str(row.get("owner_name") or row.get("team_name") or "").strip()
        try:
            roster_id = int(row.get("sleeper_roster_id"))
        except (TypeError, ValueError):
            continue
        if name:
            app_names[roster_id] = name

    users = sleeper.get(f"league/{sleeper_id}/users") or []
    rosters = sleeper.get(f"league/{sleeper_id}/rosters") or []
    display = {
        u.get("user_id"): (u.get("display_name") or u.get("username") or "").strip() for u in users
    }
    return {
        r.get("roster_id"): app_names.get(r.get("roster_id"), display.get(r.get("owner_id")) or f"Roster {r.get('roster_id')}")
        for r in rosters
        if r.get("roster_id") is not None
    }


def last_scored_week(sleeper_id: str) -> int:
    try:
        league = sleeper.get(f"league/{sleeper_id}")
    except Exception:
        league = None
    try:
        state = sleeper.get("state/nfl")
    except Exception:
        state = None
    return min(last_completed_week(league, state), 25)


def current_week() -> int:
    try:
        week = int((sleeper.get("state/nfl") or {}).get("week") or 1)
    except Exception:
        week = 1
    return max(1, min(week, 25))


def regular_season_end(sleeper_id: str) -> int:
    """The last regular-season week (the week before the playoffs start)."""
    try:
        start = int(((sleeper.get(f"league/{sleeper_id}") or {}).get("settings") or {}).get("playoff_week_start") or 0)
    except (TypeError, ValueError):
        start = 0
    return start - 1 if start > 1 else 25


def standings(sleeper_id: str, names: dict[int, str], my_roster_id: int | None) -> dict[str, Any]:
    """Season standings, scored the same way as build_standings_from_sleeper (regular season only)."""
    latest = min(last_scored_week(sleeper_id), regular_season_end(sleeper_id))
    frames = []
    for week in range(1, latest + 1):
        # Finished weeks never change, so they can stay cached for a day.
        rows = sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=86400) or []
        by_mid: dict[Any, list[dict]] = {}
        for row in rows:
            if row.get("matchup_id") is not None:
                by_mid.setdefault(row["matchup_id"], []).append(row)

        results = []
        for pair in by_mid.values():
            if len(pair) < 2:
                continue
            a, b = pair[0], pair[1]
            pa, pb = as_number(a.get("points")), as_number(b.get("points"))
            sa, sb = as_number(a.get("starters_points")), as_number(b.get("starters_points"))
            if pa < 10 <= sa:
                pa = sa
            if pb < 10 <= sb:
                pb = sb
            # Teams are keyed by roster id so renamed owners never split a row.
            results.append((str(a.get("roster_id")), pa, pb))
            results.append((str(b.get("roster_id")), pb, pa))

        if not results or all(s == 0 and o == 0 for _, s, o in results):
            continue
        frame = pd.DataFrame(score_week(results))
        frame["week"] = week
        frames.append(frame)

    if not frames:
        return {"weeks_played": 0, "playoff_teams": PLAYOFF_TEAMS, "rows": []}

    big = pd.concat(frames, ignore_index=True)
    table = _aggregate_standings(big).sort_values(STANDINGS_SORT, ascending=False)
    last_week = big["week"].max()
    previous = big[big["week"] < last_week]
    prev_rank = _standings_rank(_aggregate_standings(previous)) if not previous.empty else {}

    rows = []
    for rank, rec in enumerate(table.to_dict("records"), start=1):
        roster_id = int(rec["Team"])
        games = int(rec["Games"]) or 1
        rows.append({
            "rank": rank,
            "prev_rank": prev_rank.get(rec["Team"]),
            "roster_id": roster_id,
            "name": names.get(roster_id, f"Roster {roster_id}"),
            "is_me": roster_id == my_roster_id,
            "standing_points": int(rec["Standing Points"]),
            "wins": int(rec["Wins"]),
            "losses": int(rec["Losses"]),
            "ties": int(rec["Ties"]),
            "top5": int(rec["Top 5"]),
            "pf": round(float(rec["PF"]), 2),
            "pa": round(float(rec["PA"]), 2),
            "pf_per_game": round(float(rec["PF"]) / games, 1),
            "pa_per_game": round(float(rec["PA"]) / games, 1),
            "games": int(rec["Games"]),
        })
    return {"weeks_played": int(last_week), "playoff_teams": PLAYOFF_TEAMS, "rows": rows}


def matchups(sleeper_id: str, names: dict[int, str], week: int, my_roster_id: int | None) -> dict[str, Any]:
    final = week <= last_scored_week(sleeper_id)
    rows = sleeper.get(f"league/{sleeper_id}/matchups/{week}", ttl=86400 if final else LIVE_TTL) or []
    games = build_games(rows, names)
    summary = week_summary(games)
    return {
        "week": week,
        "is_final": final,
        "my_roster_id": my_roster_id,
        "games": games,
        "summary": summary,
    }


def sleeper_owners(sleeper_id: str) -> dict[str, int]:
    """player_id -> roster_id for everyone on a Sleeper roster, taxi and IR included.

    Sleeper is the source of truth for who is rostered; a player can be there
    before (or without) a contract row in the app, e.g. a fresh rookie pick.
    """
    out: dict[str, int] = {}
    for roster in sleeper.get(f"league/{sleeper_id}/rosters") or []:
        rid = roster.get("roster_id")
        for key in ("players", "taxi", "reserve"):
            for pid in roster.get(key) or []:
                if pid and rid is not None:
                    out[str(pid)] = int(rid)
    return out
