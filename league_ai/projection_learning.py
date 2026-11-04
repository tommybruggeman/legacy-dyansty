"""Projections that look for what they're missing. No model call.

Runs right after the daily calibration (league_ai.projection_calibration), on the same finished weeks:

1. Misses: every finished week, the projections that missed by a lot (8+ points and half the projection).
2. Causes: each miss is tagged from free data we already load (nfl_player_stats snaps and targets, nfl_games scores
   and Vegas lines, nflverse injury reports): he didn't play (listed on the injury report or a late scratch), left the
   game early, his role changed, a touchdown swing, a blowout, his offense flopped or boomed against its Vegas total,
   bad wind, a different QB.
3. Signal pool: each candidate signal is a small learned nudge on the calibrated projection (multiplier 1 + k x).
   k is fit only on weeks already played, and every signal is replayed walk-forward over all the history. A signal
   switches on only while it makes both players and team totals more accurate over the recent window, and switches
   off again when it stops helping. The daily job saves this week's nudges per player; the app, website and Front
   Office apply them in api.projections.week_projections.
4. Gaps: causes that keep costing points and that no live signal covers are listed on the report card (for example
   late inactives), so we know which feed to add next. In-game injuries and plain variance are counted as luck, not gaps.

Saved in the calibration card (league_ai_experiments, 'projection_calibration_v1'): signals, causes, gaps, last
week's biggest misses, and this week's adjustments. Prints numbers only (the repo and its Actions logs are public).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping

MIN_PROJ = 3.0
MISS_MIN = 8.0          # a miss: off by 8+ points ...
MISS_SHARE = 0.5        # ... and by half the projection or more
MISS_MIN_PROJ = 5.0     # only players who were a real option
RIDGE = 400.0           # shrinks each signal's k toward 0 until enough evidence piles up
MULT_RANGE = (0.6, 1.4)
WARMUP_WEEKS = 4        # history weeks before a signal is graded at all
WINDOW_WEEKS = 30       # a signal must help over the most recent graded weeks to be on
MIN_PLAYER_GAIN = 0.003  # points of player MAE
OUT = {"out", "doubtful"}

SKILL = ("QB", "RB", "WR", "TE")


def _f(v: Any) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ---- the signal pool -----------------------------------------------------------------------------------------
# Each: label, positions, x(row, ctx) -> number (0 = no nudge). ctx is that week's pre-game context (see context()).

def _vegas(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    imp, lg = c["implied"].get(r["team"]), c.get("lg_implied")
    return imp / lg - 1 if imp and lg else 0.0


def _questionable(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    return 1.0 if c["status"].get(r["id"]) == "questionable" else 0.0


def _snap_trend(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    g = c["snaps"].get(r["id"]) or []
    if len(g) < 3:
        return 0.0
    avg = sum(g) / len(g)
    return _clamp((g[-1] + g[-2]) / 2 / avg - 1, -0.5, 0.5) if avg > 0 else 0.0


def _teammate_out(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    if c["status"].get(r["id"]) in OUT:
        return 0.0
    return min(0.5, c["freed_share"].get(r["team"], 0.0))


def _backup_qb(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    return 1.0 if r["team"] in c["qb_out"] else 0.0


def _script(sign: float) -> Callable[[Mapping[str, Any], Mapping[str, Any]], float]:
    return lambda r, c: sign * c["spread"].get(r["team"], 0.0) / 7.0


def _rz_vs_proj(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    """Touchdown points his red zone role says (carries inside the 10, targets inside the 20), against the
    touchdown points in his projection, as a share of the projection."""
    x = (c.get("rz_xtd") or {}).get(r["id"])
    if x is None:
        return 0.0
    return _clamp((6.0 * x - _f(r.get("td_proj"))) / max(_f(r.get("cal")), MIN_PROJ), -0.5, 0.5)


def _rz_role(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    """His red zone role against the position's average."""
    x, avg = (c.get("rz_xtd") or {}).get(r["id"]), (c.get("rz_avg") or {}).get(r["pos"])
    return _clamp(x / avg - 1, -0.6, 1.0) if x is not None and avg else 0.0


def _wind(r: Mapping[str, Any], c: Mapping[str, Any]) -> float:
    return max(0.0, c["wind"].get(r["team"], 0.0) - 12.0) / 10.0


