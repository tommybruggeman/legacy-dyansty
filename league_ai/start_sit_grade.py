"""Grade start/sit on every saved lineup, not just the questions people ask. Free: no model call.

Each week, every team's lineup (league_history_events kind 'lineup') holds a set of real start/sit calls: a starter
and a bench player at the same position who were both expected to play. For each call we ask what each method would
have started, using only what was known before kickoff, and check it against who actually scored more:

- owner: what the owner started
- projection: Sleeper's projection in league scoring
- projection_matchup: the projection tilted by the same matchup context the app uses (defense vs position as of that
  week, Vegas implied points, game weather, rest), capped at +/-20%
- engine_live: what the app does now (the tilt weighted by position, nfl_data.TILT_WEIGHT)
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
METHODS = ("owner", "projection", "projection_matchup", "engine_live", "season_avg", "recent")
BLENDS = (0.1, 0.2)
TIER_HIGH, TIER_MID = 15.0, 8.0   # the better player's projection: a call between starters, mid-range players, or depth   # season-average weights tried against the engine
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
          tilt: Callable[[int, int, str], float] | None = None, live_tilt: Callable[[int, int, str], float] | None = None,
          variants: Mapping[str, Callable[[int, int, str, float], float | None]] | None = None) -> dict[str, Any]:
    """projections(season, week) -> player id -> projected league points; tilt(season, week, player id) -> full matchup multiplier;
    live_tilt -> the multiplier the app uses now (per-position weights), defaulting to tilt.
    variants: name -> f(season, week, player id, engine_live score) -> that method's score (None to skip the call), for
    trying a new signal against the engine on the same calls."""
    lineups = sorted(lineups, key=lambda r: (int(r.get("season") or 0), int(r.get("week") or 0)))
    by_week: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for r in lineups:
        by_week[(int(r["season"]), int(r["week"]))].append(r)
    history: dict[tuple[int, str], list[float]] = defaultdict(list)
    # bucket -> method -> [right, graded, points gained over the other choice]
    tally: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
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
                kl = (lambda pid: live_tilt(season, week, pid)) if live_tilt else k
                score = {
                    "owner": (1.0, 0.0),
                    "projection": (proj[sid], proj[bid]),
                    "projection_matchup": (proj[sid] * k(sid), proj[bid] * k(bid)),
                    "engine_live": (proj[sid] * kl(sid), proj[bid] * kl(bid)),
                    "season_avg": (sum(hs) / len(hs) if hs else None, sum(hb) / len(hb) if hb else None),
                    "recent": (sum(hs[-2:]) / len(hs[-2:]) if hs else None, sum(hb[-2:]) / len(hb[-2:]) if hb else None),
                }
                live = score["engine_live"]
                for w in BLENDS:   # a little of the season average mixed into the engine's number
                    score[f"blend_avg_{round(w * 100)}"] = ((1 - w) * live[0] + w * sum(hs) / len(hs), (1 - w) * live[1] + w * sum(hb) / len(hb)) if hs and hb else (None, None)
                for name, fn in (variants or {}).items():
                    score[name] = (fn(season, week, sid, live[0]), fn(season, week, bid, live[1]))
                close = abs(proj[sid] - proj[bid]) <= CLOSE_SHARE * max(proj[sid], proj[bid])
                pos = s["pos"]
                top = max(proj[sid], proj[bid])
                tier = "high" if top >= TIER_HIGH else "mid" if top >= TIER_MID else "low"
                buckets = ["all", "close" if close else "clear", "weeks_1_5" if week <= EARLY_WEEKS else "weeks_6_on", f"pos_{pos}",
                           # where each position struggles: close calls, early season, and how big the players are
                           f"{pos}_{'close' if close else 'clear'}", f"{pos}_{'weeks_1_5' if week <= EARLY_WEEKS else 'weeks_6_on'}", f"{pos}_tier_{tier}",
                           # for the app's report card (saved to the database, never printed: the logs are public)
                           f"season:{season}", f"season:{season}:{pos}", f"week:{season}:{week}", f"owner:{r.get('owner_name')}:{season}"]
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
                eng = score["engine_live"]
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
        out[bk] = {m: {"right": round(t[0] / t[1], 3), "calls": int(t[1]), "pts_per_call": round(t[2] / t[1], 2)} for m, t in sorted(methods.items()) if t[1]}
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


WR_COLUMNS = "gsis_id, sleeper_id, week, team, opponent, position, targets, receptions, receiving_yards, receiving_tds, receiving_air_yards"


def _receiving_rows(client: Any, season: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, 30000, 1000):
        chunk = (client.table("nfl_player_stats").select(WR_COLUMNS).eq("season", season).eq("season_type", "REG")
                 .order("gsis_id").order("week").range(start, start + 999).execute().data or [])
        rows += chunk
        if len(chunk) < 1000:
            break
    return rows


def wr_variants(client: Any, week_lines: Callable[[int, int], Mapping[str, Mapping[str, Any]]],
                games_for: Callable[[int, int], Mapping[str, Any]]) -> dict[str, Callable[[int, int, str, float], float | None]]:
    """The WR matchup model (league_ai.wr_matchup) as grader variants on top of the engine's number, at a few strengths,
    with and without the teammates-out bump. Other positions keep the engine's number."""
    from league_ai.nfl_data import team_code
    from league_ai.wr_matchup import WrModel

    season_rows: dict[int, list[dict[str, Any]]] = {}
    models: dict[tuple[int, int], WrModel] = {}

    def model(season: int, week: int) -> tuple[WrModel, list[dict[str, Any]], dict[str, str]]:
        if season not in season_rows:
            try:
                season_rows[season] = _receiving_rows(client, season)
            except Exception:
                season_rows[season] = []
        rows = season_rows[season]
        if (season, week) not in models:
            models[(season, week)] = WrModel([r for r in rows if int(r.get("week") or 0) < week])
        gsis = {str(r["sleeper_id"]): str(r["gsis_id"]) for r in rows if r.get("sleeper_id")}
        return models[(season, week)], [r for r in rows if int(r.get("week") or 0) == week], gsis

    def make(k: float, injuries: bool) -> Callable[[int, int, str, float], float | None]:
        def fn(season: int, week: int, pid: str, live: float) -> float | None:
            line = week_lines(season, week).get(pid) or {}
            if line.get("pos") != "WR":
                return live
            m, this_week, gsis = model(season, week)
            g = gsis.get(pid)
            game = games_for(season, week).get(team_code(line.get("team"))) or {}
            if not g or not game:
                return live
            out = m.out_teammates(g, this_week) if injuries else []
            mult, _ = m.multiplier(g, str(game.get("opponent") or ""), out)
            return live * (1 + k * (mult - 1))
        return fn

    return {"wr_model_50": make(0.5, True), "wr_model_100": make(1.0, True), "wr_model_100_no_injuries": make(1.0, False)}


