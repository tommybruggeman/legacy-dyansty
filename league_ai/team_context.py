"""Team-level game context from the synced nfl_* tables: rest and byes, coaching tendencies, team defense.

Pure functions over nfl_games / nfl_player_stats rows plus thin cached readers. No model calls:
everything here becomes a few short lines of context the AI reasons from.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from league_ai.nfl_data import _cached, _ordinal, season_games, season_stat_rows, team_code

SHORT_WEEK_DAYS = 5   # Thursday after Sunday is 4 days
LONG_REST_DAYS = 10   # off a bye (or a Thursday game followed by a Sunday ten days later)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _team_games(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """team -> its games in week order, from that team's side: opponent, points for/against, coach, date."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        try:
            week = int(r.get("week"))
        except Exception:
            continue
        for side, other in (("home", "away"), ("away", "home")):
            team = team_code(r.get(f"{side}_team"))
            if not team:
                continue
            out.setdefault(team, []).append({
                "week": week, "day": _day(r.get("gameday")), "opponent": team_code(r.get(f"{other}_team")), "coach": r.get(f"{side}_coach"),
                "pf": r.get(f"{side}_score"), "pa": r.get(f"{other}_score"),
            })
    for games in out.values():
        games.sort(key=lambda g: g["week"])
    return out


def rest_info(rows: Iterable[Mapping[str, Any]], week: int) -> dict[str, dict[str, Any]]:
    """team -> days of rest before this week's game and whether it is coming off a bye or on a short week."""
    out: dict[str, dict[str, Any]] = {}
    for team, games in _team_games(rows).items():
        now = next((g for g in games if g["week"] == week), None)
        prev = [g for g in games if g["week"] < week]
        if not now or not prev or not now["day"] or not prev[-1]["day"]:
            continue
        days = (now["day"] - prev[-1]["day"]).days
        out[team] = {"rest_days": days, "off_bye": now["week"] - prev[-1]["week"] > 1, "short_week": days <= SHORT_WEEK_DAYS}
    return out


