"""Rookie draft pick value study: what each rookie pick slot is really worth in this league.

For every rookie class from 2020 on it takes the FantasyPros rookie rankings as they stood at the end of May
(after the NFL draft, when dynasty rookie drafts happen), simulates 1,000 drafts off that board with the experts'
own disagreement as the noise, and scores every pick by what the player actually did over his first three seasons
in this league's scoring (points above a 10-team superflex replacement level). Real rookie drafts from Sleeper
(this league's history and any extra league ids in DRAFT_LEAGUES) are scored the same way.

Free: public nflverse / DynastyProcess files plus the read-only Sleeper API, no database or AI calls.
Run: python -m league_ai.draft_study  (writes stress_results.json; the workflow commits it encrypted)
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Mapping

import numpy as np
import pandas as pd
import requests

SLEEPER = "https://api.sleeper.app/v1"
STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
ECR_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_fpecr.parquet"
IDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
LIVE_LEAGUE = "1361083066197491712"

FIRST_CLASS = 2020
SIMS = 1000
POSITIONS = ("QB", "RB", "WR", "TE")
ELITE = {"QB": 6, "RB": 6, "WR": 6, "TE": 3}       # a top-6 PPG season at the position (TE top 3) in his first three years
STARTER = {"QB": 12, "RB": 24, "WR": 24, "TE": 12}
# Sleeper scoring key -> nflverse weekly column
STAT_COLS = {"pass_yd": "passing_yards", "pass_td": "passing_tds", "pass_int": "passing_interceptions", "pass_2pt": "passing_2pt_conversions",
             "pass_fd": "passing_first_downs", "rush_yd": "rushing_yards", "rush_td": "rushing_tds", "rush_2pt": "rushing_2pt_conversions",
             "rush_fd": "rushing_first_downs", "rec": "receptions", "rec_yd": "receiving_yards", "rec_td": "receiving_tds",
             "rec_2pt": "receiving_2pt_conversions", "rec_fd": "receiving_first_downs", "pass_sack": "sacks_suffered"}
FUMBLE_COLS = ("rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost")
DEFAULT_SCORING = {"pass_yd": .04, "pass_td": 4, "pass_int": -1, "rush_yd": .1, "rush_td": 6, "rec": 1, "rec_yd": .1, "rec_td": 6,
                   "fum_lost": -2, "pass_2pt": 2, "rush_2pt": 2, "rec_2pt": 2}


def get_json(url: str) -> Any:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def get_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    return r.content


# ---- pure parts ------------------------------------------------------------------------------------------

def replacement_levels(teams: int, superflex: bool) -> dict[str, int]:
    """Season-total rank of the last startable player at each position (starters plus a little bench depth)."""
    return {"QB": teams * (2 if superflex else 1), "RB": int(teams * 2.5), "WR": int(teams * 3.5), "TE": int(teams * 1.2)}


def week_points(w: pd.DataFrame, scoring: Mapping[str, float]) -> pd.Series:
    pts = pd.Series(0.0, index=w.index)
    for key, col in STAT_COLS.items():
        if scoring.get(key) and col in w:
            pts += w[col].fillna(0) * float(scoring[key])
    if scoring.get("fum_lost"):
        pts += sum(w[c].fillna(0) for c in FUMBLE_COLS if c in w) * float(scoring["fum_lost"])
    if scoring.get("bonus_rec_te") and "receptions" in w:
        pts += np.where(w.position == "TE", w.receptions.fillna(0), 0) * float(scoring["bonus_rec_te"])
    for pos in ("rb", "wr"):
        if scoring.get(f"bonus_rec_{pos}") and "receptions" in w:
            pts += np.where(w.position == pos.upper(), w.receptions.fillna(0), 0) * float(scoring[f"bonus_rec_{pos}"])
    return pts


def season_table(weeks: pd.DataFrame, repl: Mapping[str, int], live_season: int, live_week: int) -> pd.DataFrame:
    """One row per player-season: points, PPG rank at the position, and points above replacement (live season paced to 17 games)."""
    s = weeks.groupby(["player_id", "season", "position"]).agg(pts=("pts", "sum"), g=("week", "nunique")).reset_index()
    live = s.season == live_season
    s["pts_full"] = np.where(live, s.pts * 17 / max(live_week, 1), s.pts)
    s["ppg"] = s.pts / s.g
    s["qual"] = s.g >= np.where(live, max(3, live_week // 2), 8)
    s["ppg_rank"] = s[s.qual].groupby(["season", "position"]).ppg.rank(ascending=False)
    cut = {(yr, p): g.pts_full.nlargest(repl[p]).min() for (yr, p), g in s.groupby(["season", "position"])}
    s["par"] = [max(0.0, r.pts_full - cut[(r.season, r.position)]) for r in s.itertuples()]
    return s


def outcome(seasons: pd.DataFrame, gsis: str | None, pos: str, draft_year: int, live_season: int) -> dict[str, Any]:
    """Points above replacement over his first three seasons (unplayed years carry his latest season forward) and his tier."""
    seen = min(3, live_season - draft_year + 1)
    s = seasons[(seasons.player_id == gsis) & seasons.season.between(draft_year, draft_year + 2)].sort_values("season") if gsis else seasons.iloc[0:0]
    par = float(s.par.sum())
    if seen < 3 and len(s):
        par += float(s.par.iloc[-1]) * (3 - seen)
    best = s.ppg_rank.min() if len(s) and s.qual.any() else np.nan
    if not np.isnan(best) and best <= ELITE.get(pos, 6):
        tier = "elite"
    elif not np.isnan(best) and best <= STARTER.get(pos, 24):
        tier = "starter"
    else:
        tier = "bust" if seen >= 2 else "too_early"
    return {"par3": round(par, 1), "best_rank": None if np.isnan(best) else int(best), "tier": tier}


def simulate(players: list[Mapping[str, Any]], slots: int, sims: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """sims x slots arrays of the value / elite flag each slot got when managers draft off the board with the experts' spread as noise."""
    ecr = np.array([p["ecr"] for p in players], float)
    sd = np.array([max(float(p.get("sd") or 3), 1.0) for p in players])
    par = np.array([p["par3"] for p in players], float)
    elite = np.array([p["tier"] == "elite" for p in players], float)
    n = min(slots, len(players))
    out_par, out_el = np.zeros((sims, slots)), np.zeros((sims, slots))
    for s in range(sims):
        order = np.argsort(ecr + rng.normal(0, sd))[:n]
        out_par[s, :n], out_el[s, :n] = par[order], elite[order]
    return out_par, out_el


