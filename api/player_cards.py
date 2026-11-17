"""Player page cards added in the v3 page: this week's play call, role on the team, rest of season, past seasons.

Pure builders plus the reads they need. Everything here is shared by every league (NFL data only).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from api import cache
from league_ai import injury_outlook as io
from league_ai.nfl_data import team_code

OUT_TAGS = {"out", "ir", "injured reserve", "pup", "pup-r", "doubtful"}
POSITIONS = ("QB", "RB", "WR", "TE")
PLAYOFF_WEEKS = (15, 16, 17)


def _n(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _pct(v: Any) -> int | None:
    if v is None:
        return None
    x = _n(v)
    return int(round(x * 100 if x <= 1 else x))


# ---- this week's call ------------------------------------------------------------
REPORTED = {"positive": "Expected to play", "negative": "Not expected to play", "uncertain": "Up in the air"}


def play_call(h: dict[str, Any], pos: str, espn: dict[str, Any] | None, rates: dict, game_date: str | None) -> dict[str, Any] | None:
    """Chance he plays his next game (league history of the same tag, nudged by the news) and the three-way
    Official / Reported / Our call row. None for a healthy player or one on IR (the IR rows say it all)."""
    if h.get("healthy"):
        return None
    report = h.get("report") or {}
    if report.get("on_ir"):
        return None
    espn = espn or {}
    week = h.get("this_week") or {}
    days = week.get("days") or []
    practice = io.norm_practice((days[-1] if days else {}).get("practice")) or io.norm_practice((week.get("practice") or "").replace(" practice", ""))
    official = io.norm_status(week.get("game_status"))
    injury = io.injury_key(week.get("injury") or h.get("detail"))
    signal = io.comment_signal(espn.get("short_comment"), espn.get("long_comment"))
    out = io.play_chance(position=pos, injury=injury, official_status=official, official_practice=practice if official else None,
                         sleeper_status=h.get("status"), sleeper_practice=practice, espn_status=espn.get("status"),
                         return_date=espn.get("return_date"), game_date=game_date, comments=signal, rates=rates)
    if not out:
        return None
    p = float(out["probability"])
    scopes = ([f"injury:{injury}"] if injury else []) + [f"pos:{pos}", "all"]
    why = None
    if official:
        hit = io._rate(rates, scopes, official, practice)
        if hit:
            tag = f"listed {official}" + (f" after a {practice.lower()} practice" if practice and practice != "DNP" else " after missing practice" if practice == "DNP" else "")
            who = f"{pos}s" if hit[2].startswith("pos:") or hit[2].startswith("injury:") else "Players"
            injury_word = f" with a{'n' if (injury or ' ')[0] in 'aeiou' else ''} {injury} injury" if hit[2].startswith("injury:") and injury else ""
            why = f"{who}{injury_word} {tag} played {round(hit[0] * 100)}% of the time"
    elif practice:
        why = f"No game status yet. Practice so far: {', '.join(f'{d.get('day')} {d.get('practice')}' for d in days if d.get('practice')) or practice}"
    lean = (signal or {}).get("lean")
    verdict = out["verdict"]
    ours = "Sit him" if official == "Out" or p < 0.4 else "Start him" if p >= 0.65 else "Have a backup ready"
    return {
        "pct": round(p * 100),
        "verdict": verdict[0].upper() + verdict[1:],
        "why": why,
        "official": official or None,
        "reported": REPORTED.get(lean or ""),
        "reported_date": (str(espn.get("reported_at") or "")[:10]) or None,
        "ours": ours,
    }


def news_summary(espn: dict[str, Any] | None) -> dict[str, Any] | None:
    """The 'Where he's at' paragraph: ESPN's full write-up when there is one, else the short note."""
    espn = espn or {}
    text = str(espn.get("long_comment") or "").strip() or str(espn.get("short_comment") or "").strip()
    if not text:
        return None
    return {"text": text, "date": (str(espn.get("reported_at") or "")[:10]) or None, "source": "ESPN news"}


# ---- role on the team -----------------------------------------------------------
METRIC = {"QB": ("attempts", "pass attempts"), "RB": ("carries", "carry share"), "WR": ("targets", "target share"), "TE": ("targets", "target share")}


def role_on_team(team_rows: list[dict[str, Any]], player_id: str, pos: str, hurt: Iterable[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """His spot in the pecking order: the top three at his position group by share of the team's work, his snap share
    over the last three games, and teammates who are out this week."""
    if pos not in METRIC or not team_rows:
        return None
    col, label = METRIC[pos]
    group = ("WR", "TE") if pos in ("WR", "TE") else (pos,)
    team_total = sum(_n(r.get(col)) for r in team_rows)
    if team_total <= 0:
        return None
    by: dict[str, dict[str, Any]] = {}
    for r in team_rows:
        p = str(r.get("position") or "").upper()
        sid = str(r.get("sleeper_id") or r.get("gsis_id") or "")
        if p not in group or not sid:
            continue
        cur = by.setdefault(sid, {"id": sid, "name": r.get("player_name") or "", "pos": p, "total": 0.0})
        cur["total"] += _n(r.get(col))
    ranked = sorted(by.values(), key=lambda x: -x["total"])
    count: dict[str, int] = {}
    chips = []
    for x in ranked:
        count[x["pos"]] = count.get(x["pos"], 0) + 1
        x["slot"] = f"{x['pos']}{count[x['pos']]}"
    for x in ranked[:3]:
        chips.append({"slot": x["slot"], "name": _last(x["name"]), "share": round(100 * x["total"] / team_total), "me": x["id"] == str(player_id)})
    mine = next((x for x in ranked if x["id"] == str(player_id)), None)
    if mine and not any(c["me"] for c in chips):
        chips[-1] = {"slot": mine["slot"], "name": _last(mine["name"]), "share": round(100 * mine["total"] / team_total), "me": True}
    my_rows = sorted((r for r in team_rows if str(r.get("sleeper_id") or "") == str(player_id)), key=lambda r: int(r.get("week") or 0))
    snaps = [s for s in (_pct(r.get("offense_pct")) for r in my_rows[-3:]) if s is not None]
    rows = []
    if snaps:
        rows.append({"label": "Snap share (last 3)", "value": f"{round(sum(snaps) / len(snaps))}%"})
    if mine and pos != "QB":
        rows.append({"label": label[0].upper() + label[1:], "value": f"{round(100 * mine['total'] / team_total)}% · {_ordinal(ranked.index(mine) + 1)} on team"})
    if pos != "QB":
        tgt = sum(_n(r.get("targets")) for r in my_rows)
        if pos == "RB" and tgt:
            rows.append({"label": "Targets", "value": f"{tgt / max(1, len(my_rows)):.1f} a game"})
    shares = {x["id"]: x["total"] / team_total for x in by.values()}
    out = [f"{_last(t.get('full_name') or '')} ({t.get('position')})" for t in hurt
           if str(t.get("sleeper_id")) != str(player_id) and (shares.get(str(t.get("sleeper_id")), 0) >= 0.08 or str(t.get("position")) == "QB" and str(t.get("depth_chart_order") or "") == "1")]
    rows.append({"label": "Teammates out", "value": ", ".join(out[:3]) if out else "None this week"})
    return {"chips": chips, "rows": rows}


def _last(name: str) -> str:
    parts = str(name or "").replace(".", ". ").split()
    return parts[-1] if parts else ""


def load_team_rows(client: Any, season: int, team: str) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        return (client.table("nfl_player_stats").select("sleeper_id,gsis_id,player_name,position,week,attempts,carries,targets,offense_pct")
                .eq("season", season).eq("team", team).eq("season_type", "REG").limit(1000).execute().data or [])
    return cache.cached(("team_rows", season, team), 3600, load)


def load_team_hurt(client: Any, team: str) -> list[dict[str, Any]]:
    rows = (client.table("nfl_players").select("sleeper_id,full_name,position,injury_status,depth_chart_order")
            .eq("team", team).in_("position", list(POSITIONS)).limit(200).execute().data or [])
    return [r for r in rows if str(r.get("injury_status") or "").strip().lower() in OUT_TAGS]


# ---- rest of season -------------------------------------------------------------
def rest_of_season(games: list[dict[str, Any]], team: str, pos: str, dvp: dict, from_week: int | None,
                   playoff_weeks: Iterable[int] = PLAYOFF_WEEKS) -> dict[str, Any] | None:
    """His team's remaining games, each tagged easy / avg / tough for his position, and how his playoff weeks
    rank against every other team's (easiest = 1)."""
    if not team or not from_week or pos not in POSITIONS:
        return None
    team = team_code(team)
    playoff = set(playoff_weeks)
    sched: dict[str, dict[int, tuple[str, bool]]] = {}
    for g in games:
        if str(g.get("game_type") or "REG") != "REG":
            continue
        try:
            w = int(g["week"])
        except (KeyError, TypeError, ValueError):
            continue
        home, away = team_code(g.get("home_team")), team_code(g.get("away_team"))
        if home and away:
            sched.setdefault(home, {})[w] = (away, True)
            sched.setdefault(away, {})[w] = (home, False)

    def tone(opp: str) -> tuple[str | None, float | None]:
        r = dvp.get((opp, pos))
        if not r:
            return None, None
        rank, teams = int(r["rank"]), int(r.get("teams") or 32)
        return ("tough" if rank <= 10 else "easy" if rank > teams - 10 else "avg"), float(r.get("factor") or 1)

    mine = sched.get(team) or {}
    weeks = []
    for w in sorted(mine):
        if w < from_week:
            continue
        opp, home = mine[w]
        t, _ = tone(opp)
        weeks.append({"week": w, "opp": opp, "home": home, "tone": t, "playoff": w in playoff})
    if not weeks:
        return None
    ease = {}
    for t, s in sched.items():
        fs = [tone(s[w][0])[1] for w in playoff if w in s]
        fs = [f for f in fs if f is not None]
        if fs:
            ease[t] = sum(fs) / len(fs)
    rank = None
    if team in ease:
        rank = 1 + sum(1 for v in ease.values() if v > ease[team])
    return {"weeks": weeks, "playoff_rank": rank, "teams": len(ease) or None}


