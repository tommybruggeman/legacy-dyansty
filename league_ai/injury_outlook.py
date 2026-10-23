"""Injury returns, chance to play and position-room roles: the math behind the return and role tools.

Pure functions over rows the tools read (nfl_injury_episodes, nfl_injury_play_rates, nfl_player_stats,
nfl_players, nfl_injuries), so they are testable without a database.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date
from statistics import mean, median
from typing import Any, Iterable, Mapping

from league_ai.nfl_data import team_code

RELEVANT_SNAP_PCT, RELEVANT_OPPS = 0.3, 6  # a comp has to have had a real role before he got hurt
MIN_COMPS = 6


def _f(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except Exception:
        return None


def _pct(x: float | None) -> str | None:
    return None if x is None else f"{round(100 * x)}%"


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def injury_key(text: Any) -> str | None:
    """Same normalisation the nightly job uses: 'Right Knee (ACL)' -> 'knee'."""
    from pipeline.nfl_data.injury_history import injury_key as key

    return key(re.sub(r"\(.*?\)", "", str(text or "")))


# ---- typical time missed and return comps ------------------------------------------

def _relevant(e: Mapping[str, Any]) -> bool:
    return (_f(e.get("pre_snap_pct")) or 0) >= RELEVANT_SNAP_PCT or (_f(e.get("pre_opps")) or 0) >= RELEVANT_OPPS


def typical_absence(episodes: Iterable[Mapping[str, Any]], position: str | None, injury: str | None) -> dict[str, Any] | None:
    """Games missed by players with a real role and this injury: median and middle half, at his position when the sample allows."""
    eps = [e for e in episodes if e.get("injury") == injury and e.get("returned") and _relevant(e)]
    pos = str(position or "").upper()
    at_pos = [e for e in eps if str(e.get("position") or "").upper() == pos]
    pool, scope = (at_pos, f"{pos}s") if len(at_pos) >= MIN_COMPS else (eps, "skill players")
    if not pool:
        return None
    games = [int(e["games_missed"]) for e in pool]
    return {"injury": injury, "scope": scope, "absences": len(pool), "median_games_missed": round(median(games), 1),
            "middle_half": f"{_quantile(games, 0.25):.0f}-{_quantile(games, 0.75):.0f} games", "p25": _quantile(games, 0.25), "p75": _quantile(games, 0.75),
            "missed_1_game_or_less": _pct(sum(1 for g in games if g <= 1) / len(games)), "missed_4_plus": _pct(sum(1 for g in games if g >= 4) / len(games))}


def return_comps(episodes: Iterable[Mapping[str, Any]], position: str | None, injury: str | None, games_missed: int | None, exclude_gsis: str | None = None) -> dict[str, Any] | None:
    """How players with the same injury and a similar layoff produced on return, relative to their own pre-injury level.

    Similar layoff: within half the games missed either way (at least 2). Falls back to every skill position, then to any
    layoff length, when the position sample is thin. Ratios are each comp's own post / pre, so stars and role players compare.
    """
    eps = [e for e in episodes if e.get("injury") == injury and e.get("returned") and _relevant(e) and e.get("gsis_id") != exclude_gsis
           and (_f(e.get("pre_ppr")) or 0) > 0]
    pos = str(position or "").upper()
    if games_missed:
        band = max(2, round(games_missed * 0.5))
        similar = [e for e in eps if abs(int(e["games_missed"]) - games_missed) <= band]
    else:
        similar = eps
    for pool, scope in ((([e for e in similar if str(e.get("position") or "").upper() == pos]), f"{pos}s, similar layoff"),
                        (similar, "all skill positions, similar layoff"), (eps, "all skill positions, any layoff")):
        if len(pool) >= MIN_COMPS or (pool and pool is eps):
            break
    if not pool:
        return None

    def ratios(post_key: str, pre_key: str) -> list[float]:
        out = []
        for e in pool:
            post, pre = _f(e.get(post_key)), _f(e.get(pre_key))
            if post is not None and pre:
                out.append(post / pre)
        return out

    def summary(xs: list[float]) -> dict[str, Any] | None:
        if not xs:
            return None
        return {"median": round(median(xs), 2), "range_middle_half": f"{_quantile(xs, 0.25):.2f}-{_quantile(xs, 0.75):.2f}"}

    examples = sorted(pool, key=lambda e: (-(int(e.get("return_season") or 0)), -(_f(e.get("pre_ppr")) or 0)))[:4]
    return {
        "scope": scope, "comps": len(pool),
        "first_game_vs_before": {"ppr": summary(ratios("post1_ppr", "pre_ppr")), "snap_share": summary(ratios("post1_snap_pct", "pre_snap_pct")),
                                 "opportunities": summary(ratios("post1_opps", "pre_opps"))},
        "first_3_games_vs_before": {"ppr": summary(ratios("post3_ppr", "pre_ppr")), "snap_share": summary(ratios("post3_snap_pct", "pre_snap_pct"))},
        "back_to_80pct_of_old_ppr_by_game_3": _pct(sum(1 for r in ratios("post3_ppr", "pre_ppr") if r >= 0.8) / max(1, len(ratios("post3_ppr", "pre_ppr")))),
        "examples": [f"{e.get('player_name')} ({e.get('position')}, {e.get('return_season')}): missed {e.get('games_missed')}, "
                     f"{_f(e.get('pre_ppr')) or 0:.1f} PPR/g before -> {_f(e.get('post1_ppr')) or 0:.1f} in game 1, {_f(e.get('post3_ppr')) or 0:.1f}/g over 3" for e in examples],
        "how_to_read": "ratios are post-return / his own pre-injury level: 0.80 = 80% of what he was doing before",
    }


# ---- chance to play ----------------------------------------------------------------------

POSITIVE = re.compile(r"\b(expected to (play|suit up|return|be active)|will (play|suit up|return|be active)|on track|cleared|full(y)? (practice|participat)|"
                      r"activated|designated (him |\w+ )?(to|for) return|returned to practice|no limitations|good to go|set to return|"
                      r"plans? to play|should (play|be (ready|available|good))|ready to (go|play)|off the injury report|removed .*injury report)", re.I)
NEGATIVE = re.compile(r"\b(ruled out|will (not|n't) play|won't (play|suit up)|placed (him )?on (ir|injured reserve)|setback|week-to-week|"
                      r"multiple weeks|several weeks|out (indefinitely|for the season|until)|season-ending|surgery|not expected to play|"
                      r"unlikely to play|doubtful|did not practice|didn't practice|sidelined)", re.I)
UNCERTAIN = re.compile(r"\b(game-time decision|uncertain|day-to-day|questionable|limited (practice|participant|session)|hopeful|a chance)", re.I)
COACH = re.compile(r"[^.]*\b(coach|head coach|coordinator|HC|told reporters|said|says|according to)\b[^.]*\.", re.I)


def comment_signal(*texts: Any) -> dict[str, Any] | None:
    """Lean of the latest news blurbs (ESPN carries the beat-writer and coach quotes) plus the quote itself when there is one."""
    text = " ".join(str(t) for t in texts if t)
    if not text.strip():
        return None
    pos, neg, unc = len(POSITIVE.findall(text)), len(NEGATIVE.findall(text)), len(UNCERTAIN.findall(text))
    lean = "positive" if pos > neg else "negative" if neg > pos else ("uncertain" if unc or pos else None)
    quote = COACH.search(text)
    return {"lean": lean, "coach_or_beat_quote": quote.group(0).strip()[:220] if quote else None}


STATUS_ALIASES = {"out": "Out", "doubtful": "Doubtful", "questionable": "Questionable", "ir": "IR", "injured reserve": "IR", "pup": "PUP",
                  "physically unable to perform": "PUP", "nfi": "IR", "sus": "Suspended", "suspended": "Suspended", "day-to-day": "Questionable", "dtd": "Questionable"}
PRACTICE_ALIASES = {"dnp": "DNP", "did not participate in practice": "DNP", "out": "DNP", "limited": "Limited", "limited participation in practice": "Limited",
                    "lp": "Limited", "full": "Full", "fp": "Full", "full participation in practice": "Full"}
FALLBACK_RATES = {"Out": 0.01, "Doubtful": 0.05, "Questionable": 0.7, "": 0.95}  # used only before the nightly play-rate table exists
FALLBACK_NEXT = {"Out": 0.45, "Doubtful": 0.6, "Questionable": 0.85, "": 0.95}


def norm_status(v: Any) -> str:
    return STATUS_ALIASES.get(str(v or "").strip().lower(), "")


def norm_practice(v: Any) -> str:
    return PRACTICE_ALIASES.get(str(v or "").strip().lower(), "")


def _rate(rates: Mapping[tuple[str, str, str], Mapping[str, Any]], scopes: list[str], status: str, practice: str, prefix: str = "") -> tuple[float, int, str] | None:
    for scope in scopes:
        for prac in ([practice, ""] if practice else [""]):
            row = rates.get((prefix + scope, status, prac))
            if row and int(row.get("listings") or 0) >= 15:
                return float(row["rate"]), int(row["listings"]), prefix + scope
    return None


def _logit(p: float) -> float:
    p = min(0.99, max(0.01, p))
    return math.log(p / (1 - p))


def play_chance(*, position: str | None, injury: str | None, official_status: str | None, official_practice: str | None,
                sleeper_status: str | None, sleeper_practice: str | None, espn_status: str | None, return_date: Any, game_date: Any,
                comments: Mapping[str, Any] | None, rates: Mapping[tuple[str, str, str], Mapping[str, Any]], absence: Mapping[str, Any] | None = None,
                games_missed_so_far: int | None = None) -> dict[str, Any] | None:
    """Chance he plays this week, from the league's own history of players with the same tag, nudged by the news.

    official_*: this week's NFL game-status report (Friday). Before it is out, the base is how often players with last
    week's tag played the following week, and practice, the ESPN return date, the news and the usual recovery time count more.
    Every input is shown in factors, so the weekly grading can check which ones were right.
    """
    pos = str(position or "").upper()
    official, sleeper, espn = norm_status(official_status), norm_status(sleeper_status), norm_status(espn_status)
    status = official or sleeper or espn
    practice = norm_practice(official_practice) or norm_practice(sleeper_practice)
    signal = (comments or {}).get("lean")
    if not status and not injury and not practice:
        return None
    scopes = [f"injury:{injury}"] if injury else []
    scopes += [f"pos:{pos}", "all"]
    factors: list[str] = []
    have_official = bool(official) or bool(official_practice)

    if status in ("IR", "PUP", "Suspended"):
        p = 0.45 if signal == "positive" else 0.05  # the positive news is added below
        factors.append(f"on {status}: plays only once activated" + ("; the news says he is set to return" if signal == "positive" else ""))
    elif have_official:
        hit = _rate(rates, scopes, status, practice)
        p = hit[0] if hit else FALLBACK_RATES.get(status, 0.95)
        tag = f"{status or 'no game status'}" + (f", {practice} practice" if practice else "")
        factors.append(f"official report this week: {tag}; " + (f"{_pct(hit[0])} of players with that tag played ({hit[1]} cases, {hit[2]})" if hit else "league-wide rule of thumb"))
    else:
        hit = _rate(rates, scopes, status, "", prefix="next:") if status else None
        p = hit[0] if hit else FALLBACK_NEXT.get(status, 0.95)
        if status:
            factors.append(f"last tag {status}, no game status for this week yet; " + (f"{_pct(hit[0])} of players with that tag played the next week ({hit[1]} cases)" if hit else "rule of thumb"))
        weight = {"Full": 0.9, "Limited": 0.25, "DNP": -0.7}.get(practice)
        if weight is not None:
            p = _nudge(p, weight)
            factors.append(f"practice this week: {practice}")

    official_out = official == "Out"
    scale = 0.5 if have_official else 1.0
    if signal in ("positive", "negative") and not official_out:
        p = _nudge(p, (1.0 if signal == "positive" else -1.2) * scale)
        factors.append(f"news leans {signal}" + (f": \"{comments['coach_or_beat_quote']}\"" if comments and comments.get("coach_or_beat_quote") else ""))
    rd, gd = _day(return_date), _day(game_date)
    if rd and gd and not official_out and status not in ("IR", "PUP"):
        if rd <= gd:
            p = _nudge(p, 0.4 * scale)
            factors.append(f"ESPN's estimated return {rd.isoformat()} is on or before kickoff ({gd.isoformat()})")
        elif (rd - gd).days > 6:
            p = _nudge(p, -0.9 * scale)
            factors.append(f"ESPN's estimated return {rd.isoformat()} is after this game")
    if absence and games_missed_so_far is not None and not have_official and status not in ("IR", "PUP"):
        if games_missed_so_far >= absence["p75"]:
            p = _nudge(p, 0.3)
            factors.append(f"has missed {games_missed_so_far} games, past the usual {absence['middle_half']} for a {injury} ({absence['absences']} past cases)")
        elif games_missed_so_far < absence["p25"]:
            p = _nudge(p, -0.4)
            factors.append(f"has missed {games_missed_so_far} games, short of the usual {absence['middle_half']} for a {injury}")
    if official_out:
        p = min(p, 0.02)
    p = round(min(0.99, max(0.01, p)), 2)
    verdict = "very likely" if p >= 0.85 else "likely" if p >= 0.65 else "coin flip" if p >= 0.4 else "unlikely" if p >= 0.15 else "very unlikely"
    return {"chance_to_play": _pct(p), "probability": p, "verdict": verdict, "factors": factors}


def _nudge(p: float, delta: float) -> float:
    return 1 / (1 + math.exp(-(_logit(p) + delta)))


def _day(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except Exception:
        return None


# ---- position room: who gets the work, and how the team uses each role ---------------------

def _opps(r: Mapping[str, Any]) -> float:
    if str(r.get("position") or "").upper() == "QB":
        return (_f(r.get("attempts")) or 0) + (_f(r.get("carries")) or 0)
    return (_f(r.get("carries")) or 0) + (_f(r.get("targets")) or 0)


def room_weeks(rows: Iterable[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """week -> the room's players that week, ranked (1 = lead) by snaps, then opportunities. Share = share of the room's opportunities."""
    by_week: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("week") is None:
            continue
        by_week[int(r["week"])].append({"key": str(r.get("gsis_id") or r.get("player_name")), "name": r.get("player_name"), "snaps": _f(r.get("offense_snaps")),
                                        "snap_pct": _f(r.get("offense_pct")), "opps": _opps(r), "carries": _f(r.get("carries")) or 0, "targets": _f(r.get("targets")) or 0,
                                        "ppr": _f(r.get("fantasy_points_ppr")) or 0.0})
    for week, players in by_week.items():
        total = sum(p["opps"] for p in players) or 1.0
        players.sort(key=lambda p: (-(p["snaps"] or 0), -p["opps"], -p["ppr"]))
        for i, p in enumerate(players, start=1):
            p["rank"], p["share"] = i, p["opps"] / total
    return dict(by_week)


def role_norms(weeks: Mapping[int, list[Mapping[str, Any]]], ranks: int = 3) -> dict[str, dict[str, Any]]:
    """'RB1'/'RB2'/'RB3' style norms: average share of the room's work, snap share, opportunities and PPR for whoever held that slot."""
    acc: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for players in weeks.values():
        for p in players:
            if p["rank"] <= ranks:
                acc[p["rank"]].append(p)
    out = {}
    for rank, ps in sorted(acc.items()):
        snaps = [p["snap_pct"] for p in ps if p.get("snap_pct") is not None]
        out[str(rank)] = {"games": len(ps), "share_of_work": round(mean(p["share"] for p in ps), 3), "snap_pct": round(mean(snaps), 3) if snaps else None,
                          "opps_pg": round(mean(p["opps"] for p in ps), 1), "ppr_pg": round(mean(p["ppr"] for p in ps), 1)}
    return out