SIGNALS: dict[str, tuple[str, tuple[str, ...], Callable[[Mapping[str, Any], Mapping[str, Any]], float]]] = {
    "vegas_total": ("Vegas team total", SKILL + ("K",), _vegas),
    "questionable": ("listed questionable", SKILL, _questionable),
    "snap_trend": ("snap share trend", ("RB", "WR", "TE"), _snap_trend),
    "teammate_out": ("targets freed by teammates out", ("RB", "WR", "TE"), _teammate_out),
    "backup_qb": ("backup QB starting", ("WR", "TE"), _backup_qb),
    "script_run": ("favored, runs more", ("RB",), _script(1.0)),
    "script_pass": ("trailing, throws more", ("QB", "WR", "TE"), _script(-1.0)),
    "wind": ("wind", ("QB", "WR", "TE", "K"), _wind),
    "rz_vs_proj": ("red zone role vs projected TDs", ("RB", "WR", "TE"), _rz_vs_proj),
    "rz_role": ("red zone role", ("RB", "WR", "TE"), _rz_role),
}

# cause -> (label, the signals that cover it; None = luck, never a gap)
CAUSES: dict[str, tuple[str, tuple[str, ...] | None]] = {
    "listed_injured": ("didn't play, was on the injury report", ("questionable",)),
    "late_scratch": ("late scratch (not on the injury report)", ()),
    "left_early": ("left the game early", None),
    "role_change": ("snap share changed", ("snap_trend", "teammate_out")),
    "td_swing": ("touchdowns (red zone role)", ("rz_vs_proj", "rz_role")),
    "blowout": ("blowout game script", ("script_run", "script_pass")),
    "offense": ("offense far off its Vegas total", ("vegas_total",)),
    "weather": ("wind", ("wind",)),
    "qb_change": ("different QB", ("backup_qb",)),
    "unexplained": ("no clear cause (variance)", None),
}


def signal_x(r: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, float]:
    out = {}
    for name, (_, positions, fn) in SIGNALS.items():
        if r["pos"] in positions:
            try:
                x = float(fn(r, ctx))
            except Exception:
                x = 0.0
            if x:
                out[name] = x
    return out


def nudge(cal: float, xs: Mapping[str, float], ks: Mapping[str, float], active: Iterable[str]) -> float:
    m = 1.0 + sum(ks.get(s, 0.0) * xs.get(s, 0.0) for s in active)
    return cal * _clamp(m, *MULT_RANGE)


# ---- causes --------------------------------------------------------------------------------------------------

def causes(r: Mapping[str, Any], ctx: Mapping[str, Any], post: Mapping[str, Any], shown: float) -> list[str]:
    """Why one projection missed (several can apply). post is the week's after-the-fact facts (see context())."""
    miss = r["actual"] - shown
    pid, team = r["id"], r["team"]
    pct = post["pct"].get(pid)
    prior = ctx["snaps"].get(pid) or []
    avg = sum(prior) / len(prior) if prior else None
    tags: list[str] = []
    if not pct and r["actual"] <= 1.0:
        return ["listed_injured" if ctx["status"].get(pid) in OUT | {"questionable"} else "late_scratch"]
    if pct and avg and avg >= 0.4 and pct < 0.6 * avg:
        tags.append("left_early")
    elif pct is not None and avg is not None and abs(pct - avg) >= 0.15:
        tags.append("role_change")
    if abs(_f(r.get("td_act")) - _f(r.get("td_proj"))) >= 0.5 * abs(miss):
        tags.append("td_swing")
    if abs(post["margin"].get(team, 0.0)) >= 17:
        tags.append("blowout")
    imp, pts = ctx["implied"].get(team), post["points"].get(team)
    if imp is not None and pts is not None and abs(pts - imp) >= 10:
        tags.append("offense")
    if post["wind"].get(team, 0.0) >= 18:
        tags.append("weather")
    if team in post["qb_changed"] and r["pos"] in ("QB", "WR", "TE"):
        tags.append("qb_change")
    return tags or ["unexplained"]


def is_miss(r: Mapping[str, Any], shown: float) -> bool:
    err = abs(r["actual"] - shown)
    return shown >= MISS_MIN_PROJ and err >= MISS_MIN and err >= MISS_SHARE * shown


# ---- the walk-forward replay ---------------------------------------------------------------------------------

