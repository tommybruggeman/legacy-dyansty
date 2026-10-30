"""The weeks the app runs itself: matchups scored from app lineups, and the playoff bracket from our own standings.

Once the league switch is on (league_app_transactions.enabled), every week from `own_from_week` (default: the first
playoff week) is the app's. For those weeks this module builds rows in the same shape as Sleeper's
league/{id}/matchups/{week} and league/{id}/winners_bracket and hands them to api.sleeper as overrides, so every page
that reads matchups (Matchups, box scores, Home, Shotgun, standings) shows the app's numbers without its own code path.

- Lineups: league_lineups (the week's saved row, else the latest earlier one, else Sleeper's lineup that week).
- Points: our scoring engine (scoring/engine.py) over Sleeper's public weekly stat lines, with the league's saved rules.
- Pairings: regular-season weeks keep Sleeper's schedule; playoff weeks come from our bracket.
- Bracket: seeds are our standings (2 per win, 1 per top-5 week, points-for tiebreaker) through the last regular week.
  Higher score wins; a tie goes to the higher seed.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from api import lineups
from scoring import engine
from scoring.shadow import stat_lines

# Sleeper bracket layouts: (round, match, top slot, bottom slot, placement). A slot is a seed number, or ("w", m) /
# ("l", m) for the winner or loser of match m. Placement 1 is the final, 3 the third-place game.
LAYOUTS: dict[int, list[tuple[int, int, Any, Any, int | None]]] = {
    4: [(1, 1, 1, 4, None), (1, 2, 2, 3, None), (2, 3, ("w", 1), ("w", 2), 1), (2, 4, ("l", 1), ("l", 2), 3)],
    6: [(1, 1, 3, 6, None), (1, 2, 4, 5, None), (2, 3, 1, ("w", 2), None), (2, 4, 2, ("w", 1), None),
        (3, 5, ("w", 3), ("w", 4), 1), (3, 6, ("l", 3), ("l", 4), 3)],
    8: [(1, 1, 1, 8, None), (1, 2, 4, 5, None), (1, 3, 3, 6, None), (1, 4, 2, 7, None),
        (2, 5, ("w", 1), ("w", 2), None), (2, 6, ("w", 3), ("w", 4), None),
        (3, 7, ("w", 5), ("w", 6), 1), (3, 8, ("l", 5), ("l", 6), 3)],
}


def round_weeks(start: int, rounds: int, round_type: int) -> dict[int, list[int]]:
    """Round -> its weeks. Sleeper's playoff_round_type: 0 one week each, 1 a two-week final, 2 two weeks each."""
    out, week = {}, start
    for r in range(1, rounds + 1):
        span = 2 if round_type == 2 or (round_type == 1 and r == rounds) else 1
        out[r] = list(range(week, week + span))
        week += span
    return out


def build_bracket(seeds: list[int], points: Mapping[tuple[int, int], float], done: Callable[[int], bool],
                  weeks: Mapping[int, list[int]]) -> list[dict[str, Any]]:
    """Sleeper-shaped winners bracket. seeds: roster ids, best first. points: (week, roster_id) -> score.
    done(round) says whether that round's weeks are final, so a winner can be named."""
    layout = LAYOUTS.get(len(seeds)) or LAYOUTS[4]
    seed_of = {rid: i + 1 for i, rid in enumerate(seeds)}
    games: dict[int, dict[str, Any]] = {}

    def team(slot: Any) -> int | None:
        if isinstance(slot, int):
            return seeds[slot - 1] if slot <= len(seeds) else None
        kind, m = slot
        return games.get(m, {}).get(kind)

    for rnd, m, top, bottom, place in layout:
        t1, t2 = team(top), team(bottom)
        game: dict[str, Any] = {"r": rnd, "m": m, "t1": t1, "t2": t2, "w": None, "l": None}
        if place:
            game["p"] = place
        if isinstance(top, tuple):
            game["t1_from"] = {top[0]: top[1]}
        if isinstance(bottom, tuple):
            game["t2_from"] = {bottom[0]: bottom[1]}
        if t1 and t2 and done(rnd):
            s1 = sum(points.get((w, t1), 0.0) for w in weeks[rnd])
            s2 = sum(points.get((w, t2), 0.0) for w in weeks[rnd])
            win = t1 if s1 > s2 or (s1 == s2 and seed_of[t1] < seed_of[t2]) else t2
            game["w"], game["l"] = win, t2 if win == t1 else t1
        games[m] = game
    return list(games.values())


