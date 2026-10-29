"""Shadow scoring: score every finished week ourselves and compare with what Sleeper scored. No model call.

For each season in the league's Sleeper history, every rostered player's points (players_points) and every
team's weekly total (starters) are recomputed from Sleeper's public NFL stat lines with scoring/engine.py,
then compared with Sleeper's own numbers. The current season is scored with the rules saved in the app
(Settings > League AI), falling back to Sleeper's; past seasons use that season's Sleeper rules.

Prints counts only (the repo and its Actions logs are public): no owner or player names.

    python -m scoring.shadow
"""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Callable, Mapping

from scoring.engine import CATCH_BONUS, lineup_points, player_points, rule_diff

SLEEPER = "https://api.sleeper.app"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
MAX_WEEKS = 18
TOLERANCE = 0.011  # Sleeper rounds each player to 2 decimals


def _fetch(url: str) -> Any:
    from pipeline.nfl_data.common import fetch_json
    try:
        return fetch_json(url, timeout=60)
    except Exception as exc:
        print(f"[shadow] fetch failed: {type(exc).__name__}", flush=True)
        return None


def stat_lines(season: int, week: int, get_json: Callable[[str], Any] = _fetch) -> dict[str, dict[str, Any]]:
    """player id -> {stats, pos} from Sleeper's public weekly stats (not tied to any league)."""
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)
    out: dict[str, dict[str, Any]] = {}
    for row in get_json(f"{SLEEPER}/stats/nfl/{season}/{week}?{query}") or []:
        pid = str(row.get("player_id") or "")
        if pid:
            player = row.get("player") or {}
            out[pid] = {"stats": row.get("stats") or {}, "pos": str(player.get("position") or row.get("position") or "").upper()}
    return out


def explain(diff: float, line: Mapping[str, Any], scoring: Mapping[str, Any]) -> list[str]:
    """Scoring keys that would account for a player's gap on their own: weight x stat (or the weight alone) equals the gap."""
    hits = []
    stats = line.get("stats") or {}
    for key, weight in scoring.items():
        try:
            w = float(weight)
        except (TypeError, ValueError):
            continue
        if not w:
            continue
        value = stats.get(key)
        candidates = [w] + ([w * float(value)] if isinstance(value, (int, float)) else [])
        if key in CATCH_BONUS:
            candidates.append(w * float(stats.get("rec") or 0))
        if any(abs(abs(diff) - abs(c)) <= TOLERANCE for c in candidates if c):
            hits.append(key)
    return hits


def compare_week(matchups: list[Mapping[str, Any]], lines: Mapping[str, Mapping[str, Any]], scoring: Mapping[str, Any]) -> dict[str, Any]:
    """One week: team totals and every rostered player's points, ours vs Sleeper's."""
    out: dict[str, Any] = {"teams": 0, "teams_match": 0, "overrides": 0, "override_gaps": 0, "team_diffs": [], "players": 0, "players_match": 0,
                           "no_line": 0, "misses": []}
    for row in matchups:
        sleeper_pts = row.get("custom_points") if row.get("custom_points") is not None else row.get("points")
        if row.get("custom_points") is not None:
            out["overrides"] += 1
        ours = lineup_points(row.get("starters") or [], lines, scoring)
        out["teams"] += 1
        diff = round(ours - float(sleeper_pts or 0), 2)
        out["team_diffs"].append(diff)
        if abs(diff) <= TOLERANCE * max(len(row.get("starters") or []), 1):
            out["teams_match"] += 1
        elif row.get("custom_points") is not None:
            out["override_gaps"] += 1  # the commissioner changed this score by hand in Sleeper
        for pid, theirs in (row.get("players_points") or {}).items():
            line = lines.get(str(pid))
            out["players"] += 1
            if not line:
                if abs(float(theirs or 0)) <= TOLERANCE:
                    out["players_match"] += 1
                else:
                    out["no_line"] += 1
                continue
            mine = player_points(line.get("stats"), scoring, line.get("pos"))
            gap = round(mine - float(theirs or 0), 2)
            if abs(gap) <= TOLERANCE:
                out["players_match"] += 1
            else:
                out["misses"].append({"pos": line.get("pos") or "?", "gap": gap, "keys": explain(gap, line, scoring)})
    return out


