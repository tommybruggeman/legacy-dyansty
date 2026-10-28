"""League simulation: how good is the bot's advice on player prices, contract lengths and rookie picks?

Pretend this league started in 2020. Each of 1,000 simulated leagues runs 2020-2026:
- 2020 startup auction ($225 cap, contracts of 1-4 years under the league's bid rules), then a free-agent auction
  every later preseason for expired contracts;
- a rookie draft every year from 2021: straight order (1.01 also picks 2.01), worst finish last season picks first,
  rookie scale salaries;
- each season is played with every team's best lineup from the real weekly points, in this league's scoring.

One team (the bot) bids and drafts on the bot's numbers. The other nine bid what the market would: the preseason
FantasyPros position rank priced at what that rank scored last season, with each owner's own noise.

The bot only ever uses what was known at the time: each preseason it fits the link between preseason rankings and
real results on earlier seasons only (walk-forward), so 2020's prices come from 2019 alone.

Free: public nflverse / DynastyProcess files plus the read-only Sleeper API. No database, no AI calls.
Run: python -m league_ai.league_sim  (writes stress_results.json; the workflow commits it encrypted)
"""
from __future__ import annotations

import io
import json
import math
import os
from typing import Any

import numpy as np
import pandas as pd

from league_ai import draft_study as ds

FIRST, SIMS_DEFAULT = 2020, 1000
CAP, MIN_SALARY = 225.0, 1.0
POS = ("QB", "RB", "WR", "TE")
YEAR_DISCOUNT = 0.10                       # league rule: each extra contract year makes a bid worth 10% more
MIN_BID = {1: 1.0, 2: 4.0, 3: 12.0, 4: 20.0}
ROOKIE_SALARY = {1: (15, 12, 9, 8, 6, 5, 4, 4, 4, 4), 2: (3,) * 10, 3: (1,) * 10}
ROOKIE_YEARS = {1: 2, 2: 2, 3: 1}
ROOKIE_OPTION = {1: 25.0, 2: 15.0, 3: 7.0}  # option salary for one more year (keep-or-walk at rollover)
DEAD_CAP_PCT = 0.5                         # assumption: half of each remaining year when a contract is cut
AUCTION_FILL = 20                          # roster spots filled at each auction (room left for rookies)
ROSTER_MAX = 24
RETENTION_PRIOR = {"QB": 0.85, "RB": 0.6, "WR": 0.8, "TE": 0.8}  # year-over-year value kept, before there's data to fit
NOISE_MARKET = 0.25                        # each market owner's valuation noise (lognormal sd)
BENCH_DEPTH = {"QB": 1, "RB": 3, "WR": 3, "TE": 1}  # backups the bot keeps per position beyond its starters
BEYOND_NEED = 0.2                          # a player the bot has no lineup spot for is worth this share of his value
LINEUP_AWARE = os.environ.get("SIM_LINEUP_AWARE", "1") == "1"
SMART_OWNERS = os.environ.get("SIM_SMART_OWNERS", "1") == "1"  # the other owners also buy for need, learn and draft off a board
OWNER_SKILL = (0.3, 0.9)                   # how far each other owner leans on learned values over the market price
NEED_PENALTY = 8.0                         # rookie board spots a player drops when his position is already full


# ---- data ----------------------------------------------------------------------------------------------------

class Data:
    """Everything indexed by player: weekly points, realized value, preseason ranks, rookie boards."""

    def __init__(self, weeks: pd.DataFrame, ecr: pd.DataFrame, ids: pd.DataFrame, teams: int, superflex: bool, live_season: int, live_week: int):
        self.teams, self.live_season, self.live_week = teams, live_season, live_week
        self.repl = ds.replacement_levels(teams, superflex)
        players = sorted(set(weeks.player_id))
        self.idx = {p: i for i, p in enumerate(players)}
        self.n = len(players)
        self.gsis = players
        first_pos = weeks.sort_values("season").groupby("player_id").position.last()
        self.pos = np.array([POS.index(first_pos[p]) for p in players])
        self.name = weeks.groupby("player_id").player_display_name.last().reindex(players).tolist()
        self.seasons = sorted(set(weeks.season))
        self.W: dict[int, np.ndarray] = {}
        for s in self.seasons:
            w = weeks[weeks.season == s]
            m = np.zeros((self.n, 19))
            np.add.at(m, (w.player_id.map(self.idx).to_numpy(), w.week.clip(1, 18).to_numpy()), w.pts.to_numpy())
            self.W[s] = m
        table = ds.season_table(weeks, self.repl, live_season, live_week)
        self.par = {s: np.zeros(self.n) for s in self.seasons}
        for r in table.itertuples():
            self.par[r.season][self.idx[r.player_id]] = r.par
        # dollars per point above replacement: the league's money (less $1 a roster spot) over the season's total PAR
        spots = teams * AUCTION_FILL
        self.k_dollar = {s: (teams * CAP - spots * MIN_SALARY) / max(self.par[s].sum(), 1.0) for s in self.seasons}
        self.value = {s: MIN_SALARY + self.par[s] * self.k_dollar[s] for s in self.seasons}   # what each player-season was worth
        fp = ids.dropna(subset=["fantasypros_id", "gsis_id"])
        fp2gsis = dict(zip(fp.fantasypros_id.astype(int).astype(str), fp.gsis_id))
        g = ids.dropna(subset=["gsis_id"])
        self.draft_year = np.full(self.n, np.nan)
        for gs, y in zip(g.gsis_id, g.draft_year):
            if gs in self.idx and not pd.isna(y):
                self.draft_year[self.idx[gs]] = y
        ecr = ecr.copy()
        ecr["gsis"] = ecr.id.astype(str).map(fp2gsis)
        ecr = ecr[ecr.gsis.isin(self.idx) & ecr.pos.isin(POS)]
        self.rp = {s: self._ranks(ecr, "rp", f"{s}-07-01", f"{s}-09-07") for s in self.seasons if s >= FIRST}
        self.dp = {s: self._ranks(ecr, "dp", f"{s}-07-01", f"{s}-09-07") for s in self.seasons if s >= FIRST}
        self.rookie_board: dict[int, list[tuple[int, float, float]]] = {}
        rk = ecr[ecr.ecr_type.isin(["drk", "rookies"])]
        for s in self.seasons:
            snap = rk[(rk.scrape_date.dt.year == s) & (rk.scrape_date <= f"{s}-05-31")]
            if snap.empty:
                continue
            snap = snap[snap.scrape_date == snap.scrape_date.max()].sort_values("ecr").drop_duplicates("gsis")
            self.rookie_board[s] = [(self.idx[g], float(e), float(sd) if not pd.isna(sd) else 3.0) for g, e, sd in zip(snap.gsis, snap.ecr, snap.sd)]

    def _ranks(self, ecr: pd.DataFrame, kind: str, lo: str, hi: str) -> np.ndarray:
        """Position rank (1 = best) from the last scrape in the window; NaN when unranked."""
        snap = ecr[(ecr.ecr_type == kind) & (ecr.scrape_date >= lo) & (ecr.scrape_date <= hi)]
        out = np.full(self.n, np.nan)
        if snap.empty:
            return out
        snap = snap[snap.scrape_date == snap.scrape_date.max()].sort_values("ecr").drop_duplicates("gsis")
        for p, grp in snap.groupby("pos"):
            for rank, g in enumerate(grp.gsis, start=1):
                out[self.idx[g]] = rank
        return out


