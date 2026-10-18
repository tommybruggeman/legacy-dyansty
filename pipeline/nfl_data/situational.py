"""nflverse play-by-play + FTN charting -> nfl_situational_splits (blitz, pressure, box counts).

Per season:
- defense: blitz rate, pressure rate (sacks + QB hits per dropback), average pass rushers, EPA per
  dropback allowed when blitzing and when not, stacked-box (8+) rate on runs, EPA per rush allowed.
- qb: dropbacks, how often he is blitzed, EPA per dropback and sack rate blitzed vs not blitzed.
- rb: carries, yards per carry and EPA per rush against stacked (8+) and light boxes.
- coverage style from play-by-play: depth of throws allowed (aDOT, deep-shot rate, EPA on deep vs short
  throws), how much of the passing game goes to running backs and tight ends (dump-offs, checkdowns).
- with nflverse participation data (NGS-charted; published after the season, so usually last season
  only): man vs zone rate, two-high safety shells, and QB / receiver EPA against man, zone and two-high.
FTN charting starts in 2022 and is published a few days after games, so the newest week can lag.
"""
from __future__ import annotations

from io import BytesIO, StringIO
from typing import Any

import pandas as pd

from pipeline.nfl_data.common import now_iso

PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
FTN_URL = "https://github.com/nflverse/nflverse-data/releases/download/ftn_charting/ftn_charting_{season}.csv"
PARTICIPATION_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp_participation/pbp_participation_{season}.csv"
PARTICIPATION_COLUMNS = ["nflverse_game_id", "play_id", "defense_man_zone_type", "defense_coverage_type", "was_pressure", "time_to_throw"]
TWO_HIGH = {"COVER_2", "COVER_4", "COVER_6", "2_MAN", "COVER_9"}
DEEP_AIR_YARDS = 20
MIN_REC_TARGETS = 25
PBP_COLUMNS = ["game_id", "play_id", "season", "week", "season_type", "posteam", "defteam", "qb_dropback", "rush", "epa", "sack", "qb_hit",
               "yards_gained", "passer_player_id", "passer_player_name", "rusher_player_id", "rusher_player_name", "receiver_player_id", "receiver_player_name", "air_yards", "pass_attempt"]
FTN_COLUMNS = ["nflverse_game_id", "nflverse_play_id", "n_blitzers", "n_pass_rushers", "n_defense_box", "is_qb_out_of_pocket"]
MIN_QB_DROPBACKS = 40
MIN_RB_CARRIES = 25


def read_pbp(raw: bytes) -> pd.DataFrame:
    return pd.read_csv(BytesIO(raw), compression="gzip", low_memory=False, usecols=lambda c: c in PBP_COLUMNS)


def read_ftn(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), low_memory=False, usecols=lambda c: c in FTN_COLUMNS)


def read_participation(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), low_memory=False, usecols=lambda c: c in PARTICIPATION_COLUMNS)


def _r(value: Any, nd: int = 3) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    return None if v != v else round(v, nd)


def _mean(series: pd.Series, nd: int = 3) -> float | None:
    return _r(series.mean(), nd) if len(series) else None


def _coverage_split(d: pd.DataFrame) -> dict[str, Any]:
    """EPA against man / zone / two-high / single-high, when participation data is joined."""
    if "man" not in d.columns or d["man"].isna().all():
        return {}
    out: dict[str, Any] = {}
    charted = d[d["man"].notna()]
    for label, mask in (("man", charted["man"] == True), ("zone", charted["man"] == False), ("two_high", charted["two_high"] == True), ("single_high", charted["two_high"] == False)):  # noqa: E712
        part = charted[mask]
        out[f"epa_vs_{label}"] = _mean(part["epa"])
        out[f"plays_vs_{label}"] = int(len(part))
    return out


