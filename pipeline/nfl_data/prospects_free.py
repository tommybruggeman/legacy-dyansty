"""Keyless college prospect inputs: cfbfastR-data play-level player stats + rosters, nflverse draft picks.

Builds the same inputs `prospects.build_prospects` takes from CollegeFootballData, so the nfl_prospects table fills
without a CFBD_API_KEY. Season totals and usage come from summing every play a player was credited on:
- receiving: targets, receptions, yards, TDs; rushing: carries, yards, TDs; passing: attempts, yards, TDs, INTs;
- usage: his share of his team's pass plays (targets or throws) and run plays, and of all its plays.
Recruiting ratings aren't in these files, so those columns stay empty on this path. TDs are inferred from gains that
reach the goal line (the files' TD credits are patchy), so TD counts run low; yards and usage are the reliable part.
"""
from __future__ import annotations

import io
from typing import Any

import pandas as pd

RAW = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main"
STATS_URL = RAW + "/player_stats/csv/player_stats_{season}.csv"
ROSTER_URL = RAW + "/rosters/csv/cfb_rosters_{season}.csv"
DRAFT_URL = "https://github.com/nflverse/nflverse-data/releases/download/draft_picks/draft_picks.csv"
PLAY_COLS = ["game_id", "play_id", "team", "conference", "yards_to_goal", "reception_player_id", "reception_yds", "completion_player_id", "completion_yds",
             "rush_player_id", "rush_yds", "interception_thrown_player_id", "touchdown_player_id", "incompletion_player_id", "target_player_id"]


def _id(s: pd.Series) -> pd.Series:
    return s.map(lambda x: None if pd.isna(x) or str(x) in ("NA", "") else str(x).removesuffix(".0")).astype(object)


def season_totals(plays: pd.DataFrame) -> pd.DataFrame:
    """One row per (player, team): receiving, rushing and passing totals plus usage shares."""
    p = plays.copy()
    for c in ("reception_player_id", "completion_player_id", "rush_player_id", "interception_thrown_player_id",
              "touchdown_player_id", "incompletion_player_id", "target_player_id"):
        p[c] = _id(p[c])
    for c in ("reception_yds", "completion_yds", "rush_yds"):
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0.0)
    # TD credits in these files are patchy, so a TD is a gain that reaches the goal line
    to_goal = pd.to_numeric(p.yards_to_goal, errors="coerce")
    pass_score = (p.reception_player_id.notna() & (p.reception_yds >= to_goal)).astype(int)
    rush_score = (p.rush_player_id.notna() & (p.rush_yds >= to_goal)).astype(int)
    passer = p.completion_player_id.fillna(p.incompletion_player_id).fillna(p.interception_thrown_player_id)
    is_pass = passer.notna() | p.target_player_id.notna()
    is_rush = p.rush_player_id.notna()
    team_plays = p.assign(pass_play=is_pass, rush_play=is_rush).groupby("team").agg(
        team_pass=("pass_play", "sum"), team_rush=("rush_play", "sum"), conference=("conference", "first"))
    parts = [
        pd.DataFrame({"pid": p.target_player_id, "team": p.team, "targets": 1}),
        pd.DataFrame({"pid": p.reception_player_id, "team": p.team, "receptions": 1, "rec_yds": p.reception_yds,
                      "rec_td": pass_score}),
        pd.DataFrame({"pid": p.rush_player_id, "team": p.team, "rush_att": 1, "rush_yds": p.rush_yds,
                      "rush_td": rush_score}),
        pd.DataFrame({"pid": passer, "team": p.team, "pass_att": 1,
                      "pass_yds": p.completion_yds.where(p.completion_player_id.notna(), 0.0),
                      "pass_td": pass_score,
                      "pass_int": p.interception_thrown_player_id.notna().astype(int)}),
    ]
    long = pd.concat([x.dropna(subset=["pid"]) for x in parts], ignore_index=True).fillna(0)
    tot = long.groupby(["pid", "team"]).sum(numeric_only=True).reset_index().merge(team_plays, left_on="team", right_index=True)
    tot["usage_pass"] = (tot.targets + tot.pass_att) / tot.team_pass.clip(lower=1)
    tot["usage_rush"] = tot.rush_att / tot.team_rush.clip(lower=1)
    tot["usage_overall"] = (tot.targets + tot.pass_att + tot.rush_att) / (tot.team_pass + tot.team_rush).clip(lower=1)
    return tot


STAT_ROWS = (("passing", "ATT", "pass_att"), ("passing", "YDS", "pass_yds"), ("passing", "TD", "pass_td"), ("passing", "INT", "pass_int"),
             ("rushing", "CAR", "rush_att"), ("rushing", "YDS", "rush_yds"), ("rushing", "TD", "rush_td"),
             ("receiving", "REC", "receptions"), ("receiving", "YDS", "rec_yds"), ("receiving", "TD", "rec_td"))


def to_cfbd_inputs(totals: pd.DataFrame, roster: pd.DataFrame, draft: pd.DataFrame, season: int) -> dict[str, Any]:
    """Reshape into the CFBD-style inputs build_prospects reads (roster, season stats, usage, draft picks)."""
    roster = roster.assign(athlete_id=_id(roster.athlete_id))
    pos = dict(zip(roster.athlete_id, roster.position))
    names = dict(zip(roster.athlete_id, (roster.first_name.fillna("") + " " + roster.last_name.fillna("")).str.strip()))
    keep = roster[roster.position.isin(["QB", "RB", "WR", "TE"])]
    roster_rows = [{"id": r.athlete_id, "firstName": r.first_name, "lastName": r.last_name, "team": r.team, "position": r.position,
                    "year": None if pd.isna(r.year) else int(r.year), "height": r.height, "weight": r.weight, "recruitIds": []}
                   for r in keep.itertuples()]
    stats, usage = [], []
    for r in totals.itertuples():
        if pos.get(r.pid) not in ("QB", "RB", "WR", "TE"):
            continue
        base = {"playerId": r.pid, "player": names.get(r.pid), "position": pos[r.pid], "team": r.team, "conference": r.conference}
        for cat, stat, col in STAT_ROWS:
            v = getattr(r, col)
            if v:
                stats.append({**base, "category": cat, "statType": stat, "stat": float(v)})
        usage.append({"id": r.pid, "name": names.get(r.pid), "position": pos[r.pid], "team": r.team, "conference": r.conference,
                      "usage": {"overall": round(float(r.usage_overall), 4), "pass": round(float(r.usage_pass), 4), "rush": round(float(r.usage_rush), 4)}})
    picks = draft[draft.season.isin([season + 1, season + 2]) & draft.position.isin(["QB", "RB", "WR", "TE"])]
    pick_rows = [{"name": r.pfr_player_name, "year": int(r.season), "round": int(r.round), "overall": int(r.pick), "nflTeam": r.team}
                 for r in picks.itertuples()]
    return {"season_stats": stats, "usage": usage, "roster": roster_rows, "recruits": [], "draft_picks": pick_rows}


def fetch_free_inputs(season: int) -> dict[str, Any]:
    import requests

    def csv(url: str, **kw: Any) -> pd.DataFrame:
        resp = requests.get(url, timeout=300)
        resp.raise_for_status()
        return pd.read_csv(io.BytesIO(resp.content), low_memory=False, **kw)

    plays = csv(STATS_URL.format(season=season), usecols=PLAY_COLS)
    roster = csv(ROSTER_URL.format(season=season))
    draft = csv(DRAFT_URL)
    return to_cfbd_inputs(season_totals(plays), roster, draft, season)
