"""This week's win odds and risk-aware lineups. No model call.

A lineup's score is treated as normal: its mean is the sum of each starter's matchup-adjusted
expectation, its variance the sum of each starter's week-to-week variance. Win odds compare the two
lineups. A favourite should take the steadier of two close options and an underdog the boom-or-bust
one, so risk_swaps looks for bench swaps that raise the chance of winning, not just the average.

Every week's odds are saved (league_ai_odds_history) and graded once the games are final, and the
graded history tunes `scale` (how wide the score spread really is) so the odds correct themselves.
"""
from __future__ import annotations

from math import erf, sqrt
from typing import Any, Iterable, Mapping

from league_ai.power import FLEX

DEFAULT_SD = {"QB": 7.0, "RB": 7.0, "WR": 7.5, "TE": 5.5, "K": 4.0, "DEF": 5.0}
CLOSE_POINTS = 3.0     # only swap players whose averages are this close: risk breaks ties, it doesn't overrule the projection
MIN_GAIN = 0.01        # a swap must add at least one point of win chance
SCALES = [round(0.7 + 0.05 * i, 2) for i in range(19)]  # 0.70 .. 1.60
MIN_GRADED = 30        # games before the graded history is trusted to tune the scale


def phi(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def sd_of(p: Mapping[str, Any]) -> float:
    return float(p.get("sd") or DEFAULT_SD.get(str(p.get("pos") or "").upper(), 7.0))


def lineup_dist(players: Iterable[Mapping[str, Any]]) -> tuple[float, float]:
    """(mean, variance) of a lineup's total."""
    players = list(players)
    return sum(float(p.get("pts") or 0) for p in players), sum(sd_of(p) ** 2 for p in players)


def win_prob(mu: float, var: float, opp_mu: float, opp_var: float, scale: float = 1.0) -> float:
    spread = scale * sqrt(max(var + opp_var, 1.0))
    return phi((mu - opp_mu) / spread)


def _fits(slot: str | None, pos: str) -> bool:
    slot = str(slot or "")
    return pos in FLEX[slot] if slot in FLEX else slot == pos


def risk_swaps(starters: list[Mapping[str, Any]], bench: list[Mapping[str, Any]], opp_mu: float, opp_var: float, *, scale: float = 1.0,
               limit: int = 2) -> tuple[list[dict[str, Any]], float, float]:
    """Bench swaps that raise this week's win chance. Returns (swaps, win chance before, after)."""
    lineup = [dict(p) for p in starters]
    pool = [dict(p) for p in bench if str(p.get("injury") or "").lower() not in {"out", "ir", "doubtful", "injured reserve", "pup", "suspended"}]
    before = win_prob(*lineup_dist(lineup), opp_mu, opp_var, scale)
    current, swaps = before, []
    for _ in range(limit):
        best = None
        for i, s in enumerate(lineup):
            for b in pool:
                if not _fits(s.get("slot"), str(b.get("pos") or "")) or float(s.get("pts") or 0) - float(b.get("pts") or 0) > CLOSE_POINTS:
                    continue
                trial = lineup[:i] + [{**b, "slot": s.get("slot")}] + lineup[i + 1:]
                p = win_prob(*lineup_dist(trial), opp_mu, opp_var, scale)
                if p - current >= MIN_GAIN and (best is None or p > best[0]):
                    best = (p, i, b)
        if best is None:
            break
        p, i, b = best
        out = lineup[i]
        swaps.append({"in": b, "out": out, "slot": out.get("slot"), "gain": round(p - current, 3),
                      "why": "boom-or-bust upside as the underdog" if current < 0.5 else "steadier floor as the favourite"})
        pool = [x for x in pool if x["id"] != b["id"]] + [out]
        lineup[i] = {**b, "slot": out.get("slot")}
        current = p
    return swaps, before, current


def week_odds(rankings: list[Mapping[str, Any]], games: list[tuple[str, str]], scale: float = 1.0) -> dict[str, dict[str, Any]]:
    """Owner -> {win, mu, var, opp, opp_mu, opp_var} for this week's games, from each team's best lineup."""
    by_owner = {str(r.get("owner")): r for r in rankings}
    out: dict[str, dict[str, Any]] = {}
    for a, b in games:
        ra, rb = by_owner.get(a), by_owner.get(b)
        if not ra or not rb or "week_lineup" not in ra or "week_lineup" not in rb:
            continue
        (ma, va), (mb, vb) = lineup_dist(ra["week_lineup"]), lineup_dist(rb["week_lineup"])
        out[a] = {"win": win_prob(ma, va, mb, vb, scale), "mu": round(ma, 1), "var": round(va, 1), "opp": b, "opp_mu": round(mb, 1), "opp_var": round(vb, 1)}
        out[b] = {"win": 1 - out[a]["win"], "mu": round(mb, 1), "var": round(vb, 1), "opp": a, "opp_mu": round(ma, 1), "opp_var": round(va, 1)}
    return out


# ---- saved odds and grading --------------------------------------------------------

def history_rows(league_id: str, season: int, week: int, wins: Mapping[str, Mapping[str, Any]], playoff: Mapping[str, float]) -> list[dict[str, Any]]:
    owners = sorted(set(wins) | set(playoff))
    return [{"league_id": league_id, "season": int(season), "week": int(week), "owner_name": o,
             "playoff_pct": round(100 * playoff[o], 1) if o in playoff else None,
             "win_pct": round(100 * wins[o]["win"], 1) if o in wins else None,
             "mu": wins.get(o, {}).get("mu"), "var": wins.get(o, {}).get("var"),
             "opp_owner": wins.get(o, {}).get("opp"), "opp_mu": wins.get(o, {}).get("opp_mu"), "opp_var": wins.get(o, {}).get("opp_var")} for o in owners]


def save_history(client: Any, rows: list[Mapping[str, Any]]) -> None:
    """First snapshot of the week wins (before kickoff is what gets graded). Best effort."""
    if not rows:
        return
    try:
        client.table("league_ai_odds_history").upsert(list(rows), on_conflict="league_id,season,week,owner_name", ignore_duplicates=True).execute()
    except Exception as exc:
        print(f"ODDS_HISTORY_WRITE_FAILED {type(exc).__name__}", flush=True)


def load_history(client: Any, league_id: str, season: int | None = None) -> list[dict[str, Any]]:
    try:
        q = client.table("league_ai_odds_history").select("*").eq("league_id", league_id)
        if season is not None:
            q = q.eq("season", int(season))
        return q.limit(5000).execute().data or []
    except Exception:
        return []


def graded_games(history: list[Mapping[str, Any]], results: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Saved win odds joined with final scores (league_matchups rows: season, week, owner_name, won)."""
    won = {(int(r["season"]), int(r["week"]), str(r["owner_name"]).casefold()): bool(r.get("won")) for r in results
           if r.get("season") is not None and r.get("owner_name")}
    out = []
    for h in history:
        key = (int(h["season"]), int(h["week"]), str(h["owner_name"]).casefold())
        if h.get("win_pct") is not None and key in won and h.get("mu") is not None and h.get("opp_mu") is not None:
            out.append({**h, "won": won[key]})
    return out


def brier(pairs: Iterable[tuple[float, bool]]) -> float | None:
    pairs = list(pairs)
    return round(sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def fit_scale(games: list[Mapping[str, Any]]) -> float:
    """The spread multiplier that would have scored the graded games best; 1.0 until there are enough of them."""
    if len(games) < MIN_GRADED:
        return 1.0

    def score(k: float) -> float:
        return brier((win_prob(float(g["mu"]), float(g.get("var") or 0), float(g["opp_mu"]), float(g.get("opp_var") or 0), k), g["won"]) for g in games) or 1.0
    return min(SCALES, key=score)


def grade_report(history: list[Mapping[str, Any]], results: list[Mapping[str, Any]], final_playoff: Mapping[tuple[int, str], bool] | None = None) -> dict[str, Any]:
    """How the saved odds did: weekly win odds (Brier, calibration, the tuned scale) and, for finished seasons, playoff odds."""
    games = graded_games(history, results)
    pairs = [(float(g["win_pct"]) / 100, g["won"]) for g in games]
    buckets = []
    for lo in range(0, 100, 20):
        hit = [(p, y) for p, y in pairs if lo <= 100 * p < lo + 20 or (lo == 80 and p >= 1.0)]
        if hit:
            buckets.append({"said": f"{lo}-{lo + 20}%", "games": len(hit), "won": f"{round(100 * sum(y for _, y in hit) / len(hit))}%"})
    out: dict[str, Any] = {"weekly_games": len(games), "weekly_brier": brier(pairs), "weekly_calibration": buckets, "tuned_scale": fit_scale(games)}
    if final_playoff:
        pp = [(float(h["playoff_pct"]) / 100, final_playoff[(int(h["season"]), str(h["owner_name"]).casefold())]) for h in history
              if h.get("playoff_pct") is not None and (int(h["season"]), str(h["owner_name"]).casefold()) in final_playoff]
        out.update({"playoff_predictions": len(pp), "playoff_brier": brier(pp)})
    return out


def final_playoff_teams(results: list[Mapping[str, Any]], spots: int = 4) -> dict[tuple[int, str], bool]:
    """(season, owner) -> made the playoffs, for seasons whose playoffs have started (top spots by standing points, then points for)."""
    from league_ai.playoff_odds import standings_through

    out: dict[tuple[int, str], bool] = {}
    for season in sorted({int(r["season"]) for r in results if r.get("season") is not None}):
        rows = [r for r in results if int(r.get("season") or 0) == season]
        if not any(r.get("is_playoff") for r in rows):
            continue
        table = standings_through(rows, max(int(r["week"]) for r in rows))
        made = set(sorted(table, key=lambda t: table[t], reverse=True)[:spots])
        out.update({(season, t.casefold()): t in made for t in table})
    return out


def league_grade(client: Any, league_id: str) -> dict[str, Any]:
    results = (client.table("league_matchups").select("season, week, owner_name, points, standing_points, won, is_playoff")
               .eq("league_id", league_id).limit(20000).execute().data or [])
    return grade_report(load_history(client, league_id), results, final_playoff_teams(results))


def main() -> int:
    """Print how every league's saved odds have graded so far (service role)."""
    import os

    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for league in client.table("leagues").select("id, name").execute().data or []:
        print(f"{league.get('name')}: {league_grade(client, str(league['id']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def saved_week(client: Any, league_id: str, season: int, week: int | None = None) -> tuple[int | None, dict[str, dict[str, Any]]]:
    """(week, owner -> {playoff_pct, win_pct, opp_owner}) as the Front Office last saved them: that week, or the latest saved.
    Read only, no model run, so any page (app or website) can show the same numbers."""
    try:
        q = client.table("league_ai_odds_history").select("week,owner_name,playoff_pct,win_pct,opp_owner").eq("league_id", league_id).eq("season", int(season))
        rows = (q.eq("week", int(week)) if week else q.order("week", desc=True).limit(40)).execute().data or []
    except Exception:
        return None, {}
    if not rows:
        return None, {}
    latest = max(int(r["week"]) for r in rows)
    return latest, {str(r["owner_name"]): r for r in rows if int(r["week"]) == latest}
