"""Team-level game context from the synced nfl_* tables: rest and byes, coaching tendencies, team defense.

Pure functions over nfl_games / nfl_player_stats rows plus thin cached readers. No model calls:
everything here becomes a few short lines of context the AI reasons from.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from league_ai.nfl_data import _cached, _ordinal, season_games, season_stat_rows, situational_splits, team_code

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


MIN_BLITZED_DROPBACKS = 30


def _blend(cur: Mapping[str, Any] | None, last: Mapping[str, Any] | None, weight_key: str, keys: tuple[str, ...]) -> dict[str, Any] | None:
    """Weighted mix of this season's and last season's split, by sample size; this season alone once it is big enough."""
    if not cur and not last:
        return None
    a, b = dict(cur or {}), dict(last or {})
    wa, wb = float(a.get(weight_key) or 0), float(b.get(weight_key) or 0)
    if wa >= 2 * MIN_BLITZED_DROPBACKS or not wb:
        a["sample"] = "this season"
        return a
    if not wa:
        b["sample"] = "last season"
        return b
    out = dict(a)
    for k in keys:
        if a.get(k) is not None and b.get(k) is not None:
            out[k] = round((a[k] * wa + b[k] * wb) / (wa + wb), 3)
    out[weight_key] = int(wa + wb)
    out["sample"] = "this season and last"
    return out


class Situational:
    """Blitz, pressure and box-count tendencies (nfl_situational_splits), this season blended with last."""

    def __init__(self, cur: Mapping[tuple[str, str], Mapping[str, Any]], last: Mapping[tuple[str, str], Mapping[str, Any]]):
        self.cur, self.last = cur, last
        defenses = {k[1]: v for k, v in cur.items() if k[0] == "defense"}
        self.defenses = defenses
        rates = [d.get("blitz_rate") for d in defenses.values() if d.get("blitz_rate") is not None]
        self.league_blitz = sum(rates) / len(rates) if rates else None
        self.blitz_rank = {t: i for i, t in enumerate(sorted(defenses, key=lambda t: -(defenses[t].get("blitz_rate") or 0)), start=1)}
        self.ranks: dict[str, dict[str, int]] = {}
        for key in ("allowed_deep_rate", "rb_targets_allowed_pg", "allowed_te_target_share", "allowed_adot"):
            have = [t for t in defenses if defenses[t].get(key) is not None]
            self.ranks[key] = {t: i for i, t in enumerate(sorted(have, key=lambda t: -defenses[t][key]), start=1)}  # 1 = allows the most
        self.coverage = {k[1]: v for k, v in cur.items() if k[0] == "defense" and v.get("man_rate") is not None}
        self.coverage_season = "this season"
        if not self.coverage:
            self.coverage = {k[1]: v for k, v in last.items() if k[0] == "defense" and v.get("man_rate") is not None}
            self.coverage_season = "last season"
        cov = list(self.coverage.values())
        self.league_man = sum(v["man_rate"] for v in cov) / len(cov) if cov else None
        self.league_two_high = sum(v.get("two_high_rate") or 0 for v in cov) / len(cov) if cov else None
        self.qb_by_team: dict[str, str] = {}
        for (kind, key), m in sorted(cur.items(), key=lambda kv: -(kv[1].get("dropbacks") or 0)):
            if kind == "qb" and m.get("team") and m["team"] not in self.qb_by_team:
                self.qb_by_team[m["team"]] = key

    def __bool__(self) -> bool:
        return bool(self.defenses)

    def qb(self, gsis: str | None) -> dict[str, Any] | None:
        if not gsis:
            return None
        return _blend(self.cur.get(("qb", gsis)), self.last.get(("qb", gsis)), "blitzed", ("epa_vs_blitz", "epa_no_blitz", "sack_rate_vs_blitz", "sack_rate_no_blitz"))

    def rb(self, gsis: str | None) -> dict[str, Any] | None:
        if not gsis:
            return None
        return _blend(self.cur.get(("rb", gsis)), self.last.get(("rb", gsis)), "carries_stacked", ("ypc_stacked", "ypc_light", "epa_stacked", "epa_light"))

    def coverage_lean(self, team: str) -> tuple[str | None, str | None]:
        """('man' | 'zone' | None, 'two_high' | 'single_high' | None): what the defense leans on vs league average."""
        c = self.coverage.get(team)
        if not c or self.league_man is None:
            return None, None
        shell = None
        if c.get("two_high_rate") is not None and self.league_two_high is not None:
            shell = "two_high" if c["two_high_rate"] >= self.league_two_high + 0.08 else "single_high" if c["two_high_rate"] <= self.league_two_high - 0.08 else None
        man = "man" if c["man_rate"] >= self.league_man + 0.08 else "zone" if c["man_rate"] <= self.league_man - 0.08 else None
        return man, shell

    def style_text(self, team: str) -> str | None:
        """How the defense defends the pass: depth allowed, dump-offs, coverage shell."""
        d = self.defenses.get(team)
        if not d:
            return None
        n = len(self.defenses)
        bits = []
        if d.get("allowed_adot") is not None:
            bits.append(f"throws against it average {d['allowed_adot']} air yards, deep shots {(d.get('allowed_deep_rate') or 0) * 100:.0f}% ({_ordinal(self.ranks['allowed_deep_rate'].get(team, 0))} most of {n}), "
                        f"EPA allowed {d.get('epa_deep_allowed') or 0:+.2f} deep / {d.get('epa_short_allowed') or 0:+.2f} short")
        if d.get("rb_targets_allowed_pg") is not None:
            bits.append(f"RB targets allowed {d['rb_targets_allowed_pg']}/g ({_ordinal(self.ranks['rb_targets_allowed_pg'].get(team, 0))} most), TE share {(d.get('allowed_te_target_share') or 0) * 100:.0f}% ({_ordinal(self.ranks['allowed_te_target_share'].get(team, 0))} most)")
        c = self.coverage.get(team)
        if c:
            bits.append(f"{self.coverage_season}: man {c['man_rate'] * 100:.0f}% (league {self.league_man * 100:.0f}%), two-high safeties {(c.get('two_high_rate') or 0) * 100:.0f}% (league {(self.league_two_high or 0) * 100:.0f}%)")
        return "; ".join(bits) or None

    def _coverage_note(self, split: Mapping[str, Any] | None, opponent: str, who: str) -> str | None:
        """'vs two-high last season -0.12 EPA (vs +0.05 single-high)' for the shell and man/zone the opponent leans on."""
        if not split:
            return None
        man, shell = self.coverage_lean(opponent)
        bits = []
        for lean, other in ((shell, {"two_high": "single_high", "single_high": "two_high"}.get(shell or "")), (man, {"man": "zone", "zone": "man"}.get(man or ""))):
            if lean and split.get(f"epa_vs_{lean}") is not None and split.get(f"epa_vs_{other}") is not None and (split.get(f"plays_vs_{lean}") or 0) >= 25:
                bits.append(f"{lean.replace('_', '-')} {split[f'epa_vs_{lean}']:+.2f} EPA vs {split[f'epa_vs_{other}']:+.2f} {other.replace('_', '-')} ({split.get(f'plays_vs_{lean}')} plays)")
        if not bits:
            return None
        return f"{opponent} leans {' and '.join(x for x in (shell, man) if x).replace('_', '-')}; {who} vs " + "; vs ".join(bits) + f" ({split.get('_season', 'last season')})"

    def _with_coverage(self, kind: str, gsis: str | None) -> dict[str, Any] | None:
        """This season's coverage split if charted yet, else last season's."""
        for season_rows, label in ((self.cur, "this season"), (self.last, "last season")):
            row = season_rows.get((kind, gsis or ""))
            if row and row.get("plays_vs_man") is not None:
                return {**row, "_season": label}
        return None

    def defense_text(self, team: str) -> str | None:
        d = self.defenses.get(team)
        if not d or d.get("blitz_rate") is None:
            return None
        n = len(self.defenses)
        style = self.style_text(team)
        return (f"{team} blitzes {d['blitz_rate'] * 100:.0f}% of dropbacks ({_ordinal(self.blitz_rank[team])} most of {n}), pressure {(d.get('pressure_rate') or 0) * 100:.0f}%, "
                f"EPA allowed {d.get('epa_allowed_blitz') or 0:+.2f} blitzing / {d.get('epa_allowed_no_blitz') or 0:+.2f} not; 8+ box on {(d.get('stacked_box_rate') or 0) * 100:.0f}% of runs"
                + (f"; {style}" if style else ""))

    def _qb_for(self, position: str, team: str | None, gsis: str | None) -> tuple[str | None, dict[str, Any] | None]:
        key = gsis if position == "QB" else self.qb_by_team.get(team or "")
        return key, self.qb(key)

    def multiplier(self, position: str, team: str | None, gsis: str | None, opponent: str | None) -> float:
        """A QB who falls apart against the blitz, facing a blitz-heavy defense, loses up to 4% (gains up to 4% if he feasts on it).

        His receivers get half that effect; running backs none (their box-count data is too thin to move a score).
        """
        pos = str(position or "").upper()
        if pos not in ("QB", "WR", "TE") or not opponent or self.league_blitz is None:
            return 1.0
        d = self.defenses.get(opponent)
        _, q = self._qb_for(pos, team, gsis)
        if not d or not q or d.get("blitz_rate") is None or q.get("epa_vs_blitz") is None or q.get("epa_no_blitz") is None or (q.get("blitzed") or 0) < MIN_BLITZED_DROPBACKS:
            return 1.0
        effect = (q["epa_vs_blitz"] - q["epa_no_blitz"]) * (d["blitz_rate"] - self.league_blitz)
        effect = max(-0.04, min(0.04, effect))
        return 1 + (effect if pos == "QB" else effect / 2)

    def note(self, position: str, team: str | None, gsis: str | None, opponent: str | None) -> str | None:
        pos = str(position or "").upper()
        if not opponent:
            return None
        d = self.defenses.get(opponent)
        if pos in ("QB", "WR", "TE"):
            key, q = self._qb_for(pos, team, gsis)
            if not d or not q or d.get("blitz_rate") is None or q.get("epa_vs_blitz") is None:
                return None
            who = "" if pos == "QB" else f"his QB {q.get('name') or ''} "
            text = (f"{opponent} blitzes {d['blitz_rate'] * 100:.0f}% ({_ordinal(self.blitz_rank.get(opponent, 0))} most); {who}vs blitz {q['epa_vs_blitz']:+.2f} EPA/dropback, "
                    f"{q.get('epa_no_blitz') or 0:+.2f} not blitzed, sacked {(q.get('sack_rate_vs_blitz') or 0) * 100:.0f}% when blitzed ({q.get('blitzed')} blitzed dropbacks, {q['sample']})")
            if pos == "QB":
                if q.get("epa_deep") is not None and d.get("allowed_deep_rate") is not None:
                    text += (f"; he averages {q.get('adot')} air yards, {q['epa_deep']:+.2f} EPA on deep throws / {q.get('epa_short') or 0:+.2f} short, "
                             f"and {opponent} allows deep shots on {d['allowed_deep_rate'] * 100:.0f}% of throws")
                cov = self._coverage_note(self._with_coverage("qb", key), opponent, "he")
            else:
                cov = self._coverage_note(self._with_coverage("rec", gsis), opponent, "he")
            return text + (f"; {cov}" if cov else "")
        if pos == "RB":
            r = self.rb(gsis)
            bits = []
            if d and d.get("stacked_box_rate") is not None:
                bits.append(f"{opponent} puts 8+ in the box on {d['stacked_box_rate'] * 100:.0f}% of runs, blitzes {d.get('blitz_rate', 0) * 100:.0f}%")
            if d and d.get("rb_targets_allowed_pg") is not None:
                bits.append(f"{opponent} allows {d['rb_targets_allowed_pg']} RB targets/g ({_ordinal(self.ranks['rb_targets_allowed_pg'].get(opponent, 0))} most of {len(self.defenses)}): checkdown and dump-off volume")
            if r and r.get("ypc_light") is not None:
                bits.append(f"he averages {r['ypc_light']:.1f} ypc vs light boxes, {r.get('ypc_stacked') or 0:.1f} vs 8+ ({r.get('carries_stacked')} carries, {r['sample']})")
            return "; ".join(bits) or None
        return None


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
        self.situational = Situational(situational_splits(client, self.season), situational_splits(client, self.season - 1))

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
        from league_ai.playcallers import note as playcaller_note

        caller = playcaller_note(self.season, team, self.last_tendencies)
        if caller:   # a new play-caller makes last season's profile a weaker guide
            text += f"; {caller}"
        return text

    def defense_text(self, team: str) -> str | None:
        d = self.defense.get(team)
        if not d:
            return None
        text = f"{team} D allows {d['pa_per_game']} pts/g ({_rank_word(d['rank'], d['teams'])})"
        prof = defense_profile_text(getattr(self, "defense_profiles", {}).get(team))
        situ = self.situational.defense_text(team) if getattr(self, "situational", None) else None
        return text + (f"; {prof}" if prof else "") + (f"; {situ}" if situ else "")

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


def schedule_factor(outlook: Mapping[str, Any], playoff_weight: float = 2.0) -> float:
    """One number for the rest of the season: average matchup multiplier, fantasy-playoff weeks counted double."""
    def num(text: Any) -> float | None:
        try:
            return float(str(text).rstrip("%")) / 100
        except Exception:
            return None

    rest, games = num(outlook.get("avg_matchup_effect")), int(outlook.get("games") or 0)
    po = outlook.get("fantasy_playoffs") or {}
    po_eff, po_games = num(po.get("avg_matchup_effect")), int(po.get("games") or 0)
    if rest is None or not games:
        return 1.0
    total = rest * games + (playoff_weight - 1) * (po_eff or 0) * po_games
    return 1 + total / (games + (playoff_weight - 1) * po_games)


def attach_schedules(players: Iterable[Any], client: Any, season: int, playoff_start: int | None = 15, through_week: int = 17) -> None:
    """Give each player schedule_mult (rest-of-season matchup strength, playoffs weighted double) and a short schedule_note.

    One pass per NFL team and position over cached schedule and defense data; no-op when the schedule isn't loaded.
    """
    from league_ai.nfl_data import current_week_from_games, defense_vs_position

    rows = season_games(client, season)
    week = current_week_from_games(rows) if rows else None
    if not week:
        return
    dvp = defense_vs_position(client, season)
    ctx = team_context(client, season, week)
    profiles = getattr(ctx, "defense_profiles", {}) if ctx else {}
    memo: dict[tuple[str, str], tuple[float, str]] = {}
    for p in players:
        team, pos = getattr(p, "nfl_team", None), getattr(p, "position", None)
        if not team or pos not in ("QB", "RB", "WR", "TE"):
            continue
        if (team, pos) not in memo:
            o = schedule_outlook(rows, team, pos, week, through_week, dvp, profiles, playoff_start)
            po = o.get("fantasy_playoffs") or {}
            note = f"rest of season {o.get('avg_matchup_effect') or '+0.0%'} ({o['soft']} soft, {o['tough']} tough of {o['games']})"
            if po.get("avg_matchup_effect"):
                note += f", fantasy playoffs W{po['weeks']} {po['avg_matchup_effect']}"
            memo[(team, pos)] = (schedule_factor(o), note)
        p.schedule_mult, p.schedule_note = memo[(team, pos)]
