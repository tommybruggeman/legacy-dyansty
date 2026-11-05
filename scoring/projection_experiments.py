"""Ways to make the weekly projections more accurate, each replayed walk-forward (no peeking) against real scores.

1. tilt: how strong the matchup tilt should be for point totals (0 = Sleeper's number as is, 1 = today)
2. decay: when learning the position line, older weeks count for less (0.97 / 0.94 / 0.90 per week)
3. usage: our own projection from each player's recent usage (carries, targets, pass attempts; last 4 games)
   times his position's points per opportunity so far, blended into Sleeper's at 10-40%
4. espn: ESPN's free projections (projected stat lines scored with our rules) blended into Sleeper's at 25-50%

Every variant goes through the same position line as the app, so the comparison is like for like. Prints numbers
only (repo and Actions logs are public).

    python -m scoring.projection_experiments
"""
from __future__ import annotations

import copy
import json
import os
from collections import defaultdict
from typing import Any

ESPN_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3?scoringPeriodId={week}&view=kona_player_info"
# ESPN stat id -> Sleeper/our stat key
ESPN_STATS = {"0": "pass_att", "1": "pass_cmp", "3": "pass_yd", "4": "pass_td", "20": "pass_int", "23": "rush_att", "24": "rush_yd",
              "25": "rush_td", "53": "rec", "42": "rec_yd", "43": "rec_td", "58": "rec_tgt", "72": "fum_lost", "19": "pass_2pt",
              "26": "rush_2pt", "44": "rec_2pt"}


