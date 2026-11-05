"""Weekly projections that grade and correct themselves. No model call.

Every finished week, each player's projection (Sleeper's projected stat line, scored with the league's rules by our
engine, tilted by the matchup) is compared with what he really scored (our own final stat lines, Sleeper's as a
fallback). Projections have run high at most positions, so each position gets a factor:

    factor = (real points so far + PRIOR x last season's ratio) / (projected points so far + PRIOR)

learned only from weeks already played, starting each season from where the last one ended and leaning on this
season more as its weeks pile up. Tested walk-forward on 2024-2026 (scoring/projection_accuracy.py): starters'
average miss 6.97 -> 6.53 pts and team totals 23.8 -> 18.9 pts in 2026, with the overstatement (-16.6 pts a team
a week) gone. Scaling a whole position never changes who to start within it.

The daily learning job replays every season, saves the factors and an accuracy card (league_ai_experiments,
experiment 'projection_calibration_v1', league row under the system user), and snapshots the projections shown
before each kickoff ('projection_snapshot_v1') so the card also grades exactly what the app showed. Prints and
saves numbers only (no names): the repo and its Actions logs are public.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import sqrt
from typing import Any, Iterable, Mapping

EXPERIMENT = "projection_calibration_v1"
SNAPSHOT = "projection_snapshot_v1"
SYSTEM_USER = "00000000-0000-0000-0000-000000000000"
PRIOR = 300.0         # projected points of last season's ratio mixed in (about two weeks of a position's projections)
MIN_PROJ = 3.0        # players projected under this weren't real options; they don't teach the factor
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
SKILL = ("QB", "RB", "WR", "TE")
FACTOR_RANGE = (0.6, 1.5)
FACTORS_TTL = 3600
LINE_PRIOR_N = 30.0   # player-weeks' worth of last season's line (or of "the projection is right") mixed in
SLOPE_RANGE = (0.5, 1.5)
MIN_TOP_SLOPE = 0.3   # above the knee a higher projection still means a higher number
KNEE = None           # projected points where the line may bend (None = a straight line)   # a slope above 0 keeps the order within a position: a higher projection stays higher


# ---- the math (pure) -----------------------------------------------------------------------------------------

def factor(actual: float, projected: float, carry: float = 1.0, prior: float = PRIOR) -> float:
    low, high = FACTOR_RANGE
    return max(low, min(high, (actual + prior * carry) / (projected + prior)))


class Line:
    """actual ~ a + b x per position, fit by least squares on finished weeks. Big projections run high more than small
    ones (owners start the big ones, and the biggest regress most), so a line beats one ratio for the whole position.
    Walk-forward on 2024-2026 starters (scoring/projection_accuracy.py, lin_carry): team totals MAE 19.42/21.39/18.56
    (one ratio) -> 19.39/20.83/18.15."""

    def __init__(self, sums: Iterable[float] | None = None) -> None:
        self.s = list(sums) if sums else [0.0] * 5   # n, sum x, sum y, sum xx, sum xy

    def add(self, x: float, y: float) -> None:
        for i, v in enumerate((1.0, x, y, x * x, x * y)):
            self.s[i] += v

    def fit(self, prior: "Line | None" = None) -> tuple[float, float]:
        s = list(self.s)
        if prior is not None and prior.s[0] > 0:
            k = LINE_PRIOR_N / prior.s[0]
            s = [a + k * b for a, b in zip(s, prior.s)]
        else:   # no last season: lean on the projection being right (points on y = x)
            for x in (5.0, 10.0, 15.0, 20.0):
                for i, v in enumerate((1.0, x, x, x * x, x * x)):
                    s[i] += LINE_PRIOR_N / 4 * v
        n, sx, sy, sxx, sxy = s
        den = n * sxx - sx * sx
        b = (n * sxy - sx * sy) / den if den > 0 else 1.0
        b = max(SLOPE_RANGE[0], min(SLOPE_RANGE[1], b))
        return round((sy - b * sx) / n, 3), round(b, 3)


class KneeLine:
    """actual ~ a + b x + c max(0, x - knee): a line that can bend at the knee, so the biggest projections (the stars)
    get their own trim. Least squares with last season's fit (or "the projection is right") mixed in, kept monotone
    (slope above the knee stays at least MIN_TOP_SLOPE)."""

    def __init__(self, knee: float, sums: Iterable[float] | None = None) -> None:
        self.knee = knee
        self.s = list(sums) if sums else [0.0] * 10   # n, then XtX upper (6 values) and Xty (3)

    @staticmethod
    def _basis(x: float, knee: float) -> tuple[float, float, float]:
        return 1.0, x, max(0.0, x - knee)

    def add(self, x: float, y: float, w: float = 1.0) -> None:
        f = self._basis(x, self.knee)
        self.s[0] += w
        k = 1
        for i in range(3):
            for j in range(i, 3):
                self.s[k] += w * f[i] * f[j]
                k += 1
        for i in range(3):
            self.s[7 + i] += w * f[i] * y

    def fit(self, prior: "KneeLine | None" = None) -> tuple[float, float, float]:
        import numpy as np

        s = list(self.s)
        if prior is not None and prior.s[0] > 0:
            k = LINE_PRIOR_N / prior.s[0]
            s = [a + k * b for a, b in zip(s, prior.s)]
        else:
            tmp = KneeLine(self.knee)
            for x in (5.0, 10.0, 15.0, 20.0, 25.0):
                tmp.add(x, x, LINE_PRIOR_N / 5)
            s = [a + b for a, b in zip(s, tmp.s)]
        m = np.zeros((3, 3))
        k = 1
        for i in range(3):
            for j in range(i, 3):
                m[i, j] = m[j, i] = s[k]
                k += 1
        v = np.array(s[7:10])
        try:
            a, b, c = np.linalg.solve(m + np.eye(3) * 1e-6, v)
        except Exception:
            return 0.0, 1.0, 0.0
        b = max(SLOPE_RANGE[0], min(SLOPE_RANGE[1], float(b)))
        c = max(MIN_TOP_SLOPE - b, min(0.5, float(c)))
        # refit the intercept for the clamped slopes
        n, sx, sh, sy = s[1], s[2], s[3], s[7]
        a = (sy - b * sx - c * sh) / n if n else 0.0
        return round(a, 3), round(b, 3), round(c, 3)


def line_points(x: float, ab: Iterable[float]) -> float:
    """A projection through a fitted line ([a, b] or a bent one [a, b, c, knee]); under MIN_PROJ it scales with the
    line's value at MIN_PROJ (no jump, no free points for a player barely projected)."""
    v = [float(t) for t in ab]
    a, b = v[0], v[1]
    c, knee = (v[2], v[3]) if len(v) >= 4 else (0.0, 0.0)

    def at(z: float) -> float:
        return a + b * z + c * max(0.0, z - knee)
    if x >= MIN_PROJ:
        return max(0.0, at(x))
    return max(0.0, x * at(MIN_PROJ) / MIN_PROJ)


