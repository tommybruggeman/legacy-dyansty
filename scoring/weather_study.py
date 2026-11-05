"""How does weather move fantasy scoring, against the projection? Free data only, numbers only.

For every regular-season week 2021-2026: Sleeper's projected and real stat lines (scored with this league's rules),
and for every open-air game the real kickoff weather from Open-Meteo's free historical archive (first 3 hours:
average wind, gusts, temperature, rain and snow totals). Each player's real/projected ratio is normalized by his
position's season ratio, so the buckets show what weather adds on top of the usual miss.

Buckets per position: wind, gusts, rain, snow, temperature, a cold-weather team in heat and a warm or dome team in
the cold, and game totals against the Vegas total. Prints numbers only (repo and Actions logs are public).

    python -m scoring.weather_study
"""
from __future__ import annotations

import io
import os
from collections import defaultdict
from math import sqrt
from typing import Any

SEASONS = list(range(2021, 2027))
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ALIASES = {"Heinz Field": "Acrisure Stadium", "FedExField": "Northwest Stadium", "FedEx Field": "Northwest Stadium",
           "Paul Brown Stadium": "Paycor Stadium", "TIAA Bank Stadium": "EverBank Stadium", "TIAA Bank Field": "EverBank Stadium",
           "FirstEnergy Stadium": "Huntington Bank Field", "Cleveland Browns Stadium": "Huntington Bank Field",
           "Arrowhead Stadium": "GEHA Field at Arrowhead Stadium", "GEHA Field at Arrowhead": "GEHA Field at Arrowhead Stadium",
           "Bills Stadium": "Highmark Stadium", "New Era Field": "Highmark Stadium", "Empower Field": "Empower Field at Mile High",
           "Broncos Stadium at Mile High": "Empower Field at Mile High", "Lambeau field": "Lambeau Field",
           "Commanders Field": "Northwest Stadium", "Cleveland Stadium": "Huntington Bank Field"}
SKILL = ("QB", "RB", "WR", "TE", "K")


def _games() -> list[dict[str, Any]]:
    import pandas as pd
    import requests

    from pipeline.nfl_data.games import GAMES_URL

    df = pd.read_csv(io.StringIO(requests.get(GAMES_URL, timeout=120).text), low_memory=False)
    df = df[(df["season"].isin(SEASONS)) & (df["game_type"] == "REG")]
    return df.where(df.notna(), None).to_dict("records")


