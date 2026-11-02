"""Grade the ESPN stat feed: score finished weeks from ESPN's box scores with our engine and compare with Sleeper.

For each league's current season and each finished week checked, every rostered player's points and every
team's total are scored from ESPN box scores (scoring/espn_feed.py) and compared with Sleeper's own numbers,
the same way scoring/shadow.py grades Sleeper's stat lines. Also counts ESPN players we couldn't match to an id
and which scoring rules explain the gaps. No model call.

Prints counts only (the repo and its Actions logs are public): no owner or player names.

    python -m scoring.espn_check --weeks 3
"""
from __future__ import annotations

import argparse
from collections import Counter
from typing import Any, Mapping

from scoring import espn_feed
from scoring.engine import player_points
from scoring.shadow import SLEEPER, _fetch, report, score_season, stat_lines


def espn_week(season: int, week: int, idmap: espn_feed.IdMap, get_json=_fetch) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    board = get_json(f"{espn_feed.ESPN}/scoreboard?seasontype=2&week={week}&dates={season}") or {}
    games = espn_feed.games(board)
    summaries = [get_json(f"{espn_feed.ESPN}/summary?event={g['id']}") for g in games]
    lines, unmatched = espn_feed.week_lines([s for s in summaries if s], idmap)
    unknown = Counter()  # ESPN box score keys we don't map yet (category.key)
    for s in summaries:
        for team_box in ((s or {}).get("boxscore") or {}).get("players") or []:
            for cat in team_box.get("statistics") or []:
                mapping = espn_feed.KEYS.get(str(cat.get("name") or ""), {})
                for key in cat.get("keys") or []:
                    for k, _ in espn_feed._split(str(key), "0/0" if "/" in key else "0-0" if "-" in key else "0"):
                        if k not in mapping:
                            unknown[f"{cat.get('name')}.{k}"] += 1
    return lines, {"games": len(games), "summaries": sum(1 for s in summaries if s), "unmatched": unmatched,
                   "unknown": ", ".join(k for k, _ in unknown.most_common(12))}


def key_gaps(ours: Mapping[str, Mapping[str, Any]], theirs: Mapping[str, Mapping[str, Any]], scoring: Mapping[str, Any], ids: set[str]) -> Counter:
    """Scoring keys whose stat differs between the two feeds, counted over the given players."""
    keys = [k for k, w in scoring.items() if isinstance(w, (int, float)) and w]
    out: Counter = Counter()
    for pid in ids:
        a, b = (ours.get(pid) or {}).get("stats") or {}, (theirs.get(pid) or {}).get("stats") or {}
        for k in keys:
            if abs(float(a.get(k) or 0) - float(b.get(k) or 0)) > 1e-6:
                out[k] += 1
    return out


def run(client: Any, weeks: int, get_json=_fetch) -> list[str]:
    from league_ai.settings_store import load_settings
    from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk

    state = get_json(f"{SLEEPER}/v1/state/nfl") or {}
    season, now_week = int(state.get("season") or 0), int(state.get("week") or 0)
    players = get_json(f"{SLEEPER}/v1/players/nfl") or {}
    try:
        from pipeline.nfl_data.common import fetch_text
        crosswalk = Crosswalk.from_csv(fetch_text(CROSSWALK_URL))
    except Exception:
        crosswalk = None
    idmap = espn_feed.IdMap(players, crosswalk)
    check_weeks = [w for w in range(max(1, now_week - weeks), now_week) if w >= 1]
    espn: dict[int, dict[str, dict[str, Any]]] = {}
    out = [f"ESPN feed check: season {season}, weeks {check_weeks[0] if check_weeks else '-'}-{check_weeks[-1] if check_weeks else '-'}; "
           f"{len(idmap.by_espn)} players with an ESPN id"]
    for w in check_weeks:
        espn[w], meta = espn_week(season, w, idmap, get_json)
        out.append(f"  week {w}: {meta['summaries']}/{meta['games']} game summaries, {len(espn[w])} players matched, {meta['unmatched']} unmatched (mostly defenders/specialists); unmapped keys: {meta['unknown'] or 'none'}")

    leagues = client.table("leagues").select("id, sleeper_league_id").execute().data or []
    seen: set[str] = set()
    for n, lg in enumerate(leagues, start=1):
        sid = str(lg.get("sleeper_league_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        info = get_json(f"{SLEEPER}/v1/league/{sid}") or {}
        if int(info.get("season") or 0) != season:
            continue
        scoring = load_settings(client, str(lg["id"])).scoring_settings or info.get("scoring_settings") or {}
        matchups = {w: get_json(f"{SLEEPER}/v1/league/{sid}/matchups/{w}") or [] for w in check_weeks}
        res = score_season(season, lambda w: matchups.get(w, []) if w in espn else [], lambda w: espn.get(w, {}), scoring, max(check_weeks or [0]))
        out += [f"league {n} (ESPN lines) " + line if i == 0 else line for i, line in enumerate(report(res))]
        # Which rules the two feeds disagree on, over this league's rostered players.
        gaps: Counter = Counter()
        point_gaps = []
        for w in check_weeks:
            theirs = stat_lines(season, w, get_json)
            ids = {str(p) for row in matchups[w] for p in (row.get("players_points") or {})}
            gaps += key_gaps(espn[w], theirs, scoring, ids)
            for pid in ids:
                if pid in espn[w] and pid in theirs:
                    a = player_points(espn[w][pid]["stats"], scoring, espn[w][pid]["pos"])
                    b = player_points(theirs[pid]["stats"], scoring, theirs[pid]["pos"])
                    point_gaps.append(round(a - b, 2))
        off = [g for g in point_gaps if abs(g) > 0.011]
        out.append(f"  ESPN vs Sleeper stat lines, same rules: {len(point_gaps) - len(off)}/{len(point_gaps)} players equal; "
                   f"rules that differ most: " + (", ".join(f"{k} ({c})" for k, c in gaps.most_common(8)) or "none"))
    return out


def main() -> None:
    from pipeline.nfl_data.common import service_client

    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=3)
    args = parser.parse_args()
    for line in run(service_client(), args.weeks):
        print(line, flush=True)


if __name__ == "__main__":
    main()