# ---- past seasons ---------------------------------------------------------------
COLUMNS = {
    "QB": [("cmp", "CMP", ("completions",)), ("yds", "YDS", ("passing_yards",)), ("td", "TD", ("passing_tds",)), ("int", "INT", ("interceptions",)), ("rush", "RUSH", ("rushing_yards",))],
    "RB": [("car", "CAR", ("carries",)), ("yds", "YDS", ("rushing_yards",)), ("rec", "REC", ("receptions",)), ("td", "TD", ("rushing_tds", "receiving_tds"))],
    "WR": [("rec", "REC", ("receptions",)), ("yds", "YDS", ("receiving_yards",)), ("td", "TD", ("receiving_tds", "rushing_tds"))],
}
COLUMNS["TE"] = COLUMNS["WR"]
RANKED_SEASONS = 4


def past_seasons(rows: list[dict[str, Any]], pos: str, ranks: dict[int, int] | None = None) -> dict[str, Any] | None:
    """One line per season (newest first): games, the position's main stats, PPR points a game and his finish at the position."""
    cols = COLUMNS.get(pos)
    if not cols or not rows:
        return None
    by: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(int(r["season"]), []).append(r)
    out = []
    for season in sorted(by, reverse=True):
        games = by[season]
        line = {"season": season, "games": len(games)}
        for key, _, src in cols:
            line[key] = round(sum(_n(r.get(c)) for r in games for c in src))
        ppr = sum(_n(r.get("fantasy_points_ppr")) for r in games)
        line["ppg"] = round(ppr / len(games), 1) if games else None
        line["finish"] = (ranks or {}).get(season)
        out.append(line)
    return {"columns": [{"key": k, "label": lab} for k, lab, _ in cols], "seasons": out}