def _with_knee(fit: tuple[float, ...], knee: float | None) -> tuple[float, ...]:
    return (*fit, float(knee)) if knee and len(fit) == 3 else fit


def _stats(errs: list[float]) -> dict[str, Any] | None:
    if not errs:
        return None
    n = len(errs)
    return {"n": n, "mae": round(sum(abs(e) for e in errs) / n, 2), "bias": round(sum(errs) / n, 2),
            "sd": round(sqrt(sum(e * e for e in errs) / n), 2)}


def replay(weeks: Iterable[Mapping[str, Any]], prior: float = PRIOR, knee: float | None = None) -> dict[str, Any]:
    """weeks, oldest first: {season, week, rows: [{id, pos, raw, shown, actual}], lineups: [[player ids]], snapshot: {id: pts}}.
    raw = the projection before the matchup tilt, shown = after it (both before calibration); actual None = no real score.

    Returns the factors for the next week of the latest season plus that season's card."""
    carry: dict[str, float] = {}
    hist: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    knee = KNEE if knee is None else (knee or None)
    new_line = (lambda: KneeLine(knee)) if knee else Line
    lines: dict[str, Any] = defaultdict(new_line)
    line_carry: dict[str, Any] = {}
    season = None
    card_weeks: list[dict[str, Any]] = []
    season_errs: dict[str, list[float]] = defaultdict(list)
    for wk in weeks:
        if wk["season"] != season:
            if season is not None:
                carry = {p: h[0] / h[1] for p, h in hist.items() if h[1] > 0}
                line_carry = {p: l for p, l in lines.items() if l.s[0] > 0}
            season, hist, card_weeks, season_errs = wk["season"], defaultdict(lambda: [0.0, 0.0]), [], defaultdict(list)
            lines = defaultdict(new_line)
        f = {p: factor(hist[p][0], hist[p][1], carry.get(p, 1.0), prior) for p in POSITIONS}
        ln = {p: _with_knee(lines[p].fit(line_carry.get(p)), knee) for p in SKILL}
        cal_of = lambda r: calibrate(r["shown"], r["pos"], f, ln)  # noqa: E731
        rows = [r for r in wk["rows"] if r.get("actual") is not None]
        by_id = {str(r["id"]): r for r in rows}
        errs: dict[str, list[float]] = defaultdict(list)
        for r in wk["rows"]:
            r["cal"] = cal_of(r)   # the walk-forward calibrated number (the projection learning builds on it)
        for r in rows:
            if r["raw"] < MIN_PROJ:
                continue
            cal = r["cal"]
            errs["shown"].append(r["actual"] - r["shown"])
            errs["calibrated"].append(r["actual"] - cal)
            season_errs[f"pos:{r['pos']}"].append(r["actual"] - cal)
        for ids in wk.get("lineups") or []:
            got = [by_id[i] for i in ids if i in by_id]
            if not got:
                continue
            real = sum(r["actual"] for r in got)
            errs["team_shown"].append(real - sum(r["shown"] for r in got))
            errs["team_calibrated"].append(real - sum(cal_of(r) for r in got))
        snap = wk.get("snapshot") or {}
        for pid, pts in snap.items():
            r = by_id.get(str(pid))
            if r and r["raw"] >= MIN_PROJ:
                errs["as_shown"].append(r["actual"] - float(pts))
        for k, v in errs.items():
            season_errs[k] += v
        if errs:
            card_weeks.append({"week": wk["week"], **{k: _stats(v) for k, v in errs.items()}})
        for r in rows:   # learn only after the week is graded
            if r["raw"] >= MIN_PROJ and r["pos"] in POSITIONS:
                hist[r["pos"]][0] += r["actual"]
                hist[r["pos"]][1] += r["shown"]
                if r["pos"] in SKILL:
                    lines[r["pos"]].add(r["shown"], r["actual"])
    if season is None:
        return {}
    nxt = {p: round(factor(hist[p][0], hist[p][1], carry.get(p, 1.0), prior), 3) for p in POSITIONS}
    return {
        "season": season,
        "through_week": card_weeks[-1]["week"] if card_weeks else 0,
        "factors": nxt,
        "lines": {p: list(_with_knee(lines[p].fit(line_carry.get(p)), knee)) for p in SKILL},
        "season_ratio": {p: round(h[0] / h[1], 3) for p, h in hist.items() if h[1] > 0},
        "last_season_ratio": {p: round(v, 3) for p, v in carry.items() if p in POSITIONS},
        "overall": {k: _stats(v) for k, v in season_errs.items() if not k.startswith("pos:")},
        "by_position": [{"pos": p, "factor": nxt[p], **(_stats(season_errs[f"pos:{p}"]) or {})} for p in POSITIONS if season_errs.get(f"pos:{p}")],
        "by_week": card_weeks,
    }


