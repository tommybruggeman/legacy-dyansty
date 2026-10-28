"""Grade start/sit on every saved lineup, not just the questions people ask. Free: no model call.

Each week, every team's lineup (league_history_events kind 'lineup') holds a set of real start/sit calls: a starter
and a bench player at the same position who were both expected to play. For each call we ask what each method would
have started, using only what was known before kickoff, and check it against who actually scored more:

- owner: what the owner started
- projection: Sleeper's projection in league scoring
- projection_matchup (and _half_: half the tilt): the projection tilted by the same matchup context the app uses (defense vs position as of that
  week, Vegas implied points, game weather, rest), capped at +/-20%
- season_avg / recent: points per game so far this season / over the last two games

Prints rates only (the repo and its Actions logs are public).
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping

MIN_PROJECTED = 3.0   # a bench player projected under this wasn't a real option (out, inactive, deep backup)
CLOSE_SHARE = 0.25    # a "close" call: the two projections within 25% of each other
EARLY_WEEKS = 5
METHODS = ("owner", "projection", "projection_matchup", "projection_half_matchup", "season_avg", "recent")
SKILL = ("QB", "RB", "WR", "TE")


def calls(lineup: Mapping[str, Any], proj: Mapping[str, float]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """(starter, bench) pairs at the same position where both were projected to play."""
    d = lineup.get("details") or {}
    out = []
    for s in d.get("starters") or []:
        for b in d.get("bench") or []:
            if not s.get("pos") or s.get("pos") != b.get("pos"):
                continue
            if proj.get(str(s["id"]), 0.0) >= MIN_PROJECTED and proj.get(str(b["id"]), 0.0) >= MIN_PROJECTED:
                out.append((s, b))
    return out


def grade(lineups: Iterable[Mapping[str, Any]], projections: Callable[[int, int], Mapping[str, float]],
          tilt: Callable[[int, int, str], float] | None = None) -> dict[str, Any]:
    """projections(season, week) -> player id -> projected league points; tilt(season, week, player id) -> matchup multiplier."""
    lineups = sorted(lineups, key=lambda r: (int(r.get("season") or 0), int(r.get("week") or 0)))
    by_week: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for r in lineups:
        by_week[(int(r["season"]), int(r["week"]))].append(r)
    history: dict[tuple[int, str], list[float]] = defaultdict(list)
    # bucket -> method -> [right, graded, points gained over the other choice]
    tally: dict[str, dict[str, list[float]]] = defaultdict(lambda: {m: [0.0, 0.0, 0.0] for m in METHODS})
    disagree = {"calls": 0, "engine_right": 0, "points": 0.0}
    for (season, week), rows in sorted(by_week.items()):
        proj = projections(season, week) or {}
        for r in rows:
            for s, b in calls(r, proj):
                sid, bid = str(s["id"]), str(b["id"])
                ps, pb = float(s.get("pts") or 0), float(b.get("pts") or 0)
                if ps == pb:
                    continue
                hs, hb = history[(season, sid)], history[(season, bid)]
                k = (lambda pid: tilt(season, week, pid)) if tilt else (lambda pid: 1.0)
                score = {
                    "owner": (1.0, 0.0),
                    "projection": (proj[sid], proj[bid]),
                    "projection_matchup": (proj[sid] * k(sid), proj[bid] * k(bid)),
                    "projection_half_matchup": (proj[sid] * k(sid) ** 0.5, proj[bid] * k(bid) ** 0.5),
                    "season_avg": (sum(hs) / len(hs) if hs else None, sum(hb) / len(hb) if hb else None),
                    "recent": (sum(hs[-2:]) / len(hs[-2:]) if hs else None, sum(hb[-2:]) / len(hb[-2:]) if hb else None),
                }
                close = abs(proj[sid] - proj[bid]) <= CLOSE_SHARE * max(proj[sid], proj[bid])
                buckets = ["all", "close" if close else "clear", "weeks_1_5" if week <= EARLY_WEEKS else "weeks_6_on", f"pos_{s['pos']}"]
                for m, (a, c) in score.items():
                    if a is None or c is None or a == c:
                        continue
                    picked_starter = a > c
                    right = (ps > pb) == picked_starter
                    gained = (ps - pb) if picked_starter else (pb - ps)
                    for bk in buckets:
                        t = tally[bk][m]
                        t[0] += right
                        t[1] += 1
                        t[2] += gained
                eng = score["projection_matchup"]
                if eng[0] != eng[1] and eng[0] < eng[1]:   # the engine would have benched the owner's starter
                    disagree["calls"] += 1
                    disagree["engine_right"] += pb > ps
                    disagree["points"] += pb - ps
        for r in rows:   # this week's points count only after this week's calls are graded
            d = r.get("details") or {}
            for p in [*(d.get("starters") or []), *(d.get("bench") or [])]:
                history[(season, str(p["id"]))].append(float(p.get("pts") or 0))
    out: dict[str, Any] = {}
    for bk, methods in sorted(tally.items()):
        out[bk] = {m: {"right": round(t[0] / t[1], 3), "calls": int(t[1]), "pts_per_call": round(t[2] / t[1], 2)} for m, t in methods.items() if t[1]}
    out["engine_vs_owner_when_they_disagree"] = {
        "calls": disagree["calls"],
        "engine_right": round(disagree["engine_right"] / disagree["calls"], 3) if disagree["calls"] else None,
        "points_per_call": round(disagree["points"] / disagree["calls"], 2) if disagree["calls"] else None}
    return out


# ---- live data (GitHub Actions: Supabase + Sleeper) -------------------------------------------------------------------

def _sleeper(path: str) -> Any:
    import requests

    resp = requests.get(f"https://api.sleeper.app/{path}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _projection_lines(season: int, week: int) -> dict[str, dict[str, Any]]:
    from api.projections import POSITIONS, _rows

    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)
    try:
        return _rows(_sleeper(f"projections/nfl/{season}/{week}?{query}"))
    except Exception:
        return {}


def run_league(client: Any, league_id: str, sleeper_league_id: str) -> dict[str, Any]:
    from api.projections import score
    from league_ai.nfl_data import defense_vs_position_from_stats, season_games, season_stat_rows, team_code, total_multiplier, week_matchups
    from pipeline.league_history.run import league_chain

    scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(sleeper_league_id) if l.get("season")}
    rows, start = [], 0
    while True:
        page = (client.table("league_history_events").select("season, week, owner_name, details").eq("league_id", league_id)
                .eq("kind", "lineup").order("event_id").range(start, start + 999).execute().data or [])
        rows += page
        if len(page) < 1000:
            break
        start += 1000
    lines: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}

    def week_lines(season: int, week: int) -> dict[str, dict[str, Any]]:
        if (season, week) not in lines:
            lines[(season, week)] = _projection_lines(season, week)
        return lines[(season, week)]

    def projections(season: int, week: int) -> dict[str, float]:
        sc = scoring.get(season) or next(iter(scoring.values()), {})
        return {pid: score(l["stats"], sc) for pid, l in week_lines(season, week).items()}

    dvp_cache: dict[tuple[int, int], Any] = {}

    def tilt(season: int, week: int, pid: str) -> float:
        line = week_lines(season, week).get(pid) or {}
        pos = line.get("pos") or ""
        if pos not in SKILL:
            return 1.0
        if (season, week) not in dvp_cache:
            stats = [r for r in season_stat_rows(client, season) if int(r.get("week") or 0) < week]   # only games already played
            dvp_cache[(season, week)] = (defense_vs_position_from_stats(stats), week_matchups(season_games(client, season), week))
        dvp, games = dvp_cache[(season, week)]
        game = games.get(team_code(line.get("team")))
        return total_multiplier(pos, game, dvp.get((game["opponent"], pos)) if game else None)

    out = grade(rows, projections, tilt)
    out["lineups"] = len(rows)
    out["seasons"] = sorted({int(r["season"]) for r in rows})
    return out


def main() -> None:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for n, lg in enumerate(client.table("leagues").select("id, sleeper_league_id").execute().data or [], start=1):
        if not lg.get("sleeper_league_id"):
            continue
        res = run_league(client, lg["id"], str(lg["sleeper_league_id"]))
        print(f"league {n}: {res['lineups']} lineups, seasons {res['seasons']}")
        for bucket in ("all", "close", "clear", "weeks_1_5", "weeks_6_on", "pos_QB", "pos_RB", "pos_WR", "pos_TE"):
            if bucket in res:
                print(f"  {bucket}: " + "; ".join(f"{m} {v['right']:.1%} of {v['calls']} ({v['pts_per_call']:+.2f} pts)" for m, v in res[bucket].items()))
        print(f"  engine vs owner when they disagree: {res['engine_vs_owner_when_they_disagree']}")


if __name__ == "__main__":
    main()
