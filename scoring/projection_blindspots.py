"""Where do the (calibrated) weekly projections still miss? A free breakdown of every graded projection.

Groups each projection by tier, injury report status, home/away, favored or not, Vegas total, snap trend, games
played, NFL team, week, and checks started players with no projection at all or projected 0. Bias = real - shown
(negative = we project too high). Prints numbers only (repo and Actions logs are public): no player or owner names.

    python -m scoring.projection_blindspots
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any


def _tally() -> dict[str, list[float]]:
    return defaultdict(list)


def _line(name: str, errs: list[float]) -> str:
    n = len(errs)
    if not n:
        return f"  {name}: n=0"
    return f"  {name}: n={n} MAE {sum(abs(e) for e in errs) / n:.2f} bias {sum(errs) / n:+.2f} total {sum(errs):+.0f}"


def run_league(client: Any, league_id: str, sleeper_league_id: str) -> None:
    from league_ai import projection_calibration as pc
    from league_ai import projection_learning as pl
    from league_ai.nfl_data import season_games, season_stat_rows
    from pipeline.league_history.run import league_chain

    scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(sleeper_league_id) if l.get("season")}
    lineups = pc._lineups(client, league_id)
    weeks = []
    for season in sorted({s for s, _ in lineups}):
        sc = scoring.get(season) or next(iter(scoring.values()), {})
        stats_rows, games_all = season_stat_rows(client, season), season_games(client, season)
        done = sorted({int(g["week"]) for g in games_all if g.get("week") is not None} - {int(g["week"]) for g in games_all if g.get("home_score") is None})
        for week in sorted(w for s, w in lineups if s == season):
            if games_all and week not in done:
                continue
            rows = pc.week_rows(client, season, week, sc, stats_rows, games_all)
            if rows:
                weeks.append({"season": season, "week": week, "rows": rows, "lineups": lineups[(season, week)], "snapshot": None})
    for season in sorted({s for s, _ in lineups})[-1:]:
        games_all = season_games(client, season)
        unscored = sorted({int(g["week"]) for g in games_all if g.get("home_score") is None and g.get("week") is not None})
        print(f"season {season}: lineup weeks {sorted(w for s, w in lineups if s == season)}; weeks with unscored games {unscored[:6]}"
              f"; graded weeks {[w['week'] for w in weeks if w['season'] == season]}")
    pc.replay(weeks)
    data: dict[int, Any] = {}
    for season in sorted({w["season"] for w in weeks})[-2:]:
        data[season] = pl.season_data(client, season)
        t = _tally()
        teams_err: list[float] = []
        no_proj = [0, 0.0]          # started with no projection row: count, real points
        zero_proj = [0, 0.0]        # started with a projection under 1 point
        out_started = [0, 0.0, 0.0]  # started while listed out/doubtful: count, projected, real
        by_team: dict[str, list[float]] = defaultdict(list)
        for wk in [w for w in weeks if w["season"] == season]:
            ctx, post = pl.context(data[season], season, wk["week"])
            from league_ai.nfl_data import week_matchups
            games = week_matchups(data[season]["games"], wk["week"])
            by_id = {r["id"]: r for r in wk["rows"] if r.get("actual") is not None}
            started = {i for ids in wk["lineups"] for i in ids}
            for ids in wk["lineups"]:
                real, proj = 0.0, 0.0
                for i in ids:
                    r = by_id.get(i)
                    if r is None:
                        no_proj[0] += 1
                        continue
                    real += r["actual"]
                    proj += r["cal"]
                teams_err.append(real - proj)
            for r in by_id.values():
                if r["id"] not in started:
                    continue
                e = r["actual"] - r["cal"]
                st = ctx["status"].get(r["id"]) or "none"
                if r["cal"] < 1:
                    zero_proj[0] += 1
                    zero_proj[1] += r["actual"]
                if st in ("out", "doubtful"):
                    out_started[0] += 1
                    out_started[1] += r["cal"]
                    out_started[2] += r["actual"]
                c = r["cal"]
                t["all starters"].append(e)
                t[f"pos {r['pos']}"].append(e)
                t["tier " + ("20+" if c >= 20 else "15-20" if c >= 15 else "10-15" if c >= 10 else "5-10" if c >= 5 else "0-5")].append(e)
                t[f"report {st}"].append(e)
                g = games.get(r["team"]) or {}
                t["home" if g.get("home") else "away"].append(e)
                sp = ctx["spread"].get(r["team"])
                if sp is not None:
                    t["favored 7+" if sp >= 7 else "underdog 7+" if sp <= -7 else "close game"].append(e)
                imp = ctx["implied"].get(r["team"])
                if imp is not None:
                    t["team total 26+" if imp >= 26 else "team total <20" if imp < 20 else "team total 20-26"].append(e)
                snaps = ctx["snaps"].get(r["id"]) or []
                t["games before " + ("0" if not snaps else "1-2" if len(snaps) < 3 else "3+")].append(e)
                if len(snaps) >= 3:
                    tr = (snaps[-1] + snaps[-2]) / 2 / (sum(snaps) / len(snaps)) if sum(snaps) else 1
                    t["snaps " + ("rising" if tr > 1.1 else "falling" if tr < 0.9 else "steady")].append(e)
                t["weeks 1-4" if wk["week"] <= 4 else "weeks 5-9" if wk["week"] <= 9 else "weeks 10+"].append(e)
                if r["actual"] <= 0.5 and not post["pct"].get(r["id"]):
                    t["did not play"].append(e)
                else:
                    t["played"].append(e)
                by_team[r["team"]].append(e)
        print(f"season {season}: {len(teams_err)} team-weeks")
        if teams_err:
            n = len(teams_err)
            print(f"  team totals: MAE {sum(abs(e) for e in teams_err) / n:.2f} bias {sum(teams_err) / n:+.2f}")
        print(f"  started with NO projection: {no_proj[0]} (these count as 0 projected)")
        print(f"  started projected under 1 pt: {zero_proj[0]} players, who then scored {zero_proj[1]:.0f} pts total")
        print(f"  started while listed out/doubtful: {out_started[0]} players, projected {out_started[1]:.0f}, scored {out_started[2]:.0f}")
        for name in sorted(t):
            print(_line(name, t[name]))
        worst = sorted(((sum(v), k, len(v)) for k, v in by_team.items() if len(v) >= 8))
        print("  NFL teams we project too high (total pts): " + ", ".join(f"{k} {s:+.0f} (n={n})" for s, k, n in worst[:5]))
        print("  NFL teams we project too low (total pts): " + ", ".join(f"{k} {s:+.0f} (n={n})" for s, k, n in worst[-5:][::-1]))


def main() -> None:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for n, lg in enumerate(client.table("leagues").select("id, sleeper_league_id").execute().data or [], start=1):
        if lg.get("sleeper_league_id"):
            print(f"league {n}")
            run_league(client, lg["id"], str(lg["sleeper_league_id"]))


if __name__ == "__main__":
    main()