def _weather(games: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """game_id -> kickoff weather (open-air games at known venues)."""
    import requests

    from league_ai.nfl_data import OPEN_AIR_VENUES

    by_venue: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for g in games:
        name = ALIASES.get(str(g.get("stadium") or ""), str(g.get("stadium") or ""))
        if name in OPEN_AIR_VENUES and str(g.get("roof") or "") in ("outdoors", "open") and g.get("gameday") and g.get("gametime"):
            by_venue[(name, int(g["season"]))].append(g)
    out: dict[str, dict[str, float]] = {}
    for (name, season), gs in by_venue.items():
        lat, lon = OPEN_AIR_VENUES[name]
        days = sorted(str(g["gameday"]) for g in gs)
        try:
            h = requests.get(ARCHIVE_URL, params={"latitude": lat, "longitude": lon, "start_date": days[0], "end_date": days[-1],
                                                  "hourly": "temperature_2m,rain,snowfall,wind_speed_10m,wind_gusts_10m",
                                                  "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
                                                  "timezone": "America/New_York"}, timeout=60).json().get("hourly") or {}
        except Exception:
            continue
        times = h.get("time") or []
        for g in gs:
            start = f"{g['gameday']}T{str(g['gametime'])[:2]}:00"
            if start not in times:
                continue
            i = times.index(start)
            w = slice(i, i + 3)
            vals = {k: [x for x in (h.get(k) or [])[w] if x is not None] for k in ("temperature_2m", "rain", "snowfall", "wind_speed_10m", "wind_gusts_10m")}
            if not vals["temperature_2m"] or not vals["wind_speed_10m"]:
                continue
            out[str(g["game_id"])] = {"temp": sum(vals["temperature_2m"]) / len(vals["temperature_2m"]), "rain": sum(vals["rain"]),
                                      "snow": sum(vals["snowfall"]), "wind": sum(vals["wind_speed_10m"]) / len(vals["wind_speed_10m"]),
                                      "gust": max(vals["wind_gusts_10m"] or [0.0])}
    return out


def _climate(games: list[dict[str, Any]], wx: dict[str, dict[str, float]]) -> dict[str, str]:
    """team -> 'dome' (home roof closed/dome), 'cold' (open-air home averaging under 45F in Nov-Jan) or 'warm'."""
    roofs: dict[str, list[str]] = defaultdict(list)
    temps: dict[str, list[float]] = defaultdict(list)
    for g in games:
        roofs[str(g["home_team"])].append(str(g.get("roof") or ""))
        w = wx.get(str(g["game_id"]))
        if w and str(g.get("gameday") or "")[5:7] in ("11", "12", "01"):
            temps[str(g["home_team"])].append(w["temp"])
    out = {}
    for t, rs in roofs.items():
        if sum(r in ("dome", "closed") for r in rs) > len(rs) / 2:
            out[t] = "dome"
        else:
            tt = temps.get(t) or []
            out[t] = "cold" if tt and sum(tt) / len(tt) < 45 else "warm"
    return out


def main() -> None:
    from api.projections import POSITIONS, _rows
    from league_ai.start_sit_grade import _sleeper
    from pipeline.league_history.run import league_chain
    from scoring.engine import player_points

    games = _games()
    wx = _weather(games)
    climate = _climate(games, wx)
    print(f"games {len(games)}, with kickoff weather {len(wx)}; team climates {dict((k, sum(v == k for v in climate.values())) for k in ('dome', 'cold', 'warm'))}")
    sc = league_chain("1361083066197491712")[0].get("scoring_settings") or {}
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)
    game_of: dict[tuple[int, int, str], dict[str, Any]] = {}
    for g in games:
        for side, opp in (("home_team", "away_team"), ("away_team", "home_team")):
            game_of[(int(g["season"]), int(g["week"]), str(g[side]))] = {**g, "team": g[side], "opp": g[opp], "is_home": side == "home_team"}
    recs = []
    played_weeks: dict[tuple[int, str], set[int]] = defaultdict(set)
    for season in SEASONS:
        for week in range(1, 19):
            try:
                proj = _rows(_sleeper(f"projections/nfl/{season}/{week}?{query}"))
                real = _rows(_sleeper(f"stats/nfl/{season}/{week}?{query}"))
            except Exception:
                continue
            if not real:
                continue
            for pid, line in proj.items():
                pos = line.get("pos")
                if pos not in SKILL:
                    continue
                p = player_points(line.get("stats"), sc, pos)
                if p < 3:
                    continue
                g = game_of.get((season, week, str(line.get("team") or "").replace("LAR", "LA")))
                if not g:
                    continue
                r = real.get(pid) or {}
                a = player_points(r.get("stats"), sc, pos) if r else 0.0
                played = bool((r.get("stats") or {}).get("gp") or (r.get("stats") or {}).get("off_snp") or (pos == "K" and a))
                if not played:
                    continue   # weather doesn't explain inactives
                recs.append({"season": season, "week": week, "pid": pid, "pos": pos, "p": p, "a": a, "g": g, "wx": wx.get(str(g["game_id"])),
                             "team": str(g["team"])})
            for pid, r in real.items():
                st = r.get("stats") or {}
                if st.get("gp") or st.get("off_snp"):
                    played_weeks[(season, pid)].add(week)
    # normalize by season-position ratio
    ratio: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for x in recs:
        ratio[(x["season"], x["pos"])][0] += x["a"]
        ratio[(x["season"], x["pos"])][1] += x["p"]
    for x in recs:
        a, b = ratio[(x["season"], x["pos"])]
        x["exp"] = x["p"] * a / b
    print(f"player-games {len(recs)}, outdoor with weather {sum(1 for x in recs if x['wx'])}")

    def show(label: str, sel: list[dict[str, Any]]) -> None:
        if len(sel) < 25:
            print(f"    {label:<26} n={len(sel)} (too few)")
            return
        a, e = sum(x["a"] for x in sel), sum(x["exp"] for x in sel)
        rel = [(x["a"] - x["exp"]) for x in sel]
        sd = sqrt(sum(v * v for v in rel) / len(rel))
        se_pct = sd * sqrt(len(rel)) / e * 100 if e else 0
        print(f"    {label:<26} n={len(sel):<6} scored {100 * a / e - 100:+5.1f}% vs projection (+-{se_pct:.1f}%)")

    for pos in SKILL + ("ALL",):
        rs = [x for x in recs if pos == "ALL" or x["pos"] == pos]
        print(f"  {pos}")
        show("dome / closed roof", [x for x in rs if not x["wx"] and str(x["g"].get("roof")) in ("dome", "closed")])
        out = [x for x in rs if x["wx"]]
        show("outdoors (all)", out)
        for lo, hi in ((0, 8), (8, 12), (12, 16), (16, 20), (20, 99)):
            show(f"wind {lo}-{hi} mph", [x for x in out if lo <= x["wx"]["wind"] < hi])
        show("gusts 35+ mph", [x for x in out if x["wx"]["gust"] >= 35])
        show("dry", [x for x in out if x["wx"]["rain"] < 0.02 and x["wx"]["snow"] < 0.1])
        show("light rain (0.02-0.15 in)", [x for x in out if 0.02 <= x["wx"]["rain"] < 0.15 and x["wx"]["snow"] < 0.1])
        show("steady rain (0.15+ in)", [x for x in out if x["wx"]["rain"] >= 0.15])
        show("snow", [x for x in out if x["wx"]["snow"] >= 0.1])
        for lo, hi in ((-50, 25), (25, 33), (33, 45), (45, 80), (80, 88), (88, 150)):
            show(f"temp {lo}-{hi}F", [x for x in out if lo <= x["wx"]["temp"] < hi])
        show("cold team in 85F+", [x for x in out if x["wx"]["temp"] >= 85 and climate.get(x["team"]) == "cold"])
        show("warm/dome team in <=35F", [x for x in out if x["wx"]["temp"] <= 35 and climate.get(x["team"]) in ("dome", "warm")])
        show("cold team in <=35F", [x for x in out if x["wx"]["temp"] <= 35 and climate.get(x["team"]) == "cold"])
        show("wind 15+ and rain/snow", [x for x in out if x["wx"]["wind"] >= 15 and (x["wx"]["rain"] >= 0.05 or x["wx"]["snow"] >= 0.1)])
    situations(recs, games, played_weeks, show)
    fit_effects(recs, games)
    # does Vegas already price weather? real game total vs the Vegas total
    print("  game totals vs Vegas total")
    tot = defaultdict(list)
    for g in games:
        if g.get("total_line") is None or g.get("home_score") is None:
            continue
        w = wx.get(str(g["game_id"]))
        diff = float(g["home_score"]) + float(g["away_score"]) - float(g["total_line"])
        if not w:
            tot["dome/unknown"].append(diff)
            continue
        tot["outdoors"].append(diff)
        if w["wind"] >= 15:
            tot["wind 15+"].append(diff)
        if w["rain"] >= 0.15:
            tot["steady rain"].append(diff)
        if w["snow"] >= 0.1:
            tot["snow"].append(diff)
        if w["temp"] < 25:
            tot["under 25F"].append(diff)
        if w["temp"] >= 85:
            tot["85F+"].append(diff)
    for k, v in tot.items():
        print(f"    {k:<14} n={len(v):<4} real minus Vegas total {sum(v) / len(v):+.2f} pts")