def league_role_norms(stat_rows: Iterable[Mapping[str, Any]], position: str) -> dict[str, dict[str, Any]]:
    """Same norms across every team (season_stat_rows has no snaps, so slots are ranked by opportunities)."""
    pos = position.upper()
    groups: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for r in stat_rows:
        if str(r.get("position") or "").upper() == pos and r.get("week") is not None:
            groups[(team_code(r.get("team")), int(r["week"]))].append(r)
    weeks: dict[int, list[dict[str, Any]]] = {}
    for i, rows in enumerate(groups.values()):
        ranked = room_weeks([{**r, "week": 0, "gsis_id": str(j)} for j, r in enumerate(rows)])[0]
        weeks[i] = ranked
    norms = role_norms(weeks)
    for n in norms.values():
        n.pop("snap_pct", None)
    return norms


def room_players(weeks: Mapping[int, list[Mapping[str, Any]]], recent: int = 3) -> list[dict[str, Any]]:
    """Each player in the room: games, share of the work, snap share, PPR per game, his usual slot, and his last few weeks."""
    by_player: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for week, players in weeks.items():
        for p in players:
            by_player[p["key"]].append((week, p))
    last_weeks = sorted(weeks)[-recent:]
    out = []
    for key, entries in by_player.items():
        entries.sort()
        ps = [p for _, p in entries]
        snaps = [p["snap_pct"] for p in ps if p.get("snap_pct") is not None]
        rec = [p for w, p in entries if w in last_weeks]
        out.append({"key": key, "name": ps[-1]["name"], "games": len(ps), "weeks": [w for w, _ in entries], "usual_slot": round(median(p["rank"] for p in ps)),
                    "share_of_work": round(mean(p["share"] for p in ps), 3), "snap_pct": round(mean(snaps), 3) if snaps else None,
                    "opps_pg": round(mean(p["opps"] for p in ps), 1), "ppr_pg": round(mean(p["ppr"] for p in ps), 1),
                    "recent": {"weeks": [w for w, p in entries if w in last_weeks], "slots": [p["rank"] for p in rec],
                               "share_of_work": round(mean(p["share"] for p in rec), 3) if rec else None,
                               "snap_pct": round(mean(x for x in (p["snap_pct"] for p in rec) if x is not None), 3) if any(p["snap_pct"] is not None for p in rec) else None,
                               "opps_pg": round(mean(p["opps"] for p in rec), 1) if rec else None, "ppr_pg": round(mean(p["ppr"] for p in rec), 1) if rec else None}})
    out.sort(key=lambda p: -p["share_of_work"] * p["games"])
    return out


