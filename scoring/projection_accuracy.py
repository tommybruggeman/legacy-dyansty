"""How close are the weekly projections to what players really score, and would a learned correction help?

For every finished week it compares, in this league's scoring:
- sleeper: Sleeper's projected stat line scored with the league's rules
- app: what the app shows (sleeper x the matchup tilt, weighted by position as in nfl_data.TILT_WEIGHT)
- learned corrections, each using only weeks already played (walk-forward, so no peeking):
  pos_bias (app x the position's shrunk actual/projected ratio), tier_bias (same by position and projection tier),
  player_bias (app x the player's own shrunk ratio), blend_avg_20 / _30 (part of the player's season average mixed in),
  combo (tier_bias then player_bias)
against the real score: our own official lines (final_stat_lines, scored by our engine) where saved, Sleeper's
stat lines otherwise.

Groups: every player Sleeper projected for 3+ points, and the players actually started in league lineups. Team
totals: each lineup's starters added up, projected vs real.

Prints numbers only (the repo and its Actions logs are public): no player, team or owner names.

    python -m scoring.projection_accuracy
"""
from __future__ import annotations

import os
from collections import defaultdict
from math import sqrt
from typing import Any

MIN_PROJ = 3.0
SKILL = ("QB", "RB", "WR", "TE")
POS_PRIOR = 300.0     # projected points of "the projection was right" mixed into the position ratio
TIER_PRIOR = 150.0
PLAYER_PRIOR = 40.0   # about 3-4 games of a typical starter
METHODS = ("sleeper", "app", "pos_bias", "pos_100", "pos_600", "pos_carry", "pos_last4", "tier_bias", "player_bias", "blend_avg_20", "combo")


def tier(p: float) -> str:
    return "15+" if p >= 15 else "8-15" if p >= 8 else "3-8"


def ratio(actual: float, proj: float, prior: float) -> float:
    return (actual + prior) / (proj + prior)


class Tally:
    def __init__(self) -> None:
        self.d: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    def add(self, bucket: str, method: str, proj: float, actual: float) -> None:
        self.d[bucket][method].append(actual - proj)

    def line(self, bucket: str) -> str:
        parts = []
        for m, errs in self.d[bucket].items():
            n = len(errs)
            if not n:
                continue
            mae = sum(abs(e) for e in errs) / n
            rmse = sqrt(sum(e * e for e in errs) / n)
            bias = sum(errs) / n
            parts.append(f"{m} MAE {mae:.2f} RMSE {rmse:.2f} bias {bias:+.2f}")
        n = len(next(iter(self.d[bucket].values()), []))
        return f"  {bucket} (n={n}): " + "; ".join(parts)


