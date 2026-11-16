"""How fresh our injury data is, and how our play / won't-play calls did this week (saves nothing).

    python -m scripts.injury_calls_check            # newest week with games played
    python -m scripts.injury_calls_check --week 11

The call for each player listed on this week's official report is the player page's chance to play
(league_ai.injury_outlook.play_chance from the final game status and practice line, no news nudge).
Played = he has a stat row that week. Teams whose game isn't loaded yet are skipped.
Also lists surprise scratches: rostered fantasy starters-type players with no tag who didn't play.
Prints NFL player names only (public repo logs), never owners.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import datetime, timezone

import requests
from supabase import create_client

from league_ai import injury_outlook as io

LEAGUE = "1361083066197491712"
POS = {"QB", "RB", "WR", "TE"}


def rows(client, table, cols, **eq):
    out, start = [], 0
    while True:
        q = client.table(table).select(cols)
        for k, v in eq.items():
            q = q.eq(k, v)
        chunk = q.range(start, start + 999).execute().data or []
        out += chunk
        if len(chunk) < 1000:
            return out
        start += 1000


def age(ts):
    if not ts:
        return "never"
    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    mins = (datetime.now(timezone.utc) - t).total_seconds() / 60
    return f"{t.strftime('%a %H:%M UTC')} ({mins / 60:.1f} h ago)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int)
    a = ap.parse_args()
    c = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    print("== Freshness ==")
    for table, col in (("nfl_injuries", "refreshed_at"), ("nfl_practice_days", "refreshed_at"), ("nfl_practice_reports", "refreshed_at"),
                       ("nfl_player_stats", "refreshed_at")):
        try:
            r = c.table(table).select(col).order(col, desc=True).limit(1).execute().data
            print(f"{table:22} last write {age(r[0][col] if r else None)}")
        except Exception as e:
            print(f"{table:22} error {type(e).__name__}")
    try:
        r = c.table("nfl_injuries").select("reported_at").order("reported_at", desc=True).limit(1).execute().data
        print(f"{'newest ESPN news item':22} {age(r[0]['reported_at'] if r else None)}")
    except Exception as e:
        print(f"newest ESPN news item  error {type(e).__name__}")

    stats = rows(c, "nfl_player_stats", "sleeper_id,gsis_id,team,week,position", season=a.season, season_type="REG")
    week = a.week or max(int(r["week"]) for r in stats)
    wk = [r for r in stats if int(r["week"]) == week]
    played = {r["gsis_id"] for r in wk}
    teams_loaded = {r["team"] for r in wk}
    print(f"\n== Week {week} calls (teams with box scores loaded: {len(teams_loaded)}) ==")

    rates = {(r["scope"], r.get("game_status") or "", r.get("practice") or ""): r
             for r in c.table("nfl_injury_play_rates").select("scope,game_status,practice,listings,rate").limit(2000).execute().data or []}
    report = [r for r in rows(c, "nfl_practice_reports", "gsis_id,sleeper_id,player_name,position,team,report_status,practice_status,report_injury,practice_injury",
                              season=a.season, week=week) if str(r.get("position") or "").upper() in POS]
    try:
        rostered = {p for t in requests.get(f"https://api.sleeper.app/v1/league/{LEAGUE}/rosters", timeout=20).json() for p in (t.get("players") or [])}
    except Exception:
        rostered = set()

    graded, misses = Counter(), []
    for r in report:
        status = io.norm_status(r.get("report_status"))
        if not status or r.get("team") not in teams_loaded:
            continue
        injury = io.injury_key(r.get("report_injury") or r.get("practice_injury"))
        out = io.play_chance(position=r.get("position"), injury=injury, official_status=status, official_practice=r.get("practice_status"),
                             sleeper_status=None, sleeper_practice=None, espn_status=None, return_date=None, game_date=None, comments=None, rates=rates)
        if not out:
            continue
        p, did = out["probability"], r["gsis_id"] in played
        said = p >= 0.5
        right = said == did
        mine = r.get("sleeper_id") in rostered
        graded[(status, right)] += 1
        graded[("rostered", right)] += mine
        brier = (p - did) ** 2
        graded["brier"] += brier
        graded["n"] += 1
        if not right:
            misses.append((mine, r.get("player_name"), r.get("position"), r.get("team"), status, io.norm_practice(r.get("practice_status")) or "-", out["chance_to_play"], "played" if did else "did not play"))
    n = graded["n"]
    if n:
        right = sum(v for k, v in graded.items() if isinstance(k, tuple) and k[0] != "rostered" and k[1])
        print(f"Graded {n} tagged players: {right} right, {n - right} wrong ({100 * right / n:.0f}%). Brier {graded['brier'] / n:.3f}")
        for s in ("Questionable", "Doubtful", "Out"):
            r_, w_ = graded[(s, True)], graded[(s, False)]
            if r_ + w_:
                print(f"  {s:12} {r_} right, {w_} wrong")
        print(f"  On a league roster: {graded[('rostered', True)]} right, {graded[('rostered', False)]} wrong")
    print("\nWrong calls (* = on a league roster):")
    for m in sorted(misses, key=lambda x: (not x[0], x[1] or "")):
        print(f"  {'*' if m[0] else ' '} {m[1]} {m[2]} {m[3]}: {m[4]}, practice {m[5]}, we said {m[6]} -> {m[7]}")

    # Surprise scratches: no tag this week, played last week, rostered, team played, but no stat line.
    tagged = {r["gsis_id"] for r in report if io.norm_status(r.get("report_status"))}
    last = {r["gsis_id"]: r for r in stats if int(r["week"]) == week - 1 and r.get("sleeper_id") in rostered and str(r.get("position") or "").upper() in POS}
    names = {r["gsis_id"]: r.get("player_name") for r in report}
    scratches = [g for g, r in last.items() if g not in tagged and g not in played and r.get("team") in teams_loaded]
    print(f"\nRostered players with no injury tag who played last week but not this week: {len(scratches)}")
    if scratches:
        info = {p["gsis_id"]: p for p in c.table("nfl_players").select("gsis_id,full_name,position,team,injury_status").in_("gsis_id", scratches).execute().data or []}
        for g in scratches:
            p = info.get(g, {})
            print(f"  {p.get('full_name') or names.get(g) or g} {p.get('position', '')} {p.get('team', '')} (status now: {p.get('injury_status') or 'none'})")


if __name__ == "__main__":
    main()
