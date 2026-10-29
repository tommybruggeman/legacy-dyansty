"""Score NFL stat lines with a league's scoring rules.

A player's points are each stat times the league's weight for that stat, summed and rounded to two
decimals (Sleeper's stat keys and scoring keys are the same names). Some scoring keys are bonuses or
buckets that a stat feed may not carry as their own stat (a 100-yard game, a TE catch, points allowed
7-13); when a rule needs one and the stat line has none of that family, it is worked out from the raw
stats here, so the score doesn't depend on which feed supplied the line.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

# bonus key -> (stat, threshold): 1 when the stat reaches the threshold
THRESHOLDS = {
    "bonus_pass_yd_300": ("pass_yd", 300), "bonus_pass_yd_400": ("pass_yd", 400),
    "bonus_rush_yd_100": ("rush_yd", 100), "bonus_rush_yd_200": ("rush_yd", 200),
    "bonus_rec_yd_100": ("rec_yd", 100), "bonus_rec_yd_200": ("rec_yd", 200),
    "bonus_rush_rec_yd_100": ("rush_rec_yd", 100), "bonus_rush_rec_yd_200": ("rush_rec_yd", 200),
    "bonus_pass_cmp_25": ("pass_cmp", 25), "bonus_rush_att_20": ("rush_att", 20),
}
# position catch bonuses: one per catch by a player at that position
CATCH_BONUS = {"bonus_rec_te": "TE", "bonus_rec_rb": "RB", "bonus_rec_wr": "WR"}
# team defense buckets: (low, high) inclusive, high None = and up
PTS_ALLOW = {"pts_allow_0": (0, 0), "pts_allow_1_6": (1, 6), "pts_allow_7_13": (7, 13), "pts_allow_14_20": (14, 20),
             "pts_allow_21_27": (21, 27), "pts_allow_28_34": (28, 34), "pts_allow_35p": (35, None)}
YDS_ALLOW = {"yds_allow_0_100": (0, 99), "yds_allow_100_199": (100, 199), "yds_allow_200_299": (200, 299),
             "yds_allow_300_349": (300, 349), "yds_allow_350_399": (350, 399), "yds_allow_400_449": (400, 449),
             "yds_allow_450_499": (450, 499), "yds_allow_500_549": (500, 549), "yds_allow_550p": (550, None)}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bucket(value: float | None, buckets: Mapping[str, tuple[int, int | None]]) -> str | None:
    if value is None:
        return None
    for key, (low, high) in buckets.items():
        if value >= low and (high is None or value <= high):
            return key
    return None


def full_line(stats: Mapping[str, Any] | None, position: str | None = None, rules: Iterable[str] = ()) -> dict[str, Any]:
    """The stat line plus any bonus or bucket stat a rule in `rules` needs and the feed left out."""
    line = dict(stats or {})
    rules = set(rules)
    pos = str(position or "").upper()

    def stat(name: str) -> float:
        if name == "rush_rec_yd" and name not in line:
            return stat("rush_yd") + stat("rec_yd")
        return _num(line.get(name)) or 0.0

    for key, (name, threshold) in THRESHOLDS.items():
        if key in rules and key not in line:
            line[key] = 1 if stat(name) >= threshold else 0
    for key, catch_pos in CATCH_BONUS.items():
        if key in rules and key not in line:
            line[key] = stat("rec") if pos == catch_pos else 0
    for family, raw in ((PTS_ALLOW, "pts_allow"), (YDS_ALLOW, "yds_allow")):
        if pos == "DEF" and rules & set(family) and not set(family) & set(line):
            hit = _bucket(_num(line.get(raw)), family)
            if hit:
                line[hit] = 1
    return line


def player_points(stats: Mapping[str, Any] | None, scoring: Mapping[str, Any], position: str | None = None) -> float:
    """One player's points for one stat line under the league's scoring rules."""
    if not stats:
        return 0.0
    line = full_line(stats, position, scoring)
    total = 0.0
    for key, weight in scoring.items():
        value, w = _num(line.get(key)), _num(weight)
        if value is not None and w is not None:
            total += value * w
    return round(total, 2)


def lineup_points(starters: Iterable[str], lines: Mapping[str, Mapping[str, Any]], scoring: Mapping[str, Any]) -> float:
    """A lineup's total. lines: player id -> {stats, pos}; an empty slot ('0' or '') or a player with no line scores 0."""
    total = 0.0
    for pid in starters:
        line = lines.get(str(pid)) if pid not in (None, "", "0") else None
        if line:
            total += player_points(line.get("stats"), scoring, line.get("pos"))
    return round(total, 2)


def rule_diff(ours: Mapping[str, Any], theirs: Mapping[str, Any]) -> list[str]:
    """Scoring keys whose weight differs between two rule sets (a missing key counts as 0)."""
    keys = set(ours) | set(theirs)
    return sorted(k for k in keys if abs((_num(ours.get(k)) or 0.0) - (_num(theirs.get(k)) or 0.0)) > 1e-9)
