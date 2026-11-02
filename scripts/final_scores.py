"""Official final stat lines, kept as our own copy: every finished NFL week saved to final_stat_lines.

Runs daily (.github/workflows/final-scores.yml). For each week of the current season whose games are all over
(ESPN's free scoreboard says so), it saves every fantasy player's stat line from nflverse's free weekly stats
(scoring/nflverse_lines.py), or from ESPN's final box scores when nflverse hasn't published the week yet. The
week is re-saved each day so NFL stat corrections land, then locked three days after its last game; a locked
week never changes again (the commissioner can still finalize or fix by hand later).

Then it cross-checks every league against Sleeper: each rostered player's points from our lines and our engine
vs Sleeper's, so any gap shows up while Sleeper is still running alongside.

Prints counts only (the repo and its Actions logs are public): no player, team or owner names.

    python -m scripts.final_scores            # save + check
    python -m scripts.final_scores --dry      # check only, nothing saved
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import espn_feed, nflverse_lines  # noqa: E402
from scoring.engine import player_points  # noqa: E402

TABLE = "final_stat_lines"
SLEEPER = "https://api.sleeper.app/v1"
LOCK_AFTER = timedelta(days=3)  # stat corrections are in by then
TOLERANCE = 0.011


def _get_json(url: str) -> Any:
    from pipeline.nfl_data.common import fetch_json
    try:
        return fetch_json(url, timeout=60)
    except Exception as exc:
        print(f"fetch failed: {type(exc).__name__}", flush=True)
        return None


def _date(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def week_status(board: dict[str, Any]) -> tuple[bool, datetime | None]:
    """(every game final, kickoff of the last game) for one week's ESPN scoreboard."""
    games = espn_feed.games(board)
    kicks = [d for d in (_date(e.get("date")) for e in board.get("events") or []) if d]
    return bool(games) and all(g["state"] == "post" for g in games), max(kicks, default=None)


def cross_check(client: Any, season: int, week: int, lines: dict[str, Any]) -> list[str]:
    """Each league's rostered players scored from our lines vs Sleeper's numbers for the week."""
    from league_ai.settings_store import load_settings

    out = []
    seen: set[str] = set()
    for n, lg in enumerate(client.table("leagues").select("id, sleeper_league_id").execute().data or [], start=1):
        sid = str(lg.get("sleeper_league_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        info = _get_json(f"{SLEEPER}/league/{sid}") or {}
        if int(info.get("season") or 0) != season:
            continue
        scoring = load_settings(client, str(lg["id"])).scoring_settings or info.get("scoring_settings") or {}
        rows = _get_json(f"{SLEEPER}/league/{sid}/matchups/{week}") or []
        same = total = 0
        for row in rows:
            for pid, theirs in (row.get("players_points") or {}).items():
                line = lines.get(str(pid))
                ours = player_points(line["stats"], scoring, line["pos"]) if line else 0.0
                total += 1
                same += abs(ours - float(theirs or 0)) <= TOLERANCE
        if total:
            from scoring.espn_check import key_gaps
            from scoring.shadow import stat_lines
            ids = {str(p) for row in rows for p in (row.get("players_points") or {})}
            gaps = key_gaps(lines, stat_lines(season, week, _get_json), scoring, ids)
            out.append(f"  league {n}: {same}/{total} player scores match Sleeper ({same / total:.1%}); stats that differ from "
                       f"Sleeper's lines: " + (", ".join(f"{k} ({c})" for k, c in gaps.most_common(8)) or "none"))
    return out


def main() -> int:
    from pipeline.nfl_data.common import fetch_text, service_client
    from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    client = service_client()
    now = datetime.now(timezone.utc)

    board = _get_json(f"{espn_feed.ESPN}/scoreboard") or {}
    season = int((board.get("season") or {}).get("year") or 0)
    current = int((board.get("week") or {}).get("number") or 0)
    if int((board.get("season") or {}).get("type") or 2) == 3:  # playoffs: every regular week is done
        current = 19
    if not season or not current:
        print("no NFL season on ESPN's scoreboard; nothing to do", flush=True)
        return 0

    try:
        rows = nflverse_lines.read_rows(fetch_text(nflverse_lines.STATS_URL.format(season=season)))
    except Exception as exc:
        print(f"nflverse unavailable ({type(exc).__name__}); ESPN finals only", flush=True)
        rows = []
    published = nflverse_lines.weeks_in(rows)
    players = _get_json(f"{SLEEPER}/players/nfl") or {}
    try:
        crosswalk = Crosswalk.from_csv(fetch_text(CROSSWALK_URL))
    except Exception:
        crosswalk = None
    gsis = nflverse_lines.GsisMap(players, crosswalk)
    espn_ids = espn_feed.IdMap(players, crosswalk)

    try:
        saved = {int(r["week"]): r for r in client.table(TABLE).select("week,locked").eq("season", season).execute().data or []}
    except Exception:  # table not created yet (dry runs before the SQL)
        saved = {}
    print(f"season {season}: current week {current}; nflverse has weeks {sorted(published) or 'none'}", flush=True)
    for week in range(1, min(current, 18) + 1):
        if (saved.get(week) or {}).get("locked"):
            continue
        week_board = _get_json(f"{espn_feed.ESPN}/scoreboard?seasontype=2&week={week}&dates={season}") or {}
        done, last_kick = week_status(week_board)
        if not done:
            continue
        if week in published:
            lines, unmatched = nflverse_lines.week_lines(rows, week, gsis)
            source = "nflverse"
        else:
            summaries = [_get_json(f"{espn_feed.ESPN}/summary?event={g['id']}") for g in espn_feed.games(week_board)]
            raw, unmatched = espn_feed.week_lines([s for s in summaries if s], espn_ids)
            lines = {pid: line for pid, line in raw.items() if line.get("pos") in nflverse_lines.POSITIONS}
            source = "espn"
        locked = bool(last_kick and now - last_kick >= LOCK_AFTER + timedelta(hours=4) and source == "nflverse")
        print(f"week {week}: {len(lines)} players from {source}, {unmatched} unmatched" + (", locked" if locked else ""), flush=True)
        for line in cross_check(client, season, week, lines):
            print(line, flush=True)
        if not args.dry and lines:
            client.table(TABLE).upsert({"season": season, "week": week, "lines": lines, "source": source, "locked": locked,
                                        "updated_at": now.isoformat()}, on_conflict="season,week").execute()
    return 0


if __name__ == "__main__":
    sys.exit(main())
