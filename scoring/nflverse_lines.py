"""Official weekly stat lines from nflverse's free public stats, in the stat keys our scoring engine reads.

nflverse publishes every NFL player's weekly box score (built from the NFL's own play by play) as an open CSV,
refreshed through the week as stat corrections land. scripts/final_scores.py saves these lines as our own copy
(final_stat_lines) and locks each week once corrections are done, so official scores never depend on a fantasy
platform. ESPN's feed (scoring/espn_feed.py) is the live source and the backup when nflverse hasn't published a week.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Callable, Iterable, Mapping

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
TEAM_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv"
# nflverse team code -> ours (Sleeper's team defense ids)
TEAM_IDS = {"LA": "LAR"}
POSITIONS = {"QB", "RB", "WR", "TE", "K"}

# nflverse column -> our stat key (straight copies)
COLUMNS = {
    "completions": "pass_cmp", "attempts": "pass_att", "passing_yards": "pass_yd", "passing_tds": "pass_td",
    "passing_interceptions": "pass_int", "sacks_suffered": "pass_sack", "passing_first_downs": "pass_fd",
    "passing_2pt_conversions": "pass_2pt",
    "carries": "rush_att", "rushing_yards": "rush_yd", "rushing_tds": "rush_td", "rushing_first_downs": "rush_fd",
    "rushing_2pt_conversions": "rush_2pt",
    "receptions": "rec", "targets": "rec_tgt", "receiving_yards": "rec_yd", "receiving_tds": "rec_td",
    "receiving_first_downs": "rec_fd", "receiving_2pt_conversions": "rec_2pt",
    "special_teams_tds": "st_td", "fumble_recovery_tds": "fum_rec_td", "fumble_recovery_opp": "fum_rec",
    "punt_return_yards": "pr_yd", "kickoff_return_yards": "kr_yd",
    "fg_made": "fgm", "fg_att": "fga", "fg_missed": "fgmiss",
    "fg_made_0_19": "fgm_0_19", "fg_made_20_29": "fgm_20_29", "fg_made_30_39": "fgm_30_39", "fg_made_40_49": "fgm_40_49",
    "fg_made_50_59": "fgm_50_59", "fg_made_60_": "fgm_60p",
    "fg_missed_0_19": "fgmiss_0_19", "fg_missed_20_29": "fgmiss_20_29", "fg_missed_30_39": "fgmiss_30_39",
    "fg_missed_40_49": "fgmiss_40_49", "fg_missed_50_59": "fgmiss_50_59", "fg_missed_60_": "fgmiss_60p",
    "pat_made": "xpm", "pat_att": "xpa", "pat_missed": "xpmiss",
}
# our key -> nflverse columns added together
SUMS = {
    "fum_lost": ("rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"),
    "fum": ("rushing_fumbles", "receiving_fumbles", "sack_fumbles"),
}


def _num(text: Any) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if value != value else value  # NaN -> 0


def stat_line(row: Mapping[str, Any]) -> dict[str, float]:
    """One nflverse row -> our stat keys (zeros dropped)."""
    stats: dict[str, float] = {}
    for col, key in COLUMNS.items():
        v = _num(row.get(col))
        if v:
            stats[key] = v
    for key, cols in SUMS.items():
        v = sum(_num(row.get(c)) for c in cols)
        if v:
            stats[key] = v
    # nflverse counts a touchdown as a first down; the scoring rules (Sleeper's convention) don't, so take them out.
    for fd, td in (("rush_fd", "rush_td"), ("rec_fd", "rec_td"), ("pass_fd", "pass_td")):
        if fd in stats:
            stats[fd] = max(0.0, stats[fd] - stats.get(td, 0.0))
            if not stats[fd]:
                del stats[fd]
    if stats.get("pass_att"):
        stats["pass_inc"] = max(0.0, stats["pass_att"] - stats.get("pass_cmp", 0.0))
    if stats:
        stats["gp"] = 1
    return stats


def week_lines(rows: Iterable[Mapping[str, Any]], week: int, to_ours: Callable[[str], str | None],
               season_type: str = "REG") -> tuple[dict[str, dict[str, Any]], int]:
    """Our player id -> {stats, pos} for one week; to_ours maps an nflverse (GSIS) id to ours.
    Returns the lines and how many fantasy-position players couldn't be matched."""
    out: dict[str, dict[str, Any]] = {}
    unmatched = 0
    for row in rows:
        if str(row.get("season_type") or "REG") != season_type or int(_num(row.get("week"))) != week:
            continue
        pos = str(row.get("position") or "").upper()
        if pos not in POSITIONS:
            continue
        pid = to_ours(str(row.get("player_id") or ""))
        if not pid:
            unmatched += 1
            continue
        stats = stat_line(row)
        if stats:
            out[pid] = {"stats": stats, "pos": pos}
    return out, unmatched