def calibrate(points: float, position: str, factors: Mapping[str, float], lines: Mapping[str, Iterable[float]] | None = None) -> float:
    """A projection corrected by the learned line for its position (QB/RB/WR/TE), else by the position factor."""
    pos = str(position or "").upper()
    if lines and lines.get(pos):
        return line_points(points, lines[pos])
    return points * float(factors.get(pos, 1.0))


def apply(points: float, position: str, factors: Mapping[str, float]) -> float:
    return calibrate(points, position, factors)


# ---- reading the saved factors (app, website, Front Office) ---------------------------------------------------

def _league_id(client: Any, sleeper_id: str) -> str | None:
    rows = client.table("leagues").select("id").eq("sleeper_league_id", str(sleeper_id)).limit(1).execute().data or []
    return str(rows[0]["id"]) if rows else None


def latest(client: Any, league_id: str) -> dict[str, Any] | None:
    try:
        rows = (client.table("league_ai_experiments").select("result").eq("league_id", league_id).eq("experiment", EXPERIMENT)
                .order("created_at", desc=True).limit(1).execute().data or [])
    except Exception:
        return None
    return rows[0].get("result") if rows else None


def calibration(client: Any, sleeper_id: str, season: int) -> dict[str, dict[str, Any]]:
    """{'factors': position -> factor, 'lines': position -> [a, b]} for this league's projections; empty (no change)
    when none is saved or it can't be read. A card from last season still applies to week 1 of the next."""
    from api import cache

    def load() -> dict[str, dict[str, Any]]:
        try:
            lid = _league_id(client, sleeper_id)
            card = latest(client, lid) if lid else None
        except Exception:
            return {"factors": {}, "lines": {}}
        if not card or int(card.get("season") or 0) > int(season):
            return {"factors": {}, "lines": {}}
        return {"factors": {str(k): float(v) for k, v in (card.get("factors") or {}).items()},
                "lines": {str(k): [float(x) for x in v] for k, v in (card.get("lines") or {}).items() if v and len(v) in (2, 4)},
                "adjust": card.get("adjust") or {}}

    if client is None:
        return {"factors": {}, "lines": {}}
    return cache.cached(("projection_calibration", str(sleeper_id), int(season)), FACTORS_TTL, load)


