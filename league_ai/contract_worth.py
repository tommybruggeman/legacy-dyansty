"""How much do contracts really return? A study on another league's full contract history. No model call.

The Brotherhood league (same format, 10 teams, superflex, $300 cap, not on Sleeper) kept every contract since 2017
in a spreadsheet. Each contract-season gets the player's real worth that season in this league's money: points above
replacement in PPR scoring, priced so the league's whole cap buys the whole league's points (league_sim.Data.value).
Salaries are rescaled to a $225 cap.

The question it answers for the trade engine (league_ai.trade_value): the engine says the k-th most valuable player
should cost about the k-th biggest cap hit. Is that right, and where is it off? Walk-forward: each season's fair
salaries are corrected only with what earlier seasons showed.

Run locally with the spreadsheet (never commit it: owner names, and the repo is public):
    pip install openpyxl pyarrow && python -m league_ai.contract_worth "/path/to/Fantasy Salary Model.xlsx"
Prints aggregate numbers only.
"""
from __future__ import annotations

import io
import re
import sys
from typing import Any

import numpy as np
import pandas as pd

from league_ai import draft_study as ds
from league_ai import league_sim as ls

OTHER_CAP, OUR_CAP = 300.0, 225.0
FIRST, LAST = 2017, 2025
SHEET = "All Time Contracts"


def load_contracts(path: str) -> pd.DataFrame:
    import openpyxl

    rows = list(openpyxl.load_workbook(path, data_only=True, read_only=True)[SHEET].iter_rows(values_only=True))
    df = pd.DataFrame([r[:8] for r in rows[1:] if r and r[0]], columns=["player", "season", "pos", "owner", "left", "length", "salary", "notes"])
    for c in ("season", "left", "length", "salary"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["pos"] = df.pos.astype(str).str.upper()
    df["notes"] = df.notes.astype(str)
    df = df.dropna(subset=["season", "salary"]).drop_duplicates(["player", "season"])
    df["season"] = df.season.astype(int)
    df["sal"] = (df.salary * OUR_CAP / OTHER_CAP).clip(lower=1.0)
    return df[df.pos.isin(ls.POS)].drop(columns=["owner"])


def build_data() -> ls.Data:
    ls.FIRST = 2016
    frames = []
    for y in range(2012, LAST + 1):
        w = pd.read_csv(io.BytesIO(ds.get_bytes(ds.STATS_URL.format(season=y))), low_memory=False)
        w = w[(w.season_type == "REG") & w.position.isin(ls.POS)].copy()
        w["pts"] = ds.week_points(w, ds.DEFAULT_SCORING)
        frames.append(w[["player_id", "player_display_name", "position", "season", "week", "pts"]])
    weeks = pd.concat(frames)
    ecr = pd.read_parquet(io.BytesIO(ds.get_bytes(ds.ECR_URL)))
    ecr["scrape_date"] = pd.to_datetime(ecr.scrape_date)
    ids = pd.read_csv(io.BytesIO(ds.get_bytes(ds.IDS_URL)), low_memory=False)
    d = ls.Data(weeks[weeks.season >= 2015], ecr, ids, 10, True, LAST, 18)
    d.history = ls.aging_history(weeks, ids, d.repl, LAST, 18)
    return d


def _key(name: object) -> str:
    return re.sub(r"(jr|sr|ii|iii|iv)$", "", ls.ds_name(name))


def match(df: pd.DataFrame, d: ls.Data) -> pd.DataFrame:
    by_name: dict[tuple[str, str], int] = {}
    for i in range(d.n):
        by_name.setdefault((_key(d.name[i]), ls.POS[d.pos[i]]), i)
    df = df.copy()
    df["i"] = [by_name.get((_key(p), q)) for p, q in zip(df.player, df.pos)]
    df = df.dropna(subset=["i"]).copy()
    df["i"] = df.i.astype(int)
    df["val"] = [float(d.value[s][i]) if s in d.value else np.nan for s, i in zip(df.season, df.i)]
    df["val1"] = [float(d.value[s + 1][i]) if s + 1 in d.value else np.nan for s, i in zip(df.season, df.i)]
    df["age"] = [s + 0.67 - d.born[i] for s, i in zip(df.season, df.i)]
    return df


def fair_salaries(df: pd.DataFrame, d: ls.Data, seasons: range) -> pd.DataFrame:
    """The engine's fair salary each season: players ordered by preseason worth get the league's cap hits, biggest first."""
    out = []
    for s in seasons:
        g = df[df.season == s].copy()
        bot = ls.Bot(d, s, market=ls.market_dollars(d, s))
        g["est"] = [bot.dollars_by_year(i)[0] for i in g.i]
        g = g.sort_values("est", ascending=False)
        g["fair"] = np.sort(g.sal.to_numpy())[::-1]
        out.append(g)
    return pd.concat(out)


def walk_forward(a: pd.DataFrame, top_share: float) -> dict[str, Any]:
    payroll = a.groupby("season").sal.sum() / 10
    a = a.assign(top=a.fair >= top_share * a.season.map(payroll))
    res = []
    for s in sorted(a.season.unique())[1:]:
        tr, te = a[(a.season < s) & a.top], a[a.season == s].copy()
        ratio = float(tr.val.sum() / tr.fair.sum())
        te["adj"] = np.where(te.top, te.fair * ratio, te.fair)
        te["ratio"] = ratio
        res.append(te)
    t = pd.concat(res)
    err = lambda col, real: round(float((t[col] - t[real]).abs().mean()), 2)
    return {"top_share": top_share, "contracts": int(len(t)), "top_contracts": int(t.top.sum()),
            "ratios": sorted({round(x, 2) for x in t.ratio}),
            "error_this_year": {"fair": err("fair", "val"), "adjusted": err("adj", "val")},
            "error_next_year": {"fair": err("fair", "val1"), "adjusted": err("adj", "val1")}}


def summary(df: pd.DataFrame) -> dict[str, Any]:
    """What new free-agent contracts (not rookies) returned per year, by position, price, age and length."""
    new = df[(df.left == df.length) & ~df.notes.str.contains("R") & (df.season <= LAST)].copy()
    new["surplus"] = new.val - new.sal
    new["tier"] = pd.cut(new.sal, [0, 4, 11, 22, 1e9], labels=["$1-3", "$4-10", "$11-22", "$23+"])
    new["ages"] = pd.cut(new.age, [0, 24, 27, 30, 50], labels=["24-", "25-27", "28-30", "31+"])
    table = lambda by: {str(k): {"n": int(len(g)), "salary": round(float(g.sal.mean()), 1), "surplus": round(float(g.surplus.mean()), 1)}
                        for k, g in new.groupby(by, observed=True)}
    return {"signings": int(len(new)), "by_position": table("pos"), "by_price": table("tier"), "by_age": table("ages"),
            "by_length": table("length")}


def main() -> None:
    raw = load_contracts(sys.argv[1])
    d = build_data()
    df = match(raw, d)
    print(f"contracts {len(raw)}, matched {len(df) / len(raw):.1%}")
    print(summary(df))
    a = fair_salaries(df, d, range(2020, LAST + 1))
    for share in (0.07, 0.10, 0.12, 0.15):
        print(walk_forward(a, share))


if __name__ == "__main__":
    main()