def transform_splits(pbp: pd.DataFrame, ftn: pd.DataFrame, season: int, positions: dict[str, str] | None = None,
                     participation: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """positions: gsis id -> position (for RB/TE target shares); participation: optional coverage charting."""
    plays = pbp[(pbp["season_type"] == "REG")] if "season_type" in pbp.columns else pbp
    m = plays.merge(ftn, left_on=["game_id", "play_id"], right_on=["nflverse_game_id", "nflverse_play_id"], how="inner")
    if m.empty:
        return []
    m = m.assign(blitz=m["n_blitzers"].fillna(0) > 0, stacked=m["n_defense_box"].fillna(0) >= 8,
                 pressure=(m["sack"].fillna(0) > 0) | (m["qb_hit"].fillna(0) > 0))
    if "receiver_player_id" in m.columns:
        m = m.assign(target_pos=m["receiver_player_id"].map(lambda g: (positions or {}).get(str(g)) if isinstance(g, str) else None))
    if participation is not None and not participation.empty:
        part = participation.rename(columns={"nflverse_game_id": "p_game_id", "play_id": "p_play_id"})
        m = m.merge(part, left_on=["game_id", "play_id"], right_on=["p_game_id", "p_play_id"], how="left")
        zone = m["defense_man_zone_type"]
        m = m.assign(man=zone.map(lambda z: None if not isinstance(z, str) else z == "MAN_COVERAGE"),
                     two_high=m["defense_coverage_type"].map(lambda c: None if not isinstance(c, str) or c in ("BLOWN", "COMBO") else c in TWO_HIGH))
    dropbacks = m[m["qb_dropback"] == 1]
    runs = m[(m["rush"] == 1) & (m["qb_dropback"] != 1)]
    throws = dropbacks[dropbacks["air_yards"].notna()] if "air_yards" in dropbacks.columns else dropbacks.iloc[0:0]
    throws = throws.assign(deep=throws["air_yards"] >= DEEP_AIR_YARDS, short=throws["air_yards"] < 10) if len(throws) else throws
    stamp = now_iso()
    rows: list[dict[str, Any]] = []

    for team, d in dropbacks.groupby("defteam"):
        r = runs[runs["defteam"] == team]
        b, nb = d[d["blitz"]], d[~d["blitz"]]
        rows.append({"season": season, "kind": "defense", "key": str(team), "team": str(team), "name": str(team), "refreshed_at": stamp, "metrics": {
            "dropbacks": int(len(d)), "blitz_rate": _mean(d["blitz"]), "pressure_rate": _mean(d["pressure"]), "avg_pass_rushers": _mean(d["n_pass_rushers"], 2),
            "epa_allowed_blitz": _mean(b["epa"]), "epa_allowed_no_blitz": _mean(nb["epa"]), "rushes": int(len(r)),
            "stacked_box_rate": _mean(r["stacked"]), "epa_per_rush_allowed": _mean(r["epa"]), "weeks": int(m["week"].max()),
            **_throw_profile(throws[throws["defteam"] == team], allowed=True), **_coverage_rates(d),
        }})

    for pid, d in dropbacks.dropna(subset=["passer_player_id"]).groupby("passer_player_id"):
        if len(d) < MIN_QB_DROPBACKS:
            continue
        b, nb = d[d["blitz"]], d[~d["blitz"]]
        rows.append({"season": season, "kind": "qb", "key": str(pid), "team": str(d["posteam"].mode().iat[0]), "name": str(d["passer_player_name"].mode().iat[0]), "refreshed_at": stamp, "metrics": {
            "dropbacks": int(len(d)), "blitzed": int(len(b)), "blitzed_rate": _mean(d["blitz"]),
            "epa_vs_blitz": _mean(b["epa"]), "epa_no_blitz": _mean(nb["epa"]), "sack_rate_vs_blitz": _mean(b["sack"].fillna(0)), "sack_rate_no_blitz": _mean(nb["sack"].fillna(0)),
            "pressure_rate": _mean(d["pressure"]), "out_of_pocket_rate": _mean(d["is_qb_out_of_pocket"].astype(float)),
            **_throw_profile(throws[throws["passer_player_id"] == pid], allowed=False), **_coverage_split(d),
        }})

    for pid, d in runs.dropna(subset=["rusher_player_id"]).groupby("rusher_player_id"):
        if len(d) < MIN_RB_CARRIES:
            continue
        s, light = d[d["stacked"]], d[~d["stacked"]]
        rows.append({"season": season, "kind": "rb", "key": str(pid), "team": str(d["posteam"].mode().iat[0]), "name": str(d["rusher_player_name"].mode().iat[0]), "refreshed_at": stamp, "metrics": {
            "carries": int(len(d)), "stacked_rate": _mean(d["stacked"]), "ypc_stacked": _mean(s["yards_gained"], 2), "ypc_light": _mean(light["yards_gained"], 2),
            "epa_stacked": _mean(s["epa"]), "epa_light": _mean(light["epa"]), "carries_stacked": int(len(s)),
        }})

    if "receiver_player_id" in throws.columns:
        for pid, d in throws.dropna(subset=["receiver_player_id"]).groupby("receiver_player_id"):
            if len(d) < MIN_REC_TARGETS:
                continue
            rows.append({"season": season, "kind": "rec", "key": str(pid), "team": str(d["posteam"].mode().iat[0]), "name": str(d["receiver_player_name"].mode().iat[0]), "refreshed_at": stamp, "metrics": {
                "targets": int(len(d)), "adot": _mean(d["air_yards"], 1), "deep_target_rate": _mean(d["deep"]), "epa_per_target": _mean(d["epa"]), **_coverage_split(d),
            }})
    return rows


def _throw_profile(t: pd.DataFrame, *, allowed: bool) -> dict[str, Any]:
    """Depth of throws and where the ball goes; for a defense these are what it allows."""
    if not len(t):
        return {}
    pre = "allowed_" if allowed else ""
    out = {f"{pre}adot": _mean(t["air_yards"], 1), f"{pre}deep_rate": _mean(t["deep"]), f"epa_deep{'_allowed' if allowed else ''}": _mean(t[t["deep"]]["epa"]),
           f"epa_short{'_allowed' if allowed else ''}": _mean(t[t["short"]]["epa"]), f"{pre}attempts": int(len(t))}
    if "target_pos" in t.columns and t["target_pos"].notna().any():
        out[f"{pre}rb_target_share"] = _mean((t["target_pos"] == "RB").astype(float))
        out[f"{pre}te_target_share"] = _mean((t["target_pos"] == "TE").astype(float))
        if allowed:
            weeks = max(1, t["week"].nunique())
            out["rb_targets_allowed_pg"] = _r((t["target_pos"] == "RB").sum() / weeks, 1)
    return out


def _coverage_rates(d: pd.DataFrame) -> dict[str, Any]:
    if "man" not in d.columns or d["man"].isna().all():
        return {}
    out = {"man_rate": _mean(d["man"].dropna().astype(float)), "two_high_rate": _mean(d["two_high"].dropna().astype(float)), "charted_dropbacks": int(d["man"].notna().sum())}
    out.update({f"epa_allowed_vs_{k[7:]}" if k.startswith("epa_vs_") else k: v for k, v in _coverage_split(d).items() if k.startswith("epa_vs_")})
    return out