def run_league(client: Any, league_id: str, sleeper_league_id: str) -> None:
    from api.live_stats import final_lines
    from league_ai.nfl_data import defense_vs_position_from_stats, season_games, season_stat_rows, team_code, total_multiplier, week_matchups
    from league_ai.start_sit_grade import _projection_lines, _sleeper
    from api.projections import POSITIONS, _rows
    from pipeline.league_history.run import league_chain
    from scoring.engine import player_points

    scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(sleeper_league_id) if l.get("season")}
    rows, start = [], 0
    while True:
        page = (client.table("league_history_events").select("season, week, details").eq("league_id", league_id)
                .eq("kind", "lineup").order("event_id").range(start, start + 999).execute().data or [])
        rows += page
        if len(page) < 1000:
            break
        start += 1000
    lineups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        lineups[(int(r["season"]), int(r["week"]))].append(r.get("details") or {})
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)

    carry: dict[str, float] = {}   # last season's end-of-season ratio by position
    for season in sorted({s for s, _ in lineups}):
        sc = scoring.get(season) or next(iter(scoring.values()), {})
        weeks = sorted(w for s, w in lineups if s == season)
        stats_rows = season_stat_rows(client, season)
        games_all = season_games(client, season)
        t = Tally()
        teams: dict[str, list[float]] = defaultdict(list)   # method -> team-week errors
        pos_hist: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        pos_weeks: dict[str, list[tuple[float, float]]] = defaultdict(list)   # per finished week (actual, projected)
        kdiag: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        tier_hist: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        player_hist: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        games_played: dict[str, list[float]] = defaultdict(list)
        sources = defaultdict(int)
        for week in weeks:
            proj_lines = _projection_lines(season, week)
            finals = final_lines(client, season, week)
            if finals:
                actual_lines = finals
                sources["ours"] += 1
            else:
                try:
                    actual_lines = _rows(_sleeper(f"stats/nfl/{season}/{week}?{query}"))
                except Exception:
                    continue
                sources["sleeper"] += 1
            if not proj_lines or not actual_lines:
                continue
            dvp = defense_vs_position_from_stats([r for r in stats_rows if int(r.get("week") or 0) < week])
            games = week_matchups(games_all, week)

            def actual(pid: str, pos: str) -> float:
                line = actual_lines.get(pid)
                return player_points(line.get("stats"), sc, line.get("pos") or pos) if line else 0.0

            est: dict[str, dict[str, float]] = {}
            pos_of: dict[str, str] = {}
            for pid, line in proj_lines.items():
                pos = line.get("pos") or ""
                base = player_points(line.get("stats"), sc, pos)
                if base <= 0:
                    continue
                game = games.get(team_code(line.get("team")))
                factor = total_multiplier(pos, game, dvp.get((game["opponent"], pos)) if game else None) if pos in SKILL and game else 1.0
                app = base * factor
                ph, th, plh = pos_hist[pos], tier_hist[(pos, tier(app))], player_hist[pid]
                g = games_played[pid]
                avg = sum(g) / len(g) if len(g) >= 2 else None
                tb = app * ratio(th[0], th[1], TIER_PRIOR)
                est[pid] = {
                    "sleeper": base, "app": app,
                    "pos_bias": app * ratio(ph[0], ph[1], POS_PRIOR),
                    "pos_100": app * ratio(ph[0], ph[1], 100.0),
                    "pos_600": app * ratio(ph[0], ph[1], 600.0),
                    "pos_carry": app * (ph[0] + POS_PRIOR * carry.get(pos, 1.0)) / (ph[1] + POS_PRIOR),
                    "pos_last4": app * ratio(sum(a for a, _ in pos_weeks[pos][-4:]), sum(b for _, b in pos_weeks[pos][-4:]), POS_PRIOR),
                    "tier_bias": tb,
                    "player_bias": app * ratio(plh[0], plh[1], PLAYER_PRIOR),
                    "blend_avg_20": 0.8 * app + 0.2 * avg if avg is not None else app,
                    "combo": tb * ratio(plh[0], plh[1], PLAYER_PRIOR),
                }
                pos_of[pid] = pos
            started = {str(s["id"]) for d in lineups[(season, week)] for s in d.get("starters") or [] if s.get("id")}
            pts: dict[str, float] = {}
            for pid, e in est.items():
                pos = pos_of[pid]
                a = pts[pid] = actual(pid, pos)
                groups = []
                if e["sleeper"] >= MIN_PROJ:
                    groups += ["all", f"all_{pos}", f"all_{pos}_{tier(e['app'])}"]
                if pid in started:
                    groups += ["starters", f"starters_{pos}", "starters_weeks_1_4" if week <= 4 else "starters_weeks_5_on"]
                for g in groups:
                    for m in METHODS:
                        t.add(g, m, e[m], a)
            # team totals: each league lineup's starters
            for d in lineups[(season, week)]:
                ids = [str(s["id"]) for s in d.get("starters") or [] if s.get("id")]
                if not ids:
                    continue
                real = sum(actual(pid, (est.get(pid) and pos_of[pid]) or "") for pid in ids)
                for m in METHODS:
                    teams[m].append(real - sum(est.get(pid, {}).get(m, 0.0) for pid in ids))
            # kickers: which scoring keys the projected and the real stat lines carry (keys only, no names)
            for pid, line in proj_lines.items():
                if line.get("pos") == "K":
                    for k in sc:
                        if k.startswith(("fg", "xp")):
                            kdiag[k][0] += int((line.get("stats") or {}).get(k) not in (None, 0))
                            kdiag[k][1] += int(((actual_lines.get(pid) or {}).get("stats") or {}).get(k) not in (None, 0))
            # learn from this week (only after it has been graded)
            wk: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
            for pid, e in est.items():
                if e["sleeper"] < MIN_PROJ:
                    continue
                pos, a = pos_of[pid], pts[pid]
                for h in (pos_hist[pos], tier_hist[(pos, tier(e["app"]))], player_hist[pid], wk[pos]):
                    h[0] += a
                    h[1] += e["app"]
            for pos, (a_, p_) in wk.items():
                pos_weeks[pos].append((a_, p_))
            for pid in est:
                line = actual_lines.get(pid)
                s = (line or {}).get("stats") or {}
                if line and (finals or s.get("gp") or s.get("off_snp")):
                    games_played[pid].append(pts[pid])
        print(f"season {season}: weeks {weeks[0]}-{weeks[-1]}, actual scores from {dict(sources)}")
        for b in ["all", "starters", "starters_weeks_1_4", "starters_weeks_5_on"] + [f"starters_{p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF")] \
                + [f"all_{p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF")] + [f"all_{p}_{x}" for p in SKILL for x in ("15+", "8-15", "3-8")]:
            if b in t.d:
                print(t.line(b))
        if teams:
            n = len(teams["app"])
            print(f"  team totals (n={n} team-weeks): " + "; ".join(
                f"{m} MAE {sum(abs(e) for e in v) / n:.2f} bias {sum(v) / n:+.2f}" for m, v in teams.items()))
        # what the learned position ratios ended at (positive = players outscore the projection)
        print("  end-of-season position ratios (actual / app projection): " + ", ".join(
            f"{p} {h[0] / h[1]:.3f}" for p, h in sorted(pos_hist.items()) if h[1]))
        carry = {p: h[0] / h[1] for p, h in pos_hist.items() if h[1]}
        print("  kicker scoring keys (weight; K lines with it projected / real): " + ", ".join(
            f"{k} {sc.get(k)} {v[0]}/{v[1]}" for k, v in sorted(kdiag.items())))