def score_season(season: int, matchups_for: Callable[[int], list[Mapping[str, Any]]], lines_for: Callable[[int], Mapping[str, Mapping[str, Any]]],
                 scoring: Mapping[str, Any], last_week: int) -> dict[str, Any]:
    total: dict[str, Any] = {"season": season, "weeks": 0, "teams": 0, "teams_match": 0, "overrides": 0, "override_gaps": 0, "players": 0, "players_match": 0,
                             "no_line": 0, "team_diffs": [], "misses": []}
    for week in range(1, last_week + 1):
        rows = [r for r in matchups_for(week) or [] if r.get("matchup_id") is not None or r.get("points")]
        if not rows or not any(float(r.get("points") or 0) for r in rows):
            continue
        res = compare_week(rows, lines_for(week), scoring)
        total["weeks"] += 1
        for key in ("teams", "teams_match", "overrides", "override_gaps", "players", "players_match", "no_line"):
            total[key] += res[key]
        total["team_diffs"] += res["team_diffs"]
        total["misses"] += res["misses"]
    return total


def report(res: Mapping[str, Any]) -> list[str]:
    def pct(a: int, b: int) -> str:
        return f"{a}/{b} ({a / b:.1%})" if b else "0/0"
    diffs = [abs(d) for d in res["team_diffs"]]
    lines = [f"season {res['season']}: {res['weeks']} weeks",
             f"  team totals match: {pct(res['teams_match'], res['teams'])}; biggest gap {max(diffs, default=0):.2f} pts; commissioner overrides {res['overrides']} ({res['override_gaps']} of the misses)",
             f"  player scores match: {pct(res['players_match'], res['players'])}; scored by Sleeper but no public stat line: {res['no_line']}"]
    by_pos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in res["misses"]:
        by_pos[m["pos"]].append(m)
    for pos, misses in sorted(by_pos.items(), key=lambda kv: -len(kv[1])):
        keys = Counter(k for m in misses for k in m["keys"]).most_common(4)
        lines.append(f"  {pos}: {len(misses)} off, median gap {median(m['gap'] for m in misses):+.2f}; likely rules: "
                     + (", ".join(f"{k} ({n})" for k, n in keys) or "none single out"))
    return lines


def run(client: Any, get_json: Callable[[str], Any] = _fetch) -> list[str]:
    from league_ai.settings_store import load_settings
    from pipeline.league_history.run import league_chain

    out: list[str] = []
    state = get_json(f"{SLEEPER}/v1/state/nfl") or {}
    now_season, now_week = int(state.get("season") or 0), int(state.get("week") or 0)
    leagues = client.table("leagues").select("id, sleeper_league_id").execute().data or []
    seen: set[str] = set()
    for n, lg in enumerate(leagues, start=1):
        sid = str(lg.get("sleeper_league_id") or "")
        if not sid or sid in seen:
            continue
        saved = load_settings(client, str(lg["id"])).scoring_settings or {}
        for league in league_chain(sid):
            lid = str(league.get("league_id") or "")
            if lid in seen:
                continue
            seen.add(lid)
            season = int(league.get("season") or 0)
            theirs = league.get("scoring_settings") or {}
            current = season == now_season
            scoring = saved if current and saved else theirs
            if current:
                diff = rule_diff(saved, theirs) if saved else None
                out.append(f"league {n} {season}: " + ("no scoring rules saved in the app yet; scored with Sleeper's" if diff is None else
                                                       "app scoring rules match Sleeper's" if not diff else f"app scoring rules differ from Sleeper's on {', '.join(diff)}"))
            last_week = max(now_week - 1, 0) if current else MAX_WEEKS
            res = score_season(season, lambda w: get_json(f"{SLEEPER}/v1/league/{lid}/matchups/{w}") or [],
                               lambda w: stat_lines(season, w, get_json), scoring, last_week)
            out += [f"league {n} " + line if i == 0 else line for i, line in enumerate(report(res))]
    return out


def main() -> None:
    from pipeline.nfl_data.common import service_client

    for line in run(service_client()):
        print(line, flush=True)


if __name__ == "__main__":
    main()
