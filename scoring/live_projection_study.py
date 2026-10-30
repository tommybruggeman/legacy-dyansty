"""Which in-game projection update best predicts a player's final fantasy score?

Rebuilds every QB/RB/WR/TE's points play by play from nflverse play-by-play (2023-2025, league
scoring), snapshots them at 15 points of the game clock, and tests ways to project the final from
there, with Sleeper's real pregame projection as the starting point. Fits on 2023-24, tests on 2025.

Prints numbers only (the repo and its Actions logs are public): no player, team or owner names.

    python -m scoring.live_projection_study
"""
from __future__ import annotations

import io
import os
from typing import Any

import numpy as np
import pandas as pd
import requests

PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
SLEEPER = "https://api.sleeper.app"
SEASONS = (2023, 2024, 2025)
TEST_SEASON = 2025
POSITIONS = ("QB", "RB", "WR", "TE")
CHECK = [i / 16 for i in range(1, 16)]  # elapsed share of regulation
HALF_PPR = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "rush_yd": 0.1, "rush_td": 6, "rec": 0.5, "rec_yd": 0.1, "rec_td": 6, "fum_lost": -2}
COLS = ["game_id", "season", "week", "season_type", "posteam", "defteam", "game_seconds_remaining", "passer_player_id", "rusher_player_id",
        "receiver_player_id", "passing_yards", "rushing_yards", "receiving_yards", "pass_touchdown", "rush_touchdown", "interception",
        "fumble_lost", "fumbled_1_player_id", "complete_pass", "pass_attempt", "rush_attempt", "posteam_score_post", "defteam_score_post",
        "td_player_id"]


def get(url: str) -> Any:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r


def league_scoring() -> dict[str, float]:
    """The league's Sleeper scoring for the stats play-by-play can rebuild; half PPR if it can't be read."""
    try:
        from supabase import create_client
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
        sid = next(str(r["sleeper_league_id"]) for r in client.table("leagues").select("sleeper_league_id").execute().data if r.get("sleeper_league_id"))
        scoring = get(f"{SLEEPER}/v1/league/{sid}").json().get("scoring_settings") or {}
        out = {k: float(scoring.get(k, 0) or 0) for k in HALF_PPR}
        print("[study] scoring: league settings", flush=True)
        return out
    except Exception as exc:
        print(f"[study] scoring: half PPR fallback ({type(exc).__name__})", flush=True)
        return dict(HALF_PPR)


def snapshots(season: int, sc: dict[str, float]) -> pd.DataFrame:
    d = pd.read_parquet(io.BytesIO(get(PBP.format(season=season)).content), columns=COLS)
    d = d[(d.season_type == "REG") & d.game_seconds_remaining.notna()].copy()
    d["el"] = (3600 - d.game_seconds_remaining.clip(lower=0, upper=3600)) / 3600
    f = lambda x: 0.0 if pd.isna(x) else float(x)
    rows = []
    for r in d.itertuples(index=False):
        base = (r.game_id, r.week, r.el, r.posteam)
        if isinstance(r.passer_player_id, str) and r.pass_attempt == 1:
            rows.append(base + (r.passer_player_id, sc["pass_yd"] * f(r.passing_yards) + sc["pass_td"] * f(r.pass_touchdown) + sc["pass_int"] * f(r.interception), 1, 0, 0))
        if isinstance(r.receiver_player_id, str) and r.pass_attempt == 1:
            rows.append(base + (r.receiver_player_id, sc["rec"] * f(r.complete_pass) + sc["rec_yd"] * f(r.receiving_yards) + sc["rec_td"] * (r.td_player_id == r.receiver_player_id), 0, 1, 0))
        if isinstance(r.rusher_player_id, str) and r.rush_attempt == 1:
            rows.append(base + (r.rusher_player_id, sc["rush_yd"] * f(r.rushing_yards) + sc["rush_td"] * (r.td_player_id == r.rusher_player_id and f(r.rush_touchdown) == 1), 0, 0, 1))
        if f(r.fumble_lost) and isinstance(r.fumbled_1_player_id, str):
            rows.append(base + (r.fumbled_1_player_id, sc["fum_lost"], 0, 0, 0))
    e = pd.DataFrame(rows, columns=["game_id", "week", "el", "team", "gsis", "pts", "att", "tgt", "car"]).sort_values("el")
    scores = d[["game_id", "el", "posteam", "defteam", "posteam_score_post", "defteam_score_post"]].dropna().sort_values("el")
    out = []
    for (gid, gsis), g in e.groupby(["game_id", "gsis"], sort=False):
        cum = g[["pts", "att", "tgt", "car"]].cumsum().to_numpy()
        els = g.el.to_numpy()
        team = g.team.mode().iat[0]
        for c in CHECK:
            k = np.searchsorted(els, c, side="right")
            snap = cum[k - 1] if k else np.zeros(4)
            out.append((gid, season, int(g.week.iat[0]), gsis, team, c, *snap, *cum[-1]))
    s = pd.DataFrame(out, columns=["game_id", "season", "week", "gsis", "team", "el", "pts", "att", "tgt", "car", "f_pts", "f_att", "f_tgt", "f_car"])
    marg = []
    for gid, g in scores.groupby("game_id"):
        els = g.el.to_numpy()
        for c in CHECK:
            k = np.searchsorted(els, c, side="right")
            if not k:
                continue
            last = g.iloc[k - 1]
            m = float(last.posteam_score_post - last.defteam_score_post)
            marg += [(gid, last.posteam, c, m), (gid, last.defteam, c, -m)]
    m = pd.DataFrame(marg, columns=["game_id", "team", "el", "margin"]).drop_duplicates(["game_id", "team", "el"])
    return s.merge(m, on=["game_id", "team", "el"], how="left")