def position_factors(client: Any, sleeper_id: str, season: int) -> dict[str, float]:
    """Position -> factor only (older callers)."""
    return calibration(client, sleeper_id, season)["factors"]


def card(client: Any, league_id: str) -> dict[str, Any] | None:
    """The accuracy card for the report card (app and website): season, factors, overall and by week."""
    c = latest(client, league_id)
    if not c or not c.get("overall"):
        return None
    return {k: c.get(k) for k in ("season", "through_week", "factors", "lines", "learning", "overall", "by_position", "by_week", "computed_at")}


# ---- building the weeks from data (learning job) --------------------------------------------------------------

def _actual_lines(client: Any, season: int, week: int) -> dict[str, dict[str, Any]]:
    """Our own official lines, with Sleeper's for anyone ours leave out (team defenses aren't in ours yet) and for
    seasons before ours were kept."""
    from api.live_stats import final_lines
    from api.projections import POSITIONS as Q_POS, _rows
    from league_ai.start_sit_grade import _sleeper

    ours = dict(final_lines(client, season, week) or {})
    try:
        query = "season_type=regular&" + "&".join(f"position[]={p}" for p in Q_POS)
        theirs = _rows(_sleeper(f"stats/nfl/{season}/{week}?{query}"))
    except Exception:
        theirs = {}
    if not ours:
        return theirs
    out = dict(theirs)
    for pid, line in ours.items():
        if str((theirs.get(pid) or {}).get("pos") or line.get("pos") or "").upper() != "DEF":
            out[pid] = {**(theirs.get(pid) or {}), **line}
    return out