def slot_label(k: int, teams: int) -> str:
    return f"{k // teams + 1}.{k % teams + 1:02d}"


def slot_curve(par: np.ndarray, elite: np.ndarray, teams: int) -> list[dict[str, Any]]:
    """Per slot: average value, median, chance of an elite player, value relative to 1.01, and chance he beats the next slot's pick."""
    flat, fel = par.reshape(-1, par.shape[-1]), elite.reshape(-1, elite.shape[-1])
    ev = flat.mean(0)
    rows = []
    for k in range(flat.shape[1]):
        rows.append({"slot": slot_label(k, teams), "ev_par": round(float(ev[k]), 1), "median_par": round(float(np.median(flat[:, k])), 1),
                     "p_elite": round(float(fel[:, k].mean()), 3), "p_hit": round(float((flat[:, k] > 0).mean()), 3),
                     "rel_to_101": round(float(ev[k] / ev[0]), 3) if ev[0] else None})
    return rows


def smooth_curve(ev: list[float]) -> list[float]:
    """Non-increasing fit (pool-adjacent-violators): a later pick is never worth more than an earlier one."""
    blocks = [[v, 1] for v in ev]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0]:
            v = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / (blocks[i][1] + blocks[i + 1][1])
            blocks[i:i + 2] = [[v, blocks[i][1] + blocks[i + 1][1]]]
            i = max(i - 1, 0)
        else:
            i += 1
    return [round(v, 1) for v, n in blocks for _ in range(n)]


