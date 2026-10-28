"""WR matchup: this receiver's role against this defense, this week. No model call.

The plain defense-vs-WR rank made WR start/sit calls worse (graded on every saved lineup), because it treats every
receiver the same. This looks at the receiver's own profile and how the defense handles that kind of receiver:

- role: target share, yards per target, TD rate per target and average depth of target (aDOT), this season so far,
  each pulled toward the position average until a few games are in
- volume: how many targets WRs get against this defense vs the league, and how many his own offense throws
- style: points per WR target the defense allows to deep threats (aDOT 12+) vs underneath receivers, so a defense that
  shuts down the deep ball tilts a field stretcher more than a slot receiver
- injuries: targets of teammates who are out this week are shared among the receivers who play, by their own shares
- outliers: a TD rate far above his yardage is pulled back (touchdowns regress), and tiny samples get little weight

Returns a multiplier on his projection, capped at CAP either way, plus the numbers behind it for the answer.
All inputs come from nfl_player_stats rows for games before this week.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

DEEP_ADOT = 12.0          # aDOT at or above this is a field stretcher
PLAYER_PRIOR_TARGETS = 20  # targets of position-average play mixed into each receiver's rates
DEFENSE_PRIOR_TARGETS = 40
TD_REGRESSION = 0.5        # how far a receiver's TD rate is pulled toward the TD rate his yardage implies
CAP = 0.2


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ppr(r: Mapping[str, Any]) -> float:
    """Receiving PPR points from the stat line (league scoring differs a little; this is only used as a ratio)."""
    return _f(r.get("receptions")) + 0.1 * _f(r.get("receiving_yards")) + 6 * _f(r.get("receiving_tds"))


class WrModel:
    """Built once per season-week from the rows of earlier games."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        rows = [r for r in rows if r.get("team")]
        self.team_games: dict[str, set[int]] = defaultdict(set)
        team_targets: dict[str, float] = defaultdict(float)
        self.team_week_targets: dict[tuple[str, int], float] = defaultdict(float)
        self.player: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.player_weeks: dict[str, list[tuple[int, float]]] = defaultdict(list)   # (week, targets): who usually plays
        self.player_team: dict[str, str] = {}
        d_targets: dict[str, float] = defaultdict(float)
        d_games: dict[str, set[int]] = defaultdict(set)
        d_style: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])   # (defense, deep|short) -> [targets, points]
        lg_style: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        wr = [r for r in rows if str(r.get("position") or "").upper() == "WR"]
        for r in rows:
            team, wk = str(r["team"]), int(r.get("week") or 0)
            self.team_games[team].add(wk)
            team_targets[team] += _f(r.get("targets"))
            self.team_week_targets[(team, wk)] += _f(r.get("targets"))
            g = str(r.get("gsis_id") or "")
            self.player_weeks[g].append((wk, _f(r.get("targets"))))   # every position: who usually plays and draws targets
            self.player_team[g] = team
        for r in wr:
            pid = str(r.get("gsis_id") or "")
            p = self.player[pid]
            for k in ("targets", "receiving_yards", "receiving_tds", "receiving_air_yards"):
                p[k] += _f(r.get(k))
            p["ppr"] += _ppr(r)
            p["games"] += 1
        for pid, p in self.player.items():   # style of each receiver this season, for the defense split
            p["adot"] = p["receiving_air_yards"] / p["targets"] if p["targets"] else 0.0
        for r in wr:
            opp = str(r.get("opponent") or "")
            if not opp:
                continue
            pid, t = str(r.get("gsis_id") or ""), _f(r.get("targets"))
            d_targets[opp] += t
            d_games[opp].add(int(r.get("week") or 0))
            style = "deep" if self.player[pid]["adot"] >= DEEP_ADOT else "short"
            d_style[(opp, style)][0] += t
            d_style[(opp, style)][1] += _ppr(r)
            lg_style[style][0] += t
            lg_style[style][1] += _ppr(r)
        self.team_tpg = {t: team_targets[t] / len(g) for t, g in self.team_games.items() if g}
        self.lg_team_tpg = sum(self.team_tpg.values()) / len(self.team_tpg) if self.team_tpg else 0.0
        self.d_wr_tpg = {d: d_targets[d] / len(g) for d, g in d_games.items() if g}
        self.lg_wr_tpg = sum(self.d_wr_tpg.values()) / len(self.d_wr_tpg) if self.d_wr_tpg else 0.0
        self.lg_ppt = {s: (v[1] / v[0] if v[0] else 0.0) for s, v in lg_style.items()}
        self.d_style = d_style
        all_t = sum(p["targets"] for p in self.player.values())
        self.lg = {"ypt": sum(p["receiving_yards"] for p in self.player.values()) / all_t if all_t else 8.0,
                   "tdr": sum(p["receiving_tds"] for p in self.player.values()) / all_t if all_t else 0.045,
                   "ppt": sum(p["ppr"] for p in self.player.values()) / all_t if all_t else 1.7}

    def profile(self, gsis: str) -> dict[str, float] | None:
        p = self.player.get(gsis)
        if not p or not p["games"]:
            return None
        team = self.player_team.get(gsis, "")
        team_t = sum(self.team_week_targets.get((team, w), 0.0) for w, _ in self.player_weeks[gsis])   # only games he played
        share = p["targets"] / team_t if team_t else 0.0
        n = p["targets"] + PLAYER_PRIOR_TARGETS
        ypt = (p["receiving_yards"] + self.lg["ypt"] * PLAYER_PRIOR_TARGETS) / n
        tdr = (p["receiving_tds"] + self.lg["tdr"] * PLAYER_PRIOR_TARGETS) / n
        implied_tdr = self.lg["tdr"] * ypt / self.lg["ypt"] if self.lg["ypt"] else tdr
        tdr = tdr + TD_REGRESSION * (implied_tdr - tdr)   # touchdowns follow yardage over time
        return {"games": p["games"], "target_share": round(share, 3), "targets_per_game": round(p["targets"] / p["games"], 1),
                "yards_per_game": round(p["receiving_yards"] / p["games"], 1), "yards_per_target": round(ypt, 2),
                "td_per_target": round(tdr, 3), "adot": round(p["adot"], 1), "style": "deep threat" if p["adot"] >= DEEP_ADOT else "underneath"}

    def missing_share(self, team: str, out: Iterable[str]) -> float:
        """Share of the team's targets that belonged to receivers who are out this week."""
        tot = self.team_tpg.get(team, 0.0) * len(self.team_games.get(team, ()))
        if not tot:
            return 0.0
        return min(0.5, sum(t for g in out for _, t in self.player_weeks.get(g, ())) / tot)

    def multiplier(self, gsis: str, opponent: str, out_teammates: Iterable[str] = ()) -> tuple[float, dict[str, Any]]:
        prof = self.profile(gsis)
        if not prof or not opponent:
            return 1.0, {}
        team = self.player_team.get(gsis, "")
        # volume: WR targets against this defense vs the league, shrunk by its games
        dg = len(self.team_games.get(opponent, ()))
        vol = self.d_wr_tpg.get(opponent, self.lg_wr_tpg) / self.lg_wr_tpg if self.lg_wr_tpg else 1.0
        vol = 1 + (vol - 1) * dg / (dg + 3)
        # style: points per target the defense allows to his kind of receiver vs the league
        style = "deep" if prof["adot"] >= DEEP_ADOT else "short"
        t, pts = self.d_style.get((opponent, style), [0.0, 0.0])
        lg = self.lg_ppt.get(style) or self.lg["ppt"]
        eff = ((pts + lg * DEFENSE_PRIOR_TARGETS) / (t + DEFENSE_PRIOR_TARGETS)) / lg if lg else 1.0
        # injuries: his share of the targets left by teammates who are out
        gone = self.missing_share(team, out_teammates)
        bump = 1 + gone * min(1.0, prof["target_share"] / max(1e-6, 1 - gone)) if gone else 1.0
        m = max(1 - CAP, min(1 + CAP, vol * eff * bump))
        why = {**prof, "defense_wr_targets_vs_avg": f"{(vol - 1) * 100:+.0f}%", f"defense_vs_{'deep' if style == 'deep' else 'underneath'}_receivers": f"{(eff - 1) * 100:+.0f}% points per target",
               "teammates_out_share": round(gone, 3), "effect": f"{(m - 1) * 100:+.0f}% to his projection"}
        return m, why

    def out_teammates(self, gsis: str, week_rows: Iterable[Mapping[str, Any]]) -> list[str]:
        """Backtest only: regular target-getters on his team (3+ targets a game) with no line in this week's game."""
        team = self.player_team.get(gsis, "")
        played = {str(r.get("gsis_id")) for r in week_rows if r.get("team") == team}
        return [g for g, wk in self.player_weeks.items() if self.player_team.get(g) == team and g != gsis and g not in played
                and len(wk) >= 2 and sum(t for _, t in wk) / len(wk) >= 3]
