"""Playoff odds: play out the rest of the regular season thousands of times. No model call.

Each team scores a random week around its expected points (its power-ranking lineup score), using
the league's own week-to-week spread. Every simulated week pays standing points the league's way
(2 per win, 1 for a top-5 score), then teams are ranked by standing points and then points for. A
team's playoff odds are how often it finishes in the top spots. A trade is judged by running the
same seasons with both lineups changed.
"""
from __future__ import annotations

import random
from statistics import pstdev
from typing import Any, Callable, Mapping

from services.standings_rules import TOP_FIVE_COUNT, TOP_FIVE_POINTS, WIN_POINTS

SIMS = 5000
PLAYOFF_SPOTS = 4
DEFAULT_SPREAD = 25.0  # points; used until the season has enough games to measure it
# A team's expected points are themselves a guess, shakiest early in the season. Each simulated season draws a
# season-long offset per team of MEAN_SD_K * spread / sqrt(games + MEAN_SD_GAMES), so early odds stay humble.
# Tuned on the backtest (check odds_backtest); 0 turns it off.
MEAN_SD_K = 0.5
MEAN_SD_GAMES = 2.0
SLEEPER = "https://api.sleeper.app/v1"

Schedule = list[list[tuple[str, str]]]


def mean_sd(spread: float, games: int, k: float | None = None, prior_games: float | None = None) -> float:
    """How unsure a team's expected weekly points are after `games` games."""
    k = MEAN_SD_K if k is None else k
    prior_games = MEAN_SD_GAMES if prior_games is None else prior_games
    return k * spread / (max(games, 0) + prior_games) ** 0.5


def simulate(table: Mapping[str, tuple[float, float]], means: Mapping[str, float], schedule: Schedule, *, spread: float = DEFAULT_SPREAD,
             spots: int = PLAYOFF_SPOTS, sims: int = SIMS, seed: int = 7, mean_spread: float = 0.0) -> dict[str, float]:
    """table: owner -> (standing points, points for) so far. means: owner -> expected weekly points.
    schedule: one list of (owner, owner) games per remaining week. mean_spread: sd of each team's season-long
    offset from its expected points (see mean_sd). Returns owner -> playoff chance (0-1)."""
    teams = sorted(set(table) | set(means))
    if not teams:
        return {}
    rng = random.Random(seed)
    made = dict.fromkeys(teams, 0)
    league_avg = sum(means.values()) / len(means) if means else 0.0
    mu = {t: float(means.get(t, league_avg)) for t in teams}
    for _ in range(sims):
        sp = {t: float(table.get(t, (0, 0))[0]) for t in teams}
        pf = {t: float(table.get(t, (0, 0))[1]) for t in teams}
        season_mu = {t: mu[t] + rng.gauss(0.0, mean_spread) for t in teams} if mean_spread > 0 else mu
        for games in schedule:
            scores = {t: rng.gauss(season_mu[t], spread) for t in teams}
            for a, b in games:
                if a in scores and b in scores:
                    win = a if scores[a] > scores[b] else b
                    sp[win] += WIN_POINTS
            for t in sorted(scores, key=scores.get, reverse=True)[:TOP_FIVE_COUNT]:
                sp[t] += TOP_FIVE_POINTS
            for t, s in scores.items():
                pf[t] += s
        for t in sorted(teams, key=lambda t: (sp[t], pf[t]), reverse=True)[:spots]:
            made[t] += 1
    return {t: made[t] / sims for t in teams}