def _td_points(stats: Mapping[str, Any] | None, scoring: Mapping[str, Any]) -> float:
    """Points from touchdowns alone (for telling a touchdown swing from a role change when a projection misses)."""
    total = 0.0
    for key in ("pass_td", "rush_td", "rec_td"):
        try:
            total += float((stats or {}).get(key) or 0) * float(scoring.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def week_rows(client: Any, season: int, week: int, scoring: Mapping[str, Any], stats_rows: list[dict[str, Any]],
              games_all: list[dict[str, Any]], actual: bool = True) -> list[dict[str, Any]]:
    """Every projected player's raw and matchup-tilted projection (as the app makes them, before calibration), and his
    real score when actual=True. The defense ratings use only games played before the week."""
    from api.projections import score
    from league_ai.nfl_data import defense_vs_position_from_stats, team_code, total_multiplier, week_matchups
    from league_ai.start_sit_grade import _projection_lines

    proj = _projection_lines(season, week)
    if not proj:
        return []
    lines = _actual_lines(client, season, week) if actual else {}
    if actual and not lines:
        return []
    dvp = defense_vs_position_from_stats([r for r in stats_rows if int(r.get("week") or 0) < week])
    games = week_matchups(games_all, week)
    out = []
    for pid, line in proj.items():
        pos = str(line.get("pos") or "").upper()
        raw = score(line.get("stats"), dict(scoring), pos)
        if raw <= 0:
            continue
        game = games.get(team_code(line.get("team")))
        tilt = total_multiplier(pos, game, dvp.get((game["opponent"], pos)) if game else None) if pos in SKILL and game else 1.0
        row = {"id": str(pid), "pos": pos, "team": team_code(line.get("team")), "raw": raw, "shown": raw * tilt, "actual": None,
               "td_proj": _td_points(line.get("stats"), scoring)}
        if actual:
            real = lines.get(pid)
            row["actual"] = score(real.get("stats"), dict(scoring), real.get("pos") or pos) if real else 0.0
            row["td_act"] = _td_points((real or {}).get("stats"), scoring)
        out.append(row)
    return out


def _lineups(client: Any, league_id: str) -> dict[tuple[int, int], list[list[str]]]:
    rows, start = [], 0
    while True:
        page = (client.table("league_history_events").select("season, week, details").eq("league_id", league_id)
                .eq("kind", "lineup").order("event_id").range(start, start + 999).execute().data or [])
        rows += page
        if len(page) < 1000:
            break
        start += 1000
    out: dict[tuple[int, int], list[list[str]]] = defaultdict(list)
    for r in rows:
        ids = [str(s["id"]) for s in ((r.get("details") or {}).get("starters") or []) if s.get("id")]
        if ids:
            out[(int(r["season"]), int(r["week"]))].append(ids)
    return out


def _snapshots(client: Any, league_id: str) -> dict[str, dict[str, Any]]:
    """'season-week' -> {row id, projections}."""
    try:
        rows = (client.table("league_ai_experiments").select("id, question, result").eq("league_id", league_id)
                .eq("experiment", SNAPSHOT).limit(200).execute().data or [])
    except Exception:
        return {}
    return {str(r["question"]): {"id": r["id"], "projections": (r.get("result") or {}).get("projections") or {}} for r in rows}


def run_league(client: Any, league_id: str, sleeper_league_id: str, learn: bool = True) -> dict[str, Any]:
    from league_ai.nfl_data import season_games, season_stat_rows
    from pipeline.league_history.run import league_chain

    scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(sleeper_league_id) if l.get("season")}
    lineups = _lineups(client, league_id)
    snaps = _snapshots(client, league_id)
    weeks = []
    for season in sorted({s for s, _ in lineups}):
        sc = scoring.get(season) or next(iter(scoring.values()), {})
        stats_rows, games_all = season_stat_rows(client, season), season_games(client, season)
        done = sorted({int(g["week"]) for g in games_all if g.get("week") is not None}
                      - {int(g["week"]) for g in games_all if g.get("home_score") is None})
        for week in sorted(w for s, w in lineups if s == season):
            if games_all and week not in done:
                continue   # only weeks whose games are all final
            rows = week_rows(client, season, week, sc, stats_rows, games_all)
            if rows:
                weeks.append({"season": season, "week": week, "rows": rows, "lineups": lineups[(season, week)],
                              "snapshot": (snaps.get(f"{season}-{week}") or {}).get("projections")})
    out = replay(weeks)
    if out and learn:
        try:
            out.update(_learn(client, weeks, out, scoring))
        except Exception as exc:
            print(f"[projections] learning skipped: {type(exc).__name__}", flush=True)
    return out


def _learn(client: Any, weeks: list[dict[str, Any]], card_: Mapping[str, Any], scoring: Mapping[int, Any],
           now: datetime | None = None) -> dict[str, Any]:
    """The projection learning loop (league_ai.projection_learning) on the replayed weeks, plus this week's nudges."""
    from league_ai import projection_learning as pl
    from league_ai.nfl_data import add_weather, current_week_from_games, season_games, season_stat_rows, week_matchups

    data: dict[int, Any] = {}

    def data_for(season: int) -> Any:
        if season not in data:
            data[season] = pl.season_data(client, season)
        return data[season]

    pl.attach(weeks, data_for)
    learned = pl.learn(weeks)
    season = int(card_["season"])
    names = dict(data_for(season)["names"])
    out: dict[str, Any] = {"learning": pl.card_view(learned, names), "learning_k": learned.get("k")}
    # this week's nudges, from the signals that are on
    now = now or datetime.now(timezone.utc)
    cur_season = now.year if now.month >= 3 else now.year - 1
    games_all = season_games(client, cur_season)
    week = current_week_from_games(games_all)
    if week and learned.get("active"):
        sc = scoring.get(cur_season) or next(iter(scoring.values()), {})
        rows = week_rows(client, cur_season, week, sc, season_stat_rows(client, cur_season), games_all, actual=False)
        for r in rows:
            r["cal"] = calibrate(r["shown"], r["pos"], card_.get("factors") or {}, card_.get("lines"))
        try:
            games = add_weather(week_matchups(games_all, week), cur_season, week)
        except Exception:
            games = week_matchups(games_all, week)
        ctx, _ = pl.context(data_for(cur_season), cur_season, week, games)
        out["adjust"] = {"season": cur_season, "week": week, "players": pl.adjustments(rows, ctx, learned)}
    return out