# ---- the bot's prices (walk-forward) -----------------------------------------------------------------------

MAX_RANK = 120


def rank_curve(ranks: np.ndarray, outcome: np.ndarray) -> np.ndarray:
    """Expected outcome at each position rank 1..MAX_RANK: kernel-smoothed mean, never rising with rank."""
    ok = ~np.isnan(ranks)
    r, y = ranks[ok], outcome[ok]
    out = np.zeros(MAX_RANK + 1)
    if len(r) == 0:
        return out
    for k in range(1, MAX_RANK + 1):
        w = np.exp(-0.5 * ((r - k) / max(1.5, k * 0.15)) ** 2)
        out[k] = float((w * y).sum() / w.sum()) if w.sum() > 1e-9 else 0.0
    return np.array([0.0] + ds.smooth_curve([float(x) for x in out[1:]]))


def sorted_curve(par: np.ndarray, pos: np.ndarray, p: int) -> np.ndarray:
    """What the k-th best player at a position actually scored that season (k = 1..MAX_RANK)."""
    v = np.sort(par[pos == p])[::-1]
    out = np.zeros(MAX_RANK + 1)
    out[1:min(MAX_RANK, len(v)) + 1] = v[:MAX_RANK]
    return out


class Bot:
    """Preseason price and contract length for every player, from earlier seasons only."""

    def __init__(self, d: Data, season: int):
        self.d, self.t = d, season
        past = [s for s in d.seasons if FIRST <= s < season and s in d.rp]
        self.k_dollar = float(np.mean([d.k_dollar[s] for s in d.seasons if s < season] or [d.k_dollar[min(d.seasons)]]))
        self.yr1: dict[int, np.ndarray] = {}
        self.later: dict[tuple[int, int], np.ndarray] = {}
        for p in range(len(POS)):
            if past:
                ranks = np.concatenate([np.where(d.pos == p, d.rp[s], np.nan) for s in past])
                outs = np.concatenate([d.par[s] for s in past])
                fitted = rank_curve(ranks, outs)
                prior = 0.7 * sorted_curve(d.par[season - 1], d.pos, p)
                w = min(1.0, len(past) / 3)                     # lean on the 2019 prior until three seasons are in
                self.yr1[p] = w * fitted + (1 - w) * prior
            else:
                self.yr1[p] = 0.7 * sorted_curve(d.par[season - 1], d.pos, p)   # 2020: regress 2019's finishes 30%
            for k in (1, 2, 3):
                pairs = [s for s in d.seasons if FIRST <= s and s + k < season and s in d.dp]
                prior = self.yr1[p] * RETENTION_PRIOR[POS[p]] ** k
                if pairs:
                    ranks = np.concatenate([np.where(d.pos == p, d.dp[s], np.nan) for s in pairs])
                    outs = np.concatenate([d.par[s + k] for s in pairs])
                    w = min(1.0, len(pairs) / 3)
                    self.later[(p, k)] = w * rank_curve(ranks, outs) + (1 - w) * prior
                else:
                    self.later[(p, k)] = prior

    def par_by_year(self, i: int) -> list[float]:
        """Expected points above replacement this season and the next three."""
        d, p = self.d, int(self.d.pos[i])
        r0 = d.rp.get(self.t, np.full(d.n, np.nan))[i]
        rd = d.dp.get(self.t, np.full(d.n, np.nan))[i]
        y0 = self.yr1[p][int(min(r0, MAX_RANK))] if not np.isnan(r0) else 0.0
        out = [y0]
        for k in (1, 2, 3):
            out.append(self.later[(p, k)][int(min(rd, MAX_RANK))] if not np.isnan(rd) else y0 * RETENTION_PRIOR[POS[p]] ** k)
        return out

    def dollars_by_year(self, i: int) -> list[float]:
        return [MIN_SALARY + max(0.0, v) * self.k_dollar for v in self.par_by_year(i)]

    def offer(self, i: int) -> tuple[float, int]:
        """(top annual salary, years) that gives the strongest bid under the league's year discount without overpaying."""
        dollars = self.dollars_by_year(i)
        best = (dollars[0], 1, dollars[0])
        for y in (2, 3, 4):
            salary = float(np.mean(dollars[:y]))
            if salary < MIN_BID[y]:
                continue
            equiv = salary * (1 + YEAR_DISCOUNT) ** (y - 1)
            if equiv > best[2]:
                best = (salary, y, equiv)
        return best[0], best[1]