def espn_week(season: int, week: int) -> dict[str, dict[str, float]]:
    """espn player id -> projected stat line for that week (empty when ESPN doesn't serve it)."""
    import requests

    head = {"X-Fantasy-Filter": json.dumps({"players": {"limit": 1200, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}),
            "User-Agent": "legacy-dynasty-study"}
    try:
        resp = requests.get(ESPN_URL.format(season=season, week=week), headers=head, timeout=60)
        resp.raise_for_status()
        players = resp.json().get("players") or []
    except Exception as exc:
        print(f"  espn {season} wk {week}: unavailable ({type(exc).__name__})")
        return {}
    out = {}
    for p in players:
        pl = p.get("player") or {}
        for s in pl.get("stats") or []:
            if s.get("statSourceId") == 1 and s.get("scoringPeriodId") == week and s.get("seasonId") == season and s.get("statSplitTypeId") == 1:
                out[str(pl.get("id"))] = {ESPN_STATS[k]: float(v) for k, v in (s.get("stats") or {}).items() if k in ESPN_STATS}
    return out


def _season_summary(pc: Any, weeks: list[dict[str, Any]], **kw: Any) -> list[tuple[int, float, float]]:
    ends = [i for i, w in enumerate(weeks) if i + 1 == len(weeks) or weeks[i + 1]["season"] != w["season"]]
    out = []
    for i in ends:
        card = pc.replay(copy.deepcopy(weeks[: i + 1]), **kw)
        o = card["overall"]
        out.append((card["season"], o["team_calibrated"]["mae"], o["calibrated"]["mae"]))
    return out


def _print(name: str, res: list[tuple[int, float, float]], base: list[tuple[int, float, float]] | None = None) -> None:
    parts = []
    for j, (season, team, player) in enumerate(res):
        d = f" ({team - base[j][1]:+.2f})" if base else ""
        parts.append(f"{season} team {team:.2f}{d} player {player:.3f}")
    tot = sum(t for _, t, _ in res) - (sum(t for _, t, _ in base) if base else 0)
    print(f"{name:<14} " + "; ".join(parts) + (f" | sum change {tot:+.2f}" if base else ""))


def run_league(client: Any, league_id: str, sleeper_league_id: str) -> None:
    from league_ai import projection_calibration as pc
    from league_ai.nfl_data import season_games, season_stat_rows
    from pipeline.league_history.run import league_chain
    from scoring.engine import player_points

    scoring = {int(l["season"]): l.get("scoring_settings") or {} for l in league_chain(sleeper_league_id) if l.get("season")}
    lineups = pc._lineups(client, league_id)
    weeks = []
    for season in sorted({s for s, _ in lineups}):
        sc = scoring.get(season) or next(iter(scoring.values()), {})
        stats_rows, games_all = season_stat_rows(client, season), season_games(client, season)
        done = sorted({int(g["week"]) for g in games_all if g.get("week") is not None} - {int(g["week"]) for g in games_all if g.get("home_score") is None})
        for week in sorted(w for s, w in lineups if s == season):
            if games_all and week not in done:
                continue
            rows = pc.week_rows(client, season, week, sc, stats_rows, games_all)
            if rows:
                weeks.append({"season": season, "week": week, "rows": rows, "lineups": lineups[(season, week)], "snapshot": None})
    base = _season_summary(pc, weeks)
    _print("today", base)

    # 1. matchup tilt strength
    for s in (0.0, 0.5, 1.5):
        ws = copy.deepcopy(weeks)
        for w in ws:
            for r in w["rows"]:
                tilt = r["shown"] / r["raw"] if r["raw"] else 1.0
                r["shown"] = r["raw"] * (1 + s * (tilt - 1))
        _print(f"tilt x{s}", _season_summary(pc, ws), base)

    # 2. recent weeks weigh more
    for d in (0.97, 0.94, 0.90):
        _print(f"decay {d}", _season_summary(pc, weeks, decay=d), base)

    # 3. our own usage projection
    usage: dict[tuple[int, int], dict[str, float]] = {}
    for season in sorted({w["season"] for w in weeks}):
        rows: list[dict[str, Any]] = []
        for start in range(0, 30000, 1000):
            chunk = (client.table("nfl_player_stats").select("sleeper_id, week, position, carries, targets, attempts").eq("season", season)
                     .eq("season_type", "REG").order("gsis_id").order("week").range(start, start + 999).execute().data or [])
            rows += chunk
            if len(chunk) < 1000:
                break
        for r in rows:
            if r.get("sleeper_id"):
                o = float(r.get("carries") or 0) + float(r.get("targets") or 0) + float(r.get("attempts") or 0)
                usage[(season, int(r["week"]))] = usage.get((season, int(r["week"])), {})
                usage[(season, int(r["week"]))][str(r["sleeper_id"])] = o
    own: dict[tuple[int, int, str], float] = {}
    rate: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])   # pos -> points, opportunities (all weeks before)
    past: dict[str, list[float]] = defaultdict(list)
    for w in weeks:
        u = usage.get((w["season"], w["week"]), {})
        for r in w["rows"]:
            p = past.get(r["id"], [])[-4:]
            if len(p) >= 2 and rate[r["pos"]][1] > 50:
                own[(w["season"], w["week"], r["id"])] = sum(p) / len(p) * rate[r["pos"]][0] / rate[r["pos"]][1]
        for r in w["rows"]:
            if r.get("actual") is not None and r["id"] in u and u[r["id"]] > 0:
                rate[r["pos"]][0] += r["actual"]
                rate[r["pos"]][1] += u[r["id"]]
                past[r["id"]].append(u[r["id"]])
    print(f"usage projection available for {len(own)} player-weeks")
    for wt in (0.1, 0.2, 0.3, 0.4):
        ws = copy.deepcopy(weeks)
        for w in ws:
            for r in w["rows"]:
                o = own.get((w["season"], w["week"], r["id"]))
                if o is not None and r["pos"] in ("QB", "RB", "WR", "TE"):
                    r["shown"] = (1 - wt) * r["shown"] + wt * o
        _print(f"usage {int(wt * 100)}%", _season_summary(pc, ws), base)

    # 4. ESPN projections
    from pipeline.nfl_data.run import load_crosswalk

    cw = load_crosswalk()
    espn: dict[tuple[int, int], dict[str, float]] = {}
    got = 0
    for w in weeks:
        if w["season"] < weeks[-1]["season"] - 1:
            continue
        lines = espn_week(w["season"], w["week"])
        sc = scoring.get(w["season"]) or {}
        m = {}
        for eid, st in lines.items():
            sid = cw.sleeper_for_espn(eid)
            if sid:
                m[sid] = st
        espn[(w["season"], w["week"])] = {sid: st for sid, st in m.items()}
        got += len(m)
        w["_sc"] = sc
    print(f"espn projected lines matched: {got} over {len(espn)} weeks")
    if got:
        for wt in (0.25, 0.5):
            ws = copy.deepcopy([w for w in weeks if w["season"] >= weeks[-1]["season"] - 1])
            n = 0
            for w in ws:
                e = espn.get((w["season"], w["week"]), {})
                for r in w["rows"]:
                    st = e.get(r["id"])
                    if st is not None and r["pos"] in ("QB", "RB", "WR", "TE"):
                        es = player_points(st, w["_sc"], r["pos"])
                        tilt = r["shown"] / r["raw"] if r["raw"] else 1.0
                        r["shown"] = ((1 - wt) * r["raw"] + wt * es) * tilt
                        n += 1
            b2 = _season_summary(pc, [w for w in weeks if w["season"] >= weeks[-1]["season"] - 1])
            _print(f"espn {int(wt * 100)}%", _season_summary(pc, ws), b2)
            print(f"  (blended {n} projections)")


def main() -> None:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    leagues = client.table("leagues").select("id, sleeper_league_id").execute().data or []
    lg = [l for l in leagues if str(l.get("sleeper_league_id")) == "1361083066197491712"] or leagues[-1:]
    run_league(client, lg[0]["id"], str(lg[0]["sleeper_league_id"]))


if __name__ == "__main__":
    main()