def odds_history_check(client: Any, league_id: str) -> None:
    """The saved pre-game team projections (league_ai_odds_history.mu) vs the real team scores."""
    try:
        hist = client.table("league_ai_odds_history").select("season, week, owner_name, mu").eq("league_id", league_id).limit(5000).execute().data or []
        res = client.table("league_matchups").select("season, week, owner_name, points").eq("league_id", league_id).limit(20000).execute().data or []
    except Exception as exc:
        print(f"  saved team projections: unavailable ({type(exc).__name__})")
        return
    real = {(int(r["season"]), int(r["week"]), str(r["owner_name"]).casefold()): float(r["points"]) for r in res if r.get("points") is not None}
    errs = [real[k] - float(h["mu"]) for h in hist if h.get("mu") is not None
            for k in [(int(h["season"]), int(h["week"]), str(h["owner_name"]).casefold())] if k in real and real[k] > 0]
    if errs:
        print(f"  saved team projections (n={len(errs)}): MAE {sum(abs(e) for e in errs) / len(errs):.2f} bias {sum(errs) / len(errs):+.2f}")
    else:
        print("  saved team projections: none graded yet")


def main() -> None:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for n, lg in enumerate(client.table("leagues").select("id, sleeper_league_id").execute().data or [], start=1):
        if not lg.get("sleeper_league_id"):
            continue
        print(f"league {n}")
        run_league(client, lg["id"], str(lg["sleeper_league_id"]))
        odds_history_check(client, lg["id"])


if __name__ == "__main__":
    main()