def with_without(weeks: Mapping[int, list[Mapping[str, Any]]], player_key: str, other_key: str) -> dict[str, Any] | None:
    """The player's share of the work in weeks the other player played vs weeks he didn't (only weeks the player played)."""
    with_, without = [], []
    for players in weeks.values():
        me = next((p for p in players if p["key"] == player_key), None)
        if not me:
            continue
        (with_ if any(p["key"] == other_key for p in players) else without).append(me)
    if not with_ or not without:
        return None
    line = lambda ps: {"games": len(ps), "share_of_work": round(mean(p["share"] for p in ps), 3), "opps_pg": round(mean(p["opps"] for p in ps), 1), "ppr_pg": round(mean(p["ppr"] for p in ps), 1)}
    return {"with": line(with_), "without": line(without)}


def compare_to_norm(share: float | None, norm: Mapping[str, Any] | None) -> str | None:
    if share is None or not norm or not norm.get("share_of_work"):
        return None
    ratio = share / norm["share_of_work"]
    word = "more than" if ratio > 1.15 else "less than" if ratio < 0.85 else "on par with"
    return f"{word} the norm ({_pct(share)} of the work vs {_pct(norm['share_of_work'])}, {ratio:.2f}x)"


# ---- team run game -------------------------------------------------------------------------