def read_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def weeks_in(rows: Iterable[Mapping[str, Any]], season_type: str = "REG") -> set[int]:
    return {int(_num(r.get("week"))) for r in rows if str(r.get("season_type") or "REG") == season_type}


class GsisMap:
    """GSIS id -> our player id: Sleeper's public player list (it carries gsis_id), then DynastyProcess's crosswalk."""

    def __init__(self, players: Mapping[str, Mapping[str, Any]], crosswalk: Any = None):
        self.by_gsis = {str(p.get("gsis_id")).strip(): str(pid) for pid, p in (players or {}).items() if p.get("gsis_id")}
        self.crosswalk = crosswalk

    def __call__(self, gsis: str) -> str | None:
        hit = self.by_gsis.get(gsis)
        if hit:
            return hit
        if self.crosswalk is not None:
            sid = self.crosswalk.sleeper_for_gsis(gsis)
            if sid:
                self.by_gsis[gsis] = str(sid)
                return str(sid)
        return None


# Team defense: nflverse team column -> our stat key
DEF_COLUMNS = {
    "def_sacks": "sack", "def_interceptions": "int", "fumble_recovery_opp": "fum_rec", "def_safeties": "safe",
    "def_fumbles_forced": "ff", "special_teams_tds": "def_st_td",
}
DEF_SUMS = {"def_td": ("def_tds", "fumble_recovery_tds"), "blk_kick": ("def_punt_blocks", "def_fg_blocks", "def_pat_blocks")}


def team_code(code: str) -> str:
    return TEAM_IDS.get(code, code)


def def_lines(team_rows: Iterable[Mapping[str, Any]], week: int, points_allowed: Mapping[str, float],
              season_type: str = "REG") -> dict[str, dict[str, Any]]:
    """Team defense lines for one week, keyed by our team id: the defense's own stats from nflverse's team stats,
    points allowed from the final scores (points_allowed: our team id -> the opponent's score), and yards allowed
    from the opponent's offense (passing yards net of sacks plus rushing yards)."""
    rows = [r for r in team_rows if str(r.get("season_type") or "REG") == season_type and int(_num(r.get("week"))) == week]
    by_team = {team_code(str(r.get("team") or "")): r for r in rows}
    out: dict[str, dict[str, Any]] = {}
    for team, row in by_team.items():
        if not team:
            continue
        stats: dict[str, float] = {}
        for col, key in DEF_COLUMNS.items():
            v = _num(row.get(col))
            if v:
                stats[key] = v
        for key, cols in DEF_SUMS.items():
            v = sum(_num(row.get(c)) for c in cols)
            if v:
                stats[key] = v
        opp = by_team.get(team_code(str(row.get("opponent_team") or "")))
        if opp is not None:
            stats["yds_allow"] = _num(opp.get("passing_yards")) - _num(opp.get("sack_yards_lost")) + _num(opp.get("rushing_yards"))
        if team in points_allowed:
            stats["pts_allow"] = float(points_allowed[team])
        stats["gp"] = 1
        out[team] = {"stats": stats, "pos": "DEF"}
    return out
