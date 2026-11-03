"""The league's scoring rules as an editable list, for the commissioner's Settings page.

Rules are stored as {stat key: points} (league_ai_settings.scoring_settings), the same keys Sleeper's stat
feed uses, so scoring/engine.py can score any line with them. CATALOG lists the rules a league is likely to
use, grouped and labelled; a saved key that isn't in it still shows (under Other) and still scores.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

CATALOG: list[tuple[str, list[tuple[str, str]]]] = [
    ("Passing", [
        ("pass_yd", "Passing yard"), ("pass_td", "Passing TD"), ("pass_int", "Interception thrown"), ("pass_2pt", "Passing 2-pt"),
        ("pass_cmp", "Completion"), ("pass_inc", "Incompletion"), ("pass_att", "Pass attempt"), ("pass_sack", "Sack taken"),
        ("pass_int_td", "Pick six thrown"), ("pass_fd", "Passing first down"),
        ("bonus_pass_yd_300", "300-yard passing game"), ("bonus_pass_yd_400", "400-yard passing game"), ("bonus_pass_cmp_25", "25+ completions"),
    ]),
    ("Rushing", [
        ("rush_yd", "Rushing yard"), ("rush_td", "Rushing TD"), ("rush_att", "Carry"), ("rush_2pt", "Rushing 2-pt"), ("rush_fd", "Rushing first down"),
        ("bonus_rush_yd_100", "100-yard rushing game"), ("bonus_rush_yd_200", "200-yard rushing game"), ("bonus_rush_att_20", "20+ carries"),
    ]),
    ("Receiving", [
        ("rec", "Reception"), ("rec_yd", "Receiving yard"), ("rec_td", "Receiving TD"), ("rec_2pt", "Receiving 2-pt"), ("rec_tgt", "Target"),
        ("rec_fd", "Receiving first down"), ("bonus_rec_te", "TE reception bonus"), ("bonus_rec_rb", "RB reception bonus"),
        ("bonus_rec_wr", "WR reception bonus"), ("bonus_rec_yd_100", "100-yard receiving game"), ("bonus_rec_yd_200", "200-yard receiving game"),
    ]),
    ("Turnovers and returns", [
        ("fum", "Fumble"), ("fum_lost", "Fumble lost"), ("fum_rec_td", "Fumble recovered for TD"), ("st_td", "Special teams TD"),
        ("kr_yd", "Kick return yard"), ("pr_yd", "Punt return yard"),
    ]),
    ("Kicking", [
        ("xpm", "Extra point made"), ("xpmiss", "Extra point missed"), ("fgm_0_19", "FG made 0-19"), ("fgm_20_29", "FG made 20-29"),
        ("fgm_30_39", "FG made 30-39"), ("fgm_40_49", "FG made 40-49"), ("fgm_50p", "FG made 50+"), ("fgmiss", "FG missed"), ("fgm_yds", "Per FG yard"),
    ]),
    ("Team defense", [
        ("def_td", "Defensive TD"), ("sack", "Sack"), ("int", "Interception"), ("fum_rec", "Fumble recovery"), ("ff", "Forced fumble"),
        ("safe", "Safety"), ("blk_kick", "Blocked kick"), ("def_st_td", "Defense/special teams TD"), ("def_2pt", "Defensive 2-pt return"),
        ("pts_allow_0", "0 points allowed"), ("pts_allow_1_6", "1-6 points allowed"), ("pts_allow_7_13", "7-13 points allowed"),
        ("pts_allow_14_20", "14-20 points allowed"), ("pts_allow_21_27", "21-27 points allowed"), ("pts_allow_28_34", "28-34 points allowed"),
        ("pts_allow_35p", "35+ points allowed"),
        ("yds_allow_0_100", "Under 100 yards allowed"), ("yds_allow_100_199", "100-199 yards allowed"), ("yds_allow_200_299", "200-299 yards allowed"),
        ("yds_allow_300_349", "300-349 yards allowed"), ("yds_allow_350_399", "350-399 yards allowed"), ("yds_allow_400_449", "400-449 yards allowed"),
        ("yds_allow_450_499", "450-499 yards allowed"), ("yds_allow_500_549", "500-549 yards allowed"), ("yds_allow_550p", "550+ yards allowed"),
    ]),
]
KNOWN = {key for _, rules in CATALOG for key, _ in rules}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def rows_from_rules(scoring: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """One row per rule: {group, rule, key, points}. Every catalog rule (0 when unused), then any saved key not in it."""
    scoring = scoring or {}
    rows = [{"group": group, "rule": label, "key": key, "points": _num(scoring.get(key))} for group, rules in CATALOG for key, label in rules]
    rows += [{"group": "Other", "rule": key.replace("_", " "), "key": key, "points": _num(scoring.get(key))}
             for key in sorted(set(scoring) - KNOWN)]
    return rows


def rules_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """The saved form: only rules worth points (a 0 is the same as no rule)."""
    out: dict[str, float] = {}
    for row in rows:
        key, pts = str(row.get("key") or "").strip(), _num(row.get("points"))
        if key and pts:
            out[key] = int(pts) if float(pts).is_integer() else round(pts, 4)
    return out