def standings_through(rows: list[Mapping[str, Any]], last_week: int) -> dict[str, tuple[float, float]]:
    """owner -> (standing points, points for) from league_matchups rows for regular-season weeks up to last_week."""
    out: dict[str, list[float]] = {}
    for r in rows:
        if r.get("is_playoff") or int(r.get("week") or 0) > last_week or not r.get("owner_name"):
            continue
        a = out.setdefault(str(r["owner_name"]), [0.0, 0.0])
        a[0] += float(r.get("standing_points") or 0)
        a[1] += float(r.get("points") or 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


def week_spread(rows: list[Mapping[str, Any]]) -> float:
    """How much a team's score swings week to week this season: each score minus that team's average."""
    by_team: dict[str, list[float]] = {}
    for r in rows:
        if not r.get("is_playoff") and r.get("owner_name") and r.get("points") is not None:
            by_team.setdefault(str(r["owner_name"]), []).append(float(r["points"]))
    diffs = [p - sum(ps) / len(ps) for ps in by_team.values() if len(ps) > 1 for p in ps]
    return round(pstdev(diffs), 1) if len(diffs) >= 20 else DEFAULT_SPREAD


def sleeper_schedule(sleeper_league_id: str, weeks: range, roster_to_owner: Mapping[int, str], get_json: Callable[[str], Any] | None = None) -> Schedule:
    """Each week's games as (owner, owner) pairs, from Sleeper's matchups (it lists the whole season up front)."""
    import requests

    get_json = get_json or (lambda url: requests.get(url, timeout=15).json())
    out: Schedule = []
    for w in weeks:
        try:
            rows = get_json(f"{SLEEPER}/league/{sleeper_league_id}/matchups/{w}") or []
        except Exception:
            rows = []
        pairs: dict[Any, list[str]] = {}
        for r in rows:
            if r.get("matchup_id") is not None and r.get("roster_id") is not None:
                pairs.setdefault(r["matchup_id"], []).append(roster_to_owner.get(int(r["roster_id"]), f"Roster {r['roster_id']}"))
        out.append([(p[0], p[1]) for p in pairs.values() if len(p) == 2])
    return out


class OddsModel:
    """Everything a simulation needs for one league-week, loaded once: results so far, schedule, spread and expected points."""

    def __init__(self, rows: list[Mapping[str, Any]], means: Mapping[str, float], week: int, playoff_start: int,
                 schedule_for: Callable[[range], Schedule]):
        self.week, self.playoff_start = int(week), int(playoff_start)
        self._odds: dict[str, float] | None = None
        self.week_wins: dict[str, dict[str, Any]] = {}  # this week's matchup odds, when the caller has them
        self.means = dict(means)
        # One spelling per owner: the power rankings' names win over the matchup history's.
        canon = {name.casefold(): name for name in self.means}
        self.name = lambda n: canon.get(str(n).casefold(), str(n))  # noqa: E731
        self.rows = [{**r, "owner_name": self.name(r.get("owner_name") or "")} for r in rows]
        rows = self.rows
        self.spread = week_spread(rows)
        played = [int(r.get("week") or 0) for r in rows if not r.get("is_playoff")]
        # Weeks before the current NFL week are final; a week in progress is simulated from scratch.
        self.last_final = min(max(played, default=0), self.week - 1)
        self.full_schedule = [[(self.name(a), self.name(b)) for a, b in games] for games in schedule_for(range(max(1, self.last_final), self.playoff_start))]
        self.mean_spread = mean_sd(self.spread, self.last_final)

    def remaining(self) -> Schedule:
        return self.full_schedule[1:] if self.last_final >= 1 else self.full_schedule

    def odds(self, means: Mapping[str, float] | None = None) -> dict[str, float]:
        if means is None:
            if self._odds is None:
                self._odds = simulate(standings_through(self.rows, self.last_final), self.means, self.remaining(), spread=self.spread, mean_spread=self.mean_spread)
            return self._odds
        return simulate(standings_through(self.rows, self.last_final), means, self.remaining(), spread=self.spread, mean_spread=self.mean_spread)

    def last_week_odds(self) -> dict[str, float]:
        """The odds as they stood before the last finished week, so the app can show the move."""
        if self.last_final < 1:
            return {}
        return simulate(standings_through(self.rows, self.last_final - 1), self.means, self.full_schedule, spread=self.spread,
                        mean_spread=mean_sd(self.spread, self.last_final - 1))

    def summary(self) -> dict[str, Any]:
        now, before = self.odds(), self.last_week_odds()
        out: dict[str, Any] = {"odds": {t: round(100 * p) for t, p in now.items()},
                               "change": {t: round(100 * (p - before[t])) for t, p in now.items() if t in before},
                               "weeks_left": len(self.remaining()), "spots": PLAYOFF_SPOTS}
        if self.week_wins:
            out["this_week_win_odds"] = {t: f"{round(100 * w['win'])}% to win this week's game vs {w['opp']} (not playoff odds)" for t, w in self.week_wins.items()}
        return out


def pickup_odds(model: OddsModel, owner: str, gain: float) -> dict[str, Any]:
    """This owner's playoff odds with and without a pickup that changes their weekly points by `gain`."""
    before = model.odds()
    means = dict(model.means)
    means[owner] = means.get(owner, 0.0) + gain
    after = model.odds(means)
    return {"before": round(100 * before.get(owner, 0)), "after": round(100 * after.get(owner, 0)), "weekly_points_change": round(gain, 1),
            "weeks_left": len(model.remaining())}


def load_rows(client: Any, league_id: str, season: int) -> list[dict[str, Any]]:
    try:
        return (client.table("league_matchups").select("owner_name, week, points, standing_points, is_playoff, won")
                .eq("league_id", league_id).eq("season", int(season)).limit(2000).execute().data or [])
    except Exception:
        return []


def trade_odds(model: OddsModel, owner: str, partner: str, my_gain: float, their_gain: float) -> dict[str, Any]:
    """Playoff odds for both teams with and without a trade; gains are the change in each starting lineup's weekly points."""
    before = model.odds()
    after_means = dict(model.means)
    after_means[owner] = after_means.get(owner, 0.0) + my_gain
    after_means[partner] = after_means.get(partner, 0.0) + their_gain
    after = model.odds(after_means)

    def pct(p: float) -> int:
        return round(100 * p)

    out = {
        "you": {"before": pct(before.get(owner, 0)), "after": pct(after.get(owner, 0)), "weekly_points_change": round(my_gain, 1)},
        partner: {"before": pct(before.get(partner, 0)), "after": pct(after.get(partner, 0)), "weekly_points_change": round(their_gain, 1)},
        "weeks_left": len(model.remaining()),
        "how_to_use": f"Playoff odds are the share of {SIMS} simulated rest-of-seasons where the team finishes top {PLAYOFF_SPOTS}. Quote the change for "
                      "this owner, and say if the trade lifts a rival for a playoff spot. Weigh it with the trade's value; it only measures this season.",
    }
    movers = {t: pct(after[t]) - pct(before[t]) for t in before if t not in (owner, partner) and abs(pct(after[t]) - pct(before[t])) >= 3}
    if movers:
        out["others_moved"] = movers
    return out


# ---- backtest: how accurate are the odds? ------------------------------------------

def backtest_season(rows: list[Mapping[str, Any]], *, sims: int = 2000, shrink_games: float = 3.0, k: float | None = None,
                    prior_games: float | None = None) -> list[dict[str, Any]]:
    """Replay one finished season week by week: predict with only what was known then, then compare with who made it.

    Expected points for a team = its points per game so far, pulled toward the league average by shrink_games
    (early-season averages are noisy). Returns one row per team per week: week, team, predicted chance, made it.
    """
    reg = [r for r in rows if not r.get("is_playoff") and r.get("owner_name")]
    if not reg:
        return []
    last = max(int(r["week"]) for r in reg)
    final = standings_through(reg, last)
    made = {t for t in sorted(final, key=lambda t: final[t], reverse=True)[:PLAYOFF_SPOTS]}
    games: dict[int, list[tuple[str, str]]] = {}
    for r in reg:
        a, b, w = str(r["owner_name"]), str(r.get("opponent_name") or ""), int(r["week"])
        if b and a < b:
            games.setdefault(w, []).append((a, b))
    out = []
    for w in range(1, last):
        played = [r for r in reg if int(r["week"]) <= w]
        table = standings_through(played, w)
        avg = sum(float(r.get("points") or 0) for r in played) / max(1, len(played))
        pts: dict[str, list[float]] = {}
        for r in played:
            pts.setdefault(str(r["owner_name"]), []).append(float(r.get("points") or 0))
        means = {t: (sum(p) + avg * shrink_games) / (len(p) + shrink_games) for t, p in pts.items()}
        spread = week_spread(played)
        odds = simulate(table, means, [games.get(x, []) for x in range(w + 1, last + 1)], spread=spread, sims=sims, seed=w,
                        mean_spread=mean_sd(spread, w, k, prior_games))
        now_top = set(sorted(table, key=lambda t: table[t], reverse=True)[:PLAYOFF_SPOTS])
        out += [{"week": w, "team": t, "p": p, "made": t in made, "top_now": t in now_top} for t, p in odds.items()]
    return out


def score_backtest(preds: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Brier score (lower is better; 0.25 is a coin flip) against a naive baseline that says 'top 4 now = in', plus calibration buckets."""
    if not preds:
        return {"predictions": 0}
    brier = sum((p["p"] - (1.0 if p["made"] else 0.0)) ** 2 for p in preds) / len(preds)
    base = sum(((1.0 if p.get("top_now") else 0.0) - (1.0 if p["made"] else 0.0)) ** 2 for p in preds) / len(preds)
    buckets = []
    for lo in range(0, 100, 20):
        hit = [p for p in preds if lo <= 100 * p["p"] < lo + 20 or (lo == 80 and p["p"] == 1.0)]
        if hit:
            buckets.append({"said": f"{lo}-{lo + 20}%", "teams": len(hit), "made_it": f"{round(100 * sum(p['made'] for p in hit) / len(hit))}%"})
    return {"predictions": len(preds), "brier": round(brier, 3), "baseline_brier": round(base, 3), "calibration": buckets}


def main(argv: list[str] | None = None) -> int:
    """Backtest every finished season in league_matchups (service role) and print the scores."""
    import os
    import sys

    from supabase import create_client

    _ = argv if argv is not None else sys.argv[1:]
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for league in client.table("leagues").select("id, name").execute().data or []:
        rows = (client.table("league_matchups").select("season, week, owner_name, opponent_name, points, standing_points, is_playoff")
                .eq("league_id", league["id"]).limit(20000).execute().data or [])
        seasons = sorted({int(r["season"]) for r in rows})
        everything: list[dict[str, Any]] = []
        for season in seasons:
            season_rows = [r for r in rows if int(r["season"]) == season]
            if season == max(seasons) and not any(r.get("is_playoff") for r in season_rows):
                continue  # this season's regular season isn't over: nothing to score against yet
            preds = [{**p, "season": season} for p in backtest_season(season_rows)]
            everything += preds
            print(f"{league.get('name')} {season}: {score_backtest(preds)}")
        if everything:
            print(f"{league.get('name')} ALL SEASONS: {score_backtest(everything)}")
            for half, label in ((lambda w: w <= 5, "weeks 1-5"), (lambda w: w > 5, "week 6 on")):
                print(f"  {label}: {score_backtest([p for p in everything if half(p['week'])])}")
            # tuning grid for the early-season humility (MEAN_SD_K, MEAN_SD_GAMES) and the points-per-game shrink
            done = [s for s in seasons if not (s == max(seasons) and not any(r.get("is_playoff") for r in rows if int(r["season"]) == s))]
            for shrink in (3.0, 6.0):
                for k in (0.0, 0.25, 0.5, 0.75, 1.0):
                    for g in (1.0, 2.0, 4.0):
                        if k == 0.0 and g != 2.0:
                            continue
                        preds = [p for s in done for p in backtest_season([r for r in rows if int(r["season"]) == s], sims=1000, shrink_games=shrink, k=k, prior_games=g)]
                        early = score_backtest([p for p in preds if p["week"] <= 5])
                        late = score_backtest([p for p in preds if p["week"] > 5])
                        print(f"  grid shrink={shrink:g} k={k:g} games={g:g}: all {score_backtest(preds).get('brier')} early {early.get('brier')} "
                              f"late {late.get('brier')} early calibration {early.get('calibration')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