def snapshot(client: Any, league_id: str, sleeper_league_id: str, now: datetime) -> int:
    """Save what the app shows this week for every player whose game hasn't kicked off yet (merged into the week's row,
    so each player keeps his last pregame number). Returns players saved."""
    from api.home import kickoffs
    from api.projections import week_lines, week_projections
    from league_ai.nfl_data import current_week_from_games, season_games, team_code

    season = now.year if now.month >= 3 else now.year - 1
    games_all = season_games(client, season)
    week = current_week_from_games(games_all)
    if not week:
        return 0
    starts = kickoffs([g for g in games_all if int(g.get("week") or 0) == week])
    lines = week_lines("projections", season, week, 600)
    ids = [pid for pid, l in lines.items() if starts.get(team_code(l.get("team")), now) > now]
    if not ids:
        return 0
    shown = week_projections(client, str(sleeper_league_id), season, week, ids, {p: lines[p].get("pos") or "" for p in ids},
                             {p: lines[p].get("team") or "" for p in ids})
    shown = {p: v for p, v in shown.items() if v >= MIN_PROJ}
    key = f"{season}-{week}"
    prev = _snapshots(client, league_id).get(key)
    merged = {**((prev or {}).get("projections") or {}), **shown}
    result = {"season": season, "week": week, "projections": merged, "updated_at": now.isoformat()}
    if prev:
        client.table("league_ai_experiments").update({"result": result}).eq("id", prev["id"]).execute()
    else:
        client.table("league_ai_experiments").insert({"league_id": league_id, "user_id": SYSTEM_USER, "experiment": SNAPSHOT,
                                                      "question": key, "result": result, "cost_usd": 0}).execute()
    return len(shown)


def run(client: Any, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Every league: snapshot this week's projections, replay every finished week, save factors + card. Counts only."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {}
    for lg in client.table("leagues").select("id, sleeper_league_id").execute().data or []:
        sid = str(lg.get("sleeper_league_id") or "")
        if not sid:
            continue
        lid = str(lg["id"])
        res: dict[str, Any] = {}
        if not dry_run:
            try:
                res["snapshot"] = snapshot(client, lid, sid, now)
            except Exception as exc:
                res["snapshot_error"] = type(exc).__name__
        card_ = run_league(client, lid, sid)
        if card_:
            lr = card_.get("learning") or {}
            res.update({"signals_on": lr.get("active"), "gaps": [g["cause"] for g in lr.get("gaps") or []],
                        "causes": {c["cause"]: c["misses"] for c in lr.get("causes") or []},
                        "signal_gains": {x["signal"]: [x["player_gain"], x["team_gain"]] for x in lr.get("signals") or []},
                        "adjusted_players": len(((card_.get("adjust") or {}).get("players")) or {})})
            res.update({"season": card_["season"], "through_week": card_["through_week"], "factors": card_["factors"], "lines": card_.get("lines"),
                        "overall": {k: (v or {}).get("mae") for k, v in card_["overall"].items()}})
            if not dry_run:
                client.table("league_ai_experiments").insert({"league_id": lid, "user_id": SYSTEM_USER, "experiment": EXPERIMENT,
                                                              "question": "weekly projection calibration",
                                                              "result": {**card_, "computed_at": now.isoformat()}, "cost_usd": 0}).execute()
        out[lid] = res
    return out


def main() -> int:
    import argparse
    import os

    from supabase import create_client

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for n, (_, res) in enumerate(run(client, dry_run=args.dry_run).items(), start=1):
        print(f"league {n}: {res}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