OWNER_CURVES: dict[int, np.ndarray] = {}     # position -> salary by preseason rank, from this league's real auction contracts
OWNER_YEARS: dict[str, list[float]] = {}      # price tier -> share of 1/2/3/4-year contracts owners actually signed
OWNER_PROFILES: list[dict[str, Any]] = []     # one per real owner: how his prices and lengths lean against the league's
PROFILE_SHRINK = 10                           # contracts' worth of pull toward league average (thin samples stay near 1.0)


def market_dollars(d: Data, season: int) -> np.ndarray:
    """What the other owners pay. With this league's real contracts loaded: the salary owners here actually paid for a
    player at that preseason rank. Without: the rank priced at what that rank scored last season."""
    out = np.full(d.n, MIN_SALARY)
    ranks = d.rp.get(season)
    if ranks is None:
        return out
    if OWNER_CURVES:
        for p in range(len(POS)):
            m = (d.pos == p) & ~np.isnan(ranks)
            out[m] = np.maximum(MIN_SALARY, OWNER_CURVES[p][np.minimum(ranks[m], MAX_RANK).astype(int)])
        return out
    k = d.k_dollar[season - 1]
    for p in range(len(POS)):
        curve = sorted_curve(d.par[season - 1], d.pos, p)
        m = (d.pos == p) & ~np.isnan(ranks)
        out[m] = MIN_SALARY + curve[np.minimum(ranks[m], MAX_RANK).astype(int)] * k
    return out


def owner_profiles(df: pd.DataFrame, d: Data) -> list[dict[str, Any]]:
    """Each owner's lean against what the league pays for the same preseason rank: big names, depth, each position,
    contract length and player age. Every lean is shrunk toward 1.0 (or 0) by sample size."""
    df = df.copy()
    df["curve"] = [max(MIN_SALARY, float(OWNER_CURVES[p][int(min(r, MAX_RANK))])) for p, r in zip(df.pos, df["rank"])]
    df["ratio"] = (df.salary / df.curve).clip(0.2, 5.0)
    df["exp"] = df.season - pd.Series(d.draft_year[df.i.to_numpy()], index=df.index)
    tier = pd.cut(df.salary, [0, 5, 15, 30, 1e9], labels=False, right=False)
    df["yrs_over"] = df.years - df.groupby(tier).years.transform("mean")
    exp_mean = float(df.exp.mean())

    def lean(x: pd.Series, base: float = 1.0) -> float:
        n = len(x)
        return round(float(base + (x.mean() - base) * n / (n + PROFILE_SHRINK)), 3) if n else base

    out = []
    for name, g in df.groupby("owner"):
        if len(g) < 8 or name == "?":
            continue
        e = g.dropna(subset=["exp"])
        slope = float(np.polyfit(e.exp - exp_mean, np.log(e.ratio), 1)[0]) if len(e) >= 8 and e.exp.std() > 0 else 0.0
        out.append({"owner": name, "contracts": int(len(g)),
                    "stars": lean(g[g["rank"] <= 12].ratio),          # pays this much of the going rate for top-12 names
                    "depth": lean(g[g["rank"] > 24].ratio),           # ... and for players ranked below 24
                    "positions": {POS[p]: lean(g[g.pos == p].ratio) for p in range(len(POS))},
                    "years": lean(g.yrs_over, 0.0),                   # extra contract years vs the league at that price
                    "age_slope": round(slope * len(e) / (len(e) + PROFILE_SHRINK), 3),   # + = pays up for veterans
                    "exp_mean": round(exp_mean, 2),
                    "top3_share": round(float(g.groupby("season").salary.apply(lambda x: x.nlargest(3).sum() / max(x.sum(), 1)).mean()), 3)})
    return out


def personality_mult(profile: dict[str, Any] | None, d: Data, season: int) -> np.ndarray:
    """Per-player multiplier on the market price for one owner's personality."""
    if not profile:
        return np.ones(d.n)
    ranks = d.rp.get(season, np.full(d.n, np.nan))
    m = np.ones(d.n)
    m[ranks <= 12] *= profile["stars"]
    m[ranks > 24] *= profile["depth"]
    for p, name in enumerate(POS):
        m[d.pos == p] *= profile["positions"].get(name, 1.0)
    exp = season - d.draft_year
    exp = np.where(np.isnan(exp), np.nanmean(exp), exp)
    m *= np.exp(np.clip(profile["age_slope"] * (exp - profile.get("exp_mean", 4.0)), -0.5, 0.5))
    return m


def owner_years(salary: float) -> list[float]:
    tier = "$30+" if salary >= 30 else "$15-30" if salary >= 15 else "$5-15" if salary >= 5 else "$1-5"
    return OWNER_YEARS.get(tier) or [0.35, 0.3, 0.2, 0.15]