def _signal_rows(client: Any, season: int) -> list[dict[str, Any]]:
    from league_ai.position_signals import COLUMNS

    rows: list[dict[str, Any]] = []
    for start in range(0, 30000, 1000):
        chunk = (client.table("nfl_player_stats").select(COLUMNS).eq("season", season).eq("season_type", "REG")
                 .order("gsis_id").order("week").range(start, start + 999).execute().data or [])
        rows += chunk
        if len(chunk) < 1000:
            break
    return rows


def signal_variants(client: Any, week_lines: Callable[[int, int], Mapping[str, Mapping[str, Any]]],
                    games_for: Callable[[int, int], Mapping[str, Any]]) -> dict[str, Callable[[int, int, str, float], float | None]]:
    """league_ai.position_signals as grader variants on top of the engine's number. Each touches only its positions
    (the rest keep the engine's number), so the per-position buckets show what it does there."""
    from league_ai.nfl_data import team_code
    from league_ai.position_signals import Signals

    season_rows: dict[int, list[dict[str, Any]]] = {}
    built: dict[tuple[int, int], Signals] = {}

    def sig(season: int, week: int) -> Signals:
        if season not in season_rows:
            try:
                season_rows[season] = [{**r, "opponent": team_code(r.get("opponent")) if r.get("opponent") else None} for r in _signal_rows(client, season)]
            except Exception:
                season_rows[season] = []
        if (season, week) not in built:
            built[(season, week)] = Signals([r for r in season_rows[season] if int(r.get("week") or 0) < week])
        return built[(season, week)]

    def make(positions: tuple[str, ...], f: Callable[[Signals, str, str, float], float]) -> Callable[[int, int, str, float], float | None]:
        def fn(season: int, week: int, pid: str, live: float) -> float | None:
            line = week_lines(season, week).get(pid) or {}
            if line.get("pos") not in positions:
                return live
            game = games_for(season, week).get(team_code(line.get("team"))) or {}
            return f(sig(season, week), pid, str(game.get("opponent") or ""), live)
        return fn

    def floor(c: float) -> Callable[[Signals, str, str, float], float]:
        return lambda s, pid, opp, live: live - c * (s.spread(pid) or 0.0)

    return {
        "qb_rush_25": make(("QB",), lambda s, pid, opp, live: live + 0.25 * s.qb_rush(pid)),
        "qb_rush_50": make(("QB",), lambda s, pid, opp, live: live + 0.5 * s.qb_rush(pid)),
        "qb_pressure": make(("QB",), lambda s, pid, opp, live: live * s.qb_pressure(opp)),
        "qb_pass_d": make(("QB",), lambda s, pid, opp, live: live * s.qb_pass_d(opp)),
        "qb_pressure_pass_d": make(("QB",), lambda s, pid, opp, live: live * s.qb_pressure(opp) * s.qb_pass_d(opp)),
        "role_50": make(("TE", "WR"), lambda s, pid, opp, live: live * (1 + 0.5 * (s.role_trend(pid) - 1))),
        "role_100": make(("TE", "WR"), lambda s, pid, opp, live: live * s.role_trend(pid)),
        "snaps_50": make(("TE", "RB"), lambda s, pid, opp, live: live * (1 + 0.5 * (s.role_trend(pid, "snaps") - 1))),
        "floor_10": make(SKILL, floor(0.1)),
        "floor_20": make(SKILL, floor(0.2)),
        "form_25": make(SKILL, lambda s, pid, opp, live: live * (1 + 0.25 * (s.form(pid) - 1))),
        "form_50": make(SKILL, lambda s, pid, opp, live: live * (1 + 0.5 * (s.form(pid) - 1))),
    }