def learn(weeks: list[Mapping[str, Any]]) -> dict[str, Any]:
    """weeks, oldest first, as replayed by projection_calibration (rows carry 'cal'), each with 'ctx' and 'post'.

    Returns the signals (k, gains, on/off), causes and gaps for the latest season, and last week's biggest misses."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])   # signal -> [sum c x r, sum (c x)^2]
    errs: dict[str, list[tuple[int, float, float]]] = defaultdict(list)   # signal -> (week index, player |err| gain, ...)
    team_gain: dict[str, list[tuple[int, float]]] = defaultdict(list)
    active: list[str] = []
    history: list[dict[str, Any]] = []   # per week: active set used, combined gains
    season = weeks[-1]["season"] if weeks else None
    cause_pts: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])   # this season: cause -> [misses, points]
    last_misses: list[dict[str, Any]] = []
    combined: list[tuple[int, float, float]] = []
    graded = 0
    for i, wk in enumerate(weeks):
        ctx, post = wk.get("ctx"), wk.get("post")
        rows = [r for r in wk["rows"] if r.get("actual") is not None and r.get("cal") is not None]
        if not ctx or not post or not rows:
            continue
        ks = {s: sums[s][0] / (sums[s][1] + RIDGE) for s in SIGNALS}
        xs = {r["id"]: signal_x(r, ctx) for r in rows}
        by_id = {r["id"]: r for r in rows}
        if graded >= WARMUP_WEEKS:
            for s in SIGNALS:
                gain = 0.0
                for r in rows:
                    if r["raw"] >= MIN_PROJ and s in xs[r["id"]]:
                        gain += abs(r["actual"] - r["cal"]) - abs(r["actual"] - nudge(r["cal"], xs[r["id"]], ks, [s]))
                n = sum(1 for r in rows if r["raw"] >= MIN_PROJ)
                errs[s].append((i, gain / max(n, 1), 0.0))
                tg = 0.0
                for ids in wk.get("lineups") or []:
                    got = [by_id[x] for x in ids if x in by_id]
                    if got:
                        real = sum(r["actual"] for r in got)
                        tg += abs(real - sum(r["cal"] for r in got)) - abs(real - sum(nudge(r["cal"], xs[r["id"]], ks, [s]) for r in got))
                team_gain[s].append((i, tg / max(1, len(wk.get("lineups") or []))))
            # what was live this week: the set chosen from the weeks before
            pg = sum(abs(r["actual"] - r["cal"]) - abs(r["actual"] - nudge(r["cal"], xs[r["id"]], ks, active)) for r in rows if r["raw"] >= MIN_PROJ)
            combined.append((i, pg / max(1, sum(1 for r in rows if r["raw"] >= MIN_PROJ)), 0.0))
        # misses and their causes (against what was shown: calibrated plus the live nudges)
        if wk["season"] == season:
            week_misses = []
            for r in rows:
                shown = nudge(r["cal"], xs[r["id"]], ks, active)
                if is_miss(r, shown):
                    tags = causes(r, ctx, post, shown)
                    for t in tags:
                        cause_pts[t][0] += 1
                        cause_pts[t][1] += abs(r["actual"] - shown) / len(tags)
                    week_misses.append({"id": r["id"], "pos": r["pos"], "week": wk["week"], "projected": round(shown, 1),
                                        "actual": round(r["actual"], 1), "causes": tags})
            if week_misses:
                week_misses.sort(key=lambda m: -abs(m["actual"] - m["projected"]))
                last_misses = week_misses[:8]
        # learn from this week only after it's graded
        for r in rows:
            if r["raw"] < MIN_PROJ:
                continue
            res = r["actual"] - r["cal"]
            for s, x in xs[r["id"]].items():
                cx = r["cal"] * x
                sums[s][0] += cx * res
                sums[s][1] += cx * cx
        graded += 1
        active = choose(errs, team_gain, i)
        history.append({"season": wk["season"], "week": wk["week"], "active": list(active)})
    ks = {s: round(sums[s][0] / (sums[s][1] + RIDGE), 4) for s in SIGNALS}
    signals = []
    for s, (label, positions, _) in SIGNALS.items():
        recent = [g for _, g, _ in errs[s][-WINDOW_WEEKS:]]
        recent_t = [g for _, g in team_gain[s][-WINDOW_WEEKS:]]
        signals.append({"signal": s, "label": label, "positions": list(positions), "k": ks[s], "on": s in active,
                        "player_gain": round(sum(recent) / len(recent), 4) if recent else None,
                        "team_gain": round(sum(recent_t) / len(recent_t), 3) if recent_t else None, "weeks": len(recent)})
    covered = set(active)
    cause_rows, gaps = [], []
    for c, (n, pts) in sorted(cause_pts.items(), key=lambda kv: -kv[1][1]):
        label, cover = CAUSES[c]
        status = "luck" if cover is None else ("covered" if covered & set(cover) else ("tested, not helping yet" if cover else "no signal yet"))
        row = {"cause": c, "label": label, "misses": int(n), "points": round(pts, 1), "status": status}
        cause_rows.append(row)
        if cover is not None and not covered & set(cover):
            gaps.append(row)
    recent_c = [g for _, g, _ in combined[-WINDOW_WEEKS:]]
    gaps.sort(key=lambda g: (g["status"] != "no signal yet", -g["points"]))   # missing data first, then signals that don't help yet
    return {"signals": signals, "active": list(active), "k": ks, "causes": cause_rows, "gaps": gaps[:3],
            "last_misses": last_misses, "live_gain": round(sum(recent_c) / len(recent_c), 4) if recent_c else None,
            "history": history[-20:]}


def choose(errs: Mapping[str, list[tuple[int, float, float]]], team_gain: Mapping[str, list[tuple[int, float]]], i: int) -> list[str]:
    """Signals that helped both players and team totals over the recent window."""
    on = []
    for s in SIGNALS:
        p = [g for _, g, _ in errs.get(s, [])[-WINDOW_WEEKS:]]
        t = [g for _, g in team_gain.get(s, [])[-WINDOW_WEEKS:]]
        if len(p) >= 4 and sum(p) / len(p) > MIN_PLAYER_GAIN and t and sum(t) / len(t) > 0:
            on.append(s)
    return on


def adjustments(rows: Iterable[Mapping[str, Any]], ctx: Mapping[str, Any], learned: Mapping[str, Any]) -> dict[str, float]:
    """This week's multiplier per player from the live signals (only the ones that aren't 1)."""
    active, ks = learned.get("active") or [], learned.get("k") or {}
    out = {}
    for r in rows:
        xs = signal_x(r, ctx)
        m = _clamp(1.0 + sum(ks.get(s, 0.0) * xs.get(s, 0.0) for s in active), *MULT_RANGE)
        if abs(m - 1) >= 0.005:
            out[str(r["id"])] = round(m, 3)
    return out


# ---- building the context from data (learning job) ------------------------------------------------------------

STAT_COLUMNS = "gsis_id, sleeper_id, player_name, week, team, position, offense_pct, attempts, targets, target_share"


def season_data(client: Any, season: int, get_csv: Callable[[str], list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    from league_ai.nfl_data import season_games, team_code

    stats: list[dict[str, Any]] = []
    try:
        for start in range(0, 30000, 1000):
            chunk = (client.table("nfl_player_stats").select(STAT_COLUMNS).eq("season", season).eq("season_type", "REG")
                     .order("gsis_id").order("week").range(start, start + 999).execute().data or [])
            stats += chunk
            if len(chunk) < 1000:
                break
    except Exception:
        stats = []
    for r in stats:
        r["team"] = team_code(r.get("team"))
    if get_csv is None:
        from league_ai.start_sit_grade import INJURIES_URL, _nflverse_csv
        injuries = _nflverse_csv(INJURIES_URL.format(season=season))
    else:
        injuries = get_csv(str(season))
    plays: list[dict[str, Any]] = []
    if get_csv is None:   # free nflverse play-by-play, only the columns the red zone role needs
        from league_ai.start_sit_grade import PBP_COLUMNS, PBP_URL, _nflverse_csv
        plays = [p for p in _nflverse_csv(PBP_URL.format(season=season), PBP_COLUMNS, gz=True) if p.get("season_type") == "REG"]
    return {"stats": stats, "games": season_games(client, season), "injuries": injuries, "plays": plays,
            "names": {str(r["sleeper_id"]): r.get("player_name") for r in stats if r.get("sleeper_id")}}


def context(data: Mapping[str, Any], season: int, week: int, games: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(ctx, post) for one week. ctx uses only what was known before kickoff (games before the week, that week's
    injury report, Vegas lines); post is what happened (that week's snaps, scores, wind, QB)."""
    from league_ai.nfl_data import team_code, week_matchups

    stats = data["stats"]
    g2s = {str(r["gsis_id"]): str(r["sleeper_id"]) for r in stats if r.get("sleeper_id") and r.get("gsis_id")}
    prior = [r for r in stats if int(_f(r.get("week"))) < week]
    this = [r for r in stats if int(_f(r.get("week"))) == week]
    games = games if games is not None else week_matchups(data["games"], week)
    implied = {t: float(g["implied_points"]) for t, g in games.items() if g.get("implied_points") is not None}
    spread = {t: implied[t] - implied[g["opponent"]] for t, g in games.items() if t in implied and g.get("opponent") in implied}
    wind = {t: _f(((g.get("weather") or {}).get("wind_mph"))) for t, g in games.items() if g.get("roof") not in ("dome", "closed")}
    status: dict[str, str] = {}
    for r in data["injuries"]:
        if int(_f(r.get("season"))) == season and int(_f(r.get("week"))) == week and r.get("gsis_id"):
            sid = g2s.get(str(r["gsis_id"]))
            if sid and r.get("report_status"):
                status[sid] = str(r["report_status"]).strip().lower()
    snaps: dict[str, list[float]] = defaultdict(list)
    share: dict[str, list[float]] = defaultdict(list)
    team_of: dict[str, str] = {}
    att: dict[tuple[str, str], float] = defaultdict(float)
    for r in sorted(prior, key=lambda r: int(_f(r.get("week")))):
        sid = str(r.get("sleeper_id") or "")
        if not sid:
            continue
        if r.get("offense_pct") is not None:
            snaps[sid].append(_f(r["offense_pct"]))
        if r.get("target_share") is not None:
            share[sid].append(_f(r["target_share"]))
        team_of[sid] = r.get("team") or team_of.get(sid, "")
        if str(r.get("position") or "").upper() == "QB":
            att[(r.get("team") or "", sid)] += _f(r.get("attempts"))
    main_qb: dict[str, tuple[float, str]] = {}
    for (team, sid), a in att.items():
        if a > main_qb.get(team, (-1.0, ""))[0]:
            main_qb[team] = (a, sid)
    freed: dict[str, float] = defaultdict(float)
    for sid, st in status.items():
        if st in OUT and len(share.get(sid) or []) >= 2:
            freed[team_of.get(sid, "")] += sum(share[sid]) / len(share[sid])
    qb_out = {t for t, (_, sid) in main_qb.items() if status.get(sid) in OUT}
    rz_xtd: dict[str, float] = {}
    rz_avg: dict[str, float] = {}
    if data.get("plays"):
        from league_ai.missing_signals import RedZone

        rz = RedZone([p for p in data["plays"] if int(_f(p.get("week"))) < week])
        pos_of = {str(r["gsis_id"]): str(r.get("position") or "").upper() for r in prior if r.get("gsis_id")}
        by_pos: dict[str, list[float]] = defaultdict(list)
        for g, x in rz.xtd.items():
            by_pos[pos_of.get(g, "")].append(x)
        rz_avg = {p: sum(v) / len(v) for p, v in by_pos.items() if v}
        for g in rz.xtd:
            sid = g2s.get(g)
            if sid and pos_of.get(g) in rz_avg:
                x = rz.expected_tds(g, rz_avg[pos_of[g]])
                if x is not None:
                    rz_xtd[sid] = x
    implied_vals = list(implied.values())
    ctx = {"implied": implied, "lg_implied": sum(implied_vals) / len(implied_vals) if implied_vals else None, "spread": spread,
           "wind": wind, "status": status, "snaps": dict(snaps), "freed_share": dict(freed), "qb_out": qb_out,
           "rz_xtd": rz_xtd, "rz_avg": rz_avg}
    pct = {str(r["sleeper_id"]): _f(r.get("offense_pct")) for r in this if r.get("sleeper_id")}
    week_att: dict[str, tuple[float, str]] = {}
    for r in this:
        if str(r.get("position") or "").upper() == "QB" and r.get("sleeper_id"):
            a = _f(r.get("attempts"))
            if a > week_att.get(r.get("team") or "", (-1.0, ""))[0]:
                week_att[r.get("team") or ""] = (a, str(r["sleeper_id"]))
    qb_changed = {t for t, (_, sid) in week_att.items() if t in main_qb and main_qb[t][1] != sid}
    points, margin = {}, {}
    for t, g in games.items():
        fs = g.get("final_score")
        if fs:
            points[t], margin[t] = float(fs["team"]), float(fs["team"]) - float(fs["opponent"])
    post = {"pct": pct, "points": points, "margin": margin, "wind": wind, "qb_changed": qb_changed}
    return ctx, post


def attach(weeks: list[dict[str, Any]], data_for: Callable[[int], Mapping[str, Any]]) -> None:
    """Adds ctx/post to each replayed week (in place)."""
    for wk in weeks:
        try:
            wk["ctx"], wk["post"] = context(data_for(wk["season"]), wk["season"], wk["week"])
        except Exception:
            wk["ctx"], wk["post"] = None, None


def card_view(learned: Mapping[str, Any], names: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What the report card shows (names filled in for last week's misses; the card lives in the private database)."""
    names = names or {}
    misses = [{**m, "player": names.get(m["id"]) or m["pos"], "why": [CAUSES[c][0] for c in m["causes"]]} for m in learned.get("last_misses") or []]
    return {"signals": learned.get("signals") or [], "active": learned.get("active") or [], "causes": learned.get("causes") or [],
            "gaps": learned.get("gaps") or [], "last_misses": misses, "live_gain": learned.get("live_gain")}