def load_owner_market(client: Any, d: Data, sleeper_to_gsis: dict[str, str]) -> dict[str, Any]:
    """Fit what this league's owners really pay (non-rookie contracts, by preseason position rank) and how long they sign.
    Returns aggregate stats only: the repo and its logs are public."""
    leagues = client.table("leagues").select("id").execute().data or []
    teams = client.table("league_teams").select("id, owner_name, team_name").execute().data or []
    owners = {str(t["id"]): str(t.get("owner_name") or t.get("team_name") or "?") for t in teams}   # same owner across seasons
    rows = []
    for lg in leagues:
        agreements = client.table("contract_agreements").select("*").eq("league_id", lg["id"]).execute().data or []
        ids = [a["id"] for a in agreements]
        seasons: list[dict[str, Any]] = []
        for i in range(0, len(ids), 200):
            seasons += client.table("contract_seasons").select("contract_id, season, salary, obligation_status").in_("contract_id", ids[i:i + 200]).execute().data or []
        by: dict[str, list[dict[str, Any]]] = {}
        for x in seasons:
            if x.get("obligation_status") != "voided":
                by.setdefault(str(x["contract_id"]), []).append(x)
        for a in agreements:
            kind = " ".join(str(a.get(k) or "") for k in ("contract_type", "acquisition_type", "source", "origin")).lower()
            if "rookie" in kind or "draft" in kind:
                continue
            ss = sorted(by.get(str(a["id"]), []), key=lambda x: x.get("season") or 0)
            g = sleeper_to_gsis.get(str(a.get("sleeper_player_id")))
            if not ss or g not in d.idx:
                continue
            start = int(ss[0]["season"] or 0)
            rows.append({"i": d.idx[g], "season": start, "salary": float(ss[0].get("salary") or 0), "years": len(ss),
                         "owner": owners.get(str(a.get("league_team_id")), "?")})
    df = pd.DataFrame(rows)
    df = df[(df.salary >= MIN_SALARY) & df.season.isin(list(d.rp))] if len(df) else df
    if len(df) < 60:
        return {"contracts": int(len(df)), "used": False}
    df["pos"] = d.pos[df.i.to_numpy()]
    df["rank"] = [d.rp[s][i] for s, i in zip(df.season, df.i)]
    for p in range(len(POS)):
        g = df[df.pos == p]
        OWNER_CURVES[p] = rank_curve(g["rank"].to_numpy(dtype=float), g.salary.to_numpy()) if len(g) >= 8 else np.full(MAX_RANK + 1, MIN_SALARY)
    tier = pd.cut(df.salary, [0, 5, 15, 30, 1e9], labels=["$1-5", "$5-15", "$15-30", "$30+"], right=False)
    for t, g in df.groupby(tier, observed=True):
        counts = np.array([(g.years.clip(1, 4) == y).sum() for y in (1, 2, 3, 4)], float) + 0.5
        OWNER_YEARS[str(t)] = list(counts / counts.sum())
    OWNER_PROFILES[:] = owner_profiles(df, d)
    df["real"] = [float(d.value[s][i]) if s in d.value else np.nan for s, i in zip(df.season, df.i)]
    first = df.dropna(subset=["real"])
    return {"contracts": int(len(df)), "used": True, "seasons": sorted(int(x) for x in df.season.unique()),
            "owners_first_year_error": round(float((first.salary - first.real).abs().mean()), 1),
            "owners_first_year_bias": round(float((first.salary - first.real).mean()), 1),
            "years_by_price": {k: [round(x, 2) for x in v] for k, v in OWNER_YEARS.items()},
            "owner_profiles": OWNER_PROFILES,
            "salary_by_rank": {POS[p]: {f"{POS[p]}{r}": round(float(OWNER_CURVES[p][r]), 1) for r in (1, 3, 6, 12, 24)} for p in range(len(POS))}}


# ---- one league --------------------------------------------------------------------------------------------

def lineup_points(M: np.ndarray, pos: np.ndarray, slots: dict[str, int]) -> float:
    """Season total of the best lineup each week. M = players x weeks."""
    if M.shape[0] == 0:
        return 0.0
    left = []
    total = 0.0
    for p, name in enumerate(POS):
        S = -np.sort(-M[pos == p], axis=0) if (pos == p).any() else np.zeros((0, M.shape[1]))
        c = slots.get(name, 0)
        total += S[:c].sum()
        left.append(S[c:])
    flex = np.concatenate(left[1:], axis=0) if slots.get("FLEX") else np.zeros((0, M.shape[1]))
    rest = [left[0]] + ([] if slots.get("FLEX") else left[1:])
    if slots.get("FLEX") and flex.shape[0]:
        flex = -np.sort(-flex, axis=0)
        total += flex[:slots["FLEX"]].sum()
        rest.append(flex[slots["FLEX"]:])
    if slots.get("SUPER_FLEX"):
        sf = np.concatenate([r for r in rest if r.shape[0]], axis=0) if any(r.shape[0] for r in rest) else np.zeros((0, M.shape[1]))
        if sf.shape[0]:
            total += (-np.sort(-sf, axis=0))[:slots["SUPER_FLEX"]].sum()
    return float(total)