TRAIN = (2021, 2022, 2023, 2024, 2025)
RIDGE = 8000.0   # about 80 typical player-games of evidence at zero effect


def _fit(rows: list[dict[str, Any]], names: list[str]) -> dict[str, float]:
    """Ridge fit of (actual - expected) on expected x feature: the share of the projection each condition adds."""
    import numpy as np

    if not rows or not names:
        return {}
    X = np.array([[r["e"] * r["f"].get(n, 0.0) for n in names] for r in rows])
    y = np.array([r["a"] - r["e"] for r in rows])
    b = np.linalg.solve(X.T @ X + RIDGE * np.eye(len(names)), X.T @ y)
    return dict(zip(names, (float(v) for v in b)))


def _mae(rows: list[dict[str, Any]], eff: dict[str, dict[str, float]] | None) -> tuple[float, float]:
    """Player MAE and NFL-team-week MAE (each team's projected players summed)."""
    from league_ai.game_conditions import multiplier

    err, team = 0.0, defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        pred = r["e"] * (multiplier(r["pos"], r["f"], eff) if eff else 1.0)
        err += abs(r["a"] - pred)
        t = team[(r["season"], r["week"], r["team"])]
        t[0] += r["a"]
        t[1] += pred
    return err / max(1, len(rows)), sum(abs(a - b) for a, b in team.values()) / max(1, len(team))


