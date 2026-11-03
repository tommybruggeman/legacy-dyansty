"""Official final stat lines, kept as our own copy: every finished NFL week saved to final_stat_lines.

Runs daily (.github/workflows/final-scores.yml). For each week of the current season whose games are all over
(ESPN's free scoreboard says so), it saves every fantasy player's stat line from nflverse's free weekly stats
(scoring/nflverse_lines.py), or from ESPN's final box scores when nflverse hasn't published the week yet. The
week is re-saved each day so NFL stat corrections land, then locked three days after its last game; a locked
week never changes again (the commissioner can still finalize or fix by hand later).

Stat corrections: when a re-save of official lines changes a starter's points in a league, that league gets one
saved correction (stat_corrections: old and new totals, the players and stats that changed, and any result it
flipped) and a push to everyone in the league that opens Activity. Scores, records, top-5 weeks and the bracket all
follow from the corrected lines on their own; nobody edits a score by hand.

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
from typing import Any, Mapping

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
            theirs = stat_lines(season, week, _get_json)
            defs = [pid for pid, line in theirs.items() if line.get("pos") == "DEF"]
            if defs and any(k.startswith(("pts_allow", "yds_allow", "sack", "int", "def_", "fum_rec", "safe", "blk_kick", "ff"))
                            for k, v in scoring.items() if v):
                d_same = sum(abs(player_points((lines.get(p) or {}).get("stats"), scoring, "DEF")
                                 - player_points(theirs[p]["stats"], scoring, "DEF")) <= TOLERANCE for p in defs)
                d_gaps = key_gaps(lines, theirs, scoring, set(defs))
                out.append(f"  league {n}: team defenses {d_same}/{len(defs)} match Sleeper; stats that differ: "
                           + (", ".join(f"{k} ({c})" for k, c in d_gaps.most_common(8)) or "none"))
    return out


CORRECTIONS = "stat_corrections"


def week_starters(client: Any, league_id: str, season: int, week: int, rows: list[Mapping[str, Any]]) -> dict[int, list[str]]:
    """roster id -> the week's starters: the app lineup for weeks the app runs (switch on), else Sleeper's."""
    from api import lineups
    starters = {int(r["roster_id"]): [str(p) for p in r.get("starters") or []] for r in rows if r.get("roster_id") is not None}
    try:
        switch = (client.table("league_app_transactions").select("enabled,own_from_week").eq("league_id", league_id)
                  .limit(1).execute().data or [{}])[0]
    except Exception:
        switch = {}
    if not switch.get("enabled") or week < int(switch.get("own_from_week") or 15):
        return starters
    from league_ai.settings_store import load_settings
    slots = lineups.starter_slots(load_settings(client, league_id).roster_positions)
    teams = client.table("league_teams").select("id,sleeper_roster_id").eq("league_id", league_id).execute().data or []
    saved = (client.table("league_lineups").select("league_team_id,week,slots").eq("league_id", league_id)
             .eq("season", season).lte("week", week).order("week", desc=True).execute().data or [])
    for t in teams:
        if t.get("sleeper_roster_id") is None:
            continue
        rid = int(t["sleeper_roster_id"])
        mine = [r for r in saved if str(r["league_team_id"]) == str(t["id"])]
        ids = {str(x.get("player_id")) for r in mine[:1] for x in r.get("slots") or [] if x.get("player_id")} | set(starters.get(rid, []))
        starters[rid] = [p or "0" for p in lineups.effective(slots, mine, starters.get(rid, []), ids)]
    return starters


def record_corrections(client: Any, season: int, week: int, old: dict[str, Any], new: dict[str, Any], dry: bool) -> list[str]:
    """Save and push a correction for every league where the new official lines moved a starter's points."""
    import hashlib
    import json

    from league_ai.settings_store import load_settings
    from scoring import corrections
    from scripts.notify_trades import send

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
        lid = str(lg["id"])
        scoring = load_settings(client, lid).scoring_settings or info.get("scoring_settings") or {}
        rows = _get_json(f"{SLEEPER}/league/{sid}/matchups/{week}") or []
        starters = week_starters(client, lid, season, week, rows)
        teams = corrections.team_changes(starters, old, new, scoring)
        if not teams:
            continue
        before, after = corrections.totals(starters, old, scoring), corrections.totals(starters, new, scoring)
        results = corrections.flips(before, after, {int(r["roster_id"]): r.get("matchup_id") for r in rows if r.get("roster_id") is not None})
        team_of = {int(t["sleeper_roster_id"]): str(t["id"]) for t in
                   client.table("league_teams").select("id,sleeper_roster_id").eq("league_id", lid).execute().data or []
                   if t.get("sleeper_roster_id") is not None}
        changes = [{**t, "team_id": team_of.get(int(t["team"])), "roster_id": int(t["team"]), "results": results.get(t["team"], [])}
                   for t in teams]
        for team, res in results.items():  # a team whose result flipped only because its opponent's score moved
            if team not in {c["roster_id"] for c in changes}:
                changes.append({"team_id": team_of.get(int(team)), "roster_id": int(team), "old": before.get(team), "new": after.get(team),
                                "players": [], "results": res})
        key = "statfix:" + hashlib.sha1(json.dumps([season, week, changes], sort_keys=True, default=str).encode()).hexdigest()[:20]
        flipped = sum(1 for c in changes if c["results"])
        out.append(f"  league {n}: stat correction week {week}: {len(teams)} teams' scores changed, {flipped} with a result change")
        if dry:
            continue
        sent = {r["key"] for r in client.table("push_sent").select("key").eq("league_id", lid).eq("key", key).execute().data or []}
        if key in sent:
            continue
        saved = (client.table(CORRECTIONS).insert({"league_id": lid, "season": season, "week": week, "changes": changes})
                 .execute().data or [{}])[0]
        tokens = [r["token"] for r in client.table("push_tokens").select("token").eq("league_id", lid).execute().data or []]
        messages = [{"to": tok, "body": corrections.push_body(week, len(teams)), "sound": "default",
                     "data": {"url": f"/activity?correction={saved.get('id', '')}"}} for tok in tokens]
        dead = send(messages) if messages else []
        if dead:
            client.table("push_tokens").delete().in_("token", dead).execute()
        client.table("push_sent").upsert({"league_id": lid, "key": key}, on_conflict="league_id,key").execute()
        out.append(f"    saved; pushed to {len(messages) - len(dead)} phones")
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
    try:  # team defense lines (sacks, takeaways, TDs, yards allowed) come from nflverse's team stats
        team_rows = nflverse_lines.read_rows(fetch_text(nflverse_lines.TEAM_URL.format(season=season)))
    except Exception as exc:
        print(f"nflverse team stats unavailable ({type(exc).__name__}); no team defense lines", flush=True)
        team_rows = []
    players = _get_json(f"{SLEEPER}/players/nfl") or {}
    try:
        crosswalk = Crosswalk.from_csv(fetch_text(CROSSWALK_URL))
    except Exception:
        crosswalk = None
    gsis = nflverse_lines.GsisMap(players, crosswalk)
    espn_ids = espn_feed.IdMap(players, crosswalk)

    try:
        saved = {int(r["week"]): {**r, "has_def": r.get("DEN") is not None} for r in
                 client.table(TABLE).select("week,locked,source,lines->DEN").eq("season", season).execute().data or []}
    except Exception:  # table not created yet (dry runs before the SQL)
        saved = {}
    print(f"season {season}: current week {current}; nflverse has weeks {sorted(published) or 'none'}", flush=True)
    for week in range(1, min(current, 18) + 1):
        if (saved.get(week) or {}).get("locked") and (saved.get(week) or {}).get("has_def"):
            continue  # locked weeks saved before team defense lines existed are filled in once
        week_board = _get_json(f"{espn_feed.ESPN}/scoreboard?seasontype=2&week={week}&dates={season}") or {}
        done, last_kick = week_status(week_board)
        if not done:
            continue
        if week in published and (not team_rows or week in nflverse_lines.weeks_in(team_rows)):
            lines, unmatched = nflverse_lines.week_lines(rows, week, gsis)
            lines.update(nflverse_lines.def_lines(team_rows, week, espn_feed.points_allowed(week_board)))
            source = "nflverse"
        else:
            summaries = [_get_json(f"{espn_feed.ESPN}/summary?event={g['id']}") for g in espn_feed.games(week_board)]
            raw, unmatched = espn_feed.week_lines([s for s in summaries if s], espn_ids)
            lines = {pid: line for pid, line in raw.items() if line.get("pos") in nflverse_lines.POSITIONS}
            source = "espn"
        has_def = any(line.get("pos") == "DEF" for line in lines.values())
        locked = bool(last_kick and now - last_kick >= LOCK_AFTER + timedelta(hours=4) and source == "nflverse" and has_def)
        print(f"week {week}: {len(lines)} players from {source}, {unmatched} unmatched" + (", locked" if locked else ""), flush=True)
        prev = saved.get(week) or {}
        if prev.get("source") == "nflverse" and source == "nflverse":  # an NFL stat correction, not the first save
            try:
                old_lines = (client.table(TABLE).select("lines").eq("season", season).eq("week", week).limit(1)
                             .execute().data or [{}])[0].get("lines") or {}
                for line in record_corrections(client, season, week, old_lines, lines, args.dry):
                    print(line, flush=True)
            except Exception as exc:
                print(f"  stat correction check failed: {type(exc).__name__}", flush=True)
        for line in cross_check(client, season, week, lines):
            print(line, flush=True)
        if not args.dry and lines:
            client.table(TABLE).upsert({"season": season, "week": week, "lines": lines, "source": source, "locked": locked,
                                        "updated_at": now.isoformat()}, on_conflict="season,week").execute()
    return 0


if __name__ == "__main__":
    sys.exit(main())