def projections(sc: dict[str, float], gsis_of: dict[str, str]) -> pd.DataFrame:
    """Sleeper's pregame projection per player-week, in league points, plus projected opportunities."""
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)
    rows = []
    for season in SEASONS:
        for week in range(1, 19):
            try:
                data = get(f"{SLEEPER}/projections/nfl/{season}/{week}?{query}").json()
            except Exception:
                continue
            for row in data or []:
                st, gsis = row.get("stats") or {}, gsis_of.get(str(row.get("player_id")))
                if not gsis or not st:
                    continue
                pts = sum(float(st.get(k) or 0) * w for k, w in sc.items())
                pos = (row.get("player") or {}).get("position") or ""
                opp = float(st.get("pass_att") or 0) + float(st.get("rush_att") or 0) if pos == "QB" else float(st.get("rec_tgt") or 0) + float(st.get("rush_att") or 0)
                rows.append((season, week, gsis, pos, pts, opp))
    return pd.DataFrame(rows, columns=["season", "week", "gsis", "position", "P", "O"])


def features(s: pd.DataFrame) -> pd.DataFrame:
    s = s.sort_values(["game_id", "gsis", "el"]).copy()
    s["opp"] = np.where(s.position == "QB", s.att + s.car, s.tgt + s.car)
    s["left"] = 1 - s.el
    s["ppo"] = s.P / s.O.clip(lower=1)
    s["recent"] = s.opp - s.groupby(["game_id", "gsis"]).opp.shift(2).fillna(0)
    s["cold"] = ((s.el >= 0.25) & (s.recent == 0) & (s.O * 0.125 >= 1.5)).astype(float)
    s["margin"] = s.margin.fillna(0).clip(-21, 21)
    s["pts_rate"] = (3 * s.P + s.pts) / (3 + s.el)
    s["opp_rate"] = (0.5 * s.O + s.opp) / (0.5 + s.el) * s.ppo
    return s


def design(d: pd.DataFrame, kind: str) -> np.ndarray:
    if kind == "clock":
        return np.column_stack([d.left * d.P])
    return np.column_stack([d.left * d.pts_rate, d.left * d.opp_rate, d.left * d.margin, d.left * d.cold * d.P, d.left])


def main() -> None:
    sc = league_scoring()
    players = get(f"{SLEEPER}/v1/players/nfl").json()
    gsis_of = {pid: str(p.get("gsis_id") or "").strip() for pid, p in players.items() if p.get("gsis_id")}
    proj = projections(sc, gsis_of)
    print(f"[study] projections: {len(proj)} player-weeks", flush=True)
    snaps = pd.concat([snapshots(y, sc) for y in SEASONS])
    s = features(snaps.merge(proj, on=["season", "week", "gsis"]))
    s = s[s.position.isin(POSITIONS) & (s.P >= 3)]
    print(f"[study] snapshots: {len(s)} ({s.game_id.nunique()} games)", flush=True)
    train, test = s[s.season < TEST_SEASON], s[s.season == TEST_SEASON]
    pd.set_option("display.width", 220)
    rows, coefs, spread = [], {}, []
    for pos in POSITIONS:
        tr, te = train[train.position == pos], test[test.position == pos]
        preds = {"current (pts + proj x time left)": te.pts + te.left * te.P}
        for kind in ("clock", "full"):
            coef = np.linalg.lstsq(design(tr, kind), (tr.f_pts - tr.pts).to_numpy(), rcond=None)[0]
            preds[f"fit: {kind}"] = te.pts + design(te, kind) @ coef
            if kind == "full":
                coefs[pos] = [round(float(c), 4) for c in coef]
                resid = te.f_pts - preds["fit: full"]
                for c in (0.25, 0.5, 0.75, 0.875):
                    sel = te.el == c
                    rem = (preds["fit: full"] - te.pts)[sel]
                    spread.append((pos, c, float(resid[sel].std()), float(rem.mean()), float(np.sqrt(rem.clip(lower=0)).mean())))
        for name, pred in preds.items():
            err = te.f_pts - pred
            by = (err ** 2).groupby(te.el).mean() ** 0.5
            rows.append((pos, name, float((err ** 2).mean() ** 0.5), float(err.abs().mean()), float(err.mean()),
                         by.loc[0.25], by.loc[0.5], by.loc[0.75], by.loc[0.875]))
    print(pd.DataFrame(rows, columns=["pos", "method", "rmse", "mae", "bias", "Q1 end", "half", "Q3 end", "mid Q4"]).round(2).to_string(index=False))
    print("[study] full-fit coefficients (left*pts_rate, left*opp_rate, left*margin, left*cold*P, left):")
    for pos, c in coefs.items():
        print(f"  {pos}: {c}")
    print("[study] residual spread vs projected points left (for win odds):")
    print(pd.DataFrame(spread, columns=["pos", "elapsed", "resid_sd", "mean_left", "mean_sqrt_left"]).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