def fit_effects(recs: list[dict[str, Any]], games: list[dict[str, Any]]) -> None:
    """Fit every condition's effect by position (league_ai.game_conditions.features), check it season by season
    out of sample, drop conditions that don't hold up, and print the table for COND_EFFECTS."""
    from league_ai import game_conditions as gc

    climate = gc.team_climates(games)
    situ: dict[int, dict[tuple[int, str], dict[str, float]]] = {}
    for season in SEASONS:
        situ[season] = gc.situations([g for g in games if int(g["season"]) == season])
    rows = []
    for x in recs:
        g = x["g"]
        f = gc.features(g, x["team"], climate, x["wx"], situ[x["season"]].get((x["week"], x["team"])))
        rows.append({"season": x["season"], "week": x["week"], "team": x["team"], "pos": x["pos"], "a": x["a"], "p": x["p"], "f": f})
    # the expected score: projection x the position's ratio from the OTHER seasons (no peeking at the test season)
    tot: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        tot[(r["season"], r["pos"])][0] += r["a"]
        tot[(r["season"], r["pos"])][1] += r["p"]

    def expect(test: int) -> None:
        for r in rows:
            a = sum(v[0] for (s, p), v in tot.items() if p == r["pos"] and s != test)
            b = sum(v[1] for (s, p), v in tot.items() if p == r["pos"] and s != test)
            r["e"] = r["p"] * a / b if b else r["p"]

    names = sorted({k for r in rows for k in r["f"]})

    def table(train: list[dict[str, Any]], use: list[str]) -> dict[str, dict[str, float]]:
        eff: dict[str, dict[str, float]] = defaultdict(dict)
        for pos in gc.POSITIONS:
            for n, b in _fit([r for r in train if r["pos"] == pos], use).items():
                eff[n][pos] = b
        return dict(eff)

    def loso(use: list[str]) -> list[tuple[int, float, float]]:
        out = []
        for test in SEASONS[1:]:
            expect(test)
            tr = [r for r in rows if r["season"] != test]
            te = [r for r in rows if r["season"] == test]
            eff = table(tr, use)
            b, w = _mae(te, None), _mae(te, eff)
            out.append((test, b[0] - w[0], b[1] - w[1]))
        return out

    print("  FIT (share of the projection per unit; LOSO = each season predicted from the other five)")
    full = loso(names)
    print("    all conditions: " + ", ".join(f"{s} player {p:+.3f} team {t:+.2f}" for s, p, t in full))
    keep, cur = list(names), full
    for rnd in (1, 2):   # drop a condition unless team totals get worse without it (and players don't get better)
        for n in list(keep):
            without = loso([k for k in keep if k != n])
            dt = sum(t for _, _, t in without) - sum(t for _, _, t in cur)
            dp = sum(p for _, p, _ in without) - sum(p for _, p, _ in cur)
            verdict = "drop" if dt >= 0 or dp > 0.002 else "keep"
            print(f"    round {rnd} {n:<18} n={sum(1 for r in rows if n in r['f']):<6} without it: player {dp:+.4f} team {dt:+.3f} -> {verdict}")
            if verdict == "drop":
                keep.remove(n)
                cur = without
    final = loso(keep)
    print("    kept: " + ", ".join(f"{s} player {p:+.3f} team {t:+.2f}" for s, p, t in final))
    expect(2026)
    eff = table([r for r in rows if r["season"] in TRAIN], keep)
    te = [r for r in rows if r["season"] == 2026]
    b, w = _mae(te, None), _mae(te, eff)
    print(f"    2026 out of sample (fit 2021-2025): player MAE {b[0]:.3f} -> {w[0]:.3f}, team-week MAE {b[1]:.2f} -> {w[1]:.2f}")
    expect(0)
    eff = table(rows, keep)
    print("  COND_EFFECTS (fit on all seasons):")
    print("  {")
    for n in keep:
        print(f'      "{n}": {{' + ", ".join(f'"{p}": {round(v, 3)}' for p, v in sorted(eff.get(n, {}).items())) + "},")
    print("  }")