def team_defense(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """team -> points allowed per game in finished games and its rank (1 = fewest allowed)."""
    allowed: dict[str, list[float]] = {}
    for team, games in _team_games(rows).items():
        allowed[team] = [float(g["pa"]) for g in games if g["pa"] is not None and g["pf"] is not None]
    ppg = {t: sum(v) / len(v) for t, v in allowed.items() if v}
    return {t: {"pa_per_game": round(ppg[t], 1), "rank": i, "teams": len(ppg)} for i, t in enumerate(sorted(ppg, key=ppg.get), start=1)}


def team_tendencies(stat_rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """team -> play-calling profile from weekly player stats: pass rate, plays per game, who gets the targets."""
    acc: dict[str, dict[str, Any]] = {}
    for r in stat_rows:
        team = team_code(r.get("team"))
        if not team or r.get("week") is None:
            continue
        a = acc.setdefault(team, {"att": 0.0, "car": 0.0, "sacks": 0.0, "tgt": {"RB": 0.0, "WR": 0.0, "TE": 0.0}, "weeks": set()})
        pos = str(r.get("position") or "").upper()
        a["weeks"].add(int(r["week"]))
        a["att"] += float(r.get("attempts") or 0)
        a["sacks"] += float(r.get("sacks") or 0)
        a["car"] += float(r.get("carries") or 0)
        if pos in a["tgt"]:
            a["tgt"][pos] += float(r.get("targets") or 0)
    out: dict[str, dict[str, Any]] = {}
    for team, a in acc.items():
        g = len(a["weeks"])
        dropbacks = a["att"] + a["sacks"]
        plays = dropbacks + a["car"]
        targets = sum(a["tgt"].values())
        if not g or not plays:
            continue
        out[team] = {"games": g, "pass_rate": round(dropbacks / plays, 3), "plays_per_game": round(plays / g, 1),
                     "target_share": {p: round(v / targets, 2) for p, v in a["tgt"].items()} if targets else {}}
    if out:
        lg = sum(t["pass_rate"] for t in out.values()) / len(out)
        for t in out.values():
            t["league_pass_rate"] = round(lg, 3)
    return out


def defense_profile(stat_rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """defense -> pass and run defense from what opposing offenses did against it.

    Pass D: passing yards and EPA per dropback allowed (the secondary and pass rush together; individual
    cornerback data isn't in the synced tables), sacks per game. Run D: rushing yards allowed per game.
    Ranks are 1 = best defense.
    """
    acc: dict[str, dict[str, Any]] = {}
    for r in stat_rows:
        d = team_code(r.get("opponent"))
        if not d or r.get("week") is None:
            continue
        a = acc.setdefault(d, {"py": 0.0, "epa": 0.0, "db": 0.0, "sacks": 0.0, "ry": 0.0, "weeks": set()})
        a["weeks"].add(int(r["week"]))
        if str(r.get("position") or "").upper() == "QB":
            a["py"] += float(r.get("passing_yards") or 0)
            a["epa"] += float(r.get("passing_epa") or 0)
            a["db"] += float(r.get("attempts") or 0) + float(r.get("sacks") or 0)
            a["sacks"] += float(r.get("sacks") or 0)
        a["ry"] += float(r.get("rushing_yards") or 0)
    out: dict[str, dict[str, Any]] = {}
    for d, a in acc.items():
        g = len(a["weeks"])
        if not g or not a["db"]:
            continue
        out[d] = {"games": g, "pass_yds_pg": round(a["py"] / g, 1), "epa_per_dropback": round(a["epa"] / a["db"], 3), "sacks_pg": round(a["sacks"] / g, 1), "rush_yds_pg": round(a["ry"] / g, 1)}
    n = len(out)
    for key, best_low in (("pass_yds_pg", True), ("epa_per_dropback", True), ("sacks_pg", False), ("rush_yds_pg", True)):
        for i, d in enumerate(sorted(out, key=lambda t: out[t][key], reverse=not best_low), start=1):
            out[d][f"{key}_rank"] = i
    for d in out.values():
        d["teams"] = n
    return out


def defense_profile_text(p: Mapping[str, Any] | None) -> str | None:
    if not p:
        return None
    n = p["teams"]
    return (f"pass D {p['pass_yds_pg']:.0f} yds/g ({_ordinal(p['pass_yds_pg_rank'])} of {n}), EPA/dropback {p['epa_per_dropback']:+.2f} ({_ordinal(p['epa_per_dropback_rank'])}), "
            f"{p['sacks_pg']} sacks/g ({_ordinal(p['sacks_pg_rank'])}); run D {p['rush_yds_pg']:.0f} yds/g ({_ordinal(p['rush_yds_pg_rank'])})")


def coaches(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """team -> head coach in its latest game of the season."""
    return {team: games[-1]["coach"] for team, games in _team_games(rows).items() if games and games[-1].get("coach")}


def coach_after_bye(seasons_rows: Mapping[int, Iterable[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    """head coach -> results in the game right after a bye across the loaded seasons, vs that team's season scoring average."""
    out: dict[str, dict[str, Any]] = {}
    for season, rows in sorted(seasons_rows.items()):
        for team, games in _team_games(rows).items():
            done = [g for g in games if g["pf"] is not None and g["pa"] is not None]
            if len(done) < 4:
                continue
            avg = sum(float(g["pf"]) for g in done) / len(done)
            for prev, g in zip(games, games[1:]):
                if g["week"] - prev["week"] > 1 and g["pf"] is not None and g["pa"] is not None and g.get("coach"):
                    c = out.setdefault(g["coach"], {"games": 0, "wins": 0, "pts_vs_avg": 0.0, "seasons": set()})
                    c["games"] += 1
                    c["wins"] += 1 if g["pf"] > g["pa"] else 0
                    c["pts_vs_avg"] += float(g["pf"]) - avg
                    c["seasons"].add(season)
    return {coach: {"games": c["games"], "wins": c["wins"], "losses": c["games"] - c["wins"], "pts_vs_avg": round(c["pts_vs_avg"] / c["games"], 1), "since": min(c["seasons"])}
            for coach, c in out.items() if c["games"]}


def _pct(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "?"


def tendency_text(t: Mapping[str, Any] | None) -> str | None:
    if not t:
        return None
    share = t.get("target_share") or {}
    tilt = ""
    lg = t.get("league_pass_rate")
    if lg:
        diff = t["pass_rate"] - lg
        tilt = " (pass-heavy)" if diff >= 0.03 else " (run-heavy)" if diff <= -0.03 else " (balanced)"
    targets = "/".join(f"{p} {_pct(share.get(p))}" for p in ("WR", "TE", "RB") if p in share)
    return f"pass rate {_pct(t['pass_rate'])}{tilt}, {t['plays_per_game']} plays/g" + (f", targets {targets}" if targets else "")


class TeamContext:
    """Everything team-level for one week, built from cached reads: this season, last season, all loaded seasons for coach history."""

    def __init__(self, client: Any, season: int, week: int, history_seasons: int = 3):
        self.season, self.week = int(season), int(week)
        rows = season_games(client, self.season)
        last = season_games(client, self.season - 1)
        self.rest = rest_info(rows, self.week)
        self.defense = team_defense(rows)
        self.coaches = coaches(rows)
        self.last_coaches = coaches(last)
        stats = season_stat_rows(client, self.season)
        self.tendencies = team_tendencies(stats)
        self.defense_profiles = defense_profile(stats)
        self.last_tendencies = team_tendencies(season_stat_rows(client, self.season - 1)) if last else {}
        history = {self.season - i: season_games(client, self.season - i) for i in range(0, history_seasons + 1)}
        self.after_bye = coach_after_bye({s: r for s, r in history.items() if r})

    def __bool__(self) -> bool:  # an empty context (tables not loaded) isn't cached
        return bool(self.coaches or self.tendencies)

    def team_lines(self, teams: Iterable[str], games: Mapping[str, Mapping[str, Any]]) -> list[str]:
        """One line per NFL team: its offense profile and coach, rest, and the defense it faces this week."""
        out = []
        for team in sorted({t for t in teams if t}):
            g = games.get(team) or {}
            bits = [self.offense_text(team) or team]
            if self.rest_text(team):
                bits.append(self.rest_text(team))
            opp = g.get("opponent")
            if opp:
                bits.append("faces " + (self.defense_text(opp) or f"{opp} D"))
                if self.rest_text(opp):
                    bits.append(f"{opp} {self.rest_text(opp)}")
            out.append("; ".join(bits))
        return out

    def rest_text(self, team: str) -> str | None:
        r = self.rest.get(team)
        if not r:
            return None
        if r["off_bye"]:
            text = f"off a bye ({r['rest_days']} days rest)"
            coach = self.coaches.get(team)
            hist = self.after_bye.get(coach or "")
            if hist:
                text += f"; {coach} is {hist['wins']}-{hist['losses']} after byes since {hist['since']}, {hist['pts_vs_avg']:+.1f} pts vs his team's season average"
            return text
        if r["short_week"]:
            return f"short week ({r['rest_days']} days rest)"
        return None

    def offense_text(self, team: str) -> str | None:
        """'SEA, HC Macdonald: pass rate 58% (pass-heavy), 63 plays/g, targets WR 61%/TE 18%/RB 21%; last season 54% under the same HC'."""
        now = tendency_text(self.tendencies.get(team))
        if not now:
            return None
        coach, last_coach = self.coaches.get(team), self.last_coaches.get(team)
        text = f"{team}" + (f", HC {coach}" if coach else "") + f": {now}"
        last = self.last_tendencies.get(team)
        if last:
            who = "the same HC" if coach and coach == last_coach else (f"{last_coach} (coaching change)" if last_coach else "the prior staff")
            text += f"; last season {_pct(last['pass_rate'])} pass rate, {last['plays_per_game']} plays/g under {who}"
        return text

    def defense_text(self, team: str) -> str | None:
        d = self.defense.get(team)
        if not d:
            return None
        text = f"{team} D allows {d['pa_per_game']} pts/g ({_rank_word(d['rank'], d['teams'])})"
        prof = defense_profile_text(getattr(self, "defense_profiles", {}).get(team))
        return text + (f"; {prof}" if prof else "")

    def game_flags(self, team: str, opponent: str | None) -> dict[str, Any]:
        """Rest flags for the game dicts week_matchups returns (used by the scoring multiplier and the note)."""
        mine, theirs = self.rest.get(team) or {}, self.rest.get(opponent or "") or {}
        return {"off_bye": bool(mine.get("off_bye")), "short_week": bool(mine.get("short_week")), "rest_days": mine.get("rest_days"),
                "opp_off_bye": bool(theirs.get("off_bye")), "opp_short_week": bool(theirs.get("short_week")),
                "rest_text": self.rest_text(team), "opp_rest_text": self.rest_text(opponent) if opponent else None}

    def annotate(self, games: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        for team, g in games.items():
            g.update(self.game_flags(team, g.get("opponent")))
        return games


def _rank_word(rank: int, teams: int) -> str:
    return f"{_ordinal(rank)}-fewest of {teams}" if rank <= teams / 2 else f"{_ordinal(teams - rank + 1)}-most of {teams}"


def team_context(client: Any, season: int, week: int) -> TeamContext:
    """Cached per season and week; every input is a cached read too."""
    return _cached(("ctx", int(season), int(week)), lambda: TeamContext(client, season, week))


PASS_POSITIONS = {"QB", "WR", "TE"}


def schedule_outlook(rows: list[Mapping[str, Any]], team: str, position: str, from_week: int, through_week: int,
                     dvp: Mapping[tuple[str, str], Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]], playoff_start: int | None = None) -> dict[str, Any]:
    """Remaining-schedule strength for one player: each week's opponent graded against his position, then averaged.

    Uses this season's defense-vs-position and pass/run defense ranks. Future weather and Vegas lines aren't
    known yet, so they stay out; this is matchup quality only.
    """
    from league_ai.nfl_data import bye_weeks_from_games, matchup_label, matchup_multiplier, week_matchups

    team, pos = team_code(team), str(position or "").upper()
    bye = bye_weeks_from_games(list(rows)).get(team)
    weeks: list[str] = []
    mults: list[float] = []
    playoff_mults: list[float] = []
    soft = tough = 0
    for w in range(int(from_week), int(through_week) + 1):
        if w == bye:
            weeks.append(f"W{w} BYE")
            continue
        game = week_matchups(list(rows), w).get(team)
        if not game:
            continue
        opp = game["opponent"]
        rating = dvp.get((opp, pos))
        mult = matchup_multiplier(rating.get("factor") if rating else None)
        label = matchup_label(rating) or "unrated"
        soft += label == "soft"
        tough += label == "tough"
        prof = profiles.get(opp) or {}
        unit = (f"pass D {_ordinal(prof['pass_yds_pg_rank'])}" if pos in PASS_POSITIONS else f"run D {_ordinal(prof['rush_yds_pg_rank'])}") if prof else ""
        weeks.append(f"W{w} {'vs' if game['home'] else '@'} {opp} {label}" + (f" ({unit})" if unit else ""))
        mults.append(mult)
        if playoff_start and w >= playoff_start:
            playoff_mults.append(mult)

    def pct(xs: list[float]) -> str | None:
        return f"{(sum(xs) / len(xs) - 1) * 100:+.1f}%" if xs else None

    out: dict[str, Any] = {"weeks": weeks, "games": len(mults), "soft": soft, "tough": tough, "avg_matchup_effect": pct(mults)}
    if bye and bye >= from_week:
        out["bye_week"] = bye
    if playoff_start:
        out["fantasy_playoffs"] = {"weeks": f"{playoff_start}-{through_week}", "avg_matchup_effect": pct(playoff_mults), "games": len(playoff_mults)}
    return out