class League:
    def __init__(self, d: Data, slots: dict[str, int], rng: np.random.Generator, bot_seat: int = 0):
        self.d, self.slots, self.rng, self.bot = d, slots, rng, bot_seat
        self.T = d.teams
        self.owner = np.full(d.n, -1)
        self.salary = np.zeros(d.n)
        self.years = np.zeros(d.n, dtype=int)
        self.rookie_round = np.zeros(d.n, dtype=int)
        self.dead = np.zeros(self.T)
        self.contracts: list[dict[str, Any]] = []
        self.picks: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.noise = rng.lognormal(0, NOISE_MARKET, size=(self.T, d.n))  # each owner's lasting opinion of each player
        starters = {p: slots.get(p, 0) for p in POS}
        starters["QB"] += slots.get("SUPER_FLEX", 0)
        for p in ("RB", "WR"):
            starters[p] += slots.get("FLEX", 0)
        self.need_cap = np.array([starters[p] + BENCH_DEPTH[p] for p in POS])
        self.skill = rng.uniform(*OWNER_SKILL, size=self.T) if SMART_OWNERS else np.zeros(self.T)
        self.skill[self.bot] = 1.0
        picks = rng.permutation(len(OWNER_PROFILES))[:self.T] if OWNER_PROFILES else []
        self.profile: list[dict[str, Any] | None] = [OWNER_PROFILES[k] for k in picks] + [None] * (self.T - len(picks))
        self.profile[self.bot] = None

    def full(self, team: int, i: int) -> bool:
        """The team already has its starters and backups at this player's position."""
        return bool((self.d.pos[self.roster(team)] == self.d.pos[i]).sum() >= self.need_cap[self.d.pos[i]])

    def roster(self, team: int) -> np.ndarray:
        return np.flatnonzero(self.owner == team)

    def committed(self, team: int) -> float:
        return float(self.salary[self.owner == team].sum() + self.dead[team])

    def values(self, season: int, bot: Bot, market: np.ndarray) -> np.ndarray:
        """teams x players: what each owner thinks a player is worth this season (bot row = bot's prices)."""
        learned = np.array([bot.dollars_by_year(i)[0] for i in range(self.d.n)])
        v = (self.skill[:, None] * learned[None, :] + (1 - self.skill[:, None]) * market[None, :]) * self.noise
        for t in range(self.T):
            if self.profile[t]:
                v[t] *= personality_mult(self.profile[t], self.d, season)
        v[self.bot] = learned
        return v

    def auction(self, season: int, bot: Bot, market: np.ndarray, values: np.ndarray) -> None:
        d = self.d
        ranked = d.rp.get(season, np.full(d.n, np.nan))
        pool = np.flatnonzero((self.owner < 0) & ~np.isnan(ranked))
        order = pool[np.argsort(-(market[pool] * self.rng.lognormal(0, 0.15, len(pool))))]
        need = np.array([max(0, AUCTION_FILL - len(self.roster(t))) for t in range(self.T)])
        budget = np.array([CAP - self.committed(t) for t in range(self.T)])
        offers = {}
        for i in order:
            if need.sum() == 0:
                break
            bids = np.zeros(self.T)
            terms = []
            for t in range(self.T):
                if need[t] == 0:
                    terms.append((0.0, 1))
                    continue
                if t == self.bot:
                    if i not in offers:
                        offers[i] = bot.offer(i)
                    sal, yrs = offers[i]
                    if LINEUP_AWARE and self.full(t, i):
                        sal = max(MIN_SALARY, sal * BEYOND_NEED)   # no lineup spot left at his position: bench value only
                else:
                    sal = values[t, i]
                    if SMART_OWNERS and self.rng.random() < self.skill[t]:
                        if i not in offers:
                            offers[i] = bot.offer(i)
                        yrs = offers[i][1]                        # a sharp owner picks the length the numbers favor
                    else:
                        yrs = int(self.rng.choice([1, 2, 3, 4], p=owner_years(sal)))
                    lean = (self.profile[t] or {}).get("years", 0.0)
                    if self.rng.random() < min(1.0, abs(lean)):
                        yrs = int(min(4, max(1, yrs + np.sign(lean))))   # this owner tends to go longer (or shorter)
                    while yrs > 1 and sal < MIN_BID[yrs]:
                        yrs -= 1
                    if SMART_OWNERS and self.full(t, i):
                        sal = max(MIN_SALARY, sal * BEYOND_NEED)
                room = budget[t] - (need[t] - 1) * MIN_SALARY
                sal = min(sal, room)
                terms.append((sal, yrs))
                bids[t] = sal * (1 + YEAR_DISCOUNT) ** (yrs - 1) if sal >= MIN_BID[yrs] else 0.0
            win = int(np.argmax(bids))
            if bids[win] < MIN_SALARY:
                continue
            second = np.sort(bids)[-2] if self.T > 1 else 0.0
            sal_max, yrs = terms[win]
            price = min(sal_max, max(MIN_BID[yrs], math.ceil((second + 1) / (1 + YEAR_DISCOUNT) ** (yrs - 1))))
            self.sign(i, win, float(price), yrs, season, "auction", bot_price=offers.get(i, (None, None))[0], bot_years=offers.get(i, (None, None))[1],
                      market_price=float(market[i]))
            need[win] -= 1
            budget[win] -= price

    def sign(self, i: int, team: int, salary: float, years: int, season: int, how: str, **extra: Any) -> None:
        self.owner[i], self.salary[i], self.years[i] = team, salary, years
        self.contracts.append({"player": int(i), "team": team, "salary": salary, "years": years, "season": season, "how": how, **extra})

    def rookie_draft(self, season: int, order: list[int], bot_board: list[int]) -> None:
        board = [b for b in self.d.rookie_board.get(season, []) if self.owner[b[0]] < 0]
        learned_rank = {i: r for r, i in enumerate(bot_board, start=1)}
        taken: set[int] = set()
        for rnd in (1, 2, 3):
            for slot, team in enumerate(order, start=1):
                if team == self.bot:
                    left = [i for i in bot_board if i not in taken and self.owner[i] < 0]
                    pick = min(left, key=lambda i: learned_rank[i] + (NEED_PENALTY if LINEUP_AWARE and self.full(team, i) else 0.0), default=None)
                else:
                    k = self.skill[team] if SMART_OWNERS else 0.0
                    noisy = sorted(((k * learned_rank.get(i, e) + (1 - k) * e + self.rng.normal(0, max(sd, 1.0))
                                     + (NEED_PENALTY if SMART_OWNERS and self.full(team, i) else 0.0), i) for i, e, sd in board if i not in taken))
                    pick = noisy[0][1] if noisy else None
                if pick is None:
                    continue
                taken.add(pick)
                self.rookie_round[pick] = rnd
                self.sign(pick, team, float(ROOKIE_SALARY[rnd][slot - 1]), ROOKIE_YEARS[rnd], season, "rookie", round=rnd, slot=slot)
                self.picks.append({"season": season, "round": rnd, "slot": slot, "team": team, "player": int(pick)})

    def trim(self, values: np.ndarray) -> None:
        """Over the roster limit: cut the players each owner values least, paying dead cap."""
        for t in range(self.T):
            r = self.roster(t)
            extra = len(r) - ROSTER_MAX
            if extra <= 0:
                continue
            cut = r[np.argsort(values[t, r] - self.salary[r])][:extra]
            for i in cut:
                self.dead[t] += DEAD_CAP_PCT * self.salary[i] * self.years[i] if self.salary[i] > MIN_SALARY else 0.0
                self.owner[i], self.salary[i], self.years[i] = -1, 0.0, 0

    def play(self, season: int) -> np.ndarray:
        pts = np.array([lineup_points(self.d.W[season][self.roster(t)], self.d.pos[self.roster(t)], self.slots) for t in range(self.T)])
        rank = (-pts).argsort().argsort() + 1
        for t in range(self.T):
            self.results.append({"season": season, "team": t, "points": float(pts[t]), "rank": int(rank[t])})
        return pts

    def roll(self, season: int, values_next: np.ndarray) -> None:
        """End of season: a year off every contract; expired rookies keep their option if the owner thinks he's worth it."""
        self.years[self.owner >= 0] -= 1
        self.dead[:] = 0.0
        for i in np.flatnonzero((self.owner >= 0) & (self.years <= 0)):
            rnd = int(self.rookie_round[i])
            if rnd and values_next[self.owner[i], i] > ROOKIE_OPTION[rnd]:
                self.salary[i], self.years[i], self.rookie_round[i] = ROOKIE_OPTION[rnd], 1, 0
                self.contracts.append({"player": int(i), "team": int(self.owner[i]), "salary": ROOKIE_OPTION[rnd], "years": 1, "season": season + 1, "how": "option"})
                continue
            self.owner[i], self.salary[i], self.years[i], self.rookie_round[i] = -1, 0.0, 0, 0