WEST = {"SEA", "SF", "LA", "LAC", "LV", "ARI"}
EAST = {"NE", "NYJ", "NYG", "BUF", "MIA", "PHI", "PIT", "BAL", "WAS", "CAR", "ATL", "TB", "JAX", "CLE", "CIN", "DET", "IND"}


def situations(recs: list[dict[str, Any]], games: list[dict[str, Any]], played_weeks: dict[tuple[int, str], set[int]], show: Any) -> None:
    """Game situations against the projection, by position."""
    # each team's usual QB and head coach so far in the season, and first meetings between division rivals
    qb_seen: dict[tuple[int, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    coach_first: dict[tuple[int, str], str] = {}
    met: set[tuple[int, str, str]] = set()
    tags: dict[tuple[int, int, str], set[str]] = defaultdict(set)
    for g in sorted(games, key=lambda g: (int(g["season"]), int(g["week"]))):
        season, week = int(g["season"]), int(g["week"])
        for side, opp in (("home", "away"), ("away", "home")):
            team, other = str(g[f"{side}_team"]), str(g[f"{opp}_team"])
            t = tags[(season, week, team)]
            rest = g.get(f"{side}_rest")
            if rest is not None:
                rest = float(rest)
                if rest <= 5:
                    t.add("short rest (5 days or less)")
                elif rest >= 13:
                    t.add("off a bye")
                if g.get(f"{opp}_rest") is not None and float(g[f"{opp}_rest"]) - rest >= 3:
                    t.add("rest disadvantage 3+ days")
            if str(g.get("location") or "") == "Neutral":
                t.add("international / neutral site")
            if side == "away" and team in WEST and str(g["home_team"]) in EAST and str(g.get("gametime") or "")[:2] in ("09", "13"):
                t.add("west coast team, early east kickoff")
            if side == "away" and str(g["home_team"]) == "DEN":
                t.add("visiting Denver altitude")
            if side == "away":
                t.add("away")
            else:
                t.add("home")
            if g.get("div_game"):
                key = (season, *sorted((team, other)))
                t.add("division rematch" if key in met else "division first meeting")
            if week >= 17:
                t.add("weeks 17-18")
            surf = str(g.get("surface") or "").lower()
            if surf:
                t.add("grass" if "grass" in surf else "turf")
            qb = str(g.get(f"{side}_qb_id") or "")
            seen = qb_seen[(season, team)]
            if qb and seen and max(seen, key=seen.get) != qb and sum(seen.values()) >= 2:
                t.add("different starting QB than usual")
            if qb:
                seen[qb] += 1
            coach = str(g.get(f"{side}_coach") or "")
            if coach:
                first = coach_first.setdefault((season, team), coach)
                if coach != first:
                    t.add("new head coach mid-season")
        if g.get("div_game"):
            met.add((season, *sorted((str(g["home_team"]), str(g["away_team"])))))
    ref_avg: dict[str, list[float]] = defaultdict(list)
    for g in games:
        if g.get("referee") and g.get("home_score") is not None and g.get("total_line") is not None:
            ref_avg[str(g["referee"])].append(float(g["home_score"]) + float(g["away_score"]) - float(g["total_line"]))
    for x in recs:
        t = tags.get((x["season"], x["week"], x["team"]), set())
        x["tags"] = set(t)
        weeks = played_weeks.get((x["season"], x["pid"]), set())
        prev = [w for w in weeks if w < x["week"]]
        if prev and x["week"] - max(prev) >= 3:
            x["tags"].add("first game back after 2+ missed weeks")
        elif prev and x["week"] - max(prev) == 2 and "off a bye" not in t:
            x["tags"].add("first game back after 1 missed week")
    names = sorted({k for x in recs for k in x["tags"]})
    print("  SITUATIONS")
    for pos in SKILL + ("ALL",):
        rs = [x for x in recs if pos == "ALL" or x["pos"] == pos]
        print(f"  {pos}")
        for n in names:
            show(n, [x for x in rs if n in x["tags"]])


if __name__ == "__main__":
    main()