def week_pairs(bracket: Iterable[Mapping[str, Any]], week: int, weeks: Mapping[int, list[int]]) -> dict[int, int]:
    """roster_id -> matchup id for one playoff week (teams not playing that week are left out)."""
    out: dict[int, int] = {}
    for g in bracket:
        if week in weeks.get(int(g["r"]), []):
            for rid in (g.get("t1"), g.get("t2")):
                if rid:
                    out[int(rid)] = int(g["m"])
    return out


def team_row(roster: Mapping[str, Any], slots: list[str], saved: list[Mapping[str, Any]], sleeper_row: Mapping[str, Any] | None,
             lines: Mapping[str, Mapping[str, Any]], scoring: Mapping[str, Any], matchup_id: int | None) -> dict[str, Any]:
    """One team's Sleeper-shaped matchup row for an app week."""
    players = [str(p) for p in roster.get("players") or []]
    benched = {str(p) for p in (roster.get("taxi") or []) + (roster.get("reserve") or [])}
    eligible = {p for p in players if p not in benched}
    sleeper_starters = [str(p) for p in (sleeper_row or {}).get("starters") or []]
    ids = lineups.effective(slots, list(saved), sleeper_starters, eligible)
    starters = [pid or "0" for pid in ids]
    starters_points = [engine.lineup_points([pid], lines, scoring) for pid in starters]
    players_points = {p: engine.lineup_points([p], lines, scoring) for p in players}
    return {
        "roster_id": roster.get("roster_id"), "matchup_id": matchup_id, "starters": starters, "players": players,
        "points": round(sum(starters_points), 2), "starters_points": starters_points, "players_points": players_points,
        "custom_points": None, "source": "app",
    }


def build(*, season: int, own_from: int, current_week: int, league: Mapping[str, Any], rosters: list[Mapping[str, Any]],
          team_of_roster: Mapping[int, str], saved: list[Mapping[str, Any]], scoring: Mapping[str, Any], slots: list[str],
          sleeper_rows: Callable[[int], list[Mapping[str, Any]]], lines_for: Callable[[int], Mapping[str, Mapping[str, Any]]],
          standings_for: Callable[[dict[int, list[dict[str, Any]]]], list[int]]) -> dict[str, Any]:
    """Every app-owned week up to the current one: {"weeks": {week: rows}, "bracket": [...] or None}.

    standings_for(own_rows_so_far) returns roster ids in standings order through the last regular week.
    """
    settings = league.get("settings") or {}
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 4)
    playoff_teams = playoff_teams if playoff_teams in LAYOUTS else 4
    rounds = max(r for r, *_ in LAYOUTS[playoff_teams])
    weeks_by_round = round_weeks(playoff_start, rounds, int(settings.get("playoff_round_type") or 0))
    last_week = max(w for ws in weeks_by_round.values() for w in ws)

    saved_by_team: dict[str, list[Mapping[str, Any]]] = {}
    for row in sorted(saved, key=lambda r: -int(r.get("week") or 0)):
        saved_by_team.setdefault(str(row.get("league_team_id")), []).append(row)

    out: dict[int, list[dict[str, Any]]] = {}
    seeds: list[int] | None = None
    points: dict[tuple[int, int], float] = {}

    def resolved(before: int) -> list[dict[str, Any]]:  # rounds that ended before `before` name their winners
        return build_bracket(seeds or [], points, lambda r: max(weeks_by_round[r]) < before, weeks_by_round)

    for week in range(own_from, min(current_week, last_week) + 1):
        lines, raw = lines_for(week), {int(r["roster_id"]): r for r in sleeper_rows(week) if r.get("roster_id") is not None}
        if week >= playoff_start:
            if seeds is None:
                seeds = standings_for(out)[:playoff_teams]
            pairs: dict[int, Any] = week_pairs(resolved(week), week, weeks_by_round)
        else:
            pairs = {rid: r.get("matchup_id") for rid, r in raw.items()}
        rows = []
        for roster in rosters:
            rid = int(roster["roster_id"])
            team_saved = [r for r in saved_by_team.get(team_of_roster.get(rid, ""), []) if int(r.get("week") or 0) <= week]
            rows.append(team_row(roster, slots, team_saved, raw.get(rid), lines, scoring, pairs.get(rid)))
            points[(week, rid)] = rows[-1]["points"]
        out[week] = rows
    if seeds is None and current_week >= playoff_start and own_from <= playoff_start:
        seeds = standings_for(out)[:playoff_teams]
    bracket = resolved(current_week) if seeds else None
    return {"weeks": out, "bracket": bracket, "playoff_start": playoff_start, "weeks_by_round": weeks_by_round}