def load_career(client: Any, player_id: str) -> list[dict[str, Any]]:
    cols = sorted({c for defs in COLUMNS.values() for _, _, src in defs for c in src})
    return (client.table("nfl_player_stats").select(",".join(["season", "week", "fantasy_points_ppr", *cols]))
            .eq("sleeper_id", player_id).eq("season_type", "REG").order("season", desc=True).limit(400).execute().data or [])


def season_finish(client: Any, season: int, pos: str, current: int) -> dict[str, int]:
    """sleeper_id -> finish at the position by total PPR points that season. Past seasons are kept a week."""
    def load() -> dict[str, int]:
        totals: dict[str, float] = {}
        for start in range(0, 20000, 1000):
            chunk = (client.table("nfl_player_stats").select("sleeper_id,fantasy_points_ppr").eq("season", season).eq("season_type", "REG")
                     .eq("position", pos).order("gsis_id").order("week").range(start, start + 999).execute().data or [])
            for r in chunk:
                if r.get("sleeper_id"):
                    totals[str(r["sleeper_id"])] = totals.get(str(r["sleeper_id"]), 0.0) + _n(r.get("fantasy_points_ppr"))
            if len(chunk) < 1000:
                break
        return {sid: i + 1 for i, sid in enumerate(sorted(totals, key=lambda s: -totals[s]))}
    return cache.cached(("finish", season, pos), 21600 if season >= current else 604800, load)


def finishes(client: Any, player_id: str, pos: str, seasons: Iterable[int], current: int) -> dict[int, int]:
    out = {}
    for s in sorted(set(seasons), reverse=True)[:RANKED_SEASONS]:
        try:
            rank = season_finish(client, s, pos, current).get(str(player_id))
        except Exception:
            rank = None
        if rank:
            out[s] = rank
    return out


def game_date(kickoff: str | None) -> str | None:
    if not kickoff:
        return None
    try:
        return date.fromisoformat(str(kickoff)[:10]).isoformat()
    except ValueError:
        return None