def team_backfield(stat_rows: Iterable[Mapping[str, Any]], position: str = "RB") -> dict[str, dict[str, Any]]:
    """team -> position-group volume and production per game with league ranks (1 = most): RB carries, rushing yards, opportunities, PPR."""
    pos = position.upper()
    acc: dict[str, dict[str, Any]] = {}
    for r in stat_rows:
        team = team_code(r.get("team"))
        if not team or r.get("week") is None:
            continue
        a = acc.setdefault(team, {"weeks": set(), "car": 0.0, "ryd": 0.0, "opps": 0.0, "ppr": 0.0})
        a["weeks"].add(int(r["week"]))
        if str(r.get("position") or "").upper() != pos:
            continue
        a["car"] += _f(r.get("carries")) or 0
        a["ryd"] += _f(r.get("rushing_yards")) or 0
        a["opps"] += _opps(r)
        a["ppr"] += _f(r.get("fantasy_points_ppr")) or 0
    out: dict[str, dict[str, Any]] = {}
    for team, a in acc.items():
        g = len(a["weeks"])
        if not g:
            continue
        out[team] = {"games": g, "carries_pg": round(a["car"] / g, 1), "rush_yds_pg": round(a["ryd"] / g, 1), "opps_pg": round(a["opps"] / g, 1),
                     "ppr_pg": round(a["ppr"] / g, 1), "ppr_per_opp": round(a["ppr"] / a["opps"], 2) if a["opps"] else None}
    for key in ("carries_pg", "rush_yds_pg", "ppr_pg"):
        for i, team in enumerate(sorted(out, key=lambda t: -out[t][key]), start=1):
            out[team][key + "_rank"] = i
    return out