def bot_rookie_board(d: Data, season: int) -> list[int]:
    """The rookie board, shifted by how each position has done against its ranking in classes already graded."""
    board = d.rookie_board.get(season, [])
    shift = {}
    for p in range(len(POS)):
        res = []
        for c in d.rookie_board:
            if c + 2 > season - 1:   # needs two seasons played before this draft
                continue
            for rank, (i, e, _) in enumerate(d.rookie_board[c][:36], start=1):
                if d.pos[i] == p:
                    got = d.par.get(c, np.zeros(d.n))[i] + d.par.get(c + 1, np.zeros(d.n))[i]
                    res.append(got)
        shift[p] = float(np.mean(res)) if res else 0.0
    base = float(np.mean(list(shift.values()))) if shift else 0.0
    # a position whose rookies out-earned the average moves up a few spots, never more than 6
    adj = [(e - max(-6.0, min(6.0, (shift.get(int(d.pos[i]), base) - base) / 10.0)), i) for i, e, _ in board]
    return [i for _, i in sorted(adj)]


def run_league(d: Data, slots: dict[str, int], seed: int, seasons: list[int]) -> League:
    rng = np.random.default_rng(seed)
    lg = League(d, slots, rng, bot_seat=int(rng.integers(0, d.teams)))
    order: list[int] | None = None
    for s in seasons:
        bot = Bot(d, s)
        market = market_dollars(d, s)
        vals = lg.values(s, bot, market)
        if order is not None:
            lg.rookie_draft(s, order, bot_rookie_board(d, s))
            lg.trim(vals)
        lg.auction(s, bot, market, vals)
        pts = lg.play(s)
        order = list(np.argsort(pts))           # worst finish picks first, every round
        if s + 1 in d.seasons:
            nb, nm = Bot(d, s + 1), market_dollars(d, s + 1)
            lg.roll(s, lg.values(s + 1, nb, nm))
    return lg


# ---- grading -----------------------------------------------------------------------------------------------

def realized(d: Data, i: int, start: int, years: int) -> list[float]:
    return [float(d.value[s][i]) for s in range(start, start + years) if s in d.value]