def run_league(client: Any, league_id: str, sleeper_league_id: str, variants: bool = True) -> dict[str, Any]:
    """variants=False skips the experimental signals (the weekly scorecard only needs the engine and the owners)."""
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

    def tilt(season: int, week: int, pid: str, weights: Any = None) -> float:
        line = week_lines(season, week).get(pid) or {}
        pos = line.get("pos") or ""
        if pos not in SKILL:
            return 1.0
        if (season, week) not in dvp_cache:
            stats = [r for r in season_stat_rows(client, season) if int(r.get("week") or 0) < week]   # only games already played
            dvp_cache[(season, week)] = (defense_vs_position_from_stats(stats), week_matchups(season_games(client, season), week))
        dvp, games = dvp_cache[(season, week)]
        game = games.get(team_code(line.get("team")))
        return total_multiplier(pos, game, dvp.get((game["opponent"], pos)) if game else None, weights)

    games_for = lambda season, week: dvp_cache.get((season, week), (None, {}))[1] or week_matchups(season_games(client, season), week)
    extra = {**wr_variants(client, week_lines, games_for), **signal_variants(client, week_lines, games_for)} if variants else {}
    out = grade(rows, projections, lambda s, w, pid: tilt(s, w, pid, {}), lambda s, w, pid: tilt(s, w, pid), extra)
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
        buckets = ["all", "close", "clear", "weeks_1_5", "weeks_6_on"]
        for pos in SKILL:
            buckets += [f"pos_{pos}", f"{pos}_close", f"{pos}_clear", f"{pos}_weeks_1_5", f"{pos}_weeks_6_on", f"{pos}_tier_high", f"{pos}_tier_mid", f"{pos}_tier_low"]
        for bucket in buckets:
            if bucket in res:
                print(f"  {bucket}: " + "; ".join(f"{m} {v['right']:.1%} of {v['calls']} ({v['pts_per_call']:+.2f} pts)" for m, v in res[bucket].items()))
        print(f"  engine vs owner when they disagree: {res['engine_vs_owner_when_they_disagree']}")


if __name__ == "__main__":
    main()
