"""Research only: where did ESPN's weekly projections disagree with Sleeper's and turn out right?

ESPN is never used as a feed. For 2025-2026 player-weeks with both projections, d = ESPN - Sleeper (league scoring,
ESPN rescaled per position to Sleeper's level since it lacks first downs) and r = real - our calibrated number.
slope = how much of ESPN's disagreement came true (0 = no information, 1 = ESPN fully right), per group, and which
parts of ESPN's stat line (volume: carries/targets/attempts, touchdowns, yards per touch) carried it. Numbers only.

    python -m scoring.espn_dissect
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

KEYS = {"volume": ("rush_att", "rec_tgt", "pass_att"), "td": ("rush_td", "rec_td", "pass_td"), "yards": ("rush_yd", "rec_yd", "pass_yd"),
        "catches": ("rec",)}


def _slope(pairs: list[tuple[float, float]]) -> tuple[float, float, int]:
    """least squares r ~ k d through the origin, and the MAE gain if we had added k d."""
    n = len(pairs)
    sdd = sum(d * d for d, _ in pairs)
    if n < 30 or sdd <= 0:
        return 0.0, 0.0, n
    k = sum(d * r for d, r in pairs) / sdd
    gain = sum(abs(r) - abs(r - k * d) for d, r in pairs) / n
    return k, gain, n


def main() -> None:
    from supabase import create_client

    from league_ai import projection_calibration as pc
    from league_ai import projection_learning as pl
    from league_ai.nfl_data import season_games, season_stat_rows
    from league_ai.start_sit_grade import _projection_lines
    from pipeline.league_history.run import league_chain
    from pipeline.nfl_data.run import load_crosswalk
    from scoring.engine import player_points
    from scoring.projection_experiments import espn_week

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    leagues = client.table("leagues").select("id, sleeper_league_id").execute().data or []
    lg = [l for l in leagues if str(l.get("sleeper_league_id")) == "1361083066197491712"][0]
    scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(str(lg["sleeper_league_id"])) if l.get("season")}
    lineups = pc._lineups(client, lg["id"])
    weeks = []
    for season in sorted({s for s, _ in lineups}):
        sc = scoring.get(season) or {}
        stats_rows, games_all = season_stat_rows(client, season), season_games(client, season)
        done = sorted({int(g["week"]) for g in games_all if g.get("week") is not None} - {int(g["week"]) for g in games_all if g.get("home_score") is None})
        for week in sorted(w for s, w in lineups if s == season):
            if week in done:
                rows = pc.week_rows(client, season, week, sc, stats_rows, games_all)
                if rows:
                    weeks.append({"season": season, "week": week, "rows": rows, "lineups": lineups[(season, week)], "snapshot": None})
    pc.replay(weeks)
    cw = load_crosswalk()
    data: dict[int, Any] = {}
    recs = []
    for w in weeks:
        if w["season"] < weeks[-1]["season"] - 1:
            continue
        sc = scoring.get(w["season"]) or {}
        e = {cw.sleeper_for_espn(k): v for k, v in espn_week(w["season"], w["week"]).items()}
        s_lines = _projection_lines(w["season"], w["week"])
        if w["season"] not in data:
            data[w["season"]] = pl.season_data(client, w["season"])
        ctx, _ = pl.context(data[w["season"]], w["season"], w["week"])
        started = {i for ids in w["lineups"] for i in ids}
        for r in w["rows"]:
            if r.get("actual") is None or r["pos"] not in ("QB", "RB", "WR", "TE") or r["raw"] < 3 or r["id"] not in e:
                continue
            es, ss = e[r["id"]], (s_lines.get(r["id"]) or {}).get("stats") or {}
            snaps = ctx["snaps"].get(r["id"]) or []
            recs.append({"season": w["season"], "pos": r["pos"], "sl": r["raw"], "es": player_points(es, sc, r["pos"]), "cal": r["cal"],
                         "r": r["actual"] - r["cal"], "started": r["id"] in started, "q": ctx["status"].get(r["id"]) == "questionable",
                         "games": len(snaps), "implied": ctx["implied"].get(r["team"]), "week": w["week"],
                         **{f"d_{k}": sum(float(es.get(x) or 0) - float(ss.get(x) or 0) for x in keys) for k, keys in KEYS.items()}})
    # rescale ESPN to Sleeper's level per season and position
    lvl: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for x in recs:
        lvl[(x["season"], x["pos"])][0] += x["sl"]
        lvl[(x["season"], x["pos"])][1] += x["es"]
    for x in recs:
        a, b = lvl[(x["season"], x["pos"])]
        x["d"] = x["es"] * (a / b if b else 1) - x["sl"]
    print(f"player-weeks with both projections: {len(recs)}")

    def show(name: str, rows: list[dict[str, Any]], key: str = "d") -> None:
        k, gain, n = _slope([(x[key], x["r"]) for x in rows])
        mad = sum(abs(x[key]) for x in rows) / max(1, len(rows))
        print(f"  {name:<28} n={n:<5} avg |disagreement| {mad:5.2f}  slope {k:+.2f}  MAE gain if used {gain:+.3f}")

    for season in sorted({x["season"] for x in recs}):
        print(f"season {season}")
        rs = [x for x in recs if x["season"] == season]
        show("all", rs)
        show("started only", [x for x in rs if x["started"]])
        for p in ("QB", "RB", "WR", "TE"):
            show(f"pos {p}", [x for x in rs if x["pos"] == p])
        for lo, hi in ((3, 8), (8, 14), (14, 99)):
            show(f"projected {lo}-{hi}", [x for x in rs if lo <= x["cal"] < hi])
        show("ESPN higher", [x for x in rs if x["d"] > 1])
        show("ESPN lower", [x for x in rs if x["d"] < -1])
        show("listed questionable", [x for x in rs if x["q"]])
        show("0-2 games of history", [x for x in rs if x["games"] < 3])
        show("3+ games of history", [x for x in rs if x["games"] >= 3])
        show("weeks 1-4", [x for x in rs if x["week"] <= 4])
        show("weeks 5+", [x for x in rs if x["week"] > 4])
        show("team total 26+", [x for x in rs if (x["implied"] or 0) >= 26])
        show("team total <20", [x for x in rs if x["implied"] is not None and x["implied"] < 20])
        for comp in KEYS:
            show(f"stat diff: {comp}", rs, f"d_{comp}")


if __name__ == "__main__":
    main()