def price_accuracy(d: Data) -> dict[str, Any]:
    """Every ranked player, every preseason: bot price and market price against what he was really worth."""
    out: dict[str, Any] = {"by_season": {}, "by_position": {}, "multi_year": {}}
    rows = []
    for s in [x for x in d.seasons if x >= FIRST and x in d.rp]:
        bot, market = Bot(d, s), market_dollars(d, s)
        ranked = np.flatnonzero(~np.isnan(d.rp[s]))
        for i in ranked:
            dollars = bot.dollars_by_year(i)
            rows.append({"season": s, "i": int(i), "pos": POS[d.pos[i]], "rank": int(d.rp[s][i]), "bot": dollars[0], "market": float(market[i]),
                         "real": float(d.value[s][i]), **{f"bot_k{k}": dollars[k] for k in (1, 2, 3)},
                         **{f"real_k{k}": float(d.value[s + k][i]) if s + k in d.value else None for k in (1, 2, 3)}})
    df = pd.DataFrame(rows)
    df = df[df["rank"] <= df.pos.map({"QB": 32, "RB": 60, "WR": 80, "TE": 24})]       # the players an auction prices
    def summarize(g: pd.DataFrame) -> dict[str, Any]:
        return {"players": int(len(g)), "bot_error": round(float((g.bot - g.real).abs().mean()), 1), "market_error": round(float((g.market - g.real).abs().mean()), 1),
                "bot_bias": round(float((g.bot - g.real).mean()), 1), "market_bias": round(float((g.market - g.real).mean()), 1),
                "bot_rank_corr": round(float(g.bot.rank().corr(g.real.rank())), 3), "market_rank_corr": round(float(g.market.rank().corr(g.real.rank())), 3)}
    out["overall"] = summarize(df)
    for s, g in df.groupby("season"):
        out["by_season"][int(s)] = summarize(g)
    for p, g in df.groupby("pos"):
        out["by_position"][p] = summarize(g)
    tiers = pd.cut(df.bot, [0, 5, 15, 30, 1e9], labels=["$1-5", "$5-15", "$15-30", "$30+"])
    out["by_price"] = {str(k): summarize(g) for k, g in df.groupby(tiers, observed=True)}
    for k in (1, 2, 3):
        g = df.dropna(subset=[f"real_k{k}"])
        g = g[g.season + k < d.live_season + 1]
        if len(g):
            out["multi_year"][f"year+{k}"] = {"players": int(len(g)), "bot_error": round(float((g[f"bot_k{k}"] - g[f"real_k{k}"]).abs().mean()), 1),
                                            "bot_bias": round(float((g[f"bot_k{k}"] - g[f"real_k{k}"]).mean()), 1),
                                            "rank_corr": round(float(g[f"bot_k{k}"].rank().corr(g[f"real_k{k}"].rank())), 3)}
    df["miss"] = df.bot - df.real
    worst = df.reindex(df.miss.abs().sort_values(ascending=False).index).head(15)
    out["biggest_misses"] = [{"season": int(r.season), "player": d.name[int(r.i)], "pos": r.pos, "bot": round(r.bot), "market": round(r.market), "real": round(r.real)} for r in worst.itertuples()]
    return out


def grade_leagues(d: Data, leagues: list[League]) -> dict[str, Any]:
    res = pd.DataFrame([{**r, "league": n, "bot": r["team"] == lg.bot} for n, lg in enumerate(leagues) for r in lg.results])
    T = d.teams
    by = res.groupby("bot")
    out: dict[str, Any] = {
        "avg_finish": {("bot" if k else "others"): round(float(g["rank"].mean()), 2) for k, g in by},
        "top4_rate": {("bot" if k else "others"): round(float((g["rank"] <= 4).mean()), 3) for k, g in by},
        "first_place_rate": {("bot" if k else "others"): round(float((g["rank"] == 1).mean()), 3) for k, g in by},
        "fair_share": {"avg_finish": (T + 1) / 2, "top4_rate": round(4 / T, 3), "first_place_rate": round(1 / T, 3)},
        "bot_by_season": {int(s): {"avg_finish": round(float(g[g.bot]["rank"].mean()), 2), "top4_rate": round(float((g[g.bot]["rank"] <= 4).mean()), 3)} for s, g in res.groupby("season")},
    }
    # contracts: what each signing returned against its salary, year by year (seasons played so far)
    rows = []
    for n, lg in enumerate(leagues):
        for c in lg.contracts:
            real = realized(d, c["player"], c["season"], c["years"])
            rows.append({"bot": c["team"] == lg.bot, "how": c["how"], "years": c["years"], "salary": c["salary"],
                         "surplus_per_year": (sum(real) - c["salary"] * len(real)) / max(len(real), 1), "seen": len(real)})
    cdf = pd.DataFrame(rows)
    out["contracts"] = {}
    for (b, how), g in cdf.groupby(["bot", "how"]):
        out["contracts"][f"{'bot' if b else 'others'}_{how}"] = {"signed": int(len(g)), "avg_salary": round(float(g.salary.mean()), 1),
                                                                   "avg_surplus_per_year": round(float(g.surplus_per_year.mean()), 2),
                                                                   "share_profitable": round(float((g.surplus_per_year > 0).mean()), 3)}
    auction = cdf[cdf.how == "auction"]
    out["contract_length"] = {f"{'bot' if b else 'others'}_{y}yr": {"signed": int(len(g)), "avg_surplus_per_year": round(float(g.surplus_per_year.mean()), 2),
                                                                     "share_profitable": round(float((g.surplus_per_year > 0).mean()), 3)}
                              for (b, y), g in auction.groupby(["bot", "years"])}
    # rookie picks: value over his first three seasons, against the average pick at that slot
    pk = pd.DataFrame([{**p, "bot": p["team"] == lg.bot, "value": sum(realized(d, p["player"], p["season"], 3)) - 3 * MIN_SALARY}
                       for lg in leagues for p in lg.picks])
    if len(pk):
        slot_avg = pk.groupby(["season", "round", "slot"]).value.transform("mean")
        pk["over_slot"] = pk.value - slot_avg
        graded = pk[pk.season <= d.live_season - 2]
        out["rookie_draft"] = {"picks_graded": int(len(graded)), "bot_value_over_slot": round(float(graded[graded.bot].over_slot.mean()), 2) if graded.bot.any() else None,
                               "others_value_over_slot": round(float(graded[~graded.bot].over_slot.mean()), 2),
                               "avg_value_by_round": {int(r): round(float(g.value.mean()), 1) for r, g in graded.groupby("round")}}
    return out