def app_rosters(client: Any, league_id: str, season: int, roster_of_team: Mapping[str, int]) -> list[dict[str, Any]]:
    """Each team's roster as the app holds it (live contracts), shaped like Sleeper's rosters: players, taxi, reserve."""
    from api import teams as teams_mod
    state = teams_mod.load_team_state(client, league_id, season)
    out = {rid: {"roster_id": rid, "players": [], "taxi": [], "reserve": []} for rid in roster_of_team.values()}
    ir = {str(r.get("sleeper_player_id")) for r in state.get("cap_adjustments", []) if r.get("adjustment_type") == "ir_adjustment"}
    for r in state.get("roster", []):
        rid, pid = roster_of_team.get(str(r.get("league_team_id"))), str(r.get("sleeper_player_id") or "")
        if rid is None or not pid or not r.get("contract_agreement_id"):
            continue
        out[rid]["players"].append(pid)
        if teams_mod.roster_designation(r) == "taxi":
            out[rid]["taxi"].append(pid)
        elif teams_mod.roster_designation(r) == "ir" or pid in ir:
            out[rid]["reserve"].append(pid)
    return list(out.values())


def sync(client: Any, league_id: str, sleeper_id: str, season: int) -> None:
    """Rebuild the app's weeks for one league and hand them to api.sleeper (shared by every owner for LIVE_TTL)."""
    from api import cache, league, sleeper  # here, not at import time: api.league reads api.sleeper too

    def load() -> dict[str, Any]:
        switch = (client.table("league_app_transactions").select("enabled,own_from_week")
                  .eq("league_id", league_id).limit(1).execute().data or [{}])[0]
        info = sleeper.raw(f"league/{sleeper_id}") or {}
        start = int((info.get("settings") or {}).get("playoff_week_start") or 15)
        own_from = int(switch.get("own_from_week") or start)
        current = league.current_week()
        if not switch.get("enabled") or current < own_from:
            sleeper.drop(f"league/{sleeper_id}/")
            return {"weeks": []}
        from league_ai.settings_store import load_settings
        settings = load_settings(client, league_id)
        teams = client.table("league_teams").select("id,sleeper_roster_id").eq("league_id", league_id).execute().data or []
        roster_of_team = {str(t["id"]): int(t["sleeper_roster_id"]) for t in teams if t.get("sleeper_roster_id") is not None}
        saved = (client.table("league_lineups").select("league_team_id,week,slots")
                 .eq("league_id", league_id).eq("season", season).execute().data or [])

        def standings_for(own: dict[int, list[dict[str, Any]]]) -> list[int]:
            for week, rows in own.items():
                sleeper.put(f"league/{sleeper_id}/matchups/{week}", rows)
            return [int(r["roster_id"]) for r in league.standings(sleeper_id, {}, None)["rows"]]

        result = build(
            season=season, own_from=own_from, current_week=current, league=info,
            rosters=app_rosters(client, league_id, season, roster_of_team),
            team_of_roster={rid: tid for tid, rid in roster_of_team.items()},
            saved=saved, scoring=settings.scoring_settings or {}, slots=lineups.starter_slots(settings.roster_positions),
            sleeper_rows=lambda w: sleeper.raw(f"league/{sleeper_id}/matchups/{w}", ttl=league.LIVE_TTL if w == current else 3600) or [],
            lines_for=lambda w: stat_lines(season, w, get_json=lambda url: sleeper.get_url(url, ttl=league.LIVE_TTL if w == current else 3600)),
            standings_for=standings_for,
        )
        for week, rows in result["weeks"].items():
            sleeper.put(f"league/{sleeper_id}/matchups/{week}", rows)
        if result["bracket"] is not None:
            sleeper.put(f"league/{sleeper_id}/winners_bracket", result["bracket"])
        return {"weeks": sorted(result["weeks"])}

    cache.cached(("own_league", league_id, sleeper_id), 15, load)
