"""Which pregame team projection gives the best weekly win odds?

For every past league game (2024 on), each side's starters are projected before kickoff several ways and turned into
win odds (normal approximation, each player's spread a x sqrt(projection) + b), then graded against who really won:

- form: each starter's recent form (0.6 x last three games + 0.4 x season average), like the Front Office rating today
- proj_raw: the app's projection before calibration (Sleeper's line in league scoring x matchup tilt)
- proj_cal: the calibrated projection (league_ai.projection_calibration, walk-forward)
- blend_20 / blend_40: calibrated projection with that share of form mixed in

The spread scale is tuned walk-forward on earlier weeks only. Also grades the team-total miss for each method and the
odds saved in league_ai_odds_history so far. Prints numbers only (the repo and its Actions logs are public).

    python -m scoring.win_odds_study
"""
from __future__ import annotations

import os
from collections import defaultdict
from math import erf, log, sqrt

SCALES = [round(0.6 + 0.05 * i, 2) for i in range(21)]   # 0.60 .. 1.60
METHODS = ("form", "proj_raw", "proj_cal", "blend_20", "blend_40")
SD_A, SD_B = 2.0, 0.4   # a player's spread from his projection (scoring/live_projection_study.py fit, kickoff point)


def phi(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def win_prob(mu: float, var: float, opp_mu: float, opp_var: float, scale: float) -> float:
    return min(0.99, max(0.01, phi((mu - opp_mu) / (scale * sqrt(max(var + opp_var, 1.0))))))


def team(ids: list[str], est: dict[str, float]) -> tuple[float, float]:
    mu = sum(est.get(i, 0.0) for i in ids)
    var = sum((SD_A * sqrt(max(est.get(i, 0.0), 0.0)) + SD_B) ** 2 for i in ids)
    return mu, var


def main() -> None:
    from supabase import create_client

    from league_ai.nfl_data import season_games, season_stat_rows
    from league_ai.projection_calibration import POSITIONS, factor, week_rows
    from pipeline.league_history.run import league_chain

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    leagues = [lg for lg in client.table("leagues").select("id, sleeper_league_id").execute().data or [] if lg.get("sleeper_league_id")]
    for n, lg in enumerate(leagues, start=1):
        lid, sid = str(lg["id"]), str(lg["sleeper_league_id"])
        scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(sid) if l.get("season")}
        lineups: dict[tuple[int, int], dict[str, list[str]]] = defaultdict(dict)
        start = 0
        while True:
            page = (client.table("league_history_events").select("season, week, owner_name, details").eq("league_id", lid)
                    .eq("kind", "lineup").order("event_id").range(start, start + 999).execute().data or [])
            for r in page:
                ids = [str(s["id"]) for s in ((r.get("details") or {}).get("starters") or []) if s.get("id")]
                lineups[(int(r["season"]), int(r["week"]))][str(r.get("owner_name") or "").casefold()] = ids
            if len(page) < 1000:
                break
            start += 1000
        results = client.table("league_matchups").select("season, week, owner_name, opponent_name, points, won").eq("league_id", lid).limit(20000).execute().data or []
        games = defaultdict(list)
        for r in results:
            if r.get("opponent_name") and r.get("points") is not None:
                a, b = str(r["owner_name"]).casefold(), str(r["opponent_name"]).casefold()
                if a < b:   # each game once
                    games[(int(r["season"]), int(r["week"]))].append((a, b, float(r["points"]), bool(r.get("won"))))
        print(f"league {n}: {len(lineups)} weeks of lineups, {sum(len(v) for v in games.values())} games")

        graded: dict[str, list[tuple[float, float, float, float, bool]]] = defaultdict(list)   # method -> (mu, var, opp_mu, opp_var, won)
        brier: dict[str, list[float]] = defaultdict(list)
        logl: dict[str, list[float]] = defaultdict(list)
        right: dict[str, list[int]] = defaultdict(list)
        miss: dict[str, list[float]] = defaultdict(list)
        calib: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
        carry: dict[str, float] = {}
        for season in sorted({s for s, _ in lineups}):
            sc = scoring.get(season) or next(iter(scoring.values()), {})
            stats_rows, games_all = season_stat_rows(client, season), season_games(client, season)
            hist: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
            played: dict[str, list[float]] = defaultdict(list)
            for week in sorted(w for s, w in lineups if s == season):
                rows = week_rows(client, season, week, sc, stats_rows, games_all)
                if not rows:
                    continue
                f = {p: factor(hist[p][0], hist[p][1], carry.get(p, 1.0)) for p in POSITIONS}
                est: dict[str, dict[str, float]] = {m: {} for m in METHODS}
                for r in rows:
                    pid, cal = r["id"], r["shown"] * f.get(r["pos"], 1.0)
                    g = played[pid]
                    form = (0.6 * sum(g[-3:]) / len(g[-3:]) + 0.4 * sum(g) / len(g)) if g else cal
                    est["form"][pid], est["proj_raw"][pid], est["proj_cal"][pid] = form, r["shown"], cal
                    est["blend_20"][pid], est["blend_40"][pid] = 0.8 * cal + 0.2 * form, 0.6 * cal + 0.4 * form
                for a, b, pts_a, won in games.get((season, week), []):
                    la, lb = lineups[(season, week)].get(a), lineups[(season, week)].get(b)
                    if not la or not lb:
                        continue
                    for m in METHODS:
                        (ma, va), (mb, vb) = team(la, est[m]), team(lb, est[m])
                        past = graded[m]
                        k = min(SCALES, key=lambda s: sum((win_prob(*g[:4], s) - g[4]) ** 2 for g in past)) if len(past) >= 30 else 1.0
                        p = win_prob(ma, va, mb, vb, k)
                        brier[m].append((p - won) ** 2)
                        logl[m].append(-log(p if won else 1 - p))
                        right[m].append(int((p > 0.5) == won))
                        calib[m][min(4, int(p * 5))].append(int(won))
                        miss[m].append(pts_a - ma)
                        past.append((ma, va, mb, vb, won))
                for r in rows:   # learn after the week is graded
                    if r["raw"] >= 3 and r["pos"] in POSITIONS:
                        hist[r["pos"]][0] += r["actual"]
                        hist[r["pos"]][1] += r["shown"]
                    if r["actual"]:
                        played[r["id"]].append(r["actual"])
            carry = {p: h[0] / h[1] for p, h in hist.items() if h[1] > 0}
        for m in METHODS:
            if not brier[m]:
                continue
            nb = len(brier[m])
            cal_txt = " ".join(f"{20 * b}-{20 * b + 20}%:{round(100 * sum(v) / len(v))}%/{len(v)}" for b, v in sorted(calib[m].items()))
            print(f"  {m}: games {nb}, Brier {sum(brier[m]) / nb:.4f}, log loss {sum(logl[m]) / nb:.4f}, favorite won {sum(right[m]) / nb:.1%}, "
                  f"team miss {sum(abs(x) for x in miss[m]) / nb:.1f} (bias {sum(miss[m]) / nb:+.1f}), calibration {cal_txt}")
        # the odds the app saved before games (league_ai_odds_history), same games where graded
        try:
            saved = client.table("league_ai_odds_history").select("season, week, owner_name, win_pct, mu").eq("league_id", lid).limit(5000).execute().data or []
        except Exception:
            saved = []
        won_of = {(int(r["season"]), int(r["week"]), str(r["owner_name"]).casefold()): (bool(r.get("won")), float(r["points"] or 0)) for r in results}
        pairs = [(float(h["win_pct"]) / 100, won_of[k][0]) for h in saved if h.get("win_pct") is not None
                 for k in [(int(h["season"]), int(h["week"]), str(h["owner_name"]).casefold())] if k in won_of and won_of[k][1] > 0]
        if pairs:
            print(f"  saved app odds: {len(pairs)} team-games, Brier {sum((p - y) ** 2 for p, y in pairs) / len(pairs):.4f}")


if __name__ == "__main__":
    main()