_SHARED: dict[str, Any] = {}


def _one(seed: int) -> League:
    return run_league(_SHARED["d"], _SHARED["slots"], seed, _SHARED["seasons"])


def run_all(d: Data, slots: dict[str, int], seasons: list[int], sims: int) -> list[League]:
    """All leagues, spread over the machine's cores (forked workers share the loaded data)."""
    _SHARED.update(d=d, slots=slots, seasons=seasons)
    workers = max(1, (os.cpu_count() or 1))
    if workers == 1 or sims < 8:
        return [_one(s) for s in range(sims)]
    import multiprocessing as mp
    with mp.get_context("fork").Pool(workers) as pool:
        return pool.map(_one, range(sims), chunksize=8)


def roster_slots(positions: list[str]) -> dict[str, int]:
    slots: dict[str, int] = {}
    for p in positions:
        if p in POS or p in ("FLEX", "SUPER_FLEX"):
            slots[p] = slots.get(p, 0) + 1
        elif p == "REC_FLEX" or p == "WRRB_FLEX":
            slots["FLEX"] = slots.get("FLEX", 0) + 1
    return slots or {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1}


def redact(obj: Any) -> Any:
    """Owner names stay out of the public Actions log (the committed results are encrypted and keep them)."""
    if isinstance(obj, dict):
        out = {k: redact(v) for k, v in obj.items()}
        if isinstance(out.get("owner_profiles"), list):
            out["owner_profiles"] = [{**p, "owner": f"Owner {chr(65 + n)}"} for n, p in enumerate(out["owner_profiles"])]
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def main() -> None:
    sims = int(os.environ.get("SIM_LEAGUES", SIMS_DEFAULT))
    live = ds.league_chain(ds.LIVE_LEAGUE)[0]
    teams = int((live.get("settings") or {}).get("num_teams") or 10)
    scoring = live.get("scoring_settings") or ds.DEFAULT_SCORING
    positions = live.get("roster_positions") or []
    superflex = "SUPER_FLEX" in positions
    slots = roster_slots(positions)
    state = ds.get_json(f"{ds.SLEEPER}/state/nfl")
    live_season = int(state.get("season") or live.get("season"))
    frames = []
    for y in range(FIRST - 1, live_season + 1):
        try:
            w = pd.read_csv(io.BytesIO(ds.get_bytes(ds.STATS_URL.format(season=y))), low_memory=False)
        except Exception as e:
            print(f"no stats for {y}: {e}")
            continue
        w = w[(w.season_type == "REG") & w.position.isin(POS)].copy()
        w["pts"] = ds.week_points(w, scoring)
        frames.append(w[["player_id", "player_display_name", "position", "season", "week", "pts"]])
    weeks = pd.concat(frames)
    live_week = int(weeks[weeks.season == live_season].week.max()) if (weeks.season == live_season).any() else 1
    ecr = pd.read_parquet(io.BytesIO(ds.get_bytes(ds.ECR_URL)))
    ecr["scrape_date"] = pd.to_datetime(ecr.scrape_date)
    ids = pd.read_csv(io.BytesIO(ds.get_bytes(ds.IDS_URL)), low_memory=False)
    d = Data(weeks, ecr, ids, teams, superflex, live_season, live_week)
    owners: dict[str, Any] = {"used": False}
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        try:
            from supabase import create_client
            sl = ids.dropna(subset=["sleeper_id", "gsis_id"])
            owners = load_owner_market(create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]), d,
                                       dict(zip(sl.sleeper_id.astype(int).astype(str), sl.gsis_id)))
        except Exception as exc:
            owners = {"used": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    print(f"Owner market from this league's contracts: {redact(owners)}")
    print(f"League: {teams} teams, slots {slots}, stats through {live_season} wk {live_week}, {d.n} players, {sims} leagues")
    seasons = [s for s in d.seasons if s >= FIRST]
    accuracy = price_accuracy(d)
    leagues = run_all(d, slots, seasons, sims)
    report = {"setup": {"teams": teams, "slots": slots, "leagues": sims, "seasons": seasons, "stats_through": f"{live_season} wk {live_week}",
                        "k_dollar": {int(s): round(v, 3) for s, v in d.k_dollar.items()}},
              "owner_market": owners, "price_accuracy": accuracy, "leagues": grade_leagues(d, leagues)}
    if owners.get("owner_profiles"):    # how each real owner's personality fared when it played a seat
        fin: dict[str, list[int]] = {}
        for lg in leagues:
            for r in lg.results:
                prof = lg.profile[r["team"]]
                if prof:
                    fin.setdefault(prof["owner"], []).append(r["rank"])
        for prof in owners["owner_profiles"]:
            ranks = fin.get(prof["owner"], [])
            prof["sim_avg_finish"] = round(float(np.mean(ranks)), 2) if ranks else None
            prof["sim_top4_rate"] = round(float(np.mean([x <= 4 for x in ranks])), 3) if ranks else None
    print(json.dumps(redact(report), indent=1, default=str)[:30000])
    with open("stress_results.json", "w") as f:
        json.dump(report, f, default=str)


if __name__ == "__main__":
    main()