def elite_counts(classes: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """How many elite players each class had, and how often there were at least 1, 2, 3 in the top 10 of the board."""
    per = {y: sum(p["tier"] == "elite" for p in c["players"][:10]) for y, c in classes.items() if c["seasons_seen"] >= 2}
    n = max(len(per), 1)
    return {"top10_elite_by_class": per, "share_at_least": {k: round(sum(v >= k for v in per.values()) / n, 2) for k in (1, 2, 3, 4)}}


# ---- data loading ----------------------------------------------------------------------------------------

def league_chain(league_id: str) -> list[dict[str, Any]]:
    out, lid = [], league_id
    while lid and lid != "0" and len(out) < 15:
        info = get_json(f"{SLEEPER}/league/{lid}")
        if not info:
            break
        out.append(info)
        lid = info.get("previous_league_id")
    return out


def rookie_picks(league: Mapping[str, Any], sleeper_draft_year: Mapping[str, int]) -> list[dict[str, Any]]:
    """Picks from this league season's drafts that took a player in his rookie year (skips startup/vet picks)."""
    season = int(league["season"])
    teams = int((league.get("settings") or {}).get("num_teams") or league.get("total_rosters") or 10)
    owners = {u["user_id"]: u.get("display_name") for u in get_json(f"{SLEEPER}/league/{league['league_id']}/users") or []}
    out = []
    for d in get_json(f"{SLEEPER}/league/{league['league_id']}/drafts") or []:
        if d.get("status") != "complete":
            continue
        for p in get_json(f"{SLEEPER}/draft/{d['draft_id']}/picks") or []:
            sid = str(p.get("player_id") or "")
            if sleeper_draft_year.get(sid) != season:
                continue
            meta = p.get("metadata") or {}
            out.append({"league": league.get("name"), "season": season, "teams": teams, "pick_no": int(p["pick_no"]), "round": int(p["round"]),
                        "sleeper_id": sid, "name": f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip(), "pos": meta.get("position"),
                        "owner": owners.get(p.get("picked_by"), p.get("picked_by"))})
    # rookie-only numbering: the 1st rookie taken is 1.01 even when the draft also had veterans
    out.sort(key=lambda r: (r["season"], r["pick_no"]))
    for i, r in enumerate(out):
        r["rookie_pick"] = i + 1
    return out


def load_weeks(scoring: Mapping[str, float], last: int) -> pd.DataFrame:
    frames = []
    for y in range(FIRST_CLASS, last + 1):
        try:
            w = pd.read_csv(io.BytesIO(get_bytes(STATS_URL.format(season=y))), low_memory=False)
        except Exception as e:  # the next season's file doesn't exist until week 1
            print(f"no stats for {y}: {e}")
            continue
        w = w[(w.season_type == "REG") & w.position.isin(POSITIONS)].copy()
        w["pts"] = week_points(w, scoring)
        frames.append(w[["player_id", "player_display_name", "position", "season", "week", "pts"]])
    return pd.concat(frames)


def main() -> None:
    rng = np.random.default_rng(int(os.environ.get("DRAFT_SEED", "7")))
    chain = league_chain(LIVE_LEAGUE)
    live = chain[0]
    teams = int((live.get("settings") or {}).get("num_teams") or 10)
    rounds = int((live.get("settings") or {}).get("draft_rounds") or 3)
    scoring = live.get("scoring_settings") or DEFAULT_SCORING
    superflex = "SUPER_FLEX" in (live.get("roster_positions") or [])
    repl = replacement_levels(teams, superflex)
    slots = teams * rounds
    print(f"League: {teams} teams, {rounds} rookie rounds, superflex={superflex}, replacement={repl}, rec={scoring.get('rec')}, te_bonus={scoring.get('bonus_rec_te')}")

    state = get_json(f"{SLEEPER}/state/nfl")
    live_season = int(state.get("season") or live.get("season"))
    weeks = load_weeks(scoring, live_season)
    live_week = int(weeks[weeks.season == live_season].week.max() or 1) if (weeks.season == live_season).any() else 1
    seasons = season_table(weeks, repl, live_season, live_week)
    print(f"Stats through {live_season} week {live_week}: {len(seasons)} player-seasons")

    ids = pd.read_csv(io.BytesIO(get_bytes(IDS_URL)), low_memory=False)
    fp_ids = ids.dropna(subset=["fantasypros_id", "gsis_id"])
    fp2gsis = dict(zip(fp_ids.fantasypros_id.astype(int).astype(str), fp_ids.gsis_id))
    sl = ids.dropna(subset=["sleeper_id"])
    sl2gsis = dict(zip(sl.sleeper_id.astype(int).astype(str), sl.gsis_id))
    sl_year = {k: int(v) for k, v in zip(sl.sleeper_id.astype(int).astype(str), sl.draft_year) if not pd.isna(v)}

    ecr = pd.read_parquet(io.BytesIO(get_bytes(ECR_URL)))
    ecr["scrape_date"] = pd.to_datetime(ecr.scrape_date)
    ecr = ecr[ecr.ecr_type.isin(["drk", "rookies"]) & ecr.pos.isin(POSITIONS)]

    classes: dict[int, dict[str, Any]] = {}
    for y in range(FIRST_CLASS, live_season + 1):
        snap = ecr[(ecr.scrape_date.dt.year == y) & (ecr.scrape_date <= f"{y}-05-31")]
        if snap.empty:
            continue
        snap = snap[snap.scrape_date == snap.scrape_date.max()].sort_values("ecr").head(slots * 2)
        players = []
        for r in snap.itertuples():
            gsis = fp2gsis.get(str(r.id))
            players.append({"name": r.player, "pos": r.pos, "ecr": float(r.ecr), "sd": float(r.sd) if not pd.isna(r.sd) else 3.0,
                            "gsis": gsis, **outcome(seasons, gsis, r.pos, y, live_season)})
        classes[y] = {"snapshot": str(snap.scrape_date.max().date()), "seasons_seen": min(3, live_season - y + 1), "players": players}

    # 1,000 drafts per class
    report: dict[str, Any] = {"league": {"teams": teams, "rounds": rounds, "superflex": superflex, "replacement": repl, "stats_through": f"{live_season} wk {live_week}"},
                              "classes": {}}
    sim_par, sim_el, used = [], [], []
    for y, c in classes.items():
        P = c["players"]
        par, el = simulate(P, slots, SIMS, rng)
        board = sorted(P, key=lambda p: p["ecr"])[:slots]
        cons = np.array([p["par3"] for p in board])
        best = np.sort([p["par3"] for p in P])[::-1][:slots]
        hind_rank = pd.Series(-cons).rank().to_numpy()
        rho = float(np.corrcoef(np.arange(len(cons)), hind_rank)[0, 1]) if cons.std() else None
        report["classes"][y] = {
            "snapshot": c["snapshot"], "seasons_seen": c["seasons_seen"],
            "elite_top10": sum(p["tier"] == "elite" for p in board[:10]), "elite_in_top60": sum(p["tier"] == "elite" for p in P),
            "tiers_drafted": dict(pd.Series([p["tier"] for p in board]).value_counts()),
            "board_order_vs_results": None if rho is None else round(rho, 3),
            "board_value_vs_perfect": round(float(cons.sum() / best.sum()), 3) if best.sum() else None,
            "sim_value_vs_perfect": round(float(par.sum(1).mean() / best.sum()), 3) if best.sum() else None,
            "round1_hits": sum(p["par3"] > 0 for p in board[:teams]),
            "board_top10": [{k: p[k] for k in ("name", "pos", "ecr", "par3", "tier", "best_rank")} for p in board[:10]],
            "steals": [{k: p[k] for k in ("name", "pos", "ecr", "par3", "tier")} for p in sorted(P[teams:], key=lambda p: -p["par3"])[:5]],
            "busts": [{k: p[k] for k in ("name", "pos", "ecr", "par3")} for p in board[:teams] if p["tier"] == "bust"],
        }
        if c["seasons_seen"] >= 2:
            sim_par.append(par), sim_el.append(el), used.append(y)
    curve = slot_curve(np.stack(sim_par), np.stack(sim_el), teams)
    for row, v in zip(curve, smooth_curve([r["ev_par"] for r in curve])):
        row["smoothed"] = v
    report["slot_curve"] = {"classes_used": used, "rows": curve}
    report["elite_counts"] = elite_counts(classes)

    # real drafts: this league's history plus any other leagues (DRAFT_LEAGUES = comma-separated Sleeper ids)
    real: list[dict[str, Any]] = []
    extra = [x.strip() for x in os.environ.get("DRAFT_LEAGUES", "").split(",") if x.strip()]
    for root in [LIVE_LEAGUE, *extra]:
        for lg in (chain if root == LIVE_LEAGUE else league_chain(root)):
            try:
                real += rookie_picks(lg, sl_year)
            except Exception as e:
                print(f"draft read failed for {lg.get('name')} {lg.get('season')}: {e}")
    for r in real:
        o = outcome(seasons, sl2gsis.get(r["sleeper_id"]), r["pos"] or "WR", r["season"], live_season)
        r.update(o)
        board = {p["gsis"]: p["ecr"] for p in classes.get(r["season"], {}).get("players", [])}
        r["board_rank"] = board.get(sl2gsis.get(r["sleeper_id"]))
    report["real_drafts"] = {"picks": real, "leagues": sorted({r["league"] for r in real if r["league"]}),
                             "seasons": sorted({r["season"] for r in real})}
    print(json.dumps({k: v for k, v in report.items() if k != "real_drafts"}, indent=1, default=str)[:20000])
    print(f"Real rookie picks found: {len(real)} across {report['real_drafts']['leagues']} seasons {report['real_drafts']['seasons']}")
    with open("stress_results.json", "w") as f:
        json.dump(report, f, default=str)


if __name__ == "__main__":
    main()