def backfield_text(b: Mapping[str, Any] | None, position: str, teams: int = 32) -> str | None:
    if not b:
        return None
    return (f"{position}s get {b['carries_pg']} carries/g ({_ordinal(b['carries_pg_rank'])} of {teams}), {b['rush_yds_pg']:.0f} rush yds/g ({_ordinal(b['rush_yds_pg_rank'])}), "
            f"{b['ppr_pg']} PPR/g as a group ({_ordinal(b['ppr_pg_rank'])}), {b['ppr_per_opp']} PPR per opportunity over {b['games']} games")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def project_return(pre: Mapping[str, Any], comps: Mapping[str, Any] | None, team_now: Mapping[str, Any] | None, team_then: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """First game back and the following games, in PPR: his old workload x how comps' workload came back x how much
    work this offense gives the position now vs when he last played, valued at a blend of his own and this offense's PPR per opportunity."""
    pre_opps, pre_ppr = _f(pre.get("pre_opps")), _f(pre.get("pre_ppr"))
    if not pre_opps or pre_ppr is None:
        return None
    volume = 1.0
    if team_now and team_then and team_then.get("opps_pg") and team_now is not team_then:
        volume = max(0.7, min(1.3, team_now["opps_pg"] / team_then["opps_pg"]))
    own_eff = pre_ppr / pre_opps
    eff = (own_eff + team_now["ppr_per_opp"]) / 2 if team_now and team_now.get("ppr_per_opp") else own_eff

    def ratio(block: str, key: str, q: str) -> float:
        s = ((comps or {}).get(block) or {}).get(key) or {}
        if not s:
            return {"lo": 0.6, "mid": 0.8, "hi": 1.0}[q] if block.startswith("first_game") else {"lo": 0.7, "mid": 0.9, "hi": 1.05}[q]
        if q == "mid":
            return float(s["median"])
        lo, hi = (float(x) for x in s["range_middle_half"].split("-"))
        return lo if q == "lo" else hi

    out = {}
    for label, block in (("first_game", "first_game_vs_before"), ("games_2_3_avg", "first_3_games_vs_before")):
        key = "opportunities" if label == "first_game" and ((comps or {}).get(block) or {}).get("opportunities") else "ppr"
        mids = {}
        for q in ("lo", "mid", "hi"):
            r = ratio(block, key, q)
            mids[q] = pre_opps * r * volume * eff if key == "opportunities" else pre_ppr * r * volume * (eff / own_eff if own_eff else 1)
        out[label] = {"ppr": round(mids["mid"], 1), "range": f"{mids['lo']:.1f}-{mids['hi']:.1f}"}
        if label == "first_game" and key == "opportunities":
            out[label]["opportunities"] = round(pre_opps * ratio(block, key, "mid") * volume, 1)
    out["inputs"] = {"his_old_opps_pg": round(pre_opps, 1), "his_old_ppr_pg": round(pre_ppr, 1), "team_volume_now_vs_then": round(volume, 2),
                     "ppr_per_opp_used": round(eff, 2)}
    return out
